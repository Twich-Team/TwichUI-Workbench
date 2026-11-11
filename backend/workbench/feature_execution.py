"""Finite parent feature work inside the existing worker and planning journal.

Admission alone dispatches nothing. Existing task sessions, grants, provider
controllers and frozen checks remain the owners of eventual implementation.
"""
from fastapi import HTTPException
from sqlalchemy import select
import json
from pathlib import Path

from workbench import intake, feature_permission, everyday
from workbench.db import proposal_decisions, planning_jobs, write_transaction


def apply_one(engine):
    try:
        feature_permission.capability(engine)
    except (ValueError, OSError):
        return False
    with engine.connect() as c:
        row = c.execute(select(proposal_decisions).where(proposal_decisions.c.status=='pending',
            proposal_decisions.c.payload['feature_run'].as_string().is_not(None))
            .order_by(proposal_decisions.c.created_at,proposal_decisions.c.id).limit(1)).mappings().first()
        if not row:
            return False
        identity = row['id']
        failure = None
        try:
            permission = feature_permission.validate(c,row,pending=True)
            from workbench.project_source import require_current
            require_current({'resolved_base':permission['scope']['feature_base']})
        except (ValueError, OSError, HTTPException, KeyError, TypeError):
            failure = 'This feature permission or source changed before admission. No implementation started.'
    with write_transaction(engine) as c:
        row = intake.require(c,proposal_decisions,identity)
        if row['status']!='pending':
            return False
        try:
            feature_permission.capability(engine)
        except (ValueError, OSError):
            return False  # Temporary withdrawal holds the command; never consumes it.
        if not failure:
            try:
                feature_permission.validate(c,row,pending=True)
            except (ValueError, OSError, HTTPException, KeyError, TypeError):
                failure = 'The feature changed while its run decision was being admitted. No implementation started.'
        job = c.execute(select(planning_jobs).where(planning_jobs.c.intake_id==row['intake_id'],
            planning_jobs.c.kind=='feature_execution',
            planning_jobs.c.payload['feature_decision_id'].as_string()==identity)).mappings().first()
        if not job or job['payload'].get('feature_decision_id')!=identity or job['status']!='queued':
            failure = 'The original feature journal is unavailable or changed. No implementation started.'
        c.execute(proposal_decisions.update().where(proposal_decisions.c.id==identity).values(
            status='failed' if failure else 'applied',finished_at=intake.now(),
            message=failure or 'Parent feature permission applied. Each step still requires its exact protected runtime and independent checks.'))
        if not failure:
            from workbench.feature_sessions import admit
            actual = intake.require(c, proposal_decisions, identity)
            admit(c, actual, permission)
        if job and job['status']=='queued':
            c.execute(planning_jobs.update().where(planning_jobs.c.id==job['id']).values(
                status='failed' if failure else 'queued',finished_at=intake.now() if failure else None,
                payload={**job['payload'],'parent_permission_pending':False,
                    'phase':'held' if failure else 'prepare_scopes'},
                message=failure or 'Feature admitted. Implementation details and protected execution have not started.'))
    return True


def _hold(engine, job, message):
    with write_transaction(engine) as c:
        current = intake.require(c, planning_jobs, job['id'])
        if current['status'] != job['status'] or current['payload'] != job['payload']:
            return False
        c.execute(planning_jobs.update().where(planning_jobs.c.id == job['id']).values(
            status='failed', finished_at=intake.now(), payload={**current['payload'],'phase':'held'},
            message=message[:1000]))
        from workbench.feature_sessions import hold_if_idle
        hold_if_idle(c, current['payload']['feature_decision_id'], message)
    return True


