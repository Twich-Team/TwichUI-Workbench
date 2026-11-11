"""Atomic organization of canonical requests in the existing application store.

Never cancels work, changes approval bindings, deletes evidence, or grants work.
Only an archived never-started idea can lose its original input/scope. A minimal
ID/hash tombstone and command receipt prevent lost-response replay/resurrection.
"""
from typing import Annotated, Literal
from uuid import UUID
from hashlib import sha256

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy import inspect, or_, select

from workbench import db
from workbench.service import now
from workbench.visual_scope import digest


class CleanupTarget(BaseModel):
    model_config = ConfigDict(extra='forbid')
    intake_id: UUID
    binding: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]


class CleanupCommand(BaseModel):
    model_config = ConfigDict(extra='forbid')
    command_id: UUID
    action: Literal['archive', 'restore', 'delete']
    targets: Annotated[list[CleanupTarget], Field(min_length=1, max_length=50)]
    confirm_delete: StrictBool = False


class CleanupContext(BaseModel):
    intake_id: str
    title: str
    binding: str
    revision: int
    archived_at: str | None
    member_count: int
    can_archive: bool
    can_restore: bool
    can_delete: bool
    archive_reasons: list[str]
    delete_reasons: list[str]
    deletion_explanation: str
    retention_explanation: str


class CleanupResult(BaseModel):
    command_id: str
    action: Literal['archive', 'restore', 'delete']
    intake_ids: list[str]
    message: str
    revisions: dict[str, int]


def available(c):
    return inspect(c).has_table('intake_lifecycle')


def lifecycle(c):
    return {r['intake_id']: dict(r) for r in c.execute(select(db.intake_lifecycle)).mappings()} if available(c) else {}


def group(c, identity):
    from workbench.task_identity import links
    ownership = links(c)
    root = next((r['canonical_id'] for r in ownership if r['intake_id'] == identity), identity)
    members = {r['intake_id'] for r in ownership if r['canonical_id'] == root} or {root}
    return root, members, ownership


def require_current(c, identity):
    root, _, _ = group(c, identity)
    state = lifecycle(c).get(root)
    if state and state['deleted_at']:
        raise HTTPException(409, 'This request was permanently deleted. Create a new request with a new ID.')
    if state and (state['archived_at'] or state['deleted_at']):
        raise HTTPException(409, 'This task is archived. Restore it from Backlog before changing or starting work.')


def _rows(c, table, condition):
    return [dict(r) for r in c.execute(select(table).where(condition)).mappings()]


def _snapshot(c, identity):
    root, members, ownership = group(c, identity)
    originals = _rows(c, db.intakes, db.intakes.c.id.in_(members))
    if not originals or len(originals) != len(members):
        raise HTTPException(404, 'This saved task was not found. Refresh Backlog.')
    data = {'intakes': originals, 'lineage': [r for r in ownership if r['canonical_id'] == root]}
    for table in (db.planning_jobs, db.proposals, db.proposal_decisions, db.backlog_items, db.intake_scopes):
        data[table.name] = _rows(c, table, table.c.intake_id.in_(members))
    tasks = {r['id'] for r in data['backlog_items']}
    plans = {r['id'] for r in data['proposals']}
    jobs = {r['id'] for r in data['planning_jobs']}
    data['dependencies'] = _rows(c, db.backlog_dependencies, or_(
        db.backlog_dependencies.c.item_id.in_(tasks), db.backlog_dependencies.c.requires_id.in_(tasks)))
    data['external_children'] = _rows(c, db.backlog_items,
        db.backlog_items.c.parent.in_(tasks) & ~db.backlog_items.c.id.in_(tasks))
    for table in (db.admission_requests, db.implementation_requests, db.candidate_verification_requests,
                  db.pr_preparations, db.task_sessions, db.real_publications):
        data[table.name] = _rows(c, table, table.c.task_id.in_(tasks))
    # Frozen workflow links, not title matching, connect execution authority.
    data['grants'] = [dict(r) for r in c.execute(select(db.real_execution_grants)).mappings()
                      if (r['snapshot'].get('workflow_task') or {}).get('intake_id') in members
                      or ((r['snapshot'].get('workflow_task') or {}).get('task') or {}).get('id') in tasks]
    data['executions'] = _rows(c, db.real_execution_requests,
        db.real_execution_requests.c.grant_id.in_([r['id'] for r in data['grants']]))
    executions = {r['id'] for r in data['executions']}
    data['attempts'] = _rows(c, db.model_attempts, or_(db.model_attempts.c.job_id.in_(jobs),
        db.model_attempts.c.task_id.in_(members | tasks), db.model_attempts.c.execution_id.in_(executions)))
    data['offers'] = _rows(c, db.task_run_offers, db.task_run_offers.c.proposal_id.in_(plans))
    # Blob contents remain private and are bound by hash, never sent to the client.
    data['artifacts'] = [{**r, 'data': sha256(r['data']).hexdigest()} for r in
                         _rows(c, db.proposal_artifacts, db.proposal_artifacts.c.proposal_id.in_(plans))]
    data['lifecycle'] = lifecycle(c).get(root)
    return root, members, data


