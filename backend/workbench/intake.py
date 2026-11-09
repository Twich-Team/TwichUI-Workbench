"""Authoritative local intake/receipts. Provider work lives in planning_worker."""
import base64
import hashlib
import math
import struct
from datetime import datetime
import zlib
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, func

from workbench.db import (intakes, planning_jobs, proposals, proposal_artifacts, proposal_decisions,
                          backlog_items, backlog_dependencies, intake_scopes, model_attempts, write_transaction)
from workbench.service import now
from workbench.planning_provider import configured_mode
from workbench.planning_transport import scenario_failure_message
from workbench.schemas import IntakeInput
from workbench.evidence_preview import references as evidence_previews
from workbench.visual_scope import (digest as digest, human_scope, scope_history, proposal_review as proposal_view, artifact_rows,
                                    approval_needs_scope_review,
                                    validate_review, task_needs_scope_review, RESEARCH_EXCLUSION)


def require(connection, table, identity):
    row = connection.execute(select(table).where(table.c.id == identity)).mappings().first()
    if row is None:
        raise HTTPException(404, 'This local record was not found.')
    return dict(row)


def enqueue(connection, intake_id, kind, revision, payload=None):
    old = connection.execute(select(planning_jobs.c.id).where(planning_jobs.c.intake_id == intake_id,
        planning_jobs.c.kind == kind, planning_jobs.c.revision == revision)).scalar()
    if old:
        return old
    identity = str(uuid4())
    connection.execute(planning_jobs.insert().values(id=identity, intake_id=intake_id, kind=kind,
        revision=revision, status='queued', attempts=0, lease=None, lease_until=None,
        payload={**(payload or {}), 'scope_revision':human_scope(connection, intake_id)['revision']}, message='Queued for bounded planning.', model=None, created_at=now(), finished_at=None))
    return identity


def submit(engine, body, *, owner_provenance=None):
    from workbench import bug_investigation, everyday

    original = body.model_dump(mode='json')
    if body.requested_work and (body.kind != 'feature' or body.local_planning or body.supported_task or body.regression_tests):
        raise HTTPException(422, 'This kind of work can be saved for later, but cannot authorize a model or repair in this release.')
    if body.supported_task is None:
        original.pop('supported_task', None)
    if body.regression_tests is not None and (body.supported_task or body.kind != 'feature'):
        raise HTTPException(422, 'Regression-test preparation is a feature request separate from a fixed supported task.')
    if body.supported_task:
        from workbench.supported_tasks import load
        if not load(body.supported_task):
            raise HTTPException(409, 'This supported task is unavailable.')
    bug = bug_investigation.requested(original)
    if body.local_planning and ((not body.regression_tests and not bug) or (not bug and body.visual_requirement != 'nonvisual')):
        raise HTTPException(422, 'Model investigation is available for eligible offline bug fixes and test coverage.')
    if body.kind == 'feature' and body.lua_error:
        raise HTTPException(422, 'Lua errors belong to issue reports.')
    identity = str(body.request_id)
    with write_transaction(engine) as connection:
        old = connection.execute(select(intakes).where(intakes.c.id == identity)).mappings().first()
        if old:
            from workbench.investigation_start import first
            initial = first(connection, identity)
            saved = initial['payload'].get('planning_start', {}).get('original_input', old['payload']) if initial else old['payload']
            saved_input=IntakeInput.model_validate(saved).model_dump(mode='json')
            if saved_input.get('supported_task') is None:
                saved_input.pop('supported_task',None)
            if saved_input != original:
                raise HTTPException(409, 'This request ID already belongs to different input.')
        else:
            if connection.scalar(select(func.count()).select_from(planning_jobs).where(planning_jobs.c.status.in_(['queued','running']))) >= 64:
                raise HTTPException(503, 'Planning queue is full. Keep this request and retry after queued work completes.')
            at = now()
            planning = {'owner_provenance': owner_provenance} if owner_provenance is not None else {}
            if body.local_planning:
                if not everyday.enabled():
                    raise HTTPException(409, 'Ordinary local planning is unavailable at this address.')
                try:
                    everyday.configuration(engine)
                    if bug:
                        from workbench.trusted_preparation import configuration as preparation_config
                        if bug_investigation.CATEGORY not in preparation_config(engine)['allowed_classes']:
                            raise ValueError('Bug investigation is not configured')
                except (OSError, ValueError, RuntimeError):
                    raise HTTPException(503, 'The Workbench installation needs attention before saving model approval.') from None
                receipt = everyday.planning_receipt(original, at)
                from workbench import provider_routes
                try:
                    key, binding = provider_routes.planning_binding(connection, body.local_planning)
                    receipt[key] = binding
                except ValueError as error:
                    raise HTTPException(409, str(error)) from None
                planning['local_planning_receipt'] = receipt
            needs_permission = everyday.enabled() and body.kind == 'issue' and not body.local_planning and not body.supported_task
            connection.execute(intakes.insert().values(id=identity, payload=original, status='unavailable' if body.requested_work else 'waiting_for_information' if needs_permission else 'queued',
                revision=0, created_at=at, priority_override=None))
            connection.execute(intake_scopes.insert().values(intake_id=identity, revision=1, command_id=identity,
                visual_requirement=body.visual_requirement, stage='implementation', origin='human',
                note='Recorded with the original request.', change_payload=original, created_at=now()))
            if not body.requested_work and not needs_permission:
                enqueue(connection, identity, 'planning', 1, planning)
    return view(engine, identity)




