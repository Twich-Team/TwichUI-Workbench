"""Between-turn suspension of an existing fenced coding request.

No new grant, task, request or attempt is issued. Only a known non-dispatch can
suspend; native uncertainty always uses the existing recovery hold instead.
"""
import json
import time
from pathlib import Path
from workbench import provider_admission as files
from workbench.real_workspace import sha,tree


def checkpoint(runtime,identity,fence,decision):
    from workbench.real_execution import owner_identity
    progress=getattr(runtime,'ai_progress',dict(next_turn=0,receipts=[],feedback=None,changed=False,seen=[]))
    count=len(progress['receipts'])
    if count!=progress['next_turn'] or count>=20:
        raise ValueError('The remaining coding request budget cannot be verified.')
    # Scope and edit-batch checks still own the candidate; waiting grants no edits.
    runtime.scope(allow_empty=True)
    for path in (runtime.root/'edit-batches').glob('*/prepared.json'):
        if not any((path.parent/name).exists() for name in ('applied.json','rolled-back.json')):
            raise ValueError('An interrupted edit batch must settle before waiting.')
    previous=getattr(runtime,'ai_resume',None) or {}
    sequence=previous.get('sequence',0)+1
    if sequence>32:
        raise ValueError('Coding wait history is at its bounded limit.')
    active=previous.get('active_seconds',0)+max(0,time.monotonic()-runtime.ai_phase_started)
    since=dict(getattr(runtime,'ai_wait_since',{}))
    if decision.get('stage_id'):
        since.setdefault(decision['stage_id'],time.time())
    value=dict(manual_authorization_sha256=getattr(runtime,'ai_manual_authorization_sha256',None),waiting_since=since,version=1,execution_id=identity,fence=fence,sequence=sequence,
        runtime_identity=runtime.identity(),source_tree=tree(runtime.workspace),
        active_seconds=active,progress=progress,decision=decision,owner=owner_identity(),
        created_at=time.time(),dispatch='not_started',prior_checkpoint=previous.get('checkpoint_sha256'))
    raw=json.dumps(value,sort_keys=True,allow_nan=False).encode()
    from workbench.real_workspace import durable
    path=runtime.root/('ai-checkpoint-'+identity+'-'+str(sequence)+'.json')
    durable(path,raw)
    path.chmod(0o600)
    return dict(path=str(path),sha256=sha(raw),sequence=sequence)


def load(runtime,row,grant):
    from workbench.real_execution import alive
    result=row['result'] or {}
    reference=result.get('ai_checkpoint',{})
    path=Path(reference['path'])
    if path.parent!=runtime.root or path.name!='ai-checkpoint-'+row['id']+'-'+str(reference['sequence'])+'.json':
        raise ValueError('The coding wait checkpoint belongs to another operation.')
    value=files.read(path,None)
    if (sha(path.read_bytes())!=reference['sha256'] or value['execution_id']!=row['id']
            or value['fence']!=row['fence'] or value['sequence']!=reference['sequence']
            or value['dispatch']!='not_started' or value['runtime_identity']!=grant['snapshot']['runtime']
            or value['source_tree']!=tree(runtime.workspace) or alive(value['owner'])
            or not 0<=value['progress']['next_turn']<20
            or len(value['progress']['receipts'])!=value['progress']['next_turn']
            or not 0<=value['active_seconds']<1200):
        raise ValueError('Frozen work, native settlement or remaining coding budget changed.')
    runtime.scope(allow_empty=True)
    # Completed native turns are not regenerated on resumption.
    for number,receipt in enumerate(value['progress']['receipts'],1):
        saved=files.read(runtime.root/('codex-turn-'+row['id']+'-'+str(number)+'.json'),None)
        if saved!=receipt or not receipt.get('provider_receipt'):
            raise ValueError('A completed native turn receipt changed.')
    return {**value,'checkpoint_sha256':reference['sha256']}


def usage(runtime,identity,fence,reference):
    value=files.read(Path(reference['path']),None)
    if value['execution_id']!=identity or value['fence']!=fence:
        raise ValueError('Coding wait accounting changed identity.')
    receipts=value['progress']['receipts']
    values={key:sum(r['values'][key] for r in receipts) for key in ('prompt_tokens','completion_tokens','total_tokens')}
    return dict(status='complete' if receipts else 'not_started',units='tokens',values=values,
        requests_observed=len(receipts),provenance=dict(method='Completed native prefix; next coding request did not start',
            checkpoint_sha256=reference['sha256']))


