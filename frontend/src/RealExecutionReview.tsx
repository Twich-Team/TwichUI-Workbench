import {useQuery} from '@tanstack/react-query';
import {Link,useParams} from 'react-router-dom';
import {request} from './api';
import {PublicationStatus} from './PublicationStatus';
import {Button} from './components/ui/button';
import {StateBadge} from './Catalog';
import {runStage} from './taskProgress';
import {DiffFile} from './CandidateReview';
import type {components} from './api.generated';

type View={id:string;title:string;status:string;message:string;base:string;current_grant:boolean;
  recovery_pending?:boolean;publication_requested?:boolean;
  grant_id:string;expires_at:string;publication_allowed:false;source:'real';
  behavior_checked?:string[];
  behavior_reproduced?:boolean;
  later_review?:{state:'rejected'|'unavailable';reason:string}|null;
  workflow_task?:{intake_id:string;task_id:string;binding:string}|null;current_task?:boolean|null;
  result:null|{inference_ran?:boolean|null;verification?:string;candidate_id?:string;
    handler_returned?:boolean;
    authorship?:{kind:string;author:string;parent_author?:string};
    usage?:{status:string;values?:{completion_tokens?:number;prompt_tokens?:number}|null};
    check_failure?:{name:string;diagnostic:string;context_hint?:{path:string;start:number;end:number}|null};
    files?:components['schemas']['FileComparison'][];
    gates?:{name:string;status:string;evidence:string}[];
    checks?:{status:string;rc:number|null;out:string;command?:string[]}[];error?:string}};

function checkName(command: string[] | undefined, i: number) {
  const [tool,...args] = command || [];
  const target = args.at(-1);
  if (tool?.startsWith('luac') && args.includes('-p')) return 'Lua syntax';
  if (tool?.startsWith('lua') && target?.endsWith('/oracle.lua')) return 'Independent behavior check';
  if (tool?.startsWith('lua') && /(?:^|\/)test_[^/]+\.lua$/.test(target || '')) return 'Focused regression test';
  if (tool === 'bash' && target === 'tests/run.sh') return 'Existing test suite';
  return `Check ${i+1}`;
}

