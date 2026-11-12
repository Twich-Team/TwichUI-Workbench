"""Native storage interruption recovery retains attempt, deadline and Stop."""
import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from workbench import feature_execution, feature_steps, feature_preparation_recovery as recovery
from workbench import task_sessions, task_session
from workbench.db import planning_jobs, proposal_decisions, task_sessions as sessions, write_transaction
from workbench.real_workspace import Runtime, durable
from test_everyday import installed as installed
from test_feature_execution import missing_names_parent, controlled_readiness, controlled_check
from test_feature_preparation import ScopePlanner


def interrupted(client, headers, store, monkeypatch):
    monkeypatch.setattr(Runtime,'check',controlled_check)
    identity, decision, first, _ = missing_names_parent(client,headers,store)
    import os
    path=Path(os.environ['WORKBENCH_PREPARATION_CONFIG'])
    policy=json.loads(path.read_text())
    policy['output_root']=str(task_sessions.ROOT/'artifacts/runs/preparations')
    Path(policy['output_root']).mkdir(parents=True,mode=0o700)
    path.write_text(json.dumps(policy))
    assert feature_execution.advance(store,adapter=ScopePlanner())
    assert feature_execution.advance(store,provision=controlled_readiness)
    assert feature_execution.advance(store)

    def cancelled(engine, config_path, stop_event=None):
        config=json.loads(config_path.read_text())
        with engine.connect() as c:
            approval=c.execute(select(proposal_decisions).where(proposal_decisions.c.id==decision)).mappings().one()
            permission=feature_steps.permission(c,approval,first,offer_id=config['feature_offer_id'])
            template=feature_steps.preparation_inputs(c,permission['offer'])
        root=Path(config['run_root'])
        real=root/'real-run'
        real.mkdir(mode=0o700)
        durable(real/'recipe.json',json.dumps(template['recipe']).encode())
        durable(root/'template.json',json.dumps(template).encode())
        from workbench.real_workspace import sha
        from workbench.real_execution import owner_identity
        gone={**owner_identity(),'pid':999999999,'start':'0'}
        durable(root/'preparation-owner.json',json.dumps({'owner':gone,
            'recipe_sha256':sha((real/'recipe.json').read_bytes())}).encode())
        task_session.finish(engine,config,'cancelled','Stopped during readiness checks. Partial evidence is retained; no model started.')

    assert task_sessions.process_one(store,advance=cancelled)
    with write_transaction(store) as c:
        row=c.execute(select(sessions).where(sessions.c.id==decision)).mappings().one()
        state={**row['state'],'feature_slot_state':{**row['state']['feature_slot_state'],'resource_hold':recovery.RESOURCE_HOLD}}
        c.execute(sessions.update().where(sessions.c.id==decision).values(state=state))
    assert feature_execution.advance(store)
    return identity,decision,first


