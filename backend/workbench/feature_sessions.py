"""One parent feature session in the existing session owner.

The schema deliberately binds a session to an actual owner decision. Individual
step packages are immutable slots inside that finite session, not new decisions
or competing task/session rows. Existing task_session/controller code runs slots.
"""
import copy
from uuid import UUID, uuid5

from sqlalchemy import select

from workbench import intake, task_sessions as owner
from workbench.db import (task_sessions, model_attempts, real_execution_requests,
    real_execution_grants, write_transaction)
from workbench.visual_scope import digest


def admit(c, approval, permission):
    """One actual parent row exists before any scope/check/model dispatch."""
    snapshot = dict(feature_parent=True, intake_id=approval['intake_id'], decision_id=approval['id'],
        parent_binding=approval['binding'], permission_binding=digest(permission))
    existing = c.execute(select(task_sessions).where(task_sessions.c.id == approval['id'])).mappings().first()
    if existing:
        if existing['snapshot'] != snapshot or existing['binding'] != digest(snapshot):
            raise ValueError('This owner decision already has a different parent session.')
        return dict(existing)
    row = dict(id=approval['id'], task_id='intake-'+approval['intake_id'], snapshot=snapshot,
        binding=digest(snapshot), status='pending', message='Preparing the reviewed feature. No implementation has started.',
        state=dict(feature_history=[]), created_at=intake.now(), finished_at=None)
    c.execute(task_sessions.insert().values(**row))
    return row


def hold_if_idle(c, decision_id, message):
    """Hold a settled parent without declaring the feature completed."""
    parent = c.execute(select(task_sessions).where(task_sessions.c.id == decision_id)).mappings().first()
    if (parent and parent['snapshot'].get('feature_parent') and parent['status'] in owner.ACTIVE
            and not parent['state'].get('cancel_requested')
            and (not parent['state'].get('feature_slot') or parent['state'].get('feature_slot_result'))):
        c.execute(task_sessions.update().where(task_sessions.c.id == decision_id).values(
            status='held', message=message[:1000], finished_at=intake.now()))


def queue(engine, offered):
    from workbench.feature_steps import _offer, parent, permission
    from workbench.real_workspace import sha
    with engine.connect() as c:
        selected = _offer(c, offered, active=True)
    detail = selected['snapshot']['feature_step']
    saved = detail['scope']
    slot_key = 'feature-step:'+saved['task_id']+':'+str(detail['attempt'])
    if detail.get('preparation_revision'):
        slot_key += ':preparation:'+str(detail['preparation_revision'])
    slot_id = str(uuid5(UUID(saved['decision_id']), slot_key))
    with write_transaction(engine) as c:
        approval, authorization = parent(c, saved['decision_id'])
        existing = c.execute(select(task_sessions).where(task_sessions.c.id == approval['id'])).mappings().first()
        if existing:
            expected = dict(feature_parent=True, intake_id=approval['intake_id'], decision_id=approval['id'],
                parent_binding=approval['binding'], permission_binding=digest(authorization))
            if existing['snapshot'] != expected or existing['binding'] != digest(expected):
                raise ValueError('The parent session changed or belongs to different work.')
            current = existing['state'].get('feature_slot')
            if current and current['id'] == slot_id:
                if current['snapshot']['feature_offer_id'] != selected['id']:
                    raise ValueError('This feature slot already owns different immutable inputs.')
                return dict(existing)
            if existing['status'] not in owner.ACTIVE or current:
                raise ValueError('The previous feature slot must settle before another can start.')
        if (c.execute(select(task_sessions.c.id).where(task_sessions.c.status.in_(owner.ACTIVE),
                task_sessions.c.id != approval['id'])).first()
                or c.execute(select(real_execution_requests.c.id).where(real_execution_requests.c.status.in_(
                    ['pending','running','checking','cancel_requested']))).first()
                or c.execute(select(model_attempts.c.id).where(model_attempts.c.finished_at.is_(None))).first()
                or c.execute(select(real_execution_grants.c.id).outerjoin(real_execution_requests,
                    real_execution_requests.c.grant_id == real_execution_grants.c.id).where(
                    real_execution_requests.c.id.is_(None), real_execution_grants.c.revoked_at.is_(None),
                    real_execution_grants.c.expires_at > intake.now())).first()):
            return None
        policy = permission(c, approval, saved['task_id'], offer_id=selected['id'])
        ref = dict(intake_id=approval['intake_id'], task_id=saved['task_id'], approval_id=approval['id'])
        snapshot = dict(task=ref, budget_fingerprint=digest(policy), feature_offer_id=selected['id'],
            capability_sha256=sha((owner.ROOT/'.local/task-runtime.json').read_bytes()),
            started_at=selected['created_at'], deadline=policy['expires_at'])
        slot = dict(id=slot_id, task_id=saved['task_id'], snapshot=snapshot, binding=digest(snapshot),
            created_at=intake.now())
        message = 'Preparing this feature step from its checked input. No model has started.'
        if existing:
            state = {**existing['state'], 'feature_slot':slot, 'feature_slot_state':{}}
            c.execute(task_sessions.update().where(task_sessions.c.id == approval['id']).values(
                status='pending', state=state, message=message))
        else:
            snapshot = dict(feature_parent=True, intake_id=approval['intake_id'], decision_id=approval['id'],
                parent_binding=approval['binding'], permission_binding=digest(authorization))
            state = dict(feature_slot=slot, feature_slot_state={}, feature_history=[])
            c.execute(task_sessions.insert().values(id=approval['id'], task_id='intake-'+approval['intake_id'],
                snapshot=snapshot, binding=digest(snapshot), status='pending', message=message,
                state=state, created_at=intake.now(), finished_at=None))
        return dict(c.execute(select(task_sessions).where(task_sessions.c.id == approval['id'])).mappings().one())


