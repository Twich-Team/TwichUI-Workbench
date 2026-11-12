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
 if(feature.run)return <>{feature.run_recovery&&<FeatureRunRecovery key={feature.run_recovery.binding} feature={feature} onSaved={onSaved}/>} {feature.scope_correction&&<FeatureScopeCorrection key={feature.scope_correction.binding} feature={feature} onSaved={onSaved}/>}<FeatureStop key={feature.run.id} feature={feature} onSaved={onSaved}/></>;
 if(!shown)return null;
 const budget=shown.budget;
 return <WorkshopPanel title="Build this feature" icon={<ShieldCheck size={21}/>}>
  <p>Workbench will work through the {shown.step_count} reviewed steps with {shown.provider==='local'?'Local Qwen':shown.model}, carrying checked changes into each dependent step.</p>
  <p>Workbench will prepare one coherent result for review. Draft publishing follows your saved permissions.</p>
  <details onToggle={e=>{if(e.currentTarget.open&&!review)setReview(shown);}}><summary>Run limits and safeguards</summary>
   <p>Up to {budget.overall_seconds/3600} hours for this feature. Each implementation step allows up to {budget.implementation_attempts_per_step} attempts, {budget.per_attempt_seconds/60} minutes, {budget.per_attempt_tokens.toLocaleString()} generated tokens and {budget.per_attempt_turns} turns per attempt. A retry waits for verified cleanup and starts from checked source.</p>
   <p>Read-only preparation allows up to {budget.scope_attempts_per_step} requests per step, each limited to {budget.scope_request_seconds} seconds and {budget.scope_completion_tokens.toLocaleString()} generated tokens. Independent feature checks allow up to {budget.verification_requests} additional read-only requests, each limited to {budget.verification_request_seconds} seconds and {budget.verification_completion_tokens.toLocaleString()} generated tokens, with up to {budget.verification_attempts} local check attempts of {budget.verification_seconds/60} minutes each. The total generated-token ceiling is {shown.maximum_generated_tokens.toLocaleString()}. Fallback is off. You can stop the feature.</p>
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
  <p>{feature.scope_correction||feature.run_recovery?feature.message:run.message}</p>{run.can_stop&&<Button variant="secondary" disabled={busy} onClick={()=>void stop()}>{busy?'Saving stop request…':'Stop this feature'}</Button>}
  {error&&<p role="alert" className="form-error">{error} Retrying keeps the same stop request.</p>}
 </WorkshopPanel>;
}

function FeatureScopeCorrection({feature,onSaved}:{feature:Feature;onSaved:()=>void}){
 const offer=feature.scope_correction!;
 const key='feature-scope-correction:'+feature.id;
 const [command,setCommand]=useState<Input|null>(()=>stored(key));
 const [accepted,setAccepted]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const saving=useRef(false);
 const stale=Boolean(command&&command.binding!==offer.binding);
 async function proceed(){
  if(saving.current||!accepted||stale)return;
  const input=command||{command_id:crypto.randomUUID(),binding:offer.binding};
  saving.current=true;setBusy(true);setError('');setCommand(input);localStorage.setItem(key,JSON.stringify(input));
  try{await mutate('/api/features/'+feature.id+'/prepare-step-again',input);localStorage.removeItem(key);onSaved();}
  catch(reason){setError((reason as Error).message);}finally{saving.current=false;setBusy(false);}
 }
 return <WorkshopPanel title="Continue step preparation" icon={<ShieldCheck size={21}/>}>
  <h3>{offer.task_title}</h3><p>{offer.message}</p><p className="small-text">Up to {offer.generation_requests} read-only requests, {offer.request_seconds/60} minutes and {offer.completion_tokens.toLocaleString()} generated tokens each. The original feature deadline and implementation limits stay in place.</p>
  <label className="feature-run-consent"><input type="checkbox" checked={accepted} disabled={busy||stale} onChange={event=>setAccepted(event.target.checked)}/><span>Allow these additional read-only preparation requests.</span></label>
  {stale&&<><p role="alert">This saved decision is out of date. Review the current preparation limits before allowing another request.</p><Button variant="secondary" onClick={()=>{setCommand(null);localStorage.removeItem(key);setAccepted(false);}}>Review current preparation limits</Button></>}
  {error&&<p role="alert" className="form-error">{error} Retrying keeps the same saved command.</p>}
  <Button disabled={!accepted||busy||stale} onClick={()=>void proceed()}>{busy?'Saving decision…':command?'Retry saved preparation decision':'Continue preparation'}</Button>
 </WorkshopPanel>;
}

function FeatureRunRecovery({feature,onSaved}:{feature:Feature;onSaved:()=>void}){
 const offer=feature.run_recovery!;
 const key='feature-unstarted-continuation:'+feature.id;
 const [command,setCommand]=useState<Input|null>(()=>stored(key));
 const [accepted,setAccepted]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const saving=useRef(false);const stale=Boolean(command&&command.binding!==offer.binding);
 async function proceed(){
  if(saving.current||!accepted||stale)return;
  const input=command||{command_id:crypto.randomUUID(),binding:offer.binding};
  saving.current=true;setBusy(true);setError('');setCommand(input);localStorage.setItem(key,JSON.stringify(input));
  try{await mutate('/api/features/'+feature.id+'/continue-unstarted-run',input);localStorage.removeItem(key);onSaved();}
  catch(reason){setError((reason as Error).message);}finally{saving.current=false;setBusy(false);}
 }
 return <WorkshopPanel title="Continue this feature" icon={<ShieldCheck size={21}/>}>
  <h3>{offer.task_title}</h3><p>{offer.message}</p>
  <details><summary>Continuation limits</summary>{offer.limits_description?<p>{offer.limits_description}</p>:<p>A separate {offer.hours}-hour window for the same reviewed feature. The earlier attempt remains in history and counts toward the existing attempt limit. Each fresh attempt keeps the existing 24,000 generated-token and 20-turn limits. Fallback stays off. Publishing follows your saved permissions.</p>}</details>
  <label className="feature-run-consent"><input type="checkbox" checked={accepted} disabled={busy||stale} onChange={e=>setAccepted(e.target.checked)}/><span>Continue the same feature within these limits.</span></label>
  {stale&&<><p role="alert">The saved continuation changed. Review the current offer.</p><Button variant="secondary" onClick={()=>{setCommand(null);localStorage.removeItem(key);setAccepted(false);}}>Review current continuation</Button></>}
  {error&&<p role="alert" className="form-error">{error} Retrying keeps the same command.</p>}
  <Button disabled={!accepted||busy||stale} onClick={()=>void proceed()}>{busy?'Saving decision…':command?'Retry saved continuation':'Continue saved feature'}</Button>
 </WorkshopPanel>;
}
