import {Dashboard,WorkspaceBacklog,WorkspaceResults} from './Workspace';
import {ExecutionRuns} from './ExecutionRuns';
import {RealExecutionReview} from './RealExecutionReview';
import { useEffect, useRef, useState } from 'react';
import { Link, NavLink, Route, Routes, useLocation, useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Activity, History as HistoryIcon, FolderTree, ArrowLeft, ArrowRight, BookOpen, Check, CheckCheck, ChevronRight, CircleDot, Clock3, FileCheck2, Settings as SettingsIcon, Monitor, Cpu, Home, ListTodo, ScrollText, Plus, RefreshCw, ShieldCheck } from 'lucide-react';
import { ApiError, loadTask, readDraft, request, saveDecision, taskKey, type Task, type Decision, type Receipt } from './api';
import { Button } from './components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogTitle } from './components/ui/dialog';

import { IntakeActions, IntakeList, IntakeForm, IntakeDetail } from './Intake';
import { TaskOverview } from './TaskOverview';
import { Monitoring } from './Monitoring';
import { Settings } from './Settings';
import { Models } from './Models';
import { Verification } from './Verification';
import { Backlog, History, RecordDetail, SourcePicker, WorkSummary, useSource, tone } from './Catalog';

const stateLabel: Record<Task['state'], string> = { held: 'Needs your review', review_confirmed: 'Review confirmed', changes_requested: 'Changes requested' };

export function taskDisplayState(task: Task) {
  if (task.latest_command?.status === 'pending') return 'Decision saved · Waiting to apply';
  if (task.latest_command?.status === 'stale') return 'Needs another look';
  if (task.latest_command?.status === 'failed') return 'Needs investigation';
  return stateLabel[task.state];
}

export function App() {
  const location=useLocation();
  const path=location.pathname;
  const links=[{title:'Dashboard',to:'/',icon:Home,active:path==='/'||path==='/intake/new'},
    {title:'Backlog',to:'/backlog',icon:ListTodo,active:path.startsWith('/backlog')||(path.startsWith('/intake')&&path!='/intake/new')||path.startsWith('/records')},
    {title:'Results',to:'/results',icon:ScrollText,active:path.startsWith('/results')||path.startsWith('/executions')||path.startsWith('/history')},
    {title:'Settings',to:'/settings',icon:SettingsIcon,active:path.startsWith('/settings')||path.startsWith('/models')||path.startsWith('/monitoring')}];
  const create=<Button asChild><Link to="/intake/new"><Plus size={18}/>Create a task</Link></Button>;
  return <div className="app-shell styled-workbench">
    <a className="skip-link" href="#main">Skip to content</a>
    <header className="identity-bar"><Link to="/" className="brand" aria-label="TwichUI Workbench home"><img className="brand-logo" src="/art/branding/twichui-launcher-transparent-edited.png" alt=""/><span>TwichUI Workbench</span></Link><span className="identity-note"><ShieldCheck size={15}/>Private workshop</span><div className="mobile-create">{create}</div></header>
    <aside className="sidebar"><nav className="primary-nav" aria-label="Main navigation">{links.map(({title,to,icon:Icon,active})=><Link key={title} to={to} aria-current={active?'page':undefined} className={'nav-item'+(active?' active':'')}><Icon size={21}/><span>{title}</span></Link>)}</nav><div className="sidebar-create">{create}</div><p className="sidebar-caption">Your addon.<br/>Your decisions.</p></aside>
    <div className="workspace"><main id="main"><Routes>
      <Route path="/" element={new URLSearchParams(location.search).has('source')?<TaskList/>:<Dashboard/>}/>
      <Route path="/backlog" element={new URLSearchParams(location.search).has('source')||new URLSearchParams(location.search).has('focus')?<Backlog/>:<WorkspaceBacklog/>}/>
      <Route path="/results" element={<WorkspaceResults/>}/><Route path="/executions" element={<ExecutionRuns/>}/><Route path="/executions/:identity" element={<RealExecutionReview/>}/>
      <Route path="/intake" element={<IntakeList/>}/><Route path="/intake/new" element={<IntakeForm/>}/><Route path="/intake/:identity" element={<IntakeDetail/>}/>
      <Route path="/history" element={<History/>}/><Route path="/monitoring" element={<Monitoring/>}/><Route path="/settings" element={<Settings/>}/><Route path="/models/*" element={<Models/>}/>
      <Route path="/records/:source/:recordId" element={<RecordDetail/>}/><Route path="/tasks/:taskId/*" element={<TaskPage/>}/><Route path="*" element={<Notice title="That page has wandered off." text="Return to your dashboard to find the current task."/>}/>
    </Routes></main><footer><span>TwichUI Workbench</span><span>Checked changes stay local. Publishing needs separate permission.</span></footer></div>
  </div>;
}

