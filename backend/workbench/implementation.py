"""Exact synthetic task only. Inert inline artifacts; no executor, model or filesystem output."""
import hashlib
import json
import os
from pathlib import Path
from typing import Literal
from uuid import UUID, NAMESPACE_URL, uuid5

from fastapi import HTTPException
from pydantic import Field
from sqlalchemy import or_, select

from workbench import admission
from workbench.admission import GateError
from workbench.db import (implementation_requests as records, implementation_attempts as attempts,
    implementation_candidates as candidates, candidate_verification_requests as verifications,
    admission_requests, write_transaction)
from workbench.schemas import StrictModel
from workbench.service import now
from workbench.visual_scope import digest

FIXTURE = Path(__file__).resolve().parents[2]/'fixtures/intake/implementation-v1.json'
CONTRACT = 'synthetic-label-implementation-v1'
ERRORS = (OSError, ValueError, KeyError, TypeError)
Status = Literal['pending', 'completed', 'blocked', 'stale', 'failed']


class Holds(StrictModel):
    source: Literal['deterministic-fixture'] = 'deterministic-fixture'
    execution_allowed: Literal[False] = False
    publication_allowed: Literal[False] = False


class SimulatedImplementationRequest(StrictModel):
    command_id: UUID
    mode: Literal['fixture']
    admission_id: UUID
    binding: str = Field(pattern=r'^[a-f0-9]{64}$')


class CandidateVerificationRequest(StrictModel):
    command_id: UUID
    mode: Literal['fixture']
    binding: str = Field(pattern=r'^[a-f0-9]{64}$')


class CandidateVerificationReceipt(Holds):
    id: str
    task_id: str
    candidate_id: str
    status: Status
    verification: Literal['unknown', 'passed', 'failed']
    checks: list[dict]
    message: str
    created_at: str
    started_at: str | None
    finished_at: str | None


class SimulatedAttempt(Holds):
    # Keep corrupt/imported evidence readable without claiming its source is valid.
    # Generation still writes only deterministic-fixture; current gates check it.
    source: str
    id: str
    request_id: str
    outcome: Literal['completed', 'failed']
    started_at: str
    finished_at: str


class SampleCandidate(Holds):
    id: str
    attempt_id: str
    provenance: dict
    created_at: str
    integrity_valid: bool
    current: bool
    artifact_text: str | None
    verification: CandidateVerificationReceipt | None


class ImplementationReceipt(Holds):
    id: str
    task_id: str
    admission_id: str
    binding: str
    status: Status
    message: str
    created_at: str
    finished_at: str | None
    attempt: SimulatedAttempt | None
    candidate: SampleCandidate | None


class ImplementationAuthority(StrictModel):
    eligible: bool
    admission_id: str | None = None
    binding: str | None = None
    message: str


class ImplementationContext(StrictModel):
    supported: bool
    current_authority: ImplementationAuthority
    receipts: list[ImplementationReceipt]


def load_fixture():
    raw = FIXTURE.read_bytes()
    if len(raw) > 32768:
        raise ValueError('Oversized fixture')
    value = json.loads(raw)
    if (value['contract'] != CONTRACT or value['version'] != 1 or
            value['media_type'] != 'application/json' or not 1 <= len(value['cases']) <= 20 or
            any(set(case) != {'input', 'expected'} or
                any(not isinstance(v, str) or len(v) > 1000 for v in case.values())
                for case in value['cases'])):
        raise ValueError('Unsupported implementation fixture')
    return value


def require(c, table, identity):
    row = c.execute(select(table).where(table.c.id == identity)).mappings().first()
    if not row:
        raise GateError('blocked', 'Required sample evidence is missing.', 404)
    return dict(row)


def input_authority(c, intake_id, task_id, admission_id):
    receipt = require(c, admission_requests, admission_id)
    if receipt['task_id'] != task_id or receipt['snapshot']['intake_id'] != intake_id:
        raise GateError('blocked', 'The approval check belongs to another task.', 404)
    if receipt['status'] != 'admitted' or not receipt['finished_at']:
        raise GateError('blocked', 'A completed approval check is needed first.')
    snapshot, binding = admission.authority(c, intake_id, task_id, receipt['approval_id'])
    if snapshot != receipt['snapshot'] or binding != receipt['binding']:
        raise GateError('stale', 'The task or approved plan changed after the approval check.')
    fixture = load_fixture()
    snapshot = dict(contract=CONTRACT, admission=receipt, fixture=fixture, fixture_sha256=digest(fixture))
    return snapshot, digest(snapshot)