@pytest.mark.parametrize('fault',[None,'stop','terminal','active_unit','expired','parent_expired','changed_scope','grant'])
def test_native_preparation_recovery_preserves_generation_budget_and_original_deadline(
    client,headers,store,installed,monkeypatch,fault,
):
    identity,decision,first=interrupted(client,headers,store,monkeypatch)
    monkeypatch.setattr(recovery,'readiness_stopped',lambda root:fault != 'active_unit')
    with store.connect() as c:
        parent=c.execute(select(sessions).where(sessions.c.id==decision)).mappings().one()
        old_slot=parent['state']['feature_slot']
        old_deadline=old_slot['snapshot']['deadline']
    if fault in {'expired','parent_expired'}:
        from datetime import datetime, timedelta, timezone
        instant = datetime.now(timezone.utc)+timedelta(hours=2 if fault=='expired' else 7)
        class Clock(datetime):
            @classmethod
            def now(cls,tz=None):
                return instant if tz is not None else instant.replace(tzinfo=None)
        monkeypatch.setattr(recovery,'datetime',Clock)
        monkeypatch.setattr('workbench.intake.now',lambda:instant.isoformat())
    if fault in {'stop','changed_scope'}:
        with write_transaction(store) as c:
            if fault=='stop':
                state={**parent['state'],'cancel_requested':True,'stop_command':str(uuid4())}
            else:
                import copy
                state=copy.deepcopy(parent['state'])
                slot=state['feature_slot']
                slot['snapshot']['budget_fingerprint']='changed'
                from workbench.visual_scope import digest
                slot['binding']=digest(slot['snapshot'])
            c.execute(sessions.update().where(sessions.c.id==decision).values(state=state))
    elif fault=='grant':
        from workbench.db import real_execution_grants
        from workbench.visual_scope import digest
        from workbench.intake import now
        snapshot={'task_budget':{'offer':{'id':old_slot['snapshot']['feature_offer_id']}}}
        with write_transaction(store) as c:
            c.execute(real_execution_grants.insert().values(id=str(uuid4()),
                snapshot=snapshot,binding=digest(snapshot),issued_at=now(),expires_at=old_deadline))
    elif fault=='terminal':
        root=task_sessions.ROOT/'artifacts/runs'/('task-lease-task-session-'+old_slot['id']+'-01')
        (root/'session-finished.json').write_text('{}')
    assert recovery.resume(store) is (fault in {None,'expired'})
    if fault not in {None,'expired'}:
        return
    assert feature_execution.advance(store,provision=controlled_readiness)
    assert feature_execution.advance(store)
    with store.connect() as c:
        parent=c.execute(select(sessions).where(sessions.c.id==decision)).mappings().one()
        slot=parent['state']['feature_slot']
        assert slot['id'] != old_slot['id']
        assert slot['snapshot']['deadline'] > old_deadline
        assert slot['snapshot']['deadline'] <= job_parent_deadline(c,decision)
        assert parent['state']['feature_history'][-1]['preparation_recovery']['expires_at'] == old_deadline
        job=c.execute(select(planning_jobs).where(planning_jobs.c.kind=='feature_execution')).mappings().one()
        assert job['payload'].get('current_attempt',1)==1
        assert job['payload']['current_preparation_recovery']['attempt_reset'] is False
        assert parent['state']['feature_history'][-1]['slot']==old_slot
    # Stop remains available in this resumed parent.
    task_sessions.cancel(store,decision,str(uuid4()))


def job_parent_deadline(c,decision):
    from workbench.feature_run_recovery import deadline
    row=c.execute(select(proposal_decisions).where(proposal_decisions.c.id==decision)).mappings().one()
    return deadline(c,row,row['payload']['feature_run'])


@pytest.mark.parametrize('populated',[False,True])
def test_stopped_readiness_needs_no_service_manager_socket(tmp_path,monkeypatch,populated):
    from workbench.real_execution import owner_identity
    from workbench.real_workspace import sha
    root=tmp_path/'task-lease-native'
    (root/'real-run').mkdir(parents=True)
    durable(root/'real-run/recipe.json',b'{}')
    durable(root/'preparation-owner.json',json.dumps(dict(owner={**owner_identity(),
        'pid':999999999,'start':'0'},recipe_sha256=sha((root/'real-run/recipe.json').read_bytes()))).encode())
    manager=tmp_path/'cgroups'
    manager.mkdir()
    if populated:
        group=manager/('workbench-real-prepare-'+root.name+'.service')
        group.mkdir()
        (group/'cgroup.procs').write_text('123')
    monkeypatch.setattr(recovery,'manager_root',lambda:manager)
    monkeypatch.setattr(recovery.subprocess,'run',lambda *a,**k:pytest.fail('Service-manager query escaped into isolated permission validation'))
    assert recovery.readiness_stopped(root) is (not populated)


