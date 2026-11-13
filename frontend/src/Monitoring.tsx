import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { Activity, RefreshCw } from 'lucide-react';
import type { components } from './api.generated';
import { request } from './api';
import { SourceStatus, useSource } from './Catalog';
import { Button } from './components/ui/button';
import { loadCharts, resourceOptions, type Chart, type ChartOption, type WindowRange } from './charts';

type Resources = components['schemas']['Resources'];
type Preferences = components['schemas']['DisplaySettings'];
type Distribution = components['schemas']['ActivityDistribution'];
export const usePreferences = () => useQuery({queryKey:['display-settings'], queryFn:() => request<Preferences>('/api/display-settings')});
const time = (epoch: number) => new Intl.DateTimeFormat(undefined,{dateStyle:'medium',timeStyle:'medium'}).format(new Date(epoch*1000));
export function value(value: number | null | undefined, unit: '%'|'GiB') {
  if (value == null) return 'Unavailable';
  const scaled = value/(unit === 'GiB' ? 2**30 : 1);
  if (unit === 'GiB' && scaled > 0 && scaled < .001) return '<0.001 GiB';
  return `${scaled.toFixed(unit === 'GiB' && scaled > 0 && scaled < .1 ? 3 : 1)} ${unit}`;
}
export function ChartView({ option, title, windowRange }: {option: ChartOption; title: string; windowRange?: WindowRange}) {
  const element = useRef<HTMLDivElement>(null);
  const chart = useRef<Chart | null>(null);
  const latestOption = useRef(option); latestOption.current = option;
  const zoom = useRef<WindowRange | undefined>(undefined);
  const range = useRef(windowRange);
  const [error, setError] = useState('');
  const [ready, setReady] = useState(false);
  useEffect(() => {
    let disposed = false;
    let observer: ResizeObserver | undefined;
    void loadCharts().then(() => {
      if (disposed || !element.current) return;
      const instance = window.echarts!.init(element.current, undefined, {renderer:'canvas'}); chart.current = instance;
      instance.setOption(latestOption.current);
      instance.on('datazoom', () => { const data = instance.getOption().dataZoom?.[0]; if (typeof data?.startValue === 'number' && typeof data?.endValue === 'number') zoom.current = {start:data.startValue,end:data.endValue}; });
      observer = new ResizeObserver(() => instance.resize()); observer.observe(element.current); setReady(true);
    }).catch(err => { if(!disposed) setError(String(err.message)); });
    return () => { disposed = true; observer?.disconnect(); chart.current?.dispose(); chart.current = null; };
  }, []);
  useEffect(() => {
    if (!chart.current) return;
    // Absolute exploration survives refresh. A deliberate range selection resets it.
    if (range.current && windowRange && range.current.end-range.current.start !== windowRange.end-windowRange.start) zoom.current = undefined;
    range.current = windowRange;
    const optionToApply = zoom.current ? {...option, dataZoom:[{type:'slider', startValue:zoom.current.start,endValue:zoom.current.end}, {type:'inside',startValue:zoom.current.start,endValue:zoom.current.end}]} : option;
    chart.current.setOption(optionToApply);
  }, [option, windowRange]);
  return <><div className="chart-canvas" ref={element} role="img" aria-label={title} data-chart-ready={ready} />{error && <p role="alert">{error}</p>}{windowRange && <div className="chart-buttons"><Button variant="ghost" onClick={() => {const selected = zoom.current || windowRange; const span=(selected.end-selected.start)/2; const middle=(selected.start+selected.end)/2; chart.current?.dispatchAction({type:'dataZoom',startValue:middle-span/2,endValue:middle+span/2});}}>Zoom in</Button><Button variant="ghost" onClick={() => {zoom.current = undefined; chart.current?.dispatchAction({type:'dataZoom', startValue:windowRange.start,endValue:windowRange.end}); zoom.current = undefined;}}>Follow latest / reset</Button></div>}</>;
}
export function Monitoring() {
  const workspace = useSource();
  const preferences = usePreferences();
  const [source, setSource] = useState<'observed'|'demo'>('observed');
  const [chosenHours, setHours] = useState<number | null>(null);
  const hours = chosenHours ?? preferences.data?.resource_hours ?? 1;
  const query = useQuery({queryKey:['resources',source,hours], queryFn:() => request<Resources>(`/api/resources?source=${source}&hours=${hours}`), refetchInterval:(preferences.data?.refresh_seconds ?? 30)*1000});
  const activity = useQuery({queryKey:['activity',workspace], queryFn:() => request<Distribution>(`/api/activity?source=${workspace}`),refetchInterval:30000});
  const data = query.data;
  const current = data?.latest;
  const statusFresh = !query.isError && data?.status_freshness === 'fresh' && data.status_at != null && Date.now()/1000-data.status_at <= 45;
  const health = data?.services || [];
  const running = health.filter(item => item.state === 'active').length;
  const notes = data?.metric_notes || {};
  const labels = {api:'Workbench', worker:'Background worker', connector:'Codex connector', private_access:'Private access', model:'Local model'};
  const states = {active:'Running', inactive:'Stopped', failed:'Failed', activating:'Starting', deactivating:'Stopping', reloading:'Reloading', unknown:'Unconfirmed'};
  return <><div className="page-heading machine-resources-heading"><div><h1><Activity size={27} /> Machine resources</h1><p>Host resources, active model and service health.</p></div><Button variant="secondary" onClick={() => {void query.refetch(); void activity.refetch();}}><RefreshCw size={15}/>Refresh display</Button></div>
    <div className="monitor-controls"><label>Resource source<select aria-label="Resource source" value={source} onChange={event => setSource(event.target.value as 'observed'|'demo')}><option value="observed">Observed host</option><option value="demo">Deterministic demo · simulated</option></select></label><label>Time range<select aria-label="Resource time range" value={hours} onChange={event => setHours(Number(event.target.value))}><option value={1}>Past hour</option><option value={24}>Past 24 hours</option><option value={168}>Past 7 days</option></select></label><Link className="back-link" to="/settings">Display preferences</Link></div>
    {query.isPending ? <p role="status">Loading resource observations…</p> : query.isError && !data ? <div role="alert" className="form-error">Resource observations couldn’t be loaded. <Button onClick={() => void query.refetch()}>Try again</Button></div> : data && <>
      <div className={`source-status source-${data.freshness}`} role="status"><div><strong>{data.label} · {data.freshness}</strong><details><summary>Collection and attribution evidence</summary><p>{data.message}</p></details><p>{current ? `${source === 'demo' ? 'Simulated at' : 'Observed'} ${time(current.at)} · ${data.age_s?.toFixed(0)} seconds old` : 'No valid observation available.'} Display refresh: {preferences.data?.refresh_seconds ?? 30}s.</p></div></div>
      {query.isError && <p role="alert" className="form-error">Refresh failed. These are the last loaded readings; current freshness is unconfirmed.</p>}
      <div className="resource-grid">{[
        ['CPU utilization',value(current?.cpu,'%'),notes.cpu || 'Host-wide'],
        ['System RAM',`${value(current?.ram_used,'GiB')} / ${value(current?.ram_total,'GiB')}`,notes.ram || 'Used / total · host-wide'],
        ['Application disk',`${value(current?.disk_available,'GiB')} available`,notes.disk || `${value(current?.disk_used,'GiB')} used / ${value(current?.disk_total,'GiB')} total`],
        ['Swap',current?.swap_total === 0 ? 'No swap configured' : `${value(current?.swap_used,'GiB')} / ${value(current?.swap_total,'GiB')}`,notes.swap || 'Used / total · host-wide'],
        ['GPU utilization',current?.gpu == null && notes.gpu ? 'Not reported' : value(current?.gpu,'%'),notes.gpu || current?.gpu_device || 'Device identity unavailable'],
        ['VRAM',`${value(current?.vram_used,'GiB')} / ${value(current?.vram_total,'GiB')}`,notes.vram || current?.gpu_device || 'Device identity unavailable'],
        ['Active model',data.model?.alias || (source === 'demo' ? 'Not sampled in demo' : 'Unavailable'),statusFresh && data.model?.api === 'ready' ? `Access verified · ${value(data.model.bytes,'GiB')} weights` : 'Last observation; current access unconfirmed'],
        ['Service health',health.length ? `${running} of ${health.length} ${statusFresh ? 'running' : 'last observed running'}` : 'Unavailable',statusFresh ? 'Current observation' : 'Current status unconfirmed'],
      ].map(([label,reading,note]) => <div className="panel resource-card" key={label}><p>{label}</p><strong>{reading}</strong><small>{note}</small></div>)}</div>
      {health.length > 0 && <section className="panel service-health"><h2>Service observations</h2><p className="history-note">{statusFresh ? 'Observed' : 'Last observed'} {data.status_at ? time(data.status_at) : 'at an unconfirmed time'}. {statusFresh ? 'Running describes the service process.' : 'These observations do not confirm current health.'}</p><ul className="service-health-list">{health.map(item => <li key={item.name}><span>{labels[item.name]}</span><strong>{statusFresh ? states[item.state] : `Last observed: ${states[item.state].toLowerCase()}`}</strong></li>)}</ul><details><summary>Model and process details</summary><p className="detail-copy">Model file: {data.model?.file || 'Unavailable'}. {data.model?.mapped === true ? 'The observed service maps this file.' : 'The model file mapping could not be confirmed.'}</p>{health.map(item => <p key={item.name} className="detail-copy">{labels[item.name]}: process {item.pid ?? 'unconfirmed'} · restarts {item.restarts ?? 'unconfirmed'}</p>)}</details></section>}
      <p className="history-note">Requested: {time(data.start)} – {time(data.end)}. </p><details><summary>History coverage and retention</summary><p className="history-note">{data.first_at ? `Retained coverage begins ${time(data.first_at)}.` : 'No retained history.'} {source === 'demo' ? 'Generated demo; no stored observations.' : `Resource retention: ${data.retention_days} days.`} Memory and disk use GiB (2³⁰ bytes). Disk availability excludes the filesystem recovery reservation.</p></details>
      {data.points.length ? <div className="resource-charts">{[['CPU / GPU utilization',['cpu','gpu'],'%'],['System RAM usage',['ram_used'],'GiB'],['VRAM usage',['vram_used'],'GiB']].map(([title,fields,unit]) => { const windowRange={start:data.start*1000,end:data.end*1000}; return <section className="panel chart-panel" key={`${source}:${title}`}><h2>{String(title)}</h2><ChartView title={String(title)} windowRange={windowRange} option={resourceOptions(data.points,fields as string[],unit as '%'|'GiB',windowRange,undefined,current?.gpu == null)} /><details><summary>Read values · keyboard and touch</summary><ReadingTable data={data} fields={fields as string[]} unit={unit as '%'|'GiB'} /></details></section>; })}</div> : <section className="panel empty-panel"><h2>{data.freshness === 'unavailable' ? 'Resource source unavailable' : 'No readings in this range'}</h2><p>Choose another range or inspect the explicitly simulated demo. Missing history is never filled in.</p></section>}
    </>}
    <section className="panel activity-panel"><h2>Waiting and blocked work</h2>{activity.isError && activity.data && <p role="alert" className="form-error">Activity refresh failed. Last-loaded states are unconfirmed.</p>}{activity.data ? <><SourceStatus meta={activity.data}/><p className="history-note">{activity.data.basis}</p>{Object.keys(activity.data.counts).length ? <><ChartView title="Current explicit waiting and blocked state counts" option={{animation:false,color:['#c49b65'],grid:{left:12,right:30,top:20,bottom:30,containLabel:true},xAxis:{type:'value',minInterval:1,axisLabel:{color:'#d2c1a8'}},yAxis:{type:'category',data:Object.keys(activity.data.counts),axisLabel:{color:'#d2c1a8',width:100,overflow:'break',lineHeight:18}},series:[{type:'bar',barMaxWidth:36,data:Object.values(activity.data.counts),label:{show:true,position:'right',color:'#ead7ba'}}]}}/><ul className="activity-values">{Object.entries(activity.data.counts).map(([state,count]) => <li key={state}>{state}: {count} records</li>)}</ul></> : <p>No fresh explicit waiting or blocked records in this snapshot.</p>}<details><summary>Chart coverage and omissions</summary>{activity.data.omissions.map(message => <p className="detail-copy" key={message}>{message}</p>)}</details></> : <p role="status">{activity.isError ? 'Work activity source unavailable.' : 'Loading work activity…'}</p>}</section>
  </>;
}
function ReadingTable({data,fields,unit}: {data:Resources;fields:string[];unit:'%'|'GiB'}) {
  const [page,setPage]=useState(0); const size=24;
  return <><p className="small-text">{data.points.length} bounded chart readings, newest first. Gaps show Unavailable.</p><div className="table-scroll"><table><thead><tr><th>Time</th>{fields.map(field => <th key={field}>{field === 'ram_used' ? 'System RAM' : field === 'vram_used' ? 'VRAM' : field.toUpperCase()} ({unit})</th>)}</tr></thead><tbody>{data.points.slice().reverse().slice(page*size,(page+1)*size).map(point => <tr key={point.at}><td>{time(point.at)}</td>{fields.map(field => <td key={field}>{value(point[field as keyof typeof point] as number|null,unit)}</td>)}</tr>)}</tbody></table></div><div className="chart-buttons"><Button variant="ghost" disabled={page===0} onClick={()=>setPage(page-1)}>Newer readings</Button><Button variant="ghost" disabled={(page+1)*size>=data.points.length} onClick={()=>setPage(page+1)}>Older readings</Button></div></>;
}
