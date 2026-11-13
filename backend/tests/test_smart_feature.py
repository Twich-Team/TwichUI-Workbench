"""Feature intake -> actual strategy planner -> durable steps, with native IO controlled."""
import json
import time
from uuid import uuid4

from sqlalchemy import select

from workbench import ai_strategy as ai,model_policy,smart_provider,worker,planning_worker,feature_permission
from workbench.db import model_attempts
from workbench.schemas import PlanningOutput
from workbench.feature_planning import FeatureDecomposition
from test_feature_planning import submit,ControlledFeaturePlanner
from test_everyday import installed,save_codex_policy
from test_task_ai_settings import catalog

__all__=['installed','catalog']


def test_feature_plan_and_connected_steps_keep_frozen_claude_stage_after_global_change(
    client,headers,store,installed,catalog,monkeypatch,
):
    save_codex_policy(store)
    catalog.executors.append(model_policy.ExecutorCapability(backend='claude',executor='Controlled native',
        source='Controlled supported catalog',observed_at=model_policy.now(),
        models=[model_policy.ModelCapability(model='claude-sonnet-5-5',efforts=['high'])],limitation='No real inference'))
    saved=model_policy.read(store)
    saved.providers['claude']=model_policy.BackendPreference(enabled=True,model='claude-sonnet-5-5',effort='high')
    model_policy.save(store,saved,catalog)
    strategy=ai.Strategy(stages=[ai.Stage(id='codex',provider='codex',model='capable-codex',effort='high',allocation=50),
        ai.Stage(id='claude',provider='claude',model='claude-sonnet-5-5',effort='high',allocation=60)])
    calls=[]
    def exchange(request,stop,seconds):
        assert not stop()
        observations=[ai.Observation(provider=p,state='available',paid_usage='disabled',observed_at=time.time(),
            windows=[ai.Window(name='short',used_percent=100 if p=='codex' else 10,resets_at=time.time()+3600)]) for p in ('codex','claude')]
        decision=ai.evaluate(request['strategy'],observations,[],now=time.time())
        assert decision.provider=='claude'
        if request['operation']=='smart-choose':
            return dict(state='choice',decision=decision.model_dump())
        calls.append(request)
        packet=json.loads(request['prompt'].split('Untrusted task data:\n')[1])
        raw,_,_=ControlledFeaturePlanner().generate(packet,FeatureDecomposition if 'proposal' in packet else PlanningOutput,decision.model)
        return dict(state='completed',result=dict(actual_model=decision.model,output=raw,
            provider_receipt=str(uuid4()),usage=dict(prompt_tokens=30,completion_tokens=10,total_tokens=40)),admission={})
    monkeypatch.setattr(smart_provider,'exchange',exchange)
    item,body=submit(client,headers,local_planning='smart-strategy',ai_settings=dict(primary=dict(
        provider='codex',model='capable-codex',effort='high'),fallback=None,strategy=strategy.model_dump()))
    assert worker.tick(store)
    data=client.get('/api/intake/'+item['id']).json()
    assert data['status']=='completed',data
    changed=model_policy.read(store)
    changed.providers['claude'].model='different-later-global-model'
    changed.providers['claude'].effort=None
    model_policy.save(store,changed,catalog)
    store.dispose()
    assert worker.tick(store)
    data=client.get('/api/intake/'+item['id']).json()
    assert len([t for t in data['tasks'] if t['kind']=='task'])==2,data
    assert len(calls)==2 and all(c['stage_id']=='claude' for c in calls)
    proposal=data['proposals'][0]
    response=client.post('/api/intake/'+item['id']+'/decisions',headers=headers,json=dict(
        command_id=str(uuid4()),proposal_id=proposal['id'],binding=proposal['binding'],scope_revision=proposal['scope_revision'],
        action='approve',note='Controlled source-grounded scope review.'))
    assert response.status_code==200,response.text
    assert planning_worker.apply_one(store)
    with store.connect() as c:
        scope=feature_permission.scope(c,item['id'])
        assert scope['provider']=='smart' and scope['route']['strategy']==strategy.model_dump()
        rows=c.execute(select(model_attempts).where(model_attempts.c.task_id==item['id'],model_attempts.c.source=='workbench')).mappings().all()
    assert len(rows)==2 and all(r['backend']=='claude' and r['actual_model']=='claude-sonnet-5-5' for r in rows)
    assert all(r['requested_effort']=='high' and r['actual_effort'] is None for r in rows)
    assert data['original']['ai_settings']==body['ai_settings']
