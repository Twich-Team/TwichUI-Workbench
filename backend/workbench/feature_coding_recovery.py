"""One separately reviewed coding reservation after a settled feature failure.

Original decisions, three-grant budgets, unknown usage and terminal outcomes stay
immutable. The receipt admits only its exact new slot, within the current parent
or a separately reviewed fresh window. It never carries failed edits, changes provider, or permits publication.
"""
from fastapi import HTTPException
from sqlalchemy import select

from workbench import feature_attempts, feature_permission, feature_steps, intake
from workbench.db import planning_jobs, task_sessions, task_run_offers, write_transaction
from workbench.visual_scope import digest


def _job(c, identity):
    return c.execute(select(planning_jobs).where(planning_jobs.c.intake_id == identity,
        planning_jobs.c.kind == 'feature_execution')).mappings().one()


def validate_authority(c, proof, scope):
    job = c.execute(select(planning_jobs).where(planning_jobs.c.kind == 'feature_execution',
        planning_jobs.c.payload['feature_decision_id'].as_string() == scope['decision_id'])).mappings().one()
    records = job['payload'].get('coding_continuations', [])
    matching = []
    for record in records:
        value = {k:v for k,v in record.items() if k != 'receipt_binding'}
        inputs = record['approved_inputs']
        if (record['receipt_binding'] != digest(value) or record['binding'] != digest(inputs)
                or record['owner_provenance'] != 'browser-feature-coding-continuation-v1'
                or record['additional_attempts'] != 1
                or record['failed_edits_reused'] is not False
                or record['original_budget_changed'] is not False or record['publication_changed'] is not False):
            raise ValueError('The separate coding reservation changed.')
        if inputs['recovery'] == proof:
            matching.append(record)
    if len(matching) != 1:
        raise ValueError('A separate reviewed reservation for this exact failed attempt is required.')
    record = matching[0]
    approval, permission = feature_steps.parent(c, scope['decision_id'], active=False)
    inputs = record['approved_inputs']
    from workbench.feature_run_recovery import deadline
    if (inputs['decision_id'] != approval['id'] or inputs['parent_binding'] != approval['binding']
            or inputs['permission_binding'] != digest(permission)
            or inputs['scope'] != scope
            or record['expires_at'] != deadline(c,approval,permission,at=record['approved_at'])
            or proof['coding_budget']['limit'] != len(proof['coding_budget']['consumed'])+1):
        raise ValueError('The separate reservation differs from the original feature or limits.')
    return record


def offer(c, identity):
    feature_permission.capability(c.engine)
    job = _job(c,identity)
    payload=job['payload']
    if job['status'] != 'failed' or payload.get('phase') != 'held' or job['lease'] or not job['finished_at']:
        return None
    selected=intake.require(c,task_run_offers,payload['current_offer_id'])
    scope=selected['snapshot']['feature_step']['scope']
    approval, permission=feature_steps.parent(c,scope['decision_id'],active=False)
    if permission['scope'] != feature_permission.scope(c,identity):
        return None
    if len(feature_attempts.coding_grants(c,scope)) < permission['budget']['implementation_attempts_per_step']:
        return None
    feature_steps._offer(c,selected,active=False)
    proof=feature_attempts.settled(c,selected,additional_review=True)
    parent=intake.require(c,task_sessions,approval['id'])
    inputs=dict(decision_id=approval['id'],parent_binding=approval['binding'],
        permission_binding=digest(permission),scope=scope,recovery=proof,
        parent_state_binding=digest(parent['state']),finished_at=job['finished_at'],
        prior_continuations_binding=digest(payload.get('coding_continuations',[])))
    limits=('One additional isolated coding attempt, at most 24,000 generated tokens and 20 turns, '
        'in a separately reviewed six-hour feature window. Each attempt lasts at most 90 minutes. '
        'Earlier grants, unknown usage, deadlines and failed results remain unchanged. '
        +'Start from the last checked source; prior failed edits remain diagnostic evidence only. '
        +'The current step only, AI choice, fallback off, independent checks and separate publishing permission stay in place.')
    from workbench.db import backlog_items
    task=intake.require(c,backlog_items,scope['task_id'])
    return dict(binding=digest(inputs),hours=6,task_title=task['title'],kind='coding_attempt',
        approved_inputs=inputs,limits_description=limits,
        message='The earlier attempt did not pass all checks. Review a bounded repair of this same step; unfinished work and its failures stay saved.')


def request(engine,identity,body):
    with write_transaction(engine) as c:
        job=_job(c,identity)
        records=job['payload'].get('coding_continuations',[])
        prior=next((r for r in records if r['command_id']==str(body.command_id)),None)
        if prior:
            if prior['binding'] != body.binding:
                raise HTTPException(409,'This command belongs to a different coding reservation.')
            return prior
        try:
            selected=offer(c,identity)
        except (ValueError,OSError,KeyError,TypeError):
            selected=None
        if not selected or selected['binding'] != body.binding:
            raise HTTPException(409,'The failed work, source or coding reservation changed. Refresh before continuing.')
        inputs=selected['approved_inputs']
        proof=inputs['recovery']
        approval,permission=feature_steps.parent(c,inputs['decision_id'],active=False)
        from workbench.feature_run_recovery import deadline
        from datetime import datetime,timedelta
        at=intake.now()
        receipt=dict(command_id=str(body.command_id),binding=body.binding,approved_inputs=inputs,
            approved_at=at,expires_at=(datetime.fromisoformat(at)+timedelta(hours=6)).isoformat(),
            new_window=True,previous_window_end=deadline(c,approval,permission),original_expires_at=permission['expires_at'],
            scope_changed=False,attempts_reset=False,additional_attempts=1,
            original_budget_changed=False,failed_edits_reused=False,publication_changed=False,
            owner_provenance='browser-feature-coding-continuation-v1')
        receipt={**receipt,'receipt_binding':digest(receipt)}
        parent=intake.require(c,task_sessions,approval['id'])
        state=parent['state']
        state={**state,'feature_history':state.get('feature_history',[])+[dict(
            slot=state['feature_slot'],result=state['feature_slot_result'],recovery=proof)]}
        for key in ('feature_slot','feature_slot_result','feature_slot_state','owner'):
            state.pop(key,None)
        payload={**job['payload'],'phase':'scope_ready','current_attempt':proof['next_attempt'],
            'current_recovery':proof,'coding_continuations':records+[receipt],
            'recovery_history':job['payload'].get('recovery_history',[])+[proof]}
        for key in ('current_offer_id','feature_preparation_claim','current_preparation_recovery','session_id'):
            payload.pop(key,None)
        c.execute(task_sessions.update().where(task_sessions.c.id==parent['id']).values(
            status='pending',finished_at=None,state=state,message='Preparing the separately reviewed coding attempt. Earlier outcomes remain saved.'))
        c.execute(planning_jobs.update().where(planning_jobs.c.id==job['id']).values(
            status='queued',finished_at=None,payload=payload,message='One separately reviewed coding attempt will start from the last checked source.'))
        return receipt
