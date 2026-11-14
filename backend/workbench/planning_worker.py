"""Durable bounded planning. No implementation, subprocess, pilot write or publication path."""
from datetime import datetime, timedelta, timezone
from workbench import worker_operation as operation

import json
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError
from workbench.planning_transport import MAX_DEADLINE
from sqlalchemy import select, or_, and_, func

from workbench import intake
from workbench.db import (intakes, planning_jobs, proposals, proposal_decisions, backlog_items,
                          backlog_dependencies, model_attempts, write_transaction)
from workbench.planning_provider import provider, evidence_packet, PlanningUnavailable, MAX_OUTPUT, MAX_INPUT
from workbench.schemas import PlanningOutput, DecompositionOutput
from workbench.service import now


@operation.isolated
def apply_one(engine, fail_before_commit=False, command_id=None):
    with write_transaction(engine) as connection:
        receipt = connection.execute(select(proposal_decisions).where(proposal_decisions.c.status == 'pending',
            proposal_decisions.c.payload['feature_run'].as_string().is_(None),
            *([proposal_decisions.c.id == command_id] if command_id else []))
            .order_by(proposal_decisions.c.created_at, proposal_decisions.c.id).limit(1)).mappings().first()
        if not receipt:
            return False
        operation.selected(proposal_decisions, receipt)
        if receipt['payload'].get('execution_continuation'):
            # Same command journal, original scope/decomposition and budget owner.
            # Applying a post-review budget must never enqueue decomposition again.
            from workbench import task_budget, task_execution
            try:
                intake.validate_approval(connection, {**receipt, 'status': 'applied'})
                continuation = receipt['payload']['execution_continuation']
                policy_proof = receipt['payload'].get('policy_authorization')
                if continuation.get('owner_provenance', {}).get('purpose') != 'browser-reviewed-offer-v1' and not policy_proof:
                    raise ValueError('Unsupported pending continuation')
                if policy_proof:
                    tasks = connection.execute(select(backlog_items.c.id).where(backlog_items.c.proposal_id == receipt['proposal_id'], backlog_items.c.kind == 'task')).scalars().all()
                    if len(tasks) != 1 or continuation['owner_provenance'] != policy_proof:
                        raise ValueError('Policy operation task or attribution changed')
                    request = dict(task_id=tasks[0], run_budget=receipt['payload']['run_budget'])
                else:
                    request = continuation['browser_request']
                budget = receipt['payload']['run_budget']
                if (request['run_budget'] != budget or receipt['binding'] !=
                        task_budget.digest(dict(execution_continuation=continuation, run_budget=budget))):
                    raise ValueError('Saved browser decision differs from the approved budget')
                ref = dict(intake_id=receipt['intake_id'], task_id=request['task_id'],
                           approval_id=continuation['scope_approval_id'])
                linked = task_execution.snapshot(connection, **ref)
                from workbench.schemas import TaskBudget
                selected = task_budget.validate_selection(connection, receipt['proposal_id'],
                    TaskBudget.model_validate(budget), admitting=True)
                if (not policy_proof and request['binding'] != task_budget.digest(dict(scope=linked, offer=selected['fingerprint']))
                        or selected['snapshot'].get('reviewed_task', {}).get('ref') != ref):
                    raise ValueError('Scope or certified offer changed before application')
                # Original timestamp owns expiry, including time spent pending.
                task_budget.permission(connection, {**receipt, 'status': 'applied', 'finished_at': now()})
                status = 'applied'
                message = ('Local implementation approval applied. Publication held.'
                           if budget['publication'] == 'hold' else
                           'Implementation and one draft PR approved. Approval applied.')
            except (HTTPException, ValueError, KeyError, TypeError):
                status, message = 'stale', 'Scope, model preference, certification, runtime or budget changed/expired. This saved decision cannot start execution.'
            connection.execute(proposal_decisions.update().where(proposal_decisions.c.id == receipt['id']).values(
                status=status, message=message, finished_at=now()))
            if fail_before_commit:
                raise RuntimeError('Simulated application crash')
            return True
        try:
            row, proposal = intake.current(connection, receipt['intake_id'], receipt['proposal_id'], receipt['binding'])
            if row['status'] != 'completed':
                raise HTTPException(409)
            action = receipt['payload']['action']
            if action == 'approve':
                intake.validate_review(connection, proposal, intake.proposal_view(connection, proposal),
                                       receipt['scope_revision'], receipt['required_artifacts'])
                from workbench.bug_evidence import validate_expectation_review
                if receipt['payload'].get('policy_authorization'):
                    from workbench.bug_authority import validate
                    try:
                        validate(connection, receipt['intake_id'], intake.proposal_view(connection, proposal), receipt['payload']['policy_authorization'])
                    except ValueError as error:
                        raise HTTPException(409, str(error)) from None
                else:
                    validate_expectation_review(intake.proposal_view(connection, proposal), receipt['payload'])
                if receipt['scope_review_required'] or receipt['required_artifacts'] is None:
                    raise HTTPException(409, 'Scope review required')
            if receipt['scope_revision'] != intake.human_scope(connection, row['id'])['revision']:
                raise HTTPException(409, 'Human scope changed')
            status, message = 'applied', ('Scope and one task budget applied. Waiting for certified execution; no model or PR started.' if receipt['payload'].get('run_budget') else 'Scope decision applied. No execution or publication permission was granted.')
            state = {'approve':'approved', 'changes':'queued', 'decline':'declined'}[action]
            from workbench.bug_investigation import requested as bug_requested
            read_only_changes = action == 'changes' and bug_requested(row['payload'])
            if read_only_changes:
                from workbench.schemas import ClarificationInput
                intake.clarify_in(connection, row['id'], ClarificationInput(
                    command_id=receipt['id'], revision=row['revision'], text=receipt['payload']['note'],
                    continue_investigation=True, renew_operation=True))
                message = 'Your requested changes are saved. Workbench will refine the plan with the saved provider; addon work remains unapproved.'
            else:
                connection.execute(intakes.update().where(intakes.c.id == row['id']).values(status=state))
            if action == 'approve':
                source_payload = connection.execute(select(planning_jobs.c.payload).where(
                    planning_jobs.c.intake_id == row['id'], planning_jobs.c.kind == 'planning',
                    planning_jobs.c.revision == proposal['revision'],
                    planning_jobs.c.status == 'completed').limit(1)).scalar() or {}
                provenance = source_payload.get('owner_provenance')
                from workbench.feature_planning import bind_reviewed_decomposition
                if not (row['payload'].get('feature_planning') and bind_reviewed_decomposition(
                        connection, row, proposal, receipt)):
                    intake.enqueue(connection, row['id'], 'decomposition', proposal['revision'],
                        {'proposal_id':proposal['id'], 'approval_id':receipt['id'], 'binding':receipt['binding'],
                         **({'owner_provenance':provenance} if provenance else {})})
            elif action == 'changes' and not read_only_changes:
                intake.enqueue(connection, row['id'], 'planning', proposal['revision']+1,
                    {'feedback':receipt['payload']['note'], 'previous_proposal':proposal['content']})
        except HTTPException as error:
            status = 'rejected' if error.status_code == 422 else 'stale'
            message = 'The proposal, human scope or required artifacts changed or are missing. The saved decision was not applied.'
        if status == 'applied' and receipt['payload'].get('policy_authorization'):
            message = 'Authorized under the saved routine bug policy. No human plan approval was recorded.'
        connection.execute(proposal_decisions.update().where(proposal_decisions.c.id == receipt['id']).values(
            status=status, message=message, finished_at=now()))
        if fail_before_commit:
            raise RuntimeError('Simulated application crash')
    return True