def priority_recommendation(kind, verified=False, severity='unknown', age_days=0, dependencies=0):
    if verified and severity in {'data_loss', 'serious_regression'}:
        rank, reason = 1, 'Serious independently verified bug or regression.'
    elif kind == 'issue':
        rank, reason = 2, 'Verified bug triage.' if verified else 'Timely triage for an unconfirmed report; serious verified regressions take precedence.'
    elif age_days >= 14:
        rank, reason = 3, 'Aged feature promoted after 14 days; dependencies remain prerequisites.'
    else:
        rank, reason = 4, 'Ordinary feature planning; verified bugs and triage take precedence.'
    if dependencies:
        reason += f' {dependencies} dependency prerequisite(s) remain separate from priority.'
    return rank, reason


def priority(row, dependencies=0):
    age = max(0, (datetime.fromisoformat(now()) - datetime.fromisoformat(row['created_at'])).days)
    # Intake never sets verified=True; independent verification belongs to a later execution slice.
    rank, reason = priority_recommendation(row['payload']['kind'], age_days=age, dependencies=dependencies)
    manual = row['priority_override'] is not None
    return dict(severity='Unconfirmed report' if row['payload']['kind']=='issue' else 'Feature request',
        priority=row['priority_override'] if manual else rank,
        reason='Manual override retained.' if manual else reason,
        execution='Not authorized · implementation and publication remain held', manual=manual)


def pending_counts(engine):
    with engine.connect() as connection:
        connection.exec_driver_sql('BEGIN')
        pending = connection.scalar(select(func.count()).select_from(proposal_decisions).where(proposal_decisions.c.status=='pending'))
        needs = 0
        for row in connection.execute(select(intakes).where(intakes.c.status=='completed')).mappings():
            if row['payload']['kind'] != 'feature':
                continue
            proposal_id = connection.scalar(select(proposals.c.id).where(proposals.c.intake_id==row['id'], proposals.c.revision==row['revision']))
            if proposal_id and not connection.execute(select(proposal_decisions.c.id).where(proposal_decisions.c.proposal_id==proposal_id, proposal_decisions.c.status.in_(['pending','applied']))).first():
                needs += 1
        return needs, pending


def view(engine, identity):
    with engine.connect() as connection:
        connection.exec_driver_sql('BEGIN')
        return view_in(connection, identity)


def retained_planning_failure(row, jobs):
    # Older workers saved failed grounded responses as completed clarification
    # drafts. The claim and missing grounding distinguish those from real questions.
    if row['status'] != 'waiting_for_information':
        return None
    return next((job for job in jobs if job['kind'] == 'planning'
        and job['revision'] == row['revision'] and job['status'] == 'completed'
        and job['payload'].get('scenario_generation_claim')
        and not job['payload'].get('task_contract', {}).get('scenario_grounding')), None)


def retained_source_failure(job):
    diagnostics = job['payload'].get('diagnostics', {})
    if job['kind'] != 'planning' or job['status'] != 'failed':
        return False
    return bool(diagnostics.get('retained_failure') or (
        diagnostics.get('category') == 'schema_validation'
        and 'A loaded client needs one core addon source' in job['message']
    ))


