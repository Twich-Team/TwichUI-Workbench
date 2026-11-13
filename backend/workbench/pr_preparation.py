"""Sample PR preparation only. Durable fake remote; no external-write adapter exists."""
from workbench import worker_operation as operation

from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field, field_validator
from sqlalchemy import or_, select

from workbench import implementation as impl, candidate_review as review
from workbench.admission import GateError
from workbench.db import pr_preparations as records, fake_publications as effects, write_transaction
from workbench.schemas import StrictModel
from workbench.service import now
from workbench.visual_scope import digest

CONTRACT = 'controlled-draft-pr-v1'
ACTIVE = ('pending', 'preparing', 'reconciling')


class PreparationSettings(impl.Holds):
    draft: Literal[True] = True
    title: str = Field(min_length=1, max_length=160, pattern=r'^[^\x00-\x1f\x7f]+$')
    category: review.Category = 'Feature'
    fake_outcome: Literal['success', 'reject', 'lost-response'] = 'success'
    target_repository: None = None
    base_branch: None = None

    @field_validator('title')
    @classmethod
    def meaningful_title(cls, value):
        if not value.strip():
            raise ValueError('A readable sample title is required')
        return value


class PreparationRequest(StrictModel):
    command_id: UUID
    mode: Literal['fixture']
    review_fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')
    settings: PreparationSettings


class Preview(impl.Holds):
    draft: Literal[True] = True
    title: str
    category: review.Category
    body: str
    target_repository: None = None
    base_branch: None = None


class PreparationReceipt(impl.Holds):
    id: str
    task_id: str
    candidate_id: str
    binding: str
    status: Literal['pending', 'preparing', 'reconciling', 'prepared', 'rejected', 'blocked', 'stale', 'failed']
    message: str
    created_at: str
    started_at: str | None
    finished_at: str | None
    preview: Preview
    settings: PreparationSettings
    review_fingerprint: str
    result: dict | None
    current_eligible: bool
    current_message: str


class PreparationContext(impl.Holds):
    review: review.Review
    settings: PreparationSettings
    preview: Preview
    history: list[PreparationReceipt]


def preview(data, settings):
    saved_checks = data['verification'].get('checks') if data['verification'] else []
    checks = [v for v in saved_checks if isinstance(v, dict) and isinstance(v.get('label'), str) and
              type(v.get('passed')) is bool] if isinstance(saved_checks, list) else []
    heading = [] if data['eligible'] else ['Preview held: '+data['message'], 'Recorded checks below are historical; current validity is not confirmed.', '']
    body = '\n'.join(heading+[
        data['purpose'], '', 'Task: '+data['task_id'], 'Category: '+settings['category'],
        '', 'Intended sample change', 'Compare the pinned original label inputs with the existing saved sample outputs.',
        'The intended behavior trims surrounding whitespace and preserves interior text. No addon files changed.',
        '', 'Approved task scope' if data['eligible'] else 'Task scope (approval not confirmed)', *['- '+v for v in data['scope']],
        '', 'Recorded verification', 'Sample check: '+str(data['verification']['id'] if data['verification'] else 'not recorded'), *['- '+v['label']+(': passed' if v['passed'] else ': did not pass') for v in checks],
        '', 'Not performed / remaining human checks', *['- '+v for v in data['limitations']],
        '', 'Provenance (sample only)', 'Candidate: '+data['candidate_id'],
        'Synthetic base SHA-256: '+str(data['base_identity']),
        'Review evidence: '+data['fingerprint'], 'Git base / target repository / branch: unconfigured.',
        'Draft preview only. No real PR, URL or publication permission.'])
    return Preview(title=settings['title'], category=settings['category'], body=body).model_dump()


def snapshot(c, intake_id, task_id, candidate_id, settings):
    data, check = review.build(c, intake_id, task_id, candidate_id)
    if not data['eligible']:
        raise GateError('blocked', data['message'])
    return dict(contract=CONTRACT, intake_id=intake_id, task_id=task_id, candidate_id=candidate_id,
        review=data, verification=check, settings=settings, preview=preview(data, settings))