def resume_controller(engine,root,row,config,stop_event=None,launch=None):
    """Resume only the saved waiting request through its original task controller."""
    from datetime import datetime,timezone
    import subprocess
    from workbench import real_execution as execution
    from workbench.real_workspace import durable
    from workbench.smart_provider import Waiting
    from sqlalchemy import select
    from workbench.db import real_execution_grants as grants,real_execution_requests as requests,model_attempts,write_transaction
    with engine.connect() as c:
        grant=c.execute(select(grants).where(grants.c.id==row['grant_id'])).mappings().one()
    number=row['result']['ai_checkpoint']['sequence']
    report=root/('ai-controller-result-'+str(number)+'.json')
    owner_file=root/('ai-controller-owner-'+str(number)+'.json')
    if report.exists():
        return files.read(report,None).get('restoration',{}).get('restored') is True
    if owner_file.exists():
        owner=files.read(owner_file,None)
        if execution.alive(owner):
            return False
        raise ValueError('A resume controller ended without a verified result. Native recovery must settle it.')
    log=root/('ai-controller-'+str(number)+'.log')
    if log.exists():
        raise ValueError('An earlier resume launch intent needs settlement; no duplicate controller was started.')
    decision=row['result']['ai_wait']
    if decision.get('recheck_at',0)>time.time():
        raise Waiting(decision)
    from workbench import bug_authority
    linked=grant['snapshot'].get('workflow_task') or {}
    with engine.connect() as c:
        covered=bool(linked and bug_authority.frozen(c,linked['intake_id']))
    if covered and execution.alive(row['result']['owner']):
        return False
    with engine.connect() as c:
        from workbench.work_controls import task_paused
        if linked and task_paused(c,linked['intake_id']):
            return False
    if covered:
        # Verify saved work before a fresh native allowance observation. Neither
        # elapsed time nor the old expired grant supplies permission to dispatch.
        from workbench.codex_runtime import CodexRuntime
        from workbench.smart_provider import choose
        selection=grant['snapshot']['runtime']['codex_selection']
        restored=CodexRuntime(Path(grant['snapshot']['recovery']['root']),selection,stop_event)
        restored.package=grant['snapshot']['runtime'].get('package')
        previous_deadline=grant['snapshot']['runtime'].get('grant_deadline')
        restored.grant_deadline=datetime.fromisoformat(previous_deadline) if previous_deadline else None
        saved=load(restored,row,grant)
        with engine.connect() as c:
            bug_authority.current(c,linked['intake_id'])
        choose(selection,lambda:bool(stop_event and stop_event.is_set()),waiting_since=saved['waiting_since'],failures=saved['progress'].get('failures',{}))
        if not execution.valid(grant):
            grant=bug_authority.renew_wait(engine,row,grant,restored)
            with engine.connect() as c:
                row=c.execute(select(requests).where(requests.c.id==row['id'])).mappings().one()
    if not execution.valid(grant):
        if execution.alive(row['result']['owner']):
            return False
        with write_transaction(engine) as c:
            current=c.execute(select(requests).where(requests.c.id==row['id'])).mappings().one()
            if current['status']=='waiting_ai' and current['fence']==row['fence']:
                c.execute(requests.update().where(requests.c.id==row['id']).values(status='failed',finished_at=execution.now(),
                    result={**current['result'],'error':'Run permission expired or was withdrawn while waiting. No new native request started.'},
                    message='Run permission needs review. Completed work and native consumption remain saved.'))
                c.execute(model_attempts.update().where(model_attempts.c.id==row['fence']).values(outcome='failed'))
        return True
    deadline=(datetime.fromisoformat(grant['expires_at']) if grant['snapshot'].get('policy_operation') else datetime.fromisoformat(config['deadline']))
    if datetime.now(timezone.utc)>=deadline:
        raise ValueError('The original run permission expired while waiting. Saved work needs a new run decision.')
    unit='workbench-real-codex-'+root.name+'-ai-'+str(number)
    from workbench import task_session
    command=task_session.delegated_command([
        '--user','--unit='+unit,'--property=Delegate=yes','--property=MemoryMax=6G',
        '--property=MemorySwapMax=0','--property=TasksMax=128','--property=CPUQuota=200%',
        '--property=RuntimeMaxSec=2400','--property=TimeoutStopSec=15','--property=KillMode=mixed','--wait','--pipe'],
        [str(task_session.ROOT/'.venv/bin/python'),str(task_session.ROOT/'scripts/codex_task.py'),str(root),str(number)])
    if launch is not None:
        launch(command)
    else:
        with log.open('xb') as stream:
            child=subprocess.Popen(command,stdout=stream,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL)
            owner=dict(pid=child.pid,boot=Path('/proc/sys/kernel/random/boot_id').read_text(),
                start=(Path('/proc')/str(child.pid)/'stat').read_text().split(') ',1)[1].split()[19])
            durable(owner_file,json.dumps(owner).encode())
            owner_file.chmod(0o600)
            try:
                while child.poll() is None:
                    if stop_event and stop_event.is_set() or datetime.now(timezone.utc)>=deadline:
                        subprocess.run(['/usr/bin/systemctl','--user','stop',unit+'.service'],
                            stdout=stream,stderr=subprocess.STDOUT,timeout=20,check=True)
                        child.wait(timeout=30)
                        break
                    time.sleep(.1)
            finally:
                # Exiting the caller never turns an uncertain producer into settlement.
                pass
    return report.exists() and files.read(report,None).get('restoration',{}).get('restored') is True


def operation_deadline(engine,root,link):
    """A resume reads fresh authority without rewriting its original workflow file."""
    from sqlalchemy import select
    from workbench.db import real_execution_grants as grants, real_execution_requests as requests
    from workbench import bug_authority, real_execution
    with engine.connect() as c:
        grant=c.execute(select(grants).join(requests,requests.c.grant_id==grants.c.id).where(
            requests.c.status=='waiting_ai',grants.c.snapshot['recovery']['root'].as_string()==str(root/'real-run'))).mappings().one_or_none()
        if not grant or not grant['snapshot'].get('policy_operation'):
            return link['run_deadline']
        if not real_execution.valid(grant):
            raise ValueError('The new bounded operation grant expired or was withdrawn')
        bug_authority.validate_operation(c,grant)
        linked=grant['snapshot']['workflow_task']
        if link['task']!=dict(intake_id=linked['intake_id'],task_id=linked['task']['id'],approval_id=linked['approval']['id']):
            raise ValueError('Resume workflow belongs to another task')
        from datetime import datetime
        return datetime.fromisoformat(grant['expires_at']).timestamp()
