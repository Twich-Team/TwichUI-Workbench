"""Ordinary local authority and repeat use with disposable stores and no model transport."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from workbench import everyday, intake, real_execution, task_session, task_sessions, worker
from workbench.db import model_attempts, planning_jobs, proposal_decisions, real_execution_grants
from test_grounded_workflow import (
    CLIENT_TEST, TEST, ControlledPlanner, configure, approve_and_decompose, save_local_policy,
)


def write_policy(path, value):
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    return str(path)


@pytest.fixture
def installed(store, tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'scripts'))
    configure(store, tmp_path, monkeypatch)
    save_local_policy(store, model=everyday.MODEL)
    local = tmp_path / '.local'
    local.mkdir()
    scratch = tmp_path / 'scratch'
    scratch.mkdir(mode=0o700)
    db = Path(store.url.database)
    binding = dict(database=str(db), database_identity=[db.stat().st_dev, db.stat().st_ino])
    proof = local / 'proof.json'
    proof.write_text('{}')
    from workbench.real_workspace import sha

    write_policy(local / 'task-runtime.json', dict(**binding, mode='explicit-one-off-only',
        templates_root=str(local / 'supported-task-templates'), repository='Twich-Team/TwichUI',
        base_branch='main', model=everyday.MODEL, automatic_fallback=False,
        total_limit_proof=str(proof), total_limit_proof_sha256=sha(proof.read_bytes())))
    monkeypatch.setenv('WORKBENCH_PLANNER', 'everyday')
    monkeypatch.setenv('TMPDIR', str(scratch))
    monkeypatch.setattr('tempfile.tempdir', str(scratch))
    monkeypatch.setenv('WORKBENCH_EVERYDAY_CONFIG', write_policy(local / 'policy.json',
        dict(**binding, scratch=str(scratch), evidence_roots=[str(tmp_path)])))
    for module in (everyday, task_session, task_sessions):
        monkeypatch.setattr(module, 'ROOT', tmp_path)
    with everyday.worker_owner(store):
        yield scratch


def body(path=TEST, permission=True):
    client = path == CLIENT_TEST
    return dict(request_id=str(uuid4()), kind='feature',
        title='Cover stored client state' if client else 'Cover name normalization',
        description=f'In {path}, test trimming spaces.' + (
            '\nExample 1: {"args":["  bob  "],"expected":"bob","incorrect":"  bob  "}' if client else
            '\nExample 1: {"args":["  ALICE "],"expected":"alice","incorrect":"ALICE"}'),
        visual_requirement='nonvisual',
        local_planning='qwen-once' if permission else None,
        regression_tests=dict(acceptance=['Trimming produces the specified result.'], test_paths=[path]))


def test_ordinary_requests_have_one_planning_claim_and_local_run_each(
    store, client, headers, installed, monkeypatch,
):
    # Fixture helpers are deliberately not production dispatch or transport.
    import local_browser_fixture
    monkeypatch.setattr(local_browser_fixture, "install_offline_guard", lambda: None)
    planner = ControlledPlanner()
    assert client.get('/api/local-work').json()['ready']
    for index, path in enumerate([TEST, CLIENT_TEST], 1):
        request = body(path)
        response = client.post('/api/intake', headers=headers, json=request)
        assert response.status_code == 200
        item = response.json()
        assert client.post('/api/intake', headers=headers, json=request).json()['id'] == item['id']
        assert worker.tick(store, planning_adapter=planner)
        assert len(planner.calls) == index
        approve_and_decompose(store, client, headers, item)
        assert worker.tick(store, planning_adapter=planner)  # Application prepares from the saved plan.
        item = client.get('/api/intake/' + item['id']).json()
        task, = (task for task in item['tasks'] if task['kind'] == 'task')
        endpoint = f"/api/intake/{item['id']}/tasks/{task['id']}/run-approval"
        offer = client.get(endpoint).json()
        assert offer['available'] and offer['local_only']
        command = dict(command_id=str(uuid4()), binding=offer['binding'],
                       scope_approval_id=offer['scope_approval_id'], run_budget=offer['offer']['budget'])
        escalated = {**command, 'run_budget': {**command['run_budget'], 'publication': 'draft-pr'}}
        assert client.post(endpoint, headers=headers, json=escalated).status_code == 409
        saved = client.post(endpoint, headers=headers, json=command)
        assert saved.status_code == 200 and saved.json()['status'] == 'pending'
        assert client.post(endpoint, headers=headers, json=command).json() == saved.json()
        assert client.post(endpoint, headers=headers, json={**command, 'command_id': str(uuid4())}).status_code == 409
        assert worker.tick(store)  # Apply.
        assert worker.tick(store)  # Queue the session.
        local_browser_fixture.advance_local(store, Path(store.url.database), 'pass', planner, example=None)
        result = client.get('/api/real-executions').json()
        assert result['total'] == index
        history = client.get('/api/intake/' + item['id']).json()['provider_activity']
        assert [(entry['purpose'], entry['backend']) for entry in history] == [('implementation', 'local')]
        assert history[0]['requested_model'] == everyday.MODEL
        assert 'fallback disabled' in history[0]['routing_reason']
        assert not list(installed.glob('workbench-verify-*'))
    with store.connect() as c:
        assert len(c.execute(select(model_attempts).where(model_attempts.c.purpose == 'planning')).all()) == 2
        assert all(c.execute(select(real_execution_grants.c.revoked_at)).scalars())
    assert intake.view(store, item['id'])['status'] == 'tasks_ready'


def test_missing_expired_and_capacity_held_planning_never_call_adapter(
    store, client, headers, installed, monkeypatch,
):
    planner = ControlledPlanner()
    for permission in (False, True):
        item = client.post('/api/intake', headers=headers, json=body(permission=permission)).json()
        if permission:
            monkeypatch.setattr(everyday, 'measure', lambda policy: (1 << 20, 8 << 30, 0))
            assert not client.get('/api/local-work').json()['ready']
            assert not worker.tick(store, planning_adapter=planner)
            state = client.get('/api/intake/' + item['id']).json()
            assert state['jobs'][0]['message'].startswith('Waiting for storage')
            old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            from workbench.db import intakes
            with store.begin() as c:
                c.execute(intakes.update().where(intakes.c.id == item['id']).values(created_at=old))
                job = intake.require(c, planning_jobs, item['jobs'][0]['id'])
                # The original stored JSON omits unrelated optional nulls.
                original = intake.require(c, intakes, item['id'])['payload']
                payload = {**job['payload'], 'local_planning_receipt': everyday.planning_receipt(original, old)}
                c.execute(planning_jobs.update().where(planning_jobs.c.id == job['id']).values(payload=payload))
        assert worker.tick(store, planning_adapter=planner)
        saved = client.get('/api/intake/' + item['id']).json()
        assert saved['status'] == 'failed'
        assert ('expired' if permission else 'approval is required') in saved['jobs'][0]['message']
    assert not planner.calls
    with store.connect() as c:
        assert not c.execute(select(model_attempts)).first()
        assert not c.execute(select(proposal_decisions)).first()


def test_worker_ownership_prevents_overlap(store, client, installed):
    with pytest.raises(BlockingIOError), everyday.worker_owner(store):
        pass
    marker = everyday.ROOT / '.local/everyday-worker.json'
    receipt = json.loads(marker.read_text())
    receipt['owner']['start'] += '0'
    write_policy(marker, receipt)
    assert not client.get('/api/local-work').json()['ready']
    assert real_execution.alive(real_execution.owner_identity())


def test_codex_preference_holds_qwen_planning_even_with_fallback_enabled(
    store, client, headers, installed,
):
    planner = ControlledPlanner()
    item = client.post('/api/intake', headers=headers, json=body()).json()
    save_local_policy(store, model=everyday.MODEL, preferred='codex')
    assert worker.tick(store, planning_adapter=planner)
    held = client.get('/api/intake/' + item['id']).json()
    assert held['status'] == 'failed'
    assert 'Codex and fallback are unavailable' in held['jobs'][0]['message']
    assert not planner.calls
    with store.connect() as c:
        assert not c.execute(select(model_attempts)).first()


def test_saved_qwen_selection_reaches_adapter_and_changed_preference_fences_run(
    store, client, headers, installed, monkeypatch,
):
    from workbench.planning_provider import LocalProvider

    planner = ControlledPlanner()

    def offline_request(self, schema, packet, key_path, expected_model):
        assert expected_model == everyday.MODEL
        response, _, _ = planner.generate(json.loads(packet), schema)
        return response, expected_model, 'local'

    monkeypatch.setenv('WORKBENCH_LOCAL_KEY', '/unused-offline')
    monkeypatch.setattr(LocalProvider, '_request', offline_request)
    item = client.post('/api/intake', headers=headers, json=body()).json()
    assert worker.tick(store)
    current = client.get('/api/intake/' + item['id']).json()
    assert current['provider_activity'][0]['actual_model'] == everyday.MODEL
    assert 'Saved preferred local' in current['provider_activity'][0]['routing_reason']
    monkeypatch.delenv('WORKBENCH_LOCAL_KEY')
    approve_and_decompose(store, client, headers, item)
    assert worker.tick(store)
    current = client.get('/api/intake/' + item['id']).json()
    task, = (task for task in current['tasks'] if task['kind'] == 'task')
    endpoint = f"/api/intake/{item['id']}/tasks/{task['id']}/run-approval"
    offer = client.get(endpoint).json()
    command = dict(command_id=str(uuid4()), binding=offer['binding'],
                   scope_approval_id=offer['scope_approval_id'], run_budget=offer['offer']['budget'])
    saved = client.post(endpoint, headers=headers, json=command)
    assert saved.status_code == 200 and saved.json()['status'] == 'pending'

    save_local_policy(store, model=everyday.MODEL, preferred='codex')
    held = client.get(endpoint).json()
    assert not held['available'] and 'Codex and fallback are unavailable' in held['message']
    assert client.post(endpoint, headers=headers, json=command).json() == saved.json()
    denied = client.post(endpoint, headers=headers, json={**command, 'command_id': str(uuid4())})
    assert denied.status_code == 409
    assert worker.tick(store)  # Pending decision is fenced before it applies.
    assert not worker.tick(store)
    assert client.get(endpoint).json()['receipt']['status'] == 'stale'
    with store.connect() as c:
        assert len(c.execute(select(model_attempts)).all()) == 1
        assert not c.execute(select(real_execution_grants)).first()
