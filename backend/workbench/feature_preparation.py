"""Bounded read-only step scopes in the existing feature planning journal.

The same parent permission, provider adapters, model accounting and source owner
are used throughout. A provider claim never expires into automatic replay.
"""
import copy
import json
import re
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select

from workbench import intake, feature_steps, feature_planning, task_execution, everyday
from workbench.db import planning_jobs, model_attempts, task_sessions, write_transaction
from workbench.real_execution import owner_identity, alive
from workbench.planning_transport import PlanningUnavailable
from workbench.visual_scope import digest


def packet(c, decision_id, task_id, feature_input, *, inspections=()):
    row, permission = feature_steps.parent(c, decision_id)
    linked = task_execution.snapshot(c, row['intake_id'], task_id, decision_id)
    root, base = feature_steps.source(c, linked, permission, feature_input)
    # Reuse the existing bounded local source selection, then read the exact
    # checked predecessor's versions. No live workspace or downloaded addon runs.
    selected = feature_planning.research(linked['original'], engine=c.engine)
    files, evidence = [], []
    for item in selected['files']:
        bound, raw = feature_steps._file(root, base, item['path'])
        lines = raw.decode().splitlines()
        windows = []
        for window in item['excerpts']:
            start = max(1, min(window['line'], len(lines)))
            chunk = []
            for line in lines[start-1:start+34]:
                if sum(len(s)+1 for s in chunk) + len(line) > 1000:
                    break
                chunk.append(line)
            text = '\n'.join(chunk)
            windows.append(dict(line=start, text=text))
        files.append(dict(path=bound['path'], sha256=bound['sha256'], excerpts=windows))
        evidence.append(dict(id='step-source-'+str(len(evidence)), path=bound['path'],
            sha256=bound['sha256'], revision=base))
    value = dict(original=linked['original'], task=dict(title=linked['task']['title'],
        purpose=linked['task']['purpose'], acceptance=linked['task']['acceptance']),
        approved_scope=linked['approved_scope'], exclusions=linked['proposal_review']['content']['exclusions'],
        base=base, files=files, evidence=evidence,
        **({'references': selected['related']} if selected['related'] else {}),
        indexed_acceptance=[dict(index=i, requirement=s) for i,s in enumerate(linked['task']['acceptance'])],
        source_note='Partial committed source excerpts; absent files and game behavior remain unknown.')
    from workbench import feature_step_source
    from pathlib import PurePosixPath
    directories={str(PurePosixPath(f['path']).parent)+'/' for f in files}
    inventory=feature_step_source.index(root,base)
    inventory.sort(key=lambda item:(not any(item['path'].startswith(d) for d in directories),item['path']))
    terms=set(re.findall(r'[a-z]{3,}',linked['original']['title'].lower()+' '+linked['original']['description'].lower()))
    tests=[item for item in inventory if re.fullmatch(r'tests/test_[A-Za-z0-9_-]+\.lua',item['path'])]
    tests.sort(key=lambda item:(-sum(term in item['path'].lower() for term in terms),item['path']))
    value.update(source_index=inventory[:96],source_index_truncated=len(inventory)>96,
        focused_test_candidates=tests[:12],
        coverage=dict(focused_test_candidates=[item['path'] for item in tests[:12]]),
        inspection_capabilities=dict(read_only=True,max_requests=3,page_lines=80,response_characters=6500,
            note='Index and test candidates are existing names, not inspected source. Read a suitable existing focused test before completing a scope. Request actual definitions as needed. No shell, network, edits or new provider authority.'))
    for record in inspections:
        if record.get('feature_input_binding')!=digest(feature_input):
            raise ValueError('Source inspection belongs to different checked predecessor input.')
    restored = feature_step_source.restore(value,root,base,inspections)
    if permission['scope']['provider'] in {'codex','claude','smart'}:
        # Keep complete observations in native evidence, while disclosing search
        # text only from the selected files. Incidental global matches must not
        # consume the twelve-file read scope or silently expand disclosure.
        selected_paths = {file['path'] for file in restored['files']}
        for name in ('source_searches', 'source_inspection_results'):
            for search in restored.get(name, []):
                if 'matches' in search:
                    matches = search['matches']
                    search['matches'] = [m for m in matches if m['path'] in selected_paths]
                    search['omitted_matches'] = len(matches)-len(search['matches'])
        restored['search_context_notice'] = 'Search excerpts outside selected files are omitted from external context; full original observations remain in native evidence. Read a needed named file before using its source.'
        return feature_step_source.compact(restored, limit=98304)
    return feature_step_source.compact(restored)


