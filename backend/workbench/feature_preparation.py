"""Bounded read-only step scopes in the existing feature planning journal.

The same parent permission, provider adapters, model accounting and source owner
are used throughout. A provider claim never expires into automatic replay.
"""
import copy
import json
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


def packet(c, decision_id, task_id, feature_input):
    row, permission = feature_steps.parent(c, decision_id)
    linked = task_execution.snapshot(c, row['intake_id'], task_id, decision_id)
    root, base = feature_steps.source(c, linked, permission, feature_input)
    # Reuse the existing bounded local source selection, then read the exact
    # checked predecessor's versions. No live workspace or downloaded addon runs.
    selected = feature_planning.research(linked['original'])
    files, evidence = [], []
    for item in selected['files']:
        bound, raw = feature_steps._file(root, base, item['path'])
        lines = raw.decode().splitlines()
        windows = []
        for window in item['excerpts']:
            start = max(1, min(window['line'], len(lines)))
            text = '\n'.join(lines[start-1:start+34])[:1000]
            windows.append(dict(line=start, text=text))
        files.append(dict(path=bound['path'], sha256=bound['sha256'], excerpts=windows))
        evidence.append(dict(id='step-source-'+str(len(evidence)), path=bound['path'],
            sha256=bound['sha256'], revision=base))
    return dict(original=linked['original'], task=dict(title=linked['task']['title'],
        purpose=linked['task']['purpose'], acceptance=linked['task']['acceptance']),
        approved_scope=linked['approved_scope'], exclusions=linked['proposal_review']['content']['exclusions'],
        base=base, files=files, evidence=evidence,
        indexed_acceptance=[dict(index=i, requirement=s) for i,s in enumerate(linked['task']['acceptance'])],
        source_note='Partial committed source excerpts; absent files and game behavior remain unknown.')


