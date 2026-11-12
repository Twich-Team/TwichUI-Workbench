"""Bounded, blind feature-check proposals in the existing parent journal.

Each provider request has its own durable lease and usage event. The model sees
original committed source only. Unknown requests stop; no implementation or
verification verdict is supplied by this preparation stage.
"""
import copy
import json
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select

from workbench import intake, feature_checks, feature_steps, everyday
from workbench.db import planning_jobs, model_attempts, task_sessions, write_transaction
from workbench.feature_preparation import ParentStop
from workbench.planning_transport import PlanningUnavailable
from workbench.real_execution import owner_identity
from workbench.visual_scope import digest


def start(engine, job):
    with engine.connect() as c:
        _, permission = feature_steps.parent(c,job['payload']['feature_decision_id'])
        if permission['budget']['version'] != 2:
            raise ValueError('This original run did not authorize independent feature-check preparation. Its old limits remain unchanged.')
        scope = job['payload'].get('current_step_scope') or {}
        if (job['payload'].get('phase')!='scope_ready' or scope.get('decision_id')!=job['payload']['feature_decision_id']
                or scope.get('scope',{}).get('role')!='verification'):
            raise ValueError('Independent preparation requires the exact current verification step.')
        feature_steps.validate_scope(c,scope)
        original = feature_checks.packet(c,job['payload']['feature_decision_id'])
    with write_transaction(engine) as c:
        feature_steps.parent(c,job['payload']['feature_decision_id'])
        current = intake.require(c,planning_jobs,job['id'])
        if current['status']!='queued' or current['payload']!=job['payload']:
            return False
        c.execute(planning_jobs.update().where(planning_jobs.c.id==job['id']).values(
            payload={**current['payload'],'phase':'prepare_independent_checks',
                'independent_check_preparation':dict(initial_packet=original,packet=original,history=[])},
            message='Preparing independent checks from the original request and source. Candidate code is withheld from the check producer.'))
    return True


