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


def request_limit(c, job, permission, *, active=True):
    limit = permission['budget']['verification_requests']
    history = job['payload']['independent_check_preparation']['history']
    corrections = job['payload'].get('independent_check_corrections', [])
    if len(corrections) > 2:
        raise ValueError('Only source correction and one corrected-contract attempt are supported.')
    for ordinal, receipt in enumerate(corrections):
        inputs = receipt['approved_inputs']
        if ordinal and (inputs.get('preparation_contract') != contract_binding()
                or inputs['additional_source']):
            raise ValueError('The corrected-contract attempt cannot add source or change its contract.')
        approval, _ = feature_steps.parent(c, inputs['decision_id'], active=active)
        from workbench.feature_run_recovery import deadline
        if (receipt['receipt_binding'] != digest({k:v for k,v in receipt.items() if k != 'receipt_binding'})
                or receipt['binding'] != digest(inputs) or inputs['parent_binding'] != approval['binding']
                or inputs['permission_binding'] != digest(permission) or inputs['starts_after'] != limit
                or digest(history[:limit]) != inputs['history_binding']
                or receipt['generation_requests'] != 2 or receipt['request_seconds'] != 120
                or receipt['completion_tokens'] != permission['budget']['verification_completion_tokens']
                or receipt['owner_provenance'] != 'browser-feature-readonly-check-correction'
                or any(receipt[k] is not False for k in ('source_writes','implementation_dispatch','publication','fallback'))
                or receipt['expires_at'] != min(datetime.fromisoformat(receipt['approved_at'])+timedelta(minutes=30),
                    datetime.fromisoformat(deadline(c, approval, permission, at=receipt['approved_at']))).isoformat()):
            raise ValueError('The separate original-source check correction changed.')
        if active and ordinal == len(corrections)-1 and datetime.now(timezone.utc)+timedelta(seconds=120) >= datetime.fromisoformat(receipt['expires_at']):
            raise ValueError('The bounded read-only check correction expired.')
        expected = {f['path']:f['sha256'] for f in receipt['prior_context']['packet']['files']}
        expected.update({f['path']:f['sha256'] for f in inputs['additional_source']})
        actual = {f['path']:f['sha256'] for f in job['payload']['independent_check_preparation']['packet']['files']}
        if actual != expected:
            raise ValueError('Independent source disclosure differs from its recorded correction.')
        limit += receipt['generation_requests']
    return limit


def contract_binding():
    return digest(dict(instructions=feature_checks.INSTRUCTIONS,
        schema=feature_checks.wire_schema(), review=feature_checks.REVIEW_INSTRUCTIONS))


def correction_offer(c, job):
    """One finite program/review correction after settled disclosure failures."""
    if ((job['payload'].get('current_step_scope') or {}).get('scope', {}).get('role') != 'verification'
            or len(job['payload'].get('independent_check_corrections', [])) >= 2
            or not job['payload'].get('independent_check_preparation')):
        return None
    approval, permission = feature_steps.parent(c, job['payload']['feature_decision_id'])
    saved = job['payload']['independent_check_preparation']
    history = saved['history']
    prior = job['payload'].get('independent_check_corrections', [])
    if (len(history) != request_limit(c, job, permission, active=False) or saved.get('proposed_program')
            or not finished_history(c, job, history)
            or c.execute(select(model_attempts.c.id).where(model_attempts.c.finished_at.is_(None))).first()):
        return None
    from workbench.repair_continuation import private_json
    from workbench.bug_source_planning import committed
    selected = {f['path'] for f in saved['packet']['files']}
    additional = []
    # These are the blind producer's actual retained dependency requests, not
    # paths inferred from candidate code or a developer-written answer key.
    for record in ([] if prior else history):
        if record.get('confirmed_finished') and 'source disclosure permission' in (record.get('failure') or ''):
            path = everyday.ROOT/'artifacts/runs'/('planning-evidence-'+record['operation_id'])/'proposed-plan.json'
            raw = private_json(path, 48000)
            for item in raw.get('inspection') or []:
                name = item.get('path')
                if not name or name in selected:
                    continue
                try:
                    _, metadata = committed(name, saved['packet']['base'])
                except (ValueError, OSError):
                    continue
                selected.add(name)
                additional.append({k:metadata[k] for k in ('path','sha256','git_blob')})
    if (not additional and not prior) or len(selected) > 12:
        return None
    if prior and (prior[-1]['approved_inputs'].get('preparation_contract') == contract_binding()
            or not saved.get('feedback')):
        return None
    from workbench.feature_run_recovery import deadline
    if datetime.now(timezone.utc)+timedelta(seconds=240) >= datetime.fromisoformat(deadline(c, approval, permission)):
        return None
    inputs = dict(kind='independent-check-correction-v1', decision_id=approval['id'],
        parent_binding=approval['binding'], permission_binding=digest(permission), starts_after=len(history),
        history_binding=digest(history), packet_binding=digest(saved['packet']), additional_source=additional,
        **({'preparation_contract': contract_binding()} if prior else {}))
    return dict(binding=digest(inputs), approved_inputs=inputs, task_title='Prepare and review independent feature checks',
        generation_requests=2, request_seconds=120, completion_tokens=permission['budget']['verification_completion_tokens'],
        message=('Prepare one complete program and its review under the corrected exclusive response contract, using the same original source. No implementation is repeated. ' if prior else
            'Prepare one independent program and its review. Add only these requested original dependencies: '+', '.join(f['path'] for f in additional)+'. ')+
            'Two separately recorded read-only requests; earlier responses, usage, original limits and publication hold remain unchanged.')


