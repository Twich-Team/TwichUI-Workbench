"""Trusted one-session owner instruction ingress. Never called by a model or public API."""

import hashlib
from pathlib import Path
import os
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select

from workbench import intake
from workbench.db import (
    intakes,
    intake_scopes,
    planning_jobs,
    proposals,
    proposal_decisions,
    backlog_items,
    write_transaction,
)
from workbench.schemas import IntakeInput, ProposalContent
from workbench.planning_worker import validate_decomposition
from workbench.service import now


def instruction(path, expected_hash, purpose="connected-chronicle-acceptance-v1"):
    if purpose not in {"connected-chronicle-acceptance-v1", "supported-task-to-draft-pr-v1"}:
        raise ValueError("Unsupported owner ingress purpose")
    p = Path(path)
    if (
        p.is_symlink()
        or not p.is_file()
        or p.stat().st_uid != os.getuid()
        or not 1 <= p.stat().st_size <= 65536
    ):
        raise ValueError("Owned bounded owner instruction required")
    data = p.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected_hash:
        raise ValueError("Owner instruction identity changed")
    data.decode("utf-8")
    return dict(
        authorization_sha256=expected_hash,
        authorization_path=str(p.resolve()),
        actor="owner",
        purpose=purpose,
    )


def record(engine, path, expected_hash):
    """Record the exact expressly authorized replay scope, not an inferred human choice.

    Creates canonical owner-provided planning/decomposition, not model attempts.
    The caller must already hold owner permission to record this exact scope.
    No grant, provider, source checkout, tool, lease or execution is invoked here.
    """
    proof = instruction(path, expected_hash)
    identity = str(uuid5(NAMESPACE_URL, "workbench-connected-owner:" + expected_hash))
    ids = {
        k: str(uuid5(NAMESPACE_URL, identity + ":" + k))
        for k in ["proposal", "approval", "planning", "decomposition"]
    }
    task_id = "intake-" + identity + "-1-replay"
    ref = dict(intake_id=identity, task_id=task_id, approval_id=ids["approval"])
    original = IntakeInput(
        request_id=identity,
        kind="feature",
        title="Connected workflow acceptance · Chronicle replay",
        description="Owner-authorized acceptance replay against the original faulty pinned base. "
        "Invalid measurements must be unavailable to waiting callbacks; valid measurements, storage safety, "
        "request cleanup, chat restoration and later valid requests must retain their behavior. "
        "Deliver one independently checked model-authored candidate through this task. "
        "The accepted Chronicle fix and all live game files remain unchanged.",
        visual_requirement="nonvisual",
    ).model_dump(mode="json")
    original.pop("supported_task", None)
    content = ProposalContent(
        summary=original["title"],
        value="Demonstrate the connected real-model task and verified-result delivery path.",
        feasibility="Uses the existing fixed Chronicle recipe at its original faulty pinned base.",
        uncertainty=[
            "This acceptance replay does not establish general or recurring unattended operation."
        ],
        evidence_ids=[],
        behavior=[original["description"]],
        scope=[
            "Implement the original Chronicle invalid-measurement behavior in the approved isolated paths."
        ],
        exclusions=[
            "Accepted candidate, live game files, publication, paid fallback, unrelated paths and recurring execution."
        ],
        acceptance=[
            "Invalid measurements reach waiting callbacks as unavailable; valid measurements and cleanup retain intended behavior.",
            "Frozen independent oracle, focused checks, complete applicable suite and artifact/scope integrity pass; result stays linked to this task.",
        ],
        dependencies=[],
        risks=["One attempt only; an incorrect candidate remains failed."],
        stages=[
            "Read and implement in isolation.",
            "Run independent checks and save a frozen result.",
        ],
        visual="nonvisual",
        delivery="implementation",
    ).model_dump(mode="json")
    decomposition = validate_decomposition(
        dict(
            tasks=[
                dict(
                    key="replay",
                    kind="task",
                    parent=None,
                    title="Chronicle connected acceptance replay",
                    purpose=original["description"],
                    acceptance=content["acceptance"],
                    depends_on=[],
                    scope_indices=[0],
                )
            ]
        ),
        content,
    ).model_dump(mode="json")
    with write_transaction(engine) as c:
        old = c.execute(select(intakes).where(intakes.c.id == identity)).mappings().first()
        if old:
            from workbench.task_execution import snapshot

            saved = snapshot(c, identity, task_id, ids["approval"])
            if (
                old["payload"] != original
                or saved["decomposition"]["payload"].get("owner_provenance") != proof
            ):
                raise ValueError("Owner task inputs differ; no overwrite or new decision")
            return ref
        at = now()
        c.execute(
            intakes.insert().values(
                id=identity,
                payload=original,
                status="tasks_ready",
                revision=1,
                created_at=at,
                priority_override=None,
            )
        )
        c.execute(
            intake_scopes.insert().values(
                intake_id=identity,
                revision=1,
                command_id=identity,
                visual_requirement="nonvisual",
                stage="implementation",
                origin="human",
                note="Exact owner-authorized acceptance scope; " + expected_hash,
                change_payload=original,
                created_at=at,
            )
        )
        c.execute(
            proposals.insert().values(
                id=ids["proposal"],
                intake_id=identity,
                revision=1,
                scope_revision=1,
                content=content,
                model="owner-supplied",
                basis="owner",
                created_at=at,
            )
        )
        proposal = intake.require(c, proposals, ids["proposal"])
        review = intake.proposal_view(c, proposal)
        intake.validate_review(c, proposal, review, 1, [])
        payload = dict(
            command_id=ids["approval"],
            proposal_id=ids["proposal"],
            binding=review["binding"],
            scope_revision=1,
            action="approve",
            note="Recorded from the explicit owner authorization "
            + expected_hash
            + " at "
            + proof["authorization_path"],
        )
        c.execute(
            proposal_decisions.insert().values(
                id=ids["approval"],
                intake_id=identity,
                proposal_id=ids["proposal"],
                scope_revision=1,
                required_artifacts=[],
                scope_review_required=0,
                binding=review["binding"],
                payload=payload,
                snapshot=review,
                status="applied",
                message="Owner-provided scope approval recorded. Execution still requires its separate scoped grant.",
                created_at=at,
                finished_at=at,
            )
        )
        intake.validate_approval(c, intake.require(c, proposal_decisions, ids["approval"]))
        base = dict(
            intake_id=identity,
            revision=1,
            status="completed",
            attempts=0,
            lease=None,
            lease_until=None,
            message="Owner-provided plan recorded. No planning model was invoked.",
            model="owner-supplied",
            created_at=at,
            finished_at=at,
        )
        c.execute(
            planning_jobs.insert().values(
                id=ids["planning"],
                kind="planning",
                payload=dict(
                    scope_revision=1,
                    owner_provenance=proof,
                    result=dict(
                        summary=content["summary"],
                        clarification="",
                        evidence_ids=[],
                        related_ids=[],
                        proposal=content,
                    ),
                    basis="owner",
                    evidence=[],
                ),
                **base,
            )
        )
        c.execute(
            planning_jobs.insert().values(
                id=ids["decomposition"],
                kind="decomposition",
                payload=dict(
                    scope_revision=1,
                    proposal_id=ids["proposal"],
                    approval_id=ids["approval"],
                    binding=review["binding"],
                    decomposition=decomposition,
                    owner_provenance=proof,
                ),
                **base,
            )
        )
        root = "intake-" + identity
        c.execute(
            backlog_items.insert().values(
                id=root,
                intake_id=identity,
                proposal_id=ids["proposal"],
                parent=None,
                kind="request",
                title=original["title"],
                purpose=content["summary"],
                acceptance=content["acceptance"],
                model="owner-supplied",
                created_at=at,
            )
        )
        spec = decomposition["tasks"][0]
        c.execute(
            backlog_items.insert().values(
                id=task_id,
                intake_id=identity,
                proposal_id=ids["proposal"],
                parent=root,
                kind="task",
                title=spec["title"],
                purpose=spec["purpose"],
                acceptance=spec["acceptance"],
                model="owner-supplied",
                created_at=at,
            )
        )
        from workbench.task_execution import snapshot

        snapshot(c, identity, task_id, ids["approval"])
    return ref


