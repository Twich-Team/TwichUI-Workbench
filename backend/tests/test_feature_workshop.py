"""Persisted feature projections through real APIs/worker, with controlled providers."""
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from workbench import planning_worker, real_execution
from workbench.api import create_app
from workbench.db import engine_for, proposal_decisions, planning_jobs, write_transaction
from workbench.real_workspace import durable
from test_task_execution import prepared, ControlledPlanner
from test_real_handoff import HandoffRuntime
from test_intake import submit, decide


def feature(client, identity):
    response = client.get('/api/features/' + identity)
    assert response.status_code == 200, response.text
    return response.json()


def test_saved_feature_identity_reload_restart_and_duplicate_conflict(client, headers, store):
    data, body = submit(client, headers, requested_work='feature', description='  My exact feature\nrequest.  ')
    before = feature(client, data['id'])
    assert before['objective'] == body['description'] and before['state'] == 'held'
    assert before['completed_steps'] == before['total_steps'] == 0
    assert not before['active'] and not before['needs_owner']
    assert client.post('/api/intake', headers=headers, json=body).status_code == 200
    assert client.post('/api/intake', headers=headers, json={**body, 'description':'changed'}).status_code == 409
    assert client.get('/api/features').json()['total'] == 1
    restarted = engine_for(store.url.database)
    with TestClient(create_app(restarted)) as second:
        after = feature(second, data['id'])
        assert after['intake'] == before['intake']
        assert after['objective'] == before['objective']
    restarted.dispose()


def test_feature_plan_worker_and_pending_decision_are_truthful(client, headers, store):
    data, _ = submit(client, headers, visual_requirement='nonvisual')
    queued = feature(client, data['id'])
    assert queued['state'] == 'planning' and not queued['active']
    assert planning_worker.process_one(store, ControlledPlanner())
    planned = feature(client, data['id'])
    assert planned['state'] == 'needs_owner_action' and planned['needs_owner']
    assert planned['intake']['proposals'] and not planned['steps']
    response = decide(client, headers, planned['intake'])
    assert response.status_code == 200
    pending = feature(client, data['id'])
    assert not pending['needs_owner'] and not pending['active']
    assert 'waiting to apply' in pending['message']
    assert planning_worker.apply_one(store)
    assert planning_worker.process_one(store, ControlledPlanner())
    tasks = feature(client, data['id'])
    assert tasks['total_steps'] > 0 and tasks['completed_steps'] == 0
    assert all(not s['verified'] for s in tasks['steps']) and not tasks['active']
    assert tasks['next_step']


def test_checked_step_does_not_certify_parent_and_stale_scope_cannot_count(client, headers, store, tmp_path, monkeypatch):
    task = prepared(client, headers, store)
    root = tmp_path / 'NON_MODEL'
    root.mkdir()
    durable(root / 'oracle.lua', b'-- controlled non-model data')
    durable(root / 'base-manifest.json', b'{}')
    runtime = HandoffRuntime(root)
    real_execution.issue(store, 'NON-MODEL feature projection ' + str(uuid4()),
                         'disposable pytest', runtime, task=task)
    assert real_execution.process_one(store, runtime)
    checked = feature(client, task['intake_id'])
    assert checked['completed_steps'] == checked['total_steps'] == 1
    assert checked['steps'][0]['verified']
    assert checked['state'] == 'held' and checked['integrated_verification'] == 'not_recorded'
    assert 'Combined feature verification' in checked['message']
    with monkeypatch.context() as patch:
        patch.setattr('workbench.result_rejection.later_review', lambda *a:dict(state='rejected', reason='A later independent check disproved the patch.'))
        rejected = feature(client, task['intake_id'])
        assert rejected['completed_steps'] == 0
        assert rejected['steps'][0]['state'] == 'held'
        assert 'disproved' in rejected['message']
    with write_transaction(store) as c:
        c.execute(proposal_decisions.update().where(proposal_decisions.c.id == task['approval_id']).values(status='stale'))
    stale = feature(client, task['intake_id'])
    assert stale['completed_steps'] == 0
    assert len(stale['steps'][0]['execution']['runs']) == 1
    assert not stale['steps'][0]['execution']['runs'][0]['current_task']
    # Read projections never create a grant, decision, attempt or publication.
    with store.connect() as c:
        assert len(c.execute(select(real_execution.requests)).all()) == 1


def test_only_feature_requests_appear_and_unknown_identity_is_404(client, headers):
    submit(client, headers, kind='issue')
    submit(client, headers, requested_work='inspection')
    submit(client, headers, regression_tests={'acceptance':['Existing behavior is covered'], 'test_paths':[]})
    assert client.get('/api/features').json()['total'] == 0
    assert client.get('/api/features/' + str(uuid4())).status_code == 404


def test_expired_planning_lease_is_not_presented_as_live_activity(client, headers, store):
    data, _ = submit(client, headers)
    job = planning_worker.claim(store)
    assert feature(client, data['id'])['active']
    with write_transaction(store) as c:
        c.execute(planning_jobs.update().where(planning_jobs.c.id == job['id']).values(
            lease_until='2000-01-01T00:00:00+00:00'))
    expired = feature(client, data['id'])
    assert not expired['active'] and expired['state'] == 'held'
