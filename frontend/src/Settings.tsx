import { Link } from 'react-router-dom';
import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError, request } from './api';
import type { components } from './api.generated';
import { Button } from './components/ui/button';
import { formatTime } from './Catalog';
import { usePreferences } from './Monitoring';
import {WorkControls} from './WorkControls';
import {SimpleModels,SavedSetup} from './SimpleModels';
import {WorkshopPanel} from './Workspace';
import {StrategyBuilder} from './StrategyBuilder';
import {UsagePreview} from './Usage';

type Preferences = components['schemas']['DisplaySettings'];
type Policy = components['schemas']['Policy'];
const plain = (value: boolean | number | undefined) => value === undefined ? 'Unknown' : typeof value === 'boolean' ? value ? 'On' : 'Off' : String(value);
const labels: Record<string,string> = {paused:'Pause preference',fallback_enabled:'Fallback preference',invocations_per_window:'Invocations per rolling window',findings_per_day:'Findings per day',max_unpublished_candidates:'Unpublished candidate capacity'};
export function Settings() {
  const client = useQueryClient();
  const query = usePreferences();
  const policy = useQuery({queryKey:['operating-policy'],queryFn:() => request<Policy>('/api/operating-policy'), refetchInterval:30000});
  const [draft,setDraft] = useState<Preferences|null>(()=> {try {return JSON.parse(sessionStorage.getItem('display-settings-draft') || 'null');} catch {return null;}});
  const [saving,setSaving] = useState(false);
  const [message,setMessage] = useState('');
  const [conflict,setConflict] = useState(false);
  const current = draft || query.data;
  const dirty = draft !== null;
  useEffect(()=> {try { if (draft) sessionStorage.setItem('display-settings-draft',JSON.stringify(draft)); else sessionStorage.removeItem('display-settings-draft'); } catch { /* Edits remain in memory. */ }},[draft]);
  async function save(event: React.FormEvent) {
    event.preventDefault(); if (!draft) return;
    setSaving(true); setMessage('');
    try {
      const session = await request<components['schemas']['Session']>('/api/session');
      const result = await request<Preferences>('/api/display-settings',{method:'PUT',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf_token},body:JSON.stringify(draft)});
      client.setQueryData(['display-settings'], result); setDraft(null); setConflict(false); setMessage('Display preferences saved and applied.');
    } catch (err) {setMessage(err instanceof Error ? err.message : 'Save could not be confirmed. Your edits are kept.'); if (err instanceof ApiError && err.status === 409) setConflict(true);}
    finally {setSaving(false);}
  }
  return <div className="settings-page"><div className="page-heading"><div><h1>Settings</h1><p>Choose your AI companion and how your workshop works.</p></div></div><StrategyBuilder/><div className="settings-grid"><SimpleModels/><aside className="settings-side"><WorkshopPanel title="Saved AI setup"><SavedSetup/></WorkshopPanel><WorkControls/><WorkshopPanel title="Recent AI activity"><UsagePreview/></WorkshopPanel></aside></div>
 <details className="panel settings-panel"><summary>Display and monitoring preferences</summary><h2>Resource display</h2><p className="detail-copy">These application-owned preferences are shared by this local Workbench. Display refresh reads observations; it does not run models or change worker scheduling.</p>
    {!current ? <p role="status">{query.isError ? 'Display preferences unavailable.' : 'Loading display preferences…'}</p> : <form onSubmit={event => void save(event)}><label>Default chart time range<select aria-label="Default chart time range" disabled={saving} value={current.resource_hours} onChange={event => {setDraft({...current,resource_hours:Number(event.target.value) as Preferences['resource_hours']});setMessage('');}}><option value={1}>Past hour</option><option value={24}>Past 24 hours</option><option value={168}>Past 7 days</option></select></label><label>Display refresh<select aria-label="Display refresh" disabled={saving} value={current.refresh_seconds} onChange={event=>{setDraft({...current,refresh_seconds:Number(event.target.value) as Preferences['refresh_seconds']});setMessage('');}}><option value={15}>Every 15 seconds</option><option value={30}>Every 30 seconds</option><option value={60}>Every 60 seconds</option></select></label><div className="settings-actions"><Button disabled={!dirty || saving || conflict} type="submit">{saving ? 'Saving…' : 'Save display preferences'}</Button>{dirty && <Button variant="ghost" type="button" disabled={saving} onClick={async()=>{const result=await query.refetch(); if(result.data) {setDraft(null);setConflict(false);setMessage('Saved values reloaded.');}}}>Discard edits and reload</Button>}</div><p className="detail-copy">{dirty ? 'Unsaved edits are kept during navigation and refresh.' : 'Saved. Preferences apply immediately.'}</p></form>}
    {message && <p className={conflict ? 'form-error' : 'detail-copy'} role="status">{message}</p>}{query.isError && <p className="form-error" role="alert">Refresh failed. Your edits are kept. <Button variant="ghost" onClick={()=>void query.refetch()}>Retry display settings</Button></p>}
  </details><WorkshopPanel title="Advanced tools and history"><div className="advanced-tool-links"><Link to="/models/configure"><strong>AI configuration</strong><span>Precise models, effort and routing</span></Link><Link to="/monitoring"><strong>Machine resources</strong><span>Observed local resource charts</span></Link><Link to="/history"><strong>Workspace history</strong><span>Imported and older records</span></Link><Link to="/backlog?source=fixture"><strong>Advanced records</strong><span>Engineering review and evidence</span></Link></div></WorkshopPanel><details className="panel policy-panel"><summary>Older maintenance policy · Read only</summary><h2>Maintenance observations</h2>{!policy.data ? <p role="status">{policy.isError ? 'Policy source unavailable.' : 'Reading saved policy…'}</p> : <><p className="detail-copy">Historical maintenance preferences, not the controls for current task execution. These observations do not reinstate old delivery caps; editing is not enabled here.</p>{policy.data.available && <><p className="detail-copy"><strong>{policy.data.acknowledgment}</strong> · observation {policy.data.freshness}</p><details><summary>Revision and acknowledgment evidence</summary><p className="detail-copy">{policy.data.message} Saved revision: {policy.data.revision}.</p><p className="detail-copy">Acknowledgment: {policy.data.observed_at ? formatTime(new Date(policy.data.observed_at*1000).toISOString()) : 'Unavailable'} · revision {policy.data.observed_revision ?? 'unknown'}</p><dl>{Object.entries(policy.data.observed_values).map(([key,value])=><div key={key}><dt>{labels[key] || key}</dt><dd>Last acknowledged: {plain(value)}</dd></div>)}</dl></details><dl>{Object.entries(policy.data.values).map(([key,value])=><div key={key}><dt>{labels[key] || key}</dt><dd>Saved preference: {plain(value)}</dd></div>)}</dl></>}</>}{policy.isError && policy.data && <p role="alert" className="form-error">Policy refresh failed; last-loaded observation is unconfirmed.</p>}</details></div>;
}
