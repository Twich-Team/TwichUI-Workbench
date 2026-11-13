"""Faults pass through production scheduling and durable journals, without live providers."""
from uuid import uuid4
import sqlite3

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from workbench import planning_worker, worker
from workbench.db import commands, planning_jobs, write_transaction
from workbench.planning_provider import DemoProvider
from workbench.service import now
from test_intake import submit, get


class BrokenProvider(DemoProvider):
    def __init__(self):
        self.dispatched = []

    def generate(self, packet, schema):
        self.dispatched.append(packet['original']['title'])
        if packet['original']['title'] == 'Malformed task':
            raise RuntimeError('Controlled response lost after possible external dispatch')
        return super().generate(packet, schema)


def test_uncertain_task_failure_is_not_replayed_and_other_request_completes(client, headers, store):
    bad, _ = submit(client, headers, title='Malformed task', kind='issue')
    good, _ = submit(client, headers, title='Healthy task', kind='issue')
    provider = BrokenProvider()
    for _ in range(8):
        worker.tick(store, planning_adapter=provider)
    with store.connect() as c:
        jobs = {r['intake_id']: r for r in c.execute(select(planning_jobs)).mappings()}
    assert jobs[bad['id']]['status'] == 'failed'
    assert jobs[bad['id']]['payload']['worker_operation_failure'] == 'RuntimeError'
    assert 'No automatic replay' in jobs[bad['id']]['message']
    assert provider.dispatched == ['Malformed task', 'Healthy task']
    assert get(client, good['id'])['status'] == 'completed'
    assert len(get(client, good['id'])['tasks']) == 1


def test_expected_malformed_command_is_contained_in_its_journal(store):
    from workbench.db import tasks
    from workbench.service import version
    with write_transaction(store) as c:
        row = c.execute(select(tasks)).mappings().first()
        identity = str(uuid4())
        c.execute(commands.insert().values(id=identity, task_id=row['id'], version=version(row),
            payload={}, reviewed_evidence=row['evidence'], reviewed_revision=row['revision'],
            status='pending', message='', created_at=now()))
    assert worker.tick(store, planning_adapter=DemoProvider())
    with store.connect() as c:
        saved = c.execute(select(commands).where(commands.c.id == identity)).mappings().one()
    assert saved['status'] == 'failed'
    assert 'KeyError' in saved['message']
    for _ in range(3):
        assert not worker.tick(store, planning_adapter=DemoProvider())


def test_global_database_hazard_is_never_swallowed(client, headers, store, monkeypatch):
    submit(client, headers)
    def corrupt(*_):
        raise OperationalError('controlled', {}, sqlite3.DatabaseError('database disk image is malformed'))
    monkeypatch.setattr(planning_worker, 'evidence_packet', corrupt)
    with pytest.raises(OperationalError):
        worker.tick(store, planning_adapter=DemoProvider())


def test_continuously_busy_earlier_stage_cannot_starve_queued_planning(client, headers, store, monkeypatch):
    queued, _ = submit(client, headers)
    monkeypatch.setattr(worker, 'process_one', lambda *_: True)
    for _ in range(17):
        worker.tick(store, planning_adapter=DemoProvider())
        if get(client, queued['id'])['status'] == 'completed':
            break
    assert get(client, queued['id'])['status'] == 'completed'
