import {useState} from 'react';
import {useQuery} from '@tanstack/react-query';
import {mutate, request} from './api';
import type {components} from './api.generated';
import {Button} from './components/ui/button';
import {formatTime} from './Catalog';

type Context = components['schemas']['AdmissionContext'];
type Command = components['schemas']['FixtureAdmissionRequest'];
type Receipt = components['schemas']['AdmissionReceipt'];

function attention(message:string) {
  if (/changed|supersed/i.test(message)) return 'The plan or task changed. Review the latest plan and approve it again if you agree.';
  if (/decomposition|provenance/i.test(message)) return 'Task preparation could not be confirmed. Reload the plan; if the task is still missing, ask for help.';
  if (/one matching task|controlled|synthetic feature/i.test(message)) return 'This task no longer matches the supported sample. Return to the sample plan and review its task.';
  if (/unavailable|could not complete/i.test(message)) return 'The approval could not be checked. Reload the plan; if the problem remains, ask for help. Your saved result is kept.';
  return 'A current approved plan is needed. Review the latest plan and approve it if you agree.';
}


export function Admission({intakeId, taskId}: {intakeId:string; taskId:string}) {
  const path = `/api/intake/${encodeURIComponent(intakeId)}/tasks/${encodeURIComponent(taskId)}/admission`;
  const key = `fixture-admission:${intakeId}:${taskId}`;
  const [draft, setDraft] = useState<Command|null>(()=>{
    try {return JSON.parse(localStorage.getItem(key)||'null');} catch {return null;}
  });
  const [error, setError] = useState('');
  const [sending, setSending] = useState(false);
  const [recovered, setRecovered] = useState<Receipt|null>(null);
  const query = useQuery({queryKey:['admission',intakeId,taskId], queryFn:()=>request<Context>(path), refetchInterval:1000});
  const data = query.data;
  if (data && !data.supported && !data.receipts.length) return null;
  const current = data?.current_authority;
  const receipt = data?.receipts.find(r=>r.id===draft?.command_id) || recovered || data?.receipts[0];
  const labels = {pending:'Waiting for approval check', admitted:'Approval check passed', blocked:"Approval check couldn’t pass", stale:'This check is out of date', failed:"Approval check couldn’t finish"};
  const matchesCurrent = Boolean(current?.eligible && receipt?.binding===current.binding);

  async function submit() {
    if (!draft && (!current?.eligible || !current.approval_id || !current.binding)) return;
    const body:Command = draft || {command_id:crypto.randomUUID(), mode:'fixture', approval_id:current!.approval_id!, binding:current!.binding!};
    try {localStorage.setItem(key, JSON.stringify(body));} catch {setError('Browser storage is required to preserve this request for safe retry.');return;}
    setDraft(body);setSending(true);setError('');
    try {setRecovered(await mutate<Receipt>(path,body));await query.refetch();}
    catch (e) {setError(e instanceof Error?e.message:'Save unconfirmed. Retry the same request.');}
    finally {setSending(false);}
  }

  return <section className="admission-rehearsal" aria-label="Approval check">
    <h3>Approval check</h3>
    <p>Uses sample data only. Check whether this task still matches the plan you approved.</p>
    <p className="soft-callout"><strong>No addon changes</strong><br/>This sample flow does not build or run anything, test the addon, or publish changes.</p>
    {query.isPending ? <p role="status">Loading approval and previous checks…</p> : query.isError ? <p role="alert">Approval information is unavailable. <Button variant="secondary" onClick={()=>void query.refetch()}>Reload approval checks</Button></p> : <>
      <div role="status"><strong>{current?.eligible?'This task has a current approved plan':'This task needs attention'}</strong>{!current?.eligible && (!receipt || ['pending','admitted'].includes(receipt.status)) && <p>{attention(current?.message||'')}</p>}</div>
      {receipt && <div className={`outcome outcome-${receipt.status}`} role="status"><div><h4>{!matchesCurrent && receipt.status==='admitted'?'Previous check passed':labels[receipt.status]}</h4><p>{receipt.status==='pending'?'Your request is saved. You can leave this page while the check waits.':receipt.status==='admitted'?(matchesCurrent?'This sample task matches the plan you approved.':'This earlier check passed, but it does not confirm approval of the task as it is now.'):receipt.status==='stale'?'The plan or task changed after this check was saved. Review the latest plan before checking again.':attention(receipt.message)}</p>{!matchesCurrent && receipt.status!=='admitted' && <p>Previous check · Does not confirm approval of the current task.</p>}<p>Saved {formatTime(receipt.created_at)} · Finished {receipt.finished_at?formatTime(receipt.finished_at):'not yet recorded'}</p></div></div>}
      <div className="intake-actions">
        {draft ? <Button variant="secondary" disabled={sending} onClick={()=>void submit()}>{sending?'Checking saved request…':'Recover saved check'}</Button> : current?.eligible && !data?.receipts.some(r=>r.binding===current.binding || r.status==='pending') ? <Button disabled={sending} onClick={()=>void submit()}>Check this task’s approval</Button> : null}
        {draft && receipt && receipt.status!=='pending' && current?.eligible && draft.binding!==current.binding && <Button variant="secondary" onClick={()=>{localStorage.removeItem(key);setDraft(null);setRecovered(null);setError('');}}>Start a check for the current task</Button>}
      </div>
    </>}
    {error && <p role="alert" className="form-error">We couldn’t confirm your request. Recover the saved check to find out whether it was received.</p>}
    <details><summary>Technical details and previous checks</summary><p>Internal checks: this supports only the exact one-item, one-task synthetic fixture with no dependencies. Proposal scope approval supplies no execution permission. No candidate, model attempt, usage, verification result or Models acknowledgment is created.</p>{error && <p>{error}</p>}<p>Current authority: {current?.eligible?'valid':'invalid'} · {current?.message}</p>{data?.receipts.map(r=><div key={r.id}><p>Request {r.id} · {r.status}</p><p>{r.message}</p><p>Approval {r.approval_id}</p><p>Binding {r.binding}</p></div>)}</details>
  </section>;
}