def check_request(c, request):
    pinned = request['snapshot']
    if (digest(pinned) != request['binding'] or
            pinned['admission']['id'] != request['admission_id'] or
            pinned['admission']['task_id'] != request['task_id'] or
            request['payload'] != dict(command_id=request['id'], mode='fixture',
                admission_id=request['admission_id'], binding=request['binding'])):
        raise GateError('stale', 'The saved sample request no longer matches its inputs.')
    original = pinned['admission']['snapshot']
    if admission.target(c, original['intake_id'], request['task_id']) != original['task']:
        raise GateError('stale', 'The task changed after the sample request was saved.')
    snapshot, binding = input_authority(c, pinned['admission']['snapshot']['intake_id'],
                                       request['task_id'], request['admission_id'])
    if snapshot != pinned or binding != request['binding']:
        raise GateError('stale', 'The approval, task or sample definition changed after save.')


def artifact_valid(candidate):
    try:
        data, p = candidate['artifact'], candidate['provenance']
        return (0 < len(data) <= 32768 and len(data) == p['artifact_size'] and
                hashlib.sha256(data).hexdigest() == p['artifact_sha256'] and
                digest(p) == candidate['id'] and p['contract'] == CONTRACT and
                p['media_type'] == 'application/json')
    except ERRORS:
        return False


def provenance(request, attempt, artifact):
    pinned = request['snapshot']
    return dict(contract=CONTRACT, request_id=request['id'], request_binding=request['binding'],
        task_id=request['task_id'], admission_id=request['admission_id'],
        approval_id=pinned['admission']['approval_id'], approval_binding=pinned['admission']['binding'],
        fixture_sha256=pinned['fixture_sha256'], fixture_version=pinned['fixture']['version'],
        media_type=pinned['fixture']['media_type'], artifact_size=len(artifact),
        artifact_sha256=hashlib.sha256(artifact).hexdigest(), attempt=attempt)


def validate_candidate(c, intake_id, task_id, candidate_id):
    candidate = require(c, candidates, candidate_id)
    attempt = require(c, attempts, candidate['attempt_id'])
    request = require(c, records, attempt['request_id'])
    if (request['task_id'] != task_id or
            request['snapshot']['admission']['snapshot']['intake_id'] != intake_id):
        raise GateError('blocked', 'The sample belongs to another task.', 404)
    if (request['status'] != 'completed' or attempt['outcome'] != 'completed' or
            attempt['source'] != 'deterministic-fixture' or not artifact_valid(candidate) or
            candidate['created_at'] != attempt['finished_at'] or
            request['finished_at'] != attempt['finished_at'] or
            candidate['provenance'] != provenance(request, attempt, candidate['artifact'])):
        raise GateError('stale', 'Sample integrity or candidate binding could not be confirmed.')
    check_request(c, request)
    return candidate


def public(row):
    return {k:v for k,v in row.items() if k not in {'snapshot', 'payload', 'artifact'}}


def candidate_identity(row):
    return {k:row[k] for k in ('id', 'attempt_id', 'provenance', 'created_at')}


def receipt_view(c, row, intake_id):
    result = public(row)
    attempt = c.execute(select(attempts).where(attempts.c.request_id == row['id'])).mappings().first()
    result['attempt'] = dict(attempt) if attempt else None
    result['candidate'] = None
    if attempt:
        candidate = c.execute(select(candidates).where(candidates.c.attempt_id == attempt['id'])).mappings().first()
        if candidate:
            valid = artifact_valid(candidate)
            try:
                validate_candidate(c, intake_id, row['task_id'], candidate['id'])
                current = True
            except (GateError, *ERRORS):
                current = False
            verification = c.execute(select(verifications).where(
                verifications.c.candidate_id == candidate['id'])).mappings().first()
            result['candidate'] = dict(**public(candidate), integrity_valid=valid, current=current,
                artifact_text=candidate['artifact'].decode('utf-8', errors='replace') if valid else None,
                verification=public(verification) if verification else None)
    return result


