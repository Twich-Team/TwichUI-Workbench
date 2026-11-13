"""Resume a proved storage interruption before dispatch, within the same unused coding attempt.

A fresh preparation retains the generation budget inside the existing parent window.
All original preparation deadlines remain immutable historical evidence.
Owner Stop, any coding grant, uncertain descendants or changed evidence prevents it.
"""
from datetime import datetime, timezone
from pathlib import Path
import os
import subprocess

from sqlalchemy import select
from sqlalchemy.exc import NoResultFound, MultipleResultsFound
from fastapi import HTTPException
from workbench import intake, feature_steps
from workbench.db import (planning_jobs, task_sessions, task_run_offers,
    real_execution_grants, real_execution_requests, model_attempts, write_transaction)
from workbench.real_workspace import sha
from workbench.repair_continuation import private_json
from workbench.visual_scope import digest

RESOURCE_HOLD = ('Waiting for working-copy storage. Disposable scratch exceeds its 1 GiB '
                 'operation safety limit; Workbench is checking retained copies.')
READINESS_QUERY_HELPER_SHA256 = '9a638fa0bca4404c7579f5b7d0b72af980b946453df493d81a187f6809d1f6c4'


def resource_paused(parent):
    state = parent['state']
    return (parent['status'] == 'cancelled' and not state.get('cancel_requested')
        and not state.get('stop_command')
        and state.get('feature_slot_state',{}).get('resource_hold') == RESOURCE_HOLD)


def validate(c, proof, scope):
    if proof['binding'] != digest({k:v for k,v in proof.items() if k != 'binding'}):
        raise ValueError('Preparation recovery evidence changed.')
    old = dict(intake.require(c, task_run_offers, proof['previous_offer_id']))
    feature_steps._offer(c, old, active=False)
    detail = old['snapshot']['feature_step']
    if (proof['kind'] != 'resource-preparation-recovery-v1'
            or detail['scope'] != scope or old['fingerprint'] != proof['previous_offer_fingerprint']
            or detail['attempt'] != proof['attempt']
            or proof['revision'] != detail.get('preparation_revision',0)+1
            or proof['revision'] > 3 or proof['attempt_reset'] is not False
            or proof['baseline_source'] != old['snapshot']['ready_preparation']['root']):
        raise ValueError('Preparation recovery changed its original scope or budget.')
    if c.execute(select(real_execution_grants.c.id).where(
            real_execution_grants.c.snapshot['task_budget']['offer']['id'].as_string() == old['id'])).first():
        raise ValueError('A dispatched preparation cannot be resumed as unused.')
    for path, expected in proof['files'].items():
        if sha(Path(path).read_bytes()) != expected:
            raise ValueError('Original preparation evidence changed.')
    approval, permission = feature_steps.parent(c,scope['decision_id'],active=False)
    from workbench.feature_run_recovery import deadline
    if (proof['parent_expires_at'] != deadline(c,approval,permission,reference=proof['parent_time_authority'])
            or proof['unused_generation_seconds'] != permission['budget']['per_attempt_seconds']
            or proof['time_policy'] != 'fresh-unused-coding-reservation-within-approved-parent-v1'):
        raise ValueError('Preparation recovery differs from the approved parent time budget.')
    config = private_json(Path(proof['config']))
    terminal = private_json(Path(config['run_root'])/'session-finished.json')
    if (config['feature_offer_id'] != old['id'] or config['deadline'] != proof['expires_at']
            or terminal != proof['outcome'] or terminal['status'] != (
                'ended-before-result' if proof.get('pre_grant_controller') else 'cancelled')
            or terminal.get('execution_closed') is not True):
        raise ValueError('Native preparation termination changed.')
    root = Path(config['run_root'])
    if proof.get('pre_grant_controller'):
        validate_controller(c,root,proof['pre_grant_controller'])
    elif any((root/name).exists() for name in (
            'session-claimed.json','lease-controller-result.json','lease-controller-started.json')):
        raise ValueError('A coding controller may have owned the previous preparation.')
    real = root/'real-run'
    if any((real/name).exists() for name in ('requests','streams','tool-receipts','ownership.json')):
        raise ValueError('Unexpected dispatch evidence prevents preparation recovery.')
    if not readiness_stopped(root):
        raise ValueError('The previous readiness unit has not stopped.')
    return proof


def manager_root():
    uid = os.getuid()
    return Path(f'/sys/fs/cgroup/user.slice/user-{uid}.slice/user@{uid}.service')


def unit_quiet(name):
    manager = manager_root()
    if not manager.is_dir():
        return False
    return not any(p.read_text().strip() for group in manager.rglob(name)
        for p in group.rglob('cgroup.procs'))