def _context(c, identity):
    root, members, data = _snapshot(c, identity)
    state = data['lifecycle'] or {}
    archived = bool(state.get('archived_at'))
    reasons = []
    if any(r['status'] in {'queued', 'running'} for r in data['planning_jobs']):
        reasons.append('Work is queued or running. Let it finish or stop it safely first.')
    if any(r['status'] == 'pending' for r in data['proposal_decisions']):
        reasons.append('A saved approval or decision is waiting to apply.')
    terminal = {'completed', 'admitted', 'blocked', 'stale', 'failed', 'cancelled', 'prepared', 'rejected', 'held'}
    for name in ('admission_requests', 'implementation_requests', 'candidate_verification_requests',
                 'pr_preparations', 'task_sessions'):
        if any(r['status'] not in terminal for r in data[name]):
            reasons.append('Preparation, checking or recovery is still pending.')
            break
    if any(r['status'] not in {'checked', 'completed', 'failed', 'blocked', 'cancelled'} for r in data['executions']):
        reasons.append('An execution still needs to finish or settle safely.')
    if any(not r['finished_at'] or r['outcome'] == 'interrupted_unknown' for r in data['attempts']):
        reasons.append('An AI operation has unfinished or uncertain recovery evidence.')
    if any(r['status'] not in {'published', 'failed', 'blocked', 'held', 'rejected'} for r in data['real_publications']):
        reasons.append('Draft publication is pending or its outcome needs checking.')
    used = {r['grant_id'] for r in data['executions']}
    if any(r['id'] not in used and not r['revoked_at'] and r['expires_at'] > now() for r in data['grants']):
        reasons.append('Unused execution permission is still valid. Let it expire or revoke it first.')
    tasks = {r['id'] for r in data['backlog_items']}
    if (data['external_children'] or any(r['parent'] and r['parent'] not in tasks for r in data['backlog_items'])
            or any(r['item_id'] not in tasks or r['requires_id'] not in tasks for r in data['dependencies'])):
        reasons.append('Another request depends on this work. Keep related work together.')
    # Only an untouched, never-executed saved idea has no audit/recovery evidence.
    original = next(r for r in data['intakes'] if r['id'] == root)
    initial = data['intake_scopes']
    untouched = (len(members) == 1 and original['status'] == 'unavailable' and original['revision'] == 0
                 and original['payload'].get('requested_work') and len(initial) == 1
                 and initial[0]['revision'] == 1 and initial[0]['command_id'] == root
                 and initial[0]['change_payload'] == original['payload'])
    evidence = any(data[key] for key in ('lineage', 'planning_jobs', 'proposals', 'proposal_decisions',
        'backlog_items', 'dependencies', 'external_children', 'grants', 'executions', 'attempts', 'offers', 'artifacts',
        'admission_requests', 'implementation_requests', 'candidate_verification_requests', 'pr_preparations',
        'task_sessions', 'real_publications'))
    deletion = []
    if not archived:
        deletion.append('Archive this task before considering permanent deletion.')
    if not untouched or evidence:
        deletion.append('This request has work, decisions or history that must be kept. Use Archive instead.')
    deletion.extend(reasons)
    return dict(intake_id=root, title=data['lineage'][0]['title'] if data['lineage'] else original['payload']['title'],
        binding=digest(data), revision=state.get('revision', 0), archived_at=state.get('archived_at'),
        member_count=len(members), can_archive=not archived and not reasons, can_restore=archived,
        can_delete=archived and not deletion, archive_reasons=reasons, delete_reasons=deletion,
        deletion_explanation='Permanently removes this unused request, its description and original task choices from this workspace. '
            'It cannot be undone. Existing recovery backups are not erased.',
        retention_explanation='A minimal task ID, original-input hash and cleanup receipts remain to prevent duplicate actions or recreating a deleted request.')