function Notice({ title, text, retry }: { title: string; text: string; retry?: () => void }) {
  return <section className="notice" role="status"><CircleDot size={28} /><h1>{title}</h1><p>{text}</p>{retry ? <Button variant="secondary" onClick={retry}><RefreshCw size={16} />Try again</Button> : <Button asChild><Link to="/">Back to work</Link></Button>}</section>;
}
function Loading() { return <section className="loading" role="status"><span className="loading-dot" /><p>Opening your work…</p><div className="skeleton" /><div className="skeleton short" /></section>; }
function TaskList() {
  const source = useSource();
  const location = useLocation();
  return <>
    <div className="page-heading"><div><img className="heading-art" src="/art/workshop/overview.png" alt="" /><h1>Work</h1><p>Describe a task, make decisions and review what finished.</p></div></div>
    <IntakeActions /><TaskOverview preview />
    <details className="panel workspace-examples" open={source==='pilot' || new URLSearchParams(location.search).has('source') || undefined}>
      <summary>Workspace records and examples</summary><WorkspaceExamples/>
    </details>
  </>;
}

function WorkspaceExamples() {
  const source = useSource();
  const query = useQuery({ queryKey: ['tasks'], queryFn: () => request<Task[]>('/api/tasks'), refetchInterval: 1500, enabled: source === 'fixture' });
  const fixtureTasks = query.data || [];
  if (source === 'fixture' && query.isPending) return <Loading />;
  if (source === 'fixture' && !query.data) return <Notice title="Your work couldn’t be loaded." text="The review store may be taking a moment. Your saved decisions remain recorded." retry={() => void query.refetch()} />;
  return <>
    {query.isError && <p className="form-error" role="alert">Refresh is unavailable. Showing the last loaded work.</p>}
    <SourcePicker /><WorkSummary reviewTaskId={fixtureTasks.find(task => task.review_available)?.id} />
    {source === 'fixture' && <section className="work-section" id="reviews"><div className="section-heading"><h2>Review queue</h2><span>Contract review</span></div>
      {fixtureTasks.length === 0 ? <Notice title="All clear for now." text="There are no fixture tasks waiting here. New work will appear when it’s available." /> : fixtureTasks.filter(task => task.review_available || task.latest_command).map(task => <div className="task-card" key={task.id}>
        <div className="task-icon"><BookOpen size={26} strokeWidth={1.3} /></div><div className="task-card-content"><span className="subsystem">{task.subsystem || 'Area not recorded'}</span><h2><Link to={`/tasks/${task.id}`}>{task.title}</Link></h2><p>{task.summary || 'No summary was recorded for this task.'}</p><div className="task-meta"><span className={`badge tone-${tone(taskDisplayState(task))}`}><CircleDot size={12} />{taskDisplayState(task)}</span><span><CheckCheck size={15} /> Checks passed</span><span>Not published</span></div></div><Link to={`/tasks/${task.id}/review`} className="review-card-action">{task.review_available ? 'Open review' : 'View saved review'}<ArrowRight size={16}/></Link>
      </div>)}
    </section>}
    <details className="diagnostics"><summary>Workspace limits and evidence</summary><p>{source === 'fixture' ? 'Demo records are simulated. Reviews use the local database and separate worker; saved and applied are separate outcomes.' : 'Maintenance observations are read only. Live review actions are unavailable.'} Nothing here publishes changes or starts implementation.</p></details>
  </>;
}

