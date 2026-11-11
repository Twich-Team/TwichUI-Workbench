"""Explicit correction of settled feature decomposition, in its existing job owner.

A new command permits one bounded read-only operation. Earlier consent, claims,
usage and rejected evidence are retained; uncertain generation cannot be renewed.
"""
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field
from sqlalchemy import select

from workbench import intake, provider_routes
from workbench.db import intakes, planning_jobs, proposals, model_attempts, write_transaction
from workbench.schemas import StrictModel
from workbench.visual_scope import digest


class CorrectionInput(StrictModel):
    command_id: UUID
    binding: str = Field(pattern=r'^[a-f0-9]{64}$')


class CorrectionOffer(StrictModel):
    binding: str
    job_id: str
    message: str


def _job(c, row):
    return c.execute(select(planning_jobs).where(planning_jobs.c.intake_id == row['id'],
        planning_jobs.c.kind == 'decomposition', planning_jobs.c.revision == row['revision'])).mappings().first()


def offer(c, row):
    if not row['payload'].get('feature_planning') or row['status'] != 'failed':
        return None
    job = _job(c, row)
    if not job or job['status'] != 'failed' or not job['finished_at'] or job['lease']:
        return None
    diagnostics = job['payload'].get('diagnostics', {})
    if diagnostics.get('generation_state') != 'finished' or diagnostics.get('accounting_state') != 'complete':
        return None
    attempts = list(c.execute(select(model_attempts).where(model_attempts.c.job_id == job['id'])
        .order_by(model_attempts.c.started_at, model_attempts.c.id)).mappings())
    if (not attempts or any(not a['finished_at'] for a in attempts)
            or attempts[-1]['outcome'] != 'failed' or not attempts[-1]['usage']):
        return None
    proposal = intake.require(c, proposals, job['payload']['proposal_id'])
    if (proposal['revision'] != row['revision']
            or job['payload'].get('scope_revision') != intake.human_scope(c, row['id'])['revision']):
        return None
    # Archive and cleanup retain evidence but withdraw dispatch authority.
    from workbench.task_cleanup import require_current
    try:
        require_current(c, row['id'])
        route = provider_routes.approved(c, row)
    except (ValueError, HTTPException):
        return None
    binding = digest(dict(original=row['payload'], proposal=intake.proposal_view(c, proposal)['binding'],
        job_id=job['id'], revision=job['revision'], attempts=job['attempts'], finished_at=job['finished_at'],
        failure=job['message'], source=job['payload'].get('feature_base'),
        scope_revision=job['payload']['scope_revision'], route=route,
        ledger=[dict(id=a['id'], outcome=a['outcome'], finished_at=a['finished_at'], usage=a['usage']) for a in attempts]))
    return dict(binding=binding, job_id=job['id'],
        message='The model finished, but its steps did not pass validation. Prepare corrected steps for this same plan using the saved AI. This permits one read-only request; implementation remains separate.')


def request(engine, identity, body):
    from workbench.project_source import require_current
    # Source checks happen before taking the writer, and again at dispatch/finish.
    with engine.connect() as c:
        row = intake.require(c, intakes, identity)
        for saved in c.execute(select(planning_jobs.c.payload).where(
                planning_jobs.c.intake_id == identity, planning_jobs.c.kind == 'decomposition')).scalars():
            prior = next((v for v in saved.get('correction_commands', [])
                if v['command_id'] == str(body.command_id)), None)
            if prior:
                if prior['binding'] != body.binding:
                    raise HTTPException(409, 'This correction command already has different saved inputs.')
                return
        job = _job(c, row)
        if not job:
            raise HTTPException(409, 'A saved feature plan and failed step preparation are required.')
        try:
            require_current({'resolved_base':job['payload'].get('feature_base')})
        except (ValueError, OSError):
            raise HTTPException(409, 'The saved feature source changed. Review the current requirements before requesting new steps.') from None
    with write_transaction(engine) as c:
        row = intake.require(c, intakes, identity)
        job = _job(c, row)
        if not job:
            raise HTTPException(409, 'The saved plan changed before the correction could be recorded.')
        commands = job['payload'].get('correction_commands', [])
        command = dict(command_id=str(body.command_id), binding=body.binding)
        prior = next((v for v in commands if v['command_id'] == command['command_id']), None)
        if prior:
            if prior['binding'] != body.binding:
                raise HTTPException(409, 'This correction command already has different saved inputs.')
            return
        available = offer(c, row)
        if not available or available['binding'] != body.binding:
            raise HTTPException(409, 'The feature, source, or settled outcome changed. Refresh before correcting steps.')
        route = provider_routes.approved(c, row)
        approved_at = intake.now()
        receipt = dict(**command, approved_at=approved_at,
            expires_at=(datetime.fromisoformat(approved_at)+timedelta(minutes=30)).isoformat(),
            original_binding=digest(row['payload']), proposal_id=job['payload']['proposal_id'],
            scope_revision=job['payload']['scope_revision'], feature_base=job['payload']['feature_base'],
            route=route, generation_requests=1, request_deadline_seconds=120,
            source_writes=False, execution_dispatch=False, publication=False,
            previous_failure=job['message'])
        history = job['payload'].get('correction_history', []) + [dict(
            finished_at=job['finished_at'], attempts=job['attempts'], message=job['message'],
            model=job['model'], diagnostics=job['payload'].get('diagnostics'),
            response_evidence_id=job['payload'].get('response_evidence_id'),
            previous_permission=job['payload'].get('feature_recovery'))]
        payload = {k:v for k,v in job['payload'].items()
            if k not in {'feature_generation_claim','diagnostics','response_evidence_id','provider_switch'}}
        payload.update(feature_recovery=receipt, correction_history=history,
            correction_commands=commands+[command])
        c.execute(planning_jobs.update().where(planning_jobs.c.id == job['id']).values(
            status='queued', finished_at=None, payload=payload,
            message='Corrected feature steps requested. The saved plan and earlier failed attempt are retained.'))
        c.execute(intakes.update().where(intakes.c.id == identity).values(status='completed'))


def validate(c, row, job):
    receipt = job['payload']['feature_recovery']
    if (job['kind'] != 'decomposition' or job['revision'] != row['revision']
            or receipt['original_binding'] != digest(row['payload'])
            or receipt['proposal_id'] != job['payload']['proposal_id']
            or receipt['feature_base'] != job['payload']['feature_base']
            or receipt['scope_revision'] != intake.human_scope(c, row['id'])['revision']
            or receipt['route'] != provider_routes.approved(c, row)
            or receipt['generation_requests'] != 1 or receipt['request_deadline_seconds'] != 120
            or any(receipt[k] is not False for k in ('source_writes','execution_dispatch','publication'))
            or datetime.now(timezone.utc)+timedelta(seconds=120) >= datetime.fromisoformat(receipt['expires_at'])
            or job['payload'].get('feature_generation_claim')):
        raise ValueError('The separate feature correction permission expired, was consumed, or changed.')
