"""One explicit parent execution decision; never a generated subtask approval.

This module binds scope, graph, saved AI and finite aggregate limits. It grants no
runtime by itself. The existing session/grant/check owners must admit every step.
"""
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select

from workbench import intake, task_execution, provider_routes, task_sessions
from workbench.db import intakes, proposals, proposal_decisions, backlog_items, planning_jobs, write_transaction
from workbench.schemas import FeatureBudget, FeatureBudgetV1
from workbench.schemas import FeatureRunInput as FeatureRunInput
from workbench.visual_scope import digest


def capability(engine):
    configured = task_sessions.capability(engine)
    if not configured or configured.get('feature_execution') != 'sequential-feature-v1':
        raise ValueError('Sequential feature implementation is not installed.')
    return configured


def maximum_tokens(scope, budget):
    result = len(scope['graph'])*(budget['scope_attempts_per_step']*budget['scope_completion_tokens']
        +budget['implementation_attempts_per_step']*budget['per_attempt_tokens'])
    if budget['version'] == 2:
        result += budget['verification_requests']*budget['verification_completion_tokens']
    return result


def scope(c, identity, *, dispatch=True):
    """Canonical current human scope and complete graph; no model or filesystem I/O."""
    row = intake.require(c, intakes, identity)
    if not row['payload'].get('feature_planning'):
        raise ValueError('Only a grounded Feature Workshop request can use a parent run.')
    from workbench.task_cleanup import require_current
    if dispatch:
        require_current(c, identity)
    if dispatch and row['status'] not in {'tasks_ready','approved'}:
        raise ValueError('The feature is not ready for implementation permission.')
    proposal = c.execute(select(proposals).where(proposals.c.intake_id == identity,
        proposals.c.revision == row['revision'])).mappings().first()
    if not proposal:
        raise ValueError('The current feature plan is unavailable.')
    approvals = c.execute(select(proposal_decisions).where(
        proposal_decisions.c.proposal_id == proposal['id'],
        proposal_decisions.c.status == 'applied').order_by(proposal_decisions.c.created_at.desc())).mappings()
    approval = next((a for a in approvals if a['payload'].get('action') == 'approve'
        and not a['payload'].get('execution_continuation') and not a['payload'].get('run_budget')), None)
    if not approval:
        raise ValueError('Review and apply this feature plan before allowing implementation.')
    intake.validate_approval(c, approval)
    tasks = list(c.execute(select(backlog_items).where(backlog_items.c.proposal_id == proposal['id'],
        backlog_items.c.kind == 'task').order_by(backlog_items.c.id)).mappings())
    if not 1 <= len(tasks) <= FeatureBudget().max_steps:
        raise ValueError('A bounded plan with one to six actionable feature steps is required.')
    linked = [task_execution.snapshot(c,identity,t['id'],approval['id']) for t in tasks]
    job = c.execute(select(planning_jobs).where(planning_jobs.c.intake_id == identity,
        planning_jobs.c.kind == 'decomposition', planning_jobs.c.revision == row['revision'])).mappings().one()
    if not job['payload'].get('feature_base'):
        raise ValueError('The graph has no grounded feature base.')
    route = provider_routes.approved(c, row)
    provider = 'codex' if row['payload']['local_planning'] in provider_routes.CODEX_CHOICES else 'local'
    return dict(intake_id=identity,proposal_id=proposal['id'],scope_approval_id=approval['id'],
        scope_binding=approval['binding'],original_binding=digest(row['payload']),
        revision=proposal['revision'],scope_revision=proposal['scope_revision'],
        feature_base=job['payload']['feature_base'],plan_binding=linked[0]['proposal_review']['binding'],
        graph=[dict(task_id=t['task']['id'],binding=digest(t),dependencies=t['dependencies']) for t in linked],
        provider=provider,route=route)