def diagnostics_view(raw):
    """Read-only scalar projection; preserve original private diagnostics.

    Early context-count records used a nested object. Flatten that exact known
    shape without migrating history. Unknown structures remain private and are
    explicitly unavailable, never interpreted as task or execution authority.
    """
    if not isinstance(raw, dict):
        return dict(diagnostics_unavailable=1)
    result = {}
    for key, value in raw.items():
        if key == 'context_budget' and isinstance(value, dict):
            if (set(value) == {'prompt_tokens', 'context_tokens'}
                    and all(type(v) is int and v > 0 for v in value.values())
                    and value['prompt_tokens'] <= value['context_tokens']):
                result.update(context_prompt_tokens=value['prompt_tokens'],
                              context_capacity_tokens=value['context_tokens'])
            else:
                result['diagnostics_unavailable'] = 1
        elif isinstance(value, (str, int, float)) and not (type(value) is float and not math.isfinite(value)):
            result[key] = value
        else:
            result['diagnostics_unavailable'] = 1
    return result


def view_in(connection, identity):
    row = require(connection, intakes, identity)
    jobs = [dict(j) for j in connection.execute(select(planning_jobs).where(
        planning_jobs.c.intake_id == identity).order_by(planning_jobs.c.created_at)).mappings()]
    retained = retained_planning_failure(row, jobs)
    if retained:
        diagnostics = retained['payload'].get('scenario_generation', {}).get('diagnostics', {})
        retained['payload']['diagnostics'] = {
            **{k: v for k, v in diagnostics.items() if k != 'usage'}, 'retained_failure': 1,
        }
        retained['status'] = 'failed'
        retained['message'] = scenario_failure_message(diagnostics.get('category'))
    for job in jobs:
        facts=job['payload'].get('diagnostics',{})
        capabilities=job['payload'].get('recovery_capabilities',{})
        if (job['status']=='failed' and (facts.get('category')=='cancelled' or facts.get('stopped_request_category')=='cancelled')
                and facts.get('generation_state')=='finished' and facts.get('accounting_state')=='complete'
                and all(capabilities.get(k) is False for k in ('source_writes','execution_dispatch','external_action_tools'))):
            job['message']='Read-only investigation stopped. Its completed responses and usage are saved. Your original report remains available, and this step made no addon changes.'
        if (job['status']=='failed' and facts.get('stopped_request_category') in {'input_budget','context_budget'}
                and facts.get('stopped_request_generation_state')=='not_started'):
            job['message']='Workbench stopped preparing a larger source request before sending it. Earlier read-only work and usage remain saved. No repair started; the source-packing problem must be corrected before continuing.'
        if (configured_mode() == 'everyday' and not row['payload'].get('local_planning')
                and job['status'] == 'failed'):
            from workbench.investigation_start import non_dispatch
            attempts = connection.execute(select(model_attempts).where(model_attempts.c.job_id == job['id'])).mappings().all()
            if non_dispatch(job, attempts):
                job['message'] = 'Workbench tried an outdated planning route before investigation permission was recorded. No model was contacted. Start read-only investigation below; your report and earlier failed attempts remain saved.'
        if retained_source_failure(job):
            diagnostics = job['payload']['diagnostics']
            if not diagnostics.get('retained_failure'):
                job['message'] = scenario_failure_message('loaded_client_core_source')
                job['payload']['diagnostics'] = {**diagnostics, 'retained_failure': 1}
        if job['kind'] == 'planning' and job['status'] == 'failed':
            from workbench.investigation_recovery import accounting, path_for
            for attempt_id in connection.execute(select(model_attempts.c.id).where(
                    model_attempts.c.job_id == job['id'], model_attempts.c.outcome.in_(['interrupted_unknown','incomplete_output','failed']))).scalars():
                try:
                    if not path_for(attempt_id).exists():
                        continue
                    disposition=accounting(connection, attempt_id)
                except (ValueError,OSError,KeyError,TypeError):
                    continue
                # Projection only: historical result/accounting remain immutable.
                job['message'] = ('This read-only investigation was quarantined. '+
                    ('Final provider outcome and usage remain unknown. ' if disposition['final_outcome']=='unknown' else
                     'Completed provider receipts and usage were recovered separately from the historical record. ' if disposition['final_outcome']=='completed' and disposition.get('final_usage') is not None else
                     'The provider confirmed interruption and reported usage; its partial output remains held. ' if disposition.get('final_usage') is not None else
                     'The provider confirmed its final state; final usage remains unavailable. ')+
                    'Its output cannot be used. Start a fresh task with fresh planning consent.')
                job['payload']['diagnostics'] = {**job['payload'].get('diagnostics',{}), 'read_only_quarantined':1}
    planned = [dict(t) for t in connection.execute(select(backlog_items).where(backlog_items.c.intake_id == identity)).mappings()]
    for item in planned:
        item['scope_review_required'] = task_needs_scope_review(connection, item)
        item['dependencies'] = list(connection.execute(select(backlog_dependencies.c.requires_id).where(
            backlog_dependencies.c.item_id == item['id'])).scalars())
    results = [j for j in jobs if j['kind'] == 'planning' and j['status'] == 'completed']
    activity = []
    if row['payload'].get('local_planning'):
        activity = connection.execute(select(
            model_attempts.c.id, model_attempts.c.purpose, model_attempts.c.backend,
            model_attempts.c.requested_model, model_attempts.c.actual_model,
            model_attempts.c.outcome, model_attempts.c.routing_reason,
        ).where(model_attempts.c.task_id == identity, model_attempts.c.source == 'workbench',
                model_attempts.c.backend.is_not(None))
            .order_by(model_attempts.c.started_at.desc(), model_attempts.c.id)).mappings().all()
    from workbench.investigation_start import resume_binding
    latest = max((j for j in jobs if j['kind']=='planning'),key=lambda j:j['revision'],default=None)
    return dict(planning_resume_binding=resume_binding(connection,row,latest) if latest else None,
        planning_start_binding=digest(row['payload']) if configured_mode()=='everyday' and row['payload']['kind']=='issue' and not row['payload'].get('local_planning') and not row['payload'].get('supported_task') and row['status'] in {'failed','waiting_for_information'} else None,
        provider_activity=[dict(attempt) for attempt in activity], human_scope=human_scope(connection, identity), scope_history=scope_history(connection, identity), id=identity, original=row['payload'], status='failed' if retained else row['status'], revision=row['revision'],
        created_at=row['created_at'], clarifications=[dict(text=j['payload']['feedback'], created_at=j['created_at'], revision=j['revision']) for j in jobs if j['payload'].get('clarification_id')], jobs=[dict({k:v for k,v in j.items() if k not in {'lease','lease_until','payload','intake_id'}}, diagnostics=diagnostics_view(j['payload'].get('diagnostics', {}))) for j in jobs],
        proposals=[proposal_view(connection, dict(p)) for p in connection.execute(select(proposals).where(
            proposals.c.intake_id == identity).order_by(proposals.c.revision.desc())).mappings()
            if not retained or p['revision'] != retained['revision']],
        decisions=[dict({k:v for k,v in d.items() if k not in {'snapshot','intake_id'}},
            scope_review_required=approval_needs_scope_review(connection, d)) for d in connection.execute(select(proposal_decisions).where(
            proposal_decisions.c.intake_id == identity).order_by(proposal_decisions.c.created_at.desc())).mappings()],
        result=results[-1]['payload'].get('result') if results and not retained else None,
        evidence=evidence_previews(next((j['payload']['evidence'] for j in reversed(jobs) if j['kind']=='planning' and 'evidence' in j['payload']), [])), tasks=[{k:v for k,v in t.items() if k not in {'intake_id','created_at'}} for t in planned], priority=priority(row, sum(len(t['dependencies']) for t in planned)))


