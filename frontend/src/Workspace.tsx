import {useQuery} from '@tanstack/react-query';
import {Link,useSearchParams} from 'react-router-dom';
import {Activity,ArrowRight,CheckCircle2,Clock3,Compass,FileCheck2,FlaskConical,HelpCircle,ListChecks,Search,Settings2,Sparkles,Wrench,AlertCircle,PauseCircle,ChevronRight} from 'lucide-react';
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
export function useWorkspace(page=1,state='all',search='',kind='all',sort='newest'){
 return useQuery({queryKey:['workspace',page,state,search,kind,sort],queryFn:()=>request<Page>(`/api/workspace?page=${page}&state=${encodeURIComponent(state)}&search=${encodeURIComponent(search)}&kind=${encodeURIComponent(kind)}&sort=${encodeURIComponent(sort)}`),refetchInterval:5000});
}
export function WorkshopPanel({title,icon,children,action,className=''}:{title:string;icon?:ReactNode;children:ReactNode;action?:ReactNode;className?:string}){
 return <section className={`panel workshop-panel ${className}`}><div className="leather-section-heading"><h2>{icon}{title}</h2>{action}</div><div className="panel-content">{children}</div></section>;
}
export function TaskState({task}:{task:WorkspaceTask}){
 const Icon=task.active?Activity:task.needs_you?HelpCircle:task.state==='completed'?CheckCircle2:['failed','interrupted'].includes(task.state)?AlertCircle:task.state==='paused'?PauseCircle:Clock3;
 return <span className={`task-state state-${task.state}`}><Icon size={16}/>{task.stage}</span>;
}
export function TaskRow({task,result=false}:{task:WorkspaceTask;result?:boolean}){
 const Icon=icons[task.kind]||Compass;
 const destination=result&&task.result_id?`/executions/${task.result_id}`:`/intake/${task.current_intake_id||task.id}`;
 const delivery=result&&task.state==='completed'?(task.draft_pr_submitted?'Draft pull request submitted':task.result_kind==='patch'?'Local patch · Not installed in game':task.result_kind==='tests'?'Test changes · Saved locally':task.result_kind==='findings'?'Findings · No verified fix':'Saved record'):task.message;
 return <Link className={'everyday-task-row'+(result?' outcome-row':'')+(task.active?' row-active':'')} to={destination}><Icon className="task-kind-icon" size={23}/><span className="task-row-copy"><strong>{task.title}</strong><small>{result&&task.state==='completed'&&['patch','tests'].includes(task.result_kind||'')?'Automated checks passed · ':''}{delivery}</small>{task.history_count&&<small>{task.history_count} saved attempts · {task.history_failures||0} unsuccessful or interrupted. Full history is available.</small>}{!result&&task.priority&&<span className="task-priority">{task.priority_manual?'Priority':'Suggested priority'} {task.priority} · Updated {formatTime(task.updated_at)}</span>}</span><span className={`kind-tag kind-${task.kind}`}>{taskKind(task.kind)}</span><TaskState task={task}/><ArrowRight className="row-arrow" size={18}/></Link>;
}
export function OutcomeCard({task}:{task:WorkspaceTask}){
 const checked=task.state==='completed';const Icon=checked?CheckCircle2:AlertCircle;
 const label=checked?(task.result_kind==='patch'?(task.kind==='feature'?'Changes prepared':'Fix prepared'):task.result_kind==='tests'?'Test coverage added':task.result_kind==='findings'?'Findings available':'Result saved'):'Work needs another look';
 const destination=task.result_id?'/executions/'+task.result_id:'/intake/'+(task.current_intake_id||task.id);
 const delivery=task.draft_pr_submitted?'Draft pull request submitted':task.result_kind==='patch'?'Local patch · Not installed in game':task.result_kind==='tests'?'Test changes · Saved locally':task.result_kind==='findings'?'Findings · No verified fix':'Saved record';
 return <article className={'outcome-card'+(checked?' is-checked':' is-incomplete')}><div className="outcome-card-heading"><span className="outcome-emblem"><Icon size={25}/></span><span><span className="eyebrow">{taskKind(task.kind)}</span><h3><Link to={destination}>{task.title}</Link></h3></span></div><p className="outcome-card-verdict">{label}{checked&&['patch','tests'].includes(task.result_kind||'')?' · Automated checks passed':''}</p><p className="outcome-card-delivery">{delivery}</p><div className="outcome-card-footer"><span>{checked&&['patch','tests'].includes(task.result_kind||'')?'In-game testing still needed':task.stage}</span><Link to={destination}>Review result<ArrowRight size={16}/></Link></div></article>;
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
function AttentionCard({task}:{task:WorkspaceTask}){
 const action=task.state==='needs_input'?'Answer question':task.stage==='Ready for investigation'?'Choose how to start':'Review next step';
 return <Link className="attention-card" to={'/intake/'+(task.current_intake_id||task.id)}><span className="attention-emblem"><HelpCircle size={23}/></span><span className="attention-copy"><strong>{task.title}</strong><span>{task.message}</span><span className="attention-action">{action}<ChevronRight size={16}/></span></span></Link>;
}
function CurrentWork({task,stale}:{task:WorkspaceTask;stale:boolean}){
 const Icon=icons[task.kind]||Compass;
 return <article className="current-work"><div className="current-work-title"><span className="work-emblem"><Icon size={25}/></span><div><Link to={'/intake/'+(task.current_intake_id||task.id)}><h3>{task.title}</h3></Link><span className={'kind-tag kind-'+task.kind}>{taskKind(task.kind)}</span></div></div>
 <div className="current-work-stage"><TaskState task={task}/><p>{task.message}</p></div>
 {!stale&&task.active&&<JourneyRail task={task}/>}
 <div className="current-work-footer"><span>{task.provider?`Working with ${task.provider==='local'?'Local Qwen':task.provider==='codex'?'Codex':task.provider}`:'Provider not reported'}</span><Link to={'/intake/'+(task.current_intake_id||task.id)}>View task<ArrowRight size={15}/></Link></div></article>;
}
export function Dashboard(){
 const query=useWorkspace();const data=query.data;
 if(!data)return <Waiting failed={query.isError} retry={()=>void query.refetch()}/>;
 const {counts,highlights}=data;const active=counts.active||0,needs=counts.needs_you||0,queued=counts.queued||0;
 const model=data.local_model;
 return <div className="dashboard-page"><div className="page-heading"><div><p className="eyebrow">YOUR ADDON WORKSHOP</p><h1>Dashboard</h1><p>Your addon. Moving forward.</p></div><span className={`work-status ${active&&!query.isError?'is-working':''}`}><span/>{query.isError?'Status unavailable':active?'Working':data.work_paused?'Paused':queued?'Work waiting':model?.maintenance_active?'Model used by maintenance':'No work running'}</span></div>
 <RefreshState failed={query.isError} at={data.observed_at}/>
 {data.mode==='demo'&&<p className="soft-callout">Isolated sample workspace. These records are not real-provider acceptance.</p>}
 {model&&model.state!=='idle'&&<section className="soft-callout model-access-note" aria-label="Local model status"><Clock3 size={20}/><div><strong>{model.state==='working'?'Local Qwen is working':model.maintenance_active?'Local model and maintenance':'Local model access'}</strong><p>{model.message}</p>{model.intake_ids.length>0&&<p>{model.automatic?'Recovery is automatic. Your saved revision remains scheduled; you do not need to submit it again.':'Access needs checking before generation can start. Your revision and earlier decisions remain saved.'}</p>}</div></section>}
 {data.work_paused&&<p className="soft-callout" role="status">New work is paused. <Link to="/settings">Resume work in Settings</Link></p>}
 <div className="companion-dashboard">
 <WorkshopPanel className="working-panel" title={query.isError?'Last known work':'Working now'} icon={<Wrench size={18}/>}>
 {highlights.active.length?highlights.active.map(task=><CurrentWork key={task.id} task={task} stale={query.isError}/>):queued?<div className="waiting-work" aria-label="Saved work waiting to start"><h3>Waiting to start</h3>{highlights.waiting?.length?highlights.waiting.map(task=><CurrentWork key={task.id} task={task} stale={query.isError}/>):<p>Your saved work is queued. <Link to="/backlog?state=queued">View waiting tasks</Link>.</p>}</div>:<div className="workshop-welcome"><span className="welcome-emblem"><Compass size={35} strokeWidth={1.3}/></span><div><h3>{data.work_paused?'Your workshop is paused':'Ready when you are'}</h3><p>{data.work_paused?'Your requests are safe. Resume when you’re ready for Workbench to continue.':'Something in your addon could work better? Tell Workbench what you have in mind.'}</p><Link className="welcome-action" to={data.work_paused?'/settings':'/intake/new'}>{data.work_paused?'Open work settings':'Create your next task'}<ArrowRight size={17}/></Link></div></div>}
 </WorkshopPanel>
 <WorkshopPanel className={'attention-panel'+(needs?' has-attention':'')} title="Needs your attention" icon={<HelpCircle size={18}/>} action={needs>3?<Link to="/backlog?state=needs_you">View all {needs}</Link>:undefined}>
 {highlights.needs_you.length?highlights.needs_you.map(task=><AttentionCard key={task.id} task={task}/>):<div className="quiet-empty"><CheckCircle2 size={22}/><p>No questions or approvals are waiting for you.</p></div>}
 </WorkshopPanel>
 <div className="dashboard-counts" aria-label="At a glance">{[{n:active,label:'Working',state:'active',Icon:Wrench},{n:queued,label:'Queued',state:'queued',Icon:Clock3},{n:needs,label:'Need you',state:'needs_you',Icon:HelpCircle}].map(({n,label,state,Icon})=><Link key={state} to={'/backlog?state='+state} aria-label={`${n} ${label}`}><Icon size={25}/><strong>{n}</strong><span>{label}</span></Link>)}</div>
 <WorkshopPanel className="recent-panel" title="Recently completed" icon={<CheckCircle2 size={18}/>} action={<Link to="/results">All results <ArrowRight size={14}/></Link>}>
 {highlights.completed.length?highlights.completed.map(task=><TaskRow key={task.id} task={task} result/>):<div className="quiet-empty"><FileCheck2 size={22}/><p>Checked results will appear here when work finishes.</p></div>}
 </WorkshopPanel>
 </div>
 <details className="workshop-help"><summary>About your workshop</summary><p>Workbench can investigate supported offline Lua bugs and add tests in a protected copy of TwichUI. You review the plan and give permission before changes. Features, inspections and simulator requests can be saved for later.</p><p>Checked changes and draft pull requests still need your review. Merging, installation and testing in World of Warcraft remain separate steps.</p><Link to="/settings">Models and work settings <Settings2 size={15}/></Link></details>
 </div>;
}
export function WorkspaceBacklog(){
 const [params,setParams]=useSearchParams();const state=params.get('state')||'all',search=params.get('search')||'',kind=params.get('kind')||'all',sort=params.get('sort')||'newest',page=Math.max(1,Number(params.get('page'))||1);
 const query=useWorkspace(page,state,search,kind,sort);const data=query.data;
 const update=(values:Record<string,string>)=>setParams({...Object.fromEntries(params),...values,page:'1'});
 return <div className="backlog-page"><div className="page-heading"><div><h1>Backlog</h1><p>Everything you want Workbench to tackle.</p></div></div>
 {data?.work_paused&&<p role="status">New work is paused. <Link to="/settings">Resume work in Settings</Link></p>}
 <div className="backlog-toolbar"><label className="task-search"><Search size={19}/><span className="sr-only">Find a task</span><input type="search" value={search} maxLength={160} placeholder="Find a task" onChange={e=>update({search:e.target.value})}/></label><div className="filter-tabs" aria-label="Task filters">{[['all','All tasks'],['active','Working'],['needs_you','Needs you'],['completed','Completed']].map(([value,label])=><Button key={value} variant={state===value?'default':'secondary'} aria-pressed={state===value} onClick={()=>update({state:value})}>{label}{data&&<span>{data.counts[value]||0}</span>}</Button>)}</div></div>
 <div className="backlog-filters"><label className="status-filter">Status <select aria-label="Task status" value={state} onChange={e=>update({state:e.target.value})}>{[['all','All statuses'],['active','Working or checking'],['needs_you','Needs you'],['queued','Queued'],['preparing','Preparing reviewed work'],['needs_input','Needs input'],['needs_approval','Needs approval'],['decision_pending','Decision recorded'],['completed','Completed'],['held','On hold'],['failed','Could not finish'],['interrupted','Interrupted'],['stopped','Stopped'],['paused','Paused'],['unavailable','Saved for later']].map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label>
 <label className="status-filter">Type of work <select value={kind} onChange={e=>update({kind:e.target.value})}><option value="all">All types</option>{Object.entries(kinds).map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label>
 <label className="status-filter">Order by <select value={sort} onChange={e=>update({sort:e.target.value})}><option value="newest">Newest requests</option><option value="oldest">Oldest requests</option><option value="updated">Recent activity</option><option value="priority">Priority</option></select></label>
 {(search||state!=='all'||kind!=='all'||sort!=='newest')&&<Button variant="ghost" onClick={()=>setParams({})}>Clear filters</Button>}</div>
 {!data?<Waiting failed={query.isError} retry={()=>void query.refetch()}/>:<><RefreshState failed={query.isError} at={data.observed_at}/><WorkshopPanel title="Tasks" action={<span>{data.total} matching {data.total===1?'request':'requests'} · {data.all_total} saved</span>}><div className="task-table-heading" aria-hidden="true"><span>Requested improvement</span><span>Type</span><span>Status</span></div>{data.items.length?data.items.map(task=><TaskRow key={task.id} task={task}/>):<div className="calm-empty"><Search size={28}/><p>{search?'No saved tasks match this search.':'No tasks have this status.'}</p></div>}</WorkshopPanel><Pages data={data} go={next=>setParams({...Object.fromEntries(params),page:String(next)})}/></>}
 <p className="advanced-records-link"><Link to="/backlog?source=fixture">Advanced records and older workspace history <ArrowRight size={14}/></Link></p></div>;
}
function Pages({data,go}:{data:Page;go:(page:number)=>void}){
 if(data.total<=data.page_size)return null;
 return <nav className="task-pagination" aria-label="Task pages"><Button variant="secondary" disabled={data.page===1} onClick={()=>go(data.page-1)}>Previous</Button><span>Page {data.page} of {Math.ceil(data.total/data.page_size)}</span><Button variant="secondary" disabled={data.page*data.page_size>=data.total} onClick={()=>go(data.page+1)}>Next</Button></nav>;
}
export function WorkspaceResults(){
 const [params,setParams]=useSearchParams();const page=Math.max(1,Number(params.get('page'))||1);const query=useWorkspace(page,'results');const data=query.data;
 return <div className="results-page"><div className="page-heading"><div><h1>Results</h1><p>What finished, what changed, and what was checked.</p></div></div>
 <div className="soft-callout result-boundary"><FileCheck2 size={23}/><p>Open a result to review its checks and see whether Workbench submitted a draft pull request.</p></div>
 {!data?<Waiting failed={query.isError} retry={()=>void query.refetch()}/>:<><RefreshState failed={query.isError} at={data.observed_at}/><div className="outcome-grid" aria-label="Saved outcomes">{data.items.length?data.items.map(task=><OutcomeCard key={task.id} task={task}/>):<WorkshopPanel title="Your next result"><div className="calm-empty"><FileCheck2 size={28}/><p>No results yet. Workbench will save changes, findings and check results here.</p></div></WorkshopPanel>}</div><Pages data={data} go={next=>setParams({page:String(next)})}/></>}
 <details className="advanced-records-link"><summary>Other history and evidence</summary><p><Link to="/executions">All recorded runs, including earlier attempts</Link></p><p><Link to="/history">Imported and sample workspace history</Link></p></details></div>;
}