def validate_decomposition(value, content):
    value = DecompositionOutput.model_validate(value)
    by_key = {task.key: task for task in value.tasks}
    if len(by_key) != len(value.tasks) or not any(t.kind == 'task' for t in value.tasks):
        raise ValueError('Unique actionable tasks required')
    if {index for task in value.tasks if task.kind=='task' for index in task.scope_indices} != set(range(len(content['scope']))):
        raise ValueError('Every approved scope item requires actionable coverage')
    for task in value.tasks:
        if any(index < 0 or index >= len(content['scope']) for index in task.scope_indices):
            raise ValueError('Task outside approved scope')
        if task.parent is not None and (task.parent not in by_key or by_key[task.parent].kind != 'milestone'):
            raise ValueError('Invalid organizational parent')
        if task.kind == 'milestone' and not any(other.parent==task.key for other in value.tasks):
            raise ValueError('Empty milestone')
        if task.key in task.depends_on or len(set(task.depends_on)) != len(task.depends_on) or any(key not in by_key for key in task.depends_on):
            raise ValueError('Invalid dependency')
    def acyclic(edges):
        visiting, visited = set(), set()
        def visit(key):
            if key in visiting:
                raise ValueError('Cycle')
            if key in visited:
                return
            visiting.add(key)
            for other in edges(key):
                visit(other)
            visiting.remove(key)
            visited.add(key)
        for key in by_key:
            visit(key)
    acyclic(lambda key: [by_key[key].parent] if by_key[key].parent else [])
    acyclic(lambda key: by_key[key].depends_on)
    return value


