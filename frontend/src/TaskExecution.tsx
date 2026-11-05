import {useState} from 'react';
import {useQuery} from '@tanstack/react-query';
import {Link} from 'react-router-dom';
import {request,mutate} from './api';
import {Button} from './components/ui/button';
import {runStage} from './ExecutionRuns';
import type {components} from './api.generated';

type Context=components['schemas']['TaskExecutionContext'];
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
export function TaskExecution({intakeId,taskId}:{intakeId:string;taskId:string}){
  const query=useQuery({queryKey:['task-execution',intakeId,taskId],queryFn:()=>request<Context>(`/api/intake/${encodeURIComponent(intakeId)}/tasks/${encodeURIComponent(taskId)}/execution`),refetchInterval:3000});
  if(query.isPending)return <section className="task-execution" aria-busy="true">Loading task progress…</section>;
  if(!query.data)return <section className="task-execution" role="alert">Task progress could not be loaded. <Button variant="secondary" onClick={()=>void query.refetch()}>Reload task progress</Button></section>;
  const data=query.data,latest=data.runs[0];
  return <section className="task-execution" aria-label="Task implementation progress">
    <h3>{data.stage==='waiting'?'Waiting to implement':runStage[data.stage]||'Recorded state unavailable'}</h3><p role="status">{data.message}</p>
    {data.session?.can_stop&&<StopRun key={data.session.id} id={data.session.id} session onSaved={()=>void query.refetch()}/>}
    {data.stage==='recovering'&&<p className="soft-callout">Saved evidence is retained. Implementation will not run again; other execution stays on hold until cleanup is confirmed.</p>}
    {query.isError&&<p role="alert">Progress could not be refreshed. Showing the last loaded record.</p>}
    {!data.execution_configured&&<p className="small-text">{['running','checking','cancel_requested'].includes(data.stage)?'Automatic execution is disabled. This run has separate task-specific permission.':'Execution is disabled in this instance. Opening this task starts no model.'}</p>}
    {!data.current_approval&&<p className="soft-callout">Current implementation eligibility is not confirmed. Earlier results remain in history and grant no permission to continue.</p>}
    {data.run_budget&&<p className="small-text">One task budget is recorded. Waiting time counts toward its overall limit.</p>}
    {latest&&<><Link className="record-link" to={'/executions/'+encodeURIComponent(latest.id)}>{latest.candidate_id?'Review changes and checks':'View run details'}</Link>
      {latest.verification==='passed'&&<p>{latest.publication?.published?'Independent checks passed. Draft PR delivered for human review and merge.':'Independent checks passed. This result has not been accepted or published here.'}</p>}
      {!latest.current_task&&<p>Earlier task inputs. These checks do not establish that the current task is verified.</p>}
      {!data.session&&!query.isError&&['pending','running','checking'].includes(latest.status)&&<StopRun key={latest.id} id={latest.id} onSaved={()=>void query.refetch()}/>}
    </>}
    {data.runs.length===10&&<p>Showing up to 10 recent runs. <Link to="/executions">All recorded runs</Link></p>}
    {data.runs.length>1&&<details><summary>Earlier task runs ({data.runs.length-1})</summary><ul>{data.runs.slice(1).map(run=><li key={run.id}><Link to={'/executions/'+encodeURIComponent(run.id)}>{runStage[run.status]||run.status}</Link><p>{run.message}</p></li>)}</ul></details>}
    <p className="small-text">Plan approval and execution permission are separate. Verification is not acceptance or publication.</p>
  </section>;
}
