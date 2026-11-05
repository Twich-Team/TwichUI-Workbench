"""One explicit owner scope/budget decision; runtime certification remains separate."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path, PurePosixPath
import os
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, inspect

from workbench import real_execution as execution
from workbench.db import (
    task_run_offers as offers,
    proposal_decisions,
    backlog_items,
    real_execution_grants,
    write_transaction,
)
from workbench.visual_scope import digest
from workbench.schemas import TaskBudget


def valid_contract(contract):
    try:
        paths = contract["paths"]
        return (
            (
                (
                    contract["provider"] == "local"
                    and contract["model"] == "pilot-qwen38-27b"
                    and not contract.get("authoring")
                )
                or (
                    contract["provider"] == "current-session"
                    and contract["model"] == "codex-current-session"
                    and contract.get("authoring") == "explicit-current-session"
                )
            )
            and contract["limits"] in (execution.LIMITS, {**execution.LIMITS, "read_chars": 98304})
            and contract["decoding"] == execution.DECODING
            and contract["workflow"] == execution.WORKFLOW
            and contract["publication_allowed"] is False
            and contract["fallback_allowed"] is False
            and contract["repository"] == "Twich-Team/TwichUI"
            and len(contract["base"]) == 40
            and all(c in "0123456789abcdef" for c in contract["base"])
            and isinstance(paths, list)
            and 1 <= len(paths) <= 3
            and len(set(paths)) == len(paths)
            and all(
                isinstance(p, str)
                and p.endswith(".lua")
                and not PurePosixPath(p).is_absolute()
                and ".." not in PurePosixPath(p).parts
                and not any(x.startswith(".") for x in PurePosixPath(p).parts)
                and p.startswith(("!!!TwichUI/", "TwichUI_Settings/", "tests/test_"))
                for p in paths
            )
            and isinstance(contract["task_id"], str)
            and 1 <= len(contract["task_id"]) <= 100
        )
    except KeyError, TypeError, ValueError:
        return False


def register_offer(engine, proposal_id, specification):
    """Trusted preparation only: no public registration, grant or human decision.

    The fixed recipe/data must be reviewed/tested before registration. A proposal
    can advertise this capability only for its exact current review fingerprint.
    """
    from workbench import intake

    from workbench.real_recipes import validate

    spec = validate(json.loads(json.dumps(specification, allow_nan=False)))
    if spec.get('verification_profile'):
        raise ValueError('Profile recipes require a current reviewed contract and certified evidence')
    if (
        not valid_contract(spec.get("contract", {}))
        or spec.get("base_branch") != "main"
        or spec.get("draft") is not True
        or len(json.dumps(spec).encode()) > 65536
    ):
        raise ValueError("Bounded fixed local recipe and verified destination required")
    with write_transaction(engine) as c:
        proposal = intake.require(c, intake.proposals, proposal_id)
        review = intake.proposal_view(c, proposal)
        if review.get('task_contract'):
            raise ValueError('Reviewed Lua contracts require certified profile offers')
        if (
            review["approval_blockers"]
            or review["human_scope"]["visual_requirement"] != "nonvisual"
        ):
            raise ValueError("Only an eligible nonvisual proposal may advertise this recipe")
        snapshot = dict(
            proposal_id=proposal_id, review_binding=review["binding"], specification=spec
        )
        fingerprint = digest(snapshot)
        old = (
            c.execute(select(offers).where(offers.c.fingerprint == fingerprint)).mappings().first()
        )
        if old:
            return dict(old)
        row = dict(
            id=str(uuid4()),
            proposal_id=proposal_id,
            fingerprint=fingerprint,
            snapshot=snapshot,
            created_at=execution.now(),
        )
        c.execute(offers.insert().values(**row))
        return row


def offer(c, proposal_id):
    if not inspect(c).has_table(offers.name):
        return None
    from workbench import intake

    try:
        proposal = intake.require(c, intake.proposals, proposal_id)
        review = intake.proposal_view(c, proposal)
    except (ValueError, KeyError, TypeError, HTTPException):
        return None
    for row in c.execute(
        select(offers)
        .where(offers.c.proposal_id == proposal_id)
        .order_by(offers.c.created_at.desc(), offers.c.id.desc())
    ).mappings():
        try:
            saved = row['snapshot']
            if (
                row["fingerprint"] == digest(saved)
                and saved["review_binding"] == review["binding"]
                and (not review.get('task_contract') or saved['specification'].get('verification_profile'))
                and certified_offer_current(c, row)
            ):
                return dict(row)
            if saved['specification'].get('verification_profile') or 'certified_preparation' in saved:
                return None  # A changed newest preparation cannot resurrect an older binding.
        except (KeyError, TypeError, ValueError):
            return None
    return None


def _certified_preparation(runtime):
    """Re-evaluate immutable task evidence; a certificate marker alone cannot pass."""
    from workbench.verification_profiles import certify
    from workbench.real_workspace import sha

    root = runtime.root
    if any(p.is_symlink() for p in (root, *root.parents)) or root.stat().st_uid != os.getuid():
        raise ValueError('Owned certified preparation root required')
    for name, ceiling in [('profile-evidence.json', 8 << 20), ('controls-passed.json', 65536)]:
        path = root / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > ceiling:
            raise ValueError('Missing or unbounded certified evidence')
    bundle = json.loads((root / 'profile-evidence.json').read_text())
    certificate = certify(runtime, bundle)
    if certificate['status'] != 'certified' or not runtime.controls_valid():
        raise ValueError('Missing, mismatched or stale task-specific certification')
    return dict(root=str(root), certificate=certificate,
                marker_sha256=sha((root / 'controls-passed.json').read_bytes()),
                evidence_sha256=sha((root / 'profile-evidence.json').read_bytes()))


def _reviewed_spec(c, ref, runtime):
    from workbench.real_recipes import from_review

    spec = runtime.recipe
    actual = from_review(c, ref, oracle=runtime.oracle_source.read_text(),
                        behavior_id=spec['behavior']['id'], read_paths=spec['read_paths'],
                        focused_test=spec['focused_test'], identity=spec['identity'])
    if actual != spec:
        raise ValueError('Certified recipe differs from the current reviewed contract')
    return actual


def register_reviewed_offer(engine, ref, runtime):
    """Existing offer store; certification/registration never creates permission."""
    from workbench.task_execution import snapshot as task_snapshot
    from workbench.real_workspace import Runtime

    runtime = Runtime(runtime.root)  # Reconstruct canonical checks; caller methods cannot certify.
    prepared = _certified_preparation(runtime)
    with write_transaction(engine) as c:
        spec = _reviewed_spec(c, ref, runtime)
        linked = task_snapshot(c, **ref)
        snapshot = dict(proposal_id=linked['task']['proposal_id'],
                        review_binding=linked['proposal_review']['binding'], specification=spec,
                        reviewed_task=dict(ref=ref, binding=digest(linked)),
                        certified_preparation=prepared)
        fingerprint = digest(snapshot)
        old = c.execute(select(offers).where(offers.c.fingerprint == fingerprint)).mappings().first()
        if old:
            return dict(old)
        row = dict(id=str(uuid4()), proposal_id=snapshot['proposal_id'], fingerprint=fingerprint,
                   snapshot=snapshot, created_at=execution.now())
        c.execute(offers.insert().values(**row))
        return row


def certified_offer_current(c, row):
    """Fail closed for changed review, decomposition, source or certified files."""
    try:
        spec = row['snapshot']['specification']
        if not spec.get('verification_profile'):
            return 'certified_preparation' not in row['snapshot']
        from workbench.real_workspace import Runtime
        from workbench.task_execution import snapshot as task_snapshot

        saved = row['snapshot']
        selected = saved['reviewed_task']
        linked = task_snapshot(c, **selected['ref'])
        if digest(linked) != selected['binding'] or linked['task']['proposal_id'] != row['proposal_id']:
            return False
        runtime = Runtime(saved['certified_preparation']['root'])
        return (_reviewed_spec(c, selected['ref'], runtime) == spec
                and _certified_preparation(runtime) == saved['certified_preparation'])
    except (OSError, ValueError, KeyError, TypeError, HTTPException):
        return False


def preparation_inputs(c, selected):
    """Runtime preparation consumes offer data without a supported-task template."""
    if (selected['fingerprint'] != digest(selected['snapshot'])
            or offer(c, selected['proposal_id']) != selected):
        raise ValueError('Certified offer inputs changed')
    saved = selected['snapshot']
    root = Path(saved['certified_preparation']['root'])
    profile = saved['specification']['controls']
    return dict(recipe=saved['specification'], oracle=(root / 'recipe-oracle.lua').read_text(),
                control_patches={name: (root / 'profile-evidence' / name / 'input.patch').read_text()
                                 for name in profile if name != 'original'})


def validate_selection(c, proposal_id, budget):
    selected = offer(c, proposal_id)
    if (
        not selected
        or selected["id"] != str(budget.offer_id)
        or selected["fingerprint"] != budget.offer_fingerprint
    ):
        raise HTTPException(409, "The supported task or budget offer changed. Review it again.")
    if budget.activate_once:
        from workbench.task_sessions import capability

        if not capability(c.engine):
            raise HTTPException(
                409, "The one-off runtime is not configured for this store. No model starts."
            )
    return selected


def permission(c, approval, *, require_current=True):
    raw = approval["payload"].get("run_budget")
    if not raw:
        return None
    budget = TaskBudget.model_validate(raw)
    if require_current and budget.activate_once:
        from workbench.db import task_sessions

        if inspect(c).has_table(task_sessions.name):
            session = (
                c.execute(select(task_sessions).where(task_sessions.c.id == approval["id"]))
                .mappings()
                .first()
            )
            if session and (
                session["state"].get("cancel_requested")
                or session["status"] in {"cancelled", "expired"}
            ):
                raise ValueError(
                    "This one-off task run was stopped or expired. Saved scope and history remain; no execution or publication starts."
                )
    if require_current:
        selected = validate_selection(c, approval["proposal_id"], budget)
    else:
        # Historical outcome provenance must survive a newer explicit budget.
        # This branch grants no current execution/publication permission.
        selected = (
            c.execute(select(offers).where(offers.c.id == str(budget.offer_id))).mappings().first()
        )
        if (
            not selected
            or selected["proposal_id"] != approval["proposal_id"]
            or selected["fingerprint"] != budget.offer_fingerprint
            or digest(selected["snapshot"]) != selected["fingerprint"]
        ):
            raise ValueError("Historical budget offer identity is unavailable")
        selected = dict(selected)
    expires = datetime.fromisoformat(approval["created_at"]) + timedelta(
        seconds=budget.overall_seconds
    )
    if (
        approval["status"] != "applied"
        or not approval["finished_at"]
        or approval["payload"]["action"] != "approve"
        or approval["scope_review_required"]
        or (require_current and datetime.now(timezone.utc) >= expires)
    ):
        raise ValueError("The explicit task budget is not current")
    return dict(
        decision_id=approval["id"],
        budget=budget.model_dump(mode="json"),
        offer=selected,
        expires_at=expires.isoformat(),
    )


def context(engine, intake_id, proposal_id):
    from workbench import intake

    with engine.connect() as c:
        proposal = intake.require(c, intake.proposals, proposal_id)
        if proposal["intake_id"] != intake_id:
            raise HTTPException(404, "Proposal not found")
        selected = offer(c, proposal_id)
        return offer_view(c, proposal, selected)


def offer_view(c, proposal, selected):
    """Display only the selected owner's snapshot; never combine two read revisions."""
    from workbench import intake
    if not selected:
        return dict(
            available=False,
            message=preparation_hold(c, proposal),
            offer=None,
        )
    spec = selected["snapshot"]["specification"]
    contract = spec["contract"]
    from workbench.task_sessions import capability

    one_off = capability(c.engine) is not None
    return dict(
        runtime_ready=one_off,
        available=True,
        message=("One-off runtime is configured; the worker must still pass current preparation and operational gates." if one_off else "Execution unavailable on this installation. Approval can save the exact budget, but no run starts."),
        offer=dict(
            id=selected["id"],
            fingerprint=selected["fingerprint"],
            title=contract["title"],
            repository=contract["repository"],
            base_branch=spec["base_branch"],
            base=contract["base"],
            paths=contract["paths"],
            checks=(selected['snapshot'].get('reviewed_task') and
                    intake.proposal_view(c, proposal)['task_contract']['verification_commands']) or [],
            limits=contract["limits"],
            budget=TaskBudget(
                offer_id=selected["id"],
                offer_fingerprint=selected["fingerprint"],
                publication="draft-pr",
                activate_once=one_off,
            ).model_dump(mode="json"),
        ),
    )


