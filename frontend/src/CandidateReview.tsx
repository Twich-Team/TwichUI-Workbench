import {useState} from 'react';
import {DiffText} from './DiffText';
import {useQuery} from '@tanstack/react-query';
import type {components} from './api.generated';
import {ApiError, mutate, request} from './api';
import {Button} from './components/ui/button';
import {formatTime} from './Catalog';

type Context = components['schemas']['PreparationContext'];
type Settings = components['schemas']['PreparationSettings'];
type Command = components['schemas']['PreparationRequest'];
type Receipt = components['schemas']['PreparationReceipt'];
type File = components['schemas']['FileComparison'];
type Saved = {candidateId:string;body:Command};
const categories:Settings['category'][] = ['Bug fix','Compatibility','Reliability','Performance','Usability / visual','Feature','Tests','Maintenance'];
const labels:Record<Receipt['status'],string> = {pending:'Waiting',preparing:'Preparing',reconciling:'Checking the saved sample result',prepared:'Sample PR prepared',rejected:'Sample PR rejected',blocked:'Sample PR needs attention',stale:'Sample PR inputs changed',failed:'Sample PR could not finish'};
function stored<T>(key:string):T|null {try{return JSON.parse(localStorage.getItem(key)||'null');}catch{return null;}}

export function DiffFile({file}:{file:File}) {
  return <article className="candidate-file">
    <h5>{file.old_path?`${file.old_path} → `:''}{file.path}</h5>
    <p>{file.kind}{file.additions!==null && file.additions!==undefined?` · +${file.additions} / −${file.deletions}`:''}</p>
    {file.reason && <p className="soft-callout">{file.reason}</p>}
    {file.diff!==null && file.diff!==undefined && <pre className="candidate-diff" tabIndex={0} aria-label={`Unified diff for ${file.path}`}>{file.diff?<DiffText text={file.diff} path={file.path}/>:'No text changes.'}</pre>}
    {file.kind==='renamed' && !file.diff && <p>The path changed; text is unchanged.</p>}
  </article>;
}

