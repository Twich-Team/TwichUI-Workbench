import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, ArrowRight, RefreshCw } from 'lucide-react';
import type { components } from './api.generated';
import { mutate, request } from './api';
import { Button } from './components/ui/button';
import { StateBadge, formatTime } from './Catalog';
import { Admission } from './Admission';
import { Implementation } from './Implementation';
import { TaskExecution } from './TaskExecution';
import { TaskJourney, TaskOverview } from './TaskOverview';
import { TaskExample } from './TaskExample';
import { minutes, providerOutcome } from './taskProgress';
import { ExactRunLimits, RunLimits } from './RunLimits';

type Input = components['schemas']['IntakeInput'];
type Intake = components['schemas']['IntakeView'];
type Page = components['schemas']['IntakePage'];
type Proposal = components['schemas']['ProposalView'];
type Decision = components['schemas']['ProposalDecisionInput'];
type Artifact = components['schemas']['ArtifactInput'];
const scopeLabels: Record<string,string> = {visual:'Changes the in-game interface',nonvisual:'Does not change the interface',unclassified:'Not sure'};
function ScopeOptions() { return <><option value="unclassified">Not sure</option><option value="visual">Changes the in-game interface</option><option value="nonvisual">Does not change the interface</option></>; }
// Translate known sample sentences only; revised plan content stays visible verbatim.
const sampleWording = (text:string) => ({
  'Normalize a synthetic record label':'Remove spaces around a sample label',
  'Provide one exact scope item for checking the existing human approval contract.':'Review a simple example plan before checking whether its task still matches your approval.',
  'A future implementation would trim surrounding whitespace from a synthetic record label.':'The example task would remove spaces at the beginning and end of a sample label, keeping the text inside.',
  'Trim surrounding whitespace from a synthetic record label without changing its interior text.':'Remove spaces at both ends of the sample label; leave the text inside unchanged.',
  'Interface changes, addon changes, provider calls, execution permission and publication.':'No screen changes, addon changes, AI calls, running code, or publishing changes.',
  "The synthetic label's surrounding whitespace is removed and its interior text is preserved.":'Spaces at both ends of the sample label would be removed; the text inside would stay the same.',
  'No implementation or independent behavior verification exists.':'Nothing has been built or tested.',
} as Record<string,string>)[text] || text;
const artifactRole = (role:string) => ({mockup:'Proposed mockup',game_context:'In-game reference',evidence:'Supporting evidence'}[role] || role);
const readStored = <T,>(key: string): T | null => { try { return JSON.parse(localStorage.getItem(key) || 'null') as T | null; } catch { return null; } };

export function IntakeActions() {
  return <div className="intake-actions"><Button asChild><Link to="/intake/new">Create a task <ArrowRight size={16}/></Link></Button><Link className="back-link" to="/intake">Submitted tasks</Link><Link className="back-link" to="/executions">Run results</Link></div>;
}

export function IntakeList() {
  const [params,setParams] = useSearchParams();
  const page = Math.min(1000, Math.max(1, Math.floor(Number(params.get('page'))) || 1));
  const query = useQuery({queryKey:['intake',page], queryFn:()=>request<Page>(`/api/intake?page=${page}`), refetchInterval:5000});
  return <div className="intake-page"><Link className="back-link" to="/"><ArrowLeft size={15}/>Work</Link>
    <div className="page-heading"><div><img className="heading-art" src="/art/workshop/backlog.png" alt=""/><h1>Submitted tasks</h1><p>See what needs a decision, what’s underway and what finished.</p></div></div>
    <div className="intake-actions"><Button asChild><Link to="/intake/new">Create a task</Link></Button><Link className="back-link" to="/executions">Run results <ArrowRight size={15}/></Link></div>
    <TaskOverview page={page}/>
    {query.data && query.data.total > 12 && <nav className="pagination" aria-label="Submitted task pages"><Button disabled={page===1} onClick={()=>setParams({page:String(page-1)})}>Previous</Button><span>Page {page} of {Math.ceil(query.data.total/12)}</span><Button disabled={page*12>=query.data.total} onClick={()=>setParams({page:String(page+1)})}>Next</Button></nav>}
  </div>;
}

