"""Elapsed-grant renewal preserves the same request, source, prefix and permissions.

Provider boundaries and a completed coding prefix are controlled fixtures. No real
native generation or service launch occurs in this test.
"""
import json
import os
import time
from pathlib import Path
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, func
from workbench import bug_authority, worker, smart_provider, ai_strategy as ai, task_budget, task_execution, real_execution, smart_coding_wait
from workbench.db import real_execution_grants as grants, real_execution_requests as requests, proposal_decisions, task_run_offers, model_attempts, write_transaction
from workbench.codex_runtime import CodexRuntime
from test_everyday import installed, write_policy  # noqa: F401
from test_task_ai_settings import catalog  # noqa: F401
from test_smart_worker import setup
from test_bug_workflow import enable_bugs, report
from test_bug_intent_workflow import scope
from test_repair_profiles_v2 import ProfileRuntime
from test_bug_authority import enable
from test_verification_profiles import git, TEST, GOOD_TEST, PRODUCTION


class CodingRuntime(CodexRuntime,ProfileRuntime):
    def preflight(self):
        return self.static_preflight()


@pytest.mark.usefixtures('installed','catalog')
def test_fresh_policy_operation_after_long_wait_preserves_the_expired_grant_and_same_request(client,headers,store,monkeypatch):
    source=enable_bugs(monkeypatch)
    (source/TEST).write_text((source/TEST).read_text()+GOOD_TEST)
    git(source,'add','.')
    git(source,'-c','user.name=Fixture','-c','user.email=fixture@invalid','commit','-qm','Reported failing baseline')
    config=Path(os.environ['WORKBENCH_PREPARATION_CONFIG'])
    settings=json.loads(config.read_text())
    settings['bug_plan_review']='source-grounded-intent-v1'
    write_policy(config,settings)
    strategy=setup(store)
    enable(store)
    monkeypatch.setattr('workbench.trusted_preparation.Runtime',ProfileRuntime)
    monkeypatch.setattr('workbench.task_sessions.process_one',lambda *a,**k:False)
    native=[]
    def exchange(request,stop,seconds):
        assert not stop()
        stage=strategy.stages[0]
        decision=ai.Decision(state='selected',stage_id=stage.id,provider=stage.provider,model=stage.model,effort=stage.effort,role=stage.role,reason='Controlled fresh allowance')
        if request['operation']=='smart-choose':
            native.append('allowance')
            return dict(state='choice',decision=decision.model_dump())
        native.append('generation')
        value=dict(finding='The original assertion establishes the reported normalization failure.',action='plan',reads=[],searches=[],
            plan={**scope(),'repair_profile':dict(version=2,kind='existing-failure')},checks=None,question=None,limitation=None)
        return dict(state='completed',result=dict(actual_model=stage.model,output=value,provider_receipt=str(uuid4()),
            usage=dict(prompt_tokens=40,completion_tokens=10,total_tokens=50)),admission=dict(stage_id=stage.id,overrun=[]))
    monkeypatch.setattr(smart_provider,'exchange',exchange)
    body=report()
    first=strategy.stages[0]
    body.update(local_planning='smart-strategy',ai_settings=dict(primary=dict(provider=first.provider,model=first.model,effort=first.effort),fallback=None,strategy=strategy.model_dump()))
    response=client.post('/api/intake',headers=headers,json=body)
    assert response.status_code==200,response.text
    identity=response.json()['id']
    for _ in range(14):
        worker.tick(store)
    assert client.get('/api/intake/'+identity).status_code==200
    with store.connect() as c:
        offered=c.execute(select(task_run_offers)).mappings().one()
        approval=c.execute(select(proposal_decisions).where(proposal_decisions.c.intake_id==identity,
            proposal_decisions.c.payload['run_budget'].as_string().is_not(None))).mappings().one()
    prepared=Path(offered['snapshot']['ready_preparation']['root'])
    cycle=prepared.parent/'controlled-wait-operation'
    cycle.mkdir(mode=0o700)
    root=cycle/'real-run'
    root.mkdir(mode=0o700)
    from workbench.real_workspace import durable
    for name in ('recipe.json','recipe-oracle.lua'):
        durable(root/name,(prepared/name).read_bytes())
    with store.connect() as c:
        from workbench.db import intakes
        from workbench.provider_routes import approved
        selection=approved(c,dict(c.execute(select(intakes).where(intakes.c.id==identity)).mappings().one()))
    runtime=CodingRuntime(root,selection)
    from workbench import task_session
    task_session.provision(runtime,dict(control_patches={}))
    runtime.task_ref=dict(intake_id=identity,task_id=runtime.recipe['contract']['task_id'],approval_id=approval['id'])
    assert task_budget.authorize_one(store,runtime)
    assert task_execution.dispatch_one(store)
    with store.connect() as c:
        row=dict(c.execute(select(requests)).mappings().one())
        old=dict(c.execute(select(grants)).mappings().one())
    # Retain a controlled complete native prefix and an unfinished harmless edit.
    (runtime.workspace/PRODUCTION).write_text((runtime.workspace/PRODUCTION).read_text()+'-- retained progress\n')
    fence=str(uuid4())
    receipt=dict(provider='codex',model=first.model,effort=first.effort,provider_receipt='controlled-terminal-receipt',values=dict(prompt_tokens=100,completion_tokens=20,total_tokens=120))
    path=root/('codex-turn-'+row['id']+'-1.json')
    durable(path,json.dumps(receipt,sort_keys=True).encode())
    path.chmod(0o600)
    runtime.ai_progress=dict(next_turn=1,receipts=[receipt],feedback=dict(status='fail'),changed=True,seen=[])
    runtime.ai_phase_started=time.monotonic()-15
    decision=dict(state='waiting',reason='Controlled allowance reset',provider='codex',stage_id=first.id,recheck_at=time.time()-1)
    checkpoint=smart_coding_wait.checkpoint(runtime,row['id'],fence,decision)
    with write_transaction(store) as c:
        c.execute(requests.update().where(requests.c.id==row['id']).values(status='waiting_ai',fence=fence,
            result=dict(owner=real_execution.owner_identity(),ai_checkpoint=checkpoint,ai_wait=decision)))
        c.execute(model_attempts.insert().values(id=fence,execution_id=row['id'],task_id=identity,source='strategy-control',backend='smart',
            purpose='implementation',requested_model=first.model,requested_effort=first.effort,started_at=real_execution.now(),outcome='waiting',
            routing_reason='Controlled saved native prefix',usage=dict(status='complete',units='tokens',values=receipt['values'],requests_observed=1)))
    with store.connect() as c:
        row=dict(c.execute(select(requests)).mappings().one())
    future=datetime.now().astimezone()+timedelta(hours=2)
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):
            return future.astimezone(tz) if tz else future.replace(tzinfo=None)
    monkeypatch.setattr(real_execution,'datetime',Clock)
    monkeypatch.setattr(task_budget,'datetime',Clock)
    monkeypatch.setattr(bug_authority,'datetime',Clock)
    monkeypatch.setattr(real_execution,'alive',lambda _:False)
    assert not real_execution.valid(old)
    launches=[]
    before_allowance=native.count('allowance')
    def launch(command):
        launches.append(command)
        report=cycle/'ai-controller-result-1.json'
        durable(report,json.dumps(dict(restoration=dict(restored=True,no_lease=True))).encode())
        report.chmod(0o600)
    config=dict(deadline=approval['created_at'])
    assert smart_coding_wait.resume_controller(store,cycle,row,config,launch=launch)
    assert len(launches)==1 and native.count('allowance')==before_allowance+1
    with store.connect() as c:
        fresh=dict(c.execute(select(grants).where(grants.c.id!=old['id'])).mappings().one())
    assert smart_coding_wait.resume_controller(store,cycle,row,config,launch=launch)
    assert len(launches)==1 and native.count('allowance')==before_allowance+1
    assert fresh['id']!=old['id'] and real_execution.valid(fresh)
    with store.connect() as c:
        current=dict(c.execute(select(requests)).mappings().one())
        assert current['id']==row['id'] and current['fence']==fence and current['grant_id']==fresh['id']
        assert current['payload']==row['payload']
        assert c.scalar(select(func.count()).select_from(requests))==1
        assert c.scalar(select(func.count()).select_from(grants))==2
        assert dict(c.execute(select(grants)).mappings().first())['snapshot']==old['snapshot']
        task_execution.validate(c,fresh)
        with pytest.raises(ValueError):
            task_budget.permission(c,approval)
        operation=bug_authority.validate_operation(c,fresh)
        assert operation['completed_requests']==1 and operation['active_seconds']>=15
        saved_usage=c.execute(select(model_attempts.c.usage).where(model_attempts.c.id==fence)).scalar_one()
        assert saved_usage['values']['total_tokens']==120
    loaded=smart_coding_wait.load(runtime,current,fresh)
    assert loaded['progress']['receipts']==[receipt] and loaded['source_tree'][PRODUCTION]
    assert not task_execution.dispatch_one(store)
    assert native.count('generation')==1  # Planning only; renewal generated nothing.

    store.dispose()
    from workbench.db import engine_for
    reopened=engine_for(store.url.database)
    try:
        with reopened.connect() as c:
            task_execution.validate(c,dict(c.execute(select(grants).where(grants.c.id==fresh['id'])).mappings().one()))
        link=dict(task=runtime.task_ref,run_deadline=0)
        assert smart_coding_wait.operation_deadline(reopened,cycle,link)==datetime.fromisoformat(fresh['expires_at']).timestamp()
        # Disabling policy keeps all previous rows but blocks another operation.
        current_policy=bug_authority.read(reopened)
        bug_authority.save(reopened,bug_authority.ControlInput(revision=current_policy.revision,enabled=False))
        with reopened.connect() as c:
            with pytest.raises(task_execution.Waiting):
                task_execution.validate(c,fresh)
        assert client.get('/api/intake/'+identity).status_code==200
    finally:
        reopened.dispose()