def preparation_hold(c, proposal):
    from workbench.regression_preparation import contract_for
    from workbench.db import planning_jobs
    from workbench.intake import proposal_view
    contract = contract_for(c, proposal)
    if contract and contract.get('scenario_grounding'):
        from workbench.trusted_preparation import validate_reviewed_policy
        from workbench.regression_preparation import repository
        try:
            validate_reviewed_policy(c.engine, contract)
            if repository()[1] != contract['resolved_base']:
                return 'Preparation held: committed source changed from the reviewed scenario base. Request a fresh grounded proposal.'
        except (ValueError, OSError) as error:
            return 'Preparation held: ' + str(error)
    if contract and contract['scope_missing']:
        return 'Preparation held: ' + ' '.join(contract['scope_missing'])
    prepared = (contract or {}).get('behavioral_evidence')
    if prepared:
        payload = c.execute(select(planning_jobs.c.payload).where(planning_jobs.c.intake_id == proposal['intake_id'],
            planning_jobs.c.revision == proposal['revision'], planning_jobs.c.kind == 'planning',
            planning_jobs.c.status == 'completed')).scalar() or {}
        outcome = payload.get('behavioral_preparation_outcome') or {}
        if (outcome.get('status') == 'hold' and outcome.get('review_binding') == proposal_view(c, proposal)['binding']):
            return 'Last behavioral preparation held: ' + outcome['message']
        # Current application preparation is journaled in the existing job owner.
        # Read it directly so a lost diagnostic projection cannot hide a failure.
        from workbench.db import proposal_decisions
        binding = proposal_view(c, proposal)['binding']
        preparation = c.execute(select(planning_jobs).join(proposal_decisions,
            planning_jobs.c.payload['ref']['approval_id'].as_string() == proposal_decisions.c.id).where(
            planning_jobs.c.intake_id == proposal['intake_id'], planning_jobs.c.revision == proposal['revision'],
            planning_jobs.c.kind == 'behavioral_preparation', proposal_decisions.c.binding == binding,
            proposal_decisions.c.status == 'applied')).mappings().first()
        if preparation:
            if preparation['status'] == 'failed':
                return 'Behavioral preparation held: ' + preparation['message']
            if preparation['status'] in {'queued', 'running'}:
                return ('Reviewed scenarios are queued for the application worker. No further preparation command is required.'
                        if preparation['status'] == 'queued' else 'The application worker is preparing reviewed behavioral controls. Certification is not complete.')
        if prepared['holds']:
            return 'Behavioral preparation held: ' + ' '.join(prepared['holds'])
        return 'Behavioral examples require independent expectation review and fresh positive, vacuous and wrong-behavior control checks at the reviewed base. No certified offer exists yet.'
    return 'Preparation held: independently certified evidence is missing, stale, or no longer matches the current reviewed scope/base. A trusted operator must prepare and certify it.'


