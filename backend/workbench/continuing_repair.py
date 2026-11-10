"""Explicit continuing permission, separate from every historical one-run grant.

The worker still grants one bounded operation at a time. Only settled, isolated
same-scope work may advance; cancellation, uncertainty or changed consent hold.
No model, public command or configuration can create this owner instruction.
"""
from fastapi import HTTPException
from sqlalchemy import select

from workbench.db import proposal_decisions, task_sessions

PURPOSE = 'same-task-local-until-result-v1'
CONTROLS = dict(actor='workbench-engineering',mode='bounded-local-reasoning-v1',request_seconds=300,reasoning_tokens=768)


def operational_controls(runtime):
    if not allows(runtime):
        return None
    return controls_for(runtime.workflow_task['approval'])


def controls_for(approval):
    if not permitted_budget(approval):
        return None
    saved=approval['payload']['execution_continuation'].get('operational_controls')
    if saved is None:
        return None  # Historical continuing decisions retain their actual direct mode.
    if saved!=CONTROLS:
        raise ValueError('Unknown continuing operational controls')
    return dict(**saved,decoding=dict(temperature=0.2,reasoning_format='deepseek',
        reasoning_budget_tokens=saved['reasoning_tokens'],chat_template_kwargs=dict(enable_thinking=True)))


def code_identity(folder=None):
    from pathlib import Path
    from hashlib import sha256
    folder = Path(folder) if folder else Path(__file__).parent
    return {name:sha256((folder/name).read_bytes()).hexdigest()
        for name in ('real_provider.py','implementation_context.py','continuing_repair.py')}


def request_engineering_stop(engine, identity, command_id, reason):
    """Trusted local recovery ingress; not a browser/owner stop or a new grant.

    Persist the actual actor/purpose before using normal cancellation. An owner
    stop already recorded always wins. The old attempt and permission stay intact.
    """
    from uuid import UUID
    from pathlib import Path
    from workbench.db import write_transaction, real_execution_requests as requests, real_execution_grants as grants
    from workbench.real_execution import now
    from workbench.real_workspace import sha
    from workbench.repair_continuation import private_json
    UUID(command_id)
    if not isinstance(reason,str) or not 20 <= len(reason) <= 600:
        raise ValueError('A concrete bounded engineering recovery reason is required')
    with write_transaction(engine) as c:
        session = c.execute(select(task_sessions).where(task_sessions.c.id==identity)).mappings().one()
        approval = c.execute(select(proposal_decisions).where(proposal_decisions.c.id==identity)).mappings().one()
        if (not permitted_budget(approval) or session['status'] not in {'active','waiting_recovery'}
                or session['state'].get('cancel_requested')):
            raise ValueError('Only active continuing work without an owner stop can yield for repair')
        row = c.execute(select(requests,grants.c.snapshot.label('grant_snapshot')).join(grants,requests.c.grant_id==grants.c.id)
            .where(grants.c.snapshot['task_budget']['decision_id'].as_string()==identity)
            .order_by(requests.c.created_at.desc()).limit(1)).mappings().one()
        if row['status'] not in {'running','checking'}:
            raise ValueError('The actual bound operation is no longer active')
        root=Path(row['grant_snapshot']['recovery']['root'])
        latest=max((root/'attempt').glob('request-*.json'),key=lambda p:p.stat().st_mtime)
        observed=private_json(latest)
        if observed['request_id']!=row['id'] or observed['fence']!=row['fence']:
            raise ValueError('Current execution observations changed')
        marker=dict(actor='engineering-recovery',reason=reason,command_id=command_id,session_id=identity,
            execution_id=row['id'],fence=row['fence'],observed_request=str(latest),observed_sha256=sha(latest.read_bytes()),
            code_identity=code_identity(root.parents[3]/'backend/workbench'),
            owner_provenance=approval['payload']['execution_continuation']['owner_provenance'],created_at=now())
        c.execute(task_sessions.update().where(task_sessions.c.id==identity).values(
            state={**session['state'],'engineering_yield':marker}))
    from workbench.task_sessions import cancel
    return cancel(engine,identity,command_id)


def engineering_stop(session, approval):
    """Only the trusted recorded engineering stop, followed by changed code."""
    state=session.get('state') or {}
    marker=state.get('engineering_yield') or {}
    import re
    return (permitted_budget(approval) and marker.get('actor')=='engineering-recovery'
        and marker.get('session_id')==session['id'] and state.get('cancel_requested') is True
        and state.get('stop_command')==marker.get('command_id')
        and marker.get('owner_provenance')==approval['payload']['execution_continuation']['owner_provenance']
        and isinstance(marker.get('code_identity'),dict) and set(marker['code_identity'])==set(code_identity())
        and all(isinstance(v,str) and re.fullmatch('[a-f0-9]{64}',v) for v in marker['code_identity'].values())
        and marker['code_identity']!=code_identity())


def permitted_budget(approval):
    payload = approval.get('payload') or {}
    continuation = payload.get('execution_continuation') or {}
    proof = continuation.get('owner_provenance') or {}
    budget = payload.get('run_budget') or {}
    from workbench.visual_scope import digest
    return (proof.get('actor') == 'owner' and proof.get('purpose') == PURPOSE
        and continuation.get('operational_controls') in (None,CONTROLS)
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
    stopped=dict(id=row['id'],state=row['session_state'])
    engineering=engineering_stop(stopped,row)
    if (row['session_status'] in {'cancelled','expired'} or row['session_state'].get('cancel_requested')) and not engineering:
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
        # A deliberate engineering stop awaits tested activation and the trusted
        # continuation command; it must not start during installation history
        # verification merely because different code has appeared. Ordinary
        # safely settled failures still recover automatically. Owner Cancel holds.
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
                'No progress after repeated', 'unchanged_rejection', 'context reserve exhausted', 'Context reserve exhausted')):
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
