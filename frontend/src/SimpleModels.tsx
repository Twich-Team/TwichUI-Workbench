import {useEffect,useState} from 'react';
import {Link} from 'react-router-dom';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {ApiError,mutate,request} from './api';
import type {components} from './api.generated';
import {Button} from './components/ui/button';
import {ModelChoiceFields} from './ModelChoiceFields';
import {savedTaskRoute} from './modelPreferences';

type Overview=components['schemas']['ModelOverview'];
type Policy=Required<components['schemas']['ExecutionPolicy']>;
type Choice='local'|'codex';
type Fallback='off'|'qwen-to-codex'|'codex-to-qwen'|'both';
const names={local:'Local Qwen',codex:'Codex',claude:'Claude'};
function fallback(policy:Policy):Fallback{
  if(policy.substitution==='deny')return 'off';
  if(policy.test_fallback)return policy.test_fallback;
  const qwen=policy.fallback.includes('codex')&&policy.fallback_on.includes('unavailable');
  const codex=policy.fallback.includes('local')&&policy.fallback_on.some(c=>c==='unavailable'||c==='capacity');
  return qwen&&codex?'both':qwen?'qwen-to-codex':codex?'codex-to-qwen':'off';
}

export function SimpleModels(){
  const client=useQueryClient();
  const query=useQuery({queryKey:['models'],queryFn:()=>request<Overview>('/api/models')});
  const access=useQuery({queryKey:['local-work'],queryFn:()=>request<components['schemas']['LocalWorkStatus']>('/api/local-work')});
  const [draft,setDraft]=useState<Policy|null>(()=>{try{return JSON.parse(sessionStorage.getItem('model-policy-draft')||'null');}catch{return null;}});
  const [busy,setBusy]=useState(false),[message,setMessage]=useState(''),[conflict,setConflict]=useState(false);
  useEffect(()=>{try{if(draft)sessionStorage.setItem('model-policy-draft',JSON.stringify(draft));else sessionStorage.removeItem('model-policy-draft');}catch{/* Keep edits in memory. */}},[draft]);
  const saved=query.data?.policy as Policy|undefined,policy=draft||saved;
  if(!policy)return <section className="panel" aria-label="Model preferences"><h2>Models for your tasks</h2><p>{query.isError?'Model preferences could not be loaded.':'Reading saved model preferences…'}</p><Button variant="secondary" onClick={()=>void query.refetch()}>Retry model preferences</Button></section>;
  const mode=fallback(policy);
  function change(preferred:Policy['preferred'],next:Fallback){
    setDraft({...policy!,preferred,substitution:next==='off'?'deny':'configured_fallback_only',test_fallback:next,
      fallback:preferred==='local'?['codex']:['local'],
      fallback_on:next==='off'?policy!.fallback_on:next==='qwen-to-codex'?['unavailable']:['unavailable','capacity']});
    setMessage('');
  }
  const readyConfig=(backend:Choice)=>policy.providers[backend].enabled&&Boolean(policy.providers[backend].model);
  const missing=(['local','codex'] as Choice[]).filter(b=>(b===policy.preferred||(mode!=='off'))&&!readyConfig(b));
  async function save(event:React.FormEvent){
    event.preventDefault();if(!draft||!query.data)return;
    setBusy(true);setMessage('');
    try{
      const value=await mutate<Policy>('/api/models/policy',draft,'PUT');
      client.setQueryData(['models'],{...query.data,policy:value});setDraft(null);setConflict(false);
      setMessage('Model preferences saved for new task decisions. Existing work keeps its approved provider.');
    }catch(error){setMessage(error instanceof Error?error.message:'The save could not be confirmed. Your edits are kept.');if(error instanceof ApiError&&error.status===409)setConflict(true);}
    finally{setBusy(false);}
  }
  async function reload(){
    const result=await query.refetch();
    if(result.data&&!result.isError){setDraft(null);setConflict(false);setMessage('Saved model preferences reloaded.');}
    else setMessage('Reload failed. Your edits are kept.');
  }
  return <section className="panel simple-models" aria-label="Model preferences"><h2>Models for your tasks</h2>
    <p>Prefer local Qwen for addon work, or deliberately choose Codex. Each request confirms which provider may investigate it.</p>
    {query.isError&&<p role="alert">Refresh unavailable. Showing the last loaded preferences; saving is held. <Button variant="secondary" onClick={()=>void query.refetch()}>Retry model preferences</Button></p>}
    <form onSubmit={event=>void save(event)}><fieldset disabled={busy||query.isError||!saved}>
      <label>Preferred provider<select value={policy.preferred} onChange={e=>change(e.target.value as Choice,mode==='off'?'off':mode==='both'?'both':e.target.value==='local'?'qwen-to-codex':'codex-to-qwen')}>
        <option value="local">Local Qwen</option><option value="codex">Codex</option>{policy.preferred==='claude'&&<option value="claude" disabled>Claude · advanced settings</option>}
      </select></label>
      <label>Fallback for new task decisions<select value={mode} onChange={e=>change(policy.preferred,e.target.value as Fallback)}>
        <option value="off">Off · keep the approved provider</option><option value="qwen-to-codex">Qwen may switch to Codex before starting</option><option value="codex-to-qwen">Codex may switch to Qwen before starting</option><option value="both">Allow either direction before starting</option>
      </select></label>
      <p>{mode==='off'?'If the approved provider cannot start, the task waits with an explanation.':'Fallback requires the task’s consent, saved provider settings and available included access. It applies before planning generation starts. An uncertain or failed running attempt cannot trigger a duplicate.'}</p>
      {mode!=='off'&&!savedTaskRoute(policy)?.includes('fallback')&&<p>The preferred provider currently has no eligible fallback. Choose its matching direction or review the advanced model settings.</p>}
      <div className="simple-model-cards">{(['local','codex'] as Choice[]).map(b=><div key={b}><ModelChoiceFields label={names[b]} value={{provider:b,model:policy.providers[b].model||'',effort:policy.providers[b].effort}} report={query.data?.capabilities} onChange={value=>{setDraft({...policy,providers:{...policy.providers,[b]:{...policy.providers[b],model:value.model,effort:value.effort}}});setMessage('');}}/><p>{!readyConfig(b)?'Needs model setup':access.isError||!access.data?'Current access could not be checked':b==='codex'?(access.data.codex_available?'Connector available · allowance checked before use':'Connector unavailable'):(access.data.ready?'Local task service ready':'Local task service not ready')}</p></div>)}</div>
      {missing.length>0&&<p>Choose and enable a model for {missing.map(b=>names[b]).join(' and ')} in <Link to="/models/configure">advanced model settings</Link> before saving this route.</p>}
      <div className="settings-actions"><Button type="submit" disabled={!draft||conflict||missing.length>0||policy.preferred==='claude'}>{busy?'Saving…':'Save task model preferences'}</Button>{draft&&<Button type="button" variant="ghost" onClick={()=>void reload()}>Discard model edits and reload</Button>}</div>
    </fieldset></form>
    <p role="status">{message||(draft?'Unsaved model preferences are kept during navigation.':'Showing saved model preferences.')}</p>
    <p className="small-text">No live subscription balance is shown here. Workbench records activity separately from subscription allowance and API spending. It does not buy credits or enable paid API billing.</p>
    <Link to="/models/configure">Advanced model identifiers, effort and routing</Link> · <Link to="/models">Provider availability and activity</Link>
  </section>;
}
