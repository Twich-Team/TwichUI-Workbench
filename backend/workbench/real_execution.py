"""One explicitly granted real task. Existing worker/transaction/accounting ownership."""
from datetime import datetime, timedelta, timezone
from workbench import worker_operation as operation

import hashlib
import copy
import json
import os
import time
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, func, inspect, case
from sqlalchemy.exc import OperationalError
from pydantic import BaseModel
from typing import Literal
from uuid import UUID

from workbench.db import (real_execution_grants as grants, real_execution_requests as requests,
                          model_attempts, write_transaction)
from workbench.service import now

BASE = 'f48b2e0bbdf966293695de8b1435b0e7e4372224'
SOURCE = '/home/rich/twichui-pilot/checkouts/base-f48b2e0bb'
PATHS = ('!!!TwichUI/chronicle/Data.lua', '!!!TwichUI/chronicle/Recorder.lua',
         'tests/test_chronicle_journey.lua')
TASK = 'chronicle-finite-played-time-v1'
DECODING = dict(reasoning_effort='none', temperature=0.2)
WORKFLOW = dict(initial_read_calls=4, first_edit_tools=['replace_text'], edit_tools=['replace_text','run_check'],
                context_refresh='one turn after an exact-match correction or failed check',
                final_check='frozen independent candidate checks; ordinary failure returns feedback within this attempt')
LIMITS = dict(corrective_read_errors=2, corrective_tool_errors=2, turns=20, request_tokens=2500, total_tokens=24000,
              response_sse_bytes=2<<20, response_line_bytes=64<<10, response_assembled_bytes=256<<10,
              read_chars=45000, implementation_s=1200, acceptance_s=2400, request_s=120,
              memory_bytes=6 << 30, pids=64, cpu_percent=200, changed_lines=150,
              patch_bytes=65536, logs_bytes=8 << 20, run_bytes=64 << 20)


class StopRequest(BaseModel):
    model_config = {'extra':'forbid'}
    command_id: UUID


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def contract():
    return dict(task_id=TASK, title='Chronicle: safely reject invalid played-time measurements',
                repository='Twich-Team/TwichUI', source=SOURCE, base=BASE, paths=list(PATHS),
                provider='local', model='pilot-qwen38-27b', limits=LIMITS, decoding=DECODING, workflow=WORKFLOW,
                publication_allowed=False, fallback_allowed=False)


def issue(engine, authorization, provenance, activation, *, task=None):
    """Trusted owner-instruction ingress, never a public approval API or sample receipt.

    activation is a runtime-owned object with fresh live checks, not client booleans.
    All capability gates must pass before starting the 60 minute clock.
    """
    if getattr(activation,'operation_kind',None)=='feature-verification-v1':
        from workbench.feature_verification import issue as local_checks
        return local_checks(engine,authorization,provenance,activation,task=task)
    if not authorization.strip() or not provenance.strip():
        raise ValueError('Exact owner instruction and provenance required')
    gates = activation.preflight()
    if not gates or any(g['status'] != 'passed' for g in gates):
        raise ValueError('Activation gates did not pass; no grant issued')
    issued = datetime.now(timezone.utc)
    expires = issued+timedelta(minutes=60)
    deadline = getattr(activation, 'grant_deadline', None)
    if deadline is not None:
        if not isinstance(deadline, datetime) or deadline.utcoffset() is None or deadline <= issued:
            raise ValueError('Current aware grant deadline required')
        expires = min(expires, deadline)
    chosen = getattr(activation,'execution_contract',contract())
    if chosen != contract():
        from workbench.task_budget import valid_contract
        if task is None or not valid_contract(chosen):
            raise ValueError('Registered task-bound local recipe required')
    snapshot = dict(contract=chosen, owner_instruction=authorization,
                    authorization_sha256=hashlib.sha256(authorization.encode()).hexdigest(),
                    provenance=provenance, gates=gates, runtime=activation.identity(),
                    grant_deadline=deadline.isoformat() if deadline else None)
    if hasattr(activation, 'recovery_identity'):
        snapshot['recovery'] = activation.recovery_identity()
    checked_input = getattr(activation, 'feature_input', None)
    prepared_task = None
    if checked_input is not None:
        if not isinstance(checked_input, dict) or task is None:
            raise ValueError('A checked feature input requires its current task and explicit execution budget.')
        checked_input = copy.deepcopy(checked_input)
        from workbench import task_execution
        with engine.connect() as c:
            prepared_task = task_execution.snapshot(c, **task)
            reason = task_execution.prerequisite_reason(c, prepared_task, checked_input)
        if (reason or chosen['source'] != checked_input.get('source')
                or chosen['base'] != checked_input.get('commit')):
            raise ValueError(reason or 'Execution source differs from the checked feature input.')
        snapshot['feature_input'] = checked_input
    binding = digest(snapshot)
    row = dict(id=str(uuid4()), snapshot=snapshot, binding=binding,
               issued_at=issued.isoformat(), expires_at=expires.isoformat())
    with write_transaction(engine) as c:
        if task is not None:
            from workbench.task_execution import snapshot as task_snapshot
            snapshot['workflow_task'] = task_snapshot(c, task['intake_id'], task['task_id'], task['approval_id'])
            if prepared_task is not None and snapshot['workflow_task'] != prepared_task:
                raise ValueError('The task changed after checked-source validation. No grant was issued.')
            from workbench.continuing_repair import controls_for
            controls=controls_for(snapshot['workflow_task']['approval'])
            if controls:
                # The original recipe keeps its baseline defaults. This fresh
                # grant separately binds the engineering settings in the newly
                # recorded continuing decision; old grants are never upgraded.
                snapshot['operational_controls']=controls
            if (snapshot['workflow_task']['approval']['payload'].get('run_budget')
                    or snapshot['workflow_task']['approval']['payload'].get('feature_run')):
                from workbench.task_budget import permission
                from workbench.db import proposal_decisions
                approval = c.execute(select(proposal_decisions).where(proposal_decisions.c.id==task['approval_id'])).mappings().one()
                selected = (dict(task_id=task['task_id'], offer_id=getattr(activation, 'feature_offer_id', None))
                    if approval['payload'].get('feature_run') else {})
                snapshot['task_budget'] = permission(c, approval, **selected)
                if snapshot['task_budget']['offer']['snapshot']['specification']['contract'] != snapshot['contract']:
                    raise ValueError('Prepared runtime and approved recipe differ')
                if snapshot['task_budget'].get('feature_task_id'):
                    from workbench import feature_attempts
                    feature_attempts.context(activation,
                        snapshot['task_budget']['offer']['snapshot']['feature_step'].get('recovery'))
            if chosen != contract() and not snapshot.get('task_budget'):
                raise ValueError('The new recipe requires an explicit task budget')
            row['binding'] = digest(snapshot)
        if snapshot.get('task_budget'):
            query = select(grants).where(grants.c.snapshot['task_budget']['decision_id'].as_string()==snapshot['task_budget']['decision_id'])
            if snapshot['task_budget'].get('feature_task_id'):
                query = query.where(grants.c.snapshot['task_budget']['offer']['id'].as_string()==snapshot['task_budget']['offer']['id'])
            prior = c.execute(query).mappings().first()
            if prior:
                if prior['snapshot'] != snapshot:
                    raise ValueError('This task budget already owns one execution grant; no replacement')
                return dict(prior)
        # Exact instruction may never issue another grant after the first attempt/expiry.
        for prior in c.execute(select(grants)).mappings():
            if prior['snapshot']['authorization_sha256'] == snapshot['authorization_sha256']:
                if prior['snapshot'] != snapshot:
                    raise ValueError('Authorization already recorded with different immutable inputs')
                return dict(prior)
        c.execute(grants.insert().values(**row))
    return row