def listing(engine, page):
    with engine.connect() as connection:
        connection.exec_driver_sql('BEGIN')
        ids = connection.execute(select(intakes.c.id).order_by(intakes.c.created_at.desc()).offset((page-1)*12).limit(12)).scalars()
        return dict(items=[view_in(connection, i) for i in ids], total=connection.scalar(select(func.count()).select_from(intakes)),
                    page=page, planner=configured_mode())


def current(connection, identity, proposal_id, binding):
    row = require(connection, intakes, identity)
    proposal = require(connection, proposals, proposal_id)
    if proposal['intake_id'] != identity or proposal['revision'] != row['revision'] or proposal_view(connection, proposal)['binding'] != binding:
        raise HTTPException(409, 'This proposal or artifact set changed. Open the current revision before deciding.')
    return row, proposal


def receipt_view(row):
    return {k: v for k, v in row.items() if k not in {'snapshot', 'intake_id'}}


def decide(engine, identity, body):
    payload = body.model_dump(mode='json')
    if body.run_budget is None:
        payload.pop('run_budget', None)
    with write_transaction(engine) as connection:
        old = connection.execute(select(proposal_decisions).where(proposal_decisions.c.id == str(body.command_id))).mappings().first()
        if old:
            if old['intake_id'] != identity or {**{'scope_revision':None}, **old['payload']} != payload:
                raise HTTPException(409, 'This command ID already belongs to another decision.')
            return {k:v for k,v in old.items() if k not in {'snapshot','intake_id'}}
        row, proposal = current(connection, identity, str(body.proposal_id), body.binding)
        if row['status'] != 'completed':
            raise HTTPException(409, 'This proposal is not currently awaiting a decision.')
        prior = connection.execute(select(proposal_decisions.c.id).where(proposal_decisions.c.proposal_id == proposal['id'],
            (proposal_decisions.c.binding == body.binding) | (proposal_decisions.c.status == 'pending'))).first()
        if prior:
            raise HTTPException(409, 'A decision is already saved for this proposal. Refresh its receipt.')
        if body.action == 'changes' and not body.note.strip():
            raise HTTPException(422, 'Explain the changes you want.')
        review = proposal_view(connection, proposal)
        if body.scope_revision != review['human_scope']['revision']:
            raise HTTPException(409, 'Human scope changed or was not bound to this decision. Review the current scope.')
        if body.run_budget is not None:
            if body.action != 'approve':
                raise HTTPException(422, 'A run budget requires explicit approval.')
            from workbench.task_budget import validate_selection
            validate_selection(connection, proposal['id'], body.run_budget)
        if body.action == 'approve':
            validate_review(connection, proposal, review, body.scope_revision)
            from workbench.bug_evidence import validate_expectation_review
            validate_expectation_review(review, payload)
        receipt = dict(id=str(body.command_id), intake_id=identity, proposal_id=proposal['id'], binding=body.binding,
            scope_revision=body.scope_revision, required_artifacts=review['required_artifacts'], scope_review_required=0,
            payload=payload, snapshot=review, status='pending',
            message=('Scope and one task budget saved · Waiting for application and certified runtime.' if body.run_budget else 'Decision saved · Pending application. Scope approval grants no execution permission.'), created_at=now(), finished_at=None)
        connection.execute(proposal_decisions.insert().values(**receipt))
    return {k:v for k,v in receipt.items() if k not in {'snapshot','intake_id'}}


