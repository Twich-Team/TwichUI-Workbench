"""One explicit owner scope/budget decision; runtime certification remains separate."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import PurePosixPath
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

    proposal = intake.require(c, intake.proposals, proposal_id)
    review = intake.proposal_view(c, proposal)
    for row in c.execute(
        select(offers)
        .where(offers.c.proposal_id == proposal_id)
        .order_by(offers.c.created_at.desc())
    ).mappings():
        if (
            row["fingerprint"] == digest(row["snapshot"])
            and row["snapshot"]["review_binding"] == review["binding"]
        ):
            return dict(row)
    return None


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
        if not selected:
            return dict(
                available=False,
                message="No tested execution recipe is registered for this exact proposal.",
                offer=None,
            )
        spec = selected["snapshot"]["specification"]
        contract = spec["contract"]
        from workbench.task_sessions import capability

        one_off = capability(engine) is not None
        return dict(
            runtime_ready=one_off,
            available=True,
            message="Approval can include one bounded task run and draft PR. Operational activation is separately disabled.",
            offer=dict(
                id=selected["id"],
                fingerprint=selected["fingerprint"],
                title=contract["title"],
                repository=contract["repository"],
                base_branch=spec["base_branch"],
                base=contract["base"],
                paths=contract["paths"],
                budget=TaskBudget(
                    offer_id=selected["id"],
                    offer_fingerprint=selected["fingerprint"],
                    publication="draft-pr",
                    activate_once=one_off,
                ).model_dump(mode="json"),
            ),
        )


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