def renew_waiting_grant(c, row, previous, snapshot, identity, issued, expires):
    """Grant-owner ingress for an already validated policy nondispatch suffix.

    The request keeps its identity and original submission payload. The old grant
    and its snapshot remain historical; a separately journaled operation owns the
    next bounded execution window. Counters, source and provider choices cannot change.
    """
    from workbench import task_execution
    current=c.execute(select(requests).where(requests.c.id==row['id'])).mappings().one()
    if (current['status']!='waiting_ai' or current['fence']!=row['fence'] or current['grant_id']!=previous['id']
            or previous['revoked_at'] or previous['binding']!=digest(previous['snapshot'])
            or datetime.fromisoformat(previous['expires_at'])>issued
            or not 0<(expires-issued).total_seconds()<=2400):
        raise ValueError('Only a settled expired policy operation can receive a fresh grant')
    changed={'policy_operation','grant_deadline','owner_instruction','authorization_sha256','provenance'}
    if {k:v for k,v in snapshot.items() if k not in changed}!={k:v for k,v in previous['snapshot'].items() if k not in changed}:
        raise ValueError('Fresh operation changed original source, work, model or accounting')
    fresh=dict(id=identity,snapshot=snapshot,binding=digest(snapshot),issued_at=issued.isoformat(),expires_at=expires.isoformat(),revoked_at=None)
    c.execute(grants.insert().values(**fresh))
    c.execute(grants.update().where(grants.c.id==previous['id']).values(revoked_at=issued.isoformat()))
    c.execute(requests.update().where(requests.c.id==row['id']).values(grant_id=identity))
    task_execution.validate(c,fresh)
    if not valid(fresh):
        raise ValueError('The fresh operation did not establish valid bounded authority')
    return fresh


def contract_matches(snapshot):
    try:
        policy = snapshot.get('task_budget')
        if policy:
            from workbench.task_budget import TaskBudget, valid_contract
            TaskBudget.model_validate(policy['budget'])
            if (datetime.now(timezone.utc) >= datetime.fromisoformat(snapshot.get('policy_operation',{}).get('expires_at',policy['expires_at'])) or
                    policy['offer']['fingerprint'] != digest(policy['offer']['snapshot']) or
                    snapshot['contract'] != policy['offer']['snapshot']['specification']['contract']):
                return False
            return valid_contract(snapshot['contract'])
        return snapshot['contract'] == contract()
    except (ValueError,TypeError,KeyError):
        return False