def validate_artifact(body):
    try:
        data = base64.b64decode(body.data_base64, validate=True)
        if not 1 <= len(data) <= 1024*1024:
            raise ValueError()
        if body.media_type == 'text/plain':
            text = data.decode('utf-8')
            if '\x00' in text or body.role != 'evidence':
                raise ValueError()
        else:
            # Strict passive PNG: no SVG/HTML/polyglots, trailing bytes or animation.
            if data[:8] != b'\x89PNG\r\n\x1a\n':
                raise ValueError()
            offset, kinds, compressed = 8, [], bytearray()
            width = height = depth = color = 0
            while offset < len(data):
                size = struct.unpack('>I', data[offset:offset+4])[0]
                kind = data[offset+4:offset+8]
                chunk = data[offset+8:offset+8+size]
                checksum = struct.unpack('>I', data[offset+8+size:offset+12+size])[0]
                if size > 1024*1024 or zlib.crc32(kind+chunk) & 0xffffffff != checksum or kind in {b'acTL', b'fcTL', b'fdAT'}:
                    raise ValueError()
                if len(kinds) >= 512 or (kinds and kind == b'IHDR'):
                    raise ValueError()
                if kind == b'IDAT':
                    compressed.extend(chunk)
                if not kinds:
                    if kind != b'IHDR' or size != 13:
                        raise ValueError()
                    width, height, depth, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', chunk)
                    allowed = {0:{1,2,4,8,16}, 2:{8,16}, 3:{1,2,4,8}, 4:{8,16}, 6:{8,16}}
                    if color not in allowed or depth not in allowed[color] or compression or filtering or interlace:
                        raise ValueError()
                    if not 1 <= width <= 4096 or not 1 <= height <= 4096 or width*height > 8000000:
                        raise ValueError()
                kinds.append(kind)
                offset += size+12
                if kind == b'IEND':
                    if size:
                        raise ValueError()
                    break
            if offset != len(data) or kinds[-1] != b'IEND' or b'IDAT' not in kinds:
                raise ValueError()
            channels = {0:1,2:3,3:1,4:2,6:4}[color]
            stride = (width*channels*depth+7)//8+1
            expected = stride*height
            decoder = zlib.decompressobj()
            pixels = decoder.decompress(bytes(compressed), expected+1)
            if len(pixels) != expected or not decoder.eof or decoder.unused_data or any(pixels[i] > 4 for i in range(0,expected,stride)):
                raise ValueError()
    except (ValueError, UnicodeError, struct.error, IndexError, zlib.error):
        raise HTTPException(422, 'Use a valid non-interlaced PNG up to 1 MiB / 8 megapixels, or UTF-8 text evidence up to 1 MiB.') from None
    return data