def readiness_stopped(root):
    # Permission validation also runs inside the isolated implementation unit,
    # where service-manager sockets are deliberately unavailable. Read native
    # process identity and the same read-only cgroup hierarchy directly.
    from workbench.real_execution import alive
    owner = private_json(root/'preparation-owner.json')
    return (owner['recipe_sha256'] == sha((root/'real-run/recipe.json').read_bytes())
        and not alive(owner['owner'])
        and unit_quiet('workbench-real-prepare-'+root.name+'.service'))


def controller_qualification(c,root,scope):
    """Known denied readiness query after a successful tool-free probe; no grant."""
    from workbench.real_execution import alive
    from workbench.local_generation_recovery import absent_epoch, normal_restored
    report_path = root/'lease-controller-result.json'
    report = private_json(report_path)
    release = root.parents[2]
    helper = release/'backend/workbench/feature_preparation_recovery.py'
    private = Path(report['private_recovery_directory'])
    if private != release/'.local'/('chronicle-primary-lease-'+root.name):
        raise ValueError('Controller restoration belongs to different work.')
    state = private_json(private/'state.json')
    probe = private_json(root/'probe-result.json')
    owner = private_json(root/'controller-owner.json')
    if (sha(helper.read_bytes()) != READINESS_QUERY_HELPER_SHA256
            or 'ValueError: The previous readiness unit has not stopped.' not in (root/'implementation-controller.log').read_text()
            or report.get('implementation_attempt_consumed') is not False
            or report.get('inference_ran') is not False or report.get('accepted') is not False
            or report.get('implementation_controller_exit') != 1
            or report.get('probe_generation_requests') != 1
            or any(report.get('restoration',{}).get(k) is not True for k in
                ('restored','original_access_verified','original_config_verified','temporary_access_rejected'))
            or probe.get('passed') is not True or probe.get('implementation_attempts') != 0
            or probe.get('purpose') != 'capability_validation' or probe.get('epoch') != state['temporary_epoch']
            or not absent_epoch(state['temporary_epoch']) or not normal_restored(private,state)
            or alive(owner) or not unit_quiet('workbench-real-chronicle-'+root.name+'.service')
            or any((root/'implementation').glob('*'))):
        raise ValueError('The known pre-grant controller failure is not safely settled.')
    ledgers = list(c.execute(select(model_attempts).where(
        model_attempts.c.task_id == scope['intake_id'])).mappings())
    matches = [dict(row) for row in ledgers if row['purpose'] == 'capability_validation'
        and row['backend'] == 'local' and row['finished_at'] and row['outcome'] == 'passed'
        and row['usage'] == probe]
    if len(matches) != 1:
        raise ValueError('The original bounded cancellation-probe accounting is unavailable.')
    ledger = matches[0]
    value = dict(kind='known-readiness-query-denied-before-grant-v1',
        probe_ledger_id=ledger['id'],probe_ledger_binding=digest(ledger),
        temporary_epoch=state['temporary_epoch'],controller_owner=owner,
        files={str(p):sha(p.read_bytes()) for p in (
            report_path,private/'state.json',root/'probe-result.json',root/'controller-owner.json',
            root/'implementation-controller.log',root/'session-claimed.json',helper)})
    return value


def validate_controller(c,root,qualification):
    from workbench.real_execution import alive
    from workbench.local_generation_recovery import absent_epoch
    ledger = dict(intake.require(c,model_attempts,qualification['probe_ledger_id']))
    if (qualification['kind'] != 'known-readiness-query-denied-before-grant-v1'
            or digest(ledger) != qualification['probe_ledger_binding']
            or not absent_epoch(qualification['temporary_epoch'])
            or alive(qualification['controller_owner'])
            or not unit_quiet('workbench-real-chronicle-'+root.name+'.service')):
        raise ValueError('The original controller or probe accounting is not settled.')
    for path,expected in qualification['files'].items():
        if sha(Path(path).read_bytes()) != expected:
            raise ValueError('The original pre-grant controller evidence changed.')
    return qualification


