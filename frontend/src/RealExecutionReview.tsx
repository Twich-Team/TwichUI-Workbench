import {useQuery} from '@tanstack/react-query';
import {Link,useParams} from 'react-router-dom';
import {request} from './api';
import {PublicationStatus} from './PublicationStatus';
import {DiffFile} from './CandidateReview';
import type {components} from './api.generated';

type View={id:string;title:string;status:string;message:string;base:string;current_grant:boolean;
  recovery_pending?:boolean;publication_requested?:boolean;
  grant_id:string;expires_at:string;publication_allowed:false;source:'real';
  workflow_task?:{intake_id:string;task_id:string;binding:string}|null;current_task?:boolean|null;
  result:null|{inference_ran?:boolean|null;verification?:string;candidate_id?:string;
    handler_returned?:boolean;
    authorship?:{kind:string;author:string;parent_author?:string};
    usage?:{status:string;values?:{completion_tokens?:number;prompt_tokens?:number}|null};
    files?:components['schemas']['FileComparison'][];
    gates?:{name:string;status:string;evidence:string}[];
    checks?:{status:string;rc:number|null;out:string}[];error?:string}};

export function RealExecutionReview(){
  const {identity}=useParams();
  const query=useQuery({queryKey:['real-execution',identity],
    queryFn:()=>request<View>('/api/real-executions/'+encodeURIComponent(identity!)),refetchInterval:3000});
  if(query.isPending)return <section className="panel" aria-busy="true">Loading real task evidence…</section>;
  if(!query.data)return <section className="panel" role="alert">Real task evidence could not be loaded. Refresh to try again.</section>;
  const row=query.data,result=row.result;
  const stages:Record<string,string>={pending:'Waiting',cancel_requested:'Stopping · Waiting for cleanup',cancelled:'Stopped · History retained',running:'Implementing in isolation',checking:'Checking real changes',
    checked:'Real candidate checked',failed:'Real attempt needs attention',blocked:'Needs attention · Permission or setup',interrupted:'Interrupted attempt retained'};
  return <section className="panel candidate-review real-execution-review" aria-label="Real task and candidate review">
    <Link to="/executions">All recorded runs</Link>
    {query.isError&&<p role="alert">Refresh unavailable. Showing the last loaded evidence.</p>}
    <h1>{row.title}</h1>
    {row.workflow_task&&<p><Link to={'/intake/'+encodeURIComponent(row.workflow_task.intake_id)}>Back to the submitted task</Link></p>}
    {row.current_task===false&&<p className="soft-callout">Earlier task inputs. This result remains in history; current task approval or scope has changed.</p>}
    <h2>{row.recovery_pending?'Waiting for safe recovery':stages[row.status]||row.status}</h2><p>{row.status==='checked'&&row.publication_requested?'Changes completed and independently checked. PR delivery is recorded below; acceptance and merge remain separate.':row.message}</p>
    {row.recovery_pending&&<p className="soft-callout">Saved evidence is retained. Implementation will not run again; other execution stays on hold until cleanup is confirmed.</p>}
    <p className="soft-callout">{row.publication_requested?'Real task · Offline checks · Draft delivery follows this task’s explicit budget. No automatic merge or fallback.':'Real task · Offline checks only · Publication and fallback disabled.'}</p>
    {!row.current_grant&&<p>Historical record. The execution grant is no longer current.</p>}
    {result?.authorship?.kind==='explicit-current-session'&&<p>Completed by Codex in the explicitly authorized session, retaining the earlier failed patch’s contribution. This is not a Qwen success or an automatic fallback.</p>}
    {result?.usage&&<p className="small-text">{result.usage.status==='complete'?`Recorded generated tokens: ${result.usage.values?.completion_tokens??'unavailable'}.`:'Token usage is unavailable or incomplete; it is not recorded as zero.'}</p>}
    {result?.inference_ran===false&&!result.authorship&&<p>{['running','checking'].includes(row.status)&&!result.handler_returned?'Model activity has not yet been reported. The run is still in progress.':'No model implementation ran.'}</p>}
    {result?.inference_ran===null&&<p>Inference completion is uncertain. The attempt remains consumed.</p>}
    {result?.error&&<p role="alert">{result.error}</p>}
    {result?.gates&&<ul>{result.gates.map(g=><li key={g.name}>{g.name}: {g.status} — {g.evidence}</li>)}</ul>}
    <h3>Review real changes</h3>
    {result?.files?.length?result.files.map(file=><DiffFile key={file.path} file={file}/>):<p>No frozen real candidate is available.</p>}
    <h3>Independent verification</h3>
    <p>{result?.verification||'Not performed'}. No simulator or physical-client verification.</p>
    {result?.checks?.map((c,i)=><details key={i}><summary>Check {i+1}: {c.status} · exit {c.rc??'unknown'}</summary><pre className="candidate-diff" tabIndex={0}>{c.out}</pre></details>)}
    <PublicationStatus identity={row.id}/>
    <details><summary>Task, grant and candidate provenance</summary>
      <p>Exact base: {row.base}</p><p>Grant: {row.grant_id} · expires {row.expires_at}</p>
      <p>Candidate: {result?.candidate_id||'Unavailable'}</p><p>Submitted task: {row.workflow_task?.task_id||'Standalone authorized task'}</p><p>Task snapshot: {row.workflow_task?.binding||'Standalone grant'}</p><p>{row.publication_requested?'PR delivery is recorded separately above. Checks grant no acceptance, merge or deployment permission.':'No PR was created. Checks grant no acceptance or publication permission.'}</p>
    </details>
  </section>;
}