def run_context(engine, intake_id, task_id):
    """Current offer and exact scope for a first browser budget after scope review."""
    from workbench import intake, task_execution

    with engine.connect() as c:
        c.exec_driver_sql('BEGIN')
        task = intake.require(c, backlog_items, task_id)
        if task['intake_id'] != intake_id or task['kind'] != 'task':
            raise HTTPException(404, 'Task not found for this request')
        selected = offer(c, task['proposal_id'])
        saved = c.execute(select(proposal_decisions).where(
            proposal_decisions.c.proposal_id == task['proposal_id'],
            proposal_decisions.c.payload['run_budget']['offer_id'].as_string().is_not(None),
        ).order_by(proposal_decisions.c.created_at.desc()).limit(1)).mappings().first()
        receipt = intake.receipt_view(saved) if saved else None
        if not selected or 'reviewed_task' not in selected['snapshot']:
            proposal = intake.require(c, intake.proposals, task['proposal_id'])
            return dict(available=False, message=preparation_hold(c, proposal), offer=None, receipt=receipt)
        ref = selected['snapshot']['reviewed_task']['ref']
        if ref['intake_id'] != intake_id or ref['task_id'] != task_id:
            raise HTTPException(409, 'Offer is bound to another task')
        linked = task_execution.snapshot(c, **ref)
        binding = digest(dict(scope=linked, offer=selected['fingerprint']))
        budget_message = None
        if saved and saved['status'] == 'applied':
            try:
                permission(c, saved)
            except (ValueError, HTTPException) as error:
                detail = error.detail if isinstance(error, HTTPException) else str(error)
                budget_message = 'Saved approval no longer authorizes execution: ' + str(detail)
        proposal = intake.require(c, intake.proposals, task['proposal_id'])
        result = offer_view(c, proposal, selected)
    if budget_message:
        result['message'] = budget_message
    return dict(**result, scope_approval_id=ref['approval_id'], binding=binding,
                scope=linked['proposal_review'], receipt=receipt)


