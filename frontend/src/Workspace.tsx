import {useQuery} from '@tanstack/react-query';
import {Link,useSearchParams} from 'react-router-dom';
import {Activity,ArrowRight,CheckCircle2,Clock3,Compass,FileCheck2,FlaskConical,HelpCircle,ListChecks,Search,Settings2,Sparkles,Wrench} from 'lucide-react';
import type {ReactNode} from 'react';
import type {components} from './api.generated';
import {request} from './api';
import {Button} from './components/ui/button';
import {formatTime} from './Catalog';

type Page=components['schemas']['WorkspacePage'];
export type WorkspaceTask=components['schemas']['WorkspaceTask'];
const kinds:Record<string,string>={bug:'Bug fix',coverage:'Test coverage',feature:'Feature',inspection:'Inspection',simulation:'Simulator',data:'Data update',other:'Other work'};
const icons:Record<string,typeof Wrench>={bug:Wrench,coverage:ListChecks,feature:Sparkles,inspection:Search,simulation:FlaskConical,data:FileCheck2};
export const taskKind=(kind:string)=>kinds[kind]||'Task';
export function useWorkspace(page=1,state='all',search=''){
 return useQuery({queryKey:['workspace',page,state,search],queryFn:()=>request<Page>(`/api/workspace?page=${page}&state=${encodeURIComponent(state)}&search=${encodeURIComponent(search)}`),refetchInterval:5000});
}
export function WorkshopPanel({title,icon,children,action,className=''}:{title:string;icon?:ReactNode;children:ReactNode;action?:ReactNode;className?:string}){
 return <section className={`panel workshop-panel ${className}`}><div className="leather-section-heading"><h2>{icon}{title}</h2>{action}</div><div className="panel-content">{children}</div></section>;
}
export function TaskState({task}:{task:WorkspaceTask}){
 return <span className={`task-state state-${task.state}`}>{task.active?<Activity size={15}/>:task.needs_you?<HelpCircle size={15}/>:task.state==='completed'?<CheckCircle2 size={15}/>:<Clock3 size={15}/>} {task.stage}</span>;
}
export function TaskRow({task,result=false}:{task:WorkspaceTask;result?:boolean}){
 const Icon=icons[task.kind]||Compass;
 const destination=result&&task.result_id?`/executions/${task.result_id}`:`/intake/${task.id}`;
 return <Link className="everyday-task-row" to={destination}><Icon className="task-kind-icon" size={23}/><span className="task-row-copy"><strong>{task.title}</strong><span className="task-row-meta"><span className={`kind-tag kind-${task.kind}`}>{taskKind(task.kind)}</span><TaskState task={task}/></span><small>{result&&task.state==='completed'?(task.result_kind==='patch'?'Local patch · Not installed in game':task.result_kind==='tests'?'Test changes · Saved locally':task.result_kind==='findings'?'Findings · No verified fix':'Saved record'):task.message}</small></span><ArrowRight className="row-arrow" size={18}/></Link>;
}
function RefreshState({failed,at}:{failed:boolean;at:string}){
 return <p className={failed?'stale-display':'freshness-note'} role={failed?'alert':undefined}>{failed?'Refresh unavailable. These are the last loaded records. Decisions need a fresh view.':`View refreshed ${formatTime(at)}`}</p>;
}
function Waiting({failed,retry}:{failed:boolean;retry:()=>void}){
 return <section className="panel empty-panel" role={failed?'alert':'status'}><Compass size={32}/><h2>{failed?'Your tasks could not be loaded':'Opening your workspace…'}</h2><p>{failed?'Your history is still saved. Check the connection and try again.':'Reading saved task records. This does not start a model.'}</p>{failed&&<Button variant="secondary" onClick={retry}>Try again</Button>}</section>;
}
function JourneyRail({task}:{task:WorkspaceTask}){
 const step=task.state==='working'?1:task.state==='checking'||task.state==='cleanup'?2:task.state==='completed'?3:0;
 return <ol className="journey-rail" aria-label="Task stages">{['Understand','Work','Check','Review'].map((label,index)=><li className={index<step?'done':index===step?'current':''} aria-current={index===step?'step':undefined} key={label}><span>{index<step?<CheckCircle2 size={18}/>:index+1}</span>{label}</li>)}</ol>;
}
export function Dashboard(){
 const query=useWorkspace();const data=query.data;
 if(!data)return <Waiting failed={query.isError} retry={()=>void query.refetch()}/>;
 const {counts,highlights}=data;const active=counts.active||0,needs=counts.needs_you||0,queued=counts.queued||0;
 const model=data.local_model;
 return <div className="dashboard-page"><div className="page-heading"><div><h1>Dashboard</h1><p>Your addon. Moving forward.</p></div><span className={`work-status ${active?'is-working':''}`}><span/>{active?'Working':queued?'Work waiting':model?.maintenance_active?'Model used by maintenance':'No work running'}</span></div>
 <RefreshState failed={query.isError} at={data.observed_at}/>
 {data.mode==='demo'&&<p className="soft-callout">Isolated sample workspace. These records are not real-provider acceptance.</p>}
 {model&&model.state!=='idle'&&<section className="soft-callout" aria-label="Local model status"><Clock3 size={20}/><div><strong>{model.state==='working'?'Local Qwen is working':model.maintenance_active?'Local model and maintenance':'Local model access'}</strong><p>{model.message}</p>{model.intake_ids.length>0&&<p>{model.automatic?'Recovery is automatic. Your saved revision remains scheduled; you do not need to submit it again.':'Access needs checking before generation can start. Your revision and earlier decisions remain saved.'}</p>}</div></section>}
 <div className="dashboard-grid"><div className="dashboard-main">
 <WorkshopPanel title="Working now" icon={<Activity size={18}/>}>
 {highlights.active.length?highlights.active.map(task=><div className="working-task" key={task.id}><TaskRow task={task}/><JourneyRail task={task}/><p className="task-latest"><strong>Latest update</strong> {task.message}{task.provider&&` · Selected provider: ${task.provider==='local'?'Local Qwen':task.provider==='codex'?'Codex':task.provider}`}</p></div>):queued?<div aria-label="Saved work waiting to start"><h3>Waiting to start</h3>{highlights.waiting?.length?highlights.waiting.map(task=><TaskRow key={task.id} task={task}/>):<p>Your saved work is queued. <Link to="/backlog?state=queued">View waiting tasks</Link>.</p>}</div>:<div className="calm-empty"><Compass size={32}/><div><h3>Ready when you are</h3><p>Describe an issue or a test you want to add. Workbench will explain the next step before changing anything.</p><Link to="/intake/new">Create your next task <ArrowRight size={15}/></Link></div></div>}
 </WorkshopPanel>
 <WorkshopPanel title="Needs your attention" icon={<HelpCircle size={18}/>} action={needs>3?<Link to="/backlog?state=needs_you">View all {needs}</Link>:undefined}>
 {highlights.needs_you.length?highlights.needs_you.map(task=><TaskRow key={task.id} task={task}/>):<div className="quiet-empty"><CheckCircle2 size={20}/><p>No questions or approvals are waiting for you.</p></div>}
 </WorkshopPanel>
 <div className="dashboard-counts">{[{n:active,label:'Working',state:'active',Icon:Activity},{n:counts.queued||0,label:'Queued',state:'queued',Icon:Clock3},{n:needs,label:'Need you',state:'needs_you',Icon:HelpCircle}].map(({n,label,state,Icon})=><Link key={state} to={'/backlog?state='+state}><Icon size={25}/><strong>{n}</strong><span>{label}</span></Link>)}</div>
 <WorkshopPanel title="Recent outcomes" icon={<CheckCircle2 size={18}/>} action={<Link to="/results">All results <ArrowRight size={14}/></Link>}>
 {(highlights.outcomes||highlights.completed).length?(highlights.outcomes||highlights.completed).map(task=><TaskRow key={task.id} task={task} result/>):<p className="quiet-empty">Checked results will appear here when work finishes.</p>}
 </WorkshopPanel></div><aside className="dashboard-notes">
 <WorkshopPanel title="Your workshop"><h3>What can Workbench do?</h3><p>Investigate supported offline Lua bugs and add regression coverage in a protected copy of TwichUI.</p><p>You review the plan and give separate permission for the work. Checked changes stay local.</p><Link to="/intake/new">See task types <ArrowRight size={15}/></Link></WorkshopPanel>
 <WorkshopPanel title="Keep the distinction"><ul className="plain-list"><li>Offline checks are not in-game testing.</li><li>A checked patch is not an installed addon.</li><li>Publishing needs separate permission.</li></ul><Link to="/settings">Models and work settings <Settings2 size={15}/></Link></WorkshopPanel>
 </aside></div></div>;
}
export function WorkspaceBacklog(){
 const [params,setParams]=useSearchParams();const state=params.get('state')||'all',search=params.get('search')||'',page=Math.max(1,Number(params.get('page'))||1);
 const query=useWorkspace(page,state,search);const data=query.data;
 const update=(values:Record<string,string>)=>setParams({...Object.fromEntries(params),...values,page:'1'});
 return <div className="backlog-page"><div className="page-heading"><div><h1>Backlog</h1><p>Everything you want Workbench to tackle.</p></div></div>
 <div className="backlog-toolbar"><label className="task-search"><Search size={19}/><span className="sr-only">Find a task</span><input type="search" value={search} maxLength={160} placeholder="Find a task" onChange={e=>update({search:e.target.value})}/></label><div className="filter-tabs" aria-label="Task filters">{[['all','All tasks'],['needs_you','Needs you'],['completed','Completed']].map(([value,label])=><Button key={value} variant={state===value?'default':'secondary'} aria-pressed={state===value} onClick={()=>update({state:value})}>{label}{data&&<span>{data.counts[value]||0}</span>}</Button>)}</div></div>
 <label className="status-filter">Status <select aria-label="Task status" value={state} onChange={e=>update({state:e.target.value})}>{[['all','All statuses'],['active','Working or checking'],['needs_you','Needs you'],['queued','Queued'],['preparing','Preparing reviewed work'],['needs_input','Needs input'],['needs_approval','Needs approval'],['decision_pending','Decision recorded'],['completed','Completed'],['held','On hold'],['failed','Could not finish'],['interrupted','Interrupted'],['stopped','Stopped'],['paused','Paused'],['unavailable','Saved for later']].map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label>
 {!data?<Waiting failed={query.isError} retry={()=>void query.refetch()}/>:<><RefreshState failed={query.isError} at={data.observed_at}/><WorkshopPanel title="Tasks" action={<span>{data.total} matching {data.total===1?'request':'requests'} · {data.all_total} saved</span>}><div className="task-table-heading" aria-hidden="true"><span>Task</span><span>Type and status</span></div>{data.items.length?data.items.map(task=><TaskRow key={task.id} task={task}/>):<div className="calm-empty"><Search size={28}/><p>{search?'No saved tasks match this search.':'No tasks have this status.'}</p></div>}</WorkshopPanel><Pages data={data} go={next=>setParams({...Object.fromEntries(params),page:String(next)})}/></>}
 <p className="advanced-records-link"><Link to="/backlog?source=fixture">Advanced records and older workspace history <ArrowRight size={14}/></Link></p></div>;
}
function Pages({data,go}:{data:Page;go:(page:number)=>void}){
 if(data.total<=data.page_size)return null;
 return <nav className="task-pagination" aria-label="Task pages"><Button variant="secondary" disabled={data.page===1} onClick={()=>go(data.page-1)}>Previous</Button><span>Page {data.page} of {Math.ceil(data.total/data.page_size)}</span><Button variant="secondary" disabled={data.page*data.page_size>=data.total} onClick={()=>go(data.page+1)}>Next</Button></nav>;
}
export function WorkspaceResults(){
 const [params,setParams]=useSearchParams();const page=Math.max(1,Number(params.get('page'))||1);const query=useWorkspace(page,'results');const data=query.data;
 return <div className="results-page"><div className="page-heading"><div><h1>Results</h1><p>What finished, what changed, and what was checked.</p></div></div>
 <div className="soft-callout result-boundary"><FileCheck2 size={23}/><p>Results stay local. A checked patch has not been installed into your game or published as a pull request.</p></div>
 {!data?<Waiting failed={query.isError} retry={()=>void query.refetch()}/>:<><RefreshState failed={query.isError} at={data.observed_at}/><WorkshopPanel title="Saved outcomes">{data.items.length?data.items.map(task=><TaskRow key={task.id} task={task} result/>):<p className="quiet-empty">No results yet. Workbench will save changes, findings and check results here.</p>}</WorkshopPanel><Pages data={data} go={next=>setParams({page:String(next)})}/></>}
 <details className="advanced-records-link"><summary>Other history and evidence</summary><p><Link to="/executions">All recorded runs, including earlier attempts</Link></p><p><Link to="/history">Imported and sample workspace history</Link></p></details></div>;
}
