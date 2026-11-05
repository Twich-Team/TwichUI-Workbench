import {useState} from 'react';
import {useQuery} from '@tanstack/react-query';
import type {components} from './api.generated';
import {mutate, request} from './api';
import {Button} from './components/ui/button';
import {formatTime} from './Catalog';
import {CandidateReview} from './CandidateReview';

type Context = components['schemas']['ImplementationContext'];
type Command = components['schemas']['SimulatedImplementationRequest'];
type Receipt = components['schemas']['ImplementationReceipt'];
type CheckCommand = components['schemas']['CandidateVerificationRequest'];
type Check = components['schemas']['CandidateVerificationReceipt'];
type SavedCheck = {candidateId:string; body:CheckCommand};

function readSaved<T>(key:string):T|null {
  try {return JSON.parse(localStorage.getItem(key)||'null');} catch {return null;}
}
const outcome = {pending:'Sample request saved',completed:'Sample prepared',stale:'Sample request is out of date',blocked:'Sample needs an approved plan',failed:'Sample could not be prepared'};
const checkOutcome = {pending:'Waiting for sample check',completed:'Sample check finished',stale:'Sample check is out of date',blocked:'Sample check needs an approved plan',failed:'Sample check could not finish'};

export function Implementation({intakeId,taskId}:{intakeId:string;taskId:string}) {
  const path = `/api/intake/${encodeURIComponent(intakeId)}/tasks/${encodeURIComponent(taskId)}/implementation`;
  const sampleKey = `fixture-implementation:${intakeId}:${taskId}`;
  const checkKey = `fixture-candidate-check:${intakeId}:${taskId}`;
  const [draft,setDraft] = useState<Command|null>(()=>readSaved(sampleKey));
  const [checkDraft,setCheckDraft] = useState<SavedCheck|null>(()=>readSaved(checkKey));
  const [saved,setSaved] = useState<Receipt|null>(null);
  const [savedCheck,setSavedCheck] = useState<Check|null>(null);
  const [sending,setSending] = useState(false);
  const [error,setError] = useState('');
  const query = useQuery({queryKey:['implementation',intakeId,taskId],queryFn:()=>request<Context>(path),refetchInterval:1000});
  const data = query.data;
  if(data && !data.supported && !data.receipts.length) return null;
  const authority = data?.current_authority;
  const receipt = data?.receipts.find(r=>r.id===draft?.command_id) || saved || data?.receipts[0];
  const candidate = receipt?.candidate;
  const check = candidate?.verification || (savedCheck?.candidate_id===candidate?.id?savedCheck:null);
  const current = Boolean(authority?.eligible && receipt?.binding===authority.binding);
  const canMake = Boolean(authority?.eligible && !data?.receipts.some(r=>r.binding===authority.binding || r.status==='pending'));

  async function send(kind:'sample'|'check') {
    let route = path;
    let body:Command|CheckCommand;
    try {
      if(kind==='sample') {
        if(!draft && (!authority?.eligible || !authority.admission_id || !authority.binding)) return;
        body = draft || {command_id:crypto.randomUUID(),mode:'fixture',admission_id:authority!.admission_id!,binding:authority!.binding!};
        localStorage.setItem(sampleKey,JSON.stringify(body));setDraft(body as Command);
      } else {
        if(!candidate) return;
        const prior = checkDraft?.candidateId===candidate.id?checkDraft:null;
        if(!prior && !candidate.current) return;
        body = prior?.body || {command_id:crypto.randomUUID(),mode:'fixture',binding:candidate.id};
        const stored = {candidateId:candidate.id,body:body as CheckCommand};
        localStorage.setItem(checkKey,JSON.stringify(stored));setCheckDraft(stored);
        route = path.replace(/implementation$/,`candidates/${encodeURIComponent(candidate.id)}/verification`);
      }
    } catch {setError('Browser storage is needed to keep this request safe for retry.');return;}
    setSending(true);setError('');
    try {
      if(kind==='sample') setSaved(await mutate<Receipt>(route,body));
      else setSavedCheck(await mutate<Check>(route,body));
      await query.refetch();
    } catch(e) {setError(e instanceof Error?e.message:'The saved request could not be confirmed.');}
    finally {setSending(false);}
  }

  return <section className="admission-rehearsal" aria-label="Sample implementation">
    <h3>Prepare and check a sample</h3>
    <p>Sample only. A simulated attempt makes a small label example inside this local workspace. It uses no AI and changes no addon files.</p>
    <p className="soft-callout"><strong>Real execution denied · Publication held</strong><br/>Approving the plan does not mean you reviewed the generated sample. A sample check is separate from human review and addon testing.</p>
    {query.isPending?<p role="status">Loading sample requests…</p>:query.isError?<p role="alert">Sample information could not be refreshed. Saved requests are kept. <Button variant="secondary" onClick={()=>void query.refetch()}>Reload samples</Button></p>:null}
    {data && <>
      <p role="status"><strong>{authority?.eligible?'Current approval checked for this sample':'A current approval check is needed'}</strong>{!authority?.eligible && <><br/>Review the latest plan and complete its task approval check above before making or checking a sample.</>}</p>
      {receipt && <div className={`outcome outcome-${receipt.status}`} role="status"><div>
        <h4>{receipt.status==='completed'&&!current?'Earlier sample prepared':outcome[receipt.status]}</h4>
        <p>{receipt.status==='pending'?'Your request is saved and waiting for the worker. No simulated attempt is recorded yet.':receipt.status==='completed'?'A simulated attempt prepared a sample. Real implementation has not run.':receipt.status==='stale'?'The plan, task or sample inputs changed after save. Review the current plan before making a new sample.':receipt.status==='blocked'?'The current plan’s approval could not be confirmed. Review it before continuing.':'The simulated attempt could not prepare a sample. Your saved request is kept.'}</p>
        {receipt.attempt && <p>Simulated attempt: {receipt.attempt.outcome==='completed'?'completed':'unsuccessful'} · Finished {formatTime(receipt.attempt.finished_at)}</p>}
        {candidate && <p>{candidate.integrity_valid?'Sample available':'Sample integrity could not be confirmed. Checking is held.'}</p>}
        {candidate && !candidate.current && <p>This earlier sample does not confirm current approval. Its recorded results are kept.</p>}
      </div></div>}
      {candidate && <div className={`outcome outcome-${check?.status||'pending'}`} role="status"><div>
        <h4>{!check?'Sample has not been checked':check.status==='completed'?(check.verification==='passed'?(candidate.current?'Sample check passed':'Earlier sample check passed'):'Sample check found a mismatch'):checkOutcome[check.status]}</h4>
        <p>{!check?(candidate.current&&candidate.integrity_valid?'Choose Check this sample to request the separate label-example checks.':'Checking is held because the sample or its current approval could not be confirmed. Your saved history is kept.'):check.status==='pending'?'Your check request is saved and waiting. No result is available yet.':check.status==='completed'?'These checks compare the saved sample with the expected label examples. They are not addon verification or human acceptance.':'The sample could not be checked against current approval. Review the plan and sample details before continuing.'}</p>
        {check?.checks.map((c,i)=><p key={i}>{c.passed?'Passed':'Did not pass'} · {String(c.label)}</p>)}
        {check?.finished_at && <p>Check finished {formatTime(check.finished_at)}</p>}
      </div></div>}
      <div className="intake-actions">
        {!draft && canMake && <Button disabled={sending||query.isError} onClick={()=>void send('sample')}>Make sample</Button>}
        {draft && !receipt && <Button disabled={sending} onClick={()=>void send('sample')}>Recover saved sample request</Button>}
        {draft && receipt && receipt.status!=='pending' && canMake && draft.binding!==authority?.binding && <Button variant="secondary" onClick={()=>{localStorage.removeItem(sampleKey);localStorage.removeItem(checkKey);setDraft(null);setCheckDraft(null);setSaved(null);setSavedCheck(null);setError('');}}>Use the latest approval for a new sample</Button>}
        {candidate?.current && candidate.integrity_valid && !check && <Button disabled={sending||query.isError} onClick={()=>void send('check')}>{checkDraft?.candidateId===candidate.id?'Recover saved sample check':'Check this sample'}</Button>}
      </div>
    </>}
    {sending && <p role="status">Saving your sample request…</p>}
    {error && <p role="alert">We could not confirm the request. Your saved request is kept; use recovery in the sample details to find out whether it was received.</p>}
    {candidate && <CandidateReview key={candidate.id} intakeId={intakeId} taskId={taskId} candidateId={candidate.id}/>}
    <details><summary>Sample content and technical details</summary>
      <p>{authority?.message}</p>{error && <p>{error}</p>}
      <p>No provider, model usage, real executor or publication action is involved. Artifact bytes and their immutable candidate are stored together in the disposable application database.</p>
      {candidate && <><p>Candidate {candidate.id}</p><pre>{candidate.artifact_text||'Sample content unavailable: integrity could not be confirmed.'}</pre><pre>{JSON.stringify(candidate.provenance,null,2)}</pre></>}
      {draft && receipt && <Button variant="secondary" disabled={sending} onClick={()=>void send('sample')}>Recover saved sample request</Button>}
      {check && checkDraft?.candidateId===candidate?.id && <Button variant="secondary" disabled={sending} onClick={()=>void send('check')}>Recover saved sample check</Button>}
      {data?.receipts.map(r=><div key={r.id}><p>Request {r.id} · {r.status} · Saved {formatTime(r.created_at)}</p><p>{r.message}</p><p>Admission {r.admission_id} · Binding {r.binding}</p>{r.attempt && <p>Attempt {r.attempt.id} · {r.attempt.outcome}</p>}{r.candidate && <p>Candidate {r.candidate.id} · Check {r.candidate.verification?.verification||'not requested'} · {r.candidate.current?'Current approval valid':'Historical sample'}</p>}</div>)}
    </details>
  </section>;
}
