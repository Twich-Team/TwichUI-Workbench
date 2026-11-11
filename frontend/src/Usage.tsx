import {useState} from 'react';
import {useQuery} from '@tanstack/react-query';
import {Link,useSearchParams} from 'react-router-dom';
import {RefreshCw} from 'lucide-react';
import type {components} from './api.generated';
import {request} from './api';
import {Button} from './components/ui/button';
import {ChartView} from './Monitoring';
import type {ChartOption} from './charts';
import {formatTime} from './Catalog';

type Report=components['schemas']['UsageOverview'];
const provider=(name:string)=>name==='local'?'Local Qwen':name==='codex'?'Codex':name==='unknown'?'Unattributed':name;
const number=(value:number|null)=>value===null?'Not reported':value.toLocaleString();
export function usageOptions(data:Report,selected='all'):ChartOption{
 const rows=data.timeline.filter(r=>selected==='all'||r.backend===selected);
 const labels=[...new Set(rows.filter(r=>r.reported_tokens!==null).map(r=>`${provider(r.backend)} · ${r.model}`))];
 const days=Array.from({length:data.days},(_,i)=>new Date(Date.parse(data.start)+i*86400000).toISOString().slice(0,10));
 return {animation:false,backgroundColor:'transparent',textStyle:{color:'#e7d8bf'},
  color:['#d0a36b','#86aaa1','#ac91b5','#88a9c2','#bd8b73','#b9b27e'],
  aria:{enabled:true,decal:{show:true}},
  legend:{type:'scroll',top:0,textStyle:{color:'#e7d8bf'},pageTextStyle:{color:'#e7d8bf'},pageIconColor:'#d0a36b'},
  tooltip:{trigger:'axis',renderMode:'richText',confine:true,valueFormatter:(value:unknown)=>typeof value==='number'?`${value.toLocaleString()} reported tokens`:'No reported amount'},
  grid:{left:12,right:12,top:48,bottom:80,containLabel:true},
  xAxis:{type:'category',data:days,axisLabel:{color:'#cbb89b',hideOverlap:true,formatter:(value:string)=>value.slice(5)}},
  yAxis:{type:'value',name:'Reported tokens',min:0,axisLabel:{color:'#cbb89b'},splitLine:{lineStyle:{color:'#4c3929'}}},
  dataZoom:[{type:'slider',start:0,end:100,height:24,bottom:12,textStyle:{color:'#e7d8bf'}},{type:'inside',zoomOnMouseWheel:'shift',moveOnMouseWheel:false}],
  series:labels.map(label=>({id:label,name:label,type:'bar',stack:'tokens',barMaxWidth:40,
   data:days.map(day=>{const rowsForDay=rows.filter(r=>r.day===day&&`${provider(r.backend)} · ${r.model}`===label);const known=rowsForDay.flatMap(r=>r.reported_tokens===null?[]:[r.reported_tokens]);return known.length?known.reduce((a,b)=>a+b,0):null;})}))};
}
export function UsageSummary(){
 const query=useQuery({queryKey:['usage',7],queryFn:()=>request<Report>('/api/usage?days=7'),refetchInterval:60000});
 return <p className="usage-summary">{typeof query.data?.operations==='number'&&!query.isError?`${query.data.operations.toLocaleString()} recorded AI operations this week. `:'AI activity and reported tokens. '}<Link to="/usage">View AI usage</Link></p>;
}
export function Usage(){
 const [params,setParams]=useSearchParams();
 const chosen=Number(params.get('days')||7),days=[7,30,90].includes(chosen)?chosen:7;
 const [selected,setSelected]=useState('all'),[allFallbacks,setAllFallbacks]=useState(false);
 const query=useQuery({queryKey:['usage',days],queryFn:()=>request<Report>(`/api/usage?days=${days}`),refetchInterval:60000});
 const data=query.data;
 const providers=[...new Set(data?.providers.map(r=>r.backend)||[])];
 const visible=data?.providers.filter(r=>selected==='all'||r.backend===selected)||[];
 return <div className="usage-page"><div className="page-heading"><div><h1>AI usage</h1><p>The work your AI companions have done for you.</p></div><Button variant="secondary" onClick={()=>void query.refetch()}><RefreshCw size={15}/>Refresh activity</Button></div>
  <div className="monitor-controls"><label>Activity range<select aria-label="Activity range" value={days} onChange={e=>setParams({days:e.target.value})}><option value={7}>This week · 7 UTC dates</option><option value={30}>Past 30 UTC dates</option><option value={90}>Past 90 UTC dates</option></select></label><Link to="/settings">AI settings</Link><Link to="/monitoring">Local machine resources</Link></div>
  {!data?<section className="panel" role={query.isError?'alert':'status'}><h2>{query.isError?'AI activity could not be loaded':'Reading saved model activity…'}</h2><p>{query.isError?'Your saved history is unchanged. Try refreshing activity.':'Only Workbench records are included.'}</p></section>:<>
   <p className={query.isError?'stale-display':'freshness-note'} role={query.isError?'alert':undefined}>{query.isError?'Refresh failed. These are the last loaded figures; current activity is unconfirmed.':`Read from saved history ${formatTime(data.observed_at)}`}</p>
   <div className="usage-totals" aria-label="Observed activity totals"><section className="panel"><span>Model operations</span><strong>{number(data.operations)}</strong><p>{number(data.tasks)} saved task records had activity. Older campaign retries may still have separate entries. One operation can contain several requests.</p></section><section className="panel"><span>Reported tokens</span><strong>{number(data.reported_tokens)}</strong><p>{data.reported_operations} operations reported an amount. {data.unreported_operations} have unreported usage.</p>{data.partial_operations>0&&<p>{data.partial_operations} partial reports are included; the total may be incomplete.</p>}</section><section className="panel"><span>Recorded fallbacks</span><strong>{number(data.fallbacks.length)}</strong><p>Confirmed switches before planning generation began.</p></section></div>
   {data.session_authored_operations>0&&<p className="small-text">{data.session_authored_operations} earlier changes were authored in a separate development session. Their records are preserved in history and excluded from model-operation totals because no separate model receipt exists.</p>}
   <section className="panel usage-allowance"><h2>Subscription allowance is unavailable</h2><p>Tokens show Workbench activity. They do not tell us how much Codex subscription allowance remains or when it resets. Separately billed API spending is also unavailable. This page does not enable billing or buy credits.</p></section>
   {!data.operations?<section className="panel empty-panel"><h2>No recorded AI work in this range</h2><p>Choose a longer range to inspect earlier work. Missing history is not zero provider usage.</p></section>:<>
    <section className="panel chart-panel"><h2>Reported tokens over time</h2><label>Show provider<select aria-label="Show provider" value={selected} onChange={e=>setSelected(e.target.value)}><option value="all">All observed providers</option>{providers.map(p=><option key={p} value={p}>{provider(p)}</option>)}</select></label>
     {visible.some(r=>r.reported_tokens!==null)?<ChartView key={days+':'+selected} title="Reported tokens by model and UTC start date" windowRange={{start:0,end:days-1}} option={usageOptions(data,selected)}/>:<p>No token amounts were reported. The operations below remain recorded.</p>}
     <p className="small-text">Use the legend to compare models or drag the date slider. Blank dates have no reported amount. Partial reports and missing operations can make any bar incomplete.</p>
     <details><summary>Read daily values · keyboard and touch</summary><div className="table-scroll" tabIndex={0} role="region" aria-label="Scrollable activity values"><table><caption>Reported tokens by recorded UTC start date</caption><thead><tr><th>Date</th><th>Provider / reported model</th><th>Operations</th><th>Reported tokens</th><th>Unreported</th></tr></thead><tbody>{data.timeline.filter(r=>selected==='all'||r.backend===selected).map(r=><tr key={r.day+r.backend+r.model}><th>{r.day}</th><td>{provider(r.backend)} · {r.model}</td><td>{r.operations}</td><td>{number(r.reported_tokens)}</td><td>{r.unreported_operations}</td></tr>)}</tbody></table></div></details>
    </section>
    <section className="panel"><h2>Model activity</h2><p>Completed model operations are not completed or independently verified addon features.</p><div className="table-scroll" tabIndex={0} role="region" aria-label="Scrollable activity values"><table><caption>Observed operations in this range</caption><thead><tr><th>Provider / reported model</th><th>Operations</th><th>Completed</th><th>Running</th><th>Other outcomes</th><th>Reported tokens</th></tr></thead><tbody>{visible.map(r=><tr key={r.backend+r.model}><th>{provider(r.backend)}<span className="usage-model">{r.model}</span></th><td>{r.operations}</td><td>{r.completed}</td><td>{r.running}</td><td>{r.other}</td><td>{number(r.reported_tokens)}{r.unreported_operations>0&&<small>{r.unreported_operations} unreported operations</small>}</td></tr>)}</tbody></table></div></section>
   </>}
   <section className="panel"><h2>Fallback history</h2>{data.fallbacks.length?<><ul className="usage-fallbacks">{data.fallbacks.slice(0,allFallbacks?undefined:8).map(f=><li key={f.job_id}><Link to={`/intake/${f.intake_id}`}>{provider(f.from_provider)} → {provider(f.to_provider)}</Link><span>Planning operation saved {formatTime(f.created_at)} · {f.reason.replaceAll('_',' ')}</span></li>)}</ul>{data.fallbacks.length>8&&<Button variant="ghost" onClick={()=>setAllFallbacks(!allFallbacks)}>{allFallbacks?'Show recent switches':'Show all recorded switches'}</Button>}</>:<p>No confirmed fallback switches in this range.</p>}</section>
   <details className="panel"><summary>What these figures measure</summary><p>{data.message}</p><p>{data.undated_operations} records have unavailable or future dates and are excluded from these charts.</p><Link to="/models">Detailed provider records</Link></details>
  </>}
 </div>;
}