export function CandidateReview({intakeId,taskId,candidateId}:{intakeId:string;taskId:string;candidateId:string}) {
  const root = `/api/intake/${encodeURIComponent(intakeId)}/tasks/${encodeURIComponent(taskId)}/candidates/`;
  const key = `sample-pr:${intakeId}:${taskId}`;
  const seenKey = `sample-review:${intakeId}:${taskId}`;
  const [open,setOpen] = useState(false);
  const [lastSeen] = useState<string|null>(()=>stored<string>(seenKey));
  const [openedFingerprint,setOpenedFingerprint] = useState<string|null>(null);
  const [draft,setDraft] = useState<Saved|null>(()=>stored<Saved>(key));
  const [saved,setSaved] = useState<Receipt|null>(null);
  const [title,setTitle] = useState<string|null>(null);
  const [category,setCategory] = useState<Settings['category']>('Feature');
  const [fakeOutcome,setFakeOutcome] = useState<Settings['fake_outcome']>('success');
  const [sending,setSending] = useState(false);
  const [error,setError] = useState('');
  const [rejectedSave,setRejectedSave] = useState(false);
  const query = useQuery({queryKey:['candidate-review',intakeId,taskId,candidateId],queryFn:()=>request<Context>(root+encodeURIComponent(candidateId)+'/review'),refetchInterval:1000});
  const data = query.data;
  const receipt = data?.history.find(r=>r.id===draft?.body.command_id) || saved || data?.history[0];
  const settings:Settings|undefined = data?(draft?.candidateId===candidateId?draft.body.settings:{...data.settings,title:title??data.settings.title,category,fake_outcome:fakeOutcome}):undefined;
  const bodyPreview = data?.preview.body.replace('\nCategory: '+data.settings.category,'\nCategory: '+(settings?.category||category));
  const duplicate = data?.history.some(r=>r.candidate_id===candidateId && r.review_fingerprint===data.review.fingerprint && r.settings.title===settings?.title && r.settings.category===category && r.settings.fake_outcome===fakeOutcome);
  const active = data?.history.some(r=>['pending','preparing','reconciling'].includes(r.status));
  const canPrepare = Boolean(data?.review.eligible && settings?.title.trim() && openedFingerprint===data?.review.fingerprint && !draft && !active && !duplicate && !query.isError);

  function rememberReview() {
    if(!data) return;
    const changed = open && openedFingerprint!==data.review.fingerprint;
    setOpen(changed || !open);
    if(changed || !open) {setOpenedFingerprint(data.review.fingerprint);try{localStorage.setItem(seenKey,JSON.stringify(data.review.fingerprint));}catch{/* Request storage is required separately. */}}
  }
  async function send(recover=false) {
    let input = recover?draft:null;
    if(!input) {
      if(!canPrepare || !data || !settings) return;
      input = {candidateId,body:{command_id:crypto.randomUUID(),mode:'fixture',review_fingerprint:data.review.fingerprint,settings}};
      try{localStorage.setItem(key,JSON.stringify(input));setDraft(input);}catch{setError('Browser storage is needed to preserve this exact request for recovery.');return;}
    }
    setSending(true);setError('');setRejectedSave(false);
    try {setSaved(await mutate<Receipt>(root+encodeURIComponent(input.candidateId)+'/sample-pr',input.body));await query.refetch();}
    catch(e){setError(e instanceof Error?e.message:'The request could not be confirmed.');setRejectedSave(e instanceof ApiError && [404,409,422].includes(e.status));}
    finally{setSending(false);}
  }
  return <section className="candidate-review admission-rehearsal" aria-label="Change review and sample PR">
    <h3>Review changes and prepare a sample PR</h3>
    <p>Review the saved label examples, then test a draft PR preview. No addon files changed and no real PR will be created.</p>
    <Button variant="secondary" disabled={query.isPending||query.isError} aria-expanded={open} onClick={rememberReview}>{open && openedFingerprint!==data?.review.fingerprint?'Review updated changes':'Review changes'}</Button>
    {query.isPending && <p role="status">Loading change review…</p>}
    {query.isError && <p role="alert">Change review could not be refreshed. Preparation is held. <Button variant="secondary" onClick={()=>void query.refetch()}>Reload change review</Button></p>}
    {open && data && settings && <div className="candidate-review-content">
      <h4>{data.review.title}</h4><p>{data.review.purpose}</p>
      <p><strong>Synthetic sample comparison</strong> · Category: {settings.category}<br/>{data.review.risk}</p>
      <p>The comparison shows original label inputs against saved sample outputs. It is not a Git or addon code diff.</p>
      {((lastSeen && lastSeen!==data.review.fingerprint) || (openedFingerprint && openedFingerprint!==data.review.fingerprint)) && <p role="status" className="soft-callout">This candidate or its evidence differs from the comparison previously opened. Review it again before preparing.</p>}
      <p role="status">{query.isError?'Last loaded approval and checks are shown; current validity could not be refreshed.':data.review.eligible?'Current approval and sample checks confirmed.':data.review.message}</p>
      {data.review.files.map((f,i)=><DiffFile key={i} file={f}/>)}
      <h4>Checks and evidence</h4>
      {data.review.verification?<><p>{query.isError?'Last loaded sample check':data.review.eligible?'Current sample check':'Recorded sample check (current validity not confirmed)'}: {String(data.review.verification.verification)}</p>{((Array.isArray(data.review.verification.checks)?data.review.verification.checks:[]) as {label:string;passed:boolean}[]).filter(c=>typeof c.label==='string' && typeof c.passed==='boolean').map((c,i)=><p key={i}>{c.passed?'Passed':'Did not pass'} · {c.label}</p>)}</>:<p>No sample verification is recorded. Choose Check this sample above first.</p>}
      <ul>{data.review.limitations.map(v=><li key={v}>{v}</li>)}</ul>
      <details><summary>Scope and exact comparison identities</summary>
        <ul>{data.review.scope.map(v=><li key={v}>{v}</li>)}</ul>
        <p>Candidate: {data.review.candidate_id}</p><p>Synthetic base SHA-256: {data.review.base_identity||'Unavailable'}</p>
        <p>Artifact SHA-256: {data.review.artifact_sha256||'Unavailable'}</p><p>Review fingerprint: {data.review.fingerprint}</p>
        <p>Git base revision: unconfigured. These sample inputs do not establish a repository base.</p>
      </details>
      <h4>Draft PR preview</h4>
      <p><strong>Sample only · Draft fixed on · Real publication disabled</strong><br/>Target repository: unconfigured. Base branch: unconfigured.</p>
      <label htmlFor={`pr-title-${candidateId}`}>Sample PR title</label>
      <input id={`pr-title-${candidateId}`} maxLength={160} value={settings.title} disabled={Boolean(draft)||!data.review.eligible} onChange={e=>setTitle(e.target.value)}/>
      <label htmlFor={`pr-category-${candidateId}`}>Primary category</label>
      <select id={`pr-category-${candidateId}`} value={settings.category} disabled={Boolean(draft)||!data.review.eligible} onChange={e=>setCategory(e.target.value as Settings['category'])}>{categories.map(v=><option key={v}>{v}</option>)}</select>
      <pre className="pr-body-preview" tabIndex={0} aria-label="Sample PR body preview">{bodyPreview}</pre>
      <details><summary>Controlled publisher test settings</summary>
        <label htmlFor={`pr-outcome-${candidateId}`}>Simulated publisher outcome</label>
        <select id={`pr-outcome-${candidateId}`} value={settings.fake_outcome} disabled={Boolean(draft)||!data.review.eligible} onChange={e=>setFakeOutcome(e.target.value as Settings['fake_outcome'])}><option value="success">Success</option><option value="reject">Rejection</option><option value="lost-response">Lost response, then reconcile</option></select>
        <p>Uses only durable local sample records. No network, Git push, credentials or provider is involved.</p>
      </details>
      {!draft && <><Button disabled={sending||!canPrepare} onClick={()=>void send()}>Prepare sample PR</Button>{active && <p>Another sample preparation is waiting. Its saved state is shown below.</p>}{duplicate && <p>This preview already has a saved request. Open its history, or change the title, category or test setting for a new preview.</p>}</>}
    </div>}
    {sending && <p role="status">Saving the sample PR request…</p>}
    {error && <div role="alert"><p>{rejectedSave?'The request was rejected. Your browser inputs are kept. Review the explanation before starting a new request.':'We could not confirm this request. Its exact inputs are kept; recover it to check whether it was saved.'}</p><p>{error}</p></div>}
    {receipt && <div className={`outcome outcome-${receipt.status}`} role="status"><div>
      <h4>{labels[receipt.status]}</h4><p>{receipt.message}</p>
      {!receipt.current_eligible && <p>Historical result only. Current approval, candidate or checks are no longer eligible. {receipt.current_message}</p>}
      {receipt.result && <p>Simulated result: {String(receipt.result.logical_id)}{receipt.result.reconciled?' · Recovered from saved simulated evidence':''}. This is not a PR URL.</p>}
      {receipt.finished_at && <p>Finished {formatTime(receipt.finished_at)}</p>}
      <details><summary>Saved preview and preparation evidence</summary><p>{receipt.preview.title} · {receipt.preview.category} · Draft</p><pre className="pr-body-preview" tabIndex={0}>{receipt.preview.body}</pre><pre>{JSON.stringify(receipt.result,null,2)}</pre></details>
    </div></div>}
    {draft && <div className="intake-actions"><Button variant="secondary" disabled={sending} onClick={()=>void send(true)}>Recover saved sample PR request</Button>
      {(rejectedSave || (receipt && !['pending','preparing','reconciling'].includes(receipt.status))) && <Button variant="secondary" onClick={()=>{try{localStorage.removeItem(key);}catch{setError('Browser storage could not be updated; the original request is kept.');return;}setDraft(null);setSaved(null);setError('');setRejectedSave(false);}}>{rejectedSave?'Review latest inputs for a new request':'Start another sample preview'}</Button>}
    </div>}
    {data && data.history.length>0 && <details><summary>Sample PR history ({data.history.length})</summary>
      <p>Recorded outcomes are kept independently of current eligibility. Showing the latest 20 requests.</p>
      {data.history.map(r=><article key={r.id}><h4>{r.preview.title}</h4><p>{labels[r.status]} · {r.preview.category} · {formatTime(r.created_at)}</p><p>{r.message}</p><p>{query.isError?'Last loaded result; current eligibility could not be refreshed.':r.current_eligible?'Current sample evidence remains eligible.':'Earlier result; current evidence is not eligible. '+r.current_message}</p><p>Request {r.id} · Candidate {r.candidate_id}</p><details><summary>Historical preview</summary><pre className="pr-body-preview" tabIndex={0}>{r.preview.body}</pre><pre>{JSON.stringify(r.result,null,2)}</pre></details></article>)}
    </details>}
  </section>;
}