export function RealExecutionReview(){
  const {identity=''}=useParams();
  return <ExecutionResult identity={identity}/>;
}
export function ExecutionResult({identity,embedded=false}:{identity:string;embedded?:boolean}){
  const query=useQuery({queryKey:['real-execution',identity],
    queryFn:()=>request<View>('/api/real-executions/'+encodeURIComponent(identity)),refetchInterval:3000});
  if(query.isPending)return <section className="panel" aria-busy="true">Opening the run result…</section>;
  if(!query.data)return <section className="panel" role="alert">The result could not be loaded. <Button variant="secondary" onClick={()=>void query.refetch()}>Try again</Button></section>;
  const row=query.data,result=row.result;
  const checked=row.status==='checked'&&result?.verification==='passed'&&!row.later_review;
  const active=['pending','running','checking','cancel_requested'].includes(row.status);
  const testOnly=Boolean(result?.files?.length&&result.files.every(file=>file.path.startsWith('tests/')&&(!file.old_path||file.old_path.startsWith('tests/'))));
  const workflow=row.workflow_task;
  return <div className={embedded?"inline-result":"intake-page result-page"}>
    {!embedded&&<Link className="back-link" to={workflow?'/intake/'+encodeURIComponent(workflow.intake_id):'/executions'}>{workflow?'Back to the task':'All run results'}</Link>}
    {!embedded&&<div className="page-heading"><div><img className="heading-art" src="/art/workshop/history.png" alt=""/><p className="eyebrow">RUN RESULT</p><h1>{row.title}</h1></div></div>}
    <section className="panel candidate-review real-execution-review" aria-label="Run result and checks">
      {query.isError&&<p role="alert">Refresh unavailable. Showing the last loaded result.</p>}
      {row.current_task===false&&<p className="soft-callout">This result belongs to an earlier plan. The current task still needs its own review and checks.</p>}
      <h2>{row.later_review?(row.later_review.state==='rejected'?'Later review rejected this patch':'Later review needs attention'):row.recovery_pending?'Waiting for safe recovery':checked?(testOnly?'Test changes checked':'Changes checked'):row.status==='checked'?'Run result saved':runStage[row.status]||'Recorded result'}</h2>
      {checked?<><p className="result-lead">{testOnly?'Only test files changed.':'The changes passed checks in a separate copy.'}</p>
        {row.behavior_reproduced&&<p>The reviewed check reproduced the bug in the original code and passed with this patch.</p>}
        {Boolean(row.behavior_checked?.length) && <><h3>Behavior checked</h3><ul>{row.behavior_checked?.map((behavior,i)=><li key={i}>{behavior}</li>)}</ul></>}
        <p>{row.publication_requested?'The checked copy is saved locally. See the separate draft delivery status below.':'Saved locally. This run did not install or publish the result.'}</p></>
        :<p>{row.later_review?'A later check found a problem with this patch. Review that finding before relying on these changes.':row.message}</p>}
      {row.later_review&&<><p className="soft-callout">The original check results and usage are retained below. This review does not authorize another run or publication.</p><details><summary>Why the later review stopped this result</summary><p>{row.later_review.reason}</p></details></>}
      {row.recovery_pending?<p>Workbench is waiting to confirm cleanup. It will keep the evidence and will not run the work again.</p>
        :!checked&&!active&&<p>Review the saved reason and checks below.{workflow&&' Open the task to see its current status and any available next step.'}</p>}
      {result?.inference_ran===false && !active && !checked && !result.authorship && <p>No model implementation ran.</p>}
      {result?.inference_ran===null&&<p>Workbench could not confirm how the model call ended. This attempt has been used.</p>}
      {result?.authorship?.kind==='explicit-current-session'&&<p>Codex finished the work in a separately approved session using contributions from the earlier attempt.</p>}
      {result?.check_failure&&<section aria-label="Last failed check"><h3>Last failed check</h3>
        <p>{result.check_failure.name}{result.check_failure.context_hint&&<> · {result.check_failure.context_hint.path}</>}</p>
        <details><summary>See the failure output</summary><pre className="candidate-diff check-diagnostic" tabIndex={0}>{result.check_failure.diagnostic}</pre></details>
      </section>}
      <p className="soft-callout">These checks run outside the game. Any simulated behavior is limited to what the checks cover.</p>
      {result?.checks?.length?<details><summary>Check details</summary><ul className="check-summary">{result.checks.map((c,i)=><li key={i}>
        <strong>{checkName(c.command,i)}</strong><StateBadge value={c.status==='pass'&&c.rc===0?'Passed':c.status==='fail'||(c.rc!==null&&c.rc!==0)?'Failed':'Outcome unavailable'}/>
      </li>)}</ul></details>:<p>{active?'Checks are still pending or have yet to report.':'No completed checks are recorded for this run.'}</p>}
      <h3>What changed</h3>
      {result?.files?.length ? <><ul>{result.files.map(file=><li key={file.path}>{file.path}</li>)}</ul>
        <details><summary>See the changes in code</summary>{result.files.map(file=><DiffFile key={file.path} file={file}/>)}</details></>
        :<p>No changes were saved for review.</p>}
      <PublicationStatus identity={row.id}/>
      <details><summary>Technical results and run details</summary>
        <p>Exact base {row.base}</p><p>Grant {row.grant_id} · expires {row.expires_at} · {row.current_grant?'Current':'Historical'}</p>
        <p>Candidate {result?.candidate_id||'Unavailable'}</p><p>Task {workflow?.task_id||'Standalone authorized task'}</p><p>Task snapshot {workflow?.binding||'Standalone grant'}</p>
        <p>Recorded verification {result?.verification||'Unavailable'}</p><p>{row.message}</p>
        {result?.usage&&<details><summary>Model usage record</summary><pre>{JSON.stringify(result.usage,null,2)}</pre></details>}
        {result?.inference_ran===false&&<p>Model execution was not reported for this result.</p>}
        {result?.error&&<p>{result.error}</p>}
        {result?.gates&&<ul>{result.gates.map(g=><li key={g.name}>{g.name} · {g.status} — {g.evidence}</li>)}</ul>}
        {result?.checks?.map((c,i)=><details key={i}><summary>Check {i+1} · {c.status} · exit {c.rc??'unknown'}</summary>
          {c.command&&<p><code>{c.command.join(' ')}</code></p>}<pre className="candidate-diff" tabIndex={0}>{c.out}</pre></details>)}
      </details>
    </section>
  </div>;
}