def materialize_single_task(connection, original, job, model):
    """Shared deterministic decomposition for one bounded, human-approved scope."""
    proposal = intake.require(connection, proposals, job['payload']['proposal_id'])
    approval = intake.require(connection, proposal_decisions, job['payload']['approval_id'])
    intake.validate_approval(connection, approval)
    content = proposal['content']
    decomposition = validate_decomposition(dict(tasks=[dict(
        key='implementation', kind='task', parent=None, title=content['summary'],
        purpose=content['value'], acceptance=content['acceptance'], depends_on=[],
        scope_indices=list(range(len(content['scope']))))]), content).model_dump(mode='json')
    root = 'intake-' + original['id']
    identity = root + '-' + str(job['revision']) + '-implementation'
    for data in [dict(id=root, parent=None, kind='request'), dict(id=identity, parent=root, kind='task')]:
        if not connection.execute(select(backlog_items.c.id).where(backlog_items.c.id == data['id'])).first():
            connection.execute(backlog_items.insert().values(**data, intake_id=original['id'],
                proposal_id=proposal['id'], title=content['summary'], purpose=content['value'],
                acceptance=content['acceptance'], model=model, created_at=now()))
    connection.execute(intakes.update().where(intakes.c.id == original['id']).values(status='tasks_ready'))
    return decomposition