function TaskPage() {
  const { taskId = '' } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const reviewOpen = location.pathname.endsWith('/review');
  const query = useQuery({ queryKey: taskKey(taskId), queryFn: () => loadTask(taskId), refetchInterval: 1000 });
  const reviewButton = useRef<HTMLAnchorElement>(null);
  if (query.isPending) return <Loading />;
  if (!query.data) return <Notice title="This task couldn’t be opened." text="Check your connection and try again. Any saved review stays in the review store." retry={() => void query.refetch()} />;
  const task = query.data;
  if (location.pathname.endsWith('/verification')) return <Verification task={task}/>;
  return <>
    {query.isError && <p className="form-error" role="alert">Refresh is unavailable. Showing the last loaded task. Your choices are kept.</p>}
    <Link className="back-link" to="/?source=fixture"><ArrowLeft size={15} />All work</Link>
    <div className="page-heading detail-heading"><div><p className="eyebrow">{task.subsystem || 'Area not recorded'}</p><h1>{task.title}</h1><p>{task.summary || 'No summary was recorded for this task.'}</p></div></div>
    <div className="detail-grid"><div className="main-column">
      <section className="panel review-intro"><div className="section-heading"><span className={`badge tone-${tone(taskDisplayState(task))}`}><CircleDot size={12} />{taskDisplayState(task)}</span><span className="revision-label">Review {task.revision}</span></div>
        <h2>{task.state === 'held' ? 'Does this match the way it should work?' : stateLabel[task.state]}</h2>
        <p>{task.state === 'held' ? 'Read the expected behavior, then make a choice for each part. You can leave a note if there’s something we should know.' : task.state === 'review_confirmed' ? 'Your review is recorded and applied. This candidate still needs the normal publication checks.' : 'Your feedback is recorded and applied. The candidate stays held for changes.'}</p>
        <div className="behavior-preview">{task.evidence.claims.map((claim, i) => <div key={claim.id}><span className="step-number">0{i + 1}</span><p>{claim.text}</p></div>)}</div>
        {task.latest_command && <Outcome receipt={task.latest_command} />}
        <div className="review-cta">{task.state==='review_confirmed' && <Button asChild variant="secondary"><Link to={`/tasks/${task.id}/verification`}>Verify fixture candidate</Link></Button>}<Button asChild><Link ref={reviewButton} to={`/tasks/${task.id}/review`}>{task.review_available ? 'Open review' : 'View saved review'}<ArrowRight size={16} /></Link></Button><span>Explicit choices. Optional notes.</span></div>
      </section>
      <section className="panel evidence-panel"><h2><FileCheck2 size={19} />What the checks tell us</h2><p>{task.evidence.checks}</p><div className="soft-callout">{task.evidence.caveat}</div><details><summary>Read the fixture evidence</summary><pre>{task.evidence.excerpt}</pre><p className="small-text">These references belong to this sanitized fixture.</p></details></section>
    </div><aside className="context-column">
      <section className="panel state-panel"><p className="eyebrow">WHERE THINGS STAND</p><dl><div><dt>Task</dt><dd>{taskDisplayState(task)}</dd></div><div><dt>Recorded attempt</dt><dd><Check size={14} />Accepted</dd></div><div><dt>Verification</dt><dd><Check size={14} />Passed</dd></div><div><dt>Publication</dt><dd>Not published</dd></div></dl><p className="state-footnote">{task.eligibility === 'contract_hold' ? 'The contract hold is still in place.' : 'Contract hold released. Other publication checks still apply.'}</p></section>
      <div className="next-note"><ShieldCheck size={23} strokeWidth={1.3} /><h3>A saved choice is the first step.</h3><p>The worker applies it after checking that the evidence still matches. You’ll see the actual outcome here.</p></div>
      <details className="diagnostics"><summary>Technical details</summary><p>Task ID: {task.id}</p><p>Evidence version: {task.version}</p><p>Candidate: {task.evidence.candidate}</p><p>Patch identity: {task.evidence.patch_sha256}</p>{task.latest_command && <p>Command: {task.latest_command.id}</p>}</details>
    </aside></div>
    <Dialog open={reviewOpen} onOpenChange={open => { if (!open) { void navigate(`/tasks/${task.id}`); } }}><DialogContent onCloseAutoFocus={event => { event.preventDefault(); reviewButton.current?.focus(); }}><ReviewForm key={task.id} task={task} /></DialogContent></Dialog>
  </>;
}

export function Outcome({ receipt }: { receipt: Receipt }) {
  const titles = { pending: 'Saved · Pending application', applied: 'Applied', stale: 'Needs another look', failed: 'Could not apply' };
  return <div className={`outcome outcome-${receipt.status}`} role="status"><span>{receipt.status === 'applied' ? <CheckCheck size={21} /> : <Clock3 size={21} />}</span><div><strong>{titles[receipt.status]}</strong><p>{receipt.message}</p>{receipt.status === 'pending' && <p className="small-text">You can leave this page. Your decision is safely recorded.</p>}</div></div>;
}