def valid(grant):
    if grant and grant['snapshot'].get('operation_kind')=='feature-verification-v1':
        from workbench.feature_verification import valid as local_checks
        return local_checks(grant)
    return (grant is not None and not grant['revoked_at'] and
            grant['binding'] == digest(grant['snapshot']) and contract_matches(grant['snapshot']) and
            datetime.now(timezone.utc) < datetime.fromisoformat(grant['expires_at']) <=
            datetime.fromisoformat(grant['issued_at'])+timedelta(minutes=60) and
            (not grant['snapshot'].get('grant_deadline') or
             datetime.fromisoformat(grant['expires_at']) <=
             datetime.fromisoformat(grant['snapshot']['grant_deadline'])))


def submit(engine, grant_id, command_id):
    from uuid import UUID
    UUID(command_id)
    with write_transaction(engine) as c:
        grant = c.execute(select(grants).where(grants.c.id == grant_id)).mappings().first()
        payload = dict(grant_id=grant_id, command_id=command_id)
        linked = grant['snapshot'].get('workflow_task') if grant else None
        if linked:
            payload['workflow_task'] = dict(intake_id=linked['intake_id'], task_id=linked['task']['id'],
                                            binding=digest(linked))
        prior = c.execute(select(requests).where(requests.c.id == command_id)).mappings().first()
        if prior:
            if prior['payload'] != payload:
                raise ValueError('Conflicting command reuse')
            return dict(prior)
        if not valid(grant):
            raise ValueError('Current real execution grant required')
        from workbench import task_execution
        task_execution.validate(c, grant)
        if linked:
            existing = c.execute(select(requests.c.id).join(grants, requests.c.grant_id == grants.c.id).where(
                grants.c.snapshot['workflow_task']['task']['id'].as_string() == linked['task']['id'],
                grants.c.snapshot['workflow_task']['intake_id'].as_string() == linked['intake_id'],
                requests.c.status.in_(['pending','waiting_ai','running','checking','checked','cancel_requested']))).first()
            if existing:
                raise ValueError('This task already has active or checked work; no duplicate execution')
        if c.execute(select(requests.c.id).where(requests.c.grant_id == grant_id)).first():
            raise ValueError('One request/attempt per grant; no replacement attempt')
        row = dict(id=command_id, grant_id=grant_id, payload=payload, status='pending',
                   message='Task saved. Waiting for execution safety checks.' if linked else
                           'Real request saved. No inference has started.', created_at=now())
        c.execute(requests.insert().values(**row))
    return row


def owner_identity():
    proc = Path('/proc')/str(os.getpid())
    return dict(pid=os.getpid(), boot=Path('/proc/sys/kernel/random/boot_id').read_text(),
                start=(proc/'stat').read_text().split(') ', 1)[1].split()[19])


def alive(owner):
    try:
        proc = Path('/proc')/str(owner['pid'])
        fields = (proc/'stat').read_text().split(') ', 1)[1].split()
        return (owner['boot'] == Path('/proc/sys/kernel/random/boot_id').read_text() and
                fields[19] == owner['start'] and fields[0] != 'Z')
    except (FileNotFoundError, KeyError):
        return False


def recover(engine, runtime):
    """No inference replay. Runtime verifies descendant cleanup before recovery."""
    with engine.connect() as c:
        rows = c.execute(select(requests).where(requests.c.status.in_(['running', 'checking']))).mappings().all()
    worked = False
    for row in rows:
        owned = row['result']['owner'] == owner_identity()
        owner_live = alive(row['result']['owner'])
        if owner_live and not owned:
            continue
        with engine.connect() as c:
            grant = c.execute(select(grants).where(grants.c.id == row['grant_id'])).mappings().one()
        if not recovery_matches(runtime, grant):
            continue
        completed = runtime.recover_completion(row, grant) if owner_live and hasattr(runtime, 'recover_completion') else None
        if not completed and owner_live and hasattr(runtime, 'recover_outcome'):
            completed = runtime.recover_outcome(row, grant)
        if owner_live and not completed:
            continue  # A live owner with no sealed completion is never reclaimed.
        if not cleanup_confirmed(runtime, row):
            worked = hold_cleanup(engine, row) or worked
            continue
        if not owner_live and hasattr(runtime, 'recover_completion'):
            completed = runtime.recover_completion(row, grant)
        if not owner_live and not completed and hasattr(runtime, 'recover_outcome'):
            completed = runtime.recover_outcome(row, grant)
        observations = {} if completed else retain_usage(failure_observations(runtime, row['id'], row['fence']), row['result'])
        at = completed['completed_at'] if completed else now()
        value = completed['result'] if completed else {**row['result'], **observations}
        if not completed and 'delivery' in value:
            value = {**value, 'delivery': {'state': 'cleanup_confirmed'}}
        status = completed.get('status', 'checked') if completed else 'interrupted'
        with write_transaction(engine) as c:
            result = c.execute(requests.update().where(requests.c.id == row['id'],
                requests.c.fence == row['fence'], requests.c.status.in_(['running', 'checking']))
                .values(status=status, finished_at=at, result=value,
                        message=('Verified completion recovered. Nothing was rerun or published.' if status == 'checked' else
                                 'Failure outcome recovered. Evidence and known usage retained; no attempt will restart.') if completed else
                                'Interrupted attempt consumed. No inference will be replayed.'))
            if result.rowcount:
                worked = True
                c.execute(model_attempts.update().where(model_attempts.c.execution_id == row['id'], model_attempts.c.id == row['fence'])
                          .values(outcome=status if completed else 'interrupted_unknown', finished_at=at,
                                  **({'verification': value.get('verification'), 'usage': value.get('usage')} if completed else
                                     ({'usage': observations['usage']} if 'usage' in observations else {}))))
    with engine.connect() as c:
        cancelled = c.execute(select(requests).where(requests.c.status == 'cancel_requested')).mappings().all()
    for row in cancelled:
        with engine.connect() as c:
            grant = c.execute(select(grants).where(grants.c.id == row['grant_id'])).mappings().one()
        if not recovery_matches(runtime, grant):
            continue
        owner = row['result']['owner']
        if alive(owner) and (owner != owner_identity() or not row['result'].get('handler_returned')):
            continue
        worked = settle_cancellation(engine, runtime, row['id'], row['fence']) or worked
    return worked


