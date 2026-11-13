"""Deterministic supported-task planning in the existing planning queue. No model."""

from workbench import worker_operation as operation

import hashlib
import json
import os
from pathlib import Path
from uuid import UUID, uuid5

from fastapi import HTTPException
from sqlalchemy import select
from workbench import intake, real_recipes
from workbench.db import (
    intakes,
    planning_jobs,
    proposals,
    task_run_offers,
    write_transaction,
)
from workbench.schemas import ProposalContent
from workbench.visual_scope import digest


def template_root(engine=None):
    root = os.environ.get("WORKBENCH_TASK_TEMPLATES_ROOT")
    if root:
        return root
    if engine is None:
        from workbench.db import engine_for

        engine = engine_for()
    from workbench.task_sessions import capability

    configured = capability(engine)
    return configured["templates_root"] if configured else None


def load(identity, engine=None):
    root = template_root(engine)
    if not root:
        return None
    return read_template(Path(root), identity)


def read_template(folder, identity):
    if folder.resolve() != folder or not folder.is_dir() or folder.stat().st_uid != os.getuid():
        raise ValueError("Owned task-template directory required")
    if (
        not isinstance(identity, str)
        or len(identity) != 64
        or any(c not in "0123456789abcdef" for c in identity)
    ):
        raise ValueError("Invalid supported task identity")
    path = folder / (identity + ".json")
    if not path.is_file():
        return None
    if path.is_symlink() or path.stat().st_uid != os.getuid() or path.stat().st_size > 65536:
        raise ValueError("Owned bounded immutable template required")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != identity:
        raise ValueError("Supported task template changed")
    data = json.loads(raw)
    real_recipes.validate(data["recipe"])
    contract = data["recipe"]["contract"]
    if (
        contract["provider"] != "local"
        or contract["model"] != "pilot-qwen38-27b"
        or contract.get("authoring")
    ):
        raise ValueError(
            "Site task templates support the configured local model only; no automatic current-session fallback"
        )
    content = ProposalContent.model_validate(data["proposal"])
    if content.visual != "nonvisual" or content.delivery != "implementation":
        raise ValueError("Only reviewed nonvisual Lua tasks are currently supported")
    return data


def catalog(engine=None):
    root = template_root(engine)
    if not root:
        return dict(
            items=[],
            message="No tested task templates are configured. Other requests remain planning only.",
        )
    items = []
    for path in sorted(Path(root).glob("*.json"))[:32]:
        try:
            data = load(path.stem, engine)
            if data:
                items.append(
                    dict(
                        id=path.stem,
                        title=data["proposal"]["summary"],
                        purpose=data["proposal"]["value"],
                    )
                )
        except ValueError, KeyError, HTTPException:
            continue
    return dict(
        items=items, message="Supported tasks use a fixed reviewed plan; no planning inference."
    )


@operation.isolated
def process_one(engine, intake_id=None):
    with write_transaction(engine) as c:
        job = (
            c.execute(
                select(planning_jobs)
                .join(intakes, planning_jobs.c.intake_id == intakes.c.id)
                .where(
                    operation.eligible(c, planning_jobs.c.intake_id),
                    planning_jobs.c.status == "queued",
                    *([planning_jobs.c.intake_id == intake_id] if intake_id else []),
                    intakes.c.payload["supported_task"].as_string().is_not(None),
                )
                .order_by(planning_jobs.c.created_at)
                .limit(1)
            )
            .mappings()
            .first()
        )
        if not job:
            return False
        operation.selected(planning_jobs, job)
        job = dict(job)
        original = intake.require(c, intakes, job["intake_id"])
        try:
            template = load(original["payload"]["supported_task"], engine)
            if not template:
                raise ValueError("The selected supported task is unavailable")
            scope = intake.human_scope(c, original["id"])
            if (
                scope["visual_requirement"] != "nonvisual"
                or scope["stage"] != "implementation"
                or job["payload"].get("scope_revision") != scope["revision"]
            ):
                raise ValueError("The supported task does not match current human scope")
            if job["kind"] == "planning":
                content = ProposalContent.model_validate(template["proposal"]).model_dump(
                    mode="json"
                )
                proposal_id = str(
                    uuid5(UUID(original["id"]), "supported-proposal-" + str(job["revision"]))
                )
                c.execute(
                    proposals.insert().values(
                        id=proposal_id,
                        intake_id=original["id"],
                        revision=job["revision"],
                        scope_revision=scope["revision"],
                        content=content,
                        model="fixed-supported-template",
                        basis="template",
                        created_at=intake.now(),
                    )
                )
                review = intake.proposal_view(c, intake.require(c, proposals, proposal_id))
                offered = dict(
                    proposal_id=proposal_id,
                    review_binding=review["binding"],
                    specification=template["recipe"],
                )
                c.execute(
                    task_run_offers.insert().values(
                        id=str(uuid5(UUID(proposal_id), "run-offer")),
                        proposal_id=proposal_id,
                        fingerprint=digest(offered),
                        snapshot=offered,
                        created_at=intake.now(),
                    )
                )
                payload = {
                    **job["payload"],
                    "basis": "template",
                    "result": dict(
                        summary=content["summary"],
                        clarification="",
                        evidence_ids=[],
                        related_ids=[],
                        proposal=content,
                    ),
                    "evidence": [],
                    "template_sha256": original["payload"]["supported_task"],
                }
                status = "completed"
                c.execute(
                    intakes.update()
                    .where(intakes.c.id == original["id"])
                    .values(revision=job["revision"], status=status)
                )
            else:
                from workbench.planning_worker import materialize_single_task

                decomposition = materialize_single_task(c, original, job, "fixed-supported-template")
                payload = {
                    **job["payload"],
                    "decomposition": decomposition,
                    "template_sha256": original["payload"]["supported_task"],
                }
                c.execute(
                    intakes.update()
                    .where(intakes.c.id == original["id"])
                    .values(status="tasks_ready")
                )
            c.execute(
                planning_jobs.update()
                .where(planning_jobs.c.id == job["id"])
                .values(
                    status="completed",
                    payload=payload,
                    model="fixed-supported-template",
                    finished_at=intake.now(),
                    message="Supported task plan prepared deterministically. No planning model ran.",
                )
            )
        except ValueError, HTTPException, KeyError:
            c.execute(
                planning_jobs.update()
                .where(planning_jobs.c.id == job["id"])
                .values(
                    status="failed",
                    finished_at=intake.now(),
                    message="Supported task or human scope changed. No model ran; review the saved request.",
                )
            )
            c.execute(
                intakes.update().where(intakes.c.id == original["id"]).values(status="failed")
            )
    return True
