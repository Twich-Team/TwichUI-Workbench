"""Standing routine-bug authority applied through existing policy/decision owners.

Receipts describe policy applications, never human review. Only fresh reports can
freeze authority. Current revocation/pause and exact source still gate admission.
"""
from typing import Literal
from uuid import UUID, uuid4, uuid5
import re

from fastapi import HTTPException
from pydantic import Field
from sqlalchemy import select

from workbench.db import execution_policies, intakes, planning_jobs, proposal_decisions, proposals, backlog_items, write_transaction
from workbench.schemas import StrictModel, PolicyAttribution
from workbench.service import now
from workbench.visual_scope import digest


class RoutineBugPolicy(StrictModel):
    version: Literal[1] = 1
    authorization_id: UUID
    enabled: bool
    repository: Literal['Twich-Team/TwichUI'] = 'Twich-Team/TwichUI'
    max_operations: Literal[3] = 3
    publication: Literal['separate-policy'] = 'separate-policy'


class ControlInput(StrictModel):
    revision: int = Field(ge=0)
    enabled: bool


class Control(ControlInput):
    message: str



def view(policy):
    return Control(revision=policy.revision, enabled=bool(policy.routine_bugs and policy.routine_bugs.enabled),
        message='New routine bug reports can be investigated and fixed in isolated copies using their saved AI choices. Draft publication requires its separate saved policy. Turning this off stops new operations on covered reports; active operations can finish safely.')


def read(engine):
    from workbench.model_policy import read
    return view(read(engine))


def save(engine, body):
    from workbench.model_policy import read_connection
    with write_transaction(engine) as c:
        policy = read_connection(c)
        current = view(policy)
        if body.enabled == current.enabled and body.revision in {current.revision, current.revision - 1}:
            return current
        if body.revision != current.revision:
            raise HTTPException(409, 'Work settings changed. Reload before changing routine bug permission.')
        # Re-enabling creates new authority. It cannot resurrect revoked reports.
        authority = RoutineBugPolicy(authorization_id=uuid4() if body.enabled else policy.routine_bugs.authorization_id,
                                    enabled=body.enabled)
        updated = policy.model_copy(update=dict(revision=policy.revision + 1, routine_bugs=authority))
        c.execute(execution_policies.insert().values(revision=updated.revision, payload=updated.model_dump(mode='json'),
            saved_at=now(), actor='local-owner-routine-bug-policy'))
        return view(updated)


def prepare_submission(engine, body):
    """Resolve once. Retry input and original frozen choices survive global edits."""
    from workbench import model_policy, provider_routes
    original = body.model_dump(mode='json')
    with engine.connect() as c:
        prior = frozen(c, str(body.request_id))
        if prior:
            if prior['submitted_input'] != original:
                raise HTTPException(409, 'This report ID already belongs to different input.')
            return None, prior
        saved = model_policy.read_connection(c)
        if not (saved.routine_bugs and saved.routine_bugs.enabled and body.kind == 'issue'
                and not body.supported_task and body.visual_requirement == 'nonvisual'):
            return body, None
        from workbench.schemas import TaskAISettings
        if not body.local_planning:
            if saved.strategy:
                body = body.model_copy(update=dict(local_planning='smart-strategy',
                    ai_settings=TaskAISettings(strategy=saved.strategy.model_dump(), primary={k:getattr(saved.strategy.stages[0], k) for k in ('provider','model','effort')})))
            else:
                body = body.model_copy(update=dict(local_planning={
                    'local':'qwen-once', 'codex':'codex-once', 'claude':'claude-once'}[saved.preferred]))
        key, route = provider_routes.planning_binding(c, body.local_planning, body.ai_settings)
        from workbench import regression_preparation as source
        _, base, _ = source.repository()
        receipt = dict(version=1, policy_revision=saved.revision, policy=saved.routine_bugs.model_dump(mode='json'),
            submitted_input=original, base=base, route_key=key, route=route)
        receipt['policy_sha256'] = digest(receipt)
        return body, receipt


def frozen(c, identity):
    for payload in c.execute(select(planning_jobs.c.payload).where(planning_jobs.c.intake_id == identity,
            planning_jobs.c.kind == 'planning').order_by(planning_jobs.c.created_at, planning_jobs.c.id)).scalars():
        if payload.get('routine_bug_authority'):
            return payload['routine_bug_authority']
    return None


def current(c, identity):
    from workbench.model_policy import read_connection
    value = frozen(c, identity)
    saved = read_connection(c)
    if (not value or digest({k:v for k,v in value.items() if k != 'policy_sha256'}) != value['policy_sha256']
            or not saved.routine_bugs or not saved.routine_bugs.enabled or saved.pause_new_work
            or saved.routine_bugs.authorization_id != RoutineBugPolicy.model_validate(value['policy']).authorization_id):
        raise ValueError('Standing routine bug permission is paused, revoked or unavailable.')
    return value


def attribution(identity, value):
    return PolicyAttribution(intake_id=identity, policy_sha256=value['policy_sha256'],
                             policy_revision=value['policy_revision']).model_dump(mode='json')


def permitted_path(path):
    return (isinstance(path, str) and re.fullmatch(
        r'(?:(?:!!!TwichUI|TwichUI_Settings)/[A-Za-z0-9_!./-]+\.lua|tests/test_[A-Za-z0-9_-]+\.(?:lua|py)|tools/[A-Za-z0-9_/-]+\.py)', path)
        and not any(p.startswith('.') for p in path.split('/'))
        and not {'libs', 'lib', 'vendor', 'auth', 'security', 'credentials'} & {p.lower() for p in path.split('/')} )


