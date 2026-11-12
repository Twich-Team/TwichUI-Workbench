"""Feature views over existing intake, plan and execution owners. No new task store."""
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from workbench import intake, task_execution, feature_recovery
from workbench.db import intakes, proposals, planning_jobs, proposal_decisions, task_sessions
from workbench.schemas import (IntakeView, PlannedTaskView, FeatureBudget,
    FeatureAttachmentView, FeatureReferenceEvidence)
from workbench.feature_recovery import CorrectionOffer
from workbench.feature_scope_recovery import FeatureScopeCorrectionOffer as ScopeCorrectionOffer
from workbench.feature_run_recovery import RunRecoveryOffer
from workbench.service import now

FeatureState = Literal['understanding', 'planning', 'needs_clarification', 'ready', 'working',
                       'checking', 'needs_owner_action', 'held', 'failed', 'ready_to_review', 'completed']


class FeatureStep(BaseModel):
    task: PlannedTaskView
    state: str
    message: str
    verified: bool = False
    execution: task_execution.TaskExecutionContext


class FeatureRunOffer(BaseModel):
    binding: str
    budget: FeatureBudget
    step_count: int
    maximum_generated_tokens: int
    provider: str
    model: str


class FeatureRunAvailability(BaseModel):
    offer: FeatureRunOffer | None = None
    message: str


class FeatureRunView(BaseModel):
    id: str
    status: str
    phase: str
    message: str
    can_stop: bool
    cancel_requested: bool = False
    expires_at: str
    continuation_expires_at: str | None = None