def recover_one(engine):
    with engine.connect() as c:
        jobs = list(c.execute(select(planning_jobs).where(planning_jobs.c.kind == 'feature_execution',
            planning_jobs.c.status == 'running', planning_jobs.c.lease.is_not(None),
            planning_jobs.c.payload['phase'].as_string().in_(['preparing_scope','preparing_independent_checks']))).mappings())
    def claim(job):
        return job['payload'].get('feature_scope_claim' if job['payload']['phase']=='preparing_scope' else 'feature_checks_claim')
    candidate = next((j for j in jobs if claim(j) and not alive(claim(j)['owner'])), None)
    if not candidate:
        return False
    with write_transaction(engine) as c:
        row = intake.require(c, planning_jobs, candidate['id'])
        if row['status'] != 'running' or row['lease'] != candidate['lease']:
            return True
        hold_interrupted(c, row)
    return True


def hold_interrupted(c, row):
    """Same disposition for dead owners and an exception ending our own operation.

    The timestamp closes the local attempt record; interrupted_unknown expressly
    does not establish provider settlement, known usage or permission to replay.
    """
    c.execute(model_attempts.update().where(model_attempts.c.id == row['lease']).values(
        outcome='interrupted_unknown', finished_at=intake.now()))
    c.execute(planning_jobs.update().where(planning_jobs.c.id == row['id']).values(
        status='failed', lease=None, lease_until=None, finished_at=intake.now(),
        payload={**row['payload'], 'phase':'held', 'unknown_scope_operation':row['lease']},
        message='Step preparation was interrupted. Its provider outcome and usage remain uncertain; the original claim is retained and will not be replayed.'))
    from workbench.feature_sessions import hold_if_idle
    hold_if_idle(c, row['payload']['feature_decision_id'], 'Step preparation was interrupted. Saved work and unknown usage remain; no automatic replay.')


class ParentStop:
    def __init__(self, engine, decision_id, outer):
        self.engine, self.decision_id, self.outer = engine, decision_id, outer

    def is_set(self):
        if self.outer is not None and self.outer.is_set():
            return True
        with self.engine.connect() as c:
            row = c.execute(select(task_sessions.c.state, task_sessions.c.status).where(
                task_sessions.c.id == self.decision_id)).mappings().first()
        return bool(not row or row['state'].get('cancel_requested') or row['status'] in {'cancelled','expired'})


def validation_feedback(record):
    diagnostics = record.get('diagnostics') or {}
    if diagnostics.get('generation_state') == 'not_started' and diagnostics.get('category') == 'input_budget':
        return ('The previous request exceeded the local input limit and was rejected before contacting the model. '
                'Its native rejection has been independently checked. This packet is compacted within the same limit; '
                'continue preparing the step from the supplied actual source definitions.')
    return record.get('failure')