def request_intact(row):
    try:
        pinned = row['snapshot']
        payload = PreparationRequest.model_validate(row['payload']).model_dump(mode='json')
        return (digest(pinned) == row['binding'] and pinned['contract'] == CONTRACT and
            payload == row['payload'] and payload['command_id'] == row['id'] and
            pinned['candidate_id'] == row['candidate_id'] and pinned['task_id'] == row['task_id'] and
            payload['settings'] == pinned['settings'] and payload['review_fingerprint'] == pinned['review']['fingerprint'] and
            pinned['preview'] == preview(pinned['review'], pinned['settings']))
    except (*impl.ERRORS, AttributeError):
        return False


def current(c, row):
    if not request_intact(row):
        raise GateError('stale', 'The saved sample PR inputs failed integrity checks.')
    pinned = row['snapshot']
    fresh = snapshot(c, pinned['intake_id'], row['task_id'], row['candidate_id'], pinned['settings'])
    if fresh != pinned:
        raise GateError('stale', 'Candidate, base, checks or review evidence changed after save.')


def view(c, row):
    valid, message = True, 'Current sample evidence is eligible; real publication is disabled.'
    try:
        current(c, row)
    except GateError as error:
        valid, message = False, error.message
    except impl.ERRORS:
        valid, message = False, 'Current sample evidence is unavailable. Historical result is retained.'
    return dict(**{k:row[k] for k in ('id','task_id','candidate_id','binding','status','message',
        'created_at','started_at','finished_at','result')}, preview=row['snapshot']['preview'],
        settings=row['snapshot']['settings'], review_fingerprint=row['snapshot']['review']['fingerprint'],
        current_eligible=valid, current_message=message)


def context(engine, intake_id, task_id, candidate_id):
    with engine.connect() as c:
        c.exec_driver_sql('BEGIN')
        try:
            data, _ = review.build(c, intake_id, task_id, candidate_id)
        except GateError as error:
            raise HTTPException(error.http, error.message) from None
        settings = PreparationSettings(title='Sample: '+data['title'][:152]).model_dump()
        history = c.execute(select(records).where(records.c.task_id == task_id)
            .order_by(records.c.created_at.desc(), records.c.id).limit(20)).mappings()
        return dict(review=data, settings=settings, preview=preview(data, settings), history=[view(c, row) for row in history])


def submit(engine, intake_id, task_id, candidate_id, body):
    payload = body.model_dump(mode='json')
    with write_transaction(engine) as c:
        old = c.execute(select(records).where(records.c.id == str(body.command_id))).mappings().first()
        if old:
            if (old['task_id'] != task_id or old['candidate_id'] != candidate_id or old['payload'] != payload or
                    old['snapshot']['intake_id'] != intake_id):
                raise HTTPException(409, 'This request ID already belongs to different sample PR inputs.')
            return view(c, old)
        try:
            pinned = snapshot(c, intake_id, task_id, candidate_id, payload['settings'])
        except GateError as error:
            raise HTTPException(error.http, error.message) from None
        except impl.ERRORS:
            raise HTTPException(409, 'Sample PR inputs are unavailable. No request was saved.') from None
        if body.review_fingerprint != pinned['review']['fingerprint']:
            raise HTTPException(409, 'The comparison or evidence changed. Review changes again before preparing.')
        binding = digest(pinned)
        if c.scalar(select(records.c.id).where(records.c.task_id == task_id,
                or_(records.c.binding == binding, records.c.status.in_(ACTIVE)))):
            raise HTTPException(409, 'This sample PR is already saved, or another preparation is waiting. Open its history.')
        row = dict(id=str(body.command_id), task_id=task_id, candidate_id=candidate_id, binding=binding,
            payload=payload, snapshot=pinned, status='pending', created_at=now(), started_at=None, finished_at=None,
            message='Request saved. Waiting for the worker to prepare a sample PR. Nothing was published.', result=None)
        c.execute(records.insert().values(**row))
        return view(c, row)


def effect_data(row):
    return dict(source='deterministic-fake-publisher', request_id=row['id'], binding=row['binding'],
        candidate_id=row['candidate_id'], logical_id='sample-'+row['binding'][:24], draft=True,
        outcome='rejected' if row['snapshot']['settings']['fake_outcome']=='reject' else 'prepared',
        real_pr_created=False, url=None)


