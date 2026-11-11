import {useRef,useState} from 'react';
import {useQuery} from '@tanstack/react-query';
import {ShieldCheck} from 'lucide-react';
import {request,mutate} from './api';
import type {components} from './api.generated';
import {Button} from './components/ui/button';
import {WorkshopPanel} from './Workspace';

type Feature=components['schemas']['FeatureView'];
type Input=components['schemas']['FeatureRunInput'];
function stored(key:string):Input|null{try{return JSON.parse(localStorage.getItem(key)||'null') as Input|null;}catch{return null;}}

export function FeatureRun({feature,onSaved}:{feature:Feature;onSaved:()=>void}){
 const key='feature-run:'+feature.id;
 const [draft,setDraft]=useState<Input|null>(()=>stored(key));
 const [accepted,setAccepted]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const saving=useRef(false);
 const query=useQuery({queryKey:['feature-run-offer',feature.id],queryFn:()=>request<components['schemas']['FeatureRunAvailability']>('/api/features/'+feature.id+'/run-offer'),enabled:!feature.run&&!feature.intake.archived_at,refetchInterval:5000});
 const offer=query.data?.offer;
 const [review,setReview]=useState<typeof offer>(null);
 const shown=review||offer;
 const stale=Boolean(review&&offer?.binding!==review.binding||draft&&offer?.binding!==draft.binding);
 async function build(){
  if(saving.current||!shown||!accepted||stale||query.isError)return;
  const command=draft||{command_id:crypto.randomUUID(),binding:shown.binding};
  saving.current=true;setBusy(true);setError('');
  try{localStorage.setItem(key,JSON.stringify(command));setDraft(command);await mutate('/api/features/'+feature.id+'/run',command);onSaved();await query.refetch();}
  catch(e){setError((e as Error).message);}finally{saving.current=false;setBusy(false);}
 }
 if(feature.run)return <FeatureStop key={feature.run.id} feature={feature} onSaved={onSaved}/>;
 if(!shown)return null;
 const budget=shown.budget;
 return <WorkshopPanel title="Build this feature" icon={<ShieldCheck size={21}/>}>
  <p>Workbench will work through the {shown.step_count} reviewed steps with {shown.provider==='local'?'Local Qwen':shown.model}, carrying checked changes into each dependent step.</p>
  <p>The result stays here for review. Publishing a draft PR has a separate decision.</p>
  <details onToggle={e=>{if(e.currentTarget.open&&!review)setReview(shown);}}><summary>Run limits and safeguards</summary>
   <p>Up to {budget.overall_seconds/3600} hours for this feature. Each implementation step allows up to {budget.implementation_attempts_per_step} attempts, {budget.per_attempt_seconds/60} minutes, {budget.per_attempt_tokens.toLocaleString()} generated tokens and {budget.per_attempt_turns} turns per attempt. A retry waits for verified cleanup and starts from checked source.</p>
   <p>Read-only preparation allows up to {budget.scope_attempts_per_step} requests per step, each limited to {budget.scope_request_seconds} seconds and {budget.scope_completion_tokens.toLocaleString()} generated tokens. The total generated-token ceiling is {shown.maximum_generated_tokens.toLocaleString()}. Fallback is off. You can stop the feature.</p>
  </details>
  <label className="feature-run-consent"><input type="checkbox" checked={accepted} disabled={busy||stale} onChange={e=>{setAccepted(e.target.checked);if(!review)setReview(shown);}}/>Build this reviewed feature within these limits.</label>
  {stale&&<p role="alert">The run offer changed. <Button variant="secondary" onClick={()=>{setReview(offer);setDraft(null);setAccepted(false);localStorage.removeItem(key);}}>Review current offer</Button></p>}
  {error&&<p role="alert" className="form-error">{error} Retrying keeps the same decision.</p>}
  <Button disabled={!accepted||busy||stale||query.isError} onClick={()=>void build()}>{busy?'Saving decision…':draft?'Retry saved decision':'Build reviewed feature'}</Button>
 </WorkshopPanel>;
}

function FeatureStop({feature,onSaved}:{feature:Feature;onSaved:()=>void}){
 const run=feature.run!;
 const key='feature-stop:'+run.id;
 const [command]=useState(()=>{const old=localStorage.getItem(key);if(old)return old;const value=crypto.randomUUID();localStorage.setItem(key,value);return value;});
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 const saving=useRef(false);
 async function stop(){
  if(saving.current)return;saving.current=true;setBusy(true);setError('');
  try{await mutate('/api/features/'+feature.id+'/runs/'+run.id+'/stop',{command_id:command});onSaved();}
  catch(e){setError((e as Error).message);}finally{saving.current=false;setBusy(false);}
 }
 return <WorkshopPanel title={run.cancel_requested?'Feature stopped or stopping':'Feature run'} icon={<ShieldCheck size={21}/>}>
  <p>{run.message}</p>{run.can_stop&&<Button variant="secondary" disabled={busy} onClick={()=>void stop()}>{busy?'Saving stop request…':'Stop this feature'}</Button>}
  {error&&<p role="alert" className="form-error">{error} Retrying keeps the same stop request.</p>}
 </WorkshopPanel>;
}