export function IntakeForm() {
  const [params] = useSearchParams();
  const [kind, setKind] = useState<'feature' | 'issue'>(params.get('kind') === 'issue' ? 'issue' : 'feature');
  const [description, setDescription] = useState<Pick<Input,'title'|'description'>>();
  return <IntakeFormBody key={kind} kind={kind} description={description} chooseKind={(next, input)=>{
    setDescription({title:input.title,description:input.description});
    setKind(next);
  }} unified={!params.has('kind')}/>;
}
function IntakeFormBody({kind, description, chooseKind, unified}: {
  kind:'feature'|'issue'; description?:Pick<Input,'title'|'description'>;
  chooseKind:(kind:'feature'|'issue',input:Input)=>void; unified:boolean;
}) {
  const navigate = useNavigate();
  const key = `intake-draft:${kind}`;
  const [body,setBody] = useState<Input>(() => {
    const saved = readStored<Input>(key);
    if (saved && !description) return {...saved,visual_requirement:saved.visual_requirement??'unclassified'};
    return {visual_requirement:'unclassified',request_id:crypto.randomUUID(),kind,title:'',description:'',lua_error:'',...description};
  });
  const [sent,setSent] = useState(false);
  const [busy,setBusy] = useState(false);
  const [error,setError] = useState('');
  const [lua,setLua] = useState(Boolean(body.lua_error));
  const [regressionAcceptance,setRegressionAcceptance] = useState(body.regression_tests?.acceptance.join('\n')||'');
  const [regressionPaths,setRegressionPaths] = useState(body.regression_tests?.test_paths?.join('\n')||'');
  const regression=body.regression_tests;
  const example=regression?.text_example;
  const localWork=useQuery({queryKey:['local-work'],queryFn:()=>request<components['schemas']['LocalWorkStatus']>('/api/local-work'),refetchInterval:5000});
  const bugWork=kind==='issue'&&Boolean(localWork.data?.bug_investigation)&&!body.supported_task;
  const supported=useQuery({queryKey:['supported-tasks'],queryFn:()=>request<{items:{id:string;title:string;purpose:string}[]}>('/api/supported-tasks')});
  async function submit(event:React.FormEvent) {
    event.preventDefault(); setError('');
    if(example&&example.expected===example.incorrect){
      setError('The expected result and incorrect result must differ.');
      return;
    }
    try {
      localStorage.setItem(key, JSON.stringify(body)); setSent(true); setBusy(true);
      const saved = await mutate<Intake>('/api/intake',body);
      localStorage.removeItem(key); void navigate(`/intake/${saved.id}?received=1`);
    } catch (e) {setError((e as Error).message);} finally {setBusy(false);}
  }
  return <div className="intake-page task-create"><Link to="/intake" className="back-link"><ArrowLeft size={15}/>Submitted tasks</Link><div className="page-heading"><div><img className="heading-art" src={`/art/workshop/${kind==='issue'?'issues':'feature-requests'}.png`} alt=""/><h1>{unified?'Create a task':kind==='issue'?'Report an issue':'Request a feature'}</h1><p>{kind==='issue'?'Tell us what happened and what you expected. A report starts an investigation.':'Describe what you want to improve, inspect or test. You’ll see a plan before any changes are approved.'}</p></div></div>
  <form className="panel intake-form" onSubmit={event=>void submit(event)}><fieldset disabled={sent || busy}>
  <label htmlFor="request-title">Title <span>Required</span></label>
  <input id="request-title" required maxLength={160} value={body.title} onChange={e=>setBody({...body,title:e.target.value})}/>
  <label htmlFor="request-description">Description <span>Required</span></label>
  <textarea id="request-description" required maxLength={6000} rows={4} value={body.description} onChange={e=>setBody({...body,description:e.target.value})}/>
  <p className="small-text">Tell Workbench what you want to happen. For a bug, say what happens today and what you expected.</p>
  {localWork.data?.enabled&&<div className="soft-callout">
    <p><strong>Available for local execution</strong> Add coverage to an existing TwichUI Lua test for a concrete example. Workbench saves the checked result here.</p>
    {localWork.data.bug_investigation
      ? <p>Workbench can also investigate an addon bug and prepare a local repair when it can reproduce the problem offline. Describe what happened and what you expected.</p>
      : <p>Bug fixes need a separate workflow.</p>}
    <p>Features, inspections, simulator work and publishing are outside this workflow.</p>
    <p role="status">{localWork.data.message}</p>
    {localWork.data.headroom_bytes!=null&&<p className="small-text">{Math.max(0,Math.floor(localWork.data.headroom_bytes/1048576))} MiB available for saved evidence. Workbench needs {localWork.data.reserve_bytes/1048576} MiB free before starting.</p>}
  </div>}
  <details className="request-options" open={!unified || kind==='issue' || Boolean(localWork.data?.enabled || regression || body.supported_task) || undefined}>
  <summary>Task options</summary>
  {unified && <><label htmlFor="request-kind">What should Workbench do?</label>
  <select id="request-kind" value={kind} onChange={e=>chooseKind(e.target.value as 'feature'|'issue',body)}><option value="feature">Plan, inspect or test</option><option value="issue">Investigate a bug</option></select></>}
  {kind==='feature' && <>
    <label className="check-label"><input type="checkbox" checked={Boolean(regression)} onChange={e=>setBody({
      ...body,supported_task:null,local_planning:null,regression_tests:e.target.checked?{
        acceptance:regressionAcceptance.split('\n').filter(s=>s.trim()),
        test_paths:regressionPaths.split(/[\s,]+/).filter(Boolean),
      }:null,
    })}/>Add a check for existing behavior</label>
    {regression&&<>
      <p className="small-text">Tell Workbench what should happen. This route can add checks to an existing TwichUI test. Workbench will ask for anything it needs to make the check specific.</p>
      <label htmlFor="regression-acceptance">Expected test results <span>Required · One result per line, up to {localWork.data?.enabled?3:8}</span></label>
      <textarea id="regression-acceptance" required rows={3} maxLength={6000} value={regressionAcceptance} onChange={e=>{
        setRegressionAcceptance(e.target.value);
        setBody({...body,regression_tests:{...regression,acceptance:e.target.value.split('\n').filter(s=>s.trim())}});
      }}/>
      <details><summary>Optional test details</summary><label htmlFor="regression-paths">Existing test paths <span>Optional · Up to 3. You can also name them in the description.</span></label>
      <textarea id="regression-paths" rows={2} maxLength={720} placeholder="tests/test_example.lua" value={regressionPaths} onChange={e=>{
        setRegressionPaths(e.target.value);
        setBody({...body,regression_tests:{...regression,test_paths:e.target.value.split(/[\s,]+/).filter(Boolean)}});
      }}/>
      <label className="check-label"><input type="checkbox" checked={Boolean(example)} onChange={e=>setBody({
        ...body,regression_tests:{...regression,text_example:e.target.checked?{input:'',expected:'',incorrect:''}:null},
      })}/>Add a text example for the first expected result</label>
      {example&&<>
        <p className="small-text">Use these fields for one text input and a text result. Spaces count. A blank field means empty text.</p>
        {([
          ['input','Example input'],['expected','Expected example result'],['incorrect','Incorrect example result to reject'],
        ] as const).map(([field,label])=><label key={field}>{label}<input type="text" maxLength={512} value={example[field]} onChange={e=>setBody({
          ...body,regression_tests:{...regression,text_example:{...example,[field]:e.target.value}},
        })}/></label>)}
      </>}</details>
    </>}
  </>}
  {Boolean(supported.data?.items.length)&&<><label htmlFor="supported-task">Supported task <span>Optional · Fixed reviewed scope</span></label><select id="supported-task" value={body.supported_task||''} disabled={Boolean(body.regression_tests)} onChange={e=>{const item=supported.data?.items.find(i=>i.id===e.target.value);setBody({...body,supported_task:item?.id||null,local_planning:null,title:body.title||item?.title||'',description:body.description||item?.purpose||''});}}><option value="">Use my description</option>{supported.data!.items.map(item=><option key={item.id} value={item.id}>{item.title}</option>)}</select><p className="small-text">Use a ready-made task with a fixed plan. Your comments stay with the request. The fixed scope sets what can run.</p></>}
  {kind==='issue' && <><label className="check-label"><input type="checkbox" checked={lua} onChange={e=>{setLua(e.target.checked);if(!e.target.checked)setBody({...body,lua_error:''});}}/>Add a Lua error <span>Optional</span></label>{lua && <><label htmlFor="lua-error">Lua error <span>Plain text · never executed</span></label><textarea className="code-input" id="lua-error" maxLength={6000} rows={6} value={body.lua_error || ''} onChange={e=>setBody({...body,lua_error:e.target.value})}/></>}</>}</details>
  <label htmlFor="request-interface">Does this request change the interface?</label><select id="request-interface" value={body.visual_requirement} onChange={e=>setBody({...body,local_planning:e.target.value==='nonvisual'?body.local_planning:null,visual_requirement:e.target.value as Input['visual_requirement']})}><ScopeOptions/></select><p className="small-text">Your scope choice stays with this request. Choose Not sure if you haven’t decided.</p>
  {localWork.data?.enabled&&(regression||bugWork)&&<div role="group" aria-label="Planning provider">
    <label className="check-label planning-consent"><input type="checkbox" checked={body.local_planning?.startsWith('qwen-')||false} disabled={body.visual_requirement!=='nonvisual'}
      onChange={e=>setBody({...body,local_planning:e.target.checked?'qwen-once':null})}/><span>Allow one local Qwen request to {bugWork?'investigate this report':'plan this task'}</span></label>
    {localWork.data.codex_configured ? <>
      {!localWork.data.codex_available&&<p className="detail-copy">The Codex connector is unavailable. If you choose Codex first, planning can proceed with Qwen only if you allow that switch.</p>}
      {body.local_planning?.startsWith('qwen-')&&<>
        <label className="check-label planning-consent"><input type="checkbox" checked={body.local_planning==='qwen-with-codex-fallback'}
          onChange={e=>setBody({...body,local_planning:e.target.checked?'qwen-with-codex-fallback':'qwen-once'})}/><span>Allow one switch to signed-in Codex if Qwen cannot connect before generation</span></label>
        {body.local_planning==='qwen-with-codex-fallback'&&<p className="detail-copy">If Qwen cannot start and Models allows the switch, this permits sending your request, clarification and {bugWork?'relevant committed addon code and test excerpts':'relevant committed source excerpts'} to OpenAI after the account and included allowance are checked.</p>}
      </>}
      <label className="check-label planning-consent"><input type="checkbox" checked={body.local_planning?.startsWith('codex-')||false} disabled={body.visual_requirement!=='nonvisual'}
        onChange={e=>setBody({...body,local_planning:e.target.checked?'codex-once':null})}/><span>Allow one signed-in Codex turn to {bugWork?'investigate this report':'plan this task'}</span></label>
      {body.local_planning?.startsWith('codex-')&&<>
        <p className="detail-copy">This sends the request, clarification and {bugWork?'relevant committed addon code and test excerpts':'relevant committed source excerpts'} to OpenAI through the configured ChatGPT sign-in. Workbench will check the saved model and included allowance before sending task content.</p>
        <label className="check-label planning-consent"><input type="checkbox" checked={body.local_planning==='codex-with-local-fallback'}
          onChange={e=>setBody({...body,local_planning:e.target.checked?'codex-with-local-fallback':'codex-once'})}/><span>Allow one switch to local Qwen if Codex is unavailable or reaches its included allowance reserve before generation</span></label>
      </>}
    </> : <p className="detail-copy">Codex requires the installed signed-in connector.</p>}
    {bugWork&&<p className="detail-copy">Investigation reads the report and relevant committed files. It cannot edit files or publish. If a detail is missing, Workbench may ask for one reply and separate permission for a follow-up with the same provider.</p>}
    <p className="detail-copy">Choose “Does not change the interface” to enable this permission. Workbench may send this request, your clarification and relevant source excerpts to local Qwen. Permission expires after 30 minutes. Fallback applies only to planning before generation starts. Failed, partial or uncertain generation stops for review. Review the plan and approve the run separately. The run keeps the provider that made the plan.</p>
  </div>}
</fieldset>
  <p className="detail-copy">{localWork.data?.enabled?(body.local_planning?'Your request and one planning approval will be saved.':(bugWork?'Your report will be saved. Model investigation needs your permission.':'Your request will be saved. Model planning requires the test coverage option and your permission.')):'Workbench saves your description and starts planning or investigating. Supported work needs approval before a run.'}</p>{error && <p role="alert" className="form-error">{error}</p>}<Button type="submit" disabled={busy || !body.title.trim() || !body.description.trim()}>{busy?'Saving…':sent?'Retry same submission':'Submit request'}<ArrowRight size={16}/></Button>{sent && !busy && <Button variant="ghost" onClick={()=>{setSent(false);setBody({...body,request_id:crypto.randomUUID()});}}>Edit as a new submission</Button>}</form></div>;
}

