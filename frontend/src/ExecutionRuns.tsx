import {useQuery} from '@tanstack/react-query';
import {Link, useSearchParams} from 'react-router-dom';
import {request} from './api';
import {runStage} from './taskProgress';
import {formatTime} from './Catalog';
import type {components} from './api.generated';

type Page=components['schemas']['RunPage'];
export function ExecutionRuns(){
  const [search,setSearch]=useSearchParams();
  const group=(['all','active','attention','checked'].includes(search.get('group')||'')?search.get('group')||'all':'all');
  const page=Math.min(1000,Math.max(1,Math.floor(Number(search.get('page')))||1));
  const query=useQuery({queryKey:['real-runs',page,group],queryFn:()=>request<Page>(`/api/real-executions?page=${page}&group=${group}`),refetchInterval:3000});
  if(query.isPending)return <section className="panel" aria-busy="true">Loading recorded task runs…</section>;
  if(!query.data)return <section className="panel" role="alert">Task runs could not be loaded. <button onClick={()=>void query.refetch()}>Try again</button></section>;
  const {items, ...data}=query.data;
  return <section className="panel execution-runs" aria-label="Recorded real task runs">
    <div className="section-heading"><h1>Run results</h1></div>
    <p>Review work in progress, completed checks and runs that stopped.</p>
    {query.isError&&<p className="form-error" role="alert">Refresh unavailable. Showing the last loaded runs.</p>}
    {!data.available?<p>{data.reason}</p>:<>
      <label>Show <select aria-label="Filter task runs" value={group} onChange={e=>setSearch({group:e.target.value,page:'1'})}>
        <option value="all">All runs</option><option value="active">Waiting or working</option><option value="attention">Needs attention</option><option value="checked">Checks passed</option>
      </select></label>
      {!items.length?<p>{group==='all'?'No runs are recorded here yet.':'No runs match this view.'}</p>:<ul className="execution-run-list">{items.map(row=><li key={row.id}>
        <Link to={'/executions/'+encodeURIComponent(row.id)}>{row.title}</Link>
        <p className="badge">{row.status==='checked'&&row.verification!=='passed'?'Run result saved':runStage[row.recovery_pending?'recovering':row.status]||'Recorded state unavailable'}</p><p>{row.message}</p>
        <small>{row.finished_at?'Finished':'Saved'} {formatTime(row.finished_at||row.created_at)}</small>
        {row.has_candidate&&<p>Changes saved · {row.verification==='passed'?'Independent checks passed':'Review check results'}</p>}
      </li>)}</ul>}
      {data.total>data.page_size&&<nav aria-label="Task run pages"><button disabled={page===1} onClick={()=>setSearch({group,page:String(page-1)})}>Previous</button> <span>Page {page} of {Math.ceil(data.total/data.page_size)}</span> <button disabled={page*data.page_size>=data.total} onClick={()=>setSearch({group,page:String(page+1)})}>Next</button></nav>}
    </>}
  </section>;
}