class FeatureResult(BaseModel):
    execution_id: str
    delivery_task_id: str
    published: bool
    draft_url: str | None = None
    message: str


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
    correction: CorrectionOffer | None = None
    run: FeatureRunView | None = None
    feature_result: FeatureResult | None = None
    request_attachments: list[FeatureAttachmentView] = []
    reference_addons: list[FeatureReferenceEvidence] = []
    reference_notice: str | None = None
    scope_correction: ScopeCorrectionOffer | None = None
    run_recovery: RunRecoveryOffer | None = None


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
    actual_activity = bool(active_step or running)
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
    parent = c.execute(select(proposal_decisions).where(proposal_decisions.c.intake_id == identity,
        proposal_decisions.c.proposal_id == latest,
        proposal_decisions.c.payload['feature_run'].as_string().is_not(None))).mappings().first()
    run = None
    feature_result = None
    integrated = 'not_recorded'
    if parent:
        journal = c.execute(select(planning_jobs).where(planning_jobs.c.kind == 'feature_execution',
            planning_jobs.c.payload['feature_decision_id'].as_string() == parent['id'])).mappings().first()
        session = c.execute(select(task_sessions).where(task_sessions.c.id == parent['id'])).mappings().first()
        stopped = bool(session and session['state'].get('cancel_requested') or
            journal and journal['payload'].get('feature_stop_command'))
        from workbench.task_sessions import ACTIVE
        run = dict(id=parent['id'], status=session['status'] if session else parent['status'],
            phase=journal['payload'].get('phase','pending') if journal else 'held',
            message=(session or journal or parent)['message'], cancel_requested=stopped,
            can_stop=not stopped and (parent['status'] == 'pending' or bool(session and session['status'] in ACTIVE | {'held','failed'})),
            expires_at=parent['payload']['feature_run']['expires_at'])
        if journal and journal['payload'].get('execution_windows'):
            from workbench.feature_run_recovery import deadline
            run['continuation_expires_at'] = deadline(c,parent,parent['payload']['feature_run'])
        state, message, needs_owner = 'held', run['message'], False
        if parent['status'] == 'pending':
            message = 'Your feature run decision is saved and waiting to apply.'
        elif active_step:
            state = 'checking' if active_step['state'] == 'checking' else 'working'
        elif running:
            state = 'working'
        elif session and session['status'] in ACTIVE and not stopped:
            state = 'ready'
        current_scope = journal['payload'].get('current_step_scope') if journal else None
        if journal and journal['status'] == 'running' and run['phase'] in {'preparing_runtime','checking_combined_feature'}:
            from workbench.real_execution import alive
            owner = (journal['payload'].get('feature_preparation_claim' if run['phase']=='preparing_runtime' else 'feature_verification_claim') or {}).get('owner')
            if owner and alive(owner):
                actual_activity, state = True, 'checking'
        if not active_step and current_scope and not stopped:
            active_step = next((s for s in steps if s['task']['id'] == current_scope['task_id']), None)
            next_step = next((s for s in steps if not s['verified'] and s is not active_step), None)
        if journal and journal['payload'].get('completed_checks'):
            try:
                from workbench.feature_verification import satisfied_tasks
                checked = satisfied_tasks(c,parent['id'],journal['payload']['completed_steps'][-1]['input'])
                integrated = 'passed_offline' if checked else 'not_recorded'
                from workbench.feature_delivery import view_in as result_view
                feature_result = result_view(c,journal)
                if feature_result:
                    state,message,needs_owner = 'ready_to_review',feature_result['message'],False
                    delivered = next((s for s in steps if s['task']['id']==feature_result['delivery_task_id']),None)
                    if delivered:
                        delivered.update(state='ready_to_review',message=feature_result['message'])
                    if feature_result['published']:
                        if delivered:
                            delivered.update(state='verified',verified=True,message='The one coherent feature draft PR was published and verified.')
            except (ValueError,OSError,HTTPException,KeyError,TypeError):
                integrated = 'needs_review'
                for prior in journal['payload'].get('completed_checks',[]):
                    step = next((s for s in steps if s['task']['id']==prior['task_id']),None)
                    if step:
                        step.update(state='held',verified=False,message='The combined verification evidence needs review. Earlier records remain saved.')
    correction = feature_recovery.offer(c, row)
    if correction:
        message = 'The plan is saved, but its proposed steps did not pass the checks. Prepare corrected steps to continue.'
    request_attachments, reference_addons, reference_notice = [], [], None
    original = row['payload']
    if original.get('feature_attachments') or original.get('feature_references'):
        try:
            from workbench.feature_context import validate
            from workbench import feature_attachments, feature_references
            validate(c.engine, original)
            request_attachments = feature_attachments.validate(c.engine,identity,original.get('feature_attachments') or [])
            if original.get('feature_references'):
                reference_addons = feature_references.evidence(c.engine,original['feature_references'].get('source_request_id') or identity,original['feature_references']['items'])
        except (ValueError, OSError, KeyError, TypeError):
            reference_notice = 'Some original reference evidence is unavailable. Its saved request bindings remain; no new lookup or model call was started.'
    scope_correction = None
    if run and run['status']=='held':
        from workbench.feature_scope_recovery import offer as scope_offer
        offered=scope_offer(c,identity)
        if offered:
            scope_correction={k:v for k,v in offered.items() if k!='approved_inputs'}
            needs_owner=True
            message='The next step needs more preparation. Your feature and completed work are saved.'
    from workbench.feature_run_recovery import offer as run_recovery_offer
    recovery = run_recovery_offer(c,identity) if run and run['status']=='held' else None
    if recovery:
        needs_owner=True
        message='The first coding request never started. Continue the saved feature in a new bounded window.'
    return dict(id=identity, title=row['payload']['title'], objective=row['payload']['description'],
        state=state, message=message, active=actual_activity, needs_owner=needs_owner,
        completed_steps=sum(s['verified'] for s in steps), total_steps=len(steps),
        current_step=active_step['task']['id'] if active_step else None,
        next_step=next_step['task']['id'] if next_step else None,
        intake=data, steps=steps, observed_at=now(),
        correction=correction, run=run, feature_result=feature_result, integrated_verification=integrated,
        request_attachments=request_attachments,reference_addons=reference_addons,reference_notice=reference_notice,
        scope_correction=scope_correction,
        run_recovery={k:v for k,v in recovery.items() if k != 'approved_inputs'} if recovery else None)


def run_offer(engine, identity):
    from workbench import feature_permission
    with engine.connect() as c:
        row = intake.require(c, intakes, identity)
        if not is_feature(row['payload']):
            raise HTTPException(404, 'Feature not found.')
    try:
        selected = feature_permission.offer(engine, identity)
        return dict(offer=dict(binding=selected['binding'], budget=selected['budget'],
            step_count=selected['step_count'], maximum_generated_tokens=selected['maximum_generated_tokens'],
            provider=selected['scope']['provider'], model=selected['scope']['route']['model']),
            message='Build the reviewed steps in order using the saved AI. Checked work stays with this feature.')
    except (ValueError, OSError, HTTPException):
        return dict(offer=None, message='The feature has no available run decision. Its plan and saved work remain available.')


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
