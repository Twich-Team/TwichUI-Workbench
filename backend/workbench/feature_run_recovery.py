"""Explicit finite continuation after a feature slot never reached dispatch.

The original owner decision, expiry, unused slot and package remain immutable.
A distinct browser receipt permits future slots for six hours with the original
scope, provider, attempt/token limits and publication hold. No old grant revives.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.exc import NoResultFound, MultipleResultsFound

from workbench import feature_permission, intake, feature_steps
from workbench.db import (
    planning_jobs,
    proposal_decisions,
    task_sessions,
    task_run_offers,
    real_execution_grants,
    real_execution_requests,
    model_attempts,
    write_transaction,
)
from workbench.schemas import StrictModel
from workbench.visual_scope import digest


class RunRecoveryInput(StrictModel):
    command_id: UUID
    binding: str = Field(pattern=r"^[a-f0-9]{64}$")


class RunRecoveryOffer(StrictModel):
    binding: str
    hours: int = 6
    task_title: str
    message: str


def journal(c, decision_id):
    return (
        c.execute(
            select(planning_jobs).where(
                planning_jobs.c.kind == "feature_execution",
                planning_jobs.c.payload["feature_decision_id"].as_string() == decision_id,
            )
        )
        .mappings()
        .first()
    )


def deadline(c, approval, permission, *, at=None, reference=None):
    """As-of authority keeps every existing offer/grant deadline unchanged."""
    original = permission["expires_at"]
    job = journal(c, approval["id"])
    if not job:
        return original
    windows = job["payload"].get("execution_windows", [])
    if len(windows) > 1:
        raise ValueError("Only the separately reviewed initial continuation is supported.")
    for receipt in windows:
        value = {k: v for k, v in receipt.items() if k != "receipt_binding"}
        inputs = receipt["approved_inputs"]
        if (
            receipt["receipt_binding"] != digest(value)
            or receipt["binding"] != digest(inputs)
            or inputs["decision_id"] != approval["id"]
            or inputs["parent_binding"] != approval["binding"]
            or inputs["permission_binding"] != digest(permission)
            or receipt["owner_provenance"] != "browser-never-dispatched-feature-continuation-v1"
            or receipt["expires_at"]
            != (datetime.fromisoformat(receipt["approved_at"]) + timedelta(hours=6)).isoformat()
            or receipt["original_expires_at"] != original
            or receipt["scope_changed"]
            or receipt["attempts_reset"]
            or receipt["publication_changed"]
        ):
            raise ValueError("The separate feature continuation changed.")
        if reference is not None:
            if receipt["command_id"] == reference:
                return receipt["expires_at"]
        elif at is None or datetime.fromisoformat(receipt["approved_at"]) <= datetime.fromisoformat(
            at
        ):
            return receipt["expires_at"]
    if reference is not None and reference != "original-parent-run":
        raise ValueError("The exact feature time authority is unavailable.")
    return original


def time_authority(c, approval, permission, *, at=None):
    job = journal(c, approval["id"])
    windows = job["payload"].get("execution_windows", []) if job else []
    eligible = [
        r
        for r in windows
        if at is None or datetime.fromisoformat(r["approved_at"]) <= datetime.fromisoformat(at)
    ]
    return (
        eligible[-1]["command_id"] if eligible else "original-parent-run",
        deadline(c, approval, permission, at=at),
    )


def validate_unstarted(c, proof, scope):
    """Positive native no-dispatch proof, not missing usage treated as zero."""
    from workbench.real_workspace import sha
    from workbench.trusted_preparation import owned

    if (
        proof["binding"] != digest({k: v for k, v in proof.items() if k != "binding"})
        or proof["kind"] != "never-dispatched-feature-slot-v1"
        or proof["decision_id"] != scope["decision_id"]
        or proof["task_id"] != scope["task_id"]
        or proof["scope_binding"] != scope["scope_binding"]
        or proof["previous_attempt"] != 1
        or proof["next_attempt"] != 2
    ):
        raise ValueError("Never-started feature recovery identity changed.")
    old = intake.require(c, task_run_offers, proof["previous_offer_id"])
    if (
        old["fingerprint"] != proof["previous_offer_fingerprint"]
        or old["fingerprint"] != digest(old["snapshot"])
        or old["snapshot"]["feature_step"]["scope"] != scope
        or old["snapshot"]["feature_step"]["attempt"] != 1
        or old["snapshot"]["ready_preparation"]["root"] != proof["baseline_source"]
        or c.execute(
            select(real_execution_grants.c.id).where(
                real_execution_grants.c.snapshot["task_budget"]["offer"]["id"].as_string()
                == old["id"]
            )
        ).first()
    ):
        raise ValueError("The original slot has dispatch authority or different inputs.")
    root = owned(proof["package_root"])
    prepared = owned(proof["baseline_source"])
    if (
        prepared.parents[4].name != "runs"
        or root.parent != prepared.parents[4]
        or root.name != "task-session-" + proof["slot_id"]
        or {p.name for p in root.iterdir()} != {"BUDGET_BASELINE.json", "owner-command.json"}
    ):
        raise ValueError("Native package is not the original never-reserved slot.")
    for name, expected in proof["package_hashes"].items():
        path = owned(root / name)
        if sha(path.read_bytes()) != expected:
            raise ValueError("The original never-started package changed.")
    import json

    budget = json.loads((root / "BUDGET_BASELINE.json").read_text())
    if not 0 < budget["deadline"] - budget["started_at"] < 3000:
        raise ValueError("The original package was not shortened below its required reserve.")
    command = json.loads((root / "owner-command.json").read_text())
    approval = intake.require(c, proposal_decisions, scope["decision_id"])
    if command["decision_id"] != approval["id"] or command["payload"] != approval["payload"]:
        raise ValueError("The package owner decision changed.")
    # The complete baseline and boundary are independently reconstructed, not
    # replaced by an operator assertion. These bytes may be reused as baseline only.
    feature_steps._offer(c, old, active=False)
    return proof


def offer(c, identity):
    try:
        feature_permission.capability(c.engine)
        job = (
            c.execute(
                select(planning_jobs).where(
                    planning_jobs.c.intake_id == identity,
                    planning_jobs.c.kind == "feature_execution",
                )
            )
            .mappings()
            .one()
        )
        payload = job["payload"]
        approval, permission = feature_steps.parent(c, payload["feature_decision_id"], active=False)
        parent = intake.require(c, task_sessions, approval["id"])
        state = parent["state"]
        slot = state.get("feature_slot") or {}
        result = state.get("feature_slot_result") or {}
        saved = payload.get("current_step_scope")
        if (
            job["status"] != "failed"
            or payload.get("phase") != "held"
            or job["lease"]
            or not job["finished_at"]
            or payload.get("execution_windows")
            or parent["status"] != "held"
            or state.get("cancel_requested")
            or result.get("status") != "held"
            or result.get("outcome") is not None
            or not saved
            or saved["scope"]["role"] != "implementation"
            or payload.get("completed_steps")
            or state.get("feature_history")
            or slot.get("snapshot", {}).get("feature_offer_id") != payload.get("current_offer_id")
            or result.get("id") != slot.get("id")
            or permission["scope"] != feature_permission.scope(c, identity)
            or datetime.fromisoformat(permission["expires_at"]) - datetime.now(timezone.utc)
            >= timedelta(seconds=5400)
            or c.execute(
                select(model_attempts.c.id).where(model_attempts.c.finished_at.is_(None))
            ).first()
            or c.execute(
                select(real_execution_requests.c.id).where(
                    real_execution_requests.c.status.in_(
                        ["pending", "running", "checking", "cancel_requested"]
                    )
                )
            ).first()
            or c.execute(
                select(real_execution_grants.c.id).where(
                    real_execution_grants.c.snapshot["task_budget"]["decision_id"].as_string()
                    == approval["id"]
                )
            ).first()
        ):
            return None
        from workbench.feature_scope_recovery import _confirmed

        if not _confirmed(c, job, payload["scope_history"], permission):
            return None
        from workbench.project_source import require_current

        require_current({"resolved_base": permission["scope"]["feature_base"]})
        from workbench.real_workspace import sha
        from workbench.trusted_preparation import owned

        old = intake.require(c, task_run_offers, payload["current_offer_id"])
        prepared = owned(old["snapshot"]["ready_preparation"]["root"])
        root = owned(prepared.parents[4] / ("task-session-" + slot["id"]))
        proof = dict(
            kind="never-dispatched-feature-slot-v1",
            decision_id=approval["id"],
            task_id=saved["task_id"],
            scope_binding=saved["scope_binding"],
            previous_offer_id=old["id"],
            previous_offer_fingerprint=old["fingerprint"],
            previous_attempt=1,
            next_attempt=2,
            slot_id=slot["id"],
            baseline_source=str(prepared),
            package_root=str(root),
            package_hashes={
                name: sha(owned(root / name).read_bytes())
                for name in ("BUDGET_BASELINE.json", "owner-command.json")
            },
        )
        proof = {**proof, "binding": digest(proof)}
        validate_unstarted(c, proof, saved)
        inputs = dict(
            decision_id=approval["id"],
            parent_binding=approval["binding"],
            permission_binding=digest(permission),
            graph_binding=payload["graph_binding"],
            history_binding=digest(payload["scope_history"]),
            parent_state_binding=digest(state),
            previous_failure=job["message"],
            finished_at=job["finished_at"],
            recovery=proof,
        )
        linked = next(g for g in permission["scope"]["graph"] if g["task_id"] == saved["task_id"])
        from workbench.db import backlog_items

        task = intake.require(c, backlog_items, linked["task_id"])
        return dict(
            binding=digest(inputs),
            hours=6,
            task_title=task["title"],
            approved_inputs=inputs,
            message="Continue this saved feature in a new six-hour window. Its first coding step never started. Your original approval and earlier attempt stay saved. Remaining attempts, token limits, checked work, AI choice and publishing rules stay in place.",
        )
    except (
        ValueError,
        OSError,
        HTTPException,
        KeyError,
        TypeError,
        StopIteration,
        IndexError,
        NoResultFound,
        MultipleResultsFound,
    ):
        return None


def request(engine, identity, body):
    with write_transaction(engine) as c:
        job = (
            c.execute(
                select(planning_jobs).where(
                    planning_jobs.c.intake_id == identity,
                    planning_jobs.c.kind == "feature_execution",
                )
            )
            .mappings()
            .first()
        )
        if not job:
            raise HTTPException(409, "This feature has no saved run to continue.")
        existing = job["payload"].get("execution_windows", [])
        prior = next((r for r in existing if r["command_id"] == str(body.command_id)), None)
        if prior:
            if prior["binding"] != body.binding:
                raise HTTPException(409, "This command already belongs to different feature work.")
            return prior
        selected = offer(c, identity)
        if not selected or selected["binding"] != body.binding:
            raise HTTPException(
                409,
                "The saved feature, no-dispatch proof or source changed. Refresh before continuing.",
            )
        inputs = selected["approved_inputs"]
        proof = inputs["recovery"]
        at = intake.now()
        approval, permission = feature_steps.parent(c, inputs["decision_id"], active=False)
        receipt = dict(
            command_id=str(body.command_id),
            binding=body.binding,
            approved_inputs=inputs,
            approved_at=at,
            expires_at=(datetime.fromisoformat(at) + timedelta(hours=6)).isoformat(),
            original_expires_at=permission["expires_at"],
            scope_changed=False,
            attempts_reset=False,
            publication_changed=False,
            owner_provenance="browser-never-dispatched-feature-continuation-v1",
        )
        receipt = {**receipt, "receipt_binding": digest(receipt)}
        parent = intake.require(c, task_sessions, approval["id"])
        state = parent["state"]
        retained = dict(
            slot=state["feature_slot"], result=state["feature_slot_result"], recovery=proof
        )
        state = {**state, "feature_history": state.get("feature_history", []) + [retained]}
        for key in ("feature_slot", "feature_slot_result", "feature_slot_state", "owner"):
            state.pop(key, None)
        payload = {
            **job["payload"],
            "phase": "scope_ready",
            "current_attempt": 2,
            "current_recovery": proof,
            "execution_windows": [receipt],
            "recovery_history": job["payload"].get("recovery_history", []) + [proof],
        }
        for key in ("current_offer_id", "feature_preparation_claim", "session_id"):
            payload.pop(key, None)
        c.execute(
            task_sessions.update()
            .where(task_sessions.c.id == parent["id"])
            .values(
                status="pending",
                finished_at=None,
                state=state,
                message="Continuing the saved feature under its separately reviewed time window.",
            )
        )
        c.execute(
            planning_jobs.update()
            .where(planning_jobs.c.id == job["id"])
            .values(
                status="queued",
                finished_at=None,
                payload=payload,
                message="Preparing a fresh bounded slot. The original unused slot and all earlier usage remain saved.",
            )
        )
        return receipt