def recovery_matches(runtime, grant):
    if hasattr(runtime, 'owns_recovery'):
        return runtime.owns_recovery(grant)
    return 'recovery' not in grant['snapshot']  # Legacy deterministic adapters only.


def cleanup_confirmed(runtime, row):
    try:
        return runtime.cleanup(row) is True
    except Exception:
        return False


def retain_usage(evidence, prior):
    """Keep the strongest already-recorded observation, with its original provenance.

    This selects evidence from this same fenced attempt; it never adds estimates,
    combines different attempts or changes unavailable values into zero.
    """
    rank = {'unavailable': 0, 'partial': 1, 'complete': 2}
    old, new = (prior or {}).get('usage'), evidence.get('usage')
    if old and (not new or rank.get(old.get('status'), -1) > rank.get(new.get('status'), -1)):
        return {**evidence, 'usage': old}
    return evidence


def hold_cleanup(engine, row, outcome=None):
    """Hold the existing lane, never replace cancellation or a different fence."""
    with write_transaction(engine) as c:
        current = c.execute(select(requests).where(requests.c.id == row['id'])).mappings().one()
        if current['fence'] != row['fence'] or current['status'] not in {'running', 'checking'}:
            return False
        result = {**current['result'], **(outcome or {}),
                  'delivery': {'state': 'waiting_cleanup'}}
        if outcome is not None:
            result['handler_returned'] = True
        if result == current['result']:
            return False
        c.execute(requests.update().where(requests.c.id == row['id']).values(result=result,
            message='Waiting for confirmed cleanup. Saved evidence is retained; no new execution can start.'))
        return True


def cancel(engine, identity, command_id):
    """A durable stop request, never permission to restart or revoke unrelated work."""
    from uuid import UUID
    UUID(command_id)
    with write_transaction(engine) as c:
        row = c.execute(select(requests).where(requests.c.id == identity)).mappings().first()
        if not row or not row['payload'].get('workflow_task'):
            raise HTTPException(404, 'Connected task run not found.')
        result = row['result'] or {}
        prior = result.get('stop_command')
        if prior:
            if prior != command_id:
                raise HTTPException(409, 'A different stop request is already recorded.')
            return dict(id=identity,status=row['status'])
        if row['status'] not in {'pending','waiting_ai','running','checking'}:
            raise HTTPException(409, 'This run has already finished; its history is retained.')
        queued = row['status'] in {'pending','waiting_ai'}
        suspended=row['status']=='waiting_ai'
        c.execute(requests.update().where(requests.c.id == identity).values(
            status='cancelled' if queued else 'cancel_requested',
            finished_at=now() if queued else None,
            message='Stopped while waiting. Completed work and consumption are retained.' if suspended else 'Stopped before execution. No attempt was consumed.' if queued else
                    'Stopping this run. Waiting for confirmed cleanup; no replacement will start.',
            result={**result,'stop_command':command_id, **({'inference_ran':False} if queued and not suspended else {})}))
    return dict(id=identity,status='cancelled' if queued else 'cancel_requested')


def settle_cancellation(engine, runtime, identity, fence, observations=None):
    with engine.connect() as c:
        row = c.execute(select(requests).where(requests.c.id == identity)).mappings().one()
    if row['status'] != 'cancel_requested' or row['fence'] != fence:
        return
    safe = cleanup_confirmed(runtime, row)
    evidence = observations if observations is not None else failure_observations(runtime, identity, fence)
    if observations is None:
        with engine.connect() as c:
            grant = c.execute(select(grants).where(grants.c.id == row['grant_id'])).mappings().one()
        if recovery_matches(runtime, grant):
            sealed = runtime.recover_completion(row, grant) if hasattr(runtime, 'recover_completion') else None
            if not sealed and hasattr(runtime, 'recover_outcome'):
                sealed = runtime.recover_outcome(row, grant)
            if sealed:
                evidence = retain_usage(evidence, sealed['result'])
    evidence = retain_usage(evidence, row['result'])
    result = {**row['result'], **evidence, 'handler_returned':True, 'verification':'not_completed'}
    if safe and 'delivery' in result:
        result['delivery'] = {'state':'cleanup_confirmed'}
    with write_transaction(engine) as c:
        changed = c.execute(requests.update().where(requests.c.id == identity, requests.c.fence == fence,
            requests.c.status == 'cancel_requested').values(status='cancelled' if safe else 'cancel_requested',
            message='Run stopped. Cleanup confirmed; nothing will restart or publish.' if safe else
                    'Stop requested. Cleanup is not confirmed; the execution lane remains held.',
            result=result, finished_at=now() if safe else None))
        if changed.rowcount and safe:
            c.execute(model_attempts.update().where(model_attempts.c.execution_id == identity, model_attempts.c.id == fence).values(
                outcome='cancelled',verification='not_completed',usage=result.get('usage'),finished_at=now()))
    return bool(changed.rowcount)


