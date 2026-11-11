"""Ordinary feature decisions and Stop use real existing owners, no model runs."""
from uuid import uuid4

from sqlalchemy import select

from workbench import feature_execution, worker
from workbench.db import proposal_decisions, planning_jobs, real_execution_requests, model_attempts
from test_feature_permission import ready, enable
from test_everyday import installed as installed


def test_run_offer_and_parent_command_are_bounded_private_and_replay_safe(client, headers, store, installed):
    identity, _ = ready(client, headers, store)
    path = '/api/features/'+identity
    assert client.get(path+'/run-offer').json()['offer'] is None
    enable()
    offer = client.get(path+'/run-offer').json()['offer']
    assert set(offer) == {'binding','budget','step_count','maximum_generated_tokens','provider','model'}
    assert offer['step_count'] == 2 and offer['budget']['implementation_fallback'] is False
    assert offer['budget']['child_publication'] == 'hold'
    body = dict(command_id=str(uuid4()), binding=offer['binding'])
    assert client.post(path+'/run', json=body).status_code == 403
    assert client.post(path+'/run', headers=headers, json={**body,'budget':{'per_attempt_tokens':999999}}).status_code == 422
    saved = client.post(path+'/run', headers=headers, json=body)
    assert saved.status_code == 200, saved.text
    assert client.post(path+'/run', headers=headers, json=body).json() == saved.json()
    assert client.post(path+'/run', headers=headers, json={**body,'command_id':str(uuid4())}).status_code == 409
    pending = client.get(path).json()
    assert pending['run']['id'] == body['command_id'] and pending['run']['can_stop']
    assert not pending['active'] and pending['completed_steps'] == 0
    assert client.get(path+'/run-offer').json()['offer'] is None
    with store.connect() as c:
        assert len(c.execute(select(proposal_decisions)).all()) == 2
        assert len(c.execute(select(planning_jobs).where(planning_jobs.c.kind == 'feature_execution')).all()) == 1
        assert not c.execute(select(real_execution_requests)).first()
        assert len(c.execute(select(model_attempts)).all()) == 2  # Original plan and graph only.


def test_stop_pending_admission_prevents_every_model_and_cannot_target_another_feature(client, headers, store, installed):
    identity, _ = ready(client, headers, store)
    enable()
    path = '/api/features/'+identity
    offer = client.get(path+'/run-offer').json()['offer']
    body = dict(command_id=str(uuid4()), binding=offer['binding'])
    assert client.post(path+'/run',headers=headers,json=body).status_code == 200
    stop = dict(command_id=str(uuid4()))
    suffix = '/runs/'+body['command_id']+'/stop'
    assert client.post('/api/features/'+str(uuid4())+suffix,headers=headers,json=stop).status_code == 404
    assert client.post(path+suffix,headers=headers,json=stop).status_code == 200
    assert client.post(path+suffix,headers=headers,json=stop).status_code == 200
    assert client.post(path+suffix,headers=headers,json=dict(command_id=str(uuid4()))).status_code == 409
    assert not worker.tick(store)
    record = client.get(path).json()
    assert record['run']['cancel_requested'] and not record['run']['can_stop'] and not record['active']
    assert record['state'] == 'held' and record['completed_steps'] == 0
    assert 'stopped before admission' in record['run']['message']
    with store.connect() as c:
        assert not c.execute(select(real_execution_requests)).first()
        assert len(c.execute(select(model_attempts)).all()) == 2


def test_stop_admitted_parent_before_first_scope_uses_same_existing_session(client, headers, store, installed):
    identity, _ = ready(client, headers, store)
    enable()
    path = '/api/features/'+identity
    offered = client.get(path+'/run-offer').json()['offer']
    body = dict(command_id=str(uuid4()),binding=offered['binding'])
    assert client.post(path+'/run',headers=headers,json=body).status_code == 200
    assert feature_execution.apply_one(store)
    before = client.get(path).json()
    assert before['state'] == 'ready' and before['run']['can_stop'] and not before['active']
    suffix = '/runs/'+body['command_id']+'/stop'
    assert client.post(path+suffix,headers=headers,json=dict(command_id=str(uuid4()))).status_code == 200
    assert feature_execution.advance(store)  # Hold the cancelled journal; never dispatch.
    assert not feature_execution.advance(store)
    after = client.get(path).json()
    assert after['run']['status'] == 'cancelled' and not after['run']['can_stop']
    with store.connect() as c:
        assert len(c.execute(select(model_attempts)).all()) == 2
        assert not c.execute(select(real_execution_requests)).first()
