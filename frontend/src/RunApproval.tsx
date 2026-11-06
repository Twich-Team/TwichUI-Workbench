import {useEffect,useRef,useState} from 'react';
import {useQuery} from '@tanstack/react-query';
import {mutate} from './api';
import {Button} from './components/ui/button';
import {approvalQuery} from './TaskOverview';
import {RunLimits} from './RunLimits';
import {minutes, type RunApprovalContext as Context} from './taskProgress';
import type {components} from './api.generated';

type Input=components['schemas']['TaskRunApprovalInput'];
type Receipt=components['schemas']['ProposalReceipt'];
type Publication=Input['run_budget']['publication'];
const readStored=<T,>(key:string):T|null=>{try{return JSON.parse(localStorage.getItem(key)||'null') as T|null;}catch{return null;}};

export function RunApproval({intakeId,taskId}:{intakeId:string;taskId:string}){
  const path=`/api/intake/${encodeURIComponent(intakeId)}/tasks/${encodeURIComponent(taskId)}/run-approval`;
  const key=`task-run-approval:${intakeId}:${taskId}`;
  const [draft,setDraft]=useState<Input|null>(()=>readStored<Input>(key));
  const [publication,setPublication]=useState<Publication>('hold');
  const [accepted,setAccepted]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const submitting=useRef(false);
  const [saved,setSaved]=useState<Receipt|null>(null);
  const query=useQuery({...approvalQuery(intakeId,taskId),refetchInterval:3000});
  const [review,setReview]=useState<Context|null>(null);
  const current=query.data;
  useEffect(()=>{if(!review&&current?.available)setReview(current);},[review,current]);
  const shown=review||current;
  const receipt=current?.receipt||saved;
  const stale=Boolean(current&&(
    (draft&&(draft.binding!==current.binding||draft.scope_approval_id!==current.scope_approval_id||
      draft.run_budget.offer_id!==current.offer?.id||draft.run_budget.offer_fingerprint!==current.offer?.fingerprint||Boolean(draft.run_budget.activate_once)!==Boolean(current.offer?.budget.activate_once)))||
    (review&&(review.binding!==current.binding||review.scope_approval_id!==current.scope_approval_id||review.offer?.id!==current.offer?.id||review.offer?.fingerprint!==current.offer?.fingerprint||
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
  if(!current)return <div role="alert"><p>The current run decision is unavailable. The plan or its permissions may have changed.</p><Button variant="secondary" onClick={()=>void query.refetch()}>Reload run decision</Button></div>;
  if(receipt)return <section className="soft-callout" aria-label="Saved task run approval">
    <h3>{receipt.status==='pending'?'Task budget saved · Waiting to apply':receipt.status==='applied'?'Task budget applied':'Task budget held'}</h3>
    <p role="status">{receipt.status==='pending'?'Workbench saved your permission. It will check the offer again before applying it.':receipt.status==='applied'?'Your decision is recorded for this one attempt.':receipt.message}</p>
    <p>{receipt.payload.run_budget?.publication==='draft-pr'?'One draft PR was authorized for this run.':'Local implementation only. Publication requires separate approval.'}</p>
    {(!receipt.payload.run_budget?.activate_once||!current.available)&&<p>{current.message}</p>}
    <p>Reloading keeps this decision. This approval cannot create another attempt.</p>
    <details><summary>Technical approval record</summary><p>{receipt.message}</p><pre>{JSON.stringify(receipt,null,2)}</pre></details>
  </section>;
  if(!shown?.available||!shown.offer)return <section className="soft-callout" aria-label="Task preparation hold">
    <h3>{current.preparing?'Preparing the reviewed task':'Task preparation held'}</h3><p role="status">{current.preparing?'Workbench is checking that the test can catch the wrong result.':'Workbench could not confirm the current test and plan are ready to run.'}</p>
    {!current.preparing && <p>Read the reason below. If the approved work has changed, return to the current plan or submit an updated request.</p>}
    <p>{current.message}</p>
  </section>;
  const {offer,scope}=shown;
  const budget=offer.budget;
  const testOnly=scope?.task_contract?.category==='lua-regression-tests';
  const mode=draft?.run_budget.publication??publication;
  const draftPR=mode==='draft-pr';
  return <section className="soft-callout run-approval" aria-label="Certified task run approval">
    <h3>Review and approve implementation</h3>
    <p>{offer.title}</p>
    <p>Plan approval prepared this task. This decision gives Workbench permission for one run in a separate copy.</p>
    <h4>What Workbench can change</h4>
    {testOnly?<p>Add checks to the existing test files below.</p>:<ul>{scope?.content.scope.map((s,i)=><li key={i}>{s}</li>)}</ul>}
    <ul>{offer.paths.map(p=><li key={p}><code>{p}</code></li>)}</ul>
    <h4>What stays outside the work</h4>
    {testOnly?<p>Addon code, existing checks and shared testing tools stay as they are.</p>:<ul>{scope?.content.exclusions.map((s,i)=><li key={i}>{s}</li>)}</ul>}
    <h4>What the checks should show</h4>
    <ul>{scope?.content.acceptance.map((s,i)=><li key={i}>{s}</li>)}</ul>
    <h4>Limits for this run</h4>
    <RunLimits budget={budget}/>
    <details><summary>Technical run details</summary>
      <p>{current.message}</p>
      <h4>Exact plan scope and exclusions</h4><ul>{[...(scope?.content.scope || []),...(scope?.content.exclusions || [])].map((s,i)=><li key={i}>{s}</li>)}</ul>
      <p>Repository {offer.repository} · Base {offer.base}</p>
      <h4>Required checks</h4><ul>{offer.checks.map((c,i)=><li key={i}>{c.purpose} <code>{c.argv.join(' ')}</code> (from {c.cwd})</li>)}</ul>
      <p>At most {budget.response_tokens.toLocaleString()} tokens per response.</p>
      <pre>{JSON.stringify(offer.limits,null,2)}</pre><p>Offer fingerprint {offer.fingerprint}</p>
    </details>
    {query.isError&&<p role="alert">Offer refresh failed. Refresh before approving.</p>}
    {error&&<p role="alert">{error}{!stale&&draft?' Your exact decision is retained for retry.':''}</p>}
    {stale?<><p role="alert">This run offer changed or is no longer available. Review the current task before deciding.</p>
      <Button variant="secondary" disabled={busy} onClick={reviewAgain}>Review current offer</Button></>:<>
      <fieldset disabled={busy||Boolean(draft)}>
        <legend>Where the result may go</legend>
        <label className="check-label"><input type="radio" name={key} checked={!draftPR} onChange={()=>choose('hold')}/>Local implementation only</label>
        <label className="check-label"><input type="radio" name={key} checked={draftPR} onChange={()=>choose('draft-pr')}/>Also authorize one draft PR</label>
      </fieldset>
      {draftPR?<p>After the checks pass, Workbench may push a branch and open one draft pull request in {offer.repository} / {offer.base_branch} for review. Delivery has up to {minutes(budget.publication_seconds)} within the overall limit. Merging, releasing and installing require separate decisions.</p>:
        <p>Workbench can make and check the change locally. Publishing, merging, releasing and installing each require separate permission.</p>}
      <label className="check-label"><input type="checkbox" checked={accepted||Boolean(draft)} disabled={busy||Boolean(draft)} onChange={e=>setAccepted(e.target.checked)}/>
        {draftPR?'I approve this exact task, expected behavior, scope, limits, and one draft PR.':'I approve this exact task, expected behavior, scope, and limits for local implementation.'}
      </label>
      <Button disabled={busy||query.isError||(!accepted&&!draft)} onClick={()=>void approve()}>
        {busy?'Saving approval…':draft?'Retry same task approval':!budget.activate_once?'Save approval · Execution unavailable':draftPR?'Approve implementation and one draft PR':'Approve local implementation'}
      </Button>
    </>}
    <p className="small-text">Workbench saves the decision before applying it. You can leave the task unapproved if you want work to stay paused.</p>
  </section>;
}
