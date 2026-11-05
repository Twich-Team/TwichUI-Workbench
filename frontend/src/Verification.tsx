import {useState} from 'react';
import {Link} from 'react-router-dom';
import {useQuery} from '@tanstack/react-query';
import {request,mutate,type Task} from './api';
import type {components} from './api.generated';
import {Button} from './components/ui/button';
type Receipt=components['schemas']['VerificationReceipt'];
type Command=components['schemas']['FixtureVerificationRequest'];
export function Verification({task}:{task:Task}){
 const key=`fixture-verification:${task.id}`;
 const [draft,setDraft]=useState<Command|null>(()=>{try{return JSON.parse(localStorage.getItem(key)||'null');}catch{return null;}});
 const [error,setError]=useState('');const [sending,setSending]=useState(false);
 const history=useQuery({queryKey:['verification',task.id],queryFn:()=>request<Receipt[]>(`/api/tasks/${task.id}/verification`),refetchInterval:1000});
 const receipt=history.data?.find(r=>r.id===draft?.command_id || r.version===task.version);
 async function submit(){
  const body=draft || {command_id:crypto.randomUUID(),version:task.version,mode:'fixture' as const};
  try{localStorage.setItem(key,JSON.stringify(body));}catch{setError('Browser storage is required for safe retry.');return;}
  setDraft(body);setSending(true);setError('');
  try{await mutate(`/api/tasks/${task.id}/verification`,body);await history.refetch();}catch(e){setError(e instanceof Error?e.message:'Save unconfirmed; retry the same request.');}finally{setSending(false);}
 }
 return <div className="intake-page"><Link className="back-link" to={`/tasks/${task.id}`}>← Back to candidate</Link><div className="page-heading"><div><h1>Verify fixture candidate</h1><p>{task.title}</p></div></div><section className="panel"><h2>Controlled verification rehearsal</h2><p>A durable request goes to the existing worker and a deterministic fake verifier. It checks the controlled fixture identity and behavior contract. No model, addon code or external service runs.</p><p>Provider activation and live execution are off. Publication remains held, even if fixture checks pass.</p>{history.isPending?<p role="status">Reading verification history…</p>:history.isError?<p role="alert">Verification history unavailable. <Button onClick={()=>void history.refetch()}>Retry history</Button></p>:receipt?<div role="status"><p>{receipt.version!==task.version ? 'This result belongs to an earlier candidate. The current candidate has not been verified by this request.' : 'Result is bound to this candidate version.'}</p><h3>{receipt.status==='pending'?'Request saved · Waiting for worker':receipt.status==='completed'?'Fixture checks completed':`Verification ${receipt.status}`}</h3><p>{receipt.message}</p><p>Verification: {receipt.verification} · Attempt: {receipt.attempt_outcome} · Publication: held</p><ul>{receipt.checks.map((c,i)=><li key={i}>{c.passed?'Passed':'Failed'} · {c.label}</li>)}</ul></div>:task.state!=='review_confirmed'?<div><p role="status"><strong>Contract review required</strong></p><p>Review this candidate before requesting fixture verification.</p><div className="intake-actions"><Button asChild><Link to={`/tasks/${task.id}/review`}>Review candidate</Link></Button></div></div>:draft && draft.version!==task.version?<p role="alert">The candidate changed. Your prior request remains bound to its original version. <Button onClick={()=>{localStorage.removeItem(key);setDraft(null);setError('');}}>Open current candidate for a new request</Button></p>:<Button disabled={sending} onClick={()=>void submit()}>{sending?'Saving…':draft?'Retry same verification request':'Request fixture verification'}</Button>}{error&&<p role="alert" className="form-error">{error}</p>}<details><summary>Audit and limitations</summary><p>Request version: {draft?.version||task.version}. Review confirmation admits this fixture check only. Candidate execution, independent addon verification and publication need separate permission/evidence.</p>{history.data?.map(r=><div key={r.id}><p>Request {r.id} · {r.status} · {r.verification}</p><p>Saved {r.created_at} · Started {r.started_at||'not run'} · Finished {r.finished_at||'not recorded'}</p><p>Source: deterministic fixture · Execution activation: off · Publication permission: held</p></div>)}</details></section></div>;
}
