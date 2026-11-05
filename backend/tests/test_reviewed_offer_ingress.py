"""Trusted local registration + public durable approval; real Lua, NO model/lease/PR."""
import copy
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from workbench import intake, planning_worker, task_budget, task_execution, task_sessions, trusted_preparation
from workbench.db import proposal_decisions, task_sessions as sessions
from test_reviewed_offers import prepared as prepare_fixture, authority_counts, git
from test_site_task_sessions import installed as installed


def prepared(*args, **kwargs):
    ref, runtime, source = prepare_fixture(*args, **kwargs)
    runtime.root.chmod(0o700)
    return ref, runtime, source


def context(client, ref):
    path = f"/api/intake/{ref['intake_id']}/tasks/{ref['task_id']}/run-approval"
    response = client.get(path)
    assert response.status_code == 200, response.text
    return path, response.json()


def approval(data):
    return dict(command_id=str(uuid4()), scope_approval_id=data['scope_approval_id'],
                binding=data['binding'], run_budget=data['offer']['budget'], note='Controlled browser approval')


def test_supported_local_preparation_and_browser_budget_reach_existing_session(
    store, client, headers, tmp_path, monkeypatch, installed
):
    ref, runtime, _ = prepared(store, client, headers, tmp_path, monkeypatch)
    before = authority_counts(store)
    selected = trusted_preparation.register(store, ref, runtime.root)
    assert trusted_preparation.register(store, ref, runtime.root) == selected
    assert authority_counts(store) == before
    path, data = context(client, ref)
    assert data['available'] and data['runtime_ready']
    assert data['scope']['task_contract']['writable_paths'] == runtime.recipe['contract']['paths']
    assert data['offer']['checks'] == data['scope']['task_contract']['verification_commands']
    body = approval(data)
    response = client.post(path, headers=headers, json=body)
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt['status'] == 'pending'
    assert client.post(path, headers=headers, json=body).json() == receipt
    assert not task_sessions.queue_one(store)
    with pytest.raises(RuntimeError):
        planning_worker.apply_one(store, command_id=body['command_id'], fail_before_commit=True)
    assert context(client, ref)[1]['receipt']['status'] == 'pending'
    assert planning_worker.apply_one(store, command_id=body['command_id'])
    assert not planning_worker.apply_one(store, command_id=body['command_id'])
    assert client.post(path, headers=headers, json={**body, 'command_id': str(uuid4())}).status_code == 409
    assert task_sessions.queue_one(store) and not task_sessions.queue_one(store)
    calls = []
    def controlled(engine, cfg, stop_event=None):
        value = json.loads(cfg.read_text())
        calls.append(value)
        assert value['task'] == {**ref, 'approval_id': body['command_id']}
        assert value['allow_publication'] is True
        with engine.connect() as c:
            decision = intake.require(c, proposal_decisions, body['command_id'])
            policy = task_budget.permission(c, decision)
            linked = task_execution.snapshot(c, **value['task'])
            assert policy['offer'] == selected
            assert linked['proposal_review'] == data['scope']
        # Explicit controller preparation seam, no real controller/lease activation.
        (Path(value['run_root']) / 'session-finished.json').write_text(json.dumps(dict(
            task=value['task'], status='held', message='Controlled preparation observed; no execution')))
    assert task_sessions.process_one(store, advance=controlled)
    assert len(calls) == 1 and not task_sessions.process_one(store, advance=controlled)
    with store.connect() as c:
        row = c.execute(select(sessions)).mappings().one()
        assert row['id'] == body['command_id']
        assert intake.require(c, proposal_decisions, ref['approval_id'])['payload'].get('run_budget') is None
    after = authority_counts(store)
    assert after == {**before, 'task_sessions': 1}


@pytest.mark.parametrize('change', ['review', 'base', 'certificate', 'expiry', 'runtime'])
def test_pending_budget_revalidates_before_application(store, client, headers, tmp_path, monkeypatch, installed, change):
    ref, runtime, source = prepared(store, client, headers, tmp_path, monkeypatch)
    trusted_preparation.register(store, ref, runtime.root)
    path, data = context(client, ref)
    body = approval(data)
    assert client.post(path, headers=headers, json=body).status_code == 200
    if change == 'review':
        with store.begin() as c:
            c.execute(proposal_decisions.update().where(proposal_decisions.c.id == ref['approval_id']).values(scope_review_required=1))
    elif change == 'base':
        git(source, '-c', 'user.name=Fixture', '-c', 'user.email=f@invalid', 'commit', '--allow-empty', '-qm', 'changed')
    elif change == 'certificate':
        (runtime.root / 'profile-evidence.json').write_text('{}')
    elif change == 'runtime':
        (installed / '.local/task-runtime.json').chmod(0o644)
    else:
        with store.begin() as c:
            c.execute(proposal_decisions.update().where(proposal_decisions.c.id == body['command_id']).values(created_at='2000-01-01T00:00:00+00:00'))
    assert planning_worker.apply_one(store, command_id=body['command_id'])
    assert context(client, ref)[1]['receipt']['status'] == 'stale'
    assert not task_sessions.queue_one(store)
    assert all(v == 0 for v in authority_counts(store).values())


