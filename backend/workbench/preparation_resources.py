"""Automatic settled, non-model preparation recovery under the same scope approval.

No implementation, model call, provider change or approval is created here. Old
claims/results stay in the job history; uncertain checks and changed scopes hold.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import time
from uuid import uuid4

from sqlalchemy import select
from fastapi import HTTPException

from workbench.db import (
    planning_jobs,
    model_attempts,
    task_run_offers,
    task_sessions,
    write_transaction,
)
from workbench.storage_resources import StorageHold, require as require_storage, regular_bytes

LEGACY = "Run/scratch/free-space budget exhausted; retain partial evidence"


def relevant(job):
    result = job["payload"].get("preparation_result", {})
    return (
        job["kind"] == "behavioral_preparation"
        and job["status"] == "failed"
        and job["finished_at"]
        and (
            result.get("storage", {}).get("code") in {"low_disk", "low_inodes"}
            or result.get("message") == LEGACY
        )
    )


def automatic(job):
    if job["status"] not in {"queued", "failed"}:
        return False
    resource = job["payload"].get("storage_recovery")
    return bool(resource.get("automatic")) if resource else bool(relevant(job))


def settled(c, job):
    from workbench import task_execution
    from workbench.trusted_preparation import owned, validate_reviewed_policy
    from workbench.real_workspace import Runtime, tree, git

    payload = job["payload"]
    claim = payload["preparation_claim"]
    linked = task_execution.snapshot(c, **payload["ref"])
    reviewed = linked["proposal_review"]["task_contract"]
    if (
        linked["proposal_review"]["binding"] != claim["review_binding"]
        or payload["preparation_result"].get("review_binding") != claim["review_binding"]
    ):
        raise ValueError("The preparation scope changed; earlier approval cannot authorize it.")
    validate_reviewed_policy(c.engine, reviewed)
    if (
        c.execute(
            select(task_run_offers.c.id).where(
                task_run_offers.c.proposal_id == linked["proposal_review"]["id"]
            )
        ).first()
        or c.execute(
            select(task_sessions.c.id).where(task_sessions.c.task_id == payload["ref"]["task_id"])
        ).first()
    ):
        raise ValueError(
            "An implementation offer or session already exists; do not repeat preparation."
        )
    if c.execute(select(model_attempts.c.id).where(model_attempts.c.job_id == job["id"])).first():
        raise ValueError(
            "Model-assisted preparation requires its separate terminal-state reconciliation."
        )
    root = owned(Path(claim["root"]))
    runtime = Runtime(root)
    if (
        not isinstance(runtime.recipe, dict)
        or runtime.recipe.get("repair_cycle") != "scoped-repair-v1"
        or runtime.recipe.get("approved_intent") != reviewed.get("bug_intent")
        or (root / "preparation-completed.json").exists()
        or (root / "controls-passed.json").exists()
    ):
        raise ValueError("Retained preparation is not an unfinished read-only scoped setup.")
    usage = root / "usage.jsonl"
    if usage.exists():
        for line in usage.read_text().splitlines():
            event = json.loads(line)
            if event.get("cgroup_removed") is not True or event.get("leaked_after_kill"):
                raise ValueError("A previous check has not proved safe settlement.")
    if runtime.workspace.exists():
        owned(runtime.workspace)
        if git(runtime.workspace, "status", "--porcelain=v1", "--ignored") or tree(
            runtime.workspace
        ) != json.loads(runtime.initial.read_text()):
            raise ValueError(
                "The retained copy contains changes; preserve it rather than replaying preparation."
            )
    return runtime


def process_one(engine):
    from workbench import everyday

    if not everyday.enabled():
        return False
    with write_transaction(engine) as c:
        jobs = (
            c.execute(
                select(planning_jobs)
                .where(
                    planning_jobs.c.kind == "behavioral_preparation",
                    planning_jobs.c.status == "failed",
                )
                .order_by(planning_jobs.c.created_at)
            )
            .mappings()
            .all()
        )
        for row in jobs:
            job = dict(row)
            if not relevant(job):
                continue
            payload = dict(job["payload"])
            saved = payload.get("storage_recovery", {})
            if saved.get("state") == "held" or saved.get("check_after", 0) > time.time():
                continue
            resource = dict(
                saved,
                automatic=True,
                check_after=time.time() + 30,
                observed_at=datetime.now(timezone.utc).isoformat(),
            )
            try:
                runtime = settled(c, job)
                # Only a verified unchanged disposable clone may be removed.
                # Markers, manifest, failure, accounting and source are retained;
                # an older installation's different scratch root is left alone.
                resource["disposable_copy_cleaned"] = everyday.clean_workspace(runtime.root)
                growth = regular_bytes(Path(runtime.source)) + (4 << 20)
                require_storage(runtime.root, runtime.workspace, growth)
                everyday.require_room(everyday.configuration(engine), growth)
            except StorageHold as error:
                resource.update(
                    state="waiting",
                    code=error.code,
                    message=str(error),
                    observation=error.observation,
                )
            except everyday.CapacityHold as error:
                resource.update(state="waiting", code="installation_capacity", message=str(error))
            except (OSError, ValueError, KeyError, TypeError, HTTPException) as error:
                resource.update(
                    state="held",
                    automatic=False,
                    code="reconciliation",
                    message="Preparation recovery needs checking: " + str(error)[:800],
                )
            else:
                history = list(payload.get("storage_recovery_history", []))
                history.append(
                    dict(
                        claim=payload["preparation_claim"],
                        result=payload["preparation_result"],
                        attempts=job["attempts"],
                        finished_at=job["finished_at"],
                        message=job["message"],
                        renewed_at=resource["observed_at"],
                        reason="Verified settled offline resource failure; same scope approval, no model/run authority.",
                        disposable_copy_cleaned=resource.get("disposable_copy_cleaned", False),
                    )
                )
                renewed = {
                    k: v
                    for k, v in payload.items()
                    if k not in {"preparation_claim", "preparation_result", "storage_recovery"}
                }
                renewed.update(storage_recovery_history=history, preparation_round_id=str(uuid4()))
                c.execute(
                    planning_jobs.update()
                    .where(planning_jobs.c.id == job["id"], planning_jobs.c.status == "failed")
                    .values(
                        status="queued",
                        finished_at=None,
                        payload=renewed,
                        message="Storage is ready. Workbench is automatically preparing the same approved scope; run permission is still separate.",
                    )
                )
                return True
            c.execute(
                planning_jobs.update()
                .where(planning_jobs.c.id == job["id"])
                .values(
                    payload={**payload, "storage_recovery": resource}, message=resource["message"]
                )
            )
            return False
    return False


def view(c, proposal):
    job = (
        c.execute(
            select(planning_jobs).where(
                planning_jobs.c.intake_id == proposal["intake_id"],
                planning_jobs.c.revision == proposal["revision"],
                planning_jobs.c.kind == "behavioral_preparation",
            )
        )
        .mappings()
        .first()
    )
    if not job:
        return None
    if resource := job["payload"].get("storage_recovery"):
        return {
            k: resource[k]
            for k in ["state", "code", "automatic", "message", "observed_at"]
            if k in resource
        }
    if relevant(job):
        return dict(
            state="reconciling",
            code="storage",
            automatic=True,
            message="Workbench is checking the saved preparation and available storage. Your plan approval is preserved; recovery does not need another click.",
        )
    return None
