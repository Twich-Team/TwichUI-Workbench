"""Exact task choices survive global edits; unsupported choices never dispatch."""
import json
from uuid import uuid4
import pytest
from sqlalchemy import select
from workbench import everyday, model_policy, provider_routes, worker
from workbench.db import intakes, model_attempts
from test_everyday import installed, body, save_codex_policy
from test_grounded_workflow import ControlledPlanner

# Reuse the isolated everyday installation fixture; it never reaches a real provider.
__all__ = ['installed']


@pytest.fixture
def catalog(monkeypatch):
    report=model_policy.CapabilityReport(executors=[model_policy.ExecutorCapability(
        backend='codex',executor='Controlled',source='Test catalog',observed_at=model_policy.now(),
        models=[model_policy.ModelCapability(model='capable-codex',efforts=['low','high'])],limitation='No live use')])
    monkeypatch.setattr(model_policy,'capabilities',lambda:report)
    return report


def settings(provider='codex',model='capable-codex',effort='high',fallback=None):
    return dict(primary=dict(provider=provider,model=model,effort=effort),fallback=fallback)


@pytest.mark.parametrize('schema', ['0012','0013','0014'])
def test_exact_override_applied_by_worker_and_frozen_across_global_change(store,client,headers,installed,catalog,monkeypatch,schema):
    from workbench import codex_provider
    if schema=='0012':
        from alembic import command
        from alembic.config import Config
        with store.connect() as c:
            config=Config('alembic.ini')
            config.attributes['connection']=c
            command.downgrade(config,'0012')
            c.commit()
    save_codex_policy(store)
    request={**body(),'local_planning':'codex-once','ai_settings':settings()}
    response=client.post('/api/intake',headers=headers,json=request)
    assert response.status_code==200,response.text
    identity=response.json()['id']
    policy=model_policy.read(store)
    policy.providers['codex'].model='different-global-model'
    model_policy.save(store,policy,model_policy.CapabilityReport(executors=[]))
    store.dispose()  # New connections model process restart.
    planner=ControlledPlanner()
    calls=[]
    def generate(model,effort,prompt,schema,stop):
        calls.append((model,effort))
        raw,_,_=planner.generate(json.loads(prompt.split('Untrusted task data:\n')[1]),None)
        return raw,dict(prompt_tokens=40,completion_tokens=10,total_tokens=50,reasoning_tokens=0)
    monkeypatch.setattr(codex_provider,'generate',generate)
    assert worker.tick(store)
    assert calls==[('capable-codex','high')]
    assert not worker.tick(store)
    assert client.get('/api/usage').status_code==200
    assert client.get('/api/workspace').status_code==200
    detail=client.get('/api/intake/'+identity).json()
    assert detail['original']['ai_settings']==request['ai_settings']
    attempt=detail['provider_activity'][0]
    assert attempt['requested_model']=='capable-codex' and attempt['requested_effort']=='high'
    assert attempt['actual_effort'] is None  # Never infer an unreported effort.
    with store.connect() as c:
        route=provider_routes.approved(c,dict(c.execute(select(intakes).where(intakes.c.id==identity)).mappings().one()))
        assert route['model']=='capable-codex' and route['effort']=='high'
    assert client.post('/api/intake',headers=headers,json=request).status_code==200


@pytest.mark.parametrize('config,choice',[
    (settings(model='fictional-model'),'codex-once'),
    (settings(effort='ultra'),'codex-once'),
    (settings('local',everyday.MODEL,'high'),'qwen-once'),
    (settings(),'qwen-once'),
])
def test_unsupported_and_mismatched_choices_refuse_before_queue(store,client,headers,installed,catalog,config,choice):
    save_codex_policy(store)
    response=client.post('/api/intake',headers=headers,json={**body(),'request_id':str(uuid4()),'local_planning':choice,'ai_settings':config})
    assert response.status_code==409,response.text
    with store.connect() as c:
        assert not c.execute(select(model_attempts)).first()
        assert not c.execute(select(intakes)).first()


