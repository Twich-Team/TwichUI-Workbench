"""Everyday task index. Reads existing owners; never starts work or grants permission."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel
from pydantic import Field
from sqlalchemy import select

from workbench.db import intakes, planning_jobs, proposals, proposal_decisions, backlog_items, model_attempts
from workbench import task_execution, task_budget
from workbench.planning_provider import configured_mode
from workbench.result_rejection import later_review
from workbench.schemas import LocalModelPriorityView

State = Literal['queued', 'understanding', 'preparing', 'needs_input', 'needs_approval', 'decision_pending',
                'working', 'checking', 'cleanup', 'held', 'failed', 'interrupted', 'stopped',
                'completed', 'unavailable', 'paused']
# Meaning comes from a recorded state, never from model-generated status prose.
LABELS = {
    'queued': ('Queued', 'Your request is saved and waiting to start.'),
    'understanding': ('Understanding the request', 'Workbench is reading relevant code and investigating.'),
    'preparing': ('Preparing the reviewed work', 'Workbench is preparing a protected copy and checking the approved limits. Implementation has not started.'),
    'needs_input': ('Needs your input', 'Answer the question on this task to continue.'),
    'needs_approval': ('Needs approval', 'Review the proposed next step before work starts.'),
    'decision_pending': ('Decision recorded', 'Your choice is saved and waiting to apply.'),
    'working': ('Working on the change', 'The agent is working in an isolated copy of the addon.'),
    'checking': ('Checking the change', 'Independent checks are running before a result is ready.'),
    'cleanup': ('Finishing safely', 'Checks passed; Workbench is confirming cleanup.'),
    'held': ('On hold', 'Open the task for the reason and available next step.'),
    'failed': ('Could not finish', 'Your request and diagnostic evidence have been saved.'),
    'interrupted': ('Waiting for recovery', 'The previous operation must settle before work can continue.'),
    'stopped': ('Stopped', 'Saved work remains available. Nothing was undone or published.'),
    'completed': ('Ready to review', 'Open the result to see the outcome, checks and limitations.'),
    'unavailable': ('Saved for later', 'This kind of work is not executable in this release.'),
    'paused': ('Paused', 'No new work is starting for this task.'),
}
ACTIVE = {'understanding', 'preparing', 'working', 'checking', 'cleanup'}
NEEDS = {'needs_input', 'needs_approval'}


class WorkspaceTask(BaseModel):
    id: str
    title: str
    kind: str
    state: State
    stage: str
    message: str
    created_at: str
    updated_at: str
    active: bool
    needs_you: bool
    result_id: str | None = None
    result_kind: Literal['patch', 'tests', 'findings', 'plan'] | None = None
    provider: str | None = None


class WorkspacePage(BaseModel):
    local_model: LocalModelPriorityView | None = Field(default=None,exclude_if=lambda value:value is None)
    items: list[WorkspaceTask]
    total: int
    all_total: int
    page: int
    page_size: int
    counts: dict[str, int]
    observed_at: str
    mode: str
    highlights: dict[str, list[WorkspaceTask]]


def indexed(rows, key='intake_id'):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row[key]].append(dict(row))
    return grouped


def listing(engine, *, page=1, search='', state='all', page_size=12):
    """Counts and rows share one filter scope. Requests, not provider attempts, count."""
    with engine.connect() as c:
        c.exec_driver_sql('BEGIN')
        originals = [dict(r) for r in c.execute(select(intakes).order_by(intakes.c.created_at.desc(), intakes.c.id)).mappings()]
        jobs = indexed(c.execute(select(planning_jobs).order_by(planning_jobs.c.created_at)).mappings())
        plans = indexed(c.execute(select(proposals).order_by(proposals.c.revision)).mappings())
        choices = indexed(c.execute(select(proposal_decisions).order_by(proposal_decisions.c.created_at)).mappings())
        tasks = indexed(c.execute(select(backlog_items).where(backlog_items.c.kind == 'task')).mappings())
        providers = indexed(c.execute(select(model_attempts).where(model_attempts.c.purpose.in_(['planning', 'implementation']))
                                      .order_by(model_attempts.c.started_at)).mappings(), 'task_id')
        from workbench.local_model_priority import snapshot as model_priority
        local_model = model_priority(c)
    rows = []
    for original in originals:
        preparation_message = None
        fresh_run_decision = False
        identity, body = original['id'], original['payload']
        current_plans, current_jobs = plans[identity], jobs[identity]
        plan = current_plans[-1] if current_plans else None
        current_choices = [d for d in choices[identity] if plan and d['proposal_id'] == plan['id']]
        phase = {'queued': 'queued', 'investigating': 'understanding', 'waiting_for_information': 'needs_input',
                 'failed': 'failed', 'decomposition_failed': 'failed', 'declined': 'stopped',
                 'scope_review_required': 'needs_approval', 'unavailable': 'unavailable', 'paused': 'paused'}.get(original['status'], 'held')
        result_id, result_kind, expired_permission = None, None, False
        if original['status'] == 'completed':
            phase = 'needs_approval' if plan else 'completed'
            result_kind = None if plan else 'findings'
        if original['status'] == 'approved':
            phase = 'understanding' if any(j['status']=='running' for j in current_jobs if j['kind']!='planning') else 'queued'
        if original['status'] == 'failed' and any(j['payload'].get('diagnostics', {}).get('category') in
                {'interrupted_unknown', 'canceled', 'cancelled'} for j in current_jobs):
            phase = 'interrupted'
        if phase == 'understanding':
            active_jobs = [j for j in current_jobs if j['status']=='running']
            if not active_jobs:
                phase = 'queued' if any(j['status']=='queued' for j in current_jobs) else 'held'
            elif all(j['lease_until'] and j['lease_until'] < datetime.now(timezone.utc).isoformat() for j in active_jobs):
                phase = 'interrupted'  # An expired claim does not prove current activity or completion.
        if original['status'] == 'tasks_ready':
            eligible = [t for t in tasks[identity] if plan and t['proposal_id']==plan['id']]
            if not (body.get('local_planning') or body.get('regression_tests') or body.get('supported_task')):
                phase, result_kind = 'held', 'plan'
            elif len(eligible)==1:
                context = task_execution.context(engine, identity, eligible[0]['id'])
                relevant = next((r for r in context['runs'] if r['current_task']), None)
                phase = {'running':'working', 'checking':'checking', 'checked':'completed', 'completed':'completed',
                         'preparing':'preparing', 'pending':'queued', 'settling':'cleanup', 'recovering':'interrupted',
                         'held':'held', 'failed':'failed', 'blocked':'held', 'cancelled':'stopped',
                         'cancel_requested':'cleanup', 'interrupted':'interrupted'}.get(context['stage'], 'held')
                if context['stage']=='waiting' and not context['run_budget'] and context['current_approval']:
                    offer = task_budget.run_context(engine, identity, eligible[0]['id'])
                    if offer.get('preparation_resource'):
                        preparation_message = offer['message']
                    phase = 'needs_approval' if offer.get('available') else ('preparing' if any(j['status']=='running' for j in current_jobs) else 'queued') if offer.get('preparing') else 'held'
                if context['runs'] and not relevant:
                    phase = 'held'  # An earlier checked run is not completion of the current plan.
                if context['stage']=='preparing' and (context.get('session') or {}).get('status')=='pending':
                    phase = 'queued'
                if context['stage']=='failed':
                    renewal = task_budget.run_context(engine, identity, eligible[0]['id'])
                    cycle = (renewal.get('offer') or {}).get('repair_cycle') or {}
                    if renewal.get('available') and not renewal.get('receipt') and cycle.get('continuation'):
                        phase = 'needs_approval'
                        fresh_run_decision = True
                if relevant:
                    if relevant.get('candidate_id') and phase not in ACTIVE | {'queued'}:
                        result_id = relevant['id']
                        result_kind = 'tests' if body.get('regression_tests') else 'patch'
                    if phase == 'completed' and relevant.get('verification') != 'passed':
                        phase = 'failed'
                    with engine.connect() as c:
                        if later_review(c.connection.driver_connection, relevant['id']):
                            phase = 'failed'
                elif context['run_budget'] and context['stage']=='waiting':
                    phase = 'queued' if context['run_budget'].get('current') else 'held'
                expired_permission = bool(context['run_budget'] and not context['run_budget'].get('current')
                                          and phase in {'held', 'queued'})
        if original['status'] in {'completed', 'approved', 'tasks_ready', 'scope_review_required'} and any(d['status']=='pending' for d in current_choices):
            phase = 'decision_pending'
        timestamps = [original['created_at'], *[j['finished_at'] or j['created_at'] for j in current_jobs],
                      *[d['finished_at'] or d['created_at'] for d in current_choices],
                      *[p['finished_at'] or p['started_at'] for p in providers[identity]]]
        kind = body.get('requested_work') or ('coverage' if body.get('regression_tests') else 'bug' if body['kind']=='issue' else 'feature')
        label, message = LABELS[phase]
        if fresh_run_decision:
            label, message = 'Ready for a new run decision', 'The previous run ended. Review the saved work and limits before allowing another attempt.'
        if preparation_message:
            label, message = 'Waiting for storage' if phase=='queued' else 'Preparation needs checking', preparation_message
        if (configured_mode()=='everyday' and body['kind']=='issue' and not body.get('local_planning')
                and not body.get('supported_task') and original['status'] in {'failed','waiting_for_information'}):
            phase, label, message = 'needs_approval', 'Ready for investigation', 'Your report is saved. Choose a provider and allow read-only investigation; no repair is approved.'
        if expired_permission:
            message = 'The earlier run permission expired. Open the task to review the next step.'
        if phase == 'queued':
            waiting_job = max((j for j in current_jobs if j['status']=='queued'),
                              key=lambda j:j['revision'],default=None)
            if local_model and identity in local_model['intake_ids']:
                label = 'Making Qwen available' if local_model['state']=='yielding' else 'Waiting for local Qwen'
                message = ('Waiting for local Qwen. It is working on another Workbench request; this saved revision remains scheduled.'
                           if local_model['state']=='working' else local_model['message'])
            elif waiting_job and waiting_job['message'].startswith('Waiting for '):
                message = waiting_job['message']
        rows.append(dict(id=identity, title=body['title'], kind=kind, state=phase, stage=label, message=message,
                         created_at=original['created_at'], updated_at=max(timestamps), active=phase in ACTIVE,
                         needs_you=phase in NEEDS or expired_permission, result_id=result_id, result_kind=result_kind,
                         provider=providers[identity][-1]['backend'] if providers[identity] else None))
    query = search.strip().casefold()
    matching = [r for r in rows if not query or query in (r['title']+' '+r['stage']+' '+r['kind']).casefold()]
    counts = Counter(r['state'] for r in matching)
    counts.update(all=len(matching), active=sum(r['active'] for r in matching), needs_you=sum(r['needs_you'] for r in matching),
                  results=sum(bool(r['result_id']) or r['result_kind']=='findings' for r in matching))
    allowed = {'all', 'active', 'needs_you', 'results', *LABELS}
    if state not in allowed:
        raise HTTPException(422, 'Choose an available task filter.')
    def included(row):
        if state=='all':
            return True
        if state=='active':
            return row['active']
        if state=='needs_you':
            return row['needs_you']
        if state=='results':
            return bool(row['result_id']) or row['result_kind']=='findings'
        return row['state']==state
    selected = [r for r in matching if included(r)]
    return dict(**({'local_model':local_model} if local_model else {}),items=selected[(page-1)*page_size:page*page_size], total=len(selected), all_total=len(rows),
                page=page, page_size=page_size, counts=dict(counts), mode=configured_mode(),
                highlights=dict(active=[r for r in matching if r['active']][:2],
                                waiting=[r for r in matching if r['state']=='queued'][:3],
                                needs_you=[r for r in matching if r['needs_you']][:3],
                                completed=[r for r in matching if r['state']=='completed'][:3],
                                outcomes=[r for r in matching if r['state'] in {'completed', 'failed', 'stopped'}][:3]),
                observed_at=datetime.now(timezone.utc).isoformat())