@pytest.mark.parametrize('fault',[None,'restore','probe','helper','owner','epoch','descendants','stop'])
def test_known_pre_grant_controller_failure_keeps_unused_attempt_and_probe_accounting(
    client,headers,store,installed,monkeypatch,tmp_path,fault,
):
    from workbench import local_generation_recovery as settlement,real_execution
    from workbench.db import model_attempts
    from workbench.intake import now
    from workbench.real_workspace import sha
    identity,decision,first=interrupted(client,headers,store,monkeypatch)
    manager=tmp_path/'cgroup-manager'
    manager.mkdir()
    monkeypatch.setattr(recovery,'manager_root',lambda:manager)
    monkeypatch.setattr(settlement,'normal_restored',lambda *a:fault!='restore')
    monkeypatch.setattr(settlement,'absent_epoch',lambda epoch:fault!='epoch')
    with store.connect() as c:
        parent=c.execute(select(sessions).where(sessions.c.id==decision)).mappings().one()
        slot=parent['state']['feature_slot']
        prior=dict(c.execute(select(model_attempts)).mappings().first())
    root=task_sessions.ROOT/'artifacts/runs'/('task-lease-task-session-'+slot['id']+'-01')
    helper=task_sessions.ROOT/'backend/workbench/feature_preparation_recovery.py'
    helper.parent.mkdir(parents=True)
    durable(helper,b'CONTROLLED frozen pre-grant query failure')
    monkeypatch.setattr(recovery,'READINESS_QUERY_HELPER_SHA256',sha(helper.read_bytes()))
    if fault=='helper':
        helper.write_bytes(b'changed')
    private=task_sessions.ROOT/'.local'/('chronicle-primary-lease-'+root.name)
    private.mkdir(mode=0o700)
    epoch=[999999998,'0']
    durable(private/'state.json',json.dumps({'temporary_epoch':epoch}).encode())
    probe=dict(passed=True,implementation_attempts=0,purpose='capability_validation',epoch=epoch,
        generated_tokens=None,generated_lower_bound=1,generated_upper_bound=60)
    durable(root/'probe-result.json',json.dumps(probe).encode())
    owner={**real_execution.owner_identity(),'pid':999999999,'start':'0'}
    if fault=='owner':
        owner=real_execution.owner_identity()
    durable(root/'controller-owner.json',json.dumps(owner).encode())
    durable(root/'session-claimed.json',b'{}')
    durable(root/'implementation-controller.log',b'ValueError: The previous readiness unit has not stopped.')
    report=dict(private_recovery_directory=str(private),implementation_attempt_consumed=False,
        inference_ran=False,accepted=False,implementation_controller_exit=1,probe_generation_requests=1,
        restoration=dict(restored=True,original_access_verified=True,original_config_verified=True,temporary_access_rejected=True))
    durable(root/'lease-controller-result.json',json.dumps(report).encode())
    if fault=='descendants':
        group=manager/('workbench-real-chronicle-'+root.name+'.service')
        group.mkdir()
        (group/'cgroup.procs').write_text('123')
    outcome=json.loads((root/'session-finished.json').read_text())
    outcome['status']='ended-before-result'
    (root/'session-finished.json').write_text(json.dumps(outcome))
    with write_transaction(store) as c:
        state={**parent['state'],'feature_slot_state':{'outcome':outcome},
            'feature_slot_result':{**parent['state']['feature_slot_result'],'status':'ended-before-result','outcome':outcome}}
        c.execute(sessions.update().where(sessions.c.id==decision).values(status='ended-before-result',state=state))
        prior.update(id=str(uuid4()),job_id=None,task_id=identity,backend='local',purpose='capability_validation',
            started_at=now(),finished_at=now(),outcome='passed',usage=probe if fault!='probe' else None)
        c.execute(model_attempts.insert().values(**prior))
    assert client.get('/api/features/'+identity).json()['run']['can_stop']
    if fault=='stop':
        task_sessions.cancel(store,decision,str(uuid4()))
    assert recovery.resume(store) is (fault is None)
    if fault is not None:
        return
    # Future permission checks run after the NEW model lease starts. They must
    # validate the old stopped epoch without probing current normal access or DBus.
    monkeypatch.setattr(settlement,'normal_restored',lambda *a:pytest.fail('Old normal-access check repeated during a new lease'))
    monkeypatch.setenv('DBUS_SESSION_BUS_ADDRESS','unix:path=/unavailable')
    assert feature_execution.advance(store,provision=controlled_readiness)
    assert feature_execution.advance(store)
    with store.connect() as c:
        current=c.execute(select(sessions).where(sessions.c.id==decision)).mappings().one()
        assert current['state']['feature_slot']['id'] != slot['id']
        assert current['state']['feature_history'][-1]['preparation_recovery']['attempt']==1
        saved=c.execute(select(model_attempts).where(model_attempts.c.id==prior['id'])).mappings().one()
        assert dict(saved)==prior
        assert saved['usage']['generated_tokens'] is None