def record_supported(engine, path, expected_hash, template_id, approved_contract):
    """Record express owner scope/budget through existing intake/decision/job owners.

    Trusted one-session entry point only. The caller must possess express owner
    authorization for this exact contract and one draft PR. The source hash and
    purpose remain visible; no GUI click or planning inference is fabricated.
    Creates no execution grant, request, lease, model call or publication effect.
    """
    from workbench import supported_tasks, task_budget, planning_worker
    from workbench.schemas import ProposalDecisionInput

    proof = instruction(path, expected_hash, "supported-task-to-draft-pr-v1")
    template = supported_tasks.load(template_id)
    if not template or template["recipe"]["contract"] != approved_contract:
        raise ValueError("Supported recipe differs from the exact owner-approved contract")
    identity = str(uuid5(NAMESPACE_URL, "workbench-supported-owner:" + expected_hash))
    approval_id = str(uuid5(NAMESPACE_URL, identity + ":scope-and-budget"))
    original = IntakeInput(
        request_id=identity,
        kind="feature",
        title=approved_contract["title"],
        description=(
            "Owner-authorized backlog task "
            + approved_contract["task_id"]
            + ". "
            + template["proposal"]["value"]
            + " Scope: "
            + ", ".join(approved_contract["paths"])
            + ". "
            "One independently verified local-model attempt and one draft PR. "
            "No production changes, fallback, recurring work, or automatic merge."
        ),
        visual_requirement="nonvisual",
        supported_task=template_id,
    )
    intake.submit(engine, original, owner_provenance=proof)

    def annotate():
        with write_transaction(engine) as c:
            for row in c.execute(
                select(planning_jobs).where(planning_jobs.c.intake_id == identity)
            ).mappings():
                payload = dict(row["payload"])
                if "owner_provenance" in payload:
                    if payload["owner_provenance"] != proof:
                        raise ValueError("Conflicting owner instruction provenance")
                elif row["status"] == "queued":
                    c.execute(
                        planning_jobs.update()
                        .where(planning_jobs.c.id == row["id"])
                        .values(payload={**payload, "owner_provenance": proof})
                    )
                else:
                    raise ValueError(
                        "Existing completed work lacks this owner provenance; no rewrite"
                    )

    annotate()
    supported_tasks.process_one(engine, intake_id=identity)
    data = intake.view(engine, identity)
    if len(data["proposals"]) != 1:
        raise ValueError("One current supported proposal required")
    proposal = data["proposals"][0]
    offer = task_budget.context(engine, identity, proposal["id"])
    if not offer["available"]:
        raise ValueError("Exact supported scope/budget offer is unavailable")
    decision = ProposalDecisionInput(
        command_id=approval_id,
        proposal_id=proposal["id"],
        binding=proposal["binding"],
        scope_revision=proposal["scope_revision"],
        action="approve",
        note="Explicit owner authorization recorded from source SHA256 "
        + expected_hash
        + "; not a browser action or inferred approval.",
        run_budget=offer["offer"]["budget"],
    )
    intake.decide(engine, identity, decision)
    planning_worker.apply_one(engine, command_id=approval_id)
    annotate()
    supported_tasks.process_one(engine, intake_id=identity)
    with engine.connect() as c:
        task = (
            c.execute(
                select(backlog_items).where(
                    backlog_items.c.intake_id == identity, backlog_items.c.kind == "task"
                )
            )
            .mappings()
            .one()
        )
        ref = dict(intake_id=identity, task_id=task["id"], approval_id=approval_id)
        from workbench.task_execution import snapshot

        snapshot(c, **ref)
    return ref


