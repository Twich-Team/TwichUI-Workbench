"""Human scope changes are local review operations, not model generations.

Keep the model's proposal, source evidence and write limits. Bind the human's
classification/explanation to a new review, without claiming the model considered
new instructions or granting execution. Historical broken transitions are only
recoverable when their exact pre-dispatch provenance can be established.
"""
from copy import deepcopy
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select

from workbench.db import (intakes, intake_scopes, planning_jobs, proposals,
                          proposal_decisions, backlog_items, write_transaction)
from workbench.investigation_start import records
from workbench.visual_scope import digest

EARLY_REJECTION = ('The approved planning operation has already been claimed. '
                   'Start a new task with updated details. Your original request and any example are saved.')


def immutable_job(job):
    # Existing preparation diagnostics can be appended after plan approval. They
    # are not a new source/contract, model receipt, or review authority.
    return {**job, 'payload':{k:v for k,v in job['payload'].items() if k != 'behavioral_preparation_outcome'}}


def prepared_job(c, proposal):
    row = c.execute(select(planning_jobs).where(planning_jobs.c.intake_id == proposal['intake_id'],
        planning_jobs.c.kind == 'planning', planning_jobs.c.revision == proposal['revision'],
        planning_jobs.c.status == 'completed')).mappings().first()
    return dict(row) if row and row['payload'].get('prepared_proposal_id') == proposal['id'] else None


def derive(c, source, scope):
    """Only rebind reviewed requirements; never generate or alter the repair plan."""
    from workbench.schemas import RegressionTaskContract
    contract = RegressionTaskContract.model_validate(source['payload']['task_contract']).model_dump(mode='json')
    if scope['stage'] != 'implementation':
        raise ValueError('Research scope cannot carry an implementation contract')
    if intent := contract.get('bug_intent'):
        from workbench import bug_intent
        original = c.execute(select(intakes.c.payload).where(intakes.c.id == source['intake_id'])).scalar_one()
        packet = source['payload']['bug_inspection_packet']
        bug_intent.validate_review(intent, original, contract['review_feedback'], packet)
        # A human explanation is an additional bound constraint, not a new model
        # finding. The unchanged plan is visibly offered for genuine review.
        feedback = contract['review_feedback'] + '\nHuman scope constraint: ' + scope['note']
        contract['review_feedback'] = feedback
        contract['bug_intent'] = bug_intent.bind(intent['plan'], packet, original, feedback).model_dump(mode='json')
    return contract


def local_payload(c, source, parent, scope, proposal_id, recovery=None):
    meta = dict(version=1, source_job=source['id'], source_job_sha256=digest(immutable_job(source)),
        source_proposal=parent['id'], source_proposal_sha256=digest(parent),
        scope_command=scope['command_id'], scope_sha256=digest(scope))
    if recovery:
        meta['recovery'] = recovery
    payload = dict(operation_kind='scope_binding', scope_binding=meta, scope_revision=scope['revision'],
        prepared_proposal_id=proposal_id, task_contract=derive(c, source, scope), basis='preparation',
        evidence=deepcopy(source['payload'].get('evidence', [])),
        result=deepcopy(source['payload']['result']))
    if packet := source['payload'].get('bug_inspection_packet'):
        payload['bug_inspection_packet'] = deepcopy(packet)
    return payload


def complete(c, source, parent, scope, proposal_id, revision, recovery=None):
    from workbench.service import now
    payload = local_payload(c, source, parent, scope, proposal_id, recovery)
    at = now()
    c.execute(planning_jobs.insert().values(id=str(uuid4()), intake_id=parent['intake_id'], kind='planning',
        revision=revision, status='completed', attempts=0, lease=None, lease_until=None,
        payload=payload, message='Saved scope applied to the existing plan. Review this revision; no model request or addon change was made.',
        model=None, created_at=at, finished_at=at))


def _one(c, query, args):
    found = records(c, query, args)
    return found[0] if len(found) == 1 else None


