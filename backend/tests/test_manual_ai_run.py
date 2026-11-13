"""Explicit run choice journal; source/owner checkpoint verification is tested separately."""
from uuid import uuid4
import copy
import pytest
from fastapi import HTTPException

from sqlalchemy import select

from workbench import ai_strategy,manual_ai,real_execution
from workbench.db import planning_jobs,task_sessions,write_transaction,proposals,proposal_decisions,backlog_items
from test_everyday import installed,body,save_codex_policy
from test_task_ai_settings import catalog
from test_real_execution import granted

__all__=['installed','catalog']


def test_manual_coding_choice_is_additional_authority_and_keeps_original_grant_and_usage(
    store,client,headers,installed,catalog,tmp_path,monkeypatch,
):
    save_codex_policy(store)
    response=client.post('/api/intake',headers=headers,json=body())
    assert response.status_code==200,response.text
    identity=response.json()['id']
    _,_,created=granted(store)
    fence,parent_id=str(uuid4()),str(uuid4())
    frozen=dict(provider='smart',model='capable-codex',effort='high',policy_revision=1,fallback=[],
        strategy=ai_strategy.Strategy(stages=[ai_strategy.Stage(id='one',provider='codex',model='capable-codex',effort='high',allocation=50)]).model_dump())
    prefix=dict(status='complete',units='tokens',requests_observed=1,values=dict(prompt_tokens=20,completion_tokens=5,total_tokens=25))
    with write_transaction(store) as c:
        grant=dict(c.execute(select(real_execution.grants)).mappings().one())
        snapshot={**grant['snapshot'],'recovery':dict(root=str(tmp_path)),
            'task_budget':dict(decision_id=parent_id)}
        c.execute(real_execution.grants.update().values(snapshot=snapshot,binding=real_execution.digest(snapshot)))
        c.execute(real_execution.requests.update().values(status='waiting_ai',fence=fence,result=dict(
            ai_wait=dict(state='waiting',reason='Waiting for allocation.',recheck_at=9999999999),
            ai_checkpoint=dict(path=str(tmp_path/'checkpoint.json'),sha256='a'*64,sequence=1),usage=prefix)))
        c.execute(planning_jobs.update().values(status='failed',finished_at=real_execution.now()))
        proposal_id=str(uuid4())
        c.execute(proposals.insert().values(id=proposal_id,intake_id=identity,revision=1,scope_revision=0,content={},model='controlled',basis='demo',created_at=real_execution.now()))
        c.execute(proposal_decisions.insert().values(id=parent_id,intake_id=identity,proposal_id=proposal_id,binding='controlled',payload={},snapshot={},status='applied',message='Controlled authorization record',created_at=real_execution.now()))
        c.execute(backlog_items.insert().values(id='intake-'+identity,intake_id=identity,proposal_id=proposal_id,kind='request',title='Controlled run',purpose='Control test',acceptance=[],model='controlled',created_at=real_execution.now()))
        c.execute(task_sessions.insert().values(id=parent_id,task_id='intake-'+identity,
            snapshot=dict(task=dict(intake_id=identity)),binding='controlled',status='pending',
            message='Controlled settled checkpoint',state={},created_at=real_execution.now()))
    def verified(c,original):
        request=c.execute(select(real_execution.requests)).mappings().one()
        grant=c.execute(select(real_execution.grants)).mappings().one()
        parent=c.execute(select(task_sessions)).mappings().one()
        return dict(request=request,grant=grant,parent=parent,state=parent['state'],checkpoint={},binding='reviewed',base=frozen)
    monkeypatch.setattr(manual_ai,'available_run',verified)
    with store.connect() as c:
        original_grant=copy.deepcopy(dict(c.execute(select(real_execution.grants)).mappings().one()))
    command=dict(command_id=str(uuid4()),binding='reviewed',choice=dict(provider='codex',model='capable-codex',effort='low'),
        exceed_workbench_allocation=True)
    manual_ai.start(store,identity,manual_ai.StartNow.model_validate(command))
    manual_ai.start(store,identity,manual_ai.StartNow.model_validate(command))
    with pytest.raises(HTTPException) as held:
        manual_ai.start(store,identity,manual_ai.StartNow.model_validate({**command,'choice':{**command['choice'],'effort':'high'}}))
    assert held.value.status_code==409
    with store.connect() as c:
        after=dict(c.execute(select(real_execution.grants)).mappings().one())
        request=c.execute(select(real_execution.requests)).mappings().one()
        parent=c.execute(select(task_sessions)).mappings().one()
        authorization=manual_ai.effective_for_run(c,request,after,{})
    assert after==original_grant
    assert request['id']==created['id'] and request['fence']==fence
    assert request['result']['usage']==prefix
    assert authorization['route']['manual_stage']=='one' and authorization['route']['effort']=='low'
    assert original_grant['snapshot']['runtime']['model']=='unit-test-no-inference'
    assert parent['state']['ai_manual_history']==[]
    assert list(tmp_path.glob('ai-manual-*.json'))