def _prepared(engine, job, *, provision=None, stop_event=None):
    """Claim before checks; interrupted checks reconcile evidence without replay."""
    from workbench import feature_steps, trusted_preparation, task_session, real_execution, everyday
    from workbench.real_workspace import Runtime, durable
    from workbench.visual_scope import digest
    if everyday.enabled():
        everyday.require_room(everyday.configuration(engine))
    saved = job['payload']['current_step_scope']
    policy = trusted_preparation.configuration(engine)
    with engine.connect() as c:
        spec, oracle = feature_steps.recipe(c, saved, policy['identity'])
    recovering = job['status'] == 'running'
    if recovering:
        claim = job['payload']['feature_preparation_claim']
        if real_execution.alive(claim['owner']):
            return False
        root = Path(claim['root'])
        if not (root/'preparation-completed.json').is_file():
            return _hold(engine, job, 'Step readiness was interrupted. Partial evidence remains; no model or duplicate checks will start without recovery.')
    else:
        root = Path(policy['output_root'])/'feature-steps'/saved['decision_id']/digest(saved['task_id'])[:24]/'attempt-01'
        if root.exists():
            return _hold(engine, job, 'Step preparation output exists without its original journal claim. Retained evidence needs recovery; nothing was overwritten.')
        claim = dict(root=str(root), owner=real_execution.owner_identity(),
            scope_binding=saved['scope_binding'], policy_binding=digest(policy))
        with write_transaction(engine) as c:
            feature_steps.validate_scope(c, saved)
            current = intake.require(c, planning_jobs, job['id'])
            if current['status'] != 'queued' or current['payload'] != job['payload']:
                return False
            c.execute(planning_jobs.update().where(planning_jobs.c.id == job['id']).values(
                status='running', payload={**current['payload'], 'phase':'preparing_runtime',
                    'feature_preparation_claim':claim},
                message='Checking the original baseline and isolated boundary for this step. No model is implementing yet.'))
        root.mkdir(parents=True, mode=0o700)
        root.chmod(0o700)
        durable(root/'recipe.json', json.dumps(spec,sort_keys=True).encode())
        durable(root/'recipe-oracle.lua', oracle.encode())
        runtime = Runtime(root, stop_event=stop_event)
        (provision or task_session.provision)(runtime, dict(control_patches={}))
    runtime = Runtime(root)
    if (claim['scope_binding'] != saved['scope_binding'] or claim['policy_binding'] != digest(policy)
            or runtime.recipe != spec):
        raise ValueError('Feature preparation policy, source or scope changed.')
    offered = feature_steps.register_offer(engine, runtime)
    with write_transaction(engine) as c:
        current = intake.require(c, planning_jobs, job['id'])
        feature_steps.validate_scope(c, saved)
        if current['status'] != 'running' or current['payload'].get('feature_preparation_claim') != claim:
            return False
        c.execute(planning_jobs.update().where(planning_jobs.c.id == job['id']).values(
            status='queued', payload={**current['payload'], 'phase':'queue_step', 'current_offer_id':offered['id']},
            message='Step readiness passed. Waiting for its existing protected implementation session.'))
    return True


