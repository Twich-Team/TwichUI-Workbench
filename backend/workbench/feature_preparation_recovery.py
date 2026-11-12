"""Resume a proved storage interruption before dispatch, within the same attempt.

A new immutable preparation retains the original deadline and generation budget.
Owner Stop, any coding grant, uncertain descendants or changed evidence prevents it.
"""
from datetime import datetime, timezone
from pathlib import Path
import subprocess

from sqlalchemy import select
from workbench import intake, feature_steps
from workbench.db import (planning_jobs, task_sessions, task_run_offers,
    real_execution_grants, real_execution_requests, model_attempts, write_transaction)
from workbench.real_workspace import sha
from workbench.repair_continuation import private_json
from workbench.visual_scope import digest

RESOURCE_HOLD = ('Waiting for working-copy storage. Disposable scratch exceeds its 1 GiB '
                 'operation safety limit; Workbench is checking retained copies.')


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
    config = private_json(Path(proof['config']))
    terminal = private_json(Path(config['run_root'])/'session-finished.json')
    if (config['feature_offer_id'] != old['id'] or config['deadline'] != proof['expires_at']
            or terminal != proof['outcome'] or terminal['status'] != 'cancelled'
            or terminal.get('execution_closed') is not True):
        raise ValueError('Native preparation termination changed.')
    root = Path(config['run_root'])
    if any((root/name).exists() for name in (
            'session-claimed.json','lease-controller-result.json','lease-controller-started.json')):
        raise ValueError('A coding controller may have owned the previous preparation.')
    real = root/'real-run'
    if any((real/name).exists() for name in ('requests','streams','tool-receipts','ownership.json')):
        raise ValueError('Unexpected dispatch evidence prevents preparation recovery.')
    if not readiness_stopped(root):
        raise ValueError('The previous readiness unit has not stopped.')
    return proof


def readiness_stopped(root):
    unit = 'workbench-real-prepare-'+root.name+'.service'
    result = subprocess.run(['/usr/bin/systemctl','--user','show',unit,
        '--property=ActiveState','--property=ControlGroup'],capture_output=True,text=True,timeout=5)
    properties = dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line)
    group = properties.get('ControlGroup','')
    cgroup = Path('/sys/fs/cgroup')/group.lstrip('/')
    return (result.returncode == 0 and properties.get('ActiveState') in {'inactive','failed'}
        and (not group or cgroup.is_relative_to('/sys/fs/cgroup')
            and not any(p.read_text().strip() for p in cgroup.rglob('cgroup.procs'))))


def proof(c, job, parent, offered):
    state = parent['state']
    slot = state['feature_slot']
    result = state['feature_slot_result']
    if (job['status'] != 'failed' or job['payload'].get('feature_stop_command')
            or parent['status'] != 'cancelled' or state.get('cancel_requested') or state.get('stop_command')
            or state['feature_slot_state'].get('resource_hold') != RESOURCE_HOLD
            or result['id'] != slot['id'] or result['status'] != 'cancelled'
            or slot['snapshot']['feature_offer_id'] != offered['id']
            or slot['binding'] != digest(slot['snapshot'])):
        raise ValueError('Only a native resource interruption before coding can resume.')
    approval, permission = feature_steps.parent(c,parent['id'],active=False)
    from workbench.feature_run_recovery import deadline
    allowed_end = deadline(c,approval,permission)
    expires = slot['snapshot']['deadline']
    if datetime.now(timezone.utc) >= min(datetime.fromisoformat(expires),datetime.fromisoformat(allowed_end)):
        raise ValueError('The original implementation attempt has expired.')
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
    paths = [config_file,root/'session-finished.json',root/'template.json',root/'real-run/recipe.json']
    detail=offered['snapshot']['feature_step']
    value=dict(kind='resource-preparation-recovery-v1',previous_offer_id=offered['id'],
        previous_offer_fingerprint=offered['fingerprint'], attempt=detail['attempt'],
        revision=detail.get('preparation_revision',0)+1, attempt_reset=False,
        expires_at=expires,config=str(config_file),outcome=result['outcome'],
        baseline_source=offered['snapshot']['ready_preparation']['root'],
        files={str(p):sha(p.read_bytes()) for p in paths})
    return validate(c,{**value,'binding':digest(value)},detail['scope'])


def resume(engine):
    with engine.connect() as c:
        jobs=list(c.execute(select(planning_jobs).where(planning_jobs.c.kind=='feature_execution',
            planning_jobs.c.status=='failed',planning_jobs.c.payload['current_offer_id'].as_string().is_not(None))).mappings())
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
                    status='pending',finished_at=None,state=state,message='Storage is ready. Resuming preparation within the original attempt and deadline.'))
                # Current source, scope and time authority must still pass before committing.
                feature_steps.validate_scope(c,payload['current_step_scope'])
                c.execute(planning_jobs.update().where(planning_jobs.c.id==job['id']).values(
                    status='queued',finished_at=None,payload=payload,message='Preparing again after a storage interruption. The original attempt limits are unchanged.'))
            return True
        except (ValueError,OSError,KeyError,TypeError,subprocess.SubprocessError):
            continue
    return False