def context(engine, intake_id, task_id):
    with engine.connect() as c:
        c.exec_driver_sql('BEGIN')
        try:
            item = admission.target(c, intake_id, task_id)
            original = admission.require(c, admission.intakes, intake_id)
        except GateError as error:
            raise HTTPException(error.http, error.message) from None
        rows = list(c.execute(select(records).where(records.c.task_id == task_id)
                    .order_by(records.c.created_at.desc(), records.c.id).limit(20)).mappings())
        try:
            supported = admission.matches_original(original, json.loads(admission.FIXTURE.read_text())) and item['kind'] == 'task'
        except ERRORS:
            supported = bool(rows)
        current = dict(eligible=False, message='Complete the current task approval check before making a sample.')
        admitted = c.scalar(select(admission_requests.c.id).where(admission_requests.c.task_id == task_id,
            admission_requests.c.status == 'admitted').order_by(admission_requests.c.created_at.desc()).limit(1))
        if admitted:
            try:
                _, binding = input_authority(c, intake_id, task_id, admitted)
                current = dict(eligible=True, admission_id=admitted, binding=binding,
                               message='Current approval supports this sample only. Real execution remains denied.')
            except GateError as error:
                current['message'] = error.message
            except ERRORS:
                current['message'] = 'Sample inputs are unavailable. Saved history is kept.'
        return dict(supported=supported, current_authority=current,
                    receipts=[receipt_view(c, r, intake_id) for r in rows])


def submit(engine, intake_id, task_id, body):
    payload = body.model_dump(mode='json')
    with write_transaction(engine) as c:
        old = c.execute(select(records).where(records.c.id == str(body.command_id))).mappings().first()
        if old:
            if (old['task_id'] != task_id or old['payload'] != payload or
                    old['snapshot']['admission']['snapshot']['intake_id'] != intake_id):
                raise HTTPException(409, 'This request ID belongs to different sample input.')
            return receipt_view(c, old, intake_id)
        try:
            snapshot, binding = input_authority(c, intake_id, task_id, str(body.admission_id))
        except GateError as error:
            raise HTTPException(error.http, error.message) from None
        except ERRORS:
            raise HTTPException(409, 'Sample inputs are unavailable. No request was saved.') from None
        if binding != body.binding:
            raise HTTPException(409, 'Sample inputs changed. Reload before requesting a sample.')
        if c.scalar(select(records.c.id).where(records.c.task_id == task_id,
                or_(records.c.binding == binding, records.c.status == 'pending'))):
            raise HTTPException(409, 'A sample request for these inputs is already saved or waiting.')
        row = dict(id=str(body.command_id), task_id=task_id, admission_id=str(body.admission_id),
            binding=binding, payload=payload, snapshot=snapshot, status='pending',
            message='Sample request saved. No simulated attempt has run yet.', created_at=now(), finished_at=None)
        c.execute(records.insert().values(**row))
        return receipt_view(c, row, intake_id)


def generate(fixture):
    """Pure Python transformation of fixed strings. The artifact is data, never executable."""
    artifact = dict(format=CONTRACT, version=fixture['version'],
                    cases=[dict(input=case['input'], output=case['input'].strip()) for case in fixture['cases']])
    return (json.dumps(artifact, ensure_ascii=False, indent=2)+'\n').encode('utf-8')


def crash_at(crash, point):
    if crash == point:
        os._exit(92 if point == 'after-commit' else 91)


