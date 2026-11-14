"""Standing routine-bug authority applied through existing policy/decision owners.

Receipts describe policy applications, never human review. Only fresh reports can
freeze authority. Current revocation/pause and exact source still gate admission.
"""
from typing import Literal
from uuid import UUID, uuid4, uuid5
import re
import json
import hashlib
from pathlib import Path
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from pydantic import Field
from sqlalchemy import select, func, or_

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
            planning_jobs.c.kind == 'planning').order_by(__import__('workbench.work_controls',fromlist=['priority_order']).priority_order(planning_jobs.c.intake_id),planning_jobs.c.created_at, planning_jobs.c.id)).scalars():
        if payload.get('routine_bug_authority'):
            return payload['routine_bug_authority']
    return None


def current(c, identity, *, admitting=True):
    from workbench.model_policy import read_connection
    value = frozen(c, identity)
    saved = read_connection(c)
    if (not value or digest({k:v for k,v in value.items() if k != 'policy_sha256'}) != value['policy_sha256']
            or not saved.routine_bugs or not saved.routine_bugs.enabled or admitting and saved.pause_new_work
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


def validate(c, identity, review, proof, *, active=True, admitting=True):
    value = current(c, identity, admitting=admitting) if active else frozen(c, identity)
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


def scope_approval(decisions):
    return next((d for d in decisions if d['status']=='applied'
        and d['payload'].get('policy_authorization') and d['payload'].get('action')=='approve'
        and not d['payload'].get('execution_continuation') and not d['payload'].get('policy_operation')),None)


def process_one(engine):
    """Derive a single scope/run receipt; normal worker stages apply and execute it."""
    from workbench import intake, task_budget, task_execution
    from workbench.schemas import TaskBudget
    with write_transaction(engine) as c:
        rows = c.execute(select(intakes).where(intakes.c.status.in_(['completed', 'tasks_ready']))
            .order_by(__import__('workbench.work_controls',fromlist=['priority_order']).priority_order(intakes.c.id),intakes.c.created_at, intakes.c.id)).mappings().all()
        for row in rows:
            from workbench.worker_operation import eligible
            if not c.scalar(select(intakes.c.id).where(intakes.c.id==row['id'],eligible(c,intakes.c.id))):
                continue
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
                parent = scope_approval(decisions)
                if not parent:
                    continue
                operations=[d for d in decisions if d['payload'].get('run_budget') or d['payload'].get('policy_operation')]
                if len(operations)>=authority['policy']['max_operations']:
                    continue
                tasks = list(c.execute(select(backlog_items).where(backlog_items.c.proposal_id == proposal['id'],
                    backlog_items.c.kind == 'task')).mappings())
                selected = task_budget.offer(c, proposal['id'])
                if len(tasks)==1 and not selected and renew_legacy_program(c,row,review,parent,proof):
                    return True
                if len(tasks) != 1 or not selected:
                    continue
                if operations and (not selected['snapshot'].get('repair_resume') or any(d['payload'].get('run_budget',{}).get('offer_id')==selected['id'] for d in decisions)):
                    continue
                ref = dict(intake_id=row['id'], task_id=tasks[0]['id'], approval_id=parent['id'])
                linked = task_execution.snapshot(c, **ref)
                if selected['snapshot'].get('reviewed_task', {}).get('ref') != ref:
                    continue
                budget = TaskBudget(offer_id=selected['id'], offer_fingerprint=selected['fingerprint'], activate_once=True)
                task_budget.validate_selection(c, proposal['id'], budget, admitting=True)
                from workbench.repair_continuation import freeze
                freeze(c,selected)
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


def renew_legacy_program(c,row,review,parent,proof):
    """One bounded upgrade of the original one-shot check design, in its existing job."""
    job=c.execute(select(planning_jobs).where(planning_jobs.c.intake_id==row['id'],
        planning_jobs.c.revision==row['revision'],planning_jobs.c.kind=='behavioral_preparation')).mappings().first()
    if not job or job['status']!='failed':
        return False
    payload=job['payload']
    claim=payload.get('independent_program_claim') or {}
    result=payload.get('preparation_result') or {}
    if (claim.get('source_basis')!='original-only' or claim.get('review_protocol')
            or payload.get('check_preparation_history') or result.get('status')!='hold'
            or result.get('message')!='Independent check review did not establish all original behavior requirements'):
        return False
    from workbench.schemas import PlanningRetry
    from workbench.bug_preparation_retry import request_in
    identity=str(uuid5(UUID(row['id']),'routine-independent-review-upgrade:'+claim['id']))
    body=PlanningRetry(job_id=job['id'],command_id=identity,review_binding=review['binding'],renew_check_construction=True)
    try:
        request_in(c,job,body)
    except (HTTPException,ValueError):
        return False
    operation=dict(kind='renew-independent-review',job_id=job['id'],previous_attempt=claim['id'],
        scope_approval_id=parent['id'],scope_binding=review['binding'],review_protocol='composed-bounded-v1')
    binding=digest(operation)
    c.execute(proposal_decisions.insert().values(id=identity,intake_id=row['id'],proposal_id=review['id'],
        binding=binding,scope_revision=review['human_scope']['revision'],required_artifacts=review['required_artifacts'],
        scope_review_required=0,payload=dict(action='authorize-operation',command_id=identity,
            proposal_id=review['id'],binding=binding,scope_revision=review['human_scope']['revision'],
            policy_authorization=proof,policy_operation=operation),snapshot=review,status='applied',
        message='Saved owner policy authorizes a fresh bounded independent check design on the same scope. No human approval or implementation grant.',
        created_at=now(),finished_at=now()))
    return True


def validate_operation(c, grant, *, admitting=True):
    """A fresh policy operation never makes its expired predecessor valid."""
    from workbench.db import real_execution_requests as requests, real_execution_grants as grants, task_sessions
    from workbench import task_budget
    value=grant['snapshot']['policy_operation']
    receipt=c.execute(select(proposal_decisions).where(proposal_decisions.c.id==value['decision_id'])).mappings().one()
    operation={k:v for k,v in value.items() if k not in {'decision_id','binding'}}
    linked=grant['snapshot']['workflow_task']
    if (receipt['status']!='applied' or receipt['payload']['action']!='authorize-operation'
            or receipt['binding']!=value['binding'] or digest(operation)!=value['binding']
            or receipt['payload']['policy_operation']!=operation or receipt['snapshot']!=linked['proposal_review']):
        raise ValueError('The policy operation record changed')
    validate(c,linked['intake_id'],linked['proposal_review'],receipt['payload']['policy_authorization'],admitting=admitting)
    row=c.execute(select(requests).where(requests.c.id==operation['request_id'])).mappings().one()
    prior=c.execute(select(grants).where(grants.c.id==operation['previous_grant_id'])).mappings().one()
    session=c.execute(select(task_sessions).where(task_sessions.c.id==linked['approval']['id'])).mappings().one()
    if (row['grant_id']!=grant['id'] or row['fence']!=operation['fence']
            or session['state'].get('cancel_requested') or session['status'] in {'cancelled','expired'}
            or prior['binding']!=operation['previous_binding']
            or grant['snapshot']['runtime']!=prior['snapshot']['runtime']
            or grant['snapshot']['workflow_task']!=prior['snapshot']['workflow_task']
            or grant['snapshot']['task_budget']!=prior['snapshot']['task_budget']
            or grant['expires_at']!=operation['expires_at']):
        raise ValueError('Policy operation source, task, grant or owner stop changed')
    from workbench.real_workspace import sha
    from workbench.provider_admission import read as private_read
    checkpoint=private_read(Path(operation['checkpoint_path']),None)
    if (sha(Path(operation['checkpoint_path']).read_bytes())!=operation['checkpoint_sha256']
            or checkpoint['dispatch']!='not_started' or checkpoint['execution_id']!=row['id']
            or checkpoint['fence']!=row['fence']):
        raise ValueError('The settled coding checkpoint changed')
    # The old explicit budget remains historical. Its counters and offer are
    # unchanged; this operation's separately recorded grant owns new admission.
    original_approval=c.execute(select(proposal_decisions).where(proposal_decisions.c.id==linked['approval']['id'])).mappings().one()
    actual=task_budget.permission(c,original_approval,require_current=False)
    if actual!=grant['snapshot']['task_budget']:
        raise ValueError('Original budget or spent accounting changed')
    from workbench.schemas import TaskBudget
    task_budget.validate_selection(c,linked['task']['proposal_id'],TaskBudget.model_validate(actual['budget']),admitting=admitting)
    return operation


def renew_wait(engine, row, grant, runtime):
    """Reauthorize only a saved nondispatched suffix; never reset a coding run."""
    import copy
    from workbench import real_execution as execution, task_execution
    from workbench.smart_coding_wait import load
    from workbench.db import real_execution_grants as grants, real_execution_requests as requests, task_sessions
    if not grant['snapshot'].get('runtime',{}).get('codex_selection',{}).get('strategy'):
        raise ValueError('Only a frozen Smart coding wait can renew through this ingress')
    checkpoint=load(runtime,row,grant)
    if execution.alive(row['result']['owner']) or grant['revoked_at']:
        raise ValueError('An active or withdrawn operation cannot be renewed')
    with write_transaction(engine) as c:
        current_row=c.execute(select(requests).where(requests.c.id==row['id'])).mappings().one()
        if current_row['status']!='waiting_ai' or current_row['fence']!=row['fence'] or current_row['result']!=row['result']:
            raise ValueError('The waiting operation changed before renewal')
        if current_row['grant_id']!=grant['id']:
            existing=c.execute(select(grants).where(grants.c.id==current_row['grant_id'])).mappings().one()
            validate_operation(c,existing)
            return dict(existing)
        linked=grant['snapshot']['workflow_task']
        authority=current(c,linked['intake_id'])
        proof=attribution(linked['intake_id'],authority)
        validate(c,linked['intake_id'],linked['proposal_review'],proof)
        if not task_execution.current(c,linked):
            raise ValueError('The original scope changed while waiting')
        session=c.execute(select(task_sessions).where(task_sessions.c.id==linked['approval']['id'])).mappings().one()
        if session['state'].get('cancel_requested') or session['status'] in {'cancelled','expired'}:
            raise ValueError('The owner stopped this task')
        from workbench import task_budget
        from workbench.schemas import TaskBudget
        original_approval=c.execute(select(proposal_decisions).where(proposal_decisions.c.id==linked['approval']['id'])).mappings().one()
        budget=task_budget.permission(c,original_approval,require_current=False)
        task_budget.validate_selection(c,linked['task']['proposal_id'],TaskBudget.model_validate(budget['budget']),admitting=True)
        issued=datetime.now(timezone.utc)
        end=issued+timedelta(seconds=2400)
        number=c.scalar(select(func.count()).select_from(proposal_decisions).where(
            proposal_decisions.c.intake_id==linked['intake_id'],or_(proposal_decisions.c.payload['action'].as_string()=='authorize-operation',proposal_decisions.c.payload['run_budget'].as_string().is_not(None))))
        if number>=authority['policy']['max_operations']:
            raise ValueError('The configured bounded repair-operation limit was reached; retained work needs review')
        identity=str(uuid5(UUID(row['id']),'policy-wait:'+grant['id']+':'+checkpoint['checkpoint_sha256']))
        operation=dict(version=1,request_id=row['id'],fence=row['fence'],previous_grant_id=grant['id'],previous_binding=grant['binding'],
            checkpoint_path=row['result']['ai_checkpoint']['path'],checkpoint_sha256=checkpoint['checkpoint_sha256'],
            sequence=checkpoint['sequence'],active_seconds=checkpoint['active_seconds'],completed_requests=checkpoint['progress']['next_turn'],
            issued_at=issued.isoformat(),expires_at=end.isoformat())
        binding=digest(operation)
        c.execute(proposal_decisions.insert().values(id=identity,intake_id=linked['intake_id'],proposal_id=linked['task']['proposal_id'],
            binding=binding,scope_revision=linked['proposal_review']['human_scope']['revision'],required_artifacts=linked['proposal_review']['required_artifacts'],
            scope_review_required=0,payload=dict(command_id=identity,proposal_id=linked['task']['proposal_id'],binding=binding,
                scope_revision=linked['proposal_review']['human_scope']['revision'],action='authorize-operation',
                note='Fresh bounded operation under the frozen owner policy; original grants, completed turns and consumption are unchanged.',
                policy_authorization=proof,policy_operation=operation),snapshot=linked['proposal_review'],status='applied',
            message='A fresh bounded operation is authorized under the saved owner policy.',created_at=issued.isoformat(),finished_at=issued.isoformat()))
        snapshot=copy.deepcopy(grant['snapshot'])
        snapshot['policy_operation']={**operation,'decision_id':identity,'binding':binding}
        snapshot['grant_deadline']=end.isoformat()
        snapshot['owner_instruction']=json_instruction=json.dumps(dict(policy_authorization=proof,operation=operation),sort_keys=True)
        snapshot['authorization_sha256']=hashlib.sha256(json_instruction.encode()).hexdigest()
        snapshot['provenance']='Fresh bounded routine bug operation after a settled allowance wait'
        return execution.renew_waiting_grant(c,row,grant,snapshot,str(uuid5(UUID(identity),'operation-grant')),issued,end)
