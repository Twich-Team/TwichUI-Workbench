"""Real intake/worker/frozen-route path; replace the private native connector IO."""
import json
import time
from uuid import uuid4

from sqlalchemy import select

from workbench import ai_strategy as ai,model_policy,smart_provider,worker
from workbench.db import planning_jobs,model_attempts,write_transaction
from test_everyday import installed,body,save_codex_policy
from test_task_ai_settings import catalog
from test_grounded_workflow import ControlledPlanner

__all__=['installed','catalog']


def setup(store):
    save_codex_policy(store)
    return ai.Strategy(stages=[ai.Stage(id='primary',provider='codex',model='capable-codex',effort='high',
        allocation=50,on_limit='wait')])


def native(monkeypatch,strategy):
    state=dict(used=50,dispatches=[])
    def exchange(request,stop,seconds):
        assert not stop()
        observation=ai.Observation(provider='codex',state='available',paid_usage='disabled',observed_at=time.time(),
            source='Controlled native account',windows=[ai.Window(name='short',used_percent=state['used'],resets_at=time.time()+3600)])
        decision=ai.evaluate(request['strategy'],[observation,ai.Observation(provider='local',state='available',paid_usage='disabled',observed_at=time.time())],[],now=time.time(),**{k:request[k] for k in ('waiting_since','manual_stage','requested_stage','failures','suitable_local','reviewed_local')})
        if request['operation']=='smart-choose':
            return dict(state='choice',decision=decision.model_dump())
        assert request['operation']=='smart-turn'
        assert decision.state=='selected' and request['stage_id']==decision.stage_id
        state['dispatches'].append(request)
        raw,_,_=ControlledPlanner().generate(json.loads(request['prompt'].split('Untrusted task data:\n')[1]),None)
        return dict(state='completed',result=dict(actual_model=decision.model,output=raw,
            provider_receipt=str(uuid4()),usage=dict(prompt_tokens=40,completion_tokens=10,total_tokens=50)),
            admission=dict(stage_id=decision.stage_id,overrun=[]))
    monkeypatch.setattr(smart_provider,'exchange',exchange)
    return state


def submit(client,headers,strategy):
    first=strategy.stages[0]
    request={**body(),'local_planning':'smart-strategy','ai_settings':dict(primary=dict(
        provider=first.provider,model=first.model,effort=first.effort),fallback=None,strategy=strategy.model_dump())}
    response=client.post('/api/intake',headers=headers,json=request)
    assert response.status_code==200,response.text
    return response.json()['id']


def test_real_worker_waits_without_dispatch_and_resumes_frozen_choice_after_restart(
    store,client,headers,installed,catalog,monkeypatch,
):
    strategy=setup(store)
    state=native(monkeypatch,strategy)
    identity=submit(client,headers,strategy)
    assert worker.tick(store)
    with store.connect() as c:
        saved=c.execute(select(planning_jobs).where(planning_jobs.c.intake_id==identity)).mappings().one()
        assert saved['status']=='queued' and saved['attempts']==0
        assert saved['payload']['ai_wait']['provider']=='codex'
        assert not c.execute(select(model_attempts)).first()
    assert not state['dispatches']
    overview=client.get('/api/workspace').json()
    waiting=next(t for t in overview['items'] if t['id']==identity)
    assert waiting['ai_status']=='Work waiting for Codex allowance' and waiting['state']=='queued'
    assert not waiting['active'] and not waiting['needs_you']
    changed=model_policy.read(store)
    changed.providers['codex'].model='different-global-model'
    model_policy.save(store,changed,model_policy.CapabilityReport(executors=[]))
    store.dispose()
    # Advance the durable eligibility field, representing a verified reset/recheck.
    with write_transaction(store) as c:
        payload=dict(saved['payload'])
        payload['ai_wait']={**payload['ai_wait'],'recheck_at':time.time()-1}
        c.execute(planning_jobs.update().where(planning_jobs.c.id==saved['id']).values(payload=payload))
    state['used']=0
    assert worker.tick(store)
    detail=client.get('/api/intake/'+identity).json()
    assert detail['status']=='completed',detail.get('jobs') or detail
    assert len(state['dispatches'])==1
    assert state['dispatches'][0]['strategy']['stages'][0]['model']=='capable-codex'
    activity=detail['provider_activity']
    assert len(activity)==1 and activity[0]['backend']=='codex' and activity[0]['actual_model']=='capable-codex'
    assert activity[0]['requested_effort']=='high' and activity[0]['actual_effort'] is None
    with store.connect() as c:
        rows=c.execute(select(model_attempts)).mappings().all()
        assert sorted(r['source'] for r in rows)==['strategy-control','workbench']
    assert not worker.tick(store)
    assert client.get('/api/usage').json()['operations']==1


