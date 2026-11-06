"""Trusted, single-use Chronicle maintenance controller. No retry/replay ingress.

Requires the exact owner instruction file. Never call from API/model tools.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'backend'))
from workbench import real_execution as job  # noqa: E402
from workbench import real_lease as primary  # noqa: E402
from workbench.real_stream import NamedStream  # noqa: E402
from workbench.real_probe import classify_probe  # noqa: E402
from workbench.real_package import package_for  # noqa: E402
from workbench.real_workspace import Runtime, durable, sha  # noqa: E402
from workbench.db import engine_for, model_attempts, write_transaction  # noqa: E402
from workbench.service import now  # noqa: E402
from workbench.task_session import delegated_command  # noqa: E402


def workflow(root, instruction=None):
    """Private trusted task link, not a public activation or arbitrary-store initializer."""
    path = root/'workflow-link.json'
    if not path.exists():
        return None
    if path.is_symlink() or path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077 or path.stat().st_size > 4096:
        raise ValueError('Private owned workflow link required')
    value = json.loads(path.read_text())
    db = Path(value['database'])
    if db.is_symlink() or not db.is_file() or db.stat().st_uid != os.getuid() or [db.stat().st_dev,db.stat().st_ino] != value['database_identity']:
        raise ValueError('Existing owned task store identity changed')
    if instruction is not None:
        from workbench.owner_task import instruction as owner_instruction
        owner_instruction(instruction, value['authorization_sha256'])
    return value


def connected_deadline(linked):
    import math
    start, end = linked['run_started_at'], linked['run_deadline']
    if (type(start) not in (int,float) or type(end) not in (int,float) or
            not math.isfinite(start) or not math.isfinite(end) or
            abs(end-start-2400) > 0.00001 or not start <= time.time() < end):
        raise ValueError('Original bounded acceptance clock required; never reset at activation')
    return datetime.fromtimestamp(end, timezone.utc)


def save(path, obj):
    durable(path, json.dumps(obj, sort_keys=True, indent=2).encode())


def store(root):
    linked = workflow(root.parent)
    if linked:
        from workbench.task_execution import snapshot
        engine = engine_for(Path(linked['database']))
        with engine.connect() as c:
            if c.exec_driver_sql('SELECT version_num FROM alembic_version').scalar() not in ('0010','0011','0012'):
                raise ValueError('Connected task store requires existing0010/0011/0012; never migrate it here')
            ref = linked['task']
            pinned = snapshot(c, ref['intake_id'],ref['task_id'],ref['approval_id'])
            if pinned['approval']['payload'].get('run_budget'):
                from workbench.task_budget import permission
                from workbench.db import proposal_decisions
                approved = c.execute(select(proposal_decisions).where(proposal_decisions.c.id==ref['approval_id'])).mappings().one()
                budget = permission(c, approved)
                if job.digest(budget)!=linked.get('task_budget_fingerprint'):
                    raise ValueError('Selected task budget differs from the workflow link')
            elif pinned['decomposition']['payload']['owner_provenance']['authorization_sha256'] != linked['authorization_sha256']:
                raise ValueError('Task scope and owner authorization differ')
            if c.execute(select(job.requests.c.id).where(job.requests.c.status.in_(
                    ['pending','running','checking','cancel_requested']))).first():
                raise ValueError('Existing active real work must not be claimed by this session')
            if c.execute(select(job.grants.c.id).outerjoin(job.requests,job.requests.c.grant_id==job.grants.c.id)
                    .where(job.requests.c.id.is_(None),job.grants.c.revoked_at.is_(None),job.grants.c.expires_at>now())).first():
                raise ValueError('Existing unconsumed execution permission must not be dispatched')
        return engine
    from alembic import command
    from alembic.config import Config
    engine = engine_for(root/'acceptance.sqlite3')
    with engine.connect() as c:
        config = Config(str(ROOT/'alembic.ini'))
        config.attributes['connection'] = c
        command.upgrade(config, 'head')
        c.commit()
    return engine


def inherited(private):
    if private.is_symlink() or private.stat().st_uid != os.getuid() or private.stat().st_mode & 0o077:
        raise RuntimeError('Private owned lease required')
    state = json.loads((private/'state.json').read_text())
    lease = primary.Lease(private)
    lease.secret = (private/'temporary.key').read_text()
    lease.established = state['established_at']
    lease.server_epoch = state['temporary_epoch']
    if not lease.current():
        raise RuntimeError('Authentication lease is no longer current')
    return lease


def checked_limit(root):
    proof = json.loads((root/'total-limit-proof.json').read_text())
    source = Path('/home/rich/llama.cpp')
    if not (proof['passed'] and proof['controls'] == 15 and proof['source_clean']
            and proof['wire_max'] == 2496 and proof['authorized_max'] == 2500):
        raise RuntimeError('Installed total generation proof missing')
    if any(sha((source/p).read_bytes()) != h for p, h in proof['source_sha256'].items()):
        raise RuntimeError('Installed limit source changed after proof')
    return sha((root/'total-limit-proof.json').read_bytes())


def audit_ready(pid):
    """Read-only availability/INFO check, not a cancellation certificate."""
    import re
    r = subprocess.run(['journalctl', '-u', primary.SERVICE, '_PID='+str(pid),
                        '-n', '128', '-o', 'json', '--no-pager'], capture_output=True, timeout=2)
    info = loss = False
    for line in r.stdout.splitlines():
        try:
            event = json.loads(line)
            msg = event.get('MESSAGE', '')
        except (ValueError, AttributeError):
            loss = True
            continue
        loss = loss or any(w in msg.lower() for w in ['suppressed', 'dropped', 'lost messages'])
        if str(event.get('_PID')) == str(pid) and re.search(r'(^|\s)I\s', msg):
            info = True
    return dict(readable=r.returncode == 0 and not r.stderr, info_visible=info, loss_observed=loss,
                passed=r.returncode == 0 and not r.stderr and info and not loss)


def journal_proof(pid, task, since, end):
    # No raw logs or prompts retained. Only numeric timing/stop facts for this PID/task.
    remaining = end-time.monotonic()
    if remaining <= 0:
        raise RuntimeError('Probe total deadline exhausted before log reconciliation')
    r = subprocess.run(['journalctl', '-u', primary.SERVICE,
                        '--since=@'+format(since, '.6f'), '-n', '512', '-o', 'json', '--no-pager'],
                       capture_output=True, timeout=min(2, remaining))
    import re
    timing = stop = False
    queued_index = stop_index = None
    lines = r.stdout.splitlines()
    error_or_loss = len(lines) >= 512 or len(r.stdout) > 1048576 or bool(r.stderr)
    for index, line in enumerate(r.stdout.splitlines()):
        if not line.startswith(b'{'):
            error_or_loss = True
            continue
        try:
            event = json.loads(line)
        except ValueError:
            error_or_loss = True
            continue
        msg = event.get('MESSAGE', '')
        if any(word in msg.lower() for word in ['suppressed', 'dropped', 'lost messages']):
            error_or_loss = True
        if str(event.get('_PID')) != str(pid):
            continue
        if re.search(r'\b(?:error|failed|fatal|exception)\b|unknown stream', msg, re.I):
            error_or_loss = True
        if re.search(r'\bcancel task, id_task\s*=\s*'+str(task)+r'\b', msg):
            queued_index = index if queued_index is None else queued_index
        if not re.search(r'task\s*(?:=)?\s*'+str(task)+r'\b', msg):
            continue
        if 'stop processing:' in msg:
            stop = True
            stop_index = index if stop_index is None else stop_index
        if 'eval time =' in msg or 'total time =' in msg:
            timing = True
    return dict(owned_stop_logged=stop, normal_completion_timing_logged=timing,
                owned_cancel_queued_logged=queued_index is not None,
                cancel_precedes_stop=(queued_index is not None and stop_index is not None and queued_index < stop_index),
                journal_readable=r.returncode == 0, error_or_loss_observed=error_or_loss,
                integrity_assumption='Installed FIFO INFO log plus readable unsuppressed unit journal; no silent-loss proof claimed.',
                interpretation='Normal stop paths print timings before release; CANCEL releases without timing. Raw logs not retained.')


def attempt(root, private, instruction):
    linked = workflow(root, instruction)
    package = package_for(root)
    if package:
        package.require_time(2700)
    cg = Path('/sys/fs/cgroup'+Path('/proc/self/cgroup').read_text().strip().split('::',1)[1])
    if not cg.name.startswith('workbench-real-'):
        raise RuntimeError('Dedicated owned cgroup required')
    (cg/'main').mkdir()
    (cg/'main/cgroup.procs').write_text(str(os.getpid()))
    (cg/'cgroup.subtree_control').write_text('+memory +pids +cpu')
    os.environ['WORKBENCH_CG_ROOT'] = str(cg)
    os.environ['WORKBENCH_LOCAL_KEY'] = str(private/'temporary.key')
    lease = inherited(private)
    certificate = json.loads((private/'certification.json').read_text())
    probe = json.loads((root/'probe-result.json').read_text())
    if (certificate['probe_sha256'] != sha((root/'probe-result.json').read_bytes())
            or certificate['limit_sha256'] != checked_limit(root) or not probe['passed']
            or certificate['epoch'] != primary.epoch()):
        raise RuntimeError('Current lease/probe/limit certification mismatch')
    state = json.loads((private/'state.json').read_text())
    if state['deadline']-time.time() < 2400+300:
        raise RuntimeError('Insufficient lease for full acceptance plus restoration reserve')
    runtime = Runtime(root/'real-run')
    runtime.package = package.verify(root) if package else None
    runtime.grant_deadline = datetime.fromtimestamp(state['deadline']-300, timezone.utc)
    if linked:
        run_end = connected_deadline(linked)
        runtime.grant_deadline = min(runtime.grant_deadline, run_end)
        runtime.deadline = min(runtime.deadline, time.monotonic()+run_end.timestamp()-time.time())
    runtime.deadline = min(runtime.deadline, time.monotonic()+state['deadline']-time.time()-300)
    runtime.primary = NamedStream(lease.secret, ROOT/'.local/real-primary-session.json', lease.current_epoch,
                                 lease.current, lambda r: save(root/'implementation'/f"stream-{r['sequence']:02}.json", r))
    runtime.primary.validated = True
    gates = runtime.preflight()
    save(root/'pre-grant-gates.json', gates)
    if any(g['status'] != 'passed' for g in gates):
        save(root/'implementation-result.json', dict(grant_issued=False, inference_ran=False, gates=gates))
        return
    engine = store(root/'real-run')
    try:
        if state['deadline']-time.time() < 2400+300:
            raise RuntimeError('Lease reserve changed during preflight; no grant issued')
        if linked and runtime.recipe:
            from workbench import task_budget
            runtime.task_ref = linked['task']
            if not task_budget.authorize_one(engine,runtime):
                raise ValueError('Current explicit task budget did not authorize the selected runtime')
            with engine.connect() as c:
                grant = dict(c.execute(select(job.grants).where(job.grants.c.snapshot['task_budget']['decision_id'].as_string()==linked['task']['approval_id'])).mappings().one())
        else:
            grant = job.issue(engine, instruction.read_text(), str(instruction.resolve()), runtime,
                              **({'task':linked['task']} if linked else {}))
        save(root/'issued-grant.json', grant)
        if linked:
            from workbench.task_execution import request_id
            receipt = {'id':request_id(grant['id'])}
        else:
            receipt = job.submit(engine, grant['id'], str(uuid4()))
        job.process_one(engine, runtime)
        save(root/'implementation-result.json', job.review(engine, receipt['id']))
        if linked:
            save(root/'connected-delivery.json',dict(task=linked['task'],request_id=receipt['id'],
                 entry='existing worker automatic task dispatch',manual_submit=False))
    finally:
        engine.dispose()


def controller(root, instruction):
    linked = workflow(root, instruction)
    if linked:
        connected_deadline(linked)
    # Create-only root check prevents another probe/run on an old invocation.
    durable(root/'controller-started.json', json.dumps({'at': time.time()}).encode())
    checked_limit(root)
    package = package_for(root)
    if package:
        package.require_time(3000)
    prepared_runtime = Runtime(root/'real-run')
    if prepared_runtime.recipe and any(g['status']!='passed' for g in prepared_runtime.static_preflight()):
        save(root/'static-readiness-blocked.json',dict(grant_issued=False,inference_ran=False))
        return
    private = ROOT/'.local'/('chronicle-primary-lease-'+root.name)
    engine = store(root/'real-run')
    report = dict(inference_ran=False, implementation_attempt_consumed=False,
                  probe_generation_requests=0, accepted=False, private_recovery_directory=str(private))
    lease = primary.Lease(private)
    try:
        with lease:
            if package:
                package.clamp_lease(lease)
            save(root/'lease-established.json', lease.metadata)
            audit = audit_ready(lease.server_epoch[0])
            save(root/'new-epoch-audit-preflight.json', audit)
            if not audit['passed']:
                raise RuntimeError('Trusted INFO audit unavailable; stop before generation')
            body = dict(model='pilot-qwen38-27b', messages=[dict(role='user', content='Count upward from one, separated by spaces. Keep counting.')],
                        max_tokens=60, temperature=0, cache_prompt=False, ignore_eos=True)
            status, raw = primary.request(lease.secret, 'POST', '/v1/chat/completions/input_tokens', json.dumps(body).encode())
            counted = json.loads(raw).get('input_tokens') if status == 200 else None
            save(root/'probe-input-count.json', dict(input_tokens=counted, limit=128, generation=False))
            if type(counted) is not int or not 0 < counted <= 128:
                raise RuntimeError('Probe input token ceiling not verified')
            probe_id = str(uuid4())
            with write_transaction(engine) as c:
                c.execute(model_attempts.insert().values(id=probe_id, job_id=None, execution_id=None,
                    task_id=linked['task']['task_id'] if linked else job.TASK,
                    source='workbench', backend='local', purpose='capability_validation',
                    requested_model=body['model'], actual_model=body['model'],
                    routing_reason='Owner-authorized single cancellation probe; separate from implementation.',
                    started_at=now(), outcome='running'))
            events = []
            def evidence(event):
                events.append(event)
                save(root/'probe'/f"stream-{event['sequence']:02}.json", event)
            client = NamedStream(lease.secret, ROOT/'.local/real-primary-session.json', lease.current_epoch, lease.current, evidence)
            started = time.monotonic()
            result = dict(passed=False, input_tokens=counted, generated_tokens=None, generated_upper_bound=60,
                          purpose='capability_validation', implementation_attempts=0, epoch=lease.current_epoch())
            try:
                client.request(body, lambda: True, started+30, probe=True)
                event = events[0]
                log = journal_proof(lease.server_epoch[0], event['probe_decision']['witness']['task'],
                                    event['started_at'], started+30)
                result.update(log)
                result['passed'] = classify_probe(event, log, time.monotonic()-started)
                result['usage_limitation'] = 'Cancellation resets n_decoded; exact final generated count unavailable. Busy count is a lower bound, wire cap60 an upper bound; idle zero is not usage.'
                result['generated_lower_bound'] = event['probe_decision']['witness']['observed_decoded']
            except Exception as e:
                result['error'] = type(e).__name__+': '+str(e)[:300]
            finally:
                result['elapsed_s'] = time.monotonic()-started
                result['wire_started'] = bool(events and events[0]['wire_started'])
                report['probe_generation_requests'] = int(result['wire_started'])
                save(root/'probe-result.json', result)
                with write_transaction(engine) as c:
                    c.execute(model_attempts.update().where(model_attempts.c.id == probe_id).values(
                        outcome='passed' if result['passed'] else 'failed', finished_at=now(), usage=result))
            if not result['passed']:
                raise RuntimeError('Single cancellation probe failed or inconclusive; implementation remains unspent')
            primary.atomic(private/'certification.json', json.dumps(dict(probe_sha256=sha((root/'probe-result.json').read_bytes()),
                           limit_sha256=checked_limit(root), epoch=primary.epoch())).encode())
            # One bounded call into the existing owned handler; no second attempt/replay.
            cmd = delegated_command(['--user', '--unit=workbench-real-chronicle-'+root.name,
                   '--property=WorkingDirectory='+str(ROOT), '--property=Delegate=yes',
                   '--property=MemoryMax=6G', '--property=MemorySwapMax=0',
                   '--property=TasksMax='+('128' if (root/'real-run/recipe.json').is_file() else '64'),
                   '--property=CPUQuota=200%', '--property=RuntimeMaxSec=2450', '--wait', '--pipe'],
                   [
                   str(ROOT/'.venv/bin/python'), str(Path(__file__).resolve()), 'attempt',
                   '--root', str(root), '--private', str(private), '--owner-instruction', str(instruction)])
            with (root/'implementation-controller.log').open('x') as log:
                child = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, timeout=2500)
            report['implementation_controller_exit'] = child.returncode
            if (root/'implementation-result.json').is_file():
                outcome = json.loads((root/'implementation-result.json').read_text())
                report['implementation_result'] = outcome
                report['implementation_attempt_consumed'] = outcome.get('status') in {'running', 'checking', 'checked', 'failed', 'interrupted'}
                report['inference_ran'] = any(json.loads(p.read_text()).get('wire_started') for p in (root/'implementation').glob('stream-*.json'))
    except Exception as error:
        report['error'] = type(error).__name__+': '+str(error)[:500]
    finally:
        engine.dispose()
        if (private/'state.json').exists():
            state = json.loads((private/'state.json').read_text())
            report['restoration'] = {k: state.get(k) for k in ['restored','restored_at','restored_epoch','original_config_verified',
                'original_access_verified','temporary_access_rejected','established_at','deadline']}
        save(root/'lease-controller-result.json', report)
    print(json.dumps(report, indent=2))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('stage', choices=['controller', 'attempt'])
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--private', type=Path)
    p.add_argument('--owner-instruction', type=Path, required=True)
    args = p.parse_args()
    root = args.root.resolve()
    if root.parent != ROOT/'artifacts/runs' or not root.name.startswith(('chronicle-lease-','task-lease-')) or root.is_symlink():
        p.error('Unique owned run root required')
    if args.stage == 'controller':
        controller(root, args.owner_instruction)
    else:
        attempt(root, args.private, args.owner_instruction)


if __name__ == '__main__':
    main()
