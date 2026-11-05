import {useEffect,useState} from 'react';
import {useQuery} from '@tanstack/react-query';
import {request,mutate} from './api';
import {Button} from './components/ui/button';
import type {components} from './api.generated';

type Input=components['schemas']['TaskRunApprovalInput'];
type Receipt=components['schemas']['ProposalReceipt'];
type Proposal=components['schemas']['ProposalView'];
type Context={available:boolean;message:string;binding?:string;scope_approval_id?:string;scope?:Proposal;receipt?:Receipt|null;offer:null|{id:string;fingerprint:string;title:string;repository:string;base_branch:string;base:string;paths:string[];checks:{argv:string[];cwd:string}[];limits:Record<string,number>;budget:Input['run_budget']}};
const readStored=<T,>(key:string):T|null=>{try{return JSON.parse(localStorage.getItem(key)||'null') as T|null;}catch{return null;}};
export function RunApproval({intakeId,taskId}:{intakeId:string;taskId:string}){
  const path=`/api/intake/${encodeURIComponent(intakeId)}/tasks/${encodeURIComponent(taskId)}/run-approval`;
  const key=`task-run-approval:${intakeId}:${taskId}`;
  const [draft,setDraft]=useState<Input|null>(()=>readStored<Input>(key));
  const [accepted,setAccepted]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const [saved,setSaved]=useState<Receipt|null>(null);
  const query=useQuery({queryKey:['task-run-approval',intakeId,taskId],queryFn:()=>request<Context>(path),refetchInterval:3000});
  const [review,setReview]=useState<Context|null>(null);
  const current=query.data;
  useEffect(()=>{if(!review&&current?.available)setReview(current);},[review,current]);
  const shown=review||current;
  const receipt=current?.receipt||saved;
  const stale=Boolean(current&&((draft&&draft.binding!==current.binding)||(review&&(review.binding!==current.binding||review.offer?.fingerprint!==current.offer?.fingerprint||review.offer?.budget.activate_once!==current.offer?.budget.activate_once||!current.available))));
  async function approve(){
    if(!shown?.offer||!shown.binding||!shown.scope_approval_id)return;
    const body=draft||{command_id:crypto.randomUUID(),scope_approval_id:shown.scope_approval_id,binding:shown.binding,run_budget:shown.offer.budget,note:''};
    setBusy(true);setError('');
    try{localStorage.setItem(key,JSON.stringify(body));setDraft(body);setSaved(await mutate<Receipt>(path,body));await query.refetch();}
    catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  }
  if(query.isPending)return <p>Loading certified task offer…</p>;
  if(!current)return <p role="alert">Task offer could not be loaded. <Button variant="secondary" onClick={()=>void query.refetch()}>Reload task offer</Button></p>;
  if(receipt)return <section className="soft-callout" aria-label="Saved task run approval"><h3>{receipt.status==='pending'?'Task budget saved · Waiting to apply':receipt.status==='applied'?'Task budget applied':'Task budget held'}</h3><p role="status">{receipt.message}</p><p>{current?.message}</p><p>Reloading keeps this decision. This approval cannot create another attempt.</p><details><summary>Saved scope and budget identity</summary><p>Decision: {receipt.id}</p><p>Offer: {receipt.payload.run_budget?.offer_fingerprint}</p></details></section>;
  if(!shown?.available||!shown.offer)return <section className="soft-callout" aria-label="Task preparation hold"><h3>Task preparation held</h3><p>{current.message}</p></section>;
  const offer=shown.offer,scope=shown.scope;
  return <section className="soft-callout" aria-label="Certified task run approval"><h3>Review one task and draft-PR budget</h3><p>{offer.title}</p><p role="status">{current.message}</p>
    <p>One local Qwen attempt · 20 turns · Up to 24,000 generated tokens · 90 minutes overall, including waiting and draft delivery. No paid fallback.</p>
    <p>Draft PR destination: {offer.repository} / {offer.base_branch}. No automatic merge or deployment.</p>
    <h4>Exact scope</h4><ul>{scope?.content.scope.map((s,i)=><li key={i}>{s}</li>)}</ul><ul>{offer.paths.map(p=><li key={p}><code>{p}</code></li>)}</ul>
    <details><summary>Bound base, checks, acceptance and limits</summary><p>Repository: {offer.repository}</p><p>Base: {offer.base}</p><h4>Acceptance</h4><ul>{scope?.content.acceptance.map((s,i)=><li key={i}>{s}</li>)}</ul><h4>Exclusions</h4><ul>{scope?.content.exclusions.map((s,i)=><li key={i}>{s}</li>)}</ul><h4>Required checks</h4><ul>{offer.checks.map((c,i)=><li key={i}><code>{c.argv.join(' ')}</code> (from {c.cwd})</li>)}</ul><p>At most 2,500 tokens per response; 20 minutes implementation, 40 minutes through verification, and 15 minutes publication within the overall 90 minutes.</p><pre>{JSON.stringify(offer.limits,null,2)}</pre><p>Offer fingerprint: {offer.fingerprint}</p></details>
    {query.isError&&<p role="alert">Offer refresh failed. Refresh before approving.</p>}
    {stale&&<p role="alert">This scope or certified offer changed. Read the current offer before deciding.</p>}
    {error&&<p role="alert">{error} Your exact decision is retained for retry.</p>}
    {stale?<Button variant="secondary" onClick={()=>{setReview(current);setDraft(null);setAccepted(false);localStorage.removeItem(key);}}>Review current offer</Button>:<><label className="check-label"><input type="checkbox" checked={accepted||Boolean(draft)} disabled={busy||Boolean(draft)} onChange={e=>{setReview(current);setAccepted(e.target.checked);}}/>I approve this exact scope, aggregate budget, and one draft PR to the destination above.</label><Button disabled={busy||query.isError||(!accepted&&!draft)} onClick={()=>void approve()}>{busy?'Saving approval…':draft?'Retry same task approval':offer.budget.activate_once?'Approve scope, budget and one draft-PR run':'Approve scope and budget · Execution unavailable'}</Button></>}
    <p className="small-text">Saved first, applied by the worker. Certification supplies evidence; your explicit decision supplies the bounded permission. Execution must pass fresh runtime gates.</p>
  </section>;
}
