import {useState} from 'react';
import {useQuery} from '@tanstack/react-query';
import {Link} from 'react-router-dom';
import {mutate} from './api';
import {Button} from './components/ui/button';
import {ExecutionResult} from './RealExecutionReview';
import {RunApproval} from './RunApproval';
import {runStage, executionProgress} from './taskProgress';
import {executionQuery} from './TaskOverview';
function StopRun({id,onSaved,session=false}:{id:string;onSaved:()=>void;session?:boolean}){
  const [command]=useState(()=>crypto.randomUUID());
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  async function stop(){
    setBusy(true);setError('');
    try{await mutate((session?'/api/task-sessions/':'/api/real-executions/')+encodeURIComponent(id)+'/stop',{command_id:command});onSaved();}
    catch(e){setError(e instanceof Error?e.message:'The stop request could not be confirmed.');}
    finally{setBusy(false);}
  }
  return <><Button variant="secondary" disabled={busy} onClick={()=>void stop()}>{busy?'Saving stop request…':'Stop this run'}</Button>{error&&<p role="alert">{error} Retrying saves the same stop request.</p>}</>;
}
export function TaskExecution({intakeId,taskId,separateApproval,showWaiting=false}:{intakeId:string;taskId:string;separateApproval:boolean;showWaiting?:boolean}){
  const query=useQuery({...executionQuery(intakeId,taskId),refetchInterval:3000});
  if(query.isPending&&!separateApproval&&!showWaiting)return null;
  if(query.isPending)return <section className="task-execution" aria-busy="true">Loading task progress…</section>;
  if(!query.data)return <section className="task-execution" role="alert">Task progress could not be loaded. <Button variant="secondary" onClick={()=>void query.refetch()}>Reload task progress</Button></section>;
  const data=query.data, latest=data.runs[0];
  const progress=executionProgress(data);
  const waiting=data.stage==='waiting';
  const reviewRun=waiting||data.stage==='failed';
  const expired=(waiting || data.stage==='held')&&data.run_budget?.current===false;
  const showProgress=!waiting || Boolean(data.run_budget) || showWaiting;
  return <>
    {separateApproval&&reviewRun&&!expired&&<RunApproval intakeId={intakeId} taskId={taskId}/>}
    {showProgress&&<section className="task-execution" aria-label="Task implementation progress">
      <h3>{expired?'Run approval expired':progress?.title||(waiting?'Waiting to start':runStage[data.stage]||'Progress unavailable')}</h3>
      {data.message.replace(/[.!]$/, '').trim()!==progress?.title &&
        <p role="status">{data.message}</p>}
      {expired && <p>The saved plan is still available. Continuing needs fresh authorization for a supported run.</p>}
      {waiting && !data.run_budget && !separateApproval && <p>The plan is saved. This ready-made task needs its run permission before work can start.</p>}
      {data.session?.can_stop&&!query.isError&&<StopRun key={data.session.id} id={data.session.id} session onSaved={()=>void query.refetch()}/>}
      {data.stage==='recovering'&&<p>Workbench is waiting for cleanup to be confirmed. Your evidence is saved.</p>}
      {!expired&&['failed','held','blocked','interrupted','cancelled'].includes(data.stage)&&<p>Review the saved evidence. Another attempt needs fresh authorization.</p>}
      {query.isError&&<p role="alert">Progress could not be refreshed. Showing the last loaded record.</p>}
      {latest&&<>
        <Link className="record-link" to={'/executions/'+encodeURIComponent(latest.id)}>{latest.candidate_id?'Review changes and checks':'View run details'}</Link>
        {!latest.current_task&&<p>These results are from an earlier version of the task.</p>}
        {!data.session&&!query.isError&&['pending','running','checking'].includes(latest.status)&&<StopRun key={latest.id} id={latest.id} onSaved={()=>void query.refetch()}/>}
      </>}
      {data.runs.length===10&&<p>Showing up to 10 recent runs. <Link to="/executions">All run results</Link></p>}
      {data.runs.length>1&&<details><summary>Earlier runs ({data.runs.length-1})</summary><ul>{data.runs.slice(1).map(run=><li key={run.id}><Link to={'/executions/'+encodeURIComponent(run.id)}>{runStage[run.status]||'Recorded run'}</Link><p>{run.message}</p></li>)}</ul></details>}
    </section>}
    {latest?.candidate_id&&!data.session?.can_stop&&<ExecutionResult identity={latest.id} embedded/>}
    {separateApproval&&!reviewRun&&<details><summary>Permission for this run</summary><RunApproval intakeId={intakeId} taskId={taskId}/></details>}
  </>;
}