def failure_observations(runtime, identity, fence):
    if not hasattr(runtime, 'failure_evidence'):
        return {}
    try:
        return runtime.failure_evidence(identity, fence)
    except Exception:
        return dict(usage=dict(status='unavailable', values=None,
                    provenance={'method': 'current-attempt observations unavailable or invalid'}))


@operation.isolated
def process_one(engine, runtime=None, stop_event=None):
    if getattr(runtime,'operation_kind',None)=='feature-verification-v1':
        from workbench.feature_verification import process_one as local_checks
        return local_checks(engine,runtime,stop_event)
    if runtime is None:
        root = os.environ.get('WORKBENCH_REAL_EXECUTION_ROOT')
        recovery_root = os.environ.get('WORKBENCH_REAL_RECOVERY_ROOT')
        if not root and not recovery_root:
            return False  # Disabled on all existing deployments, including 9487.
        from workbench.real_workspace import Runtime
        runtime = Runtime(Path(root or recovery_root), stop_event=stop_event)
        if not root:
            return recover(engine, runtime)  # Delivery/cleanup only; no activation or dispatch.
    recover(engine, runtime)
    if stop_event is not None and stop_event.is_set():
        return False
    from workbench import task_execution, task_budget
    task_budget.authorize_one(engine, runtime)
    task_execution.dispatch_one(engine)
    with engine.connect() as c:
        candidates = c.execute(select(requests).where(requests.c.status.in_(['pending','waiting_ai']))
                            .order_by(requests.c.created_at, requests.c.id).limit(64)).mappings().all()
        pending = grant = None
        for candidate in candidates:
            owner_grant = c.execute(select(grants).where(grants.c.id == candidate['grant_id'])).mappings().one()
            if recovery_matches(runtime, owner_grant):
                pending, grant = candidate, owner_grant
                break
        if not pending:
            return False
    operation.selected(requests, pending)
    resuming=pending['status']=='waiting_ai'
    if resuming:
        if pending['result'].get('ai_wait',{}).get('recheck_at',0)>time.time():
            return False
        from workbench.smart_coding_wait import load
        try:
            runtime.ai_resume=load(runtime,pending,grant)
            runtime.ai_wait_since=runtime.ai_resume['waiting_since']
            from workbench.manual_ai import effective_for_run
            with engine.connect() as current:
                manual=effective_for_run(current,pending,grant,runtime.ai_resume)
            runtime.ai_override=manual['route'] if manual else None
            runtime.ai_manual_authorization_sha256=digest(manual) if manual else None
        except (ValueError,OSError,KeyError,TypeError):
            return False  # Changed/uncertain saved work cannot resume.
    if not valid(grant):
        # No expensive service observation or attempt reservation after expiry.
        with write_transaction(engine) as c:
            c.execute(requests.update().where(requests.c.id == pending['id'], requests.c.status.in_(['pending','waiting_ai']))
                .values(status='blocked',finished_at=now(),result={**(pending['result'] or {}),'resume_blocked_before_dispatch':True} if resuming else {'inference_ran':False},
                        message='Execution permission expired, changed or was withdrawn. Nothing started; review is needed.'))
        return True
    # Expensive source/provider/capability probes are always outside database locks.
    try:
        gates = runtime.preflight()
    except OSError:
        gates = [dict(name='temporary runtime availability', status='blocked',
                      evidence='Runtime observation unavailable. No model has started.')]
    ready = bool(gates) and all(g['status'] == 'passed' for g in gates)
    identity_matches = runtime.identity() == grant['snapshot']['runtime']
    ready = ready and identity_matches and not (stop_event is not None and stop_event.is_set())
    with write_transaction(engine) as c:
        row = c.execute(select(requests).where(requests.c.id == pending['id'])).mappings().one()
        current = c.execute(select(grants).where(grants.c.id == row['grant_id'])).mappings().one()
        if row['status'] != ('waiting_ai' if resuming else 'pending'):
            return False
        from workbench import everyday

        linked = current['snapshot'].get('workflow_task')
        route = None
        try:
            if linked and everyday.enabled():
                try:
                    from workbench import provider_routes

                    route = provider_routes.run_selection(c, linked)
                    if route != getattr(runtime, 'codex_selection', None):
                        raise ValueError('The prepared runtime differs from the reviewed provider route.')
                except ValueError as error:
                    raise task_execution.Waiting(str(error)) from None
            task_execution.validate(c, current)
            task_ready = True
            task_message = ''
        except task_execution.Waiting as error:
            task_ready = False
            task_message = str(error)
        if linked and valid(current) and identity_matches and task_ready and not ready:
            blocked = [g['name'] for g in gates if g['status'] != 'passed']
            c.execute(requests.update().where(requests.c.id == row['id']).values(
                message='Waiting for execution safety checks: '+(', '.join(blocked)[:600] or 'activation evidence unavailable')+'. No model has started.',
                result={**(row['result'] or {}), 'gates':gates,**({} if resuming else {'inference_ran':False})}))
            return False  # Same unconsumed request will be reconsidered; no attempt/retry is created.
        if not valid(current) or not ready or not task_ready:
            c.execute(requests.update().where(requests.c.id == row['id']).values(
                status='blocked', finished_at=now(), message=(task_message or
                    'The execution permission or prepared environment is no longer current. Nothing started.') if linked else
                    'Activation, current task approval or execution permission failed. No inference ran.',
                result={**(row['result'] or {}),'gates':gates,**({} if resuming else {'inference_ran':False})}))
            return True
        # Globally one active real attempt; concurrent writers serialize here.
        if c.execute(select(requests.c.id).where(requests.c.status.in_(['running', 'checking', 'cancel_requested']))).first():
            if linked:
                c.execute(requests.update().where(requests.c.id == row['id']).values(
                    message='Waiting for another run or its cleanup to finish. No model has started.'))
            return False
        fence = row['fence'] if resuming else str(uuid4())
        owner = owner_identity()
        c.execute(requests.update().where(requests.c.id == row['id']).values(
            status='running',fence=fence,started_at=row['started_at'] if resuming else now(), message='Implementing with Codex in isolation.' if route else 'Implementing in isolation.',
            result={'owner':owner,'inference_ran':False,**(dict(ai_resume_sequence=runtime.ai_resume['sequence'],ai_checkpoint=row['result']['ai_checkpoint'],
                **({'ai_manual_sha256':row['result']['ai_manual_sha256']} if row['result'].get('ai_manual_sha256') else {})) if resuming else {})}))
        task_id = linked['intake_id'] if linked and everyday.enabled() else (linked or {}).get('task', {}).get('id', TASK)
        if resuming:
            c.execute(model_attempts.update().where(model_attempts.c.id==fence).values(outcome='running',finished_at=None))
        else:
            c.execute(model_attempts.insert().values(id=fence, job_id=None, execution_id=row['id'],
                task_id=task_id,
                source='strategy-control' if route and route.get('strategy') else 'workbench', backend=route.get('provider','codex') if route else getattr(runtime,'execution_contract',contract())['provider'],
                purpose='implementation', requested_model=route['model'] if route else getattr(runtime,'execution_contract',contract())['model'],
                requested_effort=route['effort'] if route else None, policy_revision=route['policy_revision'] if route else None,
                actual_model=None if route else runtime.identity()['model'],
                routing_reason='Reviewed Codex route and explicit run approval.' if route else 'Explicit one-task owner grant; fallback disabled.',
                started_at=now(), outcome='running'))

    def permitted():
        if stop_event is not None and stop_event.is_set():
            return False
        with engine.connect() as c:
            g = c.execute(select(grants).where(grants.c.id == row['grant_id'])).mappings().one()
            r = c.execute(select(requests).where(requests.c.id == row['id'])).mappings().one()
            try:
                task_execution.validate(c, g,ongoing=True)
                if linked and everyday.enabled() and route != provider_routes.run_selection(c, linked):
                    return False
            except (task_execution.Waiting, ValueError):
                return False
        return valid(g) and r['fence'] == fence and r['status'] in {'running', 'checking'}

    def phase():
        if not permitted():
            raise ValueError('Grant changed before verification')
        with write_transaction(engine) as c:
            c.execute(requests.update().where(requests.c.id == row['id'], requests.c.fence == fence,
                requests.c.status == 'running').values(status='checking', message='Checking frozen real changes.'))

    runtime.phase = phase
    runtime.strategy_engine=engine
    runtime.workflow_task = grant['snapshot'].get('workflow_task')
    returned_result = None
    try:
        returned_result = result = runtime.execute(row['id'], fence, permitted)
        # Freeze reference is checked by runtime, not model prose.
        if not permitted() or not runtime.verify_frozen(result):
            raise ValueError('Candidate/grant changed or frozen evidence is incomplete')
        if runtime.workflow_task:
            expected_task = dict(intake_id=runtime.workflow_task['intake_id'], task_id=runtime.workflow_task['task']['id'])
            if (result.get('manifest', {}).get('workflow_task_binding') != digest(runtime.workflow_task) or
                    result.get('manifest', {}).get('workflow_task') != expected_task):
                raise ValueError('Frozen candidate does not match the submitted task')
        status = 'checked' if result['verification'] == 'passed' else 'failed'
        if status == 'checked' and hasattr(runtime, 'record_completion'):
            runtime.record_completion(grant, row['id'], fence, result, now())
    except Exception as error:
        result = dict(verification='unknown', inference_ran=None,
                      error=f'{type(error).__name__}: {error}'[:1000])
        if getattr(runtime, 'check_failure', None):
            result['check_failure'] = runtime.check_failure
        result.update(failure_observations(runtime, row['id'], fence))
        result = retain_usage(result, returned_result)
        from workbench.smart_provider import Waiting
        if isinstance(error,Waiting) and getattr(runtime,'ai_checkpoint',None):
            status='waiting_ai'
            result.update(ai_wait=runtime.ai_wait,ai_checkpoint=runtime.ai_checkpoint,usage=runtime.ai_wait_usage,
                owner=owner,inference_ran=bool(runtime.ai_wait_usage['requests_observed']),
                **({'ai_manual_sha256':runtime.ai_manual_authorization_sha256} if getattr(runtime,'ai_manual_authorization_sha256',None) else {}),
                **(dict(ai_resume_sequence=runtime.ai_resume['sequence']) if resuming else {}))
        else:
            status = 'failed'
    completed_at = now()
    if status == 'failed' and hasattr(runtime, 'record_outcome'):
        try:
            runtime.record_outcome(grant, row['id'], fence, result, completed_at, owner)
        except (OSError, ValueError):
            # Disk/storage refusal is not a retry allowance. Keep original known
            # usage in the DB if possible, and make the missing handoff explicit.
            result = {**result, 'delivery_error':'Durable failure handoff unavailable; no automatic replay.'}
    with engine.connect() as c:
        latest = c.execute(select(requests).where(requests.c.id == row['id'])).mappings().one()
    if latest['status'] == 'cancel_requested' and latest['fence'] == fence:
        settle_cancellation(engine, runtime, row['id'], fence, result)
        return True
    if latest['fence'] == fence and latest['status'] in {'running','checking'} and not cleanup_confirmed(runtime, latest):
        hold_cleanup(engine, latest, result)
        return True
    def commit_result():
        with write_transaction(engine) as c:
            current = c.execute(select(requests).where(requests.c.id == row['id'])).mappings().one()
            if current['fence'] == fence and current['status'] == 'cancel_requested':
                return False
            if current['fence'] != fence or current['status'] not in {'running', 'checking'}:
                return True  # Stale completion can never overwrite a recovered outcome.
            c.execute(requests.update().where(requests.c.id == row['id']).values(
                status=status,result=result,finished_at=None if status=='waiting_ai' else completed_at,
                message=('Changes completed and independently checked. Nothing accepted or published.' if status == 'checked'
                         else result['ai_wait']['reason'] if status=='waiting_ai' else result.get('provider_message') or 'This run could not finish. Its evidence is saved; it will not restart automatically.') if linked else
                        ('Real candidate independently checked. Nothing published.' if status == 'checked'
                         else 'Real attempt did not qualify and is consumed. No replay under this grant.')))
            c.execute(model_attempts.update().where(model_attempts.c.id == fence).values(
                outcome=status, verification=result.get('verification'), usage=result.get('usage'), finished_at=completed_at,
                **({'actual_model': route['model']} if route and not route.get('strategy') and result.get('usage', {}).get('status') == 'complete' else {})))
        return True
    try:
        if not commit_result():
            observations = failure_observations(runtime, row['id'], fence)
            if result.get('usage', {}).get('status') == 'complete' and observations.get('usage', {}).get('status') != 'complete':
                observations['usage'] = result['usage']
            settle_cancellation(engine, runtime, row['id'], fence, observations)
    except OperationalError as error:
        busy = getattr(error.orig, 'sqlite_errorcode', 0) & 0xFF in {5, 6}
        if not busy or not (hasattr(runtime, 'recover_completion') or hasattr(runtime, 'recover_outcome')):
            raise
        # One bounded terminal-state reconciliation, never another implementation.
        recover(engine, runtime)
        with engine.connect() as c:
            saved = c.execute(select(requests.c.status, requests.c.fence).where(requests.c.id == row['id'])).one()
        if saved.status != status or saved.fence != fence:
            raise
    return True