def test_manual_start_explicit_allocation_override_preserves_original_frozen_settings(
    store,client,headers,installed,catalog,monkeypatch,
):
    strategy=setup(store)
    state=native(monkeypatch,strategy)
    identity=submit(client,headers,strategy)
    assert worker.tick(store)
    detail=client.get('/api/intake/'+identity).json()
    original=detail['original']
    command=dict(command_id=str(uuid4()),binding=detail['start_now_binding'],
        choice=dict(provider='codex',model='capable-codex',effort='low'),exceed_workbench_allocation=True)
    response=client.post('/api/intake/'+identity+'/start-now',headers=headers,json=command)
    assert response.status_code==200,response.text
    assert response.json()['original']==original
    assert client.post('/api/intake/'+identity+'/start-now',headers=headers,json=command).status_code==200
    assert worker.tick(store)
    result=client.get('/api/intake/'+identity).json()
    assert result['status']=='completed',result
    assert len(state['dispatches'])==1
    sent=state['dispatches'][0]
    assert sent['manual_stage']=='primary' and sent['requested_stage']=='primary'
    assert sent['strategy']['stages'][0]['effort']=='low'
    assert result['original']['ai_settings']['primary']['effort']=='high'
    assert result['provider_activity'][0]['requested_effort']=='low'


def test_manual_start_cannot_bypass_real_provider_limit(
    store,client,headers,installed,catalog,monkeypatch,
):
    strategy=setup(store)
    state=native(monkeypatch,strategy)
    identity=submit(client,headers,strategy)
    assert worker.tick(store)
    detail=client.get('/api/intake/'+identity).json()
    response=client.post('/api/intake/'+identity+'/start-now',headers=headers,json=dict(
        command_id=str(uuid4()),binding=detail['start_now_binding'],
        choice=dict(provider='codex',model='capable-codex',effort='low'),exceed_workbench_allocation=True))
    assert response.status_code==200,response.text
    state['used']=100
    assert worker.tick(store)
    assert not state['dispatches']
    with store.connect() as c:
        row=c.execute(select(planning_jobs).where(planning_jobs.c.intake_id==identity)).mappings().one()
        assert row['status']=='queued' and row['payload']['ai_wait']['condition']=='provider_limit'


def test_new_task_model_override_keeps_global_allocation_guard_without_manual_bypass(
    store,client,headers,installed,catalog,monkeypatch,
):
    strategy=setup(store)
    saved=model_policy.read(store)
    saved.strategy=strategy
    model_policy.save(store,saved,catalog)
    state=native(monkeypatch,strategy)
    request={**body(),'local_planning':'codex-once','ai_settings':dict(primary=dict(
        provider='codex',model='capable-codex',effort='low'),fallback=None)}
    response=client.post('/api/intake',headers=headers,json=request)
    assert response.status_code==200,response.text
    assert worker.tick(store)
    assert not state['dispatches']
    detail=client.get('/api/intake/'+response.json()['id']).json()
    assert detail['start_now_binding']
    with store.connect() as c:
        saved_job=c.execute(select(planning_jobs).where(planning_jobs.c.intake_id==response.json()['id'])).mappings().one()
    frozen=saved_job['payload']['local_planning_receipt']['codex_route']
    assert frozen['requested_stage']=='primary' and 'manual_stage' not in frozen
    assert frozen['strategy']['stages'][0]['effort']=='low'
    assert frozen['strategy']['stages'][0]['allocation']==50


def test_local_connection_refusal_switches_only_before_generation_to_authorized_stage(
    store,client,headers,installed,catalog,monkeypatch,
):
    from workbench.planning_provider import LocalProvider,LocalBeforeGeneration
    setup(store)
    strategy=ai.Strategy(stages=[ai.Stage(id='qwen',provider='local',model='pilot-qwen38-27b'),
        ai.Stage(id='codex',provider='codex',model='capable-codex',effort='high',allocation=50,
            role='fallback',triggers=['unavailable'])])
    state=native(monkeypatch,strategy)
    state['used']=0
    calls=[]
    def refuse(self,*args,**kwargs):
        calls.append('local-readiness')
        self.diagnostics=dict(generation_state='not_started')
        raise LocalBeforeGeneration()
    monkeypatch.setattr(LocalProvider,'generate',refuse)
    identity=submit(client,headers,strategy)
    assert worker.tick(store)
    detail=client.get('/api/intake/'+identity).json()
    assert detail['status']=='completed',detail
    assert calls==['local-readiness'] and len(state['dispatches'])==1
    assert state['dispatches'][0]['stage_id']=='codex'
    assert sorted(a['outcome'] for a in detail['provider_activity'])==['completed','not_started']