def continue_supported(
    engine, ref, path, expected_hash, recipe, *, continuation_id="", session_authoring=False
):
    """New EXPRESS owner budget for the same frozen scope/task; never reset history.

    Trusted ingress only, no public/model endpoint. Scope approval and completed
    decomposition remain byte-identical. A new immutable decision owns one fresh
    attempt. The enclosing bounded package caps all continuation decisions.
    """
    from workbench import task_budget, real_recipes, task_execution
    from workbench.schemas import TaskBudget

    proof = instruction(path, expected_hash, "supported-task-to-draft-pr-v1")
    recipe = real_recipes.validate(recipe)
    if not isinstance(continuation_id, str) or len(continuation_id) > 200:
        raise ValueError("Bounded continuation identity required")
    identity = str(
        uuid5(
            NAMESPACE_URL,
            "workbench-task-continuation:"
            + expected_hash
            + ":"
            + ref["task_id"]
            + ":"
            + continuation_id,
        )
    )
    with engine.connect() as c:
        old = intake.require(c, proposal_decisions, ref["approval_id"])
        linked = task_execution.snapshot(c, **ref)
        root_id = (
            old["payload"].get("execution_continuation", {}).get("scope_approval_id", old["id"])
        )
        previous = task_budget.permission(c, old, require_current=False)
        certified_offer = task_budget.offer(c, old['proposal_id']) if recipe.get('verification_profile') else None
        first_certified_budget = (
            previous is None and certified_offer is not None and not session_authoring
            and certified_offer['snapshot']['specification'] == recipe
            and certified_offer['snapshot']['reviewed_task']['ref'] == ref
        )
        compared_contract = dict(recipe["contract"])
        if session_authoring:
            from workbench.session_authorship import contract_allowed

            if not contract_allowed(compared_contract):
                raise ValueError("Explicit current-session authorship contract required")
            compared_contract.pop("authoring")
            compared_contract.update(provider="local", model="pilot-qwen38-27b")
        if not first_certified_budget and (
            not previous
            or {
                **compared_contract,
                "limits": {**compared_contract["limits"], "read_chars": 45000},
            }
            != {
                **previous["offer"]["snapshot"]["specification"]["contract"],
                "limits": {
                    **previous["offer"]["snapshot"]["specification"]["contract"]["limits"],
                    "read_chars": 45000,
                },
            }
            or recipe["oracle_sha256"]
            != previous["offer"]["snapshot"]["specification"]["oracle_sha256"]
            or recipe["controls"] != previous["offer"]["snapshot"]["specification"]["controls"]
        ):
            raise ValueError("Continuation cannot change approved scope, base, oracle or controls")
        from workbench import real_execution as execution

        if c.execute(
            select(execution.grants.c.id).where(
                execution.grants.c.snapshot["workflow_task"]["task"]["id"].as_string()
                == ref["task_id"],
                execution.grants.c.revoked_at.is_(None),
            )
        ).first():
            raise ValueError("Close the prior task grant before continuation")
        if c.execute(
            select(proposal_decisions.c.id).where(proposal_decisions.c.id == identity)
        ).first():
            existing = intake.require(c, proposal_decisions, identity)
            if (
                existing["payload"].get("execution_continuation", {}).get("owner_provenance")
                != proof
            ):
                raise ValueError("Conflicting continuation identity")
            return {**ref, "approval_id": identity}
        proposal_id = old["proposal_id"]
    offer = certified_offer if certified_offer else task_budget.register_offer(engine, proposal_id, recipe)
    budget = TaskBudget(
        offer_id=offer["id"], offer_fingerprint=offer["fingerprint"], publication="draft-pr"
    )
    with write_transaction(engine) as c:
        # No race with a scope change or newly issued grant may grant stale permission.
        if task_execution.snapshot(c, **ref) != linked:
            raise ValueError("Scope changed during continuation")
        if certified_offer and task_budget.offer(c, proposal_id) != certified_offer:
            raise ValueError('Certified offer changed during explicit budget authorization')
        at = now()
        review = linked["proposal_review"]
        continuation = dict(
            scope_approval_id=root_id,
            scope_binding=review["binding"],
            owner_provenance=proof,
            continuation_id=continuation_id,
            **({"authoring_mode": "explicit-current-session"} if session_authoring else {}),
        )
        binding = intake.digest(
            dict(execution_continuation=continuation, run_budget=budget.model_dump(mode="json"))
        )
        c.execute(
            proposal_decisions.insert().values(
                id=identity,
                intake_id=ref["intake_id"],
                proposal_id=proposal_id,
                scope_revision=review["human_scope"]["revision"],
                required_artifacts=review["required_artifacts"],
                scope_review_required=0,
                binding=binding,
                snapshot=review,
                status="applied",
                payload=dict(
                    command_id=identity,
                    proposal_id=proposal_id,
                    binding=binding,
                    scope_revision=review["human_scope"]["revision"],
                    action="approve",
                    note="New express owner execution budget; original scope/task and all prior failed attempts retained. Not a browser action. Source "
                    + expected_hash,
                    run_budget=budget.model_dump(mode="json"),
                    execution_continuation=continuation,
                ),
                message="New owner-authorized task budget recorded. Waiting for certified execution; previous outcomes unchanged.",
                created_at=at,
                finished_at=at,
            )
        )
        result = {**ref, "approval_id": identity}
        task_execution.snapshot(c, **result)
    return result
