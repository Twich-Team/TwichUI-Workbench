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


def test_exact_override_applied_by_worker_and_frozen_across_global_change(store,client,headers,installed,catalog,monkeypatch):
    from workbench import codex_provider
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
