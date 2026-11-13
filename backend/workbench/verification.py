"""First M5 slice: verification of a reviewed fixture via the existing worker.

No provider execution, publication, subprocess or pilot integration. The adapter is
pure and deterministic, so result and audit commit atomically like fixture reviews.
"""
from workbench import worker_operation as operation

import json
from pathlib import Path
from typing import Literal
from uuid import UUID
from pydantic import Field
from fastapi import HTTPException
from sqlalchemy import select
from workbench.db import verification_requests as records, commands, applications, write_transaction
from workbench.schemas import StrictModel
from workbench.service import get_task, now, version


class FixtureVerificationRequest(StrictModel):
    command_id: UUID
    version: str = Field(pattern=r'^[a-f0-9]{64}$')
    mode: Literal['fixture']


class Check(StrictModel):
    label: str
    passed: bool


class VerificationReceipt(StrictModel):
    id: str
    task_id: str
    version: str
    status: Literal['pending','completed','stale','blocked','failed']
    attempt_outcome: Literal['not_run','completed','failed']
    verification: Literal['unknown','passed','failed']
    checks: list[Check]
    message: str
    created_at: str
    started_at: str | None
    finished_at: str | None
    source: Literal['deterministic-fixture'] = 'deterministic-fixture'
    execution_activated: Literal[False] = False
    publication_allowed: Literal[False] = False


def admitted(c, row):
    # Contract approval does not grant execution permission. This gate admits only
    # fixed fixture verification, never implementation or a provider call.
    if row['state'] != 'review_confirmed' or row['eligibility'] != 'needs_publication_checks':
        raise HTTPException(403, 'Apply a confirmed fixture contract review first. Execution remains unauthorized.')
    audit = c.execute(select(applications.c.command_id).where(applications.c.task_id == row['id'],
        applications.c.result_version == version(row))).first()
    if not audit:
        raise HTTPException(403, 'Current applied review evidence is missing.')
    review = c.execute(select(commands).where(commands.c.id == audit[0])).mappings().one()
    if review['status'] != 'applied' or not all(v == 'confirm' for v in review['payload']['choices'].values()):
        raise HTTPException(403, 'Current review is not an applied confirmation.')


def view(row):
    return {k:v for k,v in row.items() if k not in {'payload','snapshot'}}


def listing(engine, task_id):
    with engine.connect() as c:
        get_task(c, task_id)
        return [view(r) for r in c.execute(select(records).where(records.c.task_id == task_id)
            .order_by(records.c.created_at.desc(), records.c.id).limit(20)).mappings()]


def submit(engine, task_id, body):
    payload = body.model_dump(mode='json')
    with write_transaction(engine) as c:
        old = c.execute(select(records).where(records.c.id == str(body.command_id))).mappings().first()
        if old:
            if old['task_id'] != task_id or old['payload'] != payload:
                raise HTTPException(409, 'This request ID belongs to a different verification request.')
            return view(old)
        row = get_task(c, task_id)
        if version(row) != body.version:
            raise HTTPException(409, 'Candidate evidence changed. Reload before requesting verification.')
        admitted(c, row)
        if c.execute(select(records.c.id).where(records.c.task_id == task_id, records.c.version == body.version)).first():
            raise HTTPException(409, 'Verification is already recorded for this candidate. Open its existing result.')
        record = dict(id=str(body.command_id), task_id=task_id, version=body.version, payload=payload,
            snapshot=dict(row), status='pending', attempt_outcome='not_run', verification='unknown', checks=[],
            message='Request saved; waiting for the worker. No verification has run yet.', created_at=now(), started_at=None, finished_at=None)
        c.execute(records.insert().values(**record))
        return view(record)


class FixtureVerifier:
    def verify_sample(self, candidate):
        # A distinct exact fixture identity; never relax the canonical Phase 1 path.
        from workbench.implementation import load_fixture, artifact_valid, CONTRACT
        from workbench.visual_scope import digest
        fixture = load_fixture()
        artifact = json.loads(candidate['artifact'])
        return [dict(label='Sample identity and content match the controlled fixture',
                     passed=bool(artifact_valid(candidate) and
                         candidate['provenance']['fixture_sha256'] == digest(fixture) and
                         candidate['provenance']['fixture_version'] == fixture['version'])),
                dict(label='Label examples remove surrounding spaces and preserve interior text',
                     passed=artifact == dict(format=CONTRACT, version=fixture['version'],
                         cases=[dict(input=v['input'], output=v['expected']) for v in fixture['cases']]))]

    def verify(self, row):
        source = Path(__file__).resolve().parents[2]/'fixtures/tasks.json'
        expected = next((r for r in json.loads(source.read_text()) if r['id'] == row['id']), None)
        evidence = row['evidence']
        return [dict(label='Candidate identity matches the controlled fixture', passed=bool(expected and
                    all(evidence.get(k) == expected['evidence'].get(k) for k in ('candidate','patch_sha256')))),
                dict(label='Behavior claims match the controlled fixture contract', passed=bool(expected and evidence.get('claims') == expected['evidence'].get('claims')))]


@operation.isolated
def _process_original(engine, adapter=None, crash=False):
    with write_transaction(engine) as c:
        request = c.execute(select(records).where(records.c.status == 'pending')
            .order_by(records.c.created_at, records.c.id).limit(1)).mappings().first()
        if not request:
            return False
        operation.selected(records, request)
        row = get_task(c, request['task_id'])
        values = {}
        if version(row) != request['version']:
            values.update(status='stale', message='Candidate changed; no verification ran.')
        else:
            try:
                admitted(c, row)
            except HTTPException:
                values.update(status='blocked', message='Review admission is no longer valid; no verification ran.')
            else:
                values['started_at'] = now()
                try:
                    checks = [Check.model_validate(v).model_dump() for v in (adapter or FixtureVerifier()).verify(row)]
                    if not checks or len(checks)>20:
                        raise ValueError('Invalid check envelope')
                    values.update(status='completed', attempt_outcome='completed', checks=checks,
                        verification='passed' if all(v['passed'] for v in checks) else 'failed',
                        message='Deterministic fixture checks completed. This is not addon verification; publication and live execution remain held.')
                except (OSError, ValueError, TypeError):
                    values.update(status='failed', attempt_outcome='failed', verification='unknown',
                        message='Fixture verifier failed. No check result or publication permission is inferred.')
        # Capture terminal handling after admission/checks, including failure paths.
        # These event times commit atomically; they are not commit timestamps.
        values['finished_at'] = now()
        c.execute(records.update().where(records.c.id == request['id']).values(**values))
        if crash:
            raise RuntimeError('Simulated crash before atomic result commit')
    return True


def process_one(engine, adapter=None, crash=False):
    if _process_original(engine, adapter, crash):
        return True
    from workbench import implementation
    return implementation.process_verification(engine, adapter, crash)