def legacy_failure(c, job):
    """Positive producer/lineage evidence for the old scope-only preflight reject.

    Missing process/receipt alone never suffices. The exact input was emitted by
    the old scope handler, and the retained error is thrown before the atomic
    generation claim. No generic zero-attempt or unknown-output exception exists.
    """
    payload = job['payload']
    if not (job['kind'] == 'planning' and job['status'] == 'failed' and job['finished_at']
            and job['attempts'] == 0 and not job['lease'] and not job['lease_until'] and not job['model']
            and job['message'] == EARLY_REJECTION and set(payload) == {'feedback','scope_revision','diagnostics'}
            and payload['diagnostics'] == {'category':'planning_validation'}):
        return None
    if records(c, 'SELECT id FROM model_attempts WHERE job_id=?', (job['id'],)):
        return None
    scope = _one(c, 'SELECT * FROM intake_scopes WHERE intake_id=? AND revision=?',
                 (job['intake_id'], payload['scope_revision']))
    if not scope or scope['origin'] != 'human' or scope['stage'] != 'implementation':
        return None
    change = scope['change_payload']
    # records() intentionally decodes general journal payloads only.
    if isinstance(change, str):
        import json
        change = json.loads(change)
        scope['change_payload'] = change
    if not (isinstance(change, dict) and change.get('command_id') == scope['command_id']
            and change.get('note') == payload['feedback'] == scope['note']
            and change.get('visual_requirement') == scope['visual_requirement']
            and change.get('stage') == scope['stage']):
        return None
    parent = _one(c, 'SELECT * FROM proposals WHERE id=? AND intake_id=?',
                  (change.get('proposal_id'), job['intake_id']))
    if parent and isinstance(parent['content'], str):
        import json
        parent['content'] = json.loads(parent['content'])
    orphan = _one(c, 'SELECT * FROM proposals WHERE intake_id=? AND revision=?',
                  (job['intake_id'], job['revision']-1))
    if orphan and isinstance(orphan['content'], str):
        import json
        orphan['content'] = json.loads(orphan['content'])
    if not (parent and orphan and parent['basis'] == orphan['basis'] == 'preparation'
            and parent['revision']+1 == orphan['revision']
            and orphan['scope_revision'] == scope['revision']
            and orphan['content'] == parent['content'] and orphan['model'] == parent['model']
            and scope['created_at'] <= orphan['created_at'] <= job['created_at'] <= job['finished_at']):
        return None
    source = _one(c, "SELECT * FROM planning_jobs WHERE intake_id=? AND revision=? AND kind='planning' AND status='completed'",
                  (job['intake_id'], parent['revision']))
    if not (source and source['payload'].get('prepared_proposal_id') == parent['id']
            and source['payload'].get('task_contract') and source['payload'].get('scenario_generation_claim')):
        return None
    return dict(kind='legacy_scope_preflight', job=job['id'], input_binding=digest(job),
        source_job=source['id'], scope_command=scope['command_id'],
        explained_revisions=[orphan['revision'], job['revision']],
        provider_state='not_dispatched', execution_authorized=False)


def local_operation(c, job, seen=None):
    """Verify a local operation's derivation rather than trusting a payload label."""
    if job['payload'].get('operation_kind') != 'scope_binding':
        marker = job['payload'].get('preflight_rejection')
        if marker:
            unsigned = {k:v for k,v in job['payload'].items() if k not in {'diagnostics','preflight_rejection'}}
            if (marker != dict(version=1, provider_dispatch='not_started', input_binding=digest(unsigned))
                    or job['kind'] != 'planning' or job['status'] != 'failed' or not job['finished_at']
                    or job['attempts'] != 0 or any(job[k] for k in ('lease','lease_until','model'))
                    or job['payload'].get('scenario_generation_claim')
                    or records(c, 'SELECT id FROM model_attempts WHERE job_id=?', (job['id'],))):
                raise ValueError('Planning preflight rejection provenance changed')
            return dict(kind='planning_preflight', job=job['id'], input_binding=digest(job),
                explained_revisions=[job['revision']], provider_state='not_dispatched', execution_authorized=False)
        return legacy_failure(c, job)
    seen = set() if seen is None else seen
    if job['id'] in seen or len(seen) > 128:
        raise ValueError('Scope binding lineage is cyclic or too deep')
    seen.add(job['id'])
    meta = job['payload']['scope_binding']
    source = _one(c, 'SELECT * FROM planning_jobs WHERE id=?', (meta['source_job'],))
    parent = _one(c, 'SELECT * FROM proposals WHERE id=?', (meta['source_proposal'],))
    scope = _one(c, 'SELECT * FROM intake_scopes WHERE command_id=?', (meta['scope_command'],))
    target = _one(c, 'SELECT * FROM proposals WHERE id=?', (job['payload']['prepared_proposal_id'],))
    import json
    for row, key in ((parent,'content'), (scope,'change_payload'), (target,'content')):
        if row and isinstance(row.get(key), str):
            row[key] = json.loads(row[key])
    if not (source and parent and scope and target and source['intake_id'] == parent['intake_id'] ==
            scope['intake_id'] == target['intake_id'] == job['intake_id']
            and source['status'] == 'completed' and source['revision'] == parent['revision'] < job['revision']
            and source['payload'].get('prepared_proposal_id') == parent['id']
            and job['status'] == 'completed' and job['finished_at'] and job['attempts'] == 0
            and not any(job[k] for k in ('lease','lease_until','model'))
            and not records(c, 'SELECT id FROM model_attempts WHERE job_id=?', (job['id'],))
            and target['revision'] == job['revision'] and target['scope_revision'] == scope['revision']
            and target['basis'] == parent['basis'] == 'preparation'
            and target['content'] == parent['content'] and target['model'] == parent['model']
            and scope['origin'] == 'human' and scope['change_payload'].get('proposal_id') == parent['id']):
        raise ValueError('Local scope-binding provenance changed')
    if source['payload'].get('operation_kind') == 'scope_binding':
        local_operation(c, source, seen)
    recovery = meta.get('recovery')
    if recovery:
        failed = _one(c, 'SELECT * FROM planning_jobs WHERE id=?', (recovery['failed_job'],))
        if not failed or not legacy_failure(c, failed) or digest(failed) != recovery['failed_sha256']:
            raise ValueError('Retained scope preflight evidence changed')
    if immutable_job(job)['payload'] != local_payload(c, source, parent, scope, target['id'], recovery):
        raise ValueError('Local scope-binding contract or source changed')
    return dict(kind='scope_binding', job=job['id'], input_binding=digest(job),
        source_job=source['id'], scope_command=scope['command_id'], explained_revisions=[job['revision']],
        provider_state='not_dispatched', execution_authorized=False)