def attach(engine, identity, body):
    data = validate_artifact(body)
    with write_transaction(engine) as connection:
        old = connection.execute(select(proposal_artifacts).where(proposal_artifacts.c.id == str(body.artifact_id))).mappings().first()
        if old:
            if old['proposal_id'] != str(body.proposal_id) or old['data'] != data or old['label'] != body.label or old['role'] != body.role or old['media_type'] != body.media_type:
                raise HTTPException(409, 'Artifact ID already used.')
            # Still verify request ownership on an identical retry.
            if require(connection, proposals, old['proposal_id'])['intake_id'] != identity:
                raise HTTPException(409, 'Artifact belongs to another request.')
            return
        row, proposal = current(connection, identity, str(body.proposal_id), body.binding)
        if row['status'] != 'completed' or connection.execute(select(proposal_decisions.c.id).where(
            proposal_decisions.c.proposal_id == proposal['id'], proposal_decisions.c.status.in_(['pending','applied']))).first():
            raise HTTPException(409, 'A decided proposal is immutable. Request a revision before changing its artifacts.')
        if len(artifact_rows(connection, proposal['id'])) >= 8:
            raise HTTPException(422, 'This revision already has eight artifacts.')
        connection.execute(proposal_artifacts.insert().values(id=str(body.artifact_id), proposal_id=proposal['id'],
            role=body.role, label=body.label, sha256=hashlib.sha256(data).hexdigest(), media_type=body.media_type,
            data=data, created_at=now()))


def retry(engine, identity, job_id, *, preparation=None):
    with write_transaction(engine) as connection:
        job = require(connection, planning_jobs, str(job_id))
        row = require(connection, intakes, identity)
        if job['intake_id'] != identity:
            raise HTTPException(404, 'Job not found for this request.')
        if preparation and preparation.resume_before_generation:
            from workbench.investigation_start import resume_in
            resume_in(connection,row,job,preparation)
            return
        if job['kind'] == 'behavioral_preparation':
            if preparation and preparation.renew_check_construction:
                from workbench.bug_preparation_retry import request_in
                try:
                    request_in(connection,job,preparation)
                except (ValueError,OSError):
                    raise HTTPException(409,'Check renewal requires current scope, unchanged policy, retained source and settled provider evidence.') from None
                return
            raise HTTPException(409, 'Preparation retains one durable claim. Inspect its outcome or recovery hold; retry cannot launch duplicate preparation.')
        if row['payload'].get('local_planning') and job['kind'] == 'planning':
            raise HTTPException(409, 'This approval covers one planning request. Review the saved outcome or start a new task.')
        if configured_mode() == 'everyday' and job['kind'] == 'planning' and not row['payload'].get('supported_task'):
            raise HTTPException(409, 'Retrying the disabled planner would repeat the same failure. Start read-only investigation on this saved report instead.')
        if retained_source_failure(job):
            raise HTTPException(409, 'This addon planning result is retained. Retry cannot start another generation for this request.')
        if job['kind'] == 'decomposition':
            validate_approval(connection, require(connection, proposal_decisions, job['payload']['approval_id']))
        if job['payload'].get('scope_revision', 0) != human_scope(connection, identity)['revision']:
            raise HTTPException(409, 'This job needs a new scope review before retrying.')
        if job['status'] in {'queued', 'running', 'completed'}:
            return
        if job['attempts'] >= 3 or job['revision'] < row['revision']:
            raise HTTPException(409, 'Retry limit reached or this job is superseded. Retained input and approval remain available.')
        connection.execute(planning_jobs.update().where(planning_jobs.c.id == job['id']).values(status='queued', finished_at=None,
            message='Explicit retry queued. Existing input and approval retained.'))
        connection.execute(intakes.update().where(intakes.c.id == identity).values(status='approved' if job['kind']=='decomposition' else 'queued'))