def scope_packet(c, decision_id, task_id, feature_input, previous, *, remaining=None):
    """Change strategy after settled navigation failures instead of looping."""
    inspections = [record['inspection'] for record in previous if record.get('inspection')]
    value = packet(c, decision_id, task_id, feature_input, inspections=inspections)
    if previous and previous[-1].get('failure'):
        value['previous_validation_failure'] = validation_feedback(previous[-1])
    stalled = previous[-2:]
    navigation_errors = ('Every requested source window was already inspected.',
                         'Requested source range is outside the committed file.')
    inspected = [file['path'] for file in value['files'] if file.get('excerpts')]
    codex_ready = (remaining == 1 and bool(inspections)
        and feature_steps.parent(c, decision_id)[1]['scope']['provider'] in {'codex','claude','smart'})
    repeated_navigation = len(stalled) == 2 and all(record.get('confirmed_finished')
            and not record.get('inspection')
            and any(message in (record.get('failure') or '') for message in navigation_errors)
            for record in stalled)
    if ((codex_ready or repeated_navigation)
            and any(path.startswith(('!!!TwichUI/', 'TwichUI_Settings/')) for path in inspected)
            and any(re.fullmatch(r'tests/test_[A-Za-z0-9_-]+\.lua', path) for path in inspected)):
        value['coverage']['scope_decision_required'] = True
        value['scope_strategy'] = (
            'Source preparation selects paths and independent acceptance checks; it does not '
            'write the implementation or require a completed checking artifact. The coding '
            'adapter will receive the complete approved files for implementation and tests. '
            'Use the actual available '
            'definitions to propose a bounded implementation scope, or state the specific '
            'missing capability as a limitation. Further inspection is unavailable in '
            'this decision. A proposed scope is not an implementation or verification.')
    return value