def approve_run(engine, intake_id, task_id, body):
    """One explicit browser decision, through the existing durable approval owner.

    Scope is reaffirmed without rewriting its applied decision/decomposition.
    First budget only: consumed attempts never gain a fresh run through this UI.
    """
    from workbench import intake, task_execution

    request = body.model_dump(mode='json')
    with write_transaction(engine) as c:
        old = c.execute(select(proposal_decisions).where(
            proposal_decisions.c.id == str(body.command_id))).mappings().first()
        if old:
            if (old['intake_id'] != intake_id or
                    old['payload'].get('execution_continuation', {}).get('browser_request') !=
                    dict(task_id=task_id, **request)):
                raise HTTPException(409, 'This command ID already belongs to another decision')
            return intake.receipt_view(old)
        ref = dict(intake_id=intake_id, task_id=task_id, approval_id=str(body.scope_approval_id))
        linked = task_execution.snapshot(c, **ref)
        parent = intake.require(c, proposal_decisions, ref['approval_id'])
        if parent['payload'].get('execution_continuation') or parent['payload'].get('run_budget'):
            raise HTTPException(409, 'A first scope-only approval is required; earlier budgets cannot be reset')
        selected = validate_selection(c, parent['proposal_id'], body.run_budget)
        if (selected['snapshot'].get('reviewed_task', {}).get('ref') != ref or
                body.binding != digest(dict(scope=linked, offer=selected['fingerprint']))):
            raise HTTPException(409, 'Scope, certification or offer changed. Review the current task again.')
        for row in c.execute(select(proposal_decisions).where(
                proposal_decisions.c.proposal_id == parent['proposal_id'])).mappings():
            if row['payload'].get('run_budget'):
                raise HTTPException(409, 'A task budget is already saved. Refresh its receipt; no new run was created.')
        review = linked['proposal_review']
        continuation = dict(scope_approval_id=parent['id'], scope_binding=parent['binding'],
                            owner_provenance=dict(actor='owner', purpose='browser-reviewed-offer-v1',
                                                  command_id=str(body.command_id)),
                            browser_request=dict(task_id=task_id, **request))
        budget = body.run_budget.model_dump(mode='json')
        binding = digest(dict(execution_continuation=continuation, run_budget=budget))
        row = dict(id=str(body.command_id), intake_id=intake_id, proposal_id=parent['proposal_id'],
                   binding=binding, scope_revision=review['human_scope']['revision'],
                   required_artifacts=review['required_artifacts'], scope_review_required=0,
                   payload=dict(command_id=str(body.command_id), proposal_id=parent['proposal_id'],
                                binding=binding, scope_revision=review['human_scope']['revision'],
                                action='approve', note=body.note, run_budget=budget,
                                execution_continuation=continuation), snapshot=review,
                   status='pending', message='Exact scope and one task/draft-PR budget saved · Waiting for application.',
                   created_at=execution.now(), finished_at=None)
        c.execute(proposal_decisions.insert().values(**row))
        return intake.receipt_view(row)


