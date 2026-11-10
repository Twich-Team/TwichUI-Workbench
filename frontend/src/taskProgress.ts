import type {components} from './api.generated';

type Intake = components['schemas']['IntakeView'];
type Execution = components['schemas']['TaskExecutionContext'];
export type RunApprovalContext = {
  preparation_resource?: {state:string;code:string;automatic:boolean;message:string;observed_at?:string} | null;
  provider?: {backend: 'codex'|'local'; model: string; effort?: string; fallback_reason?: string} | null;
  local_only?: boolean; approval_hold?: string; available: boolean; preparing?: boolean; message: string; binding?: string; scope_approval_id?: string;
  scope?: components['schemas']['ProposalView']; receipt?: components['schemas']['ProposalReceipt'] | null;
  offer: null | {id: string; fingerprint: string; title: string; repository: string; base_branch: string;
    base: string; paths: string[]; checks: {argv: string[]; cwd: string; purpose?: string}[];
    prepared_behavior?: {intent_digest:string;review_digest:string;oracle_sha256:string;reproducer:components['schemas']['Reproducer']};
    repair_cycle?: {verification:'not_run';notice:string;criteria:{id:string;description:string}[];continuation?:{execution_ids:string[];session_ids:string[];model_attempt_ids:string[]}};
    limits: Record<string, number>; budget: Budget};
};
export type Budget = components['schemas']['TaskBudget'];

export const runStage: Record<string, string> = {
  preparing: 'Preparing approved task', settling: 'Checks passed · Finishing cleanup',
  held: 'Work is on hold', completed: 'Completed', pending: 'Waiting to start',
  recovering: 'Waiting for safe recovery', cancel_requested: 'Stopping · Finishing cleanup',
  cancelled: 'Stopped', running: 'Working in a separate copy', checking: 'Checking changes',
  checked: 'Checks passed', failed: 'Work could not finish', blocked: 'Work could not start',
  interrupted: 'Interrupted · Evidence retained',
};

type Progress = {step: number; title: string; next: string; group: 'attention' | 'current' | 'results'};

export function intakeProgress(item: Intake): Progress {
  if(item.planning_start_binding)return {step:0,title:'Ready for read-only investigation',next:'Your report is saved. Choose a provider and allow investigation below; no repair is approved.',group:'attention'};
  if (item.decisions.some(d => d.status === 'pending')) {
    return {step: 1, title: 'Decision saved', next: 'Workbench is checking and applying your choice.', group: 'current'};
  }
  const proposal = item.proposals[0];
  const research = proposal?.research_only || proposal?.content.delivery === 'research';
  switch (item.status) {
    case 'unavailable':
      return {step:0,title:'Saved for later',next:'This kind of work is not executable yet. No model or repair has started.',group:'attention'};
    case 'queued':
      return item.local_model?.intake_ids.includes(item.id)
        ? {step:0,title:item.local_model.state==='yielding'?'Making Qwen available':'Waiting for local Qwen',next:(item.local_model.state==='working'?'Local Qwen is working on another Workbench request.':item.local_model.message)+(item.local_model.automatic?' Recovery is automatic; your saved revision remains scheduled.':''),group:'current'}
        : {step: 0, title: 'Waiting to start', next: item.jobs.find(job=>job.status==='queued'&&job.message.startsWith('Waiting for '))?.message||'Your request is saved. Planning is next.', group: 'current'};
    case 'investigating':
      return {step: 0, title: 'Looking into your request', next: 'Workbench will show its findings or bring you a plan.', group: 'current'};
    case 'waiting_for_information':
      return {step: 0, title: 'Waiting for information', next: 'Answer the question below so Workbench can continue.', group: 'attention'};
    case 'completed':
      return proposal
        ? {step: 1, title: 'Ready for your review', next: 'Read the plan and decide whether the proposed scope is right.', group: 'attention'}
        : {step: 4, title: 'Findings ready', next: 'Read what Workbench found and what still needs checking.', group: 'results'};
    case 'approved':
      return {step: 2, title: 'Plan approved', next: 'Workbench is preparing the agreed work.', group: 'current'};
    case 'tasks_ready':
      return research || (!item.original.supported_task && !item.original.regression_tests && !item.original.local_planning)
        ? {step: 4, title: 'Plan ready', next: 'Review the agreed work. This request has reached the end of planning.', group: 'results'}
        : {step: 2, title: 'Approved work', next: 'See the current decision, progress or result below.', group: 'current'};
    case 'failed': {
      const job = item.jobs.filter(j => j.kind === 'planning').at(-1);
      const interrupted = ['canceled', 'interrupted_unknown'].includes(String(job?.diagnostics.category));
      return {step: 0, title: interrupted ? 'Planning interrupted' : 'Planning could not finish', next: 'See the reason and available next steps below.', group: 'attention'};
    }
    case 'decomposition_failed':
      return {step: 2, title: 'Approved work could not be prepared', next: 'See the reason and available next steps below.', group: 'attention'};
    case 'declined':
      return {step: 1, title: 'Plan declined', next: 'Workbench saved your decision. You can submit a new request if you want a different plan.', group: 'results'};
    case 'scope_review_required':
      return {step: 1, title: 'Plan needs another review', next: 'Open the current plan. The earlier decision cannot authorize changed work.', group: 'attention'};
  }
}

export function executionProgress(data: Execution): Progress | undefined {
  if (!data.current_approval || data.stage === 'waiting') return;
  const title = runStage[data.stage] || 'Progress unavailable';
  switch (data.stage) {
    case 'running':
      return {step: 2, title, next: 'Workbench will run the required checks on the changes.', group: 'current'};
    case 'checking':
    case 'settling':
      return {step: 3, title, next: 'The result will be ready to review after the run closes.', group: 'current'};
    case 'checked':
    case 'completed':
      return data.runs.some(run => run.current_task && run.status==='checked' && run.verification==='passed')
        ? {step: 4, title, next: 'Open the result to see what changed and which checks passed.', group: 'results'}
        : {step: 4, title: 'Run result saved', next: 'Open the result to see which checks were recorded.', group: 'results'};
    case 'held':
    case 'failed':
    case 'blocked':
    case 'interrupted':
    case 'cancelled':
    case 'recovering':
      return {step: 2, title, next: 'See why the work stopped and what you can do next.', group: 'attention'};
    default:
      return {step: 2, title, next: 'Follow the run below.', group: 'current'};
  }
}

export const minutes = (seconds: number) => `${seconds / 60} min`;

export function providerOutcome(value: string) {
  if (value === 'interrupted_unknown') return 'Interrupted, outcome unknown';
  if (value === 'incomplete_output') return 'Finished with partial output';
  if (value === 'unavailable') return 'Unavailable before generation';
  return value;
}