def prepare_scope(engine, job_id, task_id, *, feature_input=None, adapter=None, stop_event=None):
    """One claimed operation; no implementation or automatic fallback capability."""
    from workbench.planning_provider import LocalProvider
    from workbench.planning_response import create_evidence
    from workbench.planning_worker import finish_attempt
    if stop_event is not None and stop_event.is_set():
        return False
    with engine.connect() as c:
        job = intake.require(c, planning_jobs, job_id)
        decision_id = job['payload']['feature_decision_id']
        row, permission = feature_steps.parent(c, decision_id)
        history = job['payload'].get('scope_history', [])
        previous = [r for r in history if r['task_id'] == task_id]
        from workbench.feature_scope_recovery import allowance
        maximum,scope_authorization,scope_deadline=allowance(c,job,task_id,permission)
        current_packet = scope_packet(c, decision_id, task_id, feature_input, previous,
            remaining=maximum-len(previous))
        if (job['kind'] != 'feature_execution' or job['status'] != 'queued'
                or job['intake_id'] != row['intake_id']
                or job['revision'] != permission['scope']['revision']
                or job['payload']['graph_binding'] != digest(permission['scope']['graph'])
                or job['payload']['scope_approval_id'] != permission['scope']['scope_approval_id']
                or job['payload'].get('phase') != 'prepare_scopes'
                or len(previous) >= maximum):
            return False
        if any(not r.get('confirmed_finished') for r in previous):
            from workbench.feature_scope_recovery import _confirmed
            if not _confirmed(c, job, previous, permission):
                return False
        route = permission['scope']['route']
        backend = permission['scope']['provider']
    if everyday.enabled():
        everyday.require_room(everyday.configuration(engine))
    if backend=='smart' and adapter is None:
        from workbench.strategy_waiting import preflight
        if job['payload'].get('ai_wait',{}).get('recheck_at',0)>time.time():
            return False
        if preflight(engine,dict(job),route,lambda:bool(stop_event and stop_event.is_set())):
            return True
        with engine.connect() as c:
            job=intake.require(c,planning_jobs,job_id)
    if backend == 'local' and adapter is None:
        from workbench.local_model_priority import planning_ready
        if not planning_ready():
            return False
    claim = dict(owner=owner_identity(), decision_id=decision_id, parent_binding=row['binding'],
        task_id=task_id, feature_input=feature_input, packet_sha256=digest(current_packet),
        scope_request_permission=scope_authorization)
    lease = str(uuid4())
    with write_transaction(engine) as c:
        active = intake.require(c, planning_jobs, job_id)
        feature_steps.parent(c, decision_id)
        actual_limit,actual_authorization,_=allowance(c,active,task_id,permission)
        if actual_limit!=maximum or actual_authorization!=scope_authorization:
            return False
        if active['status'] != 'queued' or active['payload'] != job['payload']:
            return False
        if c.execute(select(model_attempts.c.id).where(model_attempts.c.finished_at.is_(None))).first():
            return False
        c.execute(planning_jobs.update().where(planning_jobs.c.id == job_id).values(
            status='running', lease=lease, lease_until=(datetime.now(timezone.utc)+timedelta(seconds=150)).isoformat(),
            payload={**active['payload'], 'phase':'preparing_scope', 'feature_scope_claim':claim},
            message='Preparing the next step from committed source. No implementation has started.'))
        c.execute(model_attempts.insert().values(id=lease, job_id=job_id, task_id=job['intake_id'],
            purpose='feature_step_scope', source='workbench', backend=backend,
            requested_model=route['model'], requested_effort=route.get('effort'),
            policy_revision=route['policy_revision'], outcome='running', started_at=intake.now(),
            routing_reason='One approved read-only feature step scope: '+task_id+'. Permission '+scope_authorization+'. No fallback or implementation authority.'))
        c.execute(task_sessions.update().where(task_sessions.c.id == decision_id).values(
            status='active', finished_at=None,
            message='Preparing a read-only feature step scope. No implementation has started.'))
    used, evidence, result, failure, model, basis = adapter, None, None, None, None, None
    stopped = ParentStop(engine, decision_id, stop_event)
    attempted = False
    inspection = None
    unsupported = False
    try:
        if used is None:
            if backend == 'local':
                used = LocalProvider(stopped, ends_at=time.monotonic()+min(permission['budget']['scope_request_seconds'],(scope_deadline-datetime.now(timezone.utc)).total_seconds()))
            else:
                from workbench.provider_routes import external_planner
                used = external_planner(route, stopped.is_set)
                with engine.connect() as c:
                    linked = task_execution.snapshot(c, row['intake_id'], task_id, decision_id)
                    root, _ = feature_steps.source(c, linked, permission, feature_input)
                used.bind_source(current_packet, source_root=root)
        evidence = create_evidence(lease, feature_steps.StepScope,
            coverage=current_packet.get('coverage'),files=current_packet['files'])
        used.response_evidence = evidence
        if getattr(used,'strategy_control',False):
            from workbench.strategy_waiting import options
            used.bind_operation(engine,lease,**options(job))
            used.ends_at=time.monotonic()+min(permission['budget']['scope_request_seconds'],(scope_deadline-datetime.now(timezone.utc)).total_seconds())
        with engine.connect() as c:
            feature_steps.parent(c, decision_id)
        attempted = True
        raw, model, basis = used.generate(current_packet, feature_steps.StepScope, expected_model=route['model'])
        if evidence:
            evidence.snapshot('proposed-plan', raw)
        if (model != route['model'] and not getattr(used,'strategy_control',False)) or basis not in {'local','codex','claude','demo'}:
            raise ValueError('Step preparation did not use its saved model.')
        if len(json.dumps(raw, allow_nan=False).encode()) > 24000:
            raise ValueError('Proposed step scope exceeds its output bound.')
        parsed = feature_steps.StepScope.model_validate(raw)
        with engine.connect() as c:
            reread = scope_packet(c, decision_id, task_id, feature_input, previous,
                remaining=maximum-len(previous))
            if reread != current_packet:
                raise ValueError('The feature source or scope changed during preparation.')
            if parsed.inspection:
                from workbench import feature_step_source
                linked=task_execution.snapshot(c,row['intake_id'],task_id,decision_id)
                root,base=feature_steps.source(c,linked,permission,feature_input)
                requests=[item.model_dump(mode='json') for item in parsed.inspection]
                _,observations=feature_step_source.inspect(current_packet,root,base,requests)
                inspection=dict(requests=requests,result_binding=digest(observations),
                    feature_input_binding=digest(feature_input))
            else:
                if parsed.limitation.strip():
                    unsupported=True
                    raise ValueError('This step needs an unsupported source capability: '+parsed.limitation)
                inspected={f['path'] for f in current_packet['files'] if f['excerpts']}
                unseen=set(parsed.read_paths)-inspected
                if unseen:
                    names=[path for path in sorted(unseen) if feature_steps.eligible_path(path)]
                    raise ValueError('Step preparation needs committed-source inspection for selected paths: '+', '.join(names)[:650]+'. Request inspection before selecting them.')
                result = feature_steps.compile_scope(c, decision_id, task_id, raw, feature_input=feature_input)
    except (ValueError, OSError, HTTPException, PlanningUnavailable, KeyError, TypeError) as error:
        failure = 'Step preparation could not finish: '+(str(error)[:800] if not isinstance(error, HTTPException) else 'The saved feature changed.')
    finally:
        if evidence:
            evidence.close()
    diagnostics = copy.deepcopy(getattr(used, 'diagnostics', {}))
    confirmed = (diagnostics.get('generation_state') == 'finished'
        and diagnostics.get('accounting_state') == 'complete' and bool(diagnostics.get('usage')))
    if not confirmed:
        result,inspection=None,None
        failure=('The source-preparation request was rejected before generation at its input limit. Its original record and unrecorded usage remain; native evidence must qualify any separate continuation.'
            if diagnostics.get('generation_state')=='not_started' and diagnostics.get('category')=='input_budget' else
            'Read-only feature preparation has an unconfirmed outcome or usage. Exact evidence remains; no replay or implementation will start.')
    with write_transaction(engine) as c:
        active = intake.require(c, planning_jobs, job_id)
        if active['status'] != 'running' or active['lease'] != lease:
            return True  # Never let a late response mutate a newer journal owner.
        try:
            feature_steps.parent(c, decision_id)
        except (ValueError, OSError, HTTPException, KeyError, TypeError):
            failure, result, inspection = 'The feature was stopped or changed. Its late scope output is retained without implementation.', None, None
        if diagnostics.get('ai_wait'):
            from workbench.strategy_waiting import pause_preparation
            if pause_preparation(c,active,used,diagnostics['ai_wait'],decision_id,'feature_scope_claim'):
                return True
        finish_attempt(c, {**job, 'lease':lease}, used, model, basis, 'completed' if result or inspection else 'failed')
        record = dict(task_id=task_id, operation_id=lease, confirmed_finished=confirmed,
            scope_request_permission=scope_authorization,
            failure=failure, diagnostics=diagnostics, scope_binding=result['scope_binding'] if result else None,
            **({'inspection':inspection} if inspection else {}))
        parent = intake.require(c, task_sessions, decision_id)
        can_correct = bool((failure or inspection) and confirmed and not unsupported and not parent['state'].get('cancel_requested')
            and len(previous)+1 < maximum)
        payload = {**active['payload'], 'phase':'scope_ready' if result else 'prepare_scopes' if can_correct else 'held',
            'scope_history':history+[record], 'current_step_scope':result,
            'response_evidence_id':lease if evidence else None}
        message = 'The next feature step has a bounded source and check scope; implementation is not yet verified.' if result else 'Committed source inspection finished. Preparing the step from its actual definitions.' if inspection and can_correct else failure or 'Source inspection finished, but the permitted preparation requests were used before a complete scope was ready.'
        c.execute(planning_jobs.update().where(planning_jobs.c.id == job_id).values(
            status='queued' if result or can_correct else 'failed', lease=None, lease_until=None,
            finished_at=None if result or can_correct else intake.now(), payload=payload,
            message=message))
        if parent['state'].get('cancel_requested'):
            known_ended = confirmed or not attempted or diagnostics.get('generation_state') == 'not_started'
            c.execute(task_sessions.update().where(task_sessions.c.id == decision_id).values(
                status='cancelled' if known_ended else 'waiting_recovery',
                finished_at=intake.now() if known_ended else None,
                message='Feature stopped. The read-only request settled; no implementation started.' if known_ended else
                    'The feature is stopping. Its provider outcome and usage remain uncertain; no replay or implementation will start.'))
        elif result or can_correct:
            c.execute(task_sessions.update().where(task_sessions.c.id == decision_id).values(
                status='pending', finished_at=None, message='Feature step scope prepared; implementation has not yet started.'))
        if not result and not can_correct:
            from workbench.feature_sessions import hold_if_idle
            hold_if_idle(c, decision_id, message)
    return True
