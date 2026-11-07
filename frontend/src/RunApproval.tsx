import {useEffect,useRef,useState} from 'react';
import {useQuery} from '@tanstack/react-query';
import {mutate} from './api';
import {Button} from './components/ui/button';
import {approvalQuery} from './TaskOverview';
import {ExactRunLimits, RunLimits} from './RunLimits';
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
  const [note,setNote]=useState('');
  const [accepted,setAccepted]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const submitting=useRef(false);
  const [saved,setSaved]=useState<Receipt|null>(null);
  const query=useQuery({...approvalQuery(intakeId,taskId),refetchInterval:3000});
  const [review,setReview]=useState<Context|null>(null);
  const current=query.data;
  useEffect(()=>{if(!review&&current?.available)setReview(current);},[review,current]);
  const shown=review||current;
  const receipt=current?.receipt||saved;
  const offerRegion=useRef<HTMLElement>(null);
  const offerVisible=Boolean(shown?.available&&shown.offer&&!receipt);
  useEffect(()=>{
    if(offerVisible&&window.location.hash==='#local-run-review')offerRegion.current?.focus();
  },[offerVisible]);
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
      run_budget:{...shown.offer.budget,publication},note,
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
    <p>{receipt.payload.run_budget?.publication==='draft-pr'?'One draft PR was authorized for this run.':'The result stays in Workbench. Publication requires separate approval.'}</p>
    {current.approval_hold && <p>{current.approval_hold}</p>}
    {(!receipt.payload.run_budget?.activate_once||!current.available)&&!current.approval_hold&&<p>{current.message}</p>}
    <p>Reloading keeps this decision. This approval cannot create another attempt.</p>
    <details><summary>Technical approval record</summary><p>{receipt.message}</p><pre>{JSON.stringify(receipt,null,2)}</pre></details>
  </section>;
  if(current.local_only&&!current.available)return <section className="soft-callout" aria-label="Run waiting">
    <h3>Run is waiting</h3><p role="status">{current.message}</p>
    <p>The reviewed behavior and plan are saved. The run decision will be available when Workbench can start it safely.</p>
  </section>;
  if(!shown?.available||!shown.offer)return <section className="soft-callout" aria-label="Task preparation hold">
    <h3>{current.preparing?'Preparing the reviewed task':'Task preparation held'}</h3><p role="status">{current.preparing?'Workbench is checking that the test can catch the wrong result.':'Workbench could not confirm the current test and plan are ready to run.'}</p>
    {!current.preparing && <p>Read the reason below. If the approved work has changed, return to the current plan or submit an updated request.</p>}
    <p>{current.message}</p>
  </section>;
  const {offer,scope}=shown;
  const budget=offer.budget;
  const testOnly=scope?.task_contract?.category==='lua-regression-tests' &&
    offer.paths.length>0 && offer.paths.every(path=>path.startsWith('tests/'));
  const draftPR=(draft?.run_budget.publication??publication)==='draft-pr';
  const buttonLabel=busy?'Saving approval…':draft?'Retry same task approval':
    !budget.activate_once?'Save approval · Execution unavailable':
    draftPR?'Approve run and draft pull request':shown.provider?.backend==='codex'?'Approve Codex run':'Approve local run';
  return <section className="soft-callout run-approval" id="local-run-review" ref={offerRegion} tabIndex={-1} aria-label="Certified task run approval">
    <h3>{testOnly?'Add test coverage in a separate copy':'Run the approved work in a separate copy'}</h3>
    {testOnly?<p>Your installed addon, addon code and existing checks stay as they are.</p>:
      <><ul>{scope?.content.scope.map((s,i)=><li key={i}>{s}</li>)}</ul>
        <p><strong>Outside this run</strong></p><ul>{scope?.content.exclusions.map((s,i)=><li key={i}>{s}</li>)}</ul></>}
    <p className="permission-files"><strong>Files that may change</strong> {offer.paths.map((path,i)=><span key={path}>{i>0?', ':''}<code>{path}</code></span>)}</p>
    {scope?.content.acceptance.length ? <ul className="permission-outcomes" aria-label="What the run should check">{scope.content.acceptance.map((s,i)=><li key={i}>{s}</li>)}</ul> : null}
    {shown.local_only&&<p><strong>Provider</strong> {shown.provider?.backend==='codex'
      ? <>Codex · {shown.provider.model}{shown.provider.effort ? ' · '+shown.provider.effort : ''}. {shown.provider.fallback_reason==='local_unreachable' ? 'Qwen was unreachable before generation, so Codex prepared the plan. ' : ''}One signed-in turn sends the approved test and relevant committed source excerpts to OpenAI. Workbench checks the proposed change independently.</>
      : <>Local Qwen is the saved choice for this run. {shown.provider?.fallback_reason ? 'The approved switch occurred before Codex generated a plan. ' : ''}This approval allows one Qwen run.</>}</p>}
    <RunLimits budget={budget} codex={shown.provider?.backend==='codex'}/>
    {query.isError&&<p role="alert">The run offer could not be refreshed. Refresh before approving.</p>}
    {error&&<p role="alert">{error}{!stale&&draft?' You can retry the same decision.':''}</p>}
    {stale?<><p role="alert">This run offer changed or is no longer available. Review the current task before deciding.</p>
      <Button variant="secondary" disabled={busy} onClick={reviewAgain}>Review current offer</Button></>:<>
      <p><strong>{draftPR?'One draft pull request allowed':'Result stays local.'}</strong>{draftPR?
        <> After checks pass, Workbench may push a branch and open one draft pull request at {offer.repository} / {offer.base_branch}. Delivery has up to {minutes(budget.publication_seconds)}. Merging, releasing and installing need separate decisions.</>:null}</p>
      {!current.local_only&&<details open={draftPR || undefined}>
        <summary>Publishing options</summary>
        <fieldset disabled={busy||Boolean(draft)} aria-label="Where the result may go">
          <label className="check-label"><input type="radio" name={key} checked={!draftPR} onChange={()=>choose('hold')}/>Keep the result local</label>
          <label className="check-label"><input type="radio" name={key} checked={draftPR} onChange={()=>choose('draft-pr')}/>Also allow one draft pull request</label>
        </fieldset>
      </details>}
      <label className="check-label"><input type="checkbox" checked={accepted||Boolean(draft)} disabled={busy||Boolean(draft)} onChange={e=>setAccepted(e.target.checked)}/>
        {draftPR?'I reviewed the run and publishing permission.':'I reviewed what this run allows.'}
      </label>
      <Button disabled={busy||query.isError||(!accepted&&!draft)} onClick={()=>void approve()}>{buttonLabel}</Button>
    </>}
    <details>
      <summary>Technical run details</summary>
      <label>Run approval note <span>Optional</span><textarea maxLength={1600} value={draft?.note ?? note}
        disabled={busy||Boolean(draft)} onChange={e=>{setNote(e.target.value);setAccepted(false);}}/></label>
      <p>{current.message}</p><ExactRunLimits budget={budget} codex={shown.provider?.backend==='codex'}/>
      <p>If the run starts, Workbench checks that it can cancel its own work and restores normal model access afterward.</p>
      <h4>Exact plan scope and exclusions</h4><ul>{[...(scope?.content.scope || []),...(scope?.content.exclusions || [])].map((s,i)=><li key={i}>{s}</li>)}</ul>
      <p>Repository {offer.repository} · Base {offer.base}</p>
      <h4>Required checks</h4><ul>{offer.checks.map((c,i)=><li key={i}>{c.purpose} <code>{c.argv.join(' ')}</code> (from {c.cwd})</li>)}</ul>
      <pre>{JSON.stringify(offer.limits,null,2)}</pre><p>Offer fingerprint {offer.fingerprint}</p>
    </details>
  </section>;
}