export function ReviewForm({ task }: { task: Task }) {
  const client = useQueryClient();
  const existing = readDraft(task.id);
  const initialDecision = existing || (!task.review_available ? task.latest_command?.payload : null);
  const initialReceipt = task.latest_command?.id === initialDecision?.command_id ? task.latest_command : null;
  const [choices, setChoices] = useState<Decision['choices']>(initialDecision?.choices || {});
  const [notes, setNotes] = useState(initialDecision?.notes || '');
  const [draft, setDraft] = useState<Decision | null>(existing);
  const [error, setError] = useState('');
  const [conflict, setConflict] = useState(false);
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [reviewVersion, setReviewVersion] = useState(initialDecision?.version || task.version);
  // Keep evidence fixed while a person reviews. A background refresh cannot silently rebind choices.
  const [reviewEvidence, setReviewEvidence] = useState(initialReceipt?.reviewed_evidence || task.evidence);
  const displayedReceipt = task.latest_command?.id === draft?.command_id ? task.latest_command : receipt;
  const latestFinal = task.latest_command && !task.review_available ? task.latest_command : null;
  const visibleReceipt = displayedReceipt || latestFinal;
  const changed = reviewVersion !== task.version;
  const allAnswered = reviewEvidence.claims.every(claim => choices[claim.id]);
  const frozen = Boolean(draft || visibleReceipt || conflict || changed);
  const mutation = useMutation({
    mutationFn: (body: Decision) => saveDecision(task.id, body),
    onSuccess: value => { setReceipt(value); setError(''); void client.invalidateQueries({ queryKey: taskKey(task.id) }); void client.invalidateQueries({ queryKey: ['tasks'] }); void client.invalidateQueries({ queryKey: ['work'] }); },
    onError: err => { setError(err.message); if (err instanceof ApiError && err.status === 409) setConflict(true); },
  });
  useEffect(() => {
    // A lost response is recovered from the canonical task receipt, including after reload.
    if (task.latest_command?.id === draft?.command_id) setError('');
  }, [task.latest_command, draft]);
  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!allAnswered) { setError('Choose an answer for every behavior.'); return; }
    const body: Decision = draft || { command_id: crypto.randomUUID(), version: reviewVersion, choices, notes };
    try { localStorage.setItem(`review:${task.id}`, JSON.stringify(body)); }
    catch { setError('Browser storage is unavailable. Enable it so this decision can be safely retried.'); return; }
    setDraft(body); setError(''); mutation.mutate(body);
  }
  function refreshReview() {
    localStorage.removeItem(`review:${task.id}`);
    setDraft(null); setReceipt(null); setChoices({}); setNotes(''); setError(''); setConflict(false);
    setReviewVersion(task.version); setReviewEvidence(task.evidence);
  }
  return <div className="review-form-wrap">
    <p className="eyebrow">CONTRACT REVIEW</p><DialogTitle className="sheet-title">Review expected behavior</DialogTitle><DialogDescription className="sheet-description">Decide whether each expected behavior feels right. Selecting an answer doesn’t save it.</DialogDescription>
    {visibleReceipt && <Outcome receipt={visibleReceipt} />}
    {(changed || conflict || visibleReceipt?.status === 'stale') && !['pending', 'applied'].includes(visibleReceipt?.status || '') && <div className="stale-callout" role="alert"><strong>The evidence has changed.</strong><p>Your choices have been kept here. Read the refreshed evidence before making a new decision.</p><Button variant="secondary" onClick={refreshReview}>Open refreshed review</Button></div>}
    <form onSubmit={submit}>
      <div className="review-behaviors">{reviewEvidence.claims.map((claim, i) => <fieldset disabled={frozen || mutation.isPending} key={claim.id}><legend><span className="step-number">0{i + 1}</span>{claim.text}</legend><p className="claim-source">{claim.source}</p><div className="choice-grid">{(['confirm', 'change'] as const).map(value => <label key={value} className={`choice ${choices[claim.id] === value ? 'selected' : ''}`}><input type="radio" required name={claim.id} value={value} checked={choices[claim.id] === value} onChange={() => setChoices({ ...choices, [claim.id]: value })} /><span>{value === 'confirm' ? 'Confirm this behavior' : 'Needs changes'}</span></label>)}</div></fieldset>)}</div>
      <label className="notes-label" htmlFor="notes">Anything to add? <span>Optional</span></label><textarea id="notes" rows={3} maxLength={1600} value={notes} disabled={frozen || mutation.isPending} onChange={event => setNotes(event.target.value)} placeholder="A detail to clarify, or how you’d expect it to behave…" /><div className="notes-count">{notes.length} / 1600</div>
      {error && !visibleReceipt && <div className="form-error" role="alert">{error}</div>}
      {visibleReceipt?.status === 'applied' ? <p className="submit-help">Your review is complete. You can close this panel.</p> : visibleReceipt?.status === 'pending' ? <p className="submit-help">Waiting for the separate worker. This view updates automatically.</p> : !changed && !conflict && !visibleReceipt && <div className="form-footer"><p>{Object.values(choices).includes('change') ? 'Requesting changes keeps the candidate held. It does not start a new attempt.' : 'Confirming every behavior releases only the contract hold. Publication remains separate.'}</p><Button type="submit" disabled={!allAnswered || mutation.isPending}>{mutation.isPending ? 'Saving…' : draft ? 'Retry same decision' : 'Save review decisions'}<ArrowRight size={16} /></Button><p className="submit-help">Saved first. Applied after revalidation.</p></div>}
    </form>
    <details className="review-evidence"><summary>Evidence you’re reviewing</summary><p>{reviewEvidence.checks}</p><pre>{reviewEvidence.excerpt}</pre><p className="small-text">Version {reviewVersion}</p></details>
  </div>;
}
