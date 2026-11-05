"""Approval binding rehearsal only. No candidate, executor, verifier or external effect."""
import json
import os
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field
from sqlalchemy import select, or_

from workbench import intake
from workbench.db import (admission_requests as records, backlog_items, backlog_dependencies,
                          intakes, proposals, proposal_decisions, planning_jobs, write_transaction)
from workbench.schemas import StrictModel
from workbench.service import now
from workbench.visual_scope import digest

FIXTURE = Path(__file__).resolve().parents[2] / 'fixtures/intake/admission-rehearsal.json'


class FixtureAdmissionRequest(StrictModel):
    command_id: UUID
    mode: Literal['fixture']
    approval_id: UUID
    binding: str = Field(pattern=r'^[a-f0-9]{64}$')


class AdmissionReceipt(StrictModel):
    id: str
    task_id: str
    approval_id: str
    binding: str
    status: Literal['pending', 'admitted', 'blocked', 'stale', 'failed']
    message: str
    created_at: str
    finished_at: str | None
    source: Literal['deterministic-fixture'] = 'deterministic-fixture'
    execution_allowed: Literal[False] = False
    publication_allowed: Literal[False] = False


class CurrentAdmissionAuthority(StrictModel):
    eligible: bool
    approval_id: str | None = None
    binding: str | None = None
    message: str


class AdmissionContext(StrictModel):
    supported: bool
    current_authority: CurrentAdmissionAuthority
    receipts: list[AdmissionReceipt]


class GateError(Exception):
    def __init__(self, status, message, http=409):
        self.status, self.message, self.http = status, message, http


def require(c, table, identity):
    try:
        return intake.require(c, table, identity)
    except HTTPException:
        raise GateError('blocked', 'Required task or approval authority is missing.', 404) from None


def target(c, intake_id, task_id):
    item = require(c, backlog_items, task_id)
    if item['intake_id'] != intake_id:
        raise GateError('blocked', 'This task belongs to a different intake.', 404)
    return item


def matches_original(row, fixture):
    return {k:v for k,v in row['payload'].items() if k != 'request_id'} == fixture['request']


