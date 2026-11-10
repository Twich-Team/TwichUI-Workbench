import {useQueries, useQuery} from '@tanstack/react-query';
import {ArrowRight} from 'lucide-react';
import {Link} from 'react-router-dom';
import type {components} from './api.generated';
import {request} from './api';
import {formatTime, StateBadge} from './Catalog';
import {Button} from './components/ui/button';
import {executionProgress, intakeProgress, type RunApprovalContext} from './taskProgress';

type Intake = components['schemas']['IntakeView'];
type Page = components['schemas']['IntakePage'];
type Execution = components['schemas']['TaskExecutionContext'];

export const executionQuery = (intakeId: string, taskId: string) => ({
  queryKey: ['task-execution', intakeId, taskId],
  queryFn: () => request<Execution>(`/api/intake/${encodeURIComponent(intakeId)}/tasks/${encodeURIComponent(taskId)}/execution`),
});
export const approvalQuery = (intakeId: string, taskId: string) => ({
  queryKey: ['task-run-approval', intakeId, taskId],
  queryFn: () => request<RunApprovalContext>(`/api/intake/${encodeURIComponent(intakeId)}/tasks/${encodeURIComponent(taskId)}/run-approval`),
});

function singleCurrentTask(item: Intake) {
  if (item.status !== 'tasks_ready' || (!item.original.supported_task && !item.original.regression_tests && !item.original.local_planning)) return;
  const tasks = item.tasks.filter(t => t.kind === 'task' && !t.scope_review_required && t.proposal_id === item.proposals[0]?.id);
  return tasks.length === 1 ? tasks[0] : undefined;
}

function needsRunOffer(execution?: Execution) {
  return execution?.stage==='failed' || execution?.stage==='waiting'&&!execution.run_budget;
}

function summary(item: Intake, execution?: Execution, approval?: RunApprovalContext) {
  if (item.decisions.some(d => d.status === 'pending')) return intakeProgress(item);
  if (execution?.stage==='failed' && approval?.available && !approval.receipt && approval.offer?.repair_cycle?.continuation) {
    return {step:2,title:'Ready for a new run decision',next:'The previous run ended. Review the saved work and limits before allowing another attempt.',group:'attention' as const};
  }
  if (execution) {
    if (execution.run_budget?.current===false && ['held','waiting'].includes(execution.stage)) {
      return {step:2, title:'Run approval expired', next:'Open the task to see what is still available.', group:'attention' as const};
    }
    const progress = executionProgress(execution);
    if (progress) return progress;
    if (execution.run_budget) {
      return {step:2, title:'Run approved', next:'Open the task to see when work can start.', group:'current' as const};
    }
  }
  if (approval?.receipt && approval.receipt.status!=='applied') {
    return {step:2, title:'Run decision needs attention', next:'Open the task to see whether your choice could be applied.', group:'attention' as const};
  }
  if (approval && !approval.receipt) {
    if (approval.available) return {step:2, title:'Ready for your run decision', next:'Review what Workbench can do and the limits for this run.', group:'attention' as const};
    if (approval.preparation_resource) return {step:2,title:approval.preparation_resource.automatic?'Waiting for storage':'Preparation needs checking',next:approval.preparation_resource.automatic?'Your plan approval is saved. Workbench will recover preparation automatically when it is safe.':'Your plan and evidence are saved. The preparation section explains what stopped.',group:approval.preparation_resource.automatic?'current' as const:'attention' as const};
    if (!approval.preparing) return {step:2, title:'Preparation is on hold', next:'See the preparation reason below. Your plan approval remains saved.', group:'attention' as const};
    return {step:2, title:'Preparing the approved work', next:'Workbench is preparing the protected working copy and approved scope.', group:'current' as const};
  }
  return intakeProgress(item);
}

