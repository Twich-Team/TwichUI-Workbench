import {useQuery} from '@tanstack/react-query';
import {Link, useSearchParams} from 'react-router-dom';
import {request} from './api';
import {formatTime} from './Catalog';
import type {components} from './api.generated';

type Page=components['schemas']['RunPage'];
export const runStage:Record<string,string>={preparing:'Preparing approved task',settling:'Checks passed · Finishing cleanup',held:'Needs attention',completed:'Completed',pending:'Waiting',recovering:'Waiting for safe recovery',cancel_requested:'Stopping · Waiting for cleanup',cancelled:'Stopped · History retained',running:'Working in isolation',checking:'Checking changes',checked:'Completed · Checks passed',failed:'Needs attention',blocked:'Needs attention · Permission or setup',interrupted:'Interrupted · Evidence retained'};
export function ExecutionRuns({preview=false}:{preview?:boolean}){
  const [search,setSearch]=useSearchParams();
  const group=preview?'all':(['all','active','attention','checked'].includes(search.get('group')||'')?search.get('group')||'all':'all');
  const page=preview?1:Math.min(1000,Math.max(1,Math.floor(Number(search.get('page')))||1));
  const query=useQuery({queryKey:['real-runs',page,group],queryFn:()=>request<Page>(`/api/real-executions?page=${page}&group=${group}`),refetchInterval:3000});
  if(query.isPending)return preview?null:<section className="panel" aria-busy="true">Loading recorded task runs…</section>;
  if(!query.data)return <section className="panel" role="alert">Task runs could not be loaded. <button onClick={()=>void query.refetch()}>Try again</button></section>;
  const data=query.data,items=preview?data.items.slice(0,3):data.items;
  if(preview&&(!data.available||data.total===0))return null;
  const Heading=preview?'h2':'h1';
  return <section className="panel execution-runs" aria-label="Recorded real task runs">
    <div className="section-heading"><Heading>{preview?'Real task runs':'Recorded task runs'}</Heading>{preview&&<Link to="/executions">View all runs</Link>}</div>
    <p>Saved in this workspace. Completion and verification are recorded separately from acceptance and publication.</p>
    {query.isError&&<p className="form-error" role="alert">Refresh unavailable. Showing the last loaded runs.</p>}
    {!data.available?<p>{data.reason}</p>:<>
      {!preview&&<label>Show <select aria-label="Filter task runs" value={group} onChange={e=>setSearch({group:e.target.value,page:'1'})}>
        <option value="all">All runs</option><option value="active">Waiting or working</option><option value="attention">Needs attention</option><option value="checked">Checks passed</option>
      </select></label>}
      {!items.length?<p>{group==='all'?'No real task runs are recorded here yet.':'No runs match this view.'} Existing records are kept; opening this page starts no work.</p>:<ul className="execution-run-list">{items.map(row=><li key={row.id}>
        <Link to={'/executions/'+encodeURIComponent(row.id)}>{row.title}</Link>
        <p className="badge">{runStage[row.recovery_pending?'recovering':row.status]||'Recorded state unavailable'}</p><p>{row.message}</p>
        <small>{row.finished_at?'Finished':'Saved'} {formatTime(row.finished_at||row.created_at)}</small>
        {row.has_candidate&&<p>Frozen changes available · {row.verification==='passed'?'Independent checks passed':'Check evidence needs attention'}</p>}
      </li>)}</ul>}
      {!preview&&data.total>data.page_size&&<nav aria-label="Task run pages"><button disabled={page===1} onClick={()=>setSearch({group,page:String(page-1)})}>Previous</button> <span>Page {page} of {Math.ceil(data.total/data.page_size)}</span> <button disabled={page*data.page_size>=data.total} onClick={()=>setSearch({group,page:String(page+1)})}>Next</button></nav>}
    </>}
    <p className="muted">No model, retry, approval or publication is started by this view.</p>
  </section>;
}
