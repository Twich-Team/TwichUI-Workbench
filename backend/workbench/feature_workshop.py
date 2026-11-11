"""Feature views over existing intake, plan and execution owners. No new task store."""
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from workbench import intake, task_execution
from workbench.db import intakes, proposals, planning_jobs
from workbench.schemas import IntakeView, PlannedTaskView
from workbench.service import now

FeatureState = Literal['understanding', 'planning', 'needs_clarification', 'ready', 'working',
                       'checking', 'needs_owner_action', 'held', 'failed', 'ready_to_review', 'completed']


class FeatureStep(BaseModel):
    task: PlannedTaskView
    state: str
    message: str
    verified: bool = False
    execution: task_execution.TaskExecutionContext


class FeatureView(BaseModel):
    id: str
    title: str
    objective: str
    state: FeatureState
    message: str
    active: bool = False
    needs_owner: bool = False
    completed_steps: int = 0
    total_steps: int = 0
    current_step: str | None = None
    next_step: str | None = None
    integrated_verification: str = 'not_recorded'
    intake: IntakeView
    steps: list[FeatureStep]
    observed_at: str


class FeaturePage(BaseModel):
    items: list[FeatureView]
    total: int
    page: int
    page_size: int = 12


def is_feature(payload):
    return (payload.get('kind') == 'feature' and not payload.get('regression_tests')
            and not payload.get('supported_task')
            and payload.get('requested_work') in (None, 'feature'))


def step_state(execution):
    """Only a matching checked result counts; old or uncertain results cannot advance progress."""
    stage = execution['stage']
    runs = [run for run in execution['runs'] if run['current_task']]
    verified = any(run['status'] == 'checked' and run['verification'] == 'passed'
                   and run['candidate_id'] and not run['recovery_pending'] for run in runs)
    if verified and stage not in {'recovering', 'settling'}:
        return 'verified', True
    return {'running': 'working', 'checking': 'checking', 'preparing': 'preparing',
            'pending': 'queued', 'failed': 'failed', 'interrupted': 'held',
            'cancelled': 'held', 'cancel_requested': 'held', 'recovering': 'held',
            'settling': 'checking'}.get(stage, 'upcoming'), False


def view_in(c, identity):
    row = intake.require(c, intakes, identity)
    if not is_feature(row['payload']):
        raise HTTPException(404, 'This request is not a Feature Workshop feature.')
    data = intake.view_in(c, identity)
    latest = c.execute(select(proposals.c.id).where(proposals.c.intake_id == identity,
        proposals.c.revision == row['revision'])).scalar()
    tasks = [t for t in data['tasks'] if t['kind'] == 'task' and t['proposal_id'] == latest]
    # Deterministic topological presentation; organization remains separate from dependencies.
    ordered, remaining = [], sorted(tasks, key=lambda t: t['id'])
    while remaining:
        ready = [t for t in remaining if not any(d in {r['id'] for r in remaining} for d in t['dependencies'])]
        if not ready:
            ready = remaining[:]  # Corrupt historical plans remain inspectable; never eligible.
        ordered.extend(ready)
        remaining = [t for t in remaining if t not in ready]
    steps = []
    for task in ordered:
        execution = task_execution.context_in(c, identity, task['id'])
        state, verified = step_state(execution)
        from workbench.result_rejection import later_review
        reviews = [later_review(c.connection.driver_connection, r['id'])
                   for r in execution['runs'] if r['current_task'] and r['status'] == 'checked']
        review = next((r for r in reviews if r), None)
        if review:
            state, verified = 'held', False
            execution = {**execution, 'message':review['reason']}
        steps.append(dict(task=task, state=state, verified=verified,
                          message=execution['message'], execution=execution))
    status = data['status']
    jobs = [j for j in data['jobs'] if j['revision'] == row['revision'] or
            j['revision'] == row['revision'] + 1]
    live_leases = set(c.execute(select(planning_jobs.c.id).where(
        planning_jobs.c.intake_id == identity, planning_jobs.c.status == 'running',
        planning_jobs.c.lease_until > now())).scalars())
    running = next((j for j in jobs if j['id'] in live_leases), None)
    pending = next((d for d in data['decisions'] if d['status'] == 'pending'), None)
    active_step = next((s for s in steps if s['state'] in {'working', 'checking', 'preparing'}), None)
    next_step = next((s for s in steps if not s['verified'] and s is not active_step), None)
    state, message, needs_owner = 'held', 'This feature is saved. Implementation has not started.', False
    if status == 'waiting_for_information':
        state, message, needs_owner = 'needs_clarification', (data.get('result') or {}).get('clarification') or 'More information is needed to understand this feature.', True
    elif status in {'failed', 'decomposition_failed'}:
        state, message = 'failed', next((j['message'] for j in reversed(jobs) if j['status'] == 'failed'), 'This feature could not finish. Saved work is preserved.')
    elif status == 'scope_review_required':
        state, message, needs_owner = 'needs_owner_action', 'The feature scope changed. Review the current plan.', True
    elif status == 'declined':
        message = 'This feature was declined. Its original request and saved plan remain available.'
    elif pending:
        message = 'Your decision is saved and waiting to apply.'
    elif active_step:
        state = 'checking' if active_step['state'] == 'checking' else 'working'
        message = active_step['message']
    elif running:
        state = 'planning' if running['kind'] == 'decomposition' else 'understanding'
        message = running['message']
    elif status == 'completed' and latest:
        state, message, needs_owner = 'needs_owner_action', 'A feature plan is ready for review.', True
    elif steps:
        failed = next((s for s in steps if s['state'] == 'failed'), None)
        state = 'failed' if failed else 'held'
        message = (failed or next_step or steps[-1])['message']
        if all(s['verified'] for s in steps):
            message = 'All steps have checked results. Combined feature verification is still required.'
    elif status in {'queued', 'approved'}:
        state, message = 'planning', 'Feature planning is queued. No model is currently recorded as working.'
        waiting = next((j for j in reversed(jobs) if j['status']=='queued' and j['message'].startswith('Waiting for ')), None)
        if waiting:
            state, message = 'held', waiting['message']
    return dict(id=identity, title=row['payload']['title'], objective=row['payload']['description'],
        state=state, message=message, active=bool(active_step or running), needs_owner=needs_owner,
        completed_steps=sum(s['verified'] for s in steps), total_steps=len(steps),
        current_step=active_step['task']['id'] if active_step else None,
        next_step=next_step['task']['id'] if next_step else None,
        intake=data, steps=steps, observed_at=now())


def view(engine, identity):
    with engine.connect() as c:
        c.exec_driver_sql('BEGIN')
        return view_in(c, identity)


def listing(engine, page=1, archived=False):
    from workbench.task_cleanup import lifecycle
    from workbench.task_identity import links

    with engine.connect() as c:
        c.exec_driver_sql('BEGIN')
        owners = {r['intake_id']: r['canonical_id'] for r in links(c)}
        organization = lifecycle(c)
        current = {}
        for row in c.execute(select(intakes).order_by(intakes.c.created_at.desc(), intakes.c.id)).mappings():
            root = owners.get(row['id'], row['id'])
            if is_feature(row['payload']) and bool((organization.get(root) or {}).get('archived_at')) == archived:
                current.setdefault(root, row['id'])
        ids = list(current.values())
        return dict(items=[view_in(c, i) for i in ids[(page-1)*12:page*12]], total=len(ids), page=page)