@pytest.mark.parametrize('change', ['scope', 'certificate', 'task'])
def test_changed_offer_before_submit_has_no_budget_or_run(store, client, headers, tmp_path, monkeypatch, change):
    from workbench.db import backlog_items
    ref, runtime, _ = prepared(store, client, headers, tmp_path, monkeypatch)
    trusted_preparation.register(store, ref, runtime.root)
    path, data = context(client, ref)
    body = approval(data)
    if change == 'certificate':
        (runtime.root / 'profile-evidence.json').write_text('{}')
    elif change == 'task':
        with store.begin() as c:
            c.execute(backlog_items.update().where(backlog_items.c.id == ref['task_id']).values(title='Changed task'))
    else:
        with store.begin() as c:
            c.execute(proposal_decisions.update().where(proposal_decisions.c.id == ref['approval_id']).values(scope_review_required=1))
    response = client.post(path, headers=headers, json=body)
    assert response.status_code == 409, response.text
    held = context(client, ref)[1]
    assert not held['available'] and held['offer'] is None and held['receipt'] is None
    assert all(v == 0 for v in authority_counts(store).values())


def test_saved_browser_budget_stop_and_expiry_are_not_rearmed(store, client, headers, tmp_path, monkeypatch, installed):
    ref, runtime, _ = prepared(store, client, headers, tmp_path, monkeypatch)
    trusted_preparation.register(store, ref, runtime.root)
    path, data = context(client, ref)
    body = approval(data)
    assert client.post(path, headers=headers, json=body).status_code == 200
    assert planning_worker.apply_one(store, command_id=body['command_id'])
    assert task_sessions.queue_one(store)
    task_sessions.cancel(store, body['command_id'], str(uuid4()))
    assert not task_sessions.process_one(store, advance=lambda *a, **kw: pytest.fail('stopped controller ran'))
    assert not task_sessions.queue_one(store)
    current = context(client, ref)[1]
    assert 'no longer authorizes' in current['message']
    with store.begin() as c:
        c.execute(proposal_decisions.update().where(proposal_decisions.c.id == body['command_id']).values(created_at='2000-01-01T00:00:00+00:00'))
    assert client.post(path, headers=headers, json={**body, 'command_id':str(uuid4())}).status_code == 409
    with store.connect() as c:
        decision = intake.require(c, proposal_decisions, body['command_id'])
        with pytest.raises(ValueError):
            task_budget.permission(c, decision)


def test_preparation_cannot_self_certify_or_bypass_local_access(store, client, headers, tmp_path, monkeypatch):
    ref, runtime, _ = prepared(store, client, headers, tmp_path, monkeypatch, certify=False)
    with pytest.raises((ValueError, FileNotFoundError)):
        trusted_preparation.register(store, ref, runtime.root)
    runtime.root.chmod(0o777)
    with pytest.raises(ValueError, match='same-user'):
        trusted_preparation.register(store, ref, runtime.root)
    runtime.root.chmod(0o700)
    monkeypatch.setattr(os, 'getuid', lambda: 98765)
    with pytest.raises(ValueError, match='same-user'):
        trusted_preparation.register(store, ref, runtime.root)
    assert client.post('/api/trusted-preparation', headers=headers, json={'ref':ref}).status_code in {404, 405}
    assert all(v == 0 for v in authority_counts(store).values())


def test_http_approval_access_boundaries_and_exact_inputs(store, client, headers, tmp_path, monkeypatch):
    ref, runtime, _ = prepared(store, client, headers, tmp_path, monkeypatch)
    trusted_preparation.register(store, ref, runtime.root)
    path, data = context(client, ref)
    assert 'unavailable' in data['message'].lower()
    body = approval(data)
    assert client.post(path, json=body).status_code == 403
    assert client.post(path, headers={**headers, 'origin':'http://evil.invalid'}, json=body).status_code == 403
    for key in ['commands', 'oracle', 'certified', 'permissions']:
        assert client.post(path, headers=headers, json={**body, key:True}).status_code == 422
    for key, value in [('generated_tokens',24001), ('implementation_attempts',2), ('publication','merge')]:
        altered = copy.deepcopy(body)
        altered['run_budget'][key] = value
        assert client.post(path, headers=headers, json=altered).status_code == 422
    response=client.post(path, headers=headers, json=body)
    assert response.status_code == 200, response.text
    assert planning_worker.apply_one(store, command_id=body['command_id'])
    assert not task_sessions.queue_one(store)  # Execution remains explicitly unavailable.
    changed={**body,'note':'changed'}
    assert client.post(path, headers=headers, json=changed).status_code == 409
    assert all(v == 0 for v in authority_counts(store).values())