def record_correction(c, job, body, selected):
    if body.generation_requests != 2:
        raise HTTPException(409, 'This correction permits exactly one program request and one review.')
    inputs = selected['approved_inputs']
    approval, permission = feature_steps.parent(c, inputs['decision_id'])
    saved = job['payload']['independent_check_preparation']
    packet = copy.deepcopy(saved['packet'])
    from workbench.bug_source_planning import inspect
    from workbench.bug_evidence import InvestigationRead
    source = inputs['additional_source']
    for start in range(0, len(source), 3):
        inspect(packet, [InvestigationRead(path=f['path'], start=1, end=80) for f in source[start:start+3]])
    for file in source:
        actual = next(f for f in packet['files'] if f['path'] == file['path'])
        if actual['sha256'] != file['sha256'] or actual['git_blob'] != file['git_blob']:
            raise ValueError('Original dependency changed before read-only correction.')
    packet['input_binding'] = digest({k:v for k,v in packet.items() if k != 'input_binding'})
    feature_checks.validate_packet(saved['initial_packet'], packet)
    from workbench.feature_run_recovery import deadline
    at = intake.now()
    value = dict(command_id=str(body.command_id), binding=body.binding, approved_inputs=inputs, approved_at=at,
        expires_at=min(datetime.fromisoformat(at)+timedelta(minutes=30),datetime.fromisoformat(deadline(c, approval, permission))).isoformat(),
        generation_requests=2, request_seconds=120, completion_tokens=permission['budget']['verification_completion_tokens'],
        source_writes=False, implementation_dispatch=False, publication=False, fallback=False,
        owner_provenance='browser-feature-readonly-check-correction', prior_context=saved)
    receipt = {**value, 'receipt_binding':digest(value)}
    payload = {**job['payload'], 'phase':'prepare_independent_checks',
        'independent_check_corrections':job['payload'].get('independent_check_corrections', [])+[receipt], 'independent_check_preparation':{**saved, 'packet':packet,
            'feedback':dict(kind='corrected-contract' if inputs.get('preparation_contract') else 'original-dependencies-supplied',message='The complete selected original dependency definitions are supplied. Return one COMPLETE program using tested public calls and the supplied committed startup. Combine related observations within phases so lifecycle reset, controls and requirement mapping fit; an incomplete program is a limitation alone. Map behavioral portions of checkpoint/full-suite clauses to related observations; native history, full-suite execution and publication safeguards remain app-owned, not phase code. Existing tests establish settings defaults and control calls; no second settings runtime is needed. No candidate is disclosed.')}}
    c.execute(planning_jobs.update().where(planning_jobs.c.id == job['id']).values(
        status='queued', finished_at=None, payload=payload, message='Two separately recorded original-source check requests; all earlier history retained.'))
    c.execute(task_sessions.update().where(task_sessions.c.id == approval['id']).values(
        status='pending', finished_at=None, message='Preparing independent checks from the explicitly bound original dependencies.'))
    return receipt