export function TaskJourney({item}: {item: Intake}) {
  const task = singleCurrentTask(item);
  const execution = useQuery({...executionQuery(item.id, task?.id || ''), enabled: Boolean(task), refetchInterval: 3000});
  const approval = useQuery({...approvalQuery(item.id, task?.id || ''),
    enabled: Boolean(task && (item.original.regression_tests || item.original.local_planning) && needsRunOffer(execution.data)),
    refetchInterval: 3000});
  const progress = summary(item, execution.isError ? undefined : execution.data, approval.isError ? undefined : approval.data);
  return <section className="panel task-journey" aria-label="Task progress">
    <ol className="journey-steps">{['Understand', 'Review plan', 'Do the work', 'Check', 'Result'].map((step, i) =>
      <li key={step} aria-current={i === progress.step ? 'step' : undefined}><span>{i + 1}</span>{step}</li>)}</ol>
    {(!execution.data || execution.data.stage==='waiting' ||
      approval.data?.available&&!approval.data.receipt&&approval.data.offer?.repair_cycle?.continuation ||
      item.decisions.some(d=>d.status==='pending')) &&
      <div role="status"><p className="journey-title"><strong>{progress.title}</strong></p>
        {item.status!=='waiting_for_information' && <p>{progress.next}</p>}</div>}
  </section>;
}

export function TaskOverview({preview = false, page = 1}: {preview?: boolean; page?: number}) {
  const query = useQuery({queryKey: ['intake', page], queryFn: () => request<Page>(`/api/intake?page=${page}`), refetchInterval: 5000});
  const items = query.data?.items || [];
  const targets = items.map(item => ({item, task: singleCurrentTask(item)}));
  const executions = useQueries({queries: targets.map(({item, task}) => ({
    ...executionQuery(item.id, task?.id || ''), enabled: Boolean(task), refetchInterval: 8000,
  }))});
  const approvals = useQueries({queries: targets.map(({item, task}, i) => ({
    ...approvalQuery(item.id, task?.id || ''), refetchInterval: 12000,
    enabled: Boolean(task && (item.original.regression_tests || item.original.local_planning) && needsRunOffer(executions[i].data) &&
      !item.decisions.some(d => d.status === 'pending')),
  }))});
  if (!query.data) return query.isPending ? <p role="status">Opening your tasks…</p> :
    <div className="panel" role="alert">Your tasks couldn’t be loaded. <Button variant="secondary" onClick={() => void query.refetch()}>Try again</Button></div>;
  if (!items.length) return <section className="panel empty-panel"><h2>No requests yet.</h2><p>When you create a task, its decisions, progress and result will appear here.</p></section>;
  const rows = items.map((item, i) => ({
    item, progress: summary(item, executions[i].data, approvals[i].data),
    refreshError: executions[i].isError || approvals[i].isError,
    result: executions[i].data?.runs.find(run => run.current_task && run.status==='checked' && run.verification==='passed'),
  }));
  return <div className={`task-overview${preview?" task-overview-preview":""}`}>
    {query.isError && <p className="form-error" role="alert">Refresh unavailable. Showing the last loaded tasks.</p>}
    {([['attention', 'Needs your attention'], ['current', 'Active work'], ['results', 'Recent outcomes']] as const).map(([group, title]) => {
      const matches = rows.filter(row => row.progress.group === group);
      if (!matches.length && !preview) return null;
      return <section className="panel task-lane" key={group}>
        <h2>{title}</h2>
        {matches.length ? matches.slice(0, preview ? 3 : matches.length).map(({item, progress, refreshError, result}) =>
          <Link className="record-link" to={result && progress.group==='results'?`/executions/${result.id}`:`/intake/${item.id}`} key={item.id}>
            <span><strong>{item.original.title}</strong><small>{formatTime(item.created_at)}{refreshError && ' · Progress refresh unavailable'}</small></span>
            <StateBadge value={progress.title}/><ArrowRight size={16} aria-hidden="true"/>
          </Link>) : <p className="muted">None among your latest requests.</p>}
      </section>;
    })}
    {query.data.planner === 'demo' && <p className="task-overview-note">This workspace uses sample responses for general planning.</p>}
  </div>;
}