def projection(row):
    """Immutable step package identity; lifecycle writes still target its parent."""
    slot = row['state']['feature_slot']
    if slot['binding'] != digest(slot['snapshot']):
        raise ValueError('The saved feature slot changed.')
    state = copy.deepcopy(row['state'].get('feature_slot_state', {}))
    for key in ('owner','cancel_requested','stop_command'):
        if key in row['state']:
            state[key] = row['state'][key]
    return {**slot, 'status':row['status'], 'message':row['message'], 'state':state,
        'finished_at':None, '_feature_parent_id':row['id']}


def record_result(engine, slot, status, message, state):
    with write_transaction(engine) as c:
        actual = c.execute(select(task_sessions).where(task_sessions.c.id == slot['_feature_parent_id'])).mappings().one()
        if (actual['state'].get('owner') != slot['state'].get('owner')
                or actual['binding'] != digest(actual['snapshot'])
                or actual['state']['feature_slot']['id'] != slot['id']
                or actual['state']['feature_slot']['binding'] != slot['binding']
                or actual['state']['feature_slot']['snapshot'] != slot['snapshot']):
            return False
        prior = actual['state'].get('feature_slot_result')
        if prior:
            return (prior['status'] == status and prior['message'] == message[:1000]
                and prior['outcome'] == state.get('outcome'))
        updated = {**actual['state'], 'feature_slot_state':state}
        terminal = status not in owner.ACTIVE
        if terminal:
            updated['feature_slot_result'] = dict(id=slot['id'], status=status,
                message=message[:1000], outcome=state.get('outcome'), finished_at=intake.now())
        # A checked child is never a completed parent feature.
        parent_status = 'pending' if status == 'completed' else status
        if terminal and actual['state'].get('cancel_requested'):
            parent_status = 'cancelled'
        if (actual['status'] == parent_status and actual['message'] == message[:1000]
                and actual['state'] == updated):
            return True
        c.execute(task_sessions.update().where(task_sessions.c.id == actual['id']).values(
            status=parent_status, state=updated, message=message[:1000],
            finished_at=None if parent_status in owner.ACTIVE else intake.now()))
    return True