def offer(engine, identity):
    capability(engine)
    with engine.connect() as c:
        current = scope(c, identity)
        if current['provider'] == 'codex':
            # The saved route already selects the existing tool-free Codex
            # adapter. A feature run never activates its planning fallback.
            provider_routes.validate_selection('codex', current['route']['model'], current['route']['effort'])
        existing = c.execute(select(proposal_decisions.c.payload).where(
            proposal_decisions.c.proposal_id == current['proposal_id'])).scalars()
        if any(p.get('feature_run') for p in existing):
            raise ValueError('This feature already has its parent run decision. Inspect its saved progress.')
    from workbench.project_source import require_current
    require_current({'resolved_base':current['feature_base']})
    budget = FeatureBudget().model_dump(mode='json')
    bound = dict(scope=current,budget=budget)
    return dict(**bound,binding=digest(bound),step_count=len(current['graph']),
        maximum_generated_tokens=maximum_tokens(current,budget))


def stop(engine, identity, decision_id, command_id):
    """Stop the actual parent, including its brief pending-admission window."""
    from workbench.db import task_sessions as sessions
    with write_transaction(engine) as c:
        row = intake.require(c, proposal_decisions, decision_id)
        if row['intake_id'] != identity or not row['payload'].get('feature_run'):
            raise HTTPException(404, 'Feature run not found.')
        job = c.execute(select(planning_jobs).where(planning_jobs.c.kind == 'feature_execution',
            planning_jobs.c.payload['feature_decision_id'].as_string() == decision_id)).mappings().one()
        previous = job['payload'].get('feature_stop_command')
        if previous:
            if previous != command_id:
                raise HTTPException(409, 'Another stop request is already saved.')
            return dict(id=decision_id, status='cancelled')
        if row['status'] == 'pending':
            message = 'Feature stopped before admission. No implementation started.'
            c.execute(proposal_decisions.update().where(proposal_decisions.c.id == decision_id).values(
                status='rejected', finished_at=intake.now(), message=message))
            c.execute(planning_jobs.update().where(planning_jobs.c.id == job['id']).values(
                status='failed', finished_at=intake.now(), message=message,
                payload={**job['payload'], 'phase':'held', 'feature_stop_command':command_id}))
            return dict(id=decision_id, status='cancelled')
        if not c.execute(select(sessions.c.id).where(sessions.c.id == decision_id)).first():
            raise HTTPException(409, 'This run has finished. Its saved history remains available.')
    return task_sessions.cancel(engine, decision_id, command_id)


def _prior(c, identity, command):
    prior = c.execute(select(proposal_decisions).where(proposal_decisions.c.id == command['command_id'])).mappings().first()
    if prior:
        if prior['intake_id'] != identity or prior['payload'].get('feature_browser_command') != command:
            raise HTTPException(409, 'This command is already saved for different work.')
        return intake.receipt_view(prior)
    return None