def validate(c, identity, review, proof, *, active=True):
    value = current(c, identity) if active else frozen(c, identity)
    if not value or digest({k:v for k,v in value.items() if k != 'policy_sha256'}) != value['policy_sha256']:
        raise ValueError('The frozen routine bug policy receipt changed.')
    if proof != attribution(identity, value):
        raise ValueError('The exact frozen owner policy attribution changed.')
    contract = review.get('task_contract') or {}
    scope = review['human_scope']
    if (contract.get('category') not in {'lua-offline-bugfix','python-offline-bugfix'} or not contract.get('bug_intent')
            or not contract.get('bug_intent',{}).get('plan',{}).get('repair_profile')
            or contract.get('scope_missing') or scope['visual_requirement'] != 'nonvisual'
            or scope['stage'] != 'implementation' or review['research_only']
            or review['approval_blockers'] or contract.get('resolved_base') != value['base']
            or not contract.get('writable_paths')
            or not all(permitted_path(p) for p in contract['writable_paths'])):
        raise ValueError('This scope needs owner review or exceeds routine bug permission.')
    if active:
        from workbench.project_source import require_current
        require_current(contract)
    return value


def validate_behavior(c, review, payload):
    """Alternative authority for the same exact scope, without human confirmation."""
    if proof := payload.get('policy_authorization'):
        try:
            validate(c, str(proof['intake_id']), review, proof)
        except (ValueError, KeyError) as error:
            raise HTTPException(409, str(error)) from None
    else:
        from workbench.bug_evidence import validate_expectation_review
        validate_expectation_review(review, payload)


def process_one(engine):
    """Derive a single scope/run receipt; normal worker stages apply and execute it."""
    from workbench import intake, task_budget, task_execution
    from workbench.schemas import TaskBudget
    with write_transaction(engine) as c:
        rows = c.execute(select(intakes).where(intakes.c.status.in_(['completed', 'tasks_ready']))
            .order_by(intakes.c.created_at, intakes.c.id)).mappings().all()
        for row in rows:
            if not frozen(c, row['id']):
                continue
            proposal = c.execute(select(proposals).where(proposals.c.intake_id == row['id'],
                proposals.c.revision == row['revision'])).mappings().first()
            if not proposal:
                continue
            review = intake.proposal_view(c, proposal)
            try:
                authority = current(c, row['id'])
                proof = attribution(row['id'], authority)
                validate(c, row['id'], review, proof)
            except (ValueError, HTTPException):
                continue
            decisions = list(c.execute(select(proposal_decisions).where(
                proposal_decisions.c.proposal_id == proposal['id'])).mappings())
            if not decisions:
                binding = review['binding']
                identity = str(uuid5(UUID(row['id']), 'routine-scope:' + binding))
                payload = dict(command_id=identity, proposal_id=proposal['id'], binding=binding,
                    scope_revision=review['human_scope']['revision'], action='approve',
                    note='Authorized under the saved owner policy; no human plan review was recorded.',
                    policy_authorization=proof)
            else:
                parent = next((d for d in decisions if d['status'] == 'applied'
                    and d['payload'].get('policy_authorization') and not d['payload'].get('execution_continuation')), None)
                if not parent or any(d['payload'].get('run_budget') for d in decisions):
                    continue
                tasks = list(c.execute(select(backlog_items).where(backlog_items.c.proposal_id == proposal['id'],
                    backlog_items.c.kind == 'task')).mappings())
                selected = task_budget.offer(c, proposal['id'])
                if len(tasks) != 1 or not selected:
                    continue
                ref = dict(intake_id=row['id'], task_id=tasks[0]['id'], approval_id=parent['id'])
                linked = task_execution.snapshot(c, **ref)
                if selected['snapshot'].get('reviewed_task', {}).get('ref') != ref:
                    continue
                budget = TaskBudget(offer_id=selected['id'], offer_fingerprint=selected['fingerprint'], activate_once=True)
                task_budget.validate_selection(c, proposal['id'], budget, admitting=True)
                identity = str(uuid5(UUID(row['id']), 'routine-run:' + selected['fingerprint']))
                continuation = dict(scope_approval_id=parent['id'], scope_binding=parent['binding'], owner_provenance=proof)
                binding = digest(dict(execution_continuation=continuation, run_budget=budget.model_dump(mode='json')))
                payload = dict(command_id=identity, proposal_id=proposal['id'], binding=binding,
                    scope_revision=review['human_scope']['revision'], action='approve',
                    note='Bounded operation authorized under the saved owner policy; publication remains separately controlled.',
                    policy_authorization=proof, execution_continuation=continuation, run_budget=budget.model_dump(mode='json'))
                # Ensure this receipt refers to the exact task and prepared offer.
                if linked['proposal_review'] != review:
                    continue
            c.execute(proposal_decisions.insert().values(id=identity, intake_id=row['id'], proposal_id=proposal['id'],
                binding=binding, scope_revision=review['human_scope']['revision'], required_artifacts=review['required_artifacts'],
                scope_review_required=0, payload=payload, snapshot=review, status='pending',
                message='Authorized under the saved routine bug policy; waiting for validation.', created_at=now(), finished_at=None))
            return True
    return False
