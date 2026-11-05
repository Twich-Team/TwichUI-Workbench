import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useParams, useSearchParams, useLocation } from 'react-router-dom';
import { CheckCircle2, CircleHelp, XCircle, ArrowLeft, ArrowRight, ChevronDown, ChevronRight, Clock3, FolderTree, RefreshCw, ShieldCheck } from 'lucide-react';
import type { components } from './api.generated';
import { request } from './api';
import { Button } from './components/ui/button';
import { useState } from 'react';

type Meta = Pick<components['schemas']['CatalogPage'], 'source' | 'freshness' | 'partial' | 'version' | 'local_records'>;
type RecordItem = components['schemas']['CatalogRecord'];
type Page = components['schemas']['CatalogPage'];
type Detail = components['schemas']['CatalogDetail'];
type Overview = components['schemas']['WorkOverview'];
type HistoryPage = components['schemas']['HistoryPage'];
export const human = (value: string) => ({request:'Feature request',milestone:'Milestone',task:'Task',inspection:'Inspection','Organizational request':'Request','Organizational milestone':'Milestone',accepted:'Accepted',passed:'Passed',not_published:'Not published',failed:'Unsuccessful',no_change:'No change'}[value] || value);
export const tone = (s: string) => /fail|unsuccess|changes requested/i.test(s) ? 'failed' : /confirmed|passed|accepted|applied|no.change/i.test(s) ? 'verified' : /review|needs|pending|decision/i.test(s) ? 'decision' : /working|progress/i.test(s) ? 'working' : 'neutral';
export function StateBadge({value}: {value:string}) { return <span className={`record-state tone-${tone(value)}`}><OutcomeIcon value={value}/>{human(value)}</span>; }
function OutcomeIcon({value}: {value:string}) { const kind = tone(value); const Icon = kind === 'failed' ? XCircle : kind === 'verified' ? CheckCircle2 : kind === 'neutral' ? CircleHelp : Clock3; return <Icon size={14} aria-hidden="true" />; }
export function useSource() {
  const [params] = useSearchParams();
  const location = useLocation();
  return params.get('source') === 'pilot' || location.pathname.startsWith('/records/pilot/') ? 'pilot' : 'fixture';
}
export function SourcePicker() {
  const [params, setParams] = useSearchParams();
  const client = useQueryClient();
  const [demo, setDemo] = useState(() => sessionStorage.getItem('workbench-demo') || 'simple');
  return <div><label className="source-picker">Workspace<select aria-label="Workspace source" value={useSource()} onChange={event => setParams({ source: event.target.value })}>
    <option value="fixture">Local examples</option><option value="pilot">Maintenance · read only</option>
  </select><span>{params.get('source') === 'pilot' ? 'No live actions' : 'Safe fixture reviews'}</span></label>{useSource() === 'fixture' && <label className="source-picker">Demo scenario<select aria-label="Demo scenario" value={demo} onChange={event => { setDemo(event.target.value); sessionStorage.setItem('workbench-demo', event.target.value); void client.invalidateQueries(); }}><option value="simple">Simple review</option><option value="mixed">Mixed records · 37 items</option><option value="hierarchy">Feature hierarchy</option><option value="reviews">Reviews and recorded outcomes</option><option value="empty">Empty catalog</option></select></label>}</div>;
}
export function SourceStatus({ meta }: { meta: Meta }) {
  return <div className={`source-status source-${meta.freshness.state}`} role="status"><ShieldCheck size={17} /><div><strong>{meta.source === 'fixture' ? meta.local_records ? 'Local workspace · Includes examples' : 'Example workspace' : meta.freshness.state === 'fresh' ? 'Maintenance · fresh observation' : meta.freshness.state === 'stale' ? 'Maintenance · stale observations' : 'Maintenance · unavailable'}</strong>{meta.source === 'pilot' ? <p>{meta.freshness.message}</p> : <details><summary>Source details</summary><p>{meta.freshness.message}</p></details>}{meta.freshness.observed_at && <p>Last observation: <time dateTime={meta.freshness.observed_at}>{formatTime(meta.freshness.observed_at)}</time></p>}{meta.partial && <p>Partial view: the read budget limits this snapshot to 500 records.</p>}</div></div>;
}
export function formatTime(value: string) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value));
}
function LoadError({ retry }: { retry: () => void }) { return <div className="panel form-error" role="alert"><h2>This view couldn’t be loaded.</h2><p>Your saved reviews remain recorded. Try again when the connection is available.</p><Button onClick={retry} variant="secondary">Try again</Button></div>; }
export function WorkSummary({ reviewTaskId }: { reviewTaskId?: string }) {
  const source = useSource();
  const query = useQuery({ queryKey: ['work', source], queryFn: () => request<Overview>(`/api/work?source=${source}`), refetchInterval: 5000 });
  const recent = useQuery({ queryKey: ['recent-history', source], queryFn: () => request<HistoryPage>(`/api/history?source=${source}`), refetchInterval: 5000 });
  if (!query.data) return query.isPending ? <p role="status">Checking recorded work…</p> : <LoadError retry={() => void query.refetch()} />;
  const data = query.data;
  return <section className="work-overview"><SourceStatus meta={data} />{query.isError && <p role="alert">Refresh failed. Showing the last loaded observation.</p>}
    <div className="overview-grid"><div className="panel current-panel"><p className="eyebrow">CURRENT WORK</p><h2>{data.freshness.state === 'fixture' && data.current.length ? 'Simulated state' : data.current.length ? 'Recorded work in progress' : data.freshness.state === 'fixture' ? 'No implementation running.' : data.freshness.state === 'fresh' ? 'No working item recorded.' : 'Current work isn’t confirmed.'}</h2>{data.freshness.state !== 'fixture' || !data.current.length ? <p>{data.current_message}</p> : null}{data.current.map(record => <div key={record.id}><RecordLink record={record} source={source} /><StateBadge value={record.state}/><details><summary>Provenance and stage details</summary><p>{record.reason || 'No next step recorded.'} {source === 'fixture' ? data.current_message : ''}</p></details></div>)}</div>
      <div className="panel decision-panel"><img className="decision-art" src="/art/workshop/decisions.png" alt="" /><p className="eyebrow">NEEDS YOU</p><h2>{['stale','unavailable'].includes(data.freshness.state) ? 'Current count unavailable' : `${data.needs_you} ${data.needs_you === 1 ? 'decision' : 'decisions'}`}</h2><p>{data.waiting_application ? `${data.waiting_application} saved decision waiting to apply.` : 'Choose a recorded review to continue.'}</p><Link to={source === 'fixture' ? reviewTaskId ? `/tasks/${reviewTaskId}/review` : data.needs_you > 0 ? '/intake' : '/history?source=fixture' : '/backlog?source=pilot&view=needs_you'}>Open decision queue <ArrowRight size={14} /></Link></div></div>
    {data.waiting.length > 0 && <section className="panel waiting-work"><h2>Recorded waiting work</h2><p className="detail-copy">Waiting reasons from up to six fresh observations. This does not promise a start time.</p>{data.waiting.map(record => <div key={record.id}><RecordLink record={record} source={source} /><p className="detail-copy">{record.state}: {record.reason || 'No waiting reason was recorded.'}</p></div>)}</section>}
    {recent.data && recent.data.items.length > 0 && <section className="panel recent-changes"><div className="section-heading"><h2>Recent changes</h2><Link to={`/history?source=${source}`}>View all history</Link></div>{recent.data.items.slice(0,3).map(event => <Link className="record-link" key={event.id} to={`/records/${source}/${event.task_id}`}><span><strong>{event.title}</strong><small>{human(event.outcome)} · {formatTime(event.at)}</small></span><ArrowRight size={16} /></Link>)}</section>}
    <div className="section-heading"><Button variant="ghost" aria-label="Refresh work" onClick={() => void query.refetch()}><RefreshCw size={14} />Refresh</Button></div>
  </section>;
}
function RecordLink({ record, source }: { record: RecordItem; source: string }) {
  const location = useLocation();
  const [params] = useSearchParams();
  const back = location.pathname === '/backlog' ? location.search : params.get('back') || '?source='+source;
  return <Link className="record-link" to={`/records/${source}/${encodeURIComponent(record.id)}?back=${encodeURIComponent(back)}`}><span><strong><span className="record-type">{human(record.kind)}</span>{record.title}</strong><small>{record.subsystem || 'Area not recorded'}</small></span><ArrowRight size={16} /></Link>;
}
export function Backlog() {
  const source = useSource();
  const [params, setParams] = useSearchParams();
  const q = params.get('q') || '';
  const view = ['all','needs_you','working','waiting','finished'].includes(params.get('view') || '') ? params.get('view')! : 'open';
  const active = [...params.entries()].filter(([key,value]) => ['q','type','state','area'].includes(key) && value);
  function update(key: string, value: string) { const next = new URLSearchParams(params); next.set(key,value); next.delete('page'); setParams(next); }
  const query = useQuery({ queryKey: ['backlog', params.toString()], placeholderData: previous => previous, queryFn: () => request<Page>(`/api/backlog?${params.toString()}&source=${source}`) });
  return <><div className="page-heading"><div><img className="heading-art" src="/art/workshop/backlog.png" alt="" /><h1>Backlog</h1><p>Browse requests, milestones and tasks.</p></div></div><SourcePicker />
    <div className="backlog-toolbar"><form className="search-form" onSubmit={event => { event.preventDefault(); const form = new FormData(event.currentTarget); update('q', String(form.get('q') || '')); }}><label htmlFor="backlog-search">Find a record</label><div><input id="backlog-search" key={`${source}:${q}`} name="q" defaultValue={q} maxLength={200} placeholder="Search titles and summaries" /><Button type="submit" variant="secondary">Search</Button></div></form>
    <label className="backlog-view">Show<select aria-label="Backlog view" value={view} onChange={event => update('view',event.target.value)}><option value="open">Open work</option><option value="all">All recorded items</option><option value="needs_you">Needs you</option><option value="working">In progress</option><option value="waiting">Waiting</option><option value="finished">Finished</option></select></label>
    <label className="backlog-sort">Sort<select aria-label="Sort backlog" value={params.get('sort') || 'title'} onChange={event => update('sort',event.target.value)}><option value="title">Title</option><option value="updated">Updated · newest first</option></select></label></div>
    <details className="filter-panel"><summary>Filters and sorting{active.length ? ` · ${active.length} active` : ''}</summary>{(['type','state','area'] as const).map(key => <fieldset key={key}><legend>{key === 'area' ? 'Area' : key === 'type' ? 'Type' : 'State'}</legend>{(query.data?.facets[key] || []).map(value => <label key={value}><input type="checkbox" checked={params.getAll(key).includes(value)} onChange={event => { const next = new URLSearchParams(params); const values = next.getAll(key).filter(v => v !== value); next.delete(key); if(event.target.checked) values.push(value); values.forEach(v => next.append(key,v)); next.delete('page'); setParams(next); }} />{human(value) || 'Unknown'}</label>)}</fieldset>)}</details>
    {active.length > 0 && <div className="active-filters">{active.map(([key,value]) => <button key={key+value} onClick={() => { const next = new URLSearchParams(params); next.delete(key,value); next.delete('page'); setParams(next); }}>{key}: {human(value)} ×</button>)}<Button variant="ghost" onClick={() => setParams({source})}>Clear all</Button></div>}
    {!query.data ? query.isPending ? <p role="status">Opening the backlog…</p> : <LoadError retry={() => void query.refetch()} /> : <><SourceStatus meta={query.data} /><div className="section-heading"><h2>{query.data.filtered ? 'Matching records · ancestor context' : params.has('focus') ? 'Requested work' : view === 'needs_you' ? 'Decision queue' : 'Top-level records'}</h2><Button variant="ghost" onClick={() => void query.refetch()} aria-label="Refresh backlog"><RefreshCw size={14} />Refresh</Button></div>
      {query.isPlaceholderData && <p role="status">Updating records…</p>}
      {query.data.items.length ? <div className="record-list" aria-busy={query.isPlaceholderData} inert={query.isPlaceholderData}>{query.data.items.map(row => <RecordRow key={`${source}:${q}:${row.id}`} row={row} source={source} depth={(() => { let depth = 0; let parent = row.parent; const seen = new Set<string>(); while(parent && !seen.has(parent)) { seen.add(parent); depth++; parent = query.data.items.find(item => item.id === parent)?.parent || null; } return depth; })()} expand={!query.data.filtered} />)}</div> : <div className="panel empty-panel"><FolderTree size={24} /><h2>{q ? 'No matching records.' : 'No records to show.'}</h2><p>{q ? 'Try a shorter search or clear it to browse the hierarchy.' : 'Source status above explains whether observations are available.'}</p></div>}
      <Pagination page={query.data.page} busy={query.isPlaceholderData} label={query.data.filtered ? 'matching records (ancestors are context)' : 'top-level records'} total={query.data.total} size={12} onChange={next => setParams(previous => { const copy = new URLSearchParams(previous); copy.set('page',String(next)); return copy; })} />
    </>}
    {params.has('focus') && <Link className="back-link" to={`/backlog?source=${source}`}>Browse all Backlog records</Link>}
    <details className="diagnostics"><summary>Hierarchy limits</summary><p>Hierarchy organizes work. It does not set scheduling order, satisfy dependencies, or grant execution permission. Scheduling and editing are unavailable here.</p></details>
  </>;
}
function RecordRow({ row, source, depth, expand = true }: { row: RecordItem; source: string; depth: number; expand?: boolean }) {
  const [params] = useSearchParams();
  const view = ['all','needs_you','working','waiting','finished'].includes(params.get('view') || '') ? params.get('view')! : 'open';
  const expansionKey = `expanded:${source}:${row.id}`;
  const [open, setOpen] = useState(() => { try { return params.get('focus') === row.id || sessionStorage.getItem(expansionKey) === '1'; } catch { return false; } });
  function toggle() {
    try { sessionStorage.setItem(expansionKey, open ? '0' : '1'); } catch { /* Expansion still works without browser storage. */ }
    setOpen(!open);
  }
  const [page, setPage] = useState(1);
  const query = useQuery({ queryKey: ['children',source,row.id,page,view], queryFn: () => request<Page>(`/api/backlog?source=${source}&parent=${encodeURIComponent(row.id)}&page=${page}&view=${view}`), enabled: open && expand });
  return <div className="tree-item" style={{ '--tree-depth': Math.min(depth, 3) } as React.CSSProperties}>
    <div className={`record-row ${row.context ? "context-row" : ""}`}>{row.children > 0 && expand ? <button className="expand-button" aria-label={`${open ? 'Collapse' : 'Expand'} ${row.title}`} aria-expanded={open} onClick={toggle}>{open ? <ChevronDown size={18} /> : <ChevronRight size={18} />}</button> : <span className="row-spacer" />}
      <RecordLink record={row} source={source} /><span className={`record-state tone-${tone(['request','milestone'].includes(row.kind) ? '' : row.state)}`}>{source === 'pilot' && !row.currently_observed ? 'Last known: ' : ''}{row.context ? "Ancestor context · " : ""}{['request','milestone'].includes(row.kind) ? `${row.children} ${row.children === 1 ? 'subitem' : 'subitems'}` : human(row.state)}</span>
    </div>
    {open && expand && <div className="tree-children">{query.isPending ? <p role="status">Opening children…</p> : query.isError ? <LoadError retry={() => void query.refetch()} /> : <>{query.data.items.map(child => depth < 8 ? <RecordRow key={child.id} row={child} source={source} depth={depth+1} /> : <RecordLink key={child.id} record={child} source={source} />)}{query.data.total > 12 && <Pagination page={page} total={query.data.total} size={12} onChange={setPage} />}</>}</div>}
  </div>;
}
function Pagination({ page, total, size, onChange, label = 'recorded items', busy = false }: { busy?: boolean; label?: string; page: number; total: number; size: number; onChange: (value:number) => void }) {
  if (total <= size) return <p className="page-count">{total} {label}</p>;
  return <div className="pagination" aria-label="Pagination"><span>{total} {label} · Page {page} of {Math.max(1,Math.ceil(total/size))}</span><Button variant="secondary" disabled={busy || page<=1} onClick={() => onChange(page-1)}>Previous</Button><Button variant="secondary" disabled={busy || page*size>=total} onClick={() => onChange(page+1)}>Next</Button></div>;
}
export function RecordDetail() {
  const { source = 'fixture', recordId = '' } = useParams();
  const [detailParams] = useSearchParams();
  const back = detailParams.get('back') || '?source='+source;
  const query = useQuery({ queryKey:['record',source,recordId], queryFn: () => request<Detail>(`/api/records/${source}/${encodeURIComponent(recordId)}`) });
  if (!query.data) return <><Link className="back-link" to={`/backlog${back.startsWith('?') ? back : '?source='+source}`}><ArrowLeft size={15}/>Backlog</Link>{query.isPending ? <p role="status">Opening record…</p> : <LoadError retry={() => void query.refetch()} />}</>;
  const {record:row, ancestors, children,children_total} = query.data;
  return <><Link className="back-link" to={`/backlog${back.startsWith("?") ? back : "?source="+source}`}><ArrowLeft size={15} />Backlog</Link><nav className="breadcrumbs" aria-label="Record hierarchy">{ancestors.map(parent => <Link key={parent.id} to={`/records/${source}/${encodeURIComponent(parent.id)}?back=${encodeURIComponent(back)}`}>{parent.title}<ChevronRight size={12} /></Link>)}</nav><div className="page-heading detail-heading"><div><p className="eyebrow">{row.kind} · {row.subsystem}</p><h1>{row.title}</h1><p>{row.summary || 'No summary was recorded.'}</p></div></div><SourceStatus meta={query.data} />
    <div className="detail-grid"><div><section className="panel"><h2>What this record tells us</h2><p className="detail-copy">{row.intake_id ? 'Local planning record. Execution remains held; scope approval is separate from implementation authorization.' : source === 'pilot' ? 'This is a read-only observation from maintenance. Execution and permission remain owned by the existing worker.' : 'This example illustrates the relationship between a goal and its tasks.'}</p><dl className="record-facts"><div><dt>{source === 'pilot' ? 'Last recorded task or organizational state' : 'Task or organizational state'}</dt><dd>{['request','milestone'].includes(row.kind) ? `${children_total} subitems` : <StateBadge value={row.state}/>}</dd></div><div><dt>Recorded attempt outcome</dt><dd>{human(row.attempt_outcome)}</dd></div><div><dt>Verification</dt><dd>{human(row.verification)}</dd></div><div><dt>Publication</dt><dd>{human(row.publication)}</dd></div></dl>
      {row.intake_id && <><Button asChild variant="secondary"><Link to={`/intake/${row.intake_id}`}>Open request and proposal<ArrowRight size={14}/></Link></Button><details><summary>Planning acceptance and dependencies</summary><ul>{row.planning_acceptance.map((a,i)=><li key={i}>{a}</li>)}</ul><p>Approved proposal revision identity: {row.proposal_id || 'Triage task; no implementation approval'}</p><p>Planning model: {row.planning_model}</p><p>Dependencies: {row.dependencies.join(', ') || 'None'}</p></details></>}{row.review_task_id && source === 'fixture' ? <Button asChild><Link to={`/tasks/${row.review_task_id}`}>Open fixture review<ArrowRight size={14} /></Link></Button> : <div className="soft-callout">Review, retry, scheduling and publication actions are unavailable for this record. No execution permission is granted by browsing it.</div>}
      </section><section className="panel evidence-panel"><h2>Subitems</h2><p className="detail-copy">{children_total} subitems. Counts describe records, not effort or feature acceptance.</p>{children.map(child => <RecordLink key={child.id} record={child} source={source} />)}{children_total > children.length && <Link className="back-link" to={`/backlog?source=${source}`}>Expand this record in Backlog for all child pages.</Link>}{!children.length && <p className="detail-copy">No subitems were recorded.</p>}</section></div>
      <aside className="context-column"><section className="panel"><h2>Recorded attempts</h2>{row.attempts.length ? row.attempts.map((attempt,index) => <div className="attempt-row" key={index}><strong>{human(attempt.outcome)}</strong><p>Model: {attempt.model}</p></div>) : <p className="detail-copy">No individual attempt details were retained in this view.</p>}</section><details className="diagnostics"><summary>Identity and provenance</summary><p>Source: {source}</p><p>Canonical record: {row.id}</p><p>Source version: {row.source_version || 'Not recorded'}</p><p>Observation time: {row.observed_at ? formatTime(row.observed_at) : 'Not recorded'}</p><p>Update time: {row.updated_at ? formatTime(row.updated_at) : 'Not recorded'}</p><p>{row.date_basis}</p><p>Dependencies are separate from this hierarchy; dependency editing is unavailable.</p></details></aside>
    </div>
  </>;
}
export function History() {
  const source = useSource();
  const [params,setParams] = useSearchParams();
  const page = Math.max(1,Number(params.get('page') || 1) || 1);
  const query = useQuery({ queryKey:['history',source,page], queryFn: () => request<HistoryPage>(`/api/history?source=${source}&page=${page}`) });
  return <><div className="page-heading"><div><img className="heading-art" src="/art/workshop/history.png" alt="" /><h1>History</h1><p>Recorded outcomes and related work.</p></div><Button variant="secondary" onClick={() => void query.refetch()}><RefreshCw size={15} />Refresh history</Button></div><SourcePicker />
    {!query.data ? query.isPending ? <p role="status">Opening recorded history…</p> : <LoadError retry={() => void query.refetch()} /> : <><SourceStatus meta={query.data} /><details className="history-note"><summary>History coverage</summary><p>{query.data.message}</p></details>{query.data.items.length ? <div className="history-list">{query.data.items.map(event => <article className="history-event" key={event.id}><div><span className={`badge tone-${tone(event.outcome)}`}><OutcomeIcon value={event.outcome} />{human(event.outcome)}</span><time dateTime={event.at}>{formatTime(event.at)}</time></div><h2>{event.title}</h2><details><summary>Recorded evidence</summary><p>{event.message}</p><small>{event.date_basis}</small><Link className="back-link" to={`/records/${source}/${encodeURIComponent(event.task_id)}`}>Open related record<ArrowRight size={14} /></Link></details></article>)}</div> : <div className="panel empty-panel"><Clock3 size={24} /><h2>No dated events to show.</h2><p>Unknown dates remain unknown. This is not proof that no work happened.</p></div>}<Pagination page={page} total={query.data.total} size={20} onChange={next => setParams({source,page:String(next)})} /></>}
  </>;
}
