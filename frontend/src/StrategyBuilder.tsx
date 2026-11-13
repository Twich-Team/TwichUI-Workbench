import {useEffect,useState} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {ApiError,mutate,request} from './api';
import type {components} from './api.generated';
import {Button} from './components/ui/button';
import {ModelChoiceFields} from './ModelChoiceFields';

type Overview=components['schemas']['ModelOverview'];
type Policy=components['schemas']['ExecutionPolicy'];
type Strategy=components['schemas']['Strategy'];
type Stage=components['schemas']['Stage'];
type Provider=Stage['provider'];
const names:Record<Provider,string>={local:'Local Qwen',codex:'Codex',claude:'Claude'};
const triggers={allocation:'An allocation is spent',provider_limit:'A provider limit is reached',unavailable:'A provider is unavailable',authentication:'Sign-in needs attention',unsuitable:'Qwen is unsuitable',unknown:'Allowance is unknown'};
const ident=()=>crypto.randomUUID();
function stage(provider:Provider,policy:Policy):Stage{
 return {id:ident(),provider,model:policy.providers?.[provider]?.model||'',effort:provider==='local'?null:policy.providers?.[provider]?.effort||null,allocation:provider==='local'?null:25,role:'primary',triggers:['allocation','provider_limit','unavailable','unsuitable'],on_limit:'switch',wait_seconds:1800,local_suitability:'simple_only'};
}
function problems(value:Strategy):string[]{
 const result:string[]=[];
 if(!value.stages.length)result.push('Add at least one AI card.');
 if(value.stages[0]?.role==='fallback')result.push('The first card must be primary work.');
 for(const provider of ['codex','claude'] as Provider[]){const total=value.stages.filter(s=>s.provider===provider).reduce((n,s)=>n+(s.allocation||0),0);if(total>100)result.push(`${names[provider]} allocations total ${total}%. Choose at most 100%.`);}
 for(const [index,s] of value.stages.entries()){
  if(!s.model)result.push(`Card ${index+1} needs a supported model.`);
  if(s.provider!=='local'&&(!s.allocation||s.allocation<0||s.allocation>100))result.push(`Card ${index+1} needs an allocation above 0 and at most 100%.`);
  if(s.role==='fallback'&&!s.triggers?.length)result.push(`Card ${index+1} needs a fallback trigger.`);
 }
 if(value.unknown_usage==='limited_requests'&&!value.unknown_request_limit)result.push('Choose a conservative limit for requests with unknown usage.');
 return result;
}
export function StrategyBuilder(){
 const client=useQueryClient();
 const query=useQuery({queryKey:['models'],queryFn:()=>request<Overview>('/api/models')});
 const [draft,setDraft]=useState<Policy|null>(()=>{try{return JSON.parse(sessionStorage.getItem('smart-strategy-draft')||'null');}catch{return null;}});
 const [busy,setBusy]=useState(false),[conflict,setConflict]=useState(false),[message,setMessage]=useState('');
 const [drag,setDrag]=useState<number|null>(null);
 useEffect(()=>{try{if(draft)sessionStorage.setItem('smart-strategy-draft',JSON.stringify(draft));else sessionStorage.removeItem('smart-strategy-draft');}catch{/* In-memory edits remain available. */}},[draft]);
 const policy=draft||query.data?.policy;
 if(!policy)return <section className="panel"><h2>Smart AI choices</h2><p role="status">{query.isError?'AI settings could not be loaded.':'Reading your AI strategy…'}</p></section>;
 const value=policy.strategy;
 const change=(next:Strategy|null)=>{setDraft({...policy,strategy:next});setMessage('');};
 const patch=(index:number,next:Partial<Stage>)=>{if(value)change({...value,stages:value.stages.map((s,i)=>i===index?{...s,...next}:s)});};
 const move=(from:number,to:number)=>{if(!value||to<0||to>=value.stages.length||from===to)return;const cards=[...value.stages];const [card]=cards.splice(from,1);cards.splice(to,0,card);change({...value,stages:cards});};
 const add=(provider:Provider)=>{if(value)change({...value,stages:[...value.stages,stage(provider,policy)]});};
 async function save(event:React.FormEvent){
  event.preventDefault();if(!draft||!query.data)return;
  setBusy(true);setMessage('');
  try{const result=await mutate<Policy>('/api/models/policy',draft,'PUT');client.setQueryData(['models'],{...query.data,policy:result});setDraft(null);setConflict(false);setMessage('Strategy saved for new task decisions. Already approved tasks keep their frozen choices.');}
  catch(error){setMessage(error instanceof Error?error.message:'Save could not be confirmed. Your cards are kept.');if(error instanceof ApiError&&error.status===409)setConflict(true);}
  finally{setBusy(false);}
 }
 async function reload(){const result=await query.refetch();if(result.data&&!result.isError){setDraft(null);setConflict(false);setMessage('Saved strategy reloaded.');}}
 const errors=value?problems(value):[];
 return <section className="panel strategy-builder" aria-label="Smart AI strategy"><div className="strategy-heading"><div><span className="eyebrow">YOUR AI GAME PLAN</span><h2>Smart AI choices</h2><p>Choose who goes first, who steps in and when work should wait.</p></div>{value&&<span className="badge">{value.stages.length} ordered choices</span>}</div>
 <form onSubmit={event=>void save(event)}><fieldset disabled={busy||query.isError||!query.data}>
 {!value?<div className="soft-callout"><p>Your usual AI preferences are active. Create an ordered strategy when you want more control over included allowances.</p><Button type="button" onClick={()=>change({name:'My AI strategy',stages:[stage(policy.preferred,policy)],ceiling:'request_start',unknown_usage:'wait',unknown_request_limit:0,recheck_seconds:300})}>Create my strategy</Button></div>:<>
 <label>Strategy name<input maxLength={80} value={value.name||''} onChange={e=>change({...value,name:e.target.value})}/></label>
 <div className="strategy-budget-grid" aria-label="Separate provider allocations">{(['codex','claude'] as Provider[]).map(provider=>{const cards=value.stages.filter(s=>s.provider===provider);const primary=cards.filter(s=>s.role!=='fallback').reduce((n,s)=>n+(s.allocation||0),0);const reserve=cards.filter(s=>s.role==='fallback').reduce((n,s)=>n+(s.allocation||0),0);const total=primary+reserve;return <div className="strategy-budget" key={provider}><h3>{names[provider]} · {total}%</h3><div className="strategy-budget-track" aria-hidden="true"><span style={{width:`${Math.min(primary,100)}%`}}/><span className="strategy-reserve" style={{width:`${Math.min(reserve,Math.max(0,100-primary))}%`}}/></div><p>{primary}% primary + {reserve}% fallback reserve</p><p>{Math.max(0,100-total)}% outside Workbench’s allocation</p></div>;})}</div>
 <p className="small-text">Each provider has its own budget. Percentages stop new requests at the observed cutoff. A request already running may finish above it; Workbench records the overrun. Personal CLI use counts where the provider reports it.</p>
 <ol className="strategy-cards">{value.stages.map((card,index)=><li key={card.id} className="strategy-card" onDragOver={e=>e.preventDefault()} onDrop={e=>{e.preventDefault();if(drag!==null)move(drag,index);setDrag(null);}}>
  <div className="strategy-card-heading"><strong><span className="strategy-number">{index+1}</span> {names[card.provider]} {card.role==='fallback'?'reserve':''}</strong><div className="strategy-card-order"><Button type="button" variant="ghost" draggable onDragStart={()=>setDrag(index)} onDragEnd={()=>setDrag(null)} aria-label={`Drag card ${index+1}`} title="Drag to reorder">⠿</Button><Button type="button" variant="ghost" disabled={index===0} aria-label={`Move card ${index+1} earlier`} onClick={()=>move(index,index-1)}>↑</Button><Button type="button" variant="ghost" disabled={index===value.stages.length-1} aria-label={`Move card ${index+1} later`} onClick={()=>move(index,index+1)}>↓</Button></div></div>
  <div className="strategy-card-fields"><label>AI provider<select value={card.provider} onChange={e=>{const next=stage(e.target.value as Provider,policy);patch(index,{...next,id:card.id});}}>{(['local','codex','claude'] as Provider[]).map(p=><option key={p} value={p}>{names[p]}</option>)}</select></label>{card.provider!=='local'&&<label>Allocation percentage<input type="number" min={1} max={100} step={1} value={card.allocation||''} onChange={e=>patch(index,{allocation:Number(e.target.value)})}/></label>}
  <ModelChoiceFields label={`Card ${index+1}`} value={{provider:card.provider,model:card.model,effort:card.effort}} report={query.data?.capabilities} onChange={choice=>patch(index,{model:choice.model,effort:choice.effort})}/></div>
  {!policy.providers?.[card.provider]?.enabled&&<p role="alert">{names[card.provider]} is disabled. Enable its existing connection before saving this strategy. <Button type="button" variant="secondary" onClick={()=>setDraft({...policy,providers:{...policy.providers!,[card.provider]:{...policy.providers![card.provider],enabled:true}}})}>Allow {names[card.provider]} for new task choices</Button></p>}
  {card.provider==='local'&&<p>Uses the existing local service and resource checks. Complex coding will wait for a suitable model.</p>}
  <details><summary>When this choice can help</summary><label>Allocation purpose<select value={card.role||'primary'} onChange={e=>patch(index,{role:e.target.value as Stage['role']})}><option value="primary">Primary work</option><option value="fallback">Fallback reserve · only after a trigger</option></select></label>
  {card.role==='fallback'&&<fieldset className="strategy-triggers"><legend>Use this reserve when an earlier choice…</legend>{Object.entries(triggers).map(([key,label])=><label className="check-label" key={key}><input type="checkbox" checked={card.triggers?.includes(key as NonNullable<Stage['triggers']>[number])||false} onChange={e=>patch(index,{triggers:e.target.checked?[...(card.triggers||[]),key as NonNullable<Stage['triggers']>[number]]:card.triggers?.filter(t=>t!==key)})}/>{label}</label>)}</fieldset>}
  <label>If this choice reaches a limit<select value={card.on_limit||'switch'} onChange={e=>patch(index,{on_limit:e.target.value as Stage['on_limit']})}><option value="switch">Try the next authorized choice</option><option value="wait">Wait here and resume automatically</option><option value="wait_then_switch">Wait a while, then try the next choice</option></select></label>
  {card.on_limit==='wait_then_switch'&&<label>Wait minutes<input type="number" min={1} max={10080} value={(card.wait_seconds||1800)/60} onChange={e=>patch(index,{wait_seconds:Number(e.target.value)*60})}/></label>}
  {card.provider==='local'&&<label>Suitable local work<select value={card.local_suitability||'simple_only'} onChange={e=>patch(index,{local_suitability:e.target.value as Stage['local_suitability']})}><option value="simple_only">Simple tasks only</option><option value="reviewed_local_work">Also separately reviewed local work</option></select></label>}
  </details><Button type="button" variant="ghost" onClick={()=>change({...value,stages:value.stages.filter((_,i)=>i!==index)})}>Remove card {index+1}</Button>
 </li>)}</ol>
 <div className="settings-actions">{(['codex','claude','local'] as Provider[]).map(p=><Button key={p} type="button" variant="secondary" disabled={value.stages.length>=16} onClick={()=>add(p)}>Add {names[p]}</Button>)}</div>
 <details className="model-detail-options"><summary>Unknown usage and strict limits</summary><label>Allowance policy<select value={value.ceiling||'request_start'} onChange={e=>change({...value,ceiling:e.target.value as Strategy['ceiling']})}><option value="request_start">Request-start cutoffs · allow a running request to finish</option><option value="strict">Strict ceiling · hold when request consumption cannot be bounded</option></select></label>
 <label>When usage or reset information is unknown<select value={value.unknown_usage||'wait'} onChange={e=>change({...value,unknown_usage:e.target.value as Strategy['unknown_usage']})}><option value="wait">Wait for verified information</option><option value="switch">Try the next authorized choice</option><option value="limited_requests">Allow a conservative number of requests</option></select></label>
 {value.unknown_usage==='limited_requests'&&<><label>Maximum requests with unknown usage<input type="number" min={1} max={100} value={value.unknown_request_limit||''} onChange={e=>change({...value,unknown_request_limit:Number(e.target.value)})}/></label><p>This reserve stays spent until a reset can be verified. If the provider never reports a precise window, work may need your explicit Start now decision.</p></>}
 <label>Check again after minutes<input type="number" min={1} max={1440} value={(value.recheck_seconds||300)/60} onChange={e=>change({...value,recheck_seconds:Number(e.target.value)*60})}/></label><p>Unknown means unknown. Workbench never invents allowance percentages or reset times. Claude’s native display can show a reset label without an exact timestamp. Strict mode currently holds subscription work because neither CLI can guarantee a per-request percentage ceiling.</p></details>
 <Button type="button" variant="ghost" onClick={()=>change(null)}>Use usual AI preferences instead</Button>
 </>}
 {errors.length>0&&<div role="alert" className="form-error">{errors.map(error=><p key={error}>{error}</p>)}</div>}
 <div className="settings-actions"><Button type="submit" disabled={!draft||busy||conflict||errors.length>0}>{busy?'Saving…':'Save AI strategy'}</Button>{draft&&<Button type="button" variant="ghost" onClick={()=>void reload()}>Discard strategy edits and reload</Button>}</div>
 </fieldset></form><p role="status">{message||(draft?'Unsaved cards are kept during navigation.':'Saved choices apply when you authorize a new task. Frozen tasks keep their choices.')}</p>{query.isError&&<p role="alert">Settings refresh failed. Edits are kept. <Button variant="ghost" onClick={()=>void query.refetch()}>Retry AI settings</Button></p>}
 <details><summary>Latest allowance readings and diagnostics</summary>{query.data?.allowance_readings?.length?query.data.allowance_readings.filter(o=>o.provider!=='local').map(o=><div key={o.provider}><h3>{names[o.provider]}</h3><p>{o.state} · {o.source}</p><p>Observed: {o.observed_at?new Date(o.observed_at*1000).toLocaleString():'Unknown'} · {o.observed_at&&Date.now()/1000-o.observed_at>120?'Stale; refreshed before dispatch':'Checked again before dispatch'}</p><ul>{o.windows?.map(w=><li key={w.name}>{w.name.includes('seven')||w.name==='secondary'?'Longer window':'Session window'}: {w.used_percent==null?'Usage unknown':w.used_percent+'% used'} · {w.resets_at?'Provider reset '+new Date(w.resets_at*1000).toLocaleString():w.reset_label?'Native reset label: '+w.reset_label+'; exact time unavailable':'Reset time unavailable'}</li>)}</ul><p>Paid usage: {o.paid_usage}</p></div>):<p>No saved native allowance reading yet. New requests check the provider. Tokens in activity charts are never converted to allowance percentages.</p>}{query.data?.allowance_overruns?.length?<p>A running request crossed its observed cutoff. The saved native receipt retains its before/after readings and overrun.</p>:null}</details>
 <p className="small-text">Included subscriptions and local Qwen only. No API billing, paid credits or automatic billing changes.</p></section>;
}