def proof(c, job, parent, offered):
    state = parent['state']
    slot = state['feature_slot']
    result = state['feature_slot_result']
    resource = parent['status'] == 'cancelled' and state['feature_slot_state'].get('resource_hold') == RESOURCE_HOLD
    pre_grant = parent['status'] == 'ended-before-result'
    if (job['status'] != 'failed' or job['payload'].get('feature_stop_command')
            or not (resource or pre_grant) or state.get('cancel_requested') or state.get('stop_command')
            or result['id'] != slot['id'] or result['status'] != parent['status']
            or slot['snapshot']['feature_offer_id'] != offered['id']
            or slot['binding'] != digest(slot['snapshot'])):
        raise ValueError('Only a native resource interruption before coding can resume.')
    approval, permission = feature_steps.parent(c,parent['id'],active=False)
    from workbench.feature_run_recovery import time_authority
    time_reference, allowed_end = time_authority(c,approval,permission)
    expires = slot['snapshot']['deadline']
    if datetime.now(timezone.utc) >= datetime.fromisoformat(allowed_end):
        raise ValueError('The approved parent execution window has expired.')
    ready = Path(offered['snapshot']['ready_preparation']['root'])
    # Recover the frozen installation from its native preparation path.
    runs = next((p for p in ready.parents if p.name == 'runs' and p.parent.name == 'artifacts'),None)
    if runs is None:
        raise ValueError('Original native preparation location is unavailable.')
    config_file = runs/('task-session-'+slot['id'])/'session.json'
    config = private_json(config_file)
    if (config['task'] != slot['snapshot']['task'] or config['feature_offer_id'] != offered['id']
            or config['task_budget_fingerprint'] != slot['snapshot']['budget_fingerprint']):
        raise ValueError('Original native session configuration changed.')
    root = Path(config['run_root'])
    paths = [config_file,root/'session-finished.json',root/'template.json',root/'real-run/recipe.json',root/'preparation-owner.json']
    qualification = controller_qualification(c,root,dict(intake_id=approval['intake_id'])) if pre_grant else None
    detail=offered['snapshot']['feature_step']
    value=dict(kind='resource-preparation-recovery-v1',previous_offer_id=offered['id'],
        previous_offer_fingerprint=offered['fingerprint'], attempt=detail['attempt'],
        revision=detail.get('preparation_revision',0)+1, attempt_reset=False,
        expires_at=expires,config=str(config_file),outcome=result['outcome'],
        parent_time_authority=time_reference,parent_expires_at=allowed_end,
        unused_generation_seconds=permission['budget']['per_attempt_seconds'],
        time_policy='fresh-unused-coding-reservation-within-approved-parent-v1',
        baseline_source=offered['snapshot']['ready_preparation']['root'],
        files={str(p):sha(p.read_bytes()) for p in paths},
        **({'pre_grant_controller':qualification} if qualification else {}))
    return validate(c,{**value,'binding':digest(value)},detail['scope'])


def resume(engine):
    with engine.connect() as c:
        jobs=list(c.execute(select(planning_jobs).where(planning_jobs.c.kind=='feature_execution',
            planning_jobs.c.status=='failed',
            planning_jobs.c.payload['worker_operation_failure'].as_string().is_(None),planning_jobs.c.payload['current_offer_id'].as_string().is_not(None))).mappings())
    for job in jobs:
        try:
            with write_transaction(engine) as c:
                current=intake.require(c,planning_jobs,job['id'])
                if current['payload'] != job['payload'] or current['status'] != 'failed':
                    continue
                parent=intake.require(c,task_sessions,job['payload']['feature_decision_id'])
                offered=intake.require(c,task_run_offers,job['payload']['current_offer_id'])
                recovery=proof(c,current,parent,offered)
                if (c.execute(select(real_execution_requests.c.id).where(real_execution_requests.c.status.in_(
                        ['pending','running','checking','cancel_requested']))).first()
                        or c.execute(select(model_attempts.c.id).where(model_attempts.c.finished_at.is_(None))).first()):
                    continue
                state={**parent['state'],'feature_history':parent['state'].get('feature_history',[])+[
                    dict(slot=parent['state']['feature_slot'],result=parent['state']['feature_slot_result'],preparation_recovery=recovery)]}
                for key in ('feature_slot','feature_slot_result','feature_slot_state','owner'):
                    state.pop(key,None)
                payload={**current['payload'],'phase':'scope_ready','current_preparation_recovery':recovery}
                for key in ('current_offer_id','feature_preparation_claim','session_id'):
                    payload.pop(key,None)
                c.execute(task_sessions.update().where(task_sessions.c.id==parent['id']).values(
                    status='pending',finished_at=None,state=state,message='The previous preparation is safely settled. Resuming the unused coding attempt within the approved feature window.'))
                # Current source, scope and time authority must still pass before committing.
                feature_steps.validate_scope(c,payload['current_step_scope'])
                c.execute(planning_jobs.update().where(planning_jobs.c.id==job['id']).values(
                    status='queued',finished_at=None,payload=payload,message='Preparing again after a proved pre-dispatch interruption. The coding attempt limits are unchanged.'))
            return True
        except (ValueError,OSError,KeyError,TypeError,subprocess.SubprocessError,
                HTTPException,NoResultFound,MultipleResultsFound):
            continue
    return False
