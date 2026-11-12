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