def authority(c, intake_id, task_id, approval_id):
    """Read authoritative records under the caller's transaction; return pinned inputs."""
    item = target(c, intake_id, task_id)
    row = require(c, intakes, intake_id)
    approval = require(c, proposal_decisions, approval_id)
    if (item['kind'] != 'task' or not item['proposal_id'] or
            approval['intake_id'] != intake_id or approval['proposal_id'] != item['proposal_id']):
        raise GateError('blocked', 'Task, proposal and approval do not belong to the same feature scope.')
    proposal = require(c, proposals, item['proposal_id'])
    try:
        review = intake.proposal_view(c, proposal)
    except HTTPException:
        raise GateError('blocked', 'Human scope is missing; approval cannot be established.') from None
    # Supersession is distinct from withdrawal of an otherwise unchanged approval.
    if (proposal['revision'] != row['revision'] or review['binding'] != approval['binding'] or
            review['human_scope']['revision'] != approval['scope_revision']):
        raise GateError('stale', 'The proposal, human scope or artifact binding changed. Review the current scope.')
    try:
        intake.validate_approval(c, approval)
    except HTTPException:
        raise GateError('blocked', 'A current applied implementation approval is required.') from None
    scope = review['human_scope']
    if (scope['origin'] != 'human' or scope['stage'] != 'implementation' or
            scope['visual_requirement'] != 'nonvisual' or review['research_only'] or
            proposal['content']['delivery'] != 'implementation' or
            proposal['content']['visual'] != 'nonvisual'):
        raise GateError('blocked', 'This rehearsal requires explicit human nonvisual implementation approval.')
    if (approval['snapshot'] != review or approval['payload']['proposal_id'] != proposal['id'] or
            approval['payload']['binding'] != review['binding'] or
            approval['payload']['scope_revision'] != scope['revision'] or not approval['finished_at']):
        raise GateError('blocked', 'The immutable applied approval inputs do not match the current review.')
    fixture = json.loads(FIXTURE.read_text())
    if (not matches_original(row, fixture) or proposal['content'] != fixture['planning']['proposal'] or
            proposal['basis'] != 'demo' or proposal['model'] != 'deterministic-demo-v1'):
        raise GateError('blocked', 'Only the exact controlled synthetic feature is supported.')
    spec = fixture['decomposition']['tasks'][0]
    root_id = 'intake-' + intake_id
    expected_id = root_id + '-' + str(proposal['revision']) + '-' + spec['key']
    items = [dict(r) for r in c.execute(select(backlog_items).where(
        backlog_items.c.intake_id == intake_id, backlog_items.c.proposal_id == proposal['id'],
        backlog_items.c.kind != 'request'))
        .mappings()]
    root = require(c, backlog_items, root_id)
    dependencies = sorted(c.execute(select(backlog_dependencies.c.requires_id).where(
        backlog_dependencies.c.item_id == task_id)).scalars())
    if (len(items) != 1 or root['intake_id'] != intake_id or
            root['kind'] != 'request' or root['parent'] is not None or
            item['id'] != expected_id or item['parent'] != root_id or dependencies or
            any(item[k] != spec[k] for k in ('kind', 'title', 'purpose', 'acceptance')) or
            item['model'] != 'deterministic-demo-v1'):
        raise GateError('blocked', 'The controlled fixture requires exactly one matching task with no dependencies.')
    job = c.execute(select(planning_jobs).where(planning_jobs.c.intake_id == intake_id,
        planning_jobs.c.kind == 'decomposition', planning_jobs.c.revision == proposal['revision']))
    job = job.mappings().first()
    expected = dict(proposal_id=proposal['id'], approval_id=approval_id, binding=approval['binding'],
                    scope_revision=scope['revision'])
    if (not job or job['status'] != 'completed' or not job['finished_at'] or
            job['model'] != 'deterministic-demo-v1' or
            any(job['payload'].get(k) != v for k,v in expected.items())):
        raise GateError('blocked', 'Completed decomposition provenance for this exact approval is required.')
    # No general task-to-scope reconstruction: only the versioned, exact one-task fixture.
    snapshot = dict(contract=1, mode='fixture', fixture_version=fixture['version'],
        fixture_sha256=digest(fixture), intake_id=intake_id, task=item, dependencies=dependencies,
        proposal_review=review,
        approval={k:approval[k] for k in ('id', 'intake_id', 'proposal_id', 'binding', 'scope_revision',
            'required_artifacts', 'payload', 'snapshot', 'created_at', 'finished_at')},
        decomposition={k:job[k] for k in ('id', 'intake_id', 'kind', 'revision', 'payload',
                                         'model', 'created_at', 'finished_at')})
    return snapshot, digest(snapshot)


def view(row):
    return {k:v for k,v in row.items() if k not in {'payload', 'snapshot'}}


def context(engine, intake_id, task_id):
    with engine.connect() as c:
        c.exec_driver_sql('BEGIN')
        try:
            item = target(c, intake_id, task_id)
            row = require(c, intakes, intake_id)
        except GateError as error:
            raise HTTPException(error.http, error.message) from None
        receipts = [view(r) for r in c.execute(select(records).where(records.c.task_id == task_id)
            .order_by(records.c.created_at.desc(), records.c.id).limit(20)).mappings()]
        try:
            supported = matches_original(row, json.loads(FIXTURE.read_text())) and item['kind'] == 'task'
        except (OSError, ValueError, KeyError, TypeError):
            supported = bool(receipts)  # Retain audit access when fixture validation is unavailable.
        current = dict(eligible=False, message='A current applied implementation approval is required.')
        approval_id = c.scalar(select(proposal_decisions.c.id).where(
            proposal_decisions.c.proposal_id == item['proposal_id'])
            .order_by(proposal_decisions.c.created_at.desc(), proposal_decisions.c.id).limit(1))
        if approval_id:
            try:
                _, binding = authority(c, intake_id, task_id, approval_id)
                current = dict(eligible=True, approval_id=approval_id, binding=binding,
                    message='Current approval binding is valid for this admission rehearsal only.')
            except GateError as error:
                current['message'] = error.message
            except (OSError, ValueError, KeyError, TypeError):
                current['message'] = 'Current authority is unavailable. Historical receipts supply no current permission.'
        return dict(supported=supported, current_authority=current, receipts=receipts)