def context(engine, identity):
    with engine.connect() as c:
        c.exec_driver_sql('BEGIN')
        if not available(c):
            raise HTTPException(409, 'Task cleanup needs the verified application-store upgrade.')
        return _context(c, identity)


def receipt(engine, identity):
    with engine.connect() as c:
        if not available(c):
            raise HTTPException(409, 'Task cleanup needs the verified application-store upgrade.')
        row = c.execute(select(db.intake_cleanup_receipts).where(db.intake_cleanup_receipts.c.id == identity)).mappings().first()
        if not row:
            raise HTTPException(404, 'No saved cleanup receipt has this ID.')
        return row['result']


def apply(engine, body):
    payload = body.model_dump(mode='json')
    if body.action == 'delete' and (not body.confirm_delete or len(body.targets) != 1):
        raise HTTPException(422, 'Permanent deletion requires explicit confirmation of one unused archived request.')
    with db.write_transaction(engine) as c:
        if not available(c):
            raise HTTPException(409, 'Task cleanup needs the verified application-store upgrade.')
        old = c.execute(select(db.intake_cleanup_receipts).where(db.intake_cleanup_receipts.c.id == str(body.command_id))).mappings().first()
        if old:
            if old['payload'] != payload:
                raise HTTPException(409, 'This cleanup command ID already belongs to another action.')
            return old['result']
        contexts = [_context(c, str(target.intake_id)) for target in body.targets]
        if len({r['intake_id'] for r in contexts}) != len(contexts):
            raise HTTPException(422, 'Select each request only once, including its linked history.')
        for target, current in zip(body.targets, contexts, strict=True):
            if current['binding'] != target.binding:
                raise HTTPException(409, 'This task changed. Refresh the cleanup review before trying again.')
            if not current['can_' + body.action]:
                reasons = current['delete_reasons'] if body.action == 'delete' else current['archive_reasons']
                raise HTTPException(409, ' '.join(reasons) or 'This task is already in that view. Refresh Backlog.')
        at = now()
        for current in contexts:
            root = current['intake_id']
            original = c.execute(select(db.intakes.c.payload).where(db.intakes.c.id == root)).scalar_one()
            values = dict(revision=current['revision'] + 1, archived_at=at if body.action == 'archive' else
                          current['archived_at'] if body.action == 'delete' else None,
                          deleted_at=at if body.action == 'delete' else None, original_sha256=digest(original))
            if current['revision']:
                c.execute(db.intake_lifecycle.update().where(db.intake_lifecycle.c.intake_id == root).values(**values))
            else:
                c.execute(db.intake_lifecycle.insert().values(intake_id=root, **values))
            if body.action == 'delete':
                c.execute(db.intake_scopes.delete().where(db.intake_scopes.c.intake_id == root))
                c.execute(db.intakes.delete().where(db.intakes.c.id == root))
        result = dict(command_id=str(body.command_id), action=body.action, intake_ids=[r['intake_id'] for r in contexts],
            revisions={r['intake_id']: r['revision'] + 1 for r in contexts},
            message={'archive': 'Task archived. Its request, history and results are preserved.',
                     'restore': 'Task restored. Existing work permissions are unchanged.',
                     'delete': 'Unused request permanently deleted. This cannot be undone.'}[body.action])
        c.execute(db.intake_cleanup_receipts.insert().values(id=str(body.command_id), payload=payload, result=result, created_at=at))
        return result
