"""Disposable-only cleanup: evidence, identity, concurrency and permissions."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from workbench import db, intake, task_cleanup, task_identity
from workbench.api import create_app
from workbench.schemas import IntakeInput
from workbench.visual_scope import digest
from test_intake import submit, planned, decision, decide


def review(client, identity):
    response = client.get(f'/api/intake/{identity}/cleanup')
    assert response.status_code == 200, response.text
    return response.json()


def command(client, identities, action='archive', **extras):
    return dict(command_id=str(uuid4()), action=action,
                targets=[{k: v for k, v in review(client, identity).items() if k in {'intake_id', 'binding'}}
                         for identity in identities], **extras)


def apply(client, headers, identities, action='archive', **extras):
    response = client.post('/api/task-cleanup', headers=headers, json=command(client, identities, action, **extras))
    assert response.status_code == 200, response.text
    return response.json()


def idea(client, headers):
    return submit(client, headers, requested_work='inspection')


def rows(store, table):
    with store.connect() as c:
        return [dict(r) for r in c.execute(select(table)).mappings()]


def test_archive_restore_persistence_scope_counts_search_and_originals(client, headers, store):
    task, original = idea(client, headers)
    before = {t.name: rows(store, t) for t in (db.intakes, db.intake_scopes, db.execution_policies)}
    result = apply(client, headers, [task['id']])
    assert result['revisions'] == {task['id']: 1}
    assert client.get('/api/workspace').json()['counts']['all'] == 0
    assert client.get('/api/intake').json()['total'] == 0
    archived = client.get('/api/workspace', params={'archive_scope': 'archived', 'search': 'Guide'}).json()
    assert archived['all_total'] == archived['total'] == archived['archived_total'] == 1
    assert archived['items'][0]['archived_at'] and archived['summary']['kinds'] == {'inspection': 1}
    restarted = db.engine_for(store.url.database)
    with TestClient(create_app(restarted)) as second:
        assert second.get('/api/workspace').json()['archived_total'] == 1
        assert second.get('/api/intake/'+task['id']).json()['original']['title'] == original['title']
        assert review(second, task['id'])['can_restore']
    restarted.dispose()
    apply(client, headers, [task['id']], 'restore')
    assert client.get('/api/workspace').json()['counts']['all'] == 1
    assert not review(client, task['id'])['archived_at']
    assert before == {t.name: rows(store, t) for t in (db.intakes, db.intake_scopes, db.execution_policies)}


def test_delete_explicit_confirm_only_unused_archived_idea_and_no_resurrection(client, headers, store):
    task, original = idea(client, headers)
    assert not review(client, task['id'])['can_delete']
    apply(client, headers, [task['id']])
    context = review(client, task['id'])
    assert context['can_delete'] and 'backups are not erased' in context['deletion_explanation']
    body = command(client, [task['id']], 'delete')
    assert client.post('/api/task-cleanup', headers=headers, json=body).status_code == 422
    assert client.post('/api/task-cleanup', headers=headers, json={**body, 'confirm_delete': 'true'}).status_code == 422
    body['confirm_delete'] = True
    result = client.post('/api/task-cleanup', headers=headers, json=body)
    assert result.status_code == 200, result.text
    assert client.post('/api/task-cleanup', headers=headers, json=body).json() == result.json()
    assert client.get('/api/task-cleanup/'+body['command_id']).json() == result.json()
    assert client.get('/api/intake/'+task['id']).status_code == 404
    assert not rows(store, db.intakes) and not rows(store, db.intake_scopes)
    assert rows(store, db.intake_lifecycle)[0]['original_sha256'] == digest(task['original'])
    assert client.post('/api/intake', headers=headers, json=original).status_code == 409
    restarted = db.engine_for(store.url.database)
    with pytest.raises(Exception, match='permanently deleted'):
        intake.submit(restarted, IntakeInput.model_validate(original))
    restarted.dispose()


def test_queued_and_running_work_cannot_be_archived_or_deleted(client, headers, store):
    task, _ = submit(client, headers)
    for state in ('queued', 'running'):
        with db.write_transaction(store) as c:
            c.execute(db.planning_jobs.update().values(status=state))
        assert not review(client, task['id'])['can_archive']
        assert client.post('/api/task-cleanup', headers=headers, json=command(client, [task['id']])).status_code == 409
    assert not rows(store, db.intake_lifecycle)


def test_pending_decision_and_all_work_history_are_protected(client, headers, store):
    task = planned(client, headers, store)
    assert review(client, task['id'])['can_archive']  # An unanswered plan can be put away, never erased.
    saved = decide(client, headers, task, decision(task, 'changes', 'Keep keyboard use.'))
    assert saved.status_code == 200
    context = review(client, task['id'])
    assert not context['can_archive'] and not context['can_delete']
    assert 'decision is waiting' in ' '.join(context['archive_reasons'])


def test_archived_plan_retains_evidence_but_rejects_new_decisions_and_queue(client, headers, store):
    task = planned(client, headers, store)
    tables = (db.intakes, db.planning_jobs, db.proposals, db.proposal_artifacts, db.intake_scopes, db.backlog_items)
    before = {t.name: rows(store, t) for t in tables}
    apply(client, headers, [task['id']])
    assert not review(client, task['id'])['can_delete']
    assert decide(client, headers, task, decision(task)).status_code == 409
    with pytest.raises(Exception, match='archived'):
        with db.write_transaction(store) as c:
            intake.enqueue(c, task['id'], 'planning', 99)
    assert client.get('/api/intake/'+task['id']).json()['proposals'] == task['proposals']
    assert before == {t.name: rows(store, t) for t in tables}


def test_bulk_atomicity_stale_bindings_replay_and_changed_command(client, headers, store):
    first, _ = idea(client, headers)
    second, _ = idea(client, headers)
    body = command(client, [first['id'], second['id']])
    apply(client, headers, [second['id']])
    assert client.post('/api/task-cleanup', headers=headers, json=body).status_code == 409
    assert not review(client, first['id'])['archived_at']
    apply(client, headers, [second['id']], 'restore')
    body = command(client, [first['id'], second['id']])
    result = client.post('/api/task-cleanup', headers=headers, json=body)
    assert result.status_code == 200, result.text
    assert client.post('/api/task-cleanup', headers=headers, json=body).json() == result.json()
    assert client.post('/api/task-cleanup', headers=headers, json={**body, 'action': 'restore'}).status_code == 409
    assert client.get('/api/workspace?archive_scope=archived').json()['total'] == 2


def test_concurrent_archive_and_changed_original_require_fresh_review(client, headers, store):
    task, _ = idea(client, headers)
    body = command(client, [task['id']])
    other = {**body, 'command_id': str(uuid4())}
    def send(payload):
        try:
            task_cleanup.apply(store, task_cleanup.CleanupCommand.model_validate(payload))
            return 200
        except Exception as error:
            return error.status_code
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(send, [body, other])) == [200, 409]
    assert len(rows(store, db.intake_cleanup_receipts)) == 1
    apply(client, headers, [task['id']], 'restore')
    body = command(client, [task['id']])
    with db.write_transaction(store) as c:
        c.execute(db.intakes.update().where(db.intakes.c.id == task['id']).values(priority_override=2))
    assert client.post('/api/task-cleanup', headers=headers, json=body).status_code == 409


def test_concurrent_start_and_archive_cannot_both_succeed(client, headers, store):
    task = planned(client, headers, store)
    body = task_cleanup.CleanupCommand.model_validate(command(client, [task['id']]))
    def run(operation):
        try:
            if operation == 'archive':
                task_cleanup.apply(store, body)
            else:
                with db.write_transaction(store) as c:
                    intake.enqueue(c, task['id'], 'planning', 99)
            return 200
        except Exception as error:
            return error.status_code
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(run, ['archive', 'start'])) == [200, 409]
    with store.connect() as c:
        archived = bool(task_cleanup.lifecycle(c).get(task['id'], {}).get('archived_at'))
        queued = c.scalar(select(db.planning_jobs.c.id).where(db.planning_jobs.c.revision == 99))
    assert archived != bool(queued)


def test_canonical_history_archives_as_one_and_cannot_be_unlinked(client, headers, store):
    first, _ = idea(client, headers)
    second, _ = idea(client, headers)
    manifest = dict(canonical_id=first['id'], title='One original request', members=[dict(
        intake_id=t['id'], payload_sha256=digest(t['original']), relation='retry',
        evidence=[dict(source='Disposable receipt', sha256='a'*64)]) for t in (first, second)])
    task_identity.reconcile(store, manifest)
    assert review(client, second['id'])['intake_id'] == first['id']
    apply(client, headers, [second['id']])
    assert client.get('/api/workspace').json()['total'] == 0
    archived = client.get('/api/workspace?archive_scope=archived').json()
    assert archived['total'] == 1 and archived['items'][0]['history_count'] == 2
    for task in (first, second):
        assert client.get('/api/intake/'+task['id']).json()['archived_at']
    assert not review(client, first['id'])['can_delete']
    with pytest.raises(Exception, match='archived'):
        task_identity.reverse(store, manifest)
    apply(client, headers, [first['id']], 'restore')
    assert len(task_identity.reverse(store, manifest)) == 2


def test_cross_request_parent_and_dependencies_never_orphan(client, headers, store):
    first, _ = idea(client, headers)
    second, _ = idea(client, headers)
    def item(identity, request, parent=None):
        return dict(id=identity, intake_id=request, proposal_id=None, parent=parent, kind='task',
                    title='Dependent work', purpose='Disposable relationship', acceptance=[], model='fixture', created_at='2026-11-11')
    with db.write_transaction(store) as c:
        c.execute(db.backlog_items.insert().values(**item('parent', first['id'])))
        c.execute(db.backlog_items.insert().values(**item('child', second['id'], 'parent')))
        c.execute(db.backlog_dependencies.insert().values(item_id='child', requires_id='parent'))
    for task in (first, second):
        context = review(client, task['id'])
        assert not context['can_archive'] and not context['can_delete']
        assert 'depends' in ' '.join(context['archive_reasons'])


def test_same_store_guard_and_csrf_origin_permissions(client, headers, store):
    task, _ = idea(client, headers)
    body = command(client, [task['id']])
    assert client.post('/api/task-cleanup', json=body).status_code == 403
    assert client.post('/api/task-cleanup', headers={**headers, 'origin': 'https://outside.invalid'}, json=body).status_code == 403
    assert client.post('/api/task-cleanup', headers=headers, json={**body, 'targets': body['targets']*51}).status_code == 422
    assert client.post('/api/task-cleanup', headers=headers, json={**body, 'targets': body['targets']*2}).status_code == 422
    assert not rows(store, db.intake_lifecycle)


def test_additive_migration_preserves_original_rows_and_refuses_lossy_downgrade(store):
    from alembic import command as migrate
    from alembic.config import Config
    config = Config('alembic.ini')
    with store.connect() as c:
        config.attributes['connection'] = c
        migrate.downgrade(config, '0013')
        c.commit()
        tables = [t for t in db.metadata.sorted_tables if t.name not in {'intake_lifecycle', 'intake_cleanup_receipts'}]
        before = {t.name: [dict(r) for r in c.execute(select(t)).mappings()] for t in tables}
        migrate.upgrade(config, 'head')
        c.commit()
        assert c.exec_driver_sql('SELECT version_num FROM alembic_version').scalar_one() == '0014'
        assert before == {t.name: [dict(r) for r in c.execute(select(t)).mappings()] for t in tables}
        assert not c.exec_driver_sql('PRAGMA foreign_key_check').fetchall()
        c.execute(db.intake_lifecycle.insert().values(intake_id='retained-terminal-identity', revision=2,
            archived_at=None, deleted_at=None, original_sha256='a'*64))
        c.commit()
        with pytest.raises(RuntimeError, match='history must be retained'):
            migrate.downgrade(config, '0013')
        c.rollback()
        assert c.exec_driver_sql('SELECT count(*) FROM intake_lifecycle').scalar_one() == 1


@pytest.mark.parametrize('outcome,finished', [('working', None), ('interrupted_unknown', '2026-11-11')])
def test_unfinished_or_unknown_model_operations_are_not_archived(client, headers, store, outcome, finished):
    task = planned(client, headers, store)
    with db.write_transaction(store) as c:
        job = c.execute(select(db.planning_jobs.c.id)).scalar_one()
        c.execute(db.model_attempts.insert().values(id=str(uuid4()), job_id=job, execution_id=None,
            task_id=task['id'], source='workbench', backend='local', purpose='planning',
            routing_reason='Disposable lifecycle check', started_at='2026-11-11', finished_at=finished,
            outcome=outcome, usage=None))
    context = review(client, task['id'])
    assert not context['can_archive'] and 'recovery' in ' '.join(context['archive_reasons'])