def finished_history(c, job, history):
    from workbench.feature_scope_settlement import readonly_partial_check
    for record in history:
        if record.get('confirmed_finished'):
            continue
        attempt = c.execute(select(model_attempts).where(model_attempts.c.id == record['operation_id'])).mappings().first()
        if not attempt or attempt['job_id'] != job['id']:
            return False
        actual = readonly_partial_check(c.engine, record, attempt)
        matching = [p for p in job['payload'].get('independent_readonly_quarantines', [])
                    if p['operation_id'] == record['operation_id']]
        if actual is None or matching != [actual]:
            return False
    return True


def resume_closed_check(engine, job_id):
    """Quarantine a positively closed tool-free response; use unused requests only."""
    from workbench.feature_scope_settlement import readonly_partial_check
    with write_transaction(engine) as c:
        job = intake.require(c, planning_jobs, job_id)
        approval, permission = feature_steps.parent(c, job['payload']['feature_decision_id'])
        saved = job['payload']['independent_check_preparation']
        history = saved['history']
        if (job['kind'] != 'feature_execution' or job['status'] != 'failed' or job['lease']
                or job['payload']['phase'] != 'held' or not job['finished_at'] or not history
                or saved.get('proposed_program') or job['payload']['current_step_scope']['scope']['role'] != 'verification'
                or permission['scope']['provider'] != 'codex'
                or len(history) >= permission['budget']['verification_requests']
                or c.execute(select(model_attempts.c.id).where(model_attempts.c.finished_at.is_(None))).first()):
            raise ValueError('A closed unused independent-check reservation is required.')
        feature_steps.validate_scope(c, job['payload']['current_step_scope'])
        attempt = intake.require(c, model_attempts, history[-1]['operation_id'])
        if attempt['job_id'] != job['id'] or attempt['requested_model'] != permission['scope']['route']['model']:
            raise ValueError('The interrupted response belongs to another task or provider.')
        proof = readonly_partial_check(c.engine, history[-1], attempt)
        if proof is None:
            raise ValueError('Actual capability, supported interruption and producer closure are required.')
        quarantines = job['payload'].get('independent_readonly_quarantines', [])+[proof]
        prospective = {**job, 'payload':{**job['payload'], 'independent_readonly_quarantines':quarantines}}
        if not finished_history(c, prospective, history):
            raise ValueError('Earlier uncertain responses prevent continuation.')
        feature_checks.validate_packet(feature_checks.packet(c, approval['id']), saved['packet'])
        payload = {**prospective['payload'], 'phase':'prepare_independent_checks',
            'independent_check_preparation':{**saved, 'feedback':dict(kind='closed-response-quarantined',
                message='The previous tool-free response was interrupted by an incorrectly selected small-program observation guard. Its partial text is not reusable. A fresh response uses the existing larger Lua-program traffic profile, within the unchanged original request/time/output allowance.')}}
        c.execute(planning_jobs.update().where(planning_jobs.c.id == job_id).values(
            status='queued', finished_at=None, payload=payload,
            message='Interrupted read-only output quarantined; continuing within the unused original check allowance.'))
        c.execute(task_sessions.update().where(task_sessions.c.id == approval['id']).values(
            status='pending', finished_at=None, message='Fresh original-source verification preparation; interrupted output stays unusable.'))
    return dict(proof=proof, requests_consumed=len(history),
        requests_remaining=permission['budget']['verification_requests']-len(history),
        original_request_limit=permission['budget']['verification_requests'],
        budget_changed=False, interrupted_output_reused=False,
        reserved_generated_tokens=permission['budget']['verification_completion_tokens'])


def complete_original_wire(packet):
    """Deterministic original context within the already selected disclosure.

    Reading the selected files needs no model round trips. Keep the durable
    inspection packet intact; only the provider's validated copy is expanded.
    """
    from workbench.bug_source_planning import committed
    wire = copy.deepcopy(packet)
    # Full original files supersede duplicated display windows/search snippets.
    # Their complete durable observations remain in the native packet/history.
    wire.pop('inspection_windows', None)
    wire.pop('inspection_searches', None)
    size = 0
    for file in wire['files']:
        raw, metadata = committed(file['path'], wire['base'])
        if metadata['sha256'] != file['sha256']:
            raise ValueError('Original independent source changed before disclosure.')
        size += len(raw)
        if size > 262144:
            raise ValueError('Selected original context exceeds its existing read bound.')
        file['excerpts'] = [dict(line=1, text=raw.decode())]
    wire['input_binding'] = digest({k:v for k,v in wire.items() if k != 'input_binding'})
    return wire