def test_fallback_pins_its_own_model_and_effort_without_expanding_permission(store,client,headers,installed,catalog):
    save_codex_policy(store,both=True)
    fallback=dict(provider='codex',model='capable-codex',effort='low')
    request={**body(),'local_planning':'qwen-with-codex-fallback','ai_settings':settings('local',everyday.MODEL,None,fallback)}
    response=client.post('/api/intake',headers=headers,json=request)
    assert response.status_code==200,response.text
    with store.connect() as c:
        row=dict(c.execute(select(intakes)).mappings().one())
        route=provider_routes.approved(c,row)
    assert route['codex']['model']=='capable-codex' and route['codex']['effort']=='low'
    saved=model_policy.read(store)
    saved.test_fallback='off'
    model_policy.save(store,saved,catalog)
    with store.connect() as c:
        assert provider_routes.approved(c,row)==route
    request['request_id']=str(uuid4())
    assert client.post('/api/intake',headers=headers,json=request).status_code==409


def test_cache_discovery_allowlists_public_fields_without_credentials_or_inference(tmp_path):
    cache=tmp_path/'cache.json'
    cache.write_text(json.dumps(dict(fetched_at=model_policy.now(),secret='never-return-this',models=[dict(
        slug='supported-codex',visibility='list',identity='private-account',
        supported_reasoning_levels=[dict(effort='high',description='not returned')]),
        dict(slug='hidden-codex',visibility='hide')])))
    result=model_policy.cached_codex_capability(cache)
    assert result.models[0].model=='supported-codex' and result.models[0].efforts==['high']
    assert len(result.models)==1
    assert not any(s in result.model_dump_json() for s in ['never-return-this','private-account','hidden-codex'])
    cache.write_text('{invalid')
    assert model_policy.cached_codex_capability(cache) is None


def test_claude_uses_actual_worker_route_and_keeps_its_frozen_override(
    store,client,headers,installed,catalog,monkeypatch,
):
    from workbench import claude_client
    model='claude-sonnet-5-5'
    catalog.executors.append(model_policy.ExecutorCapability(backend='claude',executor='Controlled native',
        source='Controlled catalog',observed_at=model_policy.now(),
        models=[model_policy.ModelCapability(model=model,efforts=['high'])],limitation='No real call'))
    save_codex_policy(store)
    policy=model_policy.read(store)
    policy.providers['claude']=model_policy.BackendPreference(model=model,effort='high',enabled=True)
    model_policy.save(store,policy,catalog)
    request={**body(),'local_planning':'claude-once','ai_settings':settings('claude',model,'high')}
    response=client.post('/api/intake',headers=headers,json=request)
    assert response.status_code==200,response.text
    identity=response.json()['id']
    policy=model_policy.read(store)
    policy.providers['claude']=model_policy.BackendPreference(model='another-model',effort=None,enabled=True)
    model_policy.save(store,policy,catalog)
    planner=ControlledPlanner()
    calls=[]
    def generate(actual,effort,prompt,schema,stop,*,request_id,**kwargs):
        calls.append((actual,effort,request_id))
        raw,_,_=planner.generate(json.loads(prompt.split('Untrusted task data:\n')[1]),None)
        return raw,dict(prompt_tokens=40,completion_tokens=10,total_tokens=50)
    monkeypatch.setattr(claude_client,'generate',generate)
    store.dispose()
    assert worker.tick(store)
    assert len(calls)==1 and calls[0][:2]==(model,'high')
    detail=client.get('/api/intake/'+identity).json()
    activity=detail['provider_activity'][0]
    assert activity['backend']=='claude' and activity['actual_model']==model
    assert activity['requested_effort']=='high' and activity['actual_effort'] is None
    assert detail['original']['ai_settings']==request['ai_settings']
    assert client.get('/api/usage').status_code==200
    assert not worker.tick(store)


def test_explicit_qwen_only_does_not_inherit_smart_remote_stages(store,client,headers,installed,catalog):
    from workbench.ai_strategy import Strategy,Stage
    save_codex_policy(store)
    saved=model_policy.read(store)
    saved.strategy=Strategy(stages=[Stage(id='remote',provider='codex',model='capable-codex',effort='high',allocation=50)])
    model_policy.save(store,saved,catalog)
    request={**body(),'local_planning':'qwen-once','ai_settings':settings('local',everyday.MODEL,None)}
    response=client.post('/api/intake',headers=headers,json=request)
    assert response.status_code==200,response.text
    with store.connect() as c:
        row=c.execute(select(intakes).where(intakes.c.id==response.json()['id'])).mappings().one()
        route=provider_routes.approved(c,row)
    assert route['model']==everyday.MODEL and 'strategy' not in route and not route.get('fallback')