def advance(engine, job, *, adapter=None, stop_event=None):
    from workbench.planning_provider import LocalProvider
    from workbench.planning_response import create_evidence
    from workbench.planning_worker import finish_attempt
    from workbench.bug_source_planning import inspect, source_characters
    with engine.connect() as c:
        approval, permission = feature_steps.parent(c,job['payload']['feature_decision_id'])
        saved = job['payload']['independent_check_preparation']
        history = saved['history']
        budget = permission['budget']
        if (budget['version']!=2 or job['status']!='queued'
                or job['payload']['phase']!='prepare_independent_checks'
                or len(history)>=budget['verification_requests']
                or any(not record['confirmed_finished'] for record in history)):
            return False
        initial = feature_checks.packet(c,approval['id'])
        if initial != saved['initial_packet']:
            raise ValueError('Original feature requirements or source changed before independent preparation.')
        current_packet = copy.deepcopy(feature_checks.validate_packet(initial,saved['packet']))
        proposed = saved.get('proposed_program')
        schema = feature_checks.Review if proposed else feature_checks.Response
        wire = copy.deepcopy(current_packet)
        if proposed:
            wire.update(proposed_program=proposed['program'],program_binding=proposed['binding'],
                purpose='review-independent-feature-observations')
        elif saved.get('feedback'):
            wire['previous_check_feedback']=saved['feedback']
        route = permission['scope']['route']
        if permission['scope']['provider']!='local':
            raise ValueError('The saved provider has no supported independent feature-check request bound. No fallback was made.')
    if everyday.enabled():
        everyday.require_room(everyday.configuration(engine))
    if adapter is None:
        from workbench.local_model_priority import planning_ready
        if not planning_ready():
            return False
    stopped = ParentStop(engine,approval['id'],stop_event)
    if stopped.is_set():
        return False
    claim = dict(owner=owner_identity(),decision_id=approval['id'],parent_binding=approval['binding'],
        packet_binding=digest(wire),schema='review' if proposed else 'program-or-inspection')
    lease = str(uuid4())
    with write_transaction(engine) as c:
        feature_steps.parent(c,approval['id'])
        current = intake.require(c,planning_jobs,job['id'])
        if current['status']!='queued' or current['payload']!=job['payload']:
            return False
        if c.execute(select(model_attempts.c.id).where(model_attempts.c.finished_at.is_(None))).first():
            return False
        c.execute(planning_jobs.update().where(planning_jobs.c.id==job['id']).values(
            status='running',lease=lease,lease_until=(datetime.now(timezone.utc)+timedelta(
                seconds=budget['verification_request_seconds']+30)).isoformat(),
            payload={**current['payload'],'phase':'preparing_independent_checks','feature_checks_claim':claim},
            message='Reviewing independent feature checks.' if proposed else 'Reading original source to prepare independent feature checks.'))
        c.execute(model_attempts.insert().values(id=lease,job_id=job['id'],task_id=approval['intake_id'],
            purpose='feature_check_preparation',source='workbench',backend='local',
            requested_model=route['model'],requested_effort=route.get('effort'),policy_revision=route['policy_revision'],
            outcome='running',started_at=intake.now(),
            routing_reason='Parent-approved blind feature checks from original source. No candidate/coding response, editing or fallback authority.'))
        c.execute(task_sessions.update().where(task_sessions.c.id==approval['id']).values(
            status='active',finished_at=None,message='Preparing read-only independent feature checks from original source.'))
    used = adapter or LocalProvider(stopped,ends_at=time.monotonic()+budget['verification_request_seconds'])
    evidence = None
    failure = model = basis = None
    accepted = unsupported = False
    next_saved = copy.deepcopy(saved)
    try:
        evidence = create_evidence(lease,schema,files=current_packet['files'])
        used.response_evidence = evidence
        with engine.connect() as c:
            feature_steps.parent(c,approval['id'])
        raw,model,basis = used.generate(wire,schema,expected_model=route['model'])
        if evidence:
            evidence.snapshot('proposed-plan',raw)
        if model!=route['model'] or basis not in {'local','demo'} or len(json.dumps(raw).encode())>48000:
            raise ValueError('Independent check response differs from its bounded saved model.')
        parsed = schema.model_validate(raw)
        if proposed:
            if parsed.program_binding != proposed['binding']:
                raise ValueError('Independent program review names different observations.')
            next_saved['review'] = parsed.model_dump(mode='json')
            if parsed.ready:
                accepted = True
            else:
                next_saved.pop('proposed_program',None)
                next_saved['feedback'] = dict(kind='model-self-review',issues=parsed.issues,
                    program=proposed['program'],note='Untrusted review feedback, not an execution result.')
        elif parsed.limitation is not None:
            unsupported = True
            raise ValueError('Independent feature checks need an unsupported capability: '+parsed.limitation)
        elif parsed.inspection:
            windows,matches,_ = inspect(current_packet,parsed.inspection,result_budget=6500)
            if source_characters(current_packet)>262144:
                raise ValueError('Independent source inspection exceeded its original-source bound.')
            current_packet['input_binding']=digest({k:v for k,v in current_packet.items() if k!='input_binding'})
            feature_checks.validate_packet(initial,current_packet)
            next_saved['packet']=current_packet
            next_saved['feedback']=dict(kind='committed-source-inspection',finding=parsed.finding,
                windows=windows,matches=matches,source_revision=initial['base'])
        else:
            from workbench.feature_verification import observation_binding
            if observation_binding(parsed.program.model_dump(mode='json')) in saved.get('rejected_observations',[]):
                raise ValueError('The same rejected original-source observations cannot be repeated unchanged.')
            compiled,_ = feature_checks.compile(current_packet,parsed.program.model_dump(mode='json'))
            next_saved['proposed_program']=compiled
            next_saved.pop('review',None)
            next_saved.pop('feedback',None)
        with engine.connect() as c:
            if feature_checks.packet(c,approval['id'])!=initial:
                raise ValueError('Original feature requirements or source changed during independent preparation.')
    except (ValueError,OSError,HTTPException,PlanningUnavailable,KeyError,TypeError) as error:
        failure = 'Independent feature-check preparation held: '+(str(error)[:800] if not isinstance(error,HTTPException) else 'The approved feature changed.')
        accepted = False
    finally:
        if evidence:
            evidence.close()
    diagnostics = copy.deepcopy(getattr(used,'diagnostics',{}))
    confirmed = bool(diagnostics.get('generation_state')=='finished'
        and diagnostics.get('accounting_state')=='complete' and diagnostics.get('usage'))
    # A local validation failure may be corrected only after a finished request;
    # unknown or partial generation is retained and cannot replay.
    with write_transaction(engine) as c:
        active = intake.require(c,planning_jobs,job['id'])
        if active['status']!='running' or active['lease']!=lease:
            return True
        try:
            feature_steps.parent(c,approval['id'])
        except (ValueError,OSError,HTTPException,KeyError,TypeError):
            failure,accepted = 'Feature stopped or changed. Independent output remains evidence only.',False
        parent = intake.require(c,task_sessions,approval['id'])
        stopped_parent = bool(parent['state'].get('cancel_requested'))
        finish_attempt(c,{**job,'lease':lease},used,model,basis,'completed' if not failure else 'failed')
        record = dict(operation_id=lease,confirmed_finished=confirmed,schema=claim['schema'],
            failure=failure,diagnostics=diagnostics,input_binding=digest(wire))
        next_saved['history'] = history+[record]
        if failure:
            next_saved['feedback'] = dict(kind='validation',message=failure)
        can_continue = confirmed and not stopped_parent and not unsupported and len(history)+1<budget['verification_requests']
        ready = accepted and confirmed and not stopped_parent and not failure
        if ready:
            message = 'Independent observations are prepared and reviewed. Original/candidate execution has not yet run.'
        elif can_continue:
            message = failure or 'Continuing original-source independent check preparation.'
        else:
            message = failure or 'Independent preparation stopped without a complete checked program. Saved claims and unknown usage remain.'
        payload = {**active['payload'],'independent_check_preparation':next_saved,
            'phase':'independent_checks_ready' if ready else 'prepare_independent_checks' if can_continue else 'held'}
        c.execute(planning_jobs.update().where(planning_jobs.c.id==job['id']).values(
            status='queued' if ready or can_continue else 'failed',lease=None,lease_until=None,
            finished_at=None if ready or can_continue else intake.now(),payload=payload,
            message=message))
        if stopped_parent:
            known = confirmed or diagnostics.get('generation_state')=='not_started'
            c.execute(task_sessions.update().where(task_sessions.c.id==approval['id']).values(
                status='cancelled' if known else 'waiting_recovery',finished_at=intake.now() if known else None,
                message='Feature stopped. Independent preparation settled; earlier work remains.' if known else
                    'Feature stopping. Independent provider outcome or usage is uncertain; no replay or implementation starts.'))
        elif ready or can_continue:
            c.execute(task_sessions.update().where(task_sessions.c.id==approval['id']).values(
                status='pending',finished_at=None,message='Independent check preparation saved. Combined execution has not yet run.'))
        else:
            from workbench.feature_sessions import hold_if_idle
            hold_if_idle(c,approval['id'],failure or 'Independent preparation held; saved work remains.')
    return True