def claim(engine):
    with write_transaction(engine) as connection:
        job = connection.execute(select(planning_jobs).where(operation.eligible(connection, planning_jobs.c.intake_id)).where(planning_jobs.c.kind.in_(['planning', 'decomposition']), or_(planning_jobs.c.status == 'queued',
            and_(planning_jobs.c.status == 'running', planning_jobs.c.lease_until < now())))
            .join(intakes, planning_jobs.c.intake_id == intakes.c.id)
            .where(intakes.c.payload['regression_tests'].as_string().is_(None))
            .where(intakes.c.payload['feature_planning'].as_boolean().is_not(True))
            .where(~((intakes.c.payload['kind'].as_string() == 'issue')
                & intakes.c.payload['local_planning'].as_string().is_not(None)
                & intakes.c.payload['supported_task'].as_string().is_(None)))
            .order_by(planning_jobs.c.created_at, planning_jobs.c.id).limit(1)).mappings().first()
        if not job:
            return None
        operation.selected(planning_jobs, job)
        job = dict(job)
        approval = None
        try:
            row = intake.require(connection, intakes, job['intake_id'])
            if job['kind'] == 'decomposition':
                approval = intake.require(connection, proposal_decisions, job['payload']['approval_id'])
            if job['payload'].get('scope_revision', 0) != intake.human_scope(connection, row['id'])['revision']:
                raise HTTPException(409, 'Human scope changed or is unconfirmed.')
            if job['kind'] == 'decomposition':
                intake.validate_approval(connection, approval)
            elif job['revision'] <= row['revision']:
                raise HTTPException(409, 'Planning revision superseded.')
        except (HTTPException, KeyError):
            if approval:
                connection.execute(proposal_decisions.update().where(proposal_decisions.c.id == approval['id']).values(scope_review_required=1))
                if row['revision'] == job['revision']:
                    connection.execute(intakes.update().where(intakes.c.id == row['id']).values(status='scope_review_required'))
            connection.execute(planning_jobs.update().where(planning_jobs.c.id == job['id']).values(
                status='failed', message='Scope review required. Superseded or legacy approval cannot create tasks; no planner was called.',
                finished_at=now(), lease=None, lease_until=None))
            return None
        if job['attempts'] >= 3:
            connection.execute(planning_jobs.update().where(planning_jobs.c.id == job['id']).values(
                status='failed', message='Recovery attempt limit reached. Retained request and approval are unchanged.', finished_at=now()))
            connection.execute(intakes.update().where(intakes.c.id == job['intake_id']).values(status='decomposition_failed' if job['kind']=='decomposition' else 'failed'))
            return None
        # Keep interrupted/expired evidence rather than releasing an accounting reservation.
        if job['lease']:
            connection.execute(model_attempts.update().where(model_attempts.c.id == job['lease']).values(outcome='interrupted_unknown'))
        lease = str(uuid4())
        expires = (datetime.now(timezone.utc)+timedelta(seconds=MAX_DEADLINE+30)).isoformat()
        job.update(status='running', lease=lease, lease_until=expires, attempts=job['attempts']+1)
        connection.execute(planning_jobs.update().where(planning_jobs.c.id == job['id']).values(
            status='running', lease=lease, lease_until=expires, attempts=job['attempts'], message='Investigating bounded context.' if job['kind']=='planning' else 'Preparing tasks within approved scope.'))
        connection.execute(model_attempts.insert().values(id=lease, job_id=job['id'], task_id=job['intake_id'],
            source='unknown', backend=None, purpose='planning' if job['kind']=='planning' else 'decomposition',
            routing_reason='Existing planning adapter configuration; new Workbench policy is not consumed or acknowledged.',
            started_at=now(), outcome='running'))
        if job['kind'] == 'planning':
            connection.execute(intakes.update().where(intakes.c.id == job['intake_id']).values(status='investigating'))
        return job