def terminal(c, row, status, message, result=None):
    c.execute(records.update().where(records.c.id==row['id'], records.c.status.in_(ACTIVE))
        .values(status=status, message=message, finished_at=now(), result=result))


def gate_failure(c, row):
    try:
        current(c, row)
    except GateError as error:
        terminal(c, row, error.status, error.message+' No new simulated publication occurred.')
        return True
    except impl.ERRORS:
        terminal(c, row, 'failed', 'Sample PR inputs are unavailable. No new simulated publication occurred.')
        return True
    return False


@operation.isolated
def process_one(engine, crash=None, stop_event=None, publisher=None):
    with engine.connect() as connection:
        sample_pending = connection.scalar(select(records.c.id).where(records.c.status.in_(ACTIVE)).limit(1))
    if not sample_pending:
        from workbench import real_publication
        return real_publication.process_one(engine, publisher=publisher, stop_event=stop_event)
    if crash == 'before-commit':
        crash = 'before-receipt-commit'
    # Each phase acquires the existing DB writer lock. No slow work, lease or network.
    with write_transaction(engine) as c:
        row = c.execute(select(records).where(records.c.status.in_(ACTIVE))
            .order_by(records.c.created_at, records.c.id).limit(1)).mappings().first()
        if not row:
            return False
        operation.selected(records, row)
        identity = row['id']
        if not request_intact(row):
            terminal(c, row, 'stale', 'Saved sample PR input integrity failed. No new effect was attempted; earlier simulated evidence is retained.')
            return True
        recorded = c.scalar(select(effects.c.binding).where(effects.c.binding==row['binding']))
        if not recorded and gate_failure(c, row):
            return True
        if row['status']=='pending':
            c.execute(records.update().where(records.c.id==identity).values(status='preparing', started_at=now(),
                message='Preparing the saved sample PR. No real publication is enabled.'))
    impl.crash_at(crash, 'after-start')
    with write_transaction(engine) as c:
        row = impl.require(c, records, identity)
        if row['status'] not in ACTIVE:
            return True
        if not request_intact(row):
            terminal(c, row, 'stale', 'Saved sample PR input integrity failed. No new effect was attempted; earlier simulated evidence is retained.')
            return True
        effect = c.execute(select(effects).where(effects.c.binding==row['binding'])).mappings().first()
        created = effect is None
        if created:
            if gate_failure(c, row):
                return True
            data = effect_data(row)
            c.execute(effects.insert().values(binding=row['binding'], request_id=identity,
                evidence=data, evidence_sha256=digest(data), created_at=now()))
        if created and row['snapshot']['settings']['fake_outcome']=='lost-response':
            c.execute(records.update().where(records.c.id==identity).values(status='reconciling',
                message='The sample publisher response was lost. Waiting to check its saved simulated result; no real PR was created.'))
        impl.crash_at(crash, 'before-effect-commit')
    impl.crash_at(crash, 'after-effect')
    if created and row['snapshot']['settings']['fake_outcome']=='lost-response':
        return True
    with write_transaction(engine) as c:
        row = impl.require(c, records, identity)
        if row['status'] not in ACTIVE:
            return True
        effect = c.execute(
            select(effects).where(effects.c.binding==row['binding'])).mappings().one()
        data = effect['evidence']
        if (not request_intact(row) or effect['request_id']!=identity or
                data != effect_data(row) or digest(data)!=effect['evidence_sha256']):
            terminal(c, row, 'failed', 'Saved simulated publisher evidence failed integrity checks. No repeat effect was attempted.')
        else:
            result = dict(**data, reconciled=not created, recorded_at=effect['created_at'])
            terminal(c, row, data['outcome'],
                'Sample PR prepared. No real PR was created.' if data['outcome']=='prepared' else
                'The sample publisher rejected this preview. No real PR was created.', result)
        impl.crash_at(crash, 'before-receipt-commit')
    impl.crash_at(crash, 'after-commit')
    return True
