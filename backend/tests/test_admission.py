"""Disposable approval-bound rehearsal. No provider, implementation or verification."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi import HTTPException
import pytest
from sqlalchemy import select

from workbench import admission, planning_worker
from workbench.db import (metadata, engine_for, backlog_items, backlog_dependencies, proposals,
    proposal_decisions, planning_jobs, proposal_artifacts, intake_scopes, intakes, write_transaction)
from workbench.planning_provider import DemoProvider

ROOT = Path(__file__).resolve().parents[2]


def ready(client, headers, store):
    fixture = json.loads(admission.FIXTURE.read_text())
    original = dict(fixture['request'], request_id=str(uuid4()))
    result = client.post('/api/intake', headers=headers, json=original)
    assert result.status_code == 200, result.text
    identity = original['request_id']
    assert planning_worker.process_one(store, DemoProvider())
    data = client.get('/api/intake/' + identity).json()
    p = data['proposals'][0]
    assert p['required_artifacts'] == [] and not p['approval_blockers']
    body = dict(command_id=str(uuid4()), proposal_id=p['id'], binding=p['binding'],
        scope_revision=p['scope_revision'], action='approve', note='Explicit synthetic nonvisual implementation scope approval; execution stays denied.')
    assert client.post('/api/intake/'+identity+'/decisions', headers=headers, json=body).status_code == 200
    assert planning_worker.apply_one(store)
    assert planning_worker.process_one(store, DemoProvider())
    data = client.get('/api/intake/'+identity).json()
    task = next(t for t in data['tasks'] if t['kind'] == 'task')
    path = f'/api/intake/{identity}/tasks/{task["id"]}/admission'
    context = client.get(path).json()
    assert context['current_authority']['eligible'], context
    request = dict(command_id=str(uuid4()), mode='fixture',
        approval_id=body['command_id'], binding=context['current_authority']['binding'])
    return identity, task, path, request


def dump_existing(store):
    with store.connect() as c:
        return {table.name:[dict(r) for r in c.execute(select(table)).mappings()]
                for table in metadata.sorted_tables if table is not admission.records}


def worker(store, *args):
    return subprocess.run([sys.executable, '-m', 'workbench.worker', '--once', *args], cwd=ROOT,
        env={**os.environ, 'PYTHONPATH':str(ROOT/'backend'), 'WORKBENCH_DB':str(store.url.database),
             'WORKBENCH_PLANNER':'unavailable'}, capture_output=True, timeout=10)


def test_positive_snapshot_invariants_and_exact_retry(client, headers, store):
    identity, task, path, body = ready(client, headers, store)
    existing = dump_existing(store)
    response = client.post(path, headers=headers, json=body)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved['status'] == 'pending' and saved['finished_at'] is None
    assert not saved['execution_allowed'] and not saved['publication_allowed']
    assert 'snapshot' not in saved and 'payload' not in saved
    assert client.post(path, headers=headers, json=body).json() == saved
    with store.connect() as c:
        snapshot = c.execute(select(admission.records.c.snapshot)).scalar_one()
    assert snapshot['task']['id'] == task['id'] and not snapshot['dependencies']
    assert snapshot['approval']['id'] == body['approval_id']
    assert snapshot['decomposition']['payload']['approval_id'] == body['approval_id']
    assert snapshot['proposal_review']['human_scope']['stage'] == 'implementation'
    assert admission.digest(snapshot) == body['binding']
    assert worker(store).returncode == 0
    result = client.get(path).json()
    receipt = result['receipts'][0]
    assert receipt['status'] == 'admitted' and receipt['finished_at'] >= receipt['created_at']
    assert result['current_authority']['eligible']
    assert client.post(path, headers=headers, json=body).json() == receipt
    assert not admission.process_one(store)
    assert dump_existing(store) == existing  # Includes Models attempts/usage/policies and verification.
    with store.connect() as c:
        assert c.execute(select(admission.records.c.snapshot)).scalar_one() == snapshot


def test_admission_has_only_receipt_writes_and_no_execution_hooks(client, headers, store, monkeypatch):
    from sqlalchemy import event
    from workbench import planning_provider, verification, model_policy
    _, _, path, body = ready(client, headers, store)
    before = dump_existing(store)
    def forbidden(*_, **__):
        pytest.fail('Admission invoked a provider, verifier, routing or subprocess hook')
    monkeypatch.setattr(planning_provider, 'provider', forbidden)
    monkeypatch.setattr(planning_provider.DemoProvider, 'generate', forbidden)
    monkeypatch.setattr(planning_provider.LocalProvider, 'generate', forbidden)
    monkeypatch.setattr(verification.FixtureVerifier, 'verify', forbidden)
    monkeypatch.setattr(model_policy, 'evaluate', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    writes = []
    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE ')):
            writes.append(statement)
    event.listen(store, 'before_cursor_execute', observe)
    try:
        assert client.post(path, headers=headers, json=body).status_code == 200
        admission.process_one(store)
        assert client.get(path).json()['receipts'][0]['status'] == 'admitted'
    finally:
        event.remove(store, 'before_cursor_execute', observe)
    assert len(writes) == 2 and all('admission_requests' in sql for sql in writes)
    assert dump_existing(store) == before


@pytest.mark.parametrize('change', ['id', 'binding', 'approval', 'mode', 'route'])
def test_conflicting_retries(client, headers, store, change):
    identity, task, path, body = ready(client, headers, store)
    assert client.post(path, headers=headers, json=body).status_code == 200
    changed = dict(body)
    if change == 'id':
        changed['command_id'] = str(uuid4())
    elif change == 'binding':
        changed['binding'] = '0'*64
    elif change == 'approval':
        changed['approval_id'] = str(uuid4())
    elif change == 'mode':
        changed['mode'] = 'live'
    else:
        path = path.replace(identity, str(uuid4()), 1)
    response = client.post(path, headers=headers, json=changed)
    assert response.status_code == (422 if change == 'mode' else 409)
    with store.connect() as c:
        assert len(c.execute(select(admission.records)).all()) == 1


@pytest.mark.parametrize('case', ['missing', 'pending', 'changes', 'decline', 'revoked', 'legacy',
    'requirements', 'snapshot', 'research', 'scope', 'proposal', 'artifact', 'task', 'dependency',
    'provenance', 'decomposition_status', 'wrong_approval', 'arbitrary', 'extra_task'])
def test_invalid_authority_at_submission_and_worker(client, headers, store, case):
    identity, task, path, body = ready(client, headers, store)
    assert client.post(path, headers=headers, json=body).status_code == 200
    with write_transaction(store) as c:
        approval = dict(c.execute(select(proposal_decisions)).mappings().one())
        proposal_id = approval['proposal_id']
        if case == 'missing':
            # Missing authority on a new request; FK retains the saved audit's authority row.
            missing = {**body, 'command_id':str(uuid4()), 'approval_id':str(uuid4())}
        elif case in {'pending', 'revoked', 'legacy', 'requirements', 'snapshot', 'changes', 'decline', 'wrong_approval'}:
            changes = {'status':'pending'} if case == 'pending' else {'scope_review_required':1}
            if case == 'requirements':
                changes = {'required_artifacts':None}
            if case == 'snapshot':
                changes = {'snapshot':{}}
            if case in {'changes', 'decline'}:
                changes = {'payload':{**approval['payload'], 'action':case}}
            if case == 'wrong_approval':
                changes = {'payload':{**approval['payload'], 'proposal_id':str(uuid4())}}
            c.execute(proposal_decisions.update().values(**changes))
        elif case == 'research':
            # Keep ordinary valid research semantics, including its own reviewed snapshot.
            scope = dict(c.execute(select(intake_scopes)).mappings().one())
            c.execute(intake_scopes.update().values(stage='research'))
            p = dict(c.execute(select(proposals)).mappings().one())
            content = {**p['content'], 'delivery':'research', 'exclusions':[admission.intake.RESEARCH_EXCLUSION]}
            c.execute(proposals.update().values(content=content))
            p['content'] = content
            review = admission.intake.proposal_view(c, p)
            c.execute(proposal_decisions.update().values(binding=review['binding'], snapshot=review,
                payload={**approval['payload'], 'binding':review['binding']}))
            assert scope['stage'] == 'implementation'
        elif case == 'scope':
            c.execute(intake_scopes.update().values(visual_requirement='visual'))
        elif case == 'proposal':
            c.execute(intakes.update().values(revision=999))
        elif case == 'artifact':
            c.execute(proposal_artifacts.insert().values(id=str(uuid4()), proposal_id=proposal_id,
                role='evidence', label='Additional context', sha256='a'*64, media_type='text/plain',
                data=b'synthetic', created_at=admission.now()))
        elif case == 'task':
            c.execute(backlog_items.update().where(backlog_items.c.id==task['id']).values(title='Changed task'))
        elif case == 'dependency':
            c.execute(backlog_dependencies.insert().values(item_id=task['id'], requires_id='intake-'+identity))
        elif case == 'provenance':
            job = c.execute(select(planning_jobs).where(planning_jobs.c.kind=='decomposition')).mappings().one()
            c.execute(planning_jobs.update().where(planning_jobs.c.id==job['id']).values(payload={**job['payload'], 'approval_id':str(uuid4())}))
        elif case == 'decomposition_status':
            c.execute(planning_jobs.update().where(planning_jobs.c.kind=='decomposition').values(status='failed'))
        elif case == 'arbitrary':
            original = c.scalar(select(intakes.c.payload))
            c.execute(intakes.update().values(payload={**original, 'title':'Another demo feature'}))
        elif case == 'extra_task':
            item = dict(c.execute(select(backlog_items).where(backlog_items.c.id==task['id'])).mappings().one())
            c.execute(backlog_items.insert().values(**{**item, 'id':task['id']+'-extra'}))
    if case == 'missing':
        assert client.post(path, headers=headers, json=missing).status_code == 404
        return
    before = dump_existing(store)
    assert client.post(path, headers=headers, json={**body,'command_id':str(uuid4())}).status_code == 409
    assert admission.process_one(store)
    result = client.get(path).json()
    assert not result['current_authority']['eligible']
    assert result['receipts'][0]['status'] in {'stale', 'blocked'}
    assert not result['receipts'][0]['execution_allowed'] and not result['receipts'][0]['publication_allowed']
    assert client.post(path, headers=headers, json=body).json() == result['receipts'][0]
    assert dump_existing(store) == before


def test_historical_admitted_is_not_current_authority(client, headers, store):
    identity, task, path, body = ready(client, headers, store)
    client.post(path, headers=headers, json=body)
    admission.process_one(store)
    old = client.get(path).json()['receipts'][0]
    with write_transaction(store) as c:
        approval = c.execute(select(proposal_decisions)).mappings().one()
        admission.intake.revise_scope_in(c, identity,
            admission.intake.require(c, proposals, approval['proposal_id']), 'nonvisual', 'implementation',
            'New human review revision, even with unchanged classification.', str(uuid4()), {})
    context = client.get(path).json()
    assert context['receipts'] == [old] and not context['current_authority']['eligible']
    assert context['current_authority']['binding'] is None
    assert client.post(path, headers=headers, json=body).json() == old


def test_pending_guard_with_different_binding(client, headers, store):
    _, task, path, body = ready(client, headers, store)
    client.post(path, headers=headers, json=body)
    with write_transaction(store) as c:
        c.execute(backlog_items.update().where(backlog_items.c.id==task['id']).values(created_at='2026-11-01T00:00:00+00:00'))
    current = client.get(path).json()['current_authority']
    assert current['eligible'] and current['binding'] != body['binding']
    fresh = {**body, 'command_id':str(uuid4()), 'binding':current['binding']}
    assert client.post(path, headers=headers, json=fresh).status_code == 409
    admission.process_one(store)
    assert client.get(path).json()['receipts'][0]['status'] == 'stale'
    assert client.post(path, headers=headers, json=fresh).status_code == 200


def test_new_approval_and_decomposition_keep_old_task_and_receipt(client, headers, store):
    identity, old_task, path, body = ready(client, headers, store)
    client.post(path, headers=headers, json=body)
    admission.process_one(store)
    old = client.get(path).json()['receipts'][0]
    data = client.get('/api/intake/'+identity).json()
    p = data['proposals'][0]
    change = dict(command_id=str(uuid4()), proposal_id=p['id'], binding=p['binding'],
        visual_requirement='nonvisual', stage='implementation', note='Review a new scope revision explicitly.')
    revised = client.post('/api/intake/'+identity+'/scope', headers=headers, json=change).json()
    p = revised['proposals'][0]
    approval = dict(command_id=str(uuid4()), proposal_id=p['id'], binding=p['binding'],
        scope_revision=p['scope_revision'], action='approve', note='Explicit approval of the revised synthetic scope.')
    assert client.post('/api/intake/'+identity+'/decisions', headers=headers, json=approval).status_code == 200
    planning_worker.apply_one(store)
    planning_worker.process_one(store, DemoProvider())
    data = client.get('/api/intake/'+identity).json()
    task = next(t for t in data['tasks'] if t['kind']=='task' and t['proposal_id']==p['id'])
    new_path = f'/api/intake/{identity}/tasks/{task["id"]}/admission'
    current = client.get(new_path).json()['current_authority']
    assert current['eligible'] and current['binding'] != body['binding']
    new_body = dict(command_id=str(uuid4()), mode='fixture', approval_id=approval['command_id'], binding=current['binding'])
    assert client.post(new_path, headers=headers, json=new_body).status_code == 200
    admission.process_one(store)
    assert client.get(new_path).json()['receipts'][0]['status'] == 'admitted'
    assert client.get(path).json()['receipts'] == [old]
    assert not client.get(path).json()['current_authority']['eligible']
    assert any(t['id']==old_task['id'] for t in data['tasks'])


def test_changed_controlled_fixture_version_is_stale(client, headers, store, monkeypatch, tmp_path):
    _, _, path, body = ready(client, headers, store)
    client.post(path, headers=headers, json=body)
    definition = json.loads(admission.FIXTURE.read_text())
    definition['version'] += 1
    changed = tmp_path/'revised-fixture.json'
    changed.write_text(json.dumps(definition))
    monkeypatch.setattr(admission,'FIXTURE',changed)
    admission.process_one(store)
    assert client.get(path).json()['receipts'][0]['status'] == 'stale'


def test_security_and_container_boundary(client, headers, store):
    identity, _, path, body = ready(client, headers, store)
    assert client.post(path, json=body).status_code == 403
    assert client.post(path, headers={**headers,'origin':'https://other.invalid'}, json=body).status_code == 403
    assert client.post(path, headers=headers, json={**body,'candidate_id':'fake'}).status_code == 422
    root = f'/api/intake/{identity}/tasks/intake-{identity}/admission'
    assert client.post(root, headers=headers, json=body).status_code == 409
    assert not client.get(root).json()['current_authority']['eligible']


def test_concurrent_requests_and_workers(client, headers, store):
    identity, task, path, body = ready(client, headers, store)
    parsed = admission.FixtureAdmissionRequest(**body)
    def save(_):
        try:
            return admission.submit(store, identity, task['id'], parsed)
        except HTTPException as e:
            return e.status_code
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(save, range(2)))
    assert results[0] == results[1] and results[0]['status'] == 'pending'
    with ThreadPoolExecutor(2) as pool:
        assert sum(pool.map(lambda _:admission.process_one(store), range(2))) == 1
    assert len(client.get(path).json()['receipts']) == 1


def test_busy_and_real_worker_crash_recovery(client, headers, store):
    _, _, path, body = ready(client, headers, store)
    with write_transaction(store):
        response = client.post(path, headers=headers, json=body)
        assert response.status_code == 503 and response.headers['retry-after'] == '1'
    client.post(path, headers=headers, json=body)
    with write_transaction(store):
        result = worker(store)
        assert result.returncode == 0 and b'busy' in result.stderr
    assert client.get(path).json()['receipts'][0]['status'] == 'pending'
    assert worker(store, '--test-crash', 'before-commit').returncode == 91
    assert client.get(path).json()['receipts'][0]['status'] == 'pending'
    assert worker(store, '--test-crash', 'after-commit').returncode == 92
    old = client.get(path).json()['receipts'][0]
    assert old['status'] == 'admitted'
    assert worker(store).returncode == 0
    assert client.get(path).json()['receipts'] == [old]


@pytest.mark.parametrize('outcome', ['admitted', 'stale', 'blocked', 'failed'])
def test_finish_capture_after_terminal_handling(client, headers, store, monkeypatch, outcome):
    _, _, path, body = ready(client, headers, store)
    client.post(path, headers=headers, json=body)
    observed = []
    real = admission.authority
    def validate(*args):
        observed.append('validate')
        if outcome == 'blocked':
            raise admission.GateError('blocked', 'Synthetic authority withdrawal')
        if outcome == 'failed':
            raise OSError('Synthetic validation failure')
        if outcome == 'stale':
            snapshot, _ = real(*args)
            return snapshot, '0'*64
        return real(*args)
    def finish():
        observed.append('finish')
        return '2026-11-04T20:00:00+00:00'
    monkeypatch.setattr(admission,'authority',validate)
    monkeypatch.setattr(admission,'now',finish)
    admission.process_one(store)
    assert observed == ['validate','finish']
    result = client.post(path, headers=headers, json=body).json()
    assert result['status'] == outcome
    assert result['finished_at'] == '2026-11-04T20:00:00+00:00'


def test_missing_fixture_retains_audit_and_denies_current_authority(client, headers, store, monkeypatch, tmp_path):
    _, _, path, body = ready(client, headers, store)
    client.post(path, headers=headers, json=body)
    monkeypatch.setattr(admission, 'FIXTURE', tmp_path/'missing.json')
    admission.process_one(store)
    context = client.get(path).json()
    assert context['receipts'][0]['status'] == 'failed'
    assert not context['current_authority']['eligible'] and context['supported']


def test_validation_failure_receipt(client, headers, store, monkeypatch):
    _, _, path, body = ready(client, headers, store)
    client.post(path, headers=headers, json=body)
    original = admission.authority
    def broken(*_):
        raise OSError('private diagnostic')
    monkeypatch.setattr(admission,'authority',broken)
    admission.process_one(store)
    receipt = client.get(path).json()['receipts'][0]
    assert receipt['status'] == 'failed' and 'private' not in receipt['message']
    assert not client.get(path).json()['current_authority']['eligible']
    monkeypatch.setattr(admission,'authority',original)


def test_result_rollback_revalidates_changed_authority(client, headers, store):
    from sqlalchemy import event
    _, _, path, body = ready(client, headers, store)
    client.post(path, headers=headers, json=body)
    def fail(_):
        raise RuntimeError('Lost result before commit')
    event.listen(store, 'commit', fail)
    try:
        with pytest.raises(RuntimeError):
            admission.process_one(store)
    finally:
        event.remove(store, 'commit', fail)
    assert client.get(path).json()['receipts'][0]['status'] == 'pending'
    with write_transaction(store) as c:
        c.execute(proposal_decisions.update().values(scope_review_required=1))
    admission.process_one(store)
    assert client.get(path).json()['receipts'][0]['status'] == 'blocked'


def test_submission_rollback_has_no_acknowledged_request(client, headers, store):
    from sqlalchemy import event
    _, _, path, body = ready(client, headers, store)
    def fail(_):
        raise RuntimeError('Lost save before commit')
    event.listen(store, 'commit', fail)
    try:
        with pytest.raises(RuntimeError):
            client.post(path, headers=headers, json=body)
    finally:
        event.remove(store, 'commit', fail)
    assert client.get(path).json()['receipts'] == []
    assert client.post(path, headers=headers, json=body).json()['status'] == 'pending'


def test_additive_migration_preserves_0006_and_backup_restore(tmp_path):
    engine = engine_for(tmp_path/'old.sqlite3')
    config = Config(str(ROOT/'alembic.ini'))
    with engine.connect() as c:
        config.attributes['connection'] = c
        command.upgrade(config,'0006')
        c.commit()
        from workbench.seed import seed
        seed(engine)
        from workbench.api import create_app
        from fastapi.testclient import TestClient
        from workbench import worker as reviews, verification
        with TestClient(create_app(engine)) as client:
            headers = {'origin':'http://testserver', 'x-csrf-token':client.get('/api/session').json()['csrf_token']}
            task = client.get('/api/tasks/cozy-step-review').json()
            assert client.post('/api/tasks/cozy-step-review/decisions', headers=headers, json=dict(
                command_id=str(uuid4()), version=task['version'],
                choices={claim['id']:'confirm' for claim in task['evidence']['claims']})).status_code == 200
            reviews.process_one(engine)
            task = client.get('/api/tasks/cozy-step-review').json()
            assert client.post('/api/tasks/cozy-step-review/verification', headers=headers, json=dict(
                command_id=str(uuid4()), version=task['version'], mode='fixture')).status_code == 200
            verification.process_one(engine)
        before = {table:c.exec_driver_sql('SELECT * FROM "'+table+'"').fetchall()
                  for (table,) in c.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table' AND name!='alembic_version'")}
        command.upgrade(config,'head')
        c.commit()
        command.upgrade(config,'head')
        c.commit()
        for table, rows in before.items():
            assert c.exec_driver_sql('SELECT * FROM "'+table+'"').fetchall() == rows
        assert c.exec_driver_sql('SELECT COUNT(*) FROM admission_requests').scalar_one() == 0
        assert c.exec_driver_sql('PRAGMA foreign_key_check').all() == []
    engine.dispose()
    with sqlite3.connect(tmp_path/'old.sqlite3') as source, sqlite3.connect(tmp_path/'backup.sqlite3') as backup:
        source.backup(backup)
        assert backup.execute('PRAGMA integrity_check').fetchone() == ('ok',)
        assert backup.execute('SELECT version_num FROM alembic_version').fetchone() == ('0014',)


def test_terminal_receipt_survives_backup_without_reapplication(client, headers, store, tmp_path):
    identity, task, path, body = ready(client, headers, store)
    client.post(path, headers=headers, json=body)
    admission.process_one(store)
    receipt = client.get(path).json()['receipts'][0]
    backup_path = tmp_path/'restored.sqlite3'
    with sqlite3.connect(store.url.database) as source, sqlite3.connect(backup_path) as backup:
        source.backup(backup)
    restored = engine_for(backup_path)
    assert admission.context(restored, identity, task['id'])['receipts'][0] == {
        k:v for k,v in receipt.items() if k not in {'source','execution_allowed','publication_allowed'}}
    assert not admission.process_one(restored)
    restored.dispose()