def clarify(engine, identity, body):
    from workbench import bug_investigation, everyday, provider_routes

    if not body.text.strip():
        raise HTTPException(422, 'Add the requested information.')
    with write_transaction(engine) as connection:
        row = require(connection, intakes, identity)
        if everyday.enabled() and not row['payload'].get('local_planning'):
            raise HTTPException(409, 'Choose read-only investigation on this saved report before sending a follow-up.')
        jobs = list(connection.execute(select(planning_jobs).where(planning_jobs.c.intake_id == identity)).mappings())
        for job in jobs:
            if job['payload'].get('clarification_id') == str(body.command_id):
                if (job['payload'].get('feedback') != body.text
                        or bool(job['payload'].get('bug_followup')) != body.continue_investigation
                        or bool(job['payload'].get('explicit_renewal',job['payload'].get('renew_operation',False))) != body.renew_operation):
                    raise HTTPException(409, 'Clarification ID already used.')
                return
        bug_continuation=(bug_investigation.requested(row['payload']) and row['status']=='failed'
            and any(j['kind']=='planning' and j['revision']==row['revision'] and j['status']=='completed'
                and j['payload'].get('task_contract',{}).get('scope_missing') for j in jobs)
            and not connection.execute(select(backlog_items.c.id).where(backlog_items.c.intake_id==identity)).first()
            and not connection.execute(select(proposal_decisions.c.id).where(proposal_decisions.c.intake_id==identity)).first())
        if (row['status'] != 'waiting_for_information' and not bug_continuation and not (body.renew_operation and row['status']=='failed') or row['revision'] != body.revision
                or retained_planning_failure(row, jobs)):
            raise HTTPException(409, 'The request changed. Refresh before answering.')
        next_input = {'feedback': body.text, 'clarification_id': str(body.command_id)}
        next_sequence=row['revision']+1
        if body.renew_operation:
            next_input.update(renew_operation=True,explicit_renewal=True)
        if bug_investigation.requested(row['payload']):
            if not body.continue_investigation:
                raise HTTPException(422, 'Confirm the follow-up investigation before sending your answer.')
            initial = next((job for job in sorted(jobs,key=lambda j:j['revision']) if job['kind'] == 'planning'
                            and job['payload'].get('local_planning_receipt')
                            and job['status'] in ({'completed','failed'} if body.renew_operation else {'completed'})), None)
            if not initial:
                raise HTTPException(409, 'The original settled investigation is unavailable.')
            grounding = initial['payload'].get('task_contract', {}).get('scenario_grounding', {})
            try:
                from workbench.planning_limits import followup_budget, renewed_budget
                from workbench.trusted_preparation import configuration
                fresh_operation=body.renew_operation or configuration(engine).get('bug_plan_review')=='source-grounded-intent-v1'
                if fresh_operation:
                    next_input.update(renew_operation=True,explicit_renewal=body.renew_operation)
                    if (connection.execute(select(backlog_items.c.id).where(backlog_items.c.intake_id==identity)).first()
                            or connection.execute(select(proposal_decisions.c.id).where(proposal_decisions.c.intake_id==identity)).first()):
                        raise ValueError('An approved or dispatched task cannot be renewed through investigation')
                    continuation=renewed_budget(connection,initial)
                    next_sequence=continuation['next_sequence']
                else:
                    continuation=followup_budget(connection,initial,row['revision'])
                next_input['local_planning_receipt']=initial['payload']['local_planning_receipt']
                route = provider_routes.approved(connection, row)
                next_input['bug_followup'] = dict(
                    binding=digest(dict(original=row['payload'], revision=row['revision'],
                        feedback=body.text, clarification_id=str(body.command_id))),
                    expires_at=everyday.planning_receipt(row['payload'], now())['expires_at'],
                    route=route, provider=grounding.get('basis'),
                    fallback_reason=grounding.get('fallback_reason'),**continuation)
            except ValueError as error:
                raise HTTPException(409, str(error)) from None
        elif body.continue_investigation or body.renew_operation:
            raise HTTPException(422, 'This request has no bug investigation to continue.')
        enqueue(connection, identity, 'planning', next_sequence, next_input)
        connection.execute(intakes.update().where(intakes.c.id == identity).values(status='queued'))


