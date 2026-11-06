import {useEffect,useRef,useState} from 'react';
import {useQuery} from '@tanstack/react-query';
import {request,mutate} from './api';
import {Button} from './components/ui/button';
import type {components} from './api.generated';

type Input=components['schemas']['TaskRunApprovalInput'];
type Receipt=components['schemas']['ProposalReceipt'];
type Proposal=components['schemas']['ProposalView'];
type Publication=Input['run_budget']['publication'];
type Context={available:boolean;preparing?:boolean;message:string;binding?:string;scope_approval_id?:string;scope?:Proposal;receipt?:Receipt|null;offer:null|{id:string;fingerprint:string;title:string;repository:string;base_branch:string;base:string;paths:string[];checks:{argv:string[];cwd:string}[];limits:Record<string,number>;budget:Input['run_budget']}};
const readStored=<T,>(key:string):T|null=>{try{return JSON.parse(localStorage.getItem(key)||'null') as T|null;}catch{return null;}};

export function RunApproval({intakeId,taskId}:{intakeId:string;taskId:string}){
  const path=`/api/intake/${encodeURIComponent(intakeId)}/tasks/${encodeURIComponent(taskId)}/run-approval`;
  const key=`task-run-approval:${intakeId}:${taskId}`;
  const [draft,setDraft]=useState<Input|null>(()=>readStored<Input>(key));
  const [publication,setPublication]=useState<Publication>('hold');
  const [accepted,setAccepted]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const submitting=useRef(false);
  const [saved,setSaved]=useState<Receipt|null>(null);
  const query=useQuery({queryKey:['task-run-approval',intakeId,taskId],queryFn:()=>request<Context>(path),refetchInterval:3000});
  const [review,setReview]=useState<Context|null>(null);
  const current=query.data;
  useEffect(()=>{if(!review&&current?.available)setReview(current);},[review,current]);
  const shown=review||current;
  const receipt=current?.receipt||saved;
  const stale=Boolean(current&&(
    (draft&&(draft.binding!==current.binding||Boolean(draft.run_budget.activate_once)!==Boolean(current.offer?.budget.activate_once)))||
    (review&&(review.binding!==current.binding||review.offer?.fingerprint!==current.offer?.fingerprint||
      review.offer?.budget.activate_once!==current.offer?.budget.activate_once||!current.available))
  ));

  async function approve(){
    if(submitting.current||stale||query.isError||(!accepted&&!draft)||!shown?.offer||!shown.binding||!shown.scope_approval_id)return;
    const body=draft||{
      command_id:crypto.randomUUID(),scope_approval_id:shown.scope_approval_id,binding:shown.binding,
      run_budget:{...shown.offer.budget,publication},note:'',
    };
    submitting.current=true;setBusy(true);setError('');
    try{
      localStorage.setItem(key,JSON.stringify(body));
      setDraft(body);
      setSaved(await mutate<Receipt>(path,body));
      await query.refetch();
    }catch(e){
      setError((e as Error).message);
      await query.refetch();
    }finally{
      submitting.current=false;setBusy(false);
    }
  }
  function choose(next:Publication){
    setPublication(next);setAccepted(false);
  }
  function reviewAgain(){
    setReview(current??null);setDraft(null);choose('hold');setError('');localStorage.removeItem(key);
  }

  if(query.isPending)return <p>Loading task offer…</p>;
  if(!current)return <p role="alert">Task offer could not be loaded. <Button variant="secondary" onClick={()=>void query.refetch()}>Reload task offer</Button></p>;
  if(receipt)return <section className="soft-callout" aria-label="Saved task run approval">
    <h3>{receipt.status==='pending'?'Task budget saved · Waiting to apply':receipt.status==='applied'?'Task budget applied':'Task budget held'}</h3>
    <p role="status">{receipt.message}</p>
    <p>{receipt.payload.run_budget?.publication==='draft-pr'?'One draft PR was authorized for this run.':'Local implementation only. Publication requires separate approval.'}</p>
    {(!receipt.payload.run_budget?.activate_once||!current.available)&&<p>{current.message}</p>}
    <p>Reloading keeps this decision. This approval cannot create another attempt.</p>
    <details><summary>Saved scope and budget identity</summary><p>Decision: {receipt.id}</p><p>Offer: {receipt.payload.run_budget?.offer_fingerprint}</p></details>
  </section>;
  if(!shown?.available||!shown.offer)return <section className="soft-callout" aria-label="Task preparation hold">
    <h3>{current.preparing?'Preparing the reviewed task':'Task preparation held'}</h3><p role="status">{current.message}</p>
  </section>;
  const {offer,scope}=shown;
  const budget=offer.budget;
  const mode=draft?.run_budget.publication??publication;
  const draftPR=mode==='draft-pr';
  return <section className="soft-callout" aria-label="Certified task run approval">
    <h3>Review and approve implementation</h3>
    <p>{offer.title}</p><p role="status">{current.message}</p>
    <h4>Exact scope</h4>
    <ul>{scope?.content.scope.map((s,i)=><li key={i}>{s}</li>)}</ul>
    <p>Files Workbench can change</p>
    <ul>{offer.paths.map(p=><li key={p}><code>{p}</code></li>)}</ul>
    <h4>Expected behavior</h4>
    <ul>{scope?.content.acceptance.map((s,i)=><li key={i}>{s}</li>)}</ul>
    <h4>Run limits</h4>
    <p>One local Qwen attempt · {budget.turns} turns · Up to {budget.generated_tokens.toLocaleString()} generated tokens · {budget.overall_seconds/60} minutes overall, including waiting. No paid fallback.</p>
    <details><summary>Base, checks, exclusions and limits</summary>
      <p>Repository: {offer.repository}</p><p>Base: {offer.base}</p>
      <h4>Exclusions</h4><ul>{scope?.content.exclusions.map((s,i)=><li key={i}>{s}</li>)}</ul>
      <h4>Required checks</h4><ul>{offer.checks.map((c,i)=><li key={i}><code>{c.argv.join(' ')}</code> (from {c.cwd})</li>)}</ul>
      <p>At most {budget.response_tokens.toLocaleString()} tokens per response. {budget.implementation_seconds/60} minutes for implementation and {budget.complete_run_seconds/60} minutes through verification.</p>
      <pre>{JSON.stringify(offer.limits,null,2)}</pre><p>Offer fingerprint: {offer.fingerprint}</p>
    </details>
    {query.isError&&<p role="alert">Offer refresh failed. Refresh before approving.</p>}
    {error&&<p role="alert">{error}{!stale&&draft?' Your exact decision is retained for retry.':''}</p>}
    {stale?<><p role="alert">This scope, offer or runtime changed. Review the current state before deciding.</p>
      <Button variant="secondary" disabled={busy} onClick={reviewAgain}>Review current offer</Button></>:<>
      <fieldset disabled={busy||Boolean(draft)}>
        <legend>Publication permission</legend>
        <label className="check-label"><input type="radio" name={key} checked={!draftPR} onChange={()=>choose('hold')}/>Local implementation only</label>
        <label className="check-label"><input type="radio" name={key} checked={draftPR} onChange={()=>choose('draft-pr')}/>Also authorize one draft PR</label>
      </fieldset>
      {draftPR?<p>Draft PR destination: {offer.repository} / {offer.base_branch}. This permits a branch push and one draft PR after checks pass, with up to {budget.publication_seconds/60} minutes for delivery within the overall limit. No merge or release is authorized.</p>:
        <p>Workbench can make and check the change locally. Remote pushes, draft PRs, merges and releases remain unauthorized. Any later publication requires separate explicit approval.</p>}
      <label className="check-label"><input type="checkbox" checked={accepted||Boolean(draft)} disabled={busy||Boolean(draft)} onChange={e=>setAccepted(e.target.checked)}/>
        {draftPR?'I approve this exact task, expected behavior, scope, limits, and one draft PR.':'I approve this exact task, expected behavior, scope, and limits for local implementation.'}
      </label>
      <Button disabled={busy||query.isError||(!accepted&&!draft)} onClick={()=>void approve()}>
        {busy?'Saving approval…':draft?'Retry same task approval':!budget.activate_once?'Save approval · Execution unavailable':draftPR?'Approve implementation and one draft PR':'Approve local implementation'}
      </Button>
    </>}
    <p className="small-text">Workbench saves the decision before applying it. The worker checks readiness again before starting any model work.</p>
  </section>;
}