@operation.isolated
def process_one(engine, adapter=None, fail_before_commit=False, stop_event=None, *, scenario_response=None):
    from workbench import feature_planning
    if feature_planning.process_one(engine, adapter=adapter, stop_event=stop_event,
                                    fail_before_commit=fail_before_commit):
        return True
    from workbench import regression_preparation
    if regression_preparation.process_one(engine, fail_before_commit=fail_before_commit,
                                          scenario_response=scenario_response, adapter=adapter, stop_event=stop_event):
        return True
    if adapter is None:
        from workbench import supported_tasks
        if supported_tasks.process_one(engine):
            return True
    job = claim(engine)
    if not job:
        return False
    proposal = None
    model, basis = None, None
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql('BEGIN')
            row = intake.require(connection, intakes, job['intake_id'])
            related = [dict(id=r['id'], title=r['title']) for r in connection.execute(select(backlog_items.c.id, backlog_items.c.title).limit(20)).mappings()]
            proposal = intake.require(connection, proposals, job['payload']['proposal_id']) if job['kind']=='decomposition' else None
            if job['payload'].get('scope_revision', 0) != intake.human_scope(connection, row['id'])['revision']:
                raise HTTPException(409, 'Human scope changed')
            if proposal:
                intake.validate_approval(connection, intake.require(connection, proposal_decisions, job['payload']['approval_id']))
        evidence = evidence_packet(row['payload']) if not proposal else []
        packet = {'original':row['payload'], 'evidence':evidence, 'related':related,
                  **{k:v for k,v in job['payload'].items() if k in {'feedback','previous_proposal'}}}
        if proposal:
            packet['proposal'] = proposal['content']
        if len(json.dumps(packet, ensure_ascii=True).encode()) > MAX_INPUT:
            raise PlanningUnavailable('Input exceeds planning budget')
        job['payload'] = {**job['payload'], 'evidence':evidence, 'input_sha256':intake.digest(packet)}
        with write_transaction(engine) as connection:
            active = intake.require(connection, planning_jobs, job['id'])
            if active['lease'] != job['lease'] or active['status'] != 'running':
                return True
            connection.execute(planning_jobs.update().where(planning_jobs.c.id == job['id']).values(
                payload=job['payload'], message='Bounded context inspected; waiting for planning response.'))
        schema = DecompositionOutput if proposal else PlanningOutput
        adapter = adapter or provider(stop_event)
        result, model, basis = adapter.generate(packet, schema)
        if len(json.dumps(result).encode()) > MAX_OUTPUT or not isinstance(model, str) or not model or len(model) > 160 or basis not in {'demo', 'local'}:
            raise ValueError('Invalid provider envelope')
        if proposal:
            parsed = validate_decomposition(result, proposal['content'])
        else:
            parsed = PlanningOutput.model_validate(result)
            ids = set(parsed.evidence_ids + (parsed.proposal.evidence_ids if parsed.proposal else []))
            if not ids <= {e['id'] for e in evidence} or not set(parsed.related_ids) <= {r['id'] for r in related}:
                raise ValueError('Unsupported reference')
            if row['payload']['kind'] == 'feature' and not parsed.proposal:
                raise ValueError('Feature proposal missing')
            if row['payload']['kind'] == 'issue' and parsed.proposal:
                raise ValueError('Issue diagnosis cannot become an implementation proposal')
        result = parsed.model_dump(mode='json')
        failure = None
    except (PlanningUnavailable, ValidationError, ValueError, OSError, KeyError, TypeError, HTTPException) as error:
        result = None
        category = error.reason if isinstance(error, PlanningUnavailable) else 'schema_validation'
        causes = {'timeout':'Local planning reached its bounded inference deadline.',
                  'canceled':'Worker shutdown canceled local planning.',
                  'contention':'Maintenance or another local request is using the model.',
                  'provider_rejection':'The local provider rejected the request.',
                  'invalid_json':'The local provider returned invalid JSON.',
                  'schema_validation':'Planning output failed structured validation.',
                  'incomplete_output':'The local provider stopped before completing structured output.',
                  'transport':'The local model connection failed.',
                  'configuration':'The local planning deadline configuration is invalid.'}
        causes['planning_permission'] = 'No model was contacted. The saved report needs read-only investigation permission; repeating this legacy planner cannot correct that.'
        cause = causes.get(category, 'Planning unavailable or invalid output.')
        job['payload']['diagnostics'] = {**getattr(adapter, 'diagnostics', {}), 'category':category}
        if isinstance(error, PlanningUnavailable):
            job['payload']['diagnostics'].update(error.metadata)
        failure = cause+' Original input and any applied approval remain saved. Retry explicitly; no paid fallback.'
    with write_transaction(engine) as connection:
        current = intake.require(connection, planning_jobs, job['id'])
        if current['status'] != 'running' or current['lease'] != job['lease']:
            return True  # A recovered lease owns the result; discard this late response.
        row = intake.require(connection, intakes, job['intake_id'])
        if (job['payload'].get('scope_revision', 0) != intake.human_scope(connection, row['id'])['revision']
                or (job['kind'] == 'planning' and row['revision'] >= job['revision'])):
            connection.execute(planning_jobs.update().where(planning_jobs.c.id == job['id']).values(
                status='failed', payload=job['payload'], message='Human scope or proposal changed during planning. Result discarded; current review preserved.',
                finished_at=now(), lease=None, lease_until=None))
            finish_attempt(connection, job, adapter, model, basis, 'superseded')
            return True
        state = 'decomposition_failed' if job['kind'] == 'decomposition' else 'failed'
        payload = dict(job['payload'])
        if not failure and getattr(adapter, 'diagnostics', None):
            payload['diagnostics'] = {**adapter.diagnostics, 'category':'validated'}
        if not failure:
            if proposal:
                approval = intake.require(connection, proposal_decisions, job['payload']['approval_id'])
                try:
                    intake.validate_approval(connection, approval)
                except HTTPException:
                    failure = 'Approved proposal, human scope or required artifacts changed. No tasks were created.'
                    state = 'scope_review_required'
                    connection.execute(proposal_decisions.update().where(proposal_decisions.c.id == approval['id']).values(scope_review_required=1))
                if not failure:
                    root_id = 'intake-'+row['id']
                    if not connection.execute(select(backlog_items.c.id).where(backlog_items.c.id == root_id)).first():
                        connection.execute(backlog_items.insert().values(id=root_id, intake_id=row['id'], proposal_id=proposal['id'],
                            parent=None, kind='request', title=row['payload']['title'], purpose=proposal['content']['summary'],
                            acceptance=proposal['content']['acceptance'], model=model, created_at=now()))
                    identities = {task.key: root_id+'-'+str(proposal['revision'])+'-'+task.key for task in parsed.tasks}
                    # Insert parents first; dependency writes follow all items.
                    pending = list(parsed.tasks)
                    while pending:
                        for task in pending[:]:
                            if task.parent and any(t.key == task.parent for t in pending):
                                continue
                            identity = identities[task.key]
                            if not connection.execute(select(backlog_items.c.id).where(backlog_items.c.id == identity)).first():
                                connection.execute(backlog_items.insert().values(id=identity, intake_id=row['id'], proposal_id=proposal['id'],
                                    parent=identities[task.parent] if task.parent else root_id, kind=task.kind,
                                    title=task.title, purpose=task.purpose, acceptance=task.acceptance, model=model, created_at=now()))
                            pending.remove(task)
                    for task in parsed.tasks:
                        for dependency in task.depends_on:
                            connection.execute(backlog_dependencies.insert().prefix_with('OR IGNORE').values(
                                item_id=identities[task.key], requires_id=identities[dependency]))
                    payload['decomposition'] = parsed.model_dump(mode='json')
                    state = 'tasks_ready'
            else:
                payload.update(result=result, evidence=evidence, basis=basis)
                state = 'waiting_for_information' if parsed.clarification else 'completed'
                if parsed.proposal:
                    connection.execute(proposals.insert().values(id=str(uuid4()), intake_id=row['id'], revision=job['revision'],
                        scope_revision=job['payload'].get('scope_revision', 0), content=result['proposal'], model=model, basis=basis, created_at=now()))
                elif not parsed.clarification:
                    # Triage task is actionable research, never diagnosed implementation.
                    identity = 'intake-'+row['id']
                    if not connection.execute(select(backlog_items.c.id).where(backlog_items.c.id == identity)).first():
                        connection.execute(backlog_items.insert().values(id=identity, intake_id=row['id'], proposal_id=None,
                            parent=None, kind='task', title='Investigate: '+row['payload']['title'],
                            purpose='Reproduce the reported behavior; root cause and duplicates remain unverified.',
                            acceptance=['Record reproduction steps and expected versus observed behavior.', 'Inspect relevant addon evidence before proposing a fix.'],
                            model=model, created_at=now()))
                connection.execute(intakes.update().where(intakes.c.id == row['id']).values(revision=job['revision']))
        connection.execute(intakes.update().where(intakes.c.id == row['id']).values(status=state))
        connection.execute(planning_jobs.update().where(planning_jobs.c.id == job['id']).values(
            status='failed' if failure else 'completed', payload=payload, model=model,
            message=failure or ('Demo response completed; no observed model research.' if basis=='demo' else 'Local planning completed using the displayed bounded evidence; addon findings remain unverified.'),
            finished_at=now(), lease=None, lease_until=None))
        finish_attempt(connection, job, adapter, model, basis, 'failed' if failure else 'completed')
        if fail_before_commit:
            raise RuntimeError('Simulated result loss before commit')
    return True