def authorize_one(engine, runtime):
    """Same worker, prepared certified runtime; no lease/model activation here."""
    selected = getattr(runtime, "task_ref", None)
    if not selected:
        return False
    with engine.connect() as c:
        approval = (
            c.execute(
                select(proposal_decisions).where(proposal_decisions.c.id == selected["approval_id"])
            )
            .mappings()
            .first()
        )
        if not approval:
            return False
        try:
            policy = permission(c, approval)
            if not policy:
                return False
            from workbench.task_execution import snapshot

            linked = snapshot(
                c, selected["intake_id"], selected["task_id"], selected["approval_id"]
            )
            if linked["dependencies"]:
                return False
            if (
                len(
                    c.execute(
                        select(backlog_items.c.id).where(
                            backlog_items.c.proposal_id == approval["proposal_id"],
                            backlog_items.c.kind == "task",
                        )
                    ).all()
                )
                != 1
            ):
                return False
            if c.execute(
                select(real_execution_grants.c.id).where(
                    real_execution_grants.c.snapshot["task_budget"]["decision_id"].as_string()
                    == approval["id"]
                )
            ).first():
                return False
            contract = getattr(runtime, "execution_contract", execution.contract())
            if (
                contract != policy["offer"]["snapshot"]["specification"]["contract"]
                or getattr(runtime, "recipe", None) != policy["offer"]["snapshot"]["specification"]
            ):
                return False
        except ValueError, HTTPException, KeyError:
            return False
    old = getattr(runtime, "grant_deadline", None)
    expiry = datetime.fromisoformat(policy["expires_at"])
    runtime.grant_deadline = min(old, expiry) if old else expiry
    authorization = json.dumps(
        dict(owner_decision=approval["id"], task_id=selected["task_id"], task_budget=policy),
        sort_keys=True,
    )
    try:
        execution.issue(
            engine,
            authorization,
            "explicit Workbench scope and run-budget decision " + approval["id"],
            runtime,
            task=selected,
        )
        return True
    except ValueError:
        return False