def advance(engine, *, adapter=None, provision=None, stop_event=None):
    """Finite parent progress; the existing worker/session/controller own execution."""
    from workbench import feature_steps, feature_source, feature_preparation, real_execution
    from workbench.db import task_run_offers, task_sessions, real_execution_requests, real_execution_grants
    if stop_event is not None and stop_event.is_set():
        return False
    try:
        feature_permission.capability(engine)
    except (ValueError, OSError):
        return False
    if feature_preparation.recover_one(engine):
        return True
    with engine.connect() as c:
        row = c.execute(select(planning_jobs).where(planning_jobs.c.kind == 'feature_execution',
            planning_jobs.c.status.in_(['queued','running'])).order_by(planning_jobs.c.created_at).limit(1)).mappings().first()
        if not row or row['payload'].get('parent_permission_pending'):
            return False
        job = dict(row)
        if job['status'] == 'running' and job['payload'].get('phase') != 'preparing_runtime':
            return False
    try:
        with engine.connect() as c:
            approval, permission = feature_steps.parent(c, job['payload']['feature_decision_id'])
            if (job['intake_id'] != approval['intake_id'] or job['revision'] != permission['scope']['revision']
                    or job['payload']['graph_binding'] != intake.digest(permission['scope']['graph'])):
                raise ValueError('Feature journal differs from the approved graph.')
        phase = job['payload'].get('phase')
        if phase == 'prepare_scopes':
            completed = job['payload'].get('completed_steps', [])
            done = {item['task_id'] for item in completed}
            if completed:
                with engine.connect() as c:
                    from workbench import task_execution, trusted_preparation
                    linked = task_execution.snapshot(c, approval['intake_id'], completed[-1]['task_id'], approval['id'])
                    carried = feature_source.prerequisite_tasks(c, completed[-1]['input'], linked,
                        trusted_preparation.configuration(engine))
                    if carried != done:
                        raise ValueError('Feature progress differs from its actual checked source ancestry.')
            candidates = [t for t in permission['scope']['graph'] if t['task_id'] not in done and set(t['dependencies']) <= done]
            if not candidates:
                return _hold(engine, job, 'Implementation steps are checked. Combined feature verification is still required; the parent feature is not complete.')
            task = sorted(candidates,key=lambda t:t['task_id'])[0]
            return feature_preparation.prepare_scope(engine, job['id'], task['task_id'],
                feature_input=completed[-1]['input'] if completed else None, adapter=adapter, stop_event=stop_event)
        if phase in {'scope_ready','preparing_runtime'}:
            role = job['payload']['current_step_scope']['scope']['role']
            if role != 'implementation':
                return _hold(engine, job, 'The next step requires combined '+role+'. Its scope is saved; that connected path is not installed yet.')
            return _prepared(engine, job, provision=provision, stop_event=stop_event)
        if phase == 'queue_step':
            with engine.connect() as c:
                offered = dict(intake.require(c, task_run_offers, job['payload']['current_offer_id']))
            session = feature_steps.queue_session(engine, offered)
            if not session:
                return False
            with write_transaction(engine) as c:
                current = intake.require(c, planning_jobs, job['id'])
                feature_steps.parent(c, approval['id'])
                if current['status'] != 'queued' or current['payload'] != job['payload']:
                    return False
                c.execute(planning_jobs.update().where(planning_jobs.c.id == job['id']).values(
                    payload={**current['payload'],'phase':'await_step','session_id':session['id']},
                    message='The current feature step is owned by its protected implementation session.'))
            return True
        if phase == 'await_step':
            with engine.connect() as c:
                session = intake.require(c, task_sessions, approval['id'])
                outcome = session['state'].get('feature_slot_result')
                if not outcome:
                    return False
                slot = session['state']['feature_slot']
                if slot['snapshot']['feature_offer_id'] != job['payload']['current_offer_id']:
                    raise ValueError('The feature session and current prepared task differ.')
                if outcome['status'] != 'completed' or (outcome.get('outcome') or {}).get('status') != 'verified':
                    return _hold(engine, job, 'This step did not produce a qualifying checked result. Earlier checked steps and the failed attempt remain saved; no unverified changes were carried forward.')
                execution = c.execute(select(real_execution_requests).join(real_execution_grants,
                    real_execution_requests.c.grant_id == real_execution_grants.c.id).where(
                    real_execution_grants.c.snapshot['task_budget']['offer']['id'].as_string() == job['payload']['current_offer_id'])).mappings().one()
                execution_id = execution['id']
            checked = feature_source.prepare_input(engine, execution_id)
            record = dict(task_id=slot['task_id'], execution_id=execution_id,
                offer_id=job['payload']['current_offer_id'], input=checked)
            with write_transaction(engine) as c:
                current = intake.require(c, planning_jobs, job['id'])
                actual = intake.require(c, task_sessions, approval['id'])
                feature_steps.parent(c, approval['id'])
                if (current['payload'] != job['payload'] or actual['state'].get('feature_slot_result') != outcome
                        or actual['state'].get('feature_slot') != slot):
                    return False
                payload = {**current['payload'], 'phase':'prepare_scopes',
                    'completed_steps':current['payload'].get('completed_steps',[])+[record]}
                for key in ('current_offer_id','current_step_scope','feature_preparation_claim','feature_scope_claim'):
                    payload.pop(key,None)
                state = {**actual['state'], 'feature_history':actual['state'].get('feature_history',[])+[
                    dict(slot=slot, result=outcome, checked=record)]}
                for key in ('feature_slot','feature_slot_result','feature_slot_state'):
                    state.pop(key,None)
                c.execute(task_sessions.update().where(task_sessions.c.id == actual['id']).values(
                    state=state, status='pending', message='The step passed independent checks. Preparing the next step from its exact checked changes.'))
                c.execute(planning_jobs.update().where(planning_jobs.c.id == job['id']).values(
                    payload=payload, message='Checked step changes are preserved. Preparing the next dependent step.'))
            return True
        return False
    except everyday.CapacityHold as error:
        with write_transaction(engine) as c:
            current = intake.require(c, planning_jobs, job['id'])
            if current['status'] == job['status'] and current['payload'] == job['payload'] and current['message'] != str(error):
                c.execute(planning_jobs.update().where(planning_jobs.c.id == job['id']).values(message=str(error)))
        return False
    except (ValueError, OSError, HTTPException, KeyError, TypeError) as error:
        # A preparation claim may have committed before a check failed. Retain
        # that exact claim rather than letting the older queued snapshot win.
        with engine.connect() as c:
            current = dict(intake.require(c, planning_jobs, job['id']))
        if current['payload'].get('feature_preparation_claim',{}).get('owner') == real_execution.owner_identity():
            job = current
        return _hold(engine, job, 'Feature execution is held: '+str(error)[:700])
