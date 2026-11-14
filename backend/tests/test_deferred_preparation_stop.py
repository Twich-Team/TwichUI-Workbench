"""A trusted deferred stop removes admission without rewriting provider history."""
from uuid import uuid4

import pytest
from sqlalchemy import select
from workbench.db import intakes, planning_jobs, model_attempts, write_transaction
from workbench.service import now
from workbench.trusted_preparation import stop_deferred
from workbench.visual_scope import digest
from workbench.work_controls import task_read, task_save, WorkControlInput


@pytest.fixture
def deferred(store):
    intake, job, attempt = (str(uuid4()) for _ in range(3))
    with write_transaction(store) as c:
        c.execute(intakes.insert().values(id=intake, payload={'original': 'unchanged'},
            status='tasks_ready', revision=1, created_at=now()))
        c.execute(planning_jobs.insert().values(id=job, intake_id=intake,
            kind='behavioral_preparation', revision=1, status='queued', attempts=1,
            payload=dict(preparation_claim={'original': 'unchanged'},
                preparation_result=dict(status='waiting', execution_authorized=False),
                ai_wait=dict(state='waiting')), message='Saved allowance wait', created_at=now()))
        c.execute(model_attempts.insert().values(id=attempt, job_id=job, task_id=intake,
            source='strategy-control', backend='smart', purpose='check_preparation',
            routing_reason='Frozen included-usage strategy', started_at=now(), finished_at=now(),
            outcome='waiting', usage={'status': 'complete', 'values': {'total_tokens': 123}}))
    prior = task_read(store, intake)
    task_save(store, intake, WorkControlInput(revision=prior.revision, paused=True))
    with store.connect() as c:
        row = dict(c.execute(select(planning_jobs).where(planning_jobs.c.id == job)).mappings().one())
        ledger = dict(c.execute(select(model_attempts).where(model_attempts.c.id == attempt)).mappings().one())
    return row, ledger


def test_deferred_stop_is_exact_idempotent_and_preserves_original_history(store, deferred):
    row, ledger = deferred
    command, binding = str(uuid4()), digest(row)
    receipt = stop_deferred(store, row['id'], binding, command, 'a' * 64)
    assert stop_deferred(store, row['id'], binding, command, 'a' * 64) == receipt
    with store.connect() as c:
        result = c.execute(select(planning_jobs).where(planning_jobs.c.id == row['id'])).mappings().one()
        assert result['status'] == 'failed'
        assert result['payload'] == {**row['payload'], 'deferred_stop': receipt}
        assert dict(c.execute(select(model_attempts).where(model_attempts.c.id == ledger['id'])).mappings().one()) == ledger
        assert c.scalar(select(intakes.c.payload).where(intakes.c.id == row['intake_id'])) == {'original': 'unchanged'}
    with pytest.raises(ValueError, match='already recorded'):
        stop_deferred(store, row['id'], binding, str(uuid4()), 'a' * 64)


@pytest.mark.parametrize('change', ['resumed', 'lease', 'unpaused', 'changed', 'unfinished', 'running-child', 'not-deferred'])
def test_deferred_stop_refuses_active_or_changed_work(store, deferred, change):
    row, ledger = deferred
    binding = digest(row)
    if change == 'unpaused':
        prior = task_read(store, row['intake_id'])
        task_save(store, row['intake_id'], WorkControlInput(revision=prior.revision, paused=False))
    with write_transaction(store) as c:
        if change in {'unfinished', 'running-child'}:
            c.execute(model_attempts.update().where(model_attempts.c.id == ledger['id']).values(
                **({'finished_at': None} if change == 'unfinished' else {'outcome': 'running'})))
        elif change in {'resumed', 'lease', 'changed', 'not-deferred'}:
            values = {'resumed': {'status': 'running'}, 'lease': {'lease': str(uuid4())},
                'changed': {'message': 'Concurrent change'}, 'not-deferred': {'payload': {}}}[change]
            c.execute(planning_jobs.update().where(planning_jobs.c.id == row['id']).values(**values))
    with store.connect() as c:
        before = dict(c.execute(select(planning_jobs).where(planning_jobs.c.id == row['id'])).mappings().one())
    with pytest.raises(ValueError):
        stop_deferred(store, row['id'], binding, str(uuid4()), 'a' * 64)
    with store.connect() as c:
        assert dict(c.execute(select(planning_jobs).where(planning_jobs.c.id == row['id'])).mappings().one()) == before