def recovery_notice(status, result):
    """Read-only progress; a dead worker is not evidence that generation ceased."""
    result = result or {}
    if status not in {'running','checking'}:
        return False, None
    if result.get('delivery', {}).get('state') == 'waiting_cleanup':
        return True, None
    if result.get('owner') and not alive(result['owner']):
        return True, ('The worker stopped. Waiting for its original execution environment to confirm cleanup '
                      'and recover saved results. No implementation will be replayed.')
    return False, None


def review(engine, identity):
    with engine.connect() as c:
        row = c.execute(select(requests).where(requests.c.id == identity)).mappings().first()
        if not row:
            raise HTTPException(404, 'Real request not found')
        grant = c.execute(select(grants).where(grants.c.id == row['grant_id'])).mappings().one()
        from workbench.result_rejection import later_review
        followup = later_review(c.connection.driver_connection, identity)
    # Stored review data only. No client filesystem paths or arbitrary artifact reads.
    linked = grant['snapshot'].get('workflow_task')
    from workbench import task_execution
    with engine.connect() as c:
        task_current = task_execution.current(c, linked) if linked else None
    recovering, notice = recovery_notice(row['status'], row['result'])
    result = row['result'] or {}
    manifest = result.get('manifest') or {}
    proposal = (linked or {}).get('proposal_review') or {}
    contract = proposal.get('task_contract') or {}
    binding = digest(linked) if linked else None
    # Only frozen, task-bound verification evidence can describe checked behavior.
    behavior_checked = []
    behavior_reproduced = False
    if (linked and row['status'] == 'checked' and result.get('verification') == 'passed'
            and manifest.get('profile_certificate_sha256')
            and manifest.get('workflow_task_binding') == binding):
        expected = contract.get('acceptance') or []
        if contract.get('bug_evidence') and len(expected) == 1:
            behavior_checked.extend(expected)
            behavior_reproduced = True
        for scenario in (contract.get('behavioral_evidence') or {}).get('scenarios', []):
            index = scenario.get('acceptance_index')
            if type(index) is int and 0 <= index < len(expected) and expected[index] not in behavior_checked:
                behavior_checked.append(expected[index])
    if (linked and row['status'] == 'checked' and result.get('verification') == 'passed'
            and contract.get('bug_intent') and manifest.get('scope_ready_sha256')
            and manifest.get('workflow_task_binding') == binding):
        rounds = result.get('model_check_rounds') or []
        final = rounds[-1] if rounds else {}
        differential = final.get('original_test_differential') or {}
        behavior_reproduced = bool(
            final.get('status') == 'pass'
            and type(final.get('check_round')) is int
            and result.get('independent_check_evidence') == f"working-checks/{final['check_round']:02}"
            and differential.get('status') == 'pass'
            and differential.get('original_production_unchanged') is True
            and differential.get('result_sha256'))
        # A differential proves that new assertions expose the original bug;
        # it does not establish every proposed criterion individually.
    return dict(id=row['id'], title=(linked['original']['title'] if grant['snapshot'].get('operation_kind')=='feature-verification-v1' else linked['task']['title']) if linked else grant['snapshot']['contract']['title'], status=row['status'],
                message=notice or row['message'], base=grant['snapshot']['contract']['base'], current_grant=valid(grant),
                workflow_task=dict(intake_id=linked['intake_id'],task_id=linked['task']['id'],
                                   binding=binding) if linked else None,
                current_task=task_current, intended_behavior=contract.get('acceptance') or [],
                behavior_checked=behavior_checked, behavior_reproduced=behavior_reproduced and not followup,
                later_review=followup,
                grant_id=grant['id'], expires_at=grant['expires_at'],
                recovery_pending=recovering,
                publication_allowed=False, publication_requested=grant['snapshot'].get('task_budget',{}).get('budget',{}).get('publication')=='draft-pr', source='real', result=row['result'])