def request(engine, identity, body):
    """Future browser ingress; inactive until the finite feature capability is installed."""
    command = dict(command_id=str(body.command_id),binding=body.binding)
    with engine.connect() as c:
        prior = _prior(c, identity, command)
        if prior:
            return prior
    try:
        selected = offer(engine, identity)
    except (ValueError, OSError):
        # An identical overlapping command may commit between the first read
        # and offer's competing-parent guard. Return only that exact receipt.
        with engine.connect() as c:
            prior = _prior(c, identity, command)
            if prior:
                return prior
        raise HTTPException(409, 'Feature implementation is unavailable or this plan changed. Refresh the feature before allowing work.') from None
    if selected['binding'] != body.binding:
        raise HTTPException(409, 'The feature scope, graph or saved AI changed. Refresh the complete feature before allowing work.')
    with write_transaction(engine) as c:
        prior = _prior(c, identity, command)
        if prior:
            return prior
        try:
            current = scope(c, identity)
        except (ValueError, HTTPException):
            raise HTTPException(409, 'The feature changed while the run decision was being saved.') from None
        if current != selected['scope']:
            raise HTTPException(409, 'The feature changed while the run decision was being saved.')
        for value in c.execute(select(proposal_decisions.c.payload).where(
                proposal_decisions.c.proposal_id == current['proposal_id'])).scalars():
            if value.get('feature_run'):
                raise HTTPException(409, 'A parent run decision is already saved; no competing writer was created.')
        parent = intake.require(c, proposal_decisions, current['scope_approval_id'])
        started = intake.now()
        continuation = dict(scope_approval_id=parent['id'],scope_binding=parent['binding'],
            owner_provenance=dict(actor='owner',purpose='browser-feature-run-v1',command_id=command['command_id']))
        permission = dict(scope=current,budget=selected['budget'],
            maximum_generated_tokens=selected['maximum_generated_tokens'],
            expires_at=(datetime.fromisoformat(started)+timedelta(seconds=selected['budget']['overall_seconds'])).isoformat())
        binding = digest(dict(execution_continuation=continuation,feature_run=permission))
        row = dict(id=command['command_id'],intake_id=identity,proposal_id=current['proposal_id'],
            binding=binding,scope_revision=parent['scope_revision'],required_artifacts=parent['required_artifacts'],
            scope_review_required=0,payload=dict(command_id=command['command_id'],proposal_id=current['proposal_id'],
                binding=binding,scope_revision=parent['scope_revision'],action='approve',
                note='Build the reviewed feature through one authoritative sequence of checked steps.',
                feature_browser_command=command,execution_continuation=continuation,feature_run=permission),
            snapshot=parent['snapshot'],status='pending',message='Feature run decision saved. Waiting for admission.',
            created_at=started,finished_at=None)
        c.execute(proposal_decisions.insert().values(**row))
        intake.enqueue(c, identity, 'feature_execution', current['revision'], dict(
            feature_decision_id=row['id'], parent_permission_pending=True,
            scope_approval_id=current['scope_approval_id'], feature_base=current['feature_base'],
            graph_binding=digest(current['graph'])))
        return intake.receipt_view(row)


def validate(c, row, *, pending=False, active=True):
    if active:
        capability(c.engine)
        from workbench.db import task_sessions
        session = c.execute(select(task_sessions).where(task_sessions.c.id == row['id'])).mappings().first()
        if session and (session['state'].get('cancel_requested') or session['status'] in {'cancelled','expired'}):
            raise ValueError('This parent feature was stopped or expired; its history remains available.')
    permission = row['payload']['feature_run']
    (FeatureBudgetV1 if permission['budget'].get('version') == 1 else FeatureBudget).model_validate(permission['budget'])
    current = scope(c, row['intake_id'], dispatch=active)
    parent = intake.require(c,proposal_decisions,current['scope_approval_id'])
    continuation = row['payload']['execution_continuation']
    expected = digest(dict(execution_continuation=continuation,feature_run=permission))
    from workbench.feature_run_recovery import deadline
    if (permission['scope'] != current or row['binding'] != expected
            or row['proposal_id'] != current['proposal_id'] or row['payload']['proposal_id'] != current['proposal_id']
            or row['scope_revision'] != parent['scope_revision'] or row['required_artifacts'] != parent['required_artifacts']
            or row['payload'].get('feature_browser_command') != dict(command_id=row['id'],binding=digest(dict(scope=current,budget=permission['budget'])))
            or row['payload']['binding'] != expected or row['payload']['action'] != 'approve'
            or row['snapshot'] != intake.require(c,proposal_decisions,current['scope_approval_id'])['snapshot']
            or continuation['scope_approval_id'] != current['scope_approval_id']
            or continuation['scope_binding'] != current['scope_binding']
            or continuation.get('owner_provenance') != dict(actor='owner',purpose='browser-feature-run-v1',command_id=row['id'])
            or row['scope_review_required'] or row['status'] != ('pending' if pending else 'applied')
            or (not pending and not row['finished_at'])
            or (active and datetime.now(timezone.utc) >= datetime.fromisoformat(deadline(c,row,permission)))
            or datetime.fromisoformat(permission['expires_at']) != datetime.fromisoformat(row['created_at'])+timedelta(seconds=permission['budget']['overall_seconds'])
            or permission['maximum_generated_tokens'] != maximum_tokens(current,permission['budget'])):
        raise ValueError('The parent feature permission is expired, stale, or changed.')
    return permission