def refresh_original_setup(engine, job_id):
    """Resume UNUSED original requests after a settled setup-discovery defect.

    Preserve all earlier observations, failures and accounting. No new model
    allowance, source, task permission, candidate or old program is introduced.
    """
    with engine.connect() as c:
        job = intake.require(c, planning_jobs, job_id)
        approval, permission = feature_steps.parent(c, job['payload']['feature_decision_id'])
        initial = feature_checks.packet(c, approval['id'])
        saved = job['payload']['independent_check_preparation']
        history = saved['history']
        scope = job['payload'].get('current_step_scope', {})
        if (job['kind'] != 'feature_execution' or job['status'] != 'failed' or job['lease']
                or job['payload']['phase'] != 'held' or not job['finished_at']
                or scope.get('scope', {}).get('role') != 'verification'
                or saved.get('proposed_program') or not history
                or len(history) >= permission['budget']['verification_requests']):
            raise ValueError('A settled unused independent-check reservation is required.')
        feature_steps.validate_scope(c, scope)
        actual_ids = set(c.execute(select(model_attempts.c.id).where(model_attempts.c.job_id == job['id'],
            model_attempts.c.purpose == 'feature_check_preparation')).scalars())
        if actual_ids != {r['operation_id'] for r in history} or len(actual_ids) != len(history):
            raise ValueError('Every original independent generation must remain counted.')
        for record in history:
            attempt = intake.require(c, model_attempts, record['operation_id'])
            diagnostics = record.get('diagnostics', {})
            if (not record['confirmed_finished'] or attempt['job_id'] != job['id']
                    or not attempt['finished_at'] or attempt['outcome'] not in {'completed', 'failed'}
                    or attempt['backend'] != permission['scope']['provider']
                    or attempt['requested_model'] != permission['scope']['route']['model']
                    or attempt['purpose'] != 'feature_check_preparation'
                    or diagnostics.get('generation_state') != 'finished'
                    or diagnostics.get('accounting_state') != 'complete'
                    or not attempt['usage'] or attempt['usage'] != diagnostics.get('usage')):
                raise ValueError('Unsettled independent generation cannot be refreshed.')

        def unchanged(value):
            value = copy.deepcopy(value)
            value.pop('input_binding', None)
            for file in value['files']:
                file.pop('client_bootstrap', None)
                file.pop('client_access_paths', None)
            return value

        old = saved['initial_packet']
        if (unchanged(initial) != unchanged(old) or initial == old
                or not any(file.get('client_bootstrap') for file in initial['files'])):
            raise ValueError('Only corrected setup metadata on unchanged original source may be refreshed.')
        packet = copy.deepcopy(saved['packet'])
        packet['files'][:len(initial['files'])] = copy.deepcopy(initial['files'])
        packet['input_binding'] = digest({k:v for k,v in packet.items() if k != 'input_binding'})
        feature_checks.validate_packet(initial, packet)
        receipt = dict(kind='unused-original-feature-check-context-refresh-v1',
            decision_id=approval['id'], parent_binding=approval['binding'],
            permission_binding=digest(permission), prior_context=saved,
            new_initial_binding=initial['input_binding'], history_binding=digest(history),
            requests_consumed=len(history), requests_remaining=permission['budget']['verification_requests']-len(history),
            original_request_limit=permission['budget']['verification_requests'], budget_changed=False,
            requests_reset=False, provider_changed=False, candidate_disclosed=False,
            authority='standing-autonomous-delivery; original unused read-only reservation',
            recorded_at=intake.now())
        receipt = {**receipt, 'binding':digest(receipt)}
        next_saved = {**saved, 'initial_packet':initial, 'packet':packet,
            'feedback':dict(kind='original-setup-corrected',
                message='The original committed client startup is now recognized. All previous source observations remain; the original unused request allowance is unchanged. Complete selected original files are supplied deterministically. Prepare the independent program and review it; no candidate is disclosed.')}
    with write_transaction(engine) as c:
        current = intake.require(c, planning_jobs, job_id)
        feature_steps.parent(c, approval['id'])
        if (current != job or c.execute(select(model_attempts.c.id).where(model_attempts.c.finished_at.is_(None))).first()):
            raise ValueError('Work changed before original setup refresh.')
        parent = intake.require(c, task_sessions, approval['id'])
        c.execute(planning_jobs.update().where(planning_jobs.c.id == job_id).values(
            status='queued', finished_at=None, payload={**job['payload'],
                'phase':'prepare_independent_checks', 'independent_check_preparation':next_saved,
                'independent_context_refreshes':job['payload'].get('independent_context_refreshes', [])+[receipt]},
            message='Original setup metadata corrected. Remaining approved independent-check requests retain all earlier history.'))
        c.execute(task_sessions.update().where(task_sessions.c.id == parent['id']).values(
            status='pending', finished_at=None, message='Continuing original-source checks within their unused approved allowance.'))
    return receipt


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
                or len(history)>=request_limit(c, job, permission)
                or not finished_history(c, job, history)):
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
        provider = permission['scope']['provider']
        if provider not in {'local', 'codex', 'claude', 'smart'}:
            raise ValueError('The saved provider has no supported independent feature-check request bound. No fallback was made.')
        if provider in {'codex','claude','smart'}:
            wire = complete_original_wire(wire)
    if everyday.enabled():
        everyday.require_room(everyday.configuration(engine))
    if provider=='smart' and adapter is None:
        from workbench.strategy_waiting import preflight
        if job['payload'].get('ai_wait',{}).get('recheck_at',0)>time.time():
            return False
        if preflight(engine,dict(job),route,lambda:bool(stop_event and stop_event.is_set())):
            return True
        with engine.connect() as c:
            job=intake.require(c,planning_jobs,job['id'])
    if adapter is None and provider == 'local':
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
            purpose='feature_check_preparation',source='workbench',backend=provider,
            requested_model=route['model'],requested_effort=route.get('effort'),policy_revision=route['policy_revision'],
            outcome='running',started_at=intake.now(),
            routing_reason='Parent-approved blind feature checks from original source. No candidate/coding response, editing or fallback authority.'))
        c.execute(task_sessions.update().where(task_sessions.c.id==approval['id']).values(
            status='active',finished_at=None,message='Preparing read-only independent feature checks from original source.'))
    if adapter is not None:
        used = adapter
    elif provider in {'codex','claude','smart'}:
        from workbench.provider_routes import external_planner
        used = external_planner(route if route.get('strategy') else dict(model=route['model'],effort=route.get('effort'),provider=provider),stopped.is_set)
        used.ends_at = time.monotonic()+budget['verification_request_seconds']
    else:
        used = LocalProvider(stopped,ends_at=time.monotonic()+budget['verification_request_seconds'])
    evidence = None
    failure = model = basis = None
    accepted = unsupported = False
    next_saved = copy.deepcopy(saved)
    try:
        if adapter is None and provider in {'codex','claude','smart'}:
            used.bind_source(current_packet)
        evidence = create_evidence(lease,schema,files=current_packet['files'])
        used.response_evidence = evidence
        if getattr(used,'strategy_control',False):
            from workbench.strategy_waiting import options
            used.bind_operation(engine,lease,**options(job))
        with engine.connect() as c:
            feature_steps.parent(c,approval['id'])
        raw,model,basis = used.generate(wire,schema,expected_model=route['model'])
        if evidence:
            evidence.snapshot('proposed-plan',raw)
        if (model!=route['model'] and not getattr(used,'strategy_control',False)) or basis not in ({'local','codex','claude','demo'} if provider=='smart' else {provider,'demo'}) or len(json.dumps(raw).encode())>48000:
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
            windows,matches,_ = inspect(current_packet,parsed.inspection,result_budget=6500,
                **({'disclosure_paths':{f['path'] for f in current_packet['files']}} if provider in {'codex','claude','smart'} else {}))
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
        if diagnostics.get('ai_wait'):
            from workbench.strategy_waiting import pause_preparation
            if pause_preparation(c,active,used,diagnostics['ai_wait'],approval['id'],'feature_checks_claim'):
                return True
        finish_attempt(c,{**job,'lease':lease},used,model,basis,'completed' if not failure else 'failed')
        record = dict(operation_id=lease,confirmed_finished=confirmed,schema=claim['schema'],
            failure=failure,diagnostics=diagnostics,input_binding=digest(wire))
        next_saved['history'] = history+[record]
        if failure:
            next_saved['feedback'] = dict(kind='validation',message=failure)
        can_continue = confirmed and not stopped_parent and not unsupported and len(history)+1<request_limit(c, active, permission)
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