def process_one(engine, crash=None):
    with write_transaction(engine) as c:
        request = c.execute(select(records).where(records.c.status == 'pending')
            .order_by(records.c.created_at, records.c.id).limit(1)).mappings().first()
        if not request:
            return False
        started = None
        try:
            check_request(c, request)
            started = now()
            artifact = generate(request['snapshot']['fixture'])
            if not isinstance(artifact, bytes) or not 0 < len(artifact) <= 32768:
                raise ValueError('Invalid artifact envelope')
            status, message = 'completed', 'Sample prepared by a simulated attempt. It has not been checked or reviewed by a person.'
        except GateError as error:
            status, message = error.status, error.message
        except ERRORS:
            status, message = 'failed', 'The sample could not be prepared. No candidate is available.'
        finished = now()
        if started:
            attempt = dict(id=str(uuid5(NAMESPACE_URL, 'twichui-simulated-attempt:'+request['id'])),
                request_id=request['id'], source='deterministic-fixture', outcome='completed' if status == 'completed' else 'failed',
                started_at=started, finished_at=finished)
            c.execute(attempts.insert().values(**attempt))
            if status == 'completed':
                evidence = provenance(request, attempt, artifact)
                c.execute(candidates.insert().values(id=digest(evidence), attempt_id=attempt['id'],
                    provenance=evidence, artifact=artifact, created_at=finished))
                crash_at(crash, 'after-artifact')
        c.execute(records.update().where(records.c.id == request['id'], records.c.status == 'pending')
            .values(status=status, message=message+' Real execution denied; publication held.', finished_at=finished))
        crash_at(crash, 'before-commit')
    crash_at(crash, 'after-commit')
    return True


def submit_verification(engine, intake_id, task_id, candidate_id, body):
    payload = body.model_dump(mode='json')
    with write_transaction(engine) as c:
        old = c.execute(select(verifications).where(verifications.c.id == str(body.command_id))).mappings().first()
        if old:
            if (old['task_id'] != task_id or old['candidate_id'] != candidate_id or old['payload'] != payload or
                    old['snapshot']['intake_id'] != intake_id):
                raise HTTPException(409, 'This request ID belongs to different sample check input.')
            return public(old)
        try:
            candidate = validate_candidate(c, intake_id, task_id, candidate_id)
        except GateError as error:
            raise HTTPException(error.http, error.message) from None
        except ERRORS:
            raise HTTPException(409, 'The sample cannot be checked while its inputs are unavailable.') from None
        if body.binding != candidate['id']:
            raise HTTPException(409, 'Sample identity changed. Reload before checking.')
        if c.scalar(select(verifications.c.id).where(verifications.c.candidate_id == candidate_id)):
            raise HTTPException(409, 'A check is already recorded for this sample.')
        row = dict(id=str(body.command_id), task_id=task_id, candidate_id=candidate_id, payload=payload,
            snapshot=dict(intake_id=intake_id, candidate=candidate_identity(candidate)), status='pending',
            verification='unknown', checks=[], message='Sample check saved. No verification has run yet.',
            created_at=now(), started_at=None, finished_at=None)
        c.execute(verifications.insert().values(**row))
        return public(row)


def process_verification(engine, adapter=None, crash=None):
    from workbench.verification import Check, FixtureVerifier
    with write_transaction(engine) as c:
        request = c.execute(select(verifications).where(verifications.c.status == 'pending')
            .order_by(verifications.c.created_at, verifications.c.id).limit(1)).mappings().first()
        if not request:
            return False
        values = {}
        try:
            candidate = validate_candidate(c, request['snapshot']['intake_id'], request['task_id'], request['candidate_id'])
            if candidate_identity(candidate) != request['snapshot']['candidate']:
                raise GateError('stale', 'The sample binding changed after the check was saved.')
            values['started_at'] = now()
            checks = [Check.model_validate(v).model_dump() for v in (adapter or FixtureVerifier()).verify_sample(candidate)]
            if not checks or len(checks) > 20:
                raise ValueError('Invalid check envelope')
            values.update(status='completed', verification='passed' if all(v['passed'] for v in checks) else 'failed',
                checks=checks, message='Sample checks completed. This is not addon verification or human review.')
        except GateError as error:
            values.update(status=error.status, message=error.message)
        except ERRORS:
            values.update(status='failed', message='Sample checking could not finish. No verification success is inferred.')
        values.update(finished_at=now())
        values['message'] += ' Real execution denied; publication held.'
        c.execute(verifications.update().where(verifications.c.id == request['id'], verifications.c.status == 'pending').values(**values))
        if crash is True:
            raise RuntimeError('Simulated crash before atomic sample result commit')
        crash_at(crash, 'before-commit')
    crash_at(crash, 'after-commit')
    return True