def submit(engine, intake_id, task_id, body):
    payload = body.model_dump(mode='json')
    with write_transaction(engine) as c:
        old = c.execute(select(records).where(records.c.id == str(body.command_id))).mappings().first()
        if old:
            if (old['task_id'] != task_id or old['snapshot']['intake_id'] != intake_id or
                    old['payload'] != payload):
                raise HTTPException(409, 'This request ID belongs to different admission input.')
            return view(old)  # Historical receipt only; never a fresh authority assertion.
        try:
            snapshot, binding = authority(c, intake_id, task_id, str(body.approval_id))
        except GateError as error:
            raise HTTPException(error.http, error.message) from None
        if binding != body.binding:
            raise HTTPException(409, 'Admission inputs changed. Reload current authority before requesting.')
        prior = c.scalar(select(records.c.id).where(records.c.task_id == task_id,
            or_(records.c.binding == binding, records.c.status == 'pending')))
        if prior:
            raise HTTPException(409, 'An admission request is already saved for this task/binding or is pending. Open its receipt: ' + prior)
        record = dict(id=str(body.command_id), task_id=task_id, approval_id=str(body.approval_id),
            binding=binding, payload=payload, snapshot=snapshot, status='pending',
            message='Request saved · Waiting for worker validation. Execution denied; publication held.',
            created_at=now(), finished_at=None)
        c.execute(records.insert().values(**record))
    return view(record)


def process_one(engine, crash=None):
    with write_transaction(engine) as c:
        request = c.execute(select(records).where(records.c.status == 'pending')
            .order_by(records.c.created_at, records.c.id).limit(1)).mappings().first()
        if not request:
            return False
        try:
            pinned = request['snapshot']
            item = require(c, backlog_items, request['task_id'])
            dependencies = sorted(c.execute(select(backlog_dependencies.c.requires_id).where(
                backlog_dependencies.c.item_id == request['task_id'])).scalars())
            approval = require(c, proposal_decisions, request['approval_id'])
            job = require(c, planning_jobs, pinned['decomposition']['id'])
            if (item != pinned['task'] or dependencies != pinned['dependencies'] or
                    {k:approval[k] for k in pinned['approval']} != pinned['approval'] or
                    {k:job[k] for k in pinned['decomposition']} != pinned['decomposition'] or
                    digest(json.loads(FIXTURE.read_text())) != pinned['fixture_sha256']):
                raise GateError('stale', 'The saved task, approval inputs, fixture or decomposition changed.')
            snapshot, binding = authority(c, request['snapshot']['intake_id'],
                                            request['task_id'], request['approval_id'])
            if binding != request['binding'] or snapshot != request['snapshot']:
                raise GateError('stale', 'Admission inputs changed after save. No current admission was established.')
            status, message = 'admitted', 'Approval binding checked at the recorded time. No implementation was performed or execution permitted.'
        except GateError as error:
            status, message = error.status, error.message
        except (OSError, ValueError, KeyError, TypeError):
            status, message = 'failed', 'Admission validation could not complete. No permission was granted.'
        c.execute(records.update().where(records.c.id == request['id'], records.c.status == 'pending')
            .values(status=status, message=message + ' Execution denied; publication held.', finished_at=now()))
        if crash == 'before-commit':
            os._exit(91)
    if crash == 'after-commit':
        os._exit(92)
    return True