def finish_attempt(connection, job, adapter, model, basis, outcome):
    from workbench.codex_provider import Planner
    from workbench.planning_provider import DemoProvider, LocalProvider
    diagnostics = getattr(adapter, 'diagnostics', {})
    is_demo = isinstance(adapter, DemoProvider) or basis == 'demo'
    is_local = isinstance(adapter, LocalProvider) or basis == 'local'
    is_claude = getattr(adapter,'backend',None)=='claude' or basis=='claude'
    is_codex = (isinstance(adapter, Planner) and not is_claude) or basis == 'codex'
    observed_usage = diagnostics.get('usage')
    smart=bool(getattr(adapter,'strategy_control',False))
    if smart:
        from workbench.strategy_accounting import combine
        observed_usage=combine(observed_usage,job['payload'])
    if is_claude and observed_usage is None and isinstance(diagnostics.get('native_known_subtotal'),dict):
        prior=json.loads(diagnostics.get('known_usage') or '{}')
        subtotal=diagnostics['native_known_subtotal']
        known={k:prior.get(k,0)+subtotal.get(k,0) for k in ('prompt_tokens','completion_tokens','total_tokens')}
        if all(type(v)is int and v>=0 for v in known.values()) and known['prompt_tokens']+known['completion_tokens']==known['total_tokens']:
            observed_usage=dict(source='Native Claude Code observed subtotal; final consumption unavailable',
                status='partial',units='tokens',values=None,known_values=known)
    if (is_codex or is_claude) and diagnostics.get('generation_state') == 'not_started' and observed_usage is None:
        # A durable application claim precedes context assembly and transport.
        # This is a known non-dispatch, never a zero substituted for uncertain usage.
        observed_usage = dict(source='Workbench verified no '+('Claude' if is_claude else 'Codex')+' turn started', units='tokens',
            status='not_started', requests_observed=0,
            values=dict(prompt_tokens=0,completion_tokens=0,reasoning_tokens=0,total_tokens=0))
    requests=(observed_usage or {}).get('strategy_requests',[]) if smart else []
    models={r['model'] for r in requests}
    providers={r['provider'] for r in requests}
    selected_model=next(iter(models)) if len(models)==1 else None
    selected_provider=next(iter(providers)) if len(providers)==1 else 'smart'
    connection.execute(model_attempts.update().where(model_attempts.c.id == job['lease']).values(
        source='strategy-control' if getattr(adapter,'strategy_control',False) else 'demo' if is_demo else 'workbench' if is_local or is_codex or is_claude else 'unknown',
        backend=selected_provider if smart else 'claude' if is_claude else 'codex' if is_codex else 'local' if is_local else None,
        requested_model=selected_model if smart else func.coalesce(model_attempts.c.requested_model, diagnostics.get('configured_model')),
        actual_model=selected_model if smart else diagnostics.get('actual_model') or model,
        finished_at=now(), outcome=outcome, usage=observed_usage))