export function IntakeDetail() {
  const {identity=''} = useParams();
  const client = useQueryClient();
  const [params] = useSearchParams();
  const query = useQuery({queryKey:['intake-detail',identity],queryFn:()=>request<Intake>(`/api/intake/${identity}`),refetchInterval:1200});
  const [error,setError] = useState('');
  const [busy,setBusy] = useState(false);
  const [clarificationInput,setClarification] = useState('');
  const [selected,setSelected] = useState('');
  const [pickedFile, setPickedFile] = useState('');
  useEffect(()=>{if(!selected && query.data?.proposals[0]) setSelected(query.data.proposals[0].id);},[selected,query.data]);
  async function act(suffix:string, body:unknown, method='POST') {
    setError('');setBusy(true);
    try { await mutate(`/api/intake/${identity}/${suffix}`,body,method); const refreshed = await query.refetch(); if(['scope','research-revision'].includes(suffix) && refreshed.data?.proposals[0]) setSelected(refreshed.data.proposals[0].id); void client.invalidateQueries({queryKey:['backlog']}); }
    catch(e){setError((e as Error).message);throw e;} finally {setBusy(false);}
  }
  if(!query.data) return <div className="panel" role="status">{query.isPending?'Opening request…':<>Request unavailable. <Button onClick={()=>void query.refetch()}>Try again</Button></>}</div>;
  const data = query.data;
  const p = data.proposals.find(p=>p.id===selected) || data.proposals[0];

  const sample = data.original.title === 'Synthetic feature · Admission rehearsal';
  const asking = data.status==='waiting_for_information';
  const waiting = data.jobs.find(job=>job.status==='queued'&&job.message.startsWith('Waiting for '));
  const activeJobs = data.jobs.filter(job => job.revision >= data.revision && job.status === 'failed');
  const clarification = data.result?.clarification;
  // These fixed phrases come from the deterministic regression precheck.
  const exampleNumber = data.original.regression_tests && clarification?.match(/^For expected result ([1-8])(?: \(|, no committed callable matches)/)?.[1];
  const suggestedFiles = clarification?.startsWith('Name an existing tests/test_*.lua file')
    ? data.proposals[0]?.task_contract?.path_candidates || [] : [];
  const blocked = busy || query.isError;
  const bugInvestigation=data.original.kind==='issue'&&Boolean(data.original.local_planning)&&!data.original.supported_task;
  function sendAnswer(answer: string) {
    void act('clarification',{command_id:crypto.randomUUID(),revision:data.revision,text:answer,...(bugInvestigation?{continue_investigation:true}:{})}).then(()=>setClarification('')).catch(()=>{});
  }
  const receipt = data.decisions.find(d => d.proposal_id===data.proposals[0]?.id && !d.payload.execution_continuation);
  const currentTasks = data.tasks.filter(t => t.kind==='task' && (t.proposal_id===data.proposals[0]?.id || (!t.proposal_id && !p)) && !t.scope_review_required);
  return <div className="intake-page task-detail">
    <Link className="back-link" to="/intake"><ArrowLeft size={15}/>Submitted tasks</Link>
    {params.has('received') && data.status==='queued' && <p className="soft-callout" role="status">Request received and saved. You can leave this page and come back for the next step.</p>}
    <div className="page-heading detail-heading"><div><p className="eyebrow">{sample?'Sample task':data.original.kind==='issue'?'Bug investigation':data.original.regression_tests?'Test coverage':'Task request'}</p>
      <h1>{sample?'Sample plan: tidy a label':data.original.title}</h1>{!asking && <p className="original-text task-request-copy">{data.original.regression_tests
        ? data.original.description.split('\n').filter(line=>!/^\s*Example [1-8]:\s*\{/.test(line)).join('\n').trim()
        : data.original.description}</p>}</div>
      <Button variant="ghost" onClick={()=>void query.refetch()}><RefreshCw size={15}/>Refresh</Button>
    </div>
    <TaskJourney item={data}/>
    {query.isError && <p role="alert" className="form-error">Refresh failed. Showing the last saved record. Refresh before making a decision.</p>}
    {error && <p className="form-error" role="alert">{error}</p>}
    {asking && clarification && <section className="panel question-panel" aria-labelledby="task-question">
      <h2 id="task-question">Workbench needs your help</h2>
      {exampleNumber ? <>
        <TaskExample key={data.revision} index={Number(exampleNumber)}
          expectation={data.original.regression_tests?.acceptance[Number(exampleNumber)-1] || 'Add the missing example'}
          busy={blocked} send={sendAnswer}/>
        <details><summary>Advanced example format</summary><p>{clarification}</p>
          <form onSubmit={e=>{e.preventDefault();sendAnswer(clarificationInput);}}><label htmlFor="clarification">Your answer</label><textarea id="clarification" required maxLength={1600} value={clarificationInput} onChange={e=>setClarification(e.target.value)}/>{bugInvestigation&&<p>Sending this reply allows one more read-only investigation with the same provider. Any reply and relevant source excerpts may be sent to that provider. A repair still requires approval.</p>}<Button disabled={blocked || !clarificationInput.trim()}>{bugInvestigation?'Send reply and allow follow-up':'Send information'}</Button></form>
        </details>
      </> : <>
        <p className="question-text">{suggestedFiles.length?'Workbench needs to know which existing test covers the behavior you described. Choose a file only if it fits your request.':clarification}</p>
        {suggestedFiles.length>0 && <form onSubmit={e=>{e.preventDefault();if(suggestedFiles.includes(pickedFile))sendAnswer(`Use ${pickedFile}.`);}}>
          <label htmlFor="suggested-test">Possible existing test</label>
          <select id="suggested-test" value={suggestedFiles.includes(pickedFile)?pickedFile:''} onChange={e=>setPickedFile(e.target.value)}>
            <option value="">Choose a test</option>{suggestedFiles.map(file=><option key={file}>{file}</option>)}
          </select>
          <Button disabled={blocked || !suggestedFiles.includes(pickedFile)}>Use this test</Button>
        </form>}
        <form onSubmit={e=>{e.preventDefault();sendAnswer(clarificationInput);}}><label htmlFor="clarification">Your answer</label><textarea id="clarification" required maxLength={1600} value={clarificationInput} onChange={e=>setClarification(e.target.value)}/>{bugInvestigation&&<p>Sending this reply allows one more read-only investigation with the same provider. Any reply and relevant source excerpts may be sent to that provider. A repair still requires approval.</p>}<Button disabled={blocked || !clarificationInput.trim()}>{bugInvestigation?'Send reply and allow follow-up':'Send information'}</Button></form>
      </>}
    </section>}
    {waiting&&<p role="status" className="soft-callout">{waiting.message}</p>}
    {Boolean(data.provider_activity?.length)&&<section className="soft-callout" aria-label="Task model activity">
      <h2>Model activity for this task</h2>
      <ul>{data.provider_activity?.map(attempt=><li key={attempt.id}>
        <strong>{attempt.purpose==='capability_validation'?'Cancellation check':attempt.purpose==='implementation'?'Implementation':'Planning'}</strong>
        {' · '}{attempt.backend==='local'?'Local Qwen':attempt.backend==='codex'?'Codex':attempt.backend}
        {' · '}{providerOutcome(attempt.outcome)}
        <p>{attempt.routing_reason}</p>
        {(attempt.actual_model||attempt.requested_model)&&<p className="small-text">{attempt.actual_model?'Reported model':'Selected model'} {attempt.actual_model||attempt.requested_model}</p>}
      </li>)}</ul>
    </section>}
    {activeJobs.length > 0 && <section className="panel"><h2>Why planning stopped</h2>
      {activeJobs.map(job=><div key={job.id}><p>{job.message}</p>
        {job.kind!=='behavioral_preparation' && !(data.original.local_planning&&job.kind==='planning') && !job.diagnostics.retained_failure && job.attempts<3
          ? <Button disabled={blocked} variant="secondary" onClick={()=>void act('retry',{job_id:job.id}).catch(()=>{})}>Retry planning</Button>
          : <p>Your request and any saved decisions are here. You can review the details below or start a new request with updated information.</p>}</div>)}
    </section>}
    {bugInvestigation&&data.status==='failed'&&p?.task_contract?.scope_missing[0]&&
      <section className="panel"><h2>Investigation finding</h2><p>{p.content.value}</p>
        <p>{p.task_contract.scope_missing[0]}</p><p>There is no repair or verified fix for this report.</p></section>}
    {data.status==='completed' && !p && data.result && <section className="panel"><h2>Investigation findings</h2>
      <p>{data.result.summary}</p><p className="soft-callout">These are preliminary findings. A fix, a confirmed cause and in-game behavior still need their own evidence.</p>
      {data.result.related_ids.length>0 && <details><summary>Possibly related records</summary>{data.result.related_ids.map(id=><p key={id}><Link to={`/records/fixture/${id}`}>{id}</Link></p>)}</details>}
    </section>}
    {receipt && <section className="panel saved-plan-decision" aria-label="Latest plan decision">
      <h2>{receipt.status==='pending'?'Saved · Pending application':receipt.scope_review_required||receipt.status==='stale'?'Review the current plan again':receipt.status==='applied'?({approve:'Plan approved',decline:'Plan declined',changes:'Changes requested'}[receipt.payload.action]):'Plan decision could not be applied'}</h2>
      <p>{receipt.status==='pending'?'Workbench saved your choice and will check it before applying it.':receipt.scope_review_required||receipt.status==='stale'?'The earlier decision is saved. A changed plan needs a new review.':receipt.status==='applied'?({approve:sample?'Your choice was confirmed. No addon work is permitted.':receipt.payload.run_budget?'The plan and the separately selected run permission were recorded.':'Workbench confirmed the plan. Any run needs its own permission.',decline:'Workbench closed this plan without approving the work.',changes:'Workbench saved your feedback for a revised plan.'}[receipt.payload.action]):receipt.message}</p>
      {receipt.payload.note && <p>{receipt.payload.note}</p>}
    </section>}
    {currentTasks.map(t=>
      <section className="panel planned-task" key={t.id}>{(sample || currentTasks.length>1 || t.title!==data.original.title) && <h2>{t.title}</h2>}
        {sample ? <><Admission intakeId={data.id} taskId={t.id}/><Implementation intakeId={data.id} taskId={t.id}/></> :
          <>
            {!data.original.regression_tests && !data.original.supported_task && !bugInvestigation && <><p>{t.purpose}</p><Link className="back-link" to={`/records/fixture/${t.id}`}>Open {t.title} <ArrowRight size={15}/></Link><ul>{t.acceptance.map((a,i)=><li key={i}>{a}</li>)}</ul></>}
            <TaskExecution intakeId={data.id} taskId={t.id} separateApproval={Boolean(data.original.regression_tests)||bugInvestigation} showWaiting={Boolean(data.original.supported_task)}/>
          </>}
      </section>)}
    {data.tasks.length>0 && <Link className="back-link" to={`/backlog?source=fixture&focus=intake-${data.id}`}>Open in Backlog <ArrowRight size={15}/></Link>}
    {p && <>
      {p.id!==data.proposals[0]?.id && <div className="stale-callout" role="status"><p>A newer plan is ready.</p><Button onClick={()=>setSelected(data.proposals[0].id)}>Open current proposal</Button></div>}
      {data.status==='completed'
        ? <ProposalReview key={p.id} proposal={p} data={data} act={act} busy={blocked}/>
        : <details className="supporting-plan"><summary>{asking?'Read the supporting plan':'Read the plan'}</summary><ProposalReview key={p.id} proposal={p} data={data} act={act} busy={blocked}/></details>}
    </>}
    <details className="panel planning-context">
      <summary>Original request, planning history and evidence</summary>
      <p>Saved {formatTime(data.created_at)}</p><p className="original-text">{data.original.description}</p>
      {data.original.lua_error && <><h3>Submitted error message</h3><pre>{data.original.lua_error}</pre></>}
      {data.original.regression_tests?.text_example && <><h3>Submitted text example</h3><pre>{JSON.stringify(data.original.regression_tests.text_example,null,2)}</pre></>}
      {data.clarifications.map((c,i)=><div key={i}><p>Reply for revision {c.revision} · {formatTime(c.created_at)}</p><p className="original-text">{c.text}</p></div>)}
      {p && <label className="revision-picker">{sample?'Plan version':'Proposal revision'}<select value={p.id} onChange={e=>setSelected(e.target.value)}>{data.proposals.map(plan=><option key={plan.id} value={plan.id}>{sample?'Version':'Revision'} {plan.revision}</option>)}</select></label>}
      <h3>Planning history</h3>
      {data.jobs.map(job=><div className="planning-job" key={job.id}><strong>{job.kind==='planning'?'Investigation / proposal':job.kind==='behavioral_preparation'?'Behavioral scenario preparation':'Detailed task planning'} · Revision {job.revision}</strong><StateBadge value={job.status}/><p>{job.message}</p>
        <details><summary>Job provenance</summary><p>{job.model || 'No validated model response'} · {job.attempts} bounded attempt(s)</p><pre>{JSON.stringify(job.diagnostics,null,2)}</pre></details></div>)}
      {data.decisions.length>0 && <><h3>Decision history</h3>{data.decisions.map(d=><details key={d.id}><summary>{d.payload.action} · {d.status}</summary><p>{d.message}</p><pre>{JSON.stringify(d,null,2)}</pre></details>)}</>}
      {data.evidence.map(e=><details key={e.id}><summary>{e.label}</summary><pre>{e.excerpt}</pre><p>{e.id} · SHA-256 {e.sha256} · Source revision {e.source_revision || 'Unavailable'}</p></details>)}
      <label htmlFor="priority">Priority</label><p>{data.priority.reason} Priority orders work and grants no run permission.</p>
      <select id="priority" value={data.priority.manual?String(data.priority.priority):''} disabled={blocked} onChange={e=>void act('priority',{priority:e.target.value?Number(e.target.value):null},'PUT').catch(()=>{})}><option value="">Use recommendation ({data.priority.priority})</option>{[1,2,3,4,5].map(n=><option key={n} value={n}>{n}{n===1?' · Highest':''}</option>)}</select>
      {data.tasks.filter(t=>t.kind==='task' && !currentTasks.includes(t)).map(t=><section key={t.id}>
        <h3>{t.title}</h3><Link to={`/records/fixture/${t.id}`}>Open historical task record</Link>
        {sample ? <><Admission intakeId={data.id} taskId={t.id}/><Implementation intakeId={data.id} taskId={t.id}/></> :
          <TaskExecution intakeId={data.id} taskId={t.id} separateApproval={false}/>}
      </section>)}
    </details>
  </div>;
}

function ScopeControl({data,proposal,act,busy}: {data:Intake;proposal:Proposal;act:(suffix:string,body:unknown,method?:string)=>Promise<void>;busy:boolean}) {
  type ScopeInput = components['schemas']['ScopeRevisionInput'];
  const sample = data.original.title === 'Synthetic feature · Admission rehearsal';
  const scope = data.human_scope;
  const [visual,setVisual] = useState<ScopeInput['visual_requirement']>(scope.visual_requirement);
  const [stage,setStage] = useState<ScopeInput['stage']>(scope.stage);
  const [note,setNote] = useState('');
  const [command] = useState(crypto.randomUUID());
  const current = proposal.revision===data.revision;
  return <div className="human-scope">
    <details open={scope.origin==='legacy'}><summary>Change what this plan may cover</summary>
    <p><strong>In-game changes</strong> {scopeLabels[scope.visual_requirement]}</p>
    {scope.origin==='legacy' && <p>Confirm what may change before approving this older request.</p>}<form className="scope-form" onSubmit={e=>{e.preventDefault();void act('scope',{command_id:command,proposal_id:proposal.id,binding:proposal.binding,visual_requirement:visual,stage,note}).catch(()=>{});}}><label htmlFor="review-interface">Does this request change the interface?</label><select id="review-interface" disabled={!current || busy} value={visual} onChange={e=>setVisual(e.target.value as ScopeInput['visual_requirement'])}><ScopeOptions/></select><label htmlFor="review-stage">What may Workbench plan?</label><select id="review-stage" disabled={!current || busy} value={stage} onChange={e=>setStage(e.target.value as ScopeInput['stage'])}><option value="implementation">Plan possible changes</option><option value="research">Research only</option></select><label htmlFor="scope-note">Why are you changing the scope?</label><textarea id="scope-note" required maxLength={1600} rows={2} disabled={!current || busy} value={note} onChange={e=>setNote(e.target.value)}/><p className="small-text">Creates a new proposal for review. Previous decisions and attachments stay in history; attach references to the new revision before approval.</p><Button disabled={!current || busy || !note.trim()}>Save and review the updated plan</Button></form></details>
    <details><summary>Human scope history</summary>{data.scope_history.map(s=><p key={s.revision}>Revision {s.revision}: {scopeLabels[s.visual_requirement]} · {s.stage} · {s.note}</p>)}</details></div>;
}

function ProposalReview({proposal, data, act, busy}: {proposal:Proposal;data:Intake;act:(suffix:string,body:unknown,method?:string)=>Promise<void>;busy:boolean}) {
  const [review,setReview] = useState(proposal);
  const [note,setNote] = useState('');
  const [research,setResearch] = useState('');
  const [includeRun,setIncludeRun]=useState(false);
  const [expectationChecked,setExpectationChecked]=useState(false);
  const examples=review.task_contract?.behavioral_evidence;
  const bug=review.task_contract?.bug_evidence;
  const expectationDigest=bug?.review_digest||(examples?.status==='review_required'?examples.review_digest:undefined);
  const needsExpectationReview=Boolean(expectationDigest);
  const runOffer=useQuery({enabled:Boolean(data.original.supported_task && data.status==='completed'),refetchInterval:3000,queryKey:['task-run-offer',data.id,proposal.id,proposal.binding],queryFn:()=>request<{available:boolean;message:string;offer:null|{title:string;repository:string;base_branch:string;base:string;paths:string[];budget:NonNullable<Decision['run_budget']>}}>(`/api/intake/${encodeURIComponent(data.id)}/proposals/${encodeURIComponent(proposal.id)}/run-offer`)});
  const key = `proposal-decision:${data.id}:${review.id}`;
  const [draft,setDraft] = useState<Decision|null>(()=>readStored<Decision>(key));
  const [uploadError,setUploadError] = useState('');
  const [role,setRole] = useState<Artifact['role']>('mockup');
  const [label,setLabel] = useState('');
  const [file,setFile] = useState<File|null>(null);
  const stale = review.binding !== proposal.binding || review.revision !== data.revision || review.scope_revision !== data.human_scope.revision;
  const saved = data.decisions.some(d=>d.proposal_id===review.id && d.binding===review.binding);
  const locked = busy || stale || saved || data.status !== 'completed';
  async function decide(action:Decision['action']) {
    const offer=runOffer.data?.offer;
    if (locked || (action==='approve' && offerChanged) || (action==='approve' && (review.approval_blockers.length>0 ||
      (needsExpectationReview && !expectationChecked && !draft?.expectation_review) ||
      ((includeRun || draft?.run_budget) && (!offer || !runOffer.data?.available || runOffer.isError))))) return;
    const body = draft || {command_id:crypto.randomUUID(),proposal_id:review.id,binding:review.binding,scope_revision:review.scope_revision,action,note,...(action==='approve'&&needsExpectationReview&&expectationChecked?{expectation_review:expectationDigest}:{}),...(action==='approve'&&includeRun&&runOffer.data?.offer?{run_budget:runOffer.data.offer.budget}:{})};
    try {localStorage.setItem(key,JSON.stringify(body));setDraft(body);await act('decisions',body);} catch { /* Canonical error shown above; preserve exact retry. */ }
  }
  async function upload(e:React.FormEvent) {
    e.preventDefault();setUploadError('');
    if(!file) return;
    if(file.size>1024*1024 || !['image/png','text/plain'].includes(file.type)) {setUploadError('Use PNG or plain text up to 1 MiB.');return;}
    try {
      const bytes = new Uint8Array(await file.arrayBuffer());
      let binary=''; for(let i=0;i<bytes.length;i++) binary+=String.fromCharCode(bytes[i]);
      await act('artifacts',{artifact_id:crypto.randomUUID(),proposal_id:review.id,binding:review.binding,role,label,media_type:file.type,data_base64:btoa(binary)});
    } catch(e) {setUploadError((e as Error).message);}
  }
  const c = review.content;
  const sample = data.original.title === 'Synthetic feature · Admission rehearsal';
  const offer = runOffer.data?.offer;
  const runSelected = includeRun || Boolean(draft?.run_budget);
  const runChanged = Boolean(runSelected && (!runOffer.data?.available || runOffer.isError || (draft?.run_budget &&
    (draft.run_budget.offer_fingerprint!==offer?.budget.offer_fingerprint ||
     draft.run_budget.offer_id!==offer?.budget.offer_id ||
     Boolean(draft.run_budget.activate_once)!==Boolean(offer?.budget.activate_once)))));
  const [reviewedOffer, setReviewedOffer] = useState<typeof offer>(null);
  const offerChanged = runChanged || Boolean(includeRun && (reviewedOffer?.budget.offer_fingerprint!==offer?.budget.offer_fingerprint ||
    reviewedOffer?.budget.offer_id!==offer?.budget.offer_id ||
    reviewedOffer?.budget.activate_once!==offer?.budget.activate_once));
  return <section className="panel proposal-panel">
    <div className="section-heading"><h2>{sample?`Plan · Version ${review.revision}`:'The plan'}</h2><StateBadge value={sample?'Uses sample data only':review.basis==='preparation'?(review.task_contract?.category==='lua-offline-bugfix'?'Bug repair plan':'Test coverage plan'):review.basis==='owner'?'Owner-provided plan':review.basis==='template'?'Ready-made task':review.basis==='demo'?'Sample response':'Local planning'}/></div>
    <h3 className="plan-outcome">{sample?sampleWording(c.summary):c.summary}</h3>
    {(!review.task_contract || review.task_contract.category==='lua-offline-bugfix') && <p>{sample?sampleWording(c.value):c.value}</p>}
    {review.task_contract?.category==='lua-regression-tests' ? <p>Only the selected tests may change. Addon code and shared test tools stay as they are.</p> :
      <div className="plan-scope">
        <div><h4>Included</h4><ul>{c.scope.map((v,i)=><li key={i}>{sample?sampleWording(v):v}</li>)}</ul></div>
        <div><h4>Outside the plan</h4><ul>{c.exclusions.map((v,i)=><li key={i}>{sample?sampleWording(v):v}</li>)}</ul></div>
      </div>}
    {review.task_contract
      ? <RegressionContract contract={review.task_contract} asking={data.status==='waiting_for_information'}/>
      : <><h4>What a good result looks like</h4><ul>{c.acceptance.map((v,i)=><li key={i}>{sample?sampleWording(v):v}</li>)}</ul></>}
    {review.basis==='demo' && <p>Sample plan. Addon behavior and feasibility still need evidence.</p>}
    {review.basis!=='demo' && c.uncertainty.length>0 && <p>{sample?sampleWording(c.uncertainty[0]):c.uncertainty[0]}</p>}
  {stale && <div className="stale-callout" role="alert"><strong>This review is out of date.</strong><p>Open the updated plan and attachments before deciding.</p>{review.revision===data.revision && <Button onClick={()=>{setReview(proposal);setDraft(null);setExpectationChecked(false);localStorage.removeItem(key);}}>Open refreshed review</Button>}</div>}
  {!saved && data.status==='completed' && <div className="proposal-decisions">
    <h3>Your decision</h3>
    <p>{sample?'Approving confirms this sample plan.':review.research_only?'Approving agrees to the research described here. Any later changes need their own review.':'Approve this plan so Workbench can prepare the work. Running it takes a separate decision.'}</p>
    {review.approval_blockers.length>0 && <div id="approval-prerequisites" className="approval-prerequisites" role="note"><strong>What’s needed before approval</strong><ul>{review.approval_blockers.map(b=><li key={b}>{b}</li>)}</ul></div>}
    {runOffer.data?.available && offer && <p>This ready-made task can run only if you choose its extra permission below before you approve the plan.</p>}
    {runOffer.data?.available && offer && <details className="soft-callout" open={runSelected || undefined}>
      <summary>Optional permission for this ready-made task to run and open a draft pull request</summary>
      <h4>{offer.title}</h4><RunLimits budget={offer.budget}/>
      <p>Workbench may change only the files below. If the checks pass, this also permits a branch push and one draft pull request to {offer.repository} / {offer.base_branch}, with up to {minutes(offer.budget.publication_seconds)} for delivery. Merging, releasing and installing require separate decisions.</p>
      <ul>{offer.paths.map(path=><li key={path}>{path}</li>)}</ul>
      <label className="check-label"><input type="checkbox" disabled={locked||Boolean(draft)} checked={runSelected} onChange={e=>{setIncludeRun(e.target.checked);setReviewedOffer(offer);}}/>Also allow this task to run and open one draft pull request</label>
      <details><summary>Technical run details</summary><p>Base {offer.base}</p><ExactRunLimits budget={offer.budget}/><pre>{JSON.stringify(offer.budget,null,2)}</pre></details>
    </details>}
    {offerChanged && <p role="alert">The run offer changed or could not be refreshed. Review the current permission before deciding.</p>}
    {offerChanged && <Button variant="secondary" onClick={()=>{setIncludeRun(false);setReviewedOffer(null);setDraft(null);setExpectationChecked(false);localStorage.removeItem(key);}}>Clear the earlier choice</Button>}
    {needsExpectationReview && <label className="check-label"><input type="checkbox" disabled={locked||Boolean(draft)} checked={Boolean(draft?.expectation_review)||expectationChecked} onChange={e=>setExpectationChecked(e.target.checked)}/>{bug?'The reproduction and expected behavior match my report.':'The test examples match what I asked for.'}</label>}
    <details open={Boolean(draft?.note) || undefined}><summary>Add a note or request changes</summary>
    <label htmlFor="decision-note">Note <span>Required when requesting changes</span></label>
    <textarea id="decision-note" rows={2} maxLength={1600} disabled={locked || Boolean(draft)} value={draft?.note ?? note} onChange={e=>setNote(e.target.value)}/>
    {!draft && <Button variant="secondary" disabled={locked || !note.trim()} onClick={()=>void decide('changes')}>Request changes</Button>}
    </details>
    {draft ? <Button disabled={locked || offerChanged} onClick={()=>void decide(draft.action)}>Retry same decision</Button> :
      <div className="intake-actions"><Button aria-describedby={review.approval_blockers.length>0?'approval-prerequisites':undefined}
        disabled={locked || offerChanged || review.approval_blockers.length>0 || (needsExpectationReview&&!expectationChecked)}
        onClick={()=>void decide('approve')}>{sample?'Approve this plan':includeRun&&offer?'Approve plan and allow this run':'Approve plan'}</Button>
        <Button variant="ghost" disabled={locked} onClick={()=>void decide('decline')}>Decline</Button>
      </div>}
  </div>}
  <details open={review.required_artifacts.length>0 || review.scope_conflicts.length>0 || undefined}>
    <summary>In-game scope and attachments</summary>
    <ScopeControl key={`scope-${review.id}-${data.human_scope.revision}`} data={data} proposal={review} act={act} busy={busy}/>
  <div className="proposal-artifacts">{review.required_artifacts.length>0 && <p>Add a picture of the proposed change and a picture of the current in-game screen before approval.</p>}{review.scope_conflicts.map(message=><p className="scope-conflict" role="note" key={message}>{message}</p>)}{review.artifacts.map(a=><figure key={a.id}>{a.media_type==='image/png' && <img src={`/api/planning-artifacts/${a.id}`} alt={`${artifactRole(a.role)}: ${a.label}`}/>}<figcaption><a href={`/api/planning-artifacts/${a.id}`}>{a.label}</a> · {artifactRole(a.role)} · Manually attached reference, not proof of implementation</figcaption></figure>)}
  {!locked && !draft && <details><summary>Attach a picture or supporting note</summary><form className="artifact-form" onSubmit={e=>void upload(e)}><label>Artifact role<select value={role} onChange={e=>setRole(e.target.value as Artifact['role'])}><option value="mockup">Proposed mockup</option><option value="game_context">In-game-context reference</option><option value="evidence">Supporting evidence</option></select></label><label>Artifact label<input required maxLength={160} value={label} onChange={e=>setLabel(e.target.value)}/></label><label>Private attachment · PNG or text, up to 1 MiB<input type="file" accept="image/png,text/plain" required onChange={e=>setFile(e.target.files?.[0] || null)}/></label>{uploadError && <p className="form-error" role="alert">{uploadError}</p>}<Button type="submit">Attach to this plan</Button></form></details>}</div>
  </details>
  {!locked && !draft && c.delivery==='implementation' && review.approval_blockers.length>0 && <details><summary>Revise to a research stage</summary><p>Create a new proposal revision limited to research. It will need its own review; implementation remains excluded.</p><form onSubmit={e=>{e.preventDefault();void act('research-revision',{binding:review.binding,explanation:research}).catch(()=>{});}}><label htmlFor="research-scope">Research scope explanation</label><textarea id="research-scope" required maxLength={1600} value={research} onChange={e=>setResearch(e.target.value)}/><Button disabled={!research.trim()}>Create research revision</Button></form></details>}
  {sample && <details><summary>Original plan wording</summary><pre>{JSON.stringify(c,null,2)}</pre></details>}
  <details><summary>Technical plan details</summary><p>{c.feasibility}</p>{c.uncertainty.length>0 && <ul>{c.uncertainty.map((v,i)=><li key={i}>{v}</li>)}</ul>}<p>Plan revision {review.revision} · Scope revision {review.scope_revision} · Binding {review.binding}</p><h3>Proposed behavior</h3><ul>{c.behavior.map((v,i)=><li key={i}>{v}</li>)}</ul><h3>Dependencies and risks</h3><ul>{[...c.dependencies,...c.risks].map((v,i)=><li key={i}>{v}</li>)}</ul><h3>Broad delivery stages</h3><ol>{c.stages.map((v,i)=><li key={i}>{v}</li>)}</ol><p>Evidence references: {c.evidence_ids.join(', ') || 'None cited'}</p><p>Visual impact: {c.visual}. Model: {review.model}</p></details>
  </section>;
}


function RegressionContract({contract:c, asking}: {contract:NonNullable<Proposal['task_contract']>; asking:boolean}) {
  const bug=c.category==='lua-offline-bugfix';
  const reproducer=c.bug_evidence?.plan.reproducer;
  return <section className="soft-callout" aria-label={bug?'Proposed repair check':'Proposed test'}>
    <h3>{bug?'How Workbench would check the repair':'What the test would check'}</h3>
    {reproducer&&reproducer.observations.map((o,i)=><dl className="example-values" key={i}>
      <div><dt>Expected result</dt><dd>{JSON.stringify(o.expected)}</dd></div>
      <div><dt>Incorrect result to catch</dt><dd>{JSON.stringify(o.incorrect)}</dd></div>
    </dl>)}
    <ul>{c.acceptance.map((result,i)=><li key={i}>{result}</li>)}</ul>
    {c.writable_paths.length
      ? <p className="permission-files"><strong>{bug?'Files in scope':'Tests in scope'}</strong> {c.writable_paths.map((path,i)=><span key={path}>{i>0?', ':''}<code>{path}</code></span>)}</p>
      : <p>{bug?'No files selected for repair.':'No test file selected.'}</p>}
    {c.behavioral_evidence && c.behavioral_evidence.scenarios.length>0 && <div className="test-examples">
      <h4>Examples to review</h4>
      {c.behavioral_evidence.scenarios.map(s=><div className="test-example" key={s.id}>
        <strong>Expected result {s.acceptance_index+1}</strong>
        <dl className="example-values">
          <div><dt>Given</dt><dd>{s.args.length?s.args.map((arg,i)=><code key={i} className="example-chip">{JSON.stringify(arg)}</code>):'No input'}</dd></div>
          <div><dt>Should get</dt><dd><code>{JSON.stringify(s.observe.expected)}</code></dd></div>
          <div><dt>Should catch</dt><dd><code>{JSON.stringify(s.incorrect)}</code></dd></div>
        </dl>
        <details><summary>How this example works</summary>
          <p>{s.method?'Call the method':'Use'} <code>{s.target.join('.')||'the function loaded by the test'}</code>.</p>
          <p>Check {s.observe.path.length?<code>{s.observe.path.join('.')}</code>:'the first return value'}.</p>
          <p>Runs in {s.test_path} using {s.source_paths.join(', ')}.</p>
          {s.fixture!=='module'&&<p>Test player {JSON.stringify(s.name)} with {s.addons.join(', ') || 'no addons'}.</p>}
          {s.setup.length>0 && <><p>Before the test</p><ol>{s.setup.map((step,i)=><li key={i}>{step.op==='set'
            ? <>Set {step.path.join('.')} to <code>{JSON.stringify(step.value)}</code>.</>
            : <>Call {step.path.join('.')} {step.method?'as a method ':''}with <code>{JSON.stringify(step.args)}</code>.</>}</li>)}</ol></>}
        </details>
      </div>)}</div>}
    {c.scope_missing.length>0 && !asking && <><h4>Information still needed</h4><ul>{c.scope_missing.map((m,i)=><li key={i}>{m}</li>)}</ul></>}
    <details><summary>{bug?'Technical plan and checks':'Technical test plan and checks'}</summary>
      {reproducer&&<pre>{JSON.stringify(reproducer,null,2)}</pre>}
      {c.review_feedback && <><h4>Recorded planning feedback</h4><p className="original-text">{c.review_feedback}</p></>}
      <p>{c.repository} · Base {c.resolved_base||'Unresolved'} ({c.base_ref})</p>
      <p>Proposed destination for separately authorized delivery {c.pr_repository} / {c.pr_base_branch}.</p>
      {c.path_candidates.length>0&&<p>Possible files {c.path_candidates.join(', ')}</p>}
      <h4>Proposed verification · Not run</h4><ul>{c.verification_commands.map((check,i)=><li key={i}>{check.purpose} · <code>{check.argv.join(' ')}</code> (from {check.cwd})</li>)}</ul>
      <ul>{c.independent_checks.map((check,i)=><li key={i}>{check}</li>)}</ul>
      <ul>{c.evidence.map(e=><li key={e.path}>{e.path} · SHA-256 {e.sha256}</li>)}</ul>
      <ul>{c.execution_blockers.map((b,i)=><li key={i}>{b}</li>)}</ul>
      <pre>{JSON.stringify(c.resource_limits,null,2)}</pre></details>
  </section>;
}
