"""Explicit continuing permission, separate from every historical one-run grant.

The worker still grants one bounded operation at a time. Only settled, isolated
same-scope work may advance; cancellation, uncertainty or changed consent hold.
No model, public command or configuration can create this owner instruction.
"""
from fastapi import HTTPException
from sqlalchemy import select

from workbench.db import proposal_decisions, task_sessions

PURPOSE = 'same-task-local-until-result-v1'


def permitted_budget(approval):
    payload = approval.get('payload') or {}
    continuation = payload.get('execution_continuation') or {}
    proof = continuation.get('owner_provenance') or {}
    budget = payload.get('run_budget') or {}
    from workbench.visual_scope import digest
    return (proof.get('actor') == 'owner' and proof.get('purpose') == PURPOSE
        and 'browser_request' not in continuation and budget.get('turns', 20) is None
        and budget.get('fallback') is False and budget.get('publication') == 'hold'
        and budget.get('activate_once') is True
        and approval.get('binding') == digest(dict(execution_continuation=continuation, run_budget=budget)))


def allows(runtime):
    linked = getattr(runtime, 'workflow_task', None) or {}
    return permitted_budget(linked.get('approval') or {}) and not getattr(runtime, 'codex_selection', None)


def current(c, task_id):
    """Pure projection; current scope and stop decisions always take precedence."""
    row = c.execute(select(proposal_decisions, task_sessions.c.status.label('session_status'),
        task_sessions.c.state.label('session_state'), task_sessions.c.finished_at.label('session_finished'))
        .join(task_sessions, task_sessions.c.id == proposal_decisions.c.id)
        .where(task_sessions.c.task_id == task_id)
        .order_by(proposal_decisions.c.created_at.desc()).limit(1)).mappings().first()
    if not row or not permitted_budget(row) or row['status'] != 'applied':
        return None
    if row['session_status'] in {'cancelled','expired'} or row['session_state'].get('cancel_requested'):
        return None
    continuation = row['payload']['execution_continuation']
    from workbench import task_execution
    ref = dict(intake_id=row['intake_id'], task_id=task_id, approval_id=continuation['scope_approval_id'])
    try:
        linked = task_execution.snapshot(c, **ref)
    except (ValueError, KeyError, HTTPException):
        return None
    if linked['proposal_review'] != row['snapshot']:
        return None
    return dict(ref=ref, decision=dict(row), provenance=continuation['owner_provenance'])


def process_one(engine):
    """Renew a settled operation using the same authority and fresh frozen work."""
    from workbench import owner_task, task_sessions as sessions
    if not sessions.capability(engine):
        return False
    with engine.connect() as c:
        ids = c.execute(select(task_sessions.c.task_id).where(
            task_sessions.c.status == 'failed', task_sessions.c.finished_at.is_not(None)).distinct()).scalars().all()
        authorized = [v for identity in ids if (v := current(c, identity))
            and v['decision']['session_status'] == 'failed']
    for value in authorized:
        # The normal continuation proof verifies dead owners/cgroups, closed edits,
        # usage, restoration and fresh provider/scope bindings before any grant.
        proof = value['provenance']
        with engine.connect() as c:
            if c.execute(select(proposal_decisions.c.id).outerjoin(task_sessions,
                    task_sessions.c.id == proposal_decisions.c.id).where(
                    proposal_decisions.c.proposal_id == value['decision']['proposal_id'],
                    proposal_decisions.c.status == 'applied', task_sessions.c.id.is_(None),
                    proposal_decisions.c.payload['execution_continuation']['owner_provenance']['purpose'].as_string() == PURPOSE)).first():
                continue  # A fresh operation is already waiting for normal queue admission.
            from workbench.db import real_execution_grants as grants, real_execution_requests as requests
            previous = c.execute(select(requests.c.result,grants.c.snapshot).join(grants, requests.c.grant_id == grants.c.id)
                .where(grants.c.snapshot['task_budget']['decision_id'].as_string() == value['decision']['id'])
                .order_by(requests.c.created_at.desc()).limit(1)).mappings().first()
            result = previous['result'] if previous else {}
            if previous and unchanged_failure(c, previous):
                continue
        # A deliberate loop stop asks the engineering recovery path to change
        # strategy. Continuing permission does not justify identical retries.
        if any(reason in result.get('error','') for reason in (
                'Returned to the same previously rejected workspace', 'Repeated source reads made no progress',
                'No progress after repeated', 'unchanged_rejection')):
            continue
        try:
            renewed = owner_task.recover_local(engine, value['ref'], proof['authorization_path'],
                proof['authorization_sha256'], continuing=True)
        except (ValueError, OSError, KeyError, HTTPException):
            continue  # Uncertain/changed inputs are never replayed or granted.
        if renewed['approval_id'] != value['decision']['id']:
            return True
    return False


def unchanged_failure(c, previous):
    """Do not launch the same failed strategy on the same retained source again.

    An engineering correction can still authorize a changed continuation through
    trusted ingress. This fence asks for diagnosis, not another owner decision.
    """
    from pathlib import Path
    from workbench.db import real_execution_requests
    from workbench.repair_continuation import private_json
    from workbench.real_workspace import Runtime, sha
    try:
        root = Path(previous['snapshot']['recovery']['root'])
        resume_path = root/'repair-resume.json'
        if not resume_path.is_file():
            return False
        resume = private_json(resume_path)
        older = c.execute(select(real_execution_requests.c.result).where(
            real_execution_requests.c.id == resume['execution_id'])).scalar() or {}
        error = (previous['result'] or {}).get('error')
        if not error or older.get('error') != error:
            return False
        runtime = Runtime(root)
        changed, patch = runtime.scope(allow_empty=True)
        return sha(patch.encode()) == resume['patch_sha256']
    except (ValueError, OSError, KeyError, TypeError):
        return True  # Unavailable comparison never justifies blind automatic replay.