def validate_approval(connection, receipt):
    continuation = receipt['payload'].get('execution_continuation')
    binding = receipt['binding']
    if continuation:
        # A new execution-budget decision references, never rewrites, its original
        # scope decision. Its unique binding includes budget and owner provenance.
        expected = digest(dict(execution_continuation=continuation, run_budget=receipt['payload'].get('run_budget')))
        parent = require(connection, proposal_decisions, continuation['scope_approval_id'])
        if (parent['payload'].get('execution_continuation') or parent['intake_id'] != receipt['intake_id']
                or parent['proposal_id'] != receipt['proposal_id'] or parent['binding'] != continuation.get('scope_binding')
                or expected != binding or receipt['payload'].get('binding') != binding
                or not receipt['payload'].get('run_budget')):
            raise HTTPException(409, 'Execution continuation differs from its original scope and explicit budget.')
        validate_approval(connection, parent)
        binding = parent['binding']
    _, proposal = current(connection, receipt['intake_id'], receipt['proposal_id'], binding)
    review = proposal_view(connection, proposal)
    if (receipt['status'] != 'applied' or receipt['payload']['action'] != 'approve'
            or receipt['scope_review_required'] or receipt['required_artifacts'] is None):
        raise HTTPException(409, 'The approval requires human scope review. No tasks may be created.')
    validate_review(connection, proposal, review, receipt['scope_revision'], receipt['required_artifacts'])
    from workbench.bug_evidence import validate_expectation_review
    if not continuation:
        validate_expectation_review(review, receipt['payload'])
    return proposal


def revise_scope_in(connection, identity, proposal, visual_requirement, stage, note, command_id, payload):
    if not note.strip():
        raise HTTPException(422, 'Explain this scope revision.')
    row = require(connection, intakes, identity)
    scope = human_scope(connection, identity)
    revision = scope['revision']+1
    connection.execute(intake_scopes.insert().values(intake_id=identity, revision=revision,
        command_id=command_id, visual_requirement=visual_requirement, stage=stage, origin='human',
        note=note, change_payload=payload, created_at=now()))
    content = dict(proposal['content'])
    if stage == 'research':
        content.update(delivery='research', summary='Research stage: '+content['summary'][:740],
            behavior=['Investigate feasibility and return a new proposal before implementation.'],
            scope=['Research the original request and prepare evidence: '+note[:650]],
            acceptance=['Document findings and unresolved questions; return for a new implementation proposal review.'],
            exclusions=[RESEARCH_EXCLUSION], stages=['Inspect relevant evidence.', 'Return a new proposal for human review.'])
    connection.execute(proposals.insert().values(id=str(uuid4()), intake_id=identity, revision=row['revision']+1,
        scope_revision=revision, content=content, model=proposal['model'], basis=proposal['basis'], created_at=now()))
    connection.execute(intakes.update().where(intakes.c.id == identity).values(revision=row['revision']+1, status='completed'))
    # Keep historical decisions as made; flag their authority separately. Pending is rejected at application.
    connection.execute(proposal_decisions.update().where(proposal_decisions.c.intake_id == identity,
        proposal_decisions.c.status.in_(['pending','applied'])).values(scope_review_required=1))
    if proposal['basis'] == 'preparation' and stage == 'implementation':
        enqueue(connection, identity, 'planning', row['revision']+2, {'feedback':note})
        connection.execute(intakes.update().where(intakes.c.id == identity).values(status='queued'))


def revise_scope(engine, identity, body):
    payload = body.model_dump(mode='json')
    with write_transaction(engine) as connection:
        old = connection.execute(select(intake_scopes).where(intake_scopes.c.command_id == str(body.command_id))).mappings().first()
        if old:
            if old['intake_id'] != identity or old['change_payload'] != payload:
                raise HTTPException(409, 'Scope command ID already used.')
            return
        _, proposal = current(connection, identity, str(body.proposal_id), body.binding)
        revise_scope_in(connection, identity, proposal, body.visual_requirement, body.stage, body.note, str(body.command_id), payload)


def research_revision(engine, identity, body):
    with write_transaction(engine) as connection:
        row = require(connection, intakes, identity)
        proposal = connection.execute(select(proposals).where(proposals.c.intake_id == identity,
            proposals.c.revision == row['revision'])).mappings().first()
        if not proposal or proposal_view(connection, dict(proposal))['binding'] != body.binding or row['status'] != 'completed':
            raise HTTPException(409, 'Refresh the current undecided proposal before revising.')
        if connection.execute(select(proposal_decisions.c.id).where(proposal_decisions.c.proposal_id == proposal['id'],
            proposal_decisions.c.status == 'pending')).first():
            raise HTTPException(409, 'A saved decision is pending application.')
        scope = human_scope(connection, identity)
        revise_scope_in(connection, identity, dict(proposal), scope['visual_requirement'], 'research',
                        body.explanation, str(uuid4()), body.model_dump(mode='json'))


def set_priority(engine, identity, value):
    with write_transaction(engine) as connection:
        require(connection, intakes, identity)
        connection.execute(intakes.update().where(intakes.c.id == identity).values(priority_override=value))