def offer(c, identity):
    """Offer repair of the saved *scope transition*, never a model replay/approval."""
    row = c.execute(select(intakes).where(intakes.c.id == identity)).mappings().one()
    if row['status'] != 'failed':
        return None
    job = c.execute(select(planning_jobs).where(planning_jobs.c.intake_id == identity,
        planning_jobs.c.kind == 'planning').order_by(planning_jobs.c.revision.desc())).mappings().first()
    if not job or not legacy_failure(c, dict(job)):
        return None
    scope = c.execute(select(intake_scopes).where(intake_scopes.c.intake_id == identity)
        .order_by(intake_scopes.c.revision.desc()).limit(1)).mappings().one()
    parent = c.execute(select(proposals).where(proposals.c.id == scope['change_payload']['proposal_id'])).mappings().one()
    current = c.execute(select(proposals).where(proposals.c.intake_id == identity,
        proposals.c.revision == row['revision'])).mappings().first()
    if not current or current['scope_revision'] != scope['revision'] or row['revision']+1 != job['revision']:
        return None
    if c.execute(select(backlog_items.c.id).where(backlog_items.c.intake_id == identity)).first():
        return None
    if c.execute(select(proposal_decisions.c.id).where(proposal_decisions.c.proposal_id == current['id'])).first():
        return None
    from workbench import delivery_phase, everyday
    snapshot = delivery_phase.observe(c, identity, everyday.configuration(c.engine).evidence_roots)
    if not delivery_phase.renewable(snapshot):
        return None
    source = prepared_job(c, parent)
    if not source:
        return None
    contract = derive(c, source, scope)
    from workbench.project_source import require_current
    require_current(contract)
    return dict(binding=digest(dict(intake=dict(row), job=dict(job), scope=dict(scope),
        proposal=dict(current), parent=dict(parent), contract=contract, phases=snapshot)),
        proposal_id=current['id'], scope_revision=scope['revision'], note=scope['note'])


def recover(engine, identity, body):
    with write_transaction(engine) as c:
        request = body.model_dump(mode='json')
        for payload in c.execute(select(planning_jobs.c.payload).where(planning_jobs.c.intake_id == identity)).scalars():
            saved = payload.get('scope_binding', {}).get('recovery', {}).get('request')
            if saved and saved['command_id'] == request['command_id']:
                if saved != request:
                    raise HTTPException(409, 'This recovery command already belongs to another choice.')
                return
        try:
            offered = offer(c, identity)
        except (ValueError, OSError):
            offered = None
        if not offered or offered['binding'] != body.binding:
            raise HTTPException(409, 'The saved scope or earlier activity changed. Refresh this task before applying it.')
        scope = c.execute(select(intake_scopes).where(intake_scopes.c.intake_id == identity,
            intake_scopes.c.revision == offered['scope_revision'])).mappings().one()
        parent = dict(c.execute(select(proposals).where(proposals.c.id == scope['change_payload']['proposal_id'])).mappings().one())
        source = prepared_job(c, parent)
        failed = dict(c.execute(select(planning_jobs).where(planning_jobs.c.intake_id == identity,
            planning_jobs.c.kind == 'planning').order_by(planning_jobs.c.revision.desc()).limit(1)).mappings().one())
        revision = failed['revision']+1
        proposal_id = str(uuid4())
        from workbench.service import now
        c.execute(proposals.insert().values(**{k:parent[k] for k in ('intake_id','content','model','basis')},
            id=proposal_id, revision=revision, scope_revision=scope['revision'], created_at=now()))
        complete(c, source, parent, dict(scope), proposal_id, revision,
            dict(request=request, failed_job=failed['id'], failed_sha256=digest(failed)))
        c.execute(intakes.update().where(intakes.c.id == identity).values(status='completed', revision=revision))