class LaterReview(BaseModel):
    state: Literal['rejected', 'unavailable']
    reason: str


class RunSummary(BaseModel):
    id: str
    title: str
    status: str
    message: str
    created_at: str
    finished_at: str | None
    verification: str | None
    has_candidate: bool
    recovery_pending: bool = False
    later_review: LaterReview | None = None


class RunPage(BaseModel):
    available: bool
    reason: str | None = None
    items: list[RunSummary]
    total: int
    page: int
    page_size: int = 12
    publication_allowed: Literal[False] = False


def listing(engine, page=1, group='all'):
    """Bounded projection of existing durable records; no artifact/import/activation path."""
    if not inspect(engine).has_table(requests.name):
        return dict(available=False, reason='Recorded runs are unavailable in this store version.',
                    items=[], total=0, page=page)
    filters = {'all': [], 'active': ['pending','waiting_ai','running','checking'],
               'attention': ['failed','blocked','interrupted'], 'checked': ['checked']}
    filters['active'].append('cancel_requested')
    filters['attention'].append('cancelled')
    states = filters[group]
    condition = requests.c.status.in_(states) if states else True
    query = select(requests.c.id, func.substr(func.coalesce(
                   grants.c.snapshot['workflow_task']['task']['title'].as_string(),
                   grants.c.snapshot['contract']['title'].as_string()),1,240).label('title'),
                   requests.c.status, func.substr(requests.c.message,1,1000).label('message'),
                   requests.c.created_at, requests.c.finished_at,
                   requests.c.result['verification'].as_string().label('verification'),
                   requests.c.result['delivery']['state'].as_string().label('recovery_state'),
                   requests.c.result['owner'].label('execution_owner'),
                   requests.c.result['candidate_id'].as_string().is_not(None).label('has_candidate'))
    query = query.join(grants, requests.c.grant_id==grants.c.id).where(condition).order_by(
        case((requests.c.status.in_(filters['active']),0),else_=1),
        requests.c.created_at.desc(), requests.c.id.desc()).offset((page-1)*12).limit(12)
    with engine.connect() as c:
        total = c.scalar(select(func.count()).select_from(requests).where(condition))
        items = [dict(row) for row in c.execute(query).mappings()]
        from workbench.result_rejection import later_review
        for item in items:
            item['later_review'] = later_review(c.connection.driver_connection, item['id'])
    for item in items:
        item['title'] = item['title'] or 'Recorded task run'
        recovering, notice = recovery_notice(item['status'], dict(owner=item.pop('execution_owner'),
            delivery=dict(state=item.pop('recovery_state'))))
        item['recovery_pending'] = recovering
        item['message'] = notice or item['message']
    return dict(available=True, items=items, total=total, page=page)