def recover_one(engine):
    with engine.connect() as c:
        jobs = list(c.execute(select(planning_jobs).where(planning_jobs.c.kind == 'feature_execution',
            planning_jobs.c.status == 'running', planning_jobs.c.lease.is_not(None),
            planning_jobs.c.payload['phase'].as_string() == 'preparing_scope')).mappings())
    candidate = next((j for j in jobs if j['payload'].get('feature_scope_claim')
        and not alive(j['payload']['feature_scope_claim']['owner'])), None)
    if not candidate:
        return False
    with write_transaction(engine) as c:
        row = intake.require(c, planning_jobs, candidate['id'])
        if row['status'] != 'running' or row['lease'] != candidate['lease']:
            return True
        c.execute(model_attempts.update().where(model_attempts.c.id == row['lease']).values(
            outcome='interrupted_unknown', finished_at=intake.now()))
        c.execute(planning_jobs.update().where(planning_jobs.c.id == row['id']).values(
            status='failed', lease=None, lease_until=None, finished_at=intake.now(),
            payload={**row['payload'], 'phase':'held', 'unknown_scope_operation':row['lease']},
            message='Step preparation was interrupted. Its provider outcome and usage remain uncertain; the original claim is retained and will not be replayed.'))
        from workbench.feature_sessions import hold_if_idle
        hold_if_idle(c, row['payload']['feature_decision_id'], 'Step preparation was interrupted. Saved work and unknown usage remain; no automatic replay.')
    return True


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
        current_packet = packet(c, decision_id, task_id, feature_input)
        history = job['payload'].get('scope_history', [])
        previous = [r for r in history if r['task_id'] == task_id]
        if (job['kind'] != 'feature_execution' or job['status'] != 'queued'
                or job['intake_id'] != row['intake_id']
                or job['revision'] != permission['scope']['revision']
                or job['payload']['graph_binding'] != digest(permission['scope']['graph'])
                or job['payload']['scope_approval_id'] != permission['scope']['scope_approval_id']
                or job['payload'].get('phase') != 'prepare_scopes'
                or len(previous) >= permission['budget']['scope_attempts_per_step']):
            return False
        if any(not r.get('confirmed_finished') for r in previous):
            return False
        route = permission['scope']['route']
        backend = permission['scope']['provider']
    if everyday.enabled():
        everyday.require_room(everyday.configuration(engine))
    if backend == 'local' and adapter is None:
        from workbench.local_model_priority import planning_ready
        if not planning_ready():
            return False
    if previous:
        current_packet['previous_validation_failure'] = previous[-1]['failure']
    claim = dict(owner=owner_identity(), decision_id=decision_id, parent_binding=row['binding'],
        task_id=task_id, feature_input=feature_input, packet_sha256=digest(current_packet))
    lease = str(uuid4())
    with write_transaction(engine) as c:
        active = intake.require(c, planning_jobs, job_id)
        feature_steps.parent(c, decision_id)
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
            routing_reason='One parent-approved read-only feature step scope: '+task_id+'. No fallback or implementation authority.'))
        c.execute(task_sessions.update().where(task_sessions.c.id == decision_id).values(
            status='active', finished_at=None,
            message='Preparing a read-only feature step scope. No implementation has started.'))
    used, evidence, result, failure, model, basis = adapter, None, None, None, None, None
    stopped = ParentStop(engine, decision_id, stop_event)
    attempted = False
    try:
        if used is None:
            if backend == 'local':
                used = LocalProvider(stopped, ends_at=time.monotonic()+permission['budget']['scope_request_seconds'])
            else:
                from workbench.codex_provider import Planner
                used = Planner(route, stopped.is_set)
                used.bind_source(current_packet)
        evidence = create_evidence(lease, feature_steps.StepScope, files=current_packet['files'])
        used.response_evidence = evidence
        with engine.connect() as c:
            feature_steps.parent(c, decision_id)
        attempted = True
        raw, model, basis = used.generate(current_packet, feature_steps.StepScope, expected_model=route['model'])
        if evidence:
            evidence.snapshot('proposed-plan', raw)
        if model != route['model'] or basis not in {'local','codex','demo'}:
            raise ValueError('Step preparation did not use its saved model.')
        if len(json.dumps(raw, allow_nan=False).encode()) > 24000:
            raise ValueError('Proposed step scope exceeds its output bound.')
        parsed = feature_steps.StepScope.model_validate(raw)
        if not set(parsed.read_paths) <= {f['path'] for f in current_packet['files']}:
            raise ValueError('Step preparation selected a source file outside its inspected packet.')
        with engine.connect() as c:
            reread = packet(c, decision_id, task_id, feature_input)
            if previous:
                reread['previous_validation_failure'] = previous[-1]['failure']
            if reread != current_packet:
                raise ValueError('The feature source or scope changed during preparation.')
            result = feature_steps.compile_scope(c, decision_id, task_id, raw, feature_input=feature_input)
    except (ValueError, OSError, HTTPException, PlanningUnavailable, KeyError, TypeError) as error:
        failure = 'Step preparation could not finish: '+(str(error)[:800] if not isinstance(error, HTTPException) else 'The saved feature changed.')
    finally:
        if evidence:
            evidence.close()
    diagnostics = copy.deepcopy(getattr(used, 'diagnostics', {}))
    confirmed = (diagnostics.get('generation_state') == 'finished'
        and diagnostics.get('accounting_state') == 'complete' and bool(diagnostics.get('usage')))
    with write_transaction(engine) as c:
        active = intake.require(c, planning_jobs, job_id)
        if active['status'] != 'running' or active['lease'] != lease:
            return True  # Never let a late response mutate a newer journal owner.
        try:
            feature_steps.parent(c, decision_id)
        except (ValueError, OSError, HTTPException, KeyError, TypeError):
            failure, result = 'The feature was stopped or changed. Its late scope output is retained without implementation.', None
        finish_attempt(c, {**job, 'lease':lease}, used, model, basis, 'completed' if result else 'failed')
        record = dict(task_id=task_id, operation_id=lease, confirmed_finished=confirmed,
            failure=failure, diagnostics=diagnostics, scope_binding=result['scope_binding'] if result else None)
        parent = intake.require(c, task_sessions, decision_id)
        can_correct = bool(failure and confirmed and not parent['state'].get('cancel_requested')
            and len(previous)+1 < permission['budget']['scope_attempts_per_step'])
        payload = {**active['payload'], 'phase':'scope_ready' if result else 'prepare_scopes' if can_correct else 'held',
            'scope_history':history+[record], 'current_step_scope':result,
            'response_evidence_id':lease if evidence else None}
        c.execute(planning_jobs.update().where(planning_jobs.c.id == job_id).values(
            status='queued' if result or can_correct else 'failed', lease=None, lease_until=None,
            finished_at=None if result or can_correct else intake.now(), payload=payload,
            message='The next feature step has a bounded source and check scope; implementation is not yet verified.' if result else failure))
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
            hold_if_idle(c, decision_id, failure)
    return True
