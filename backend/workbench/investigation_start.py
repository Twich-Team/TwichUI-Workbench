"""Add read-only planning permission to a saved report without replacing its input.

The original request and failed jobs stay immutable. A new journaled operation
binds the selected provider, original input, old non-dispatches and current policy.
"""

import json
from sqlalchemy import select
from fastapi import HTTPException
from workbench.db import (
    intakes,
    planning_jobs,
    model_attempts,
    proposals,
    proposal_decisions,
    backlog_items,
    write_transaction,
)


def first(connection, identity):
    resumed = records(
        connection,
        "SELECT * FROM planning_jobs WHERE intake_id=? AND kind='planning' AND json_extract(payload,'$.planning_start') IS NOT NULL ORDER BY revision DESC LIMIT 1",
        (identity,),
    )
    if resumed:
        return resumed[0]
    rows = records(
        connection,
        "SELECT * FROM planning_jobs WHERE intake_id=? AND kind='planning' AND json_extract(payload,'$.local_planning_receipt') IS NOT NULL ORDER BY revision LIMIT 1",
        (identity,),
    )
    return rows[0] if rows else None


def records(connection, query, args):
    cursor = (
        connection.exec_driver_sql(query, args)
        if hasattr(connection, "exec_driver_sql")
        else connection.execute(query, args)
    )
    names = list(cursor.keys()) if hasattr(cursor, "keys") else [c[0] for c in cursor.description]
    rows = [dict(zip(names, row)) for row in cursor.fetchall()]
    for row in rows:
        for key in ("payload", "usage"):
            if isinstance(row.get(key), str):
                row[key] = json.loads(row[key])
    return rows


def non_dispatch(job, attempts):
    """Recognize only the disabled everyday legacy adapter, never unknown generation."""
    local = (
        job["status"] == "failed"
        and job["finished_at"]
        and not job["lease"]
        and not job["model"]
        and job["payload"].get("diagnostics", {}).get("category") == "contention"
        and job["payload"]["diagnostics"].get("generation_state") == "not_started"
        and job["payload"]["diagnostics"].get("investigation_requests") == 0
        and job["payload"]["diagnostics"].get("stopped_request_category") == "contention"
        and job["payload"].get("recovery_capabilities", {}).get("kind")
        == "local-message-investigation-v1"
        and all(
            job["payload"]["recovery_capabilities"].get(k) is False
            for k in ("source_writes", "execution_dispatch", "external_action_tools")
        )
        and len(attempts) == job["attempts"] == 1
        and all(
            a["backend"] == "local"
            and a["source"] == "workbench"
            and a["purpose"] == "planning"
            and a["requested_model"] == "pilot-qwen38-27b"
            and a["outcome"] == "failed"
            and a["finished_at"]
            and not any(a[k] for k in ("actual_model", "usage", "execution_id"))
            for a in attempts
        )
    )
    return local or (
        job["kind"] == "planning"
        and job["status"] == "failed"
        and job["finished_at"]
        and not job["lease"]
        and not job["model"]
        and job["payload"].get("diagnostics") == {"category": "unavailable"}
        and not any(
            job["payload"].get(k)
            for k in ("scenario_generation_claim", "local_planning_receipt", "response_evidence")
        )
        and len(attempts) == job["attempts"]
        and bool(attempts)
        and all(
            a["outcome"] == "failed"
            and a["finished_at"]
            and a["source"] == "unknown"
            and not any(
                a[k]
                for k in ("backend", "requested_model", "actual_model", "usage", "execution_id")
            )
            and a["routing_reason"]
            == "Existing planning adapter configuration; new Workbench policy is not consumed or acknowledged."
            for a in attempts
        )
    )


def fingerprint(job, attempts):
    from workbench.intake import digest

    return digest(dict(job=dict(job), attempts=[dict(a) for a in attempts]))


def validate_prefix(connection, initial):
    """Historical non-dispatches remain present and bound, outside model phase counts."""
    start = initial["payload"].get("planning_start")
    if not start:
        return set()
    saved = start["previous_jobs"]
    excluded = set()
    for item in saved:
        found = records(connection, "SELECT * FROM planning_jobs WHERE id=?", (item["id"],))
        if len(found) != 1:
            raise ValueError("The retained pre-provider job is unavailable")
        job = found[0]
        attempts = records(
            connection, "SELECT * FROM model_attempts WHERE job_id=? ORDER BY id", (job["id"],)
        )
        if (
            job["intake_id"] != initial["intake_id"]
            or job["revision"] >= initial["revision"]
            or not non_dispatch(job, attempts)
            or fingerprint(job, attempts) != item["sha256"]
        ):
            raise ValueError("The retained pre-provider failure evidence changed")
        excluded.add(job["id"])
    return excluded


def resume_binding(connection, row, job):
    from workbench.intake import digest

    attempts = records(
        connection, "SELECT * FROM model_attempts WHERE job_id=? ORDER BY id", (job["id"],)
    )
    if (
        row["status"] == "failed"
        and row["payload"].get("local_planning") == "qwen-once"
        and job["payload"].get("local_planning_receipt")
        and non_dispatch(job, attempts)
    ):
        return digest(dict(original=row["payload"], job=dict(job), attempts=attempts))
    return None


def resume_in(connection, row, job, body):
    from datetime import datetime, timedelta, timezone
    from workbench import intake, provider_routes

    for p in connection.execute(
        select(planning_jobs.c.payload).where(planning_jobs.c.intake_id == row["id"])
    ).scalars():
        if p.get("pre_generation_resume", {}).get("command_id") == str(body.command_id):
            if p["pre_generation_resume"] != body.model_dump(mode="json"):
                raise HTTPException(409, "Resume command already belongs to another binding.")
            return
    binding = resume_binding(connection, row, job)
    if not body.command_id or not binding or binding != body.review_binding:
        raise HTTPException(409, "This operation is not a verified pre-generation hold.")
    if connection.execute(
        select(planning_jobs.c.id).where(
            planning_jobs.c.intake_id == row["id"], planning_jobs.c.revision > job["revision"]
        )
    ).first():
        raise HTTPException(409, "A newer operation already exists. Refresh this report.")
    for table in (proposals, proposal_decisions, backlog_items):
        if connection.execute(select(table.c.id).where(table.c.intake_id == row["id"])).first():
            raise HTTPException(
                409, "Existing proposed or dispatched work cannot be replayed here."
            )
    initial = first(connection, row["id"])
    validate_prefix(connection, initial)
    receipt = initial["payload"]["local_planning_receipt"]
    if datetime.now(timezone.utc) + timedelta(
        seconds=receipt["planning_deadline_seconds"]
    ) >= datetime.fromisoformat(receipt["expires_at"]):
        raise HTTPException(
            409,
            "The unused investigation permission has expired. Fresh read-only consent is required.",
        )
    provider_routes.approved(connection, row)
    prior = initial["payload"].get("planning_start") or dict(
        command=dict(origin="original-intake-permission", request_id=row["id"]),
        approved_at=row["created_at"],
        original_input=row["payload"],
        previous_jobs=[],
    )
    attempts = records(
        connection, "SELECT * FROM model_attempts WHERE job_id=? ORDER BY id", (job["id"],)
    )
    intake.enqueue(
        connection,
        row["id"],
        "planning",
        job["revision"] + 1,
        dict(
            local_planning_receipt=receipt,
            planning_start={
                **prior,
                "previous_jobs": [
                    *prior["previous_jobs"],
                    dict(id=job["id"], sha256=fingerprint(job, attempts)),
                ],
            },
            pre_generation_resume=body.model_dump(mode="json"),
        ),
    )
    connection.execute(intakes.update().where(intakes.c.id == row["id"]).values(status="queued"))


def start(engine, identity, body):
    from workbench import intake, everyday, provider_routes, bug_investigation
    from workbench.trusted_preparation import configuration

    if not everyday.enabled():
        raise HTTPException(409, "Read-only investigation is unavailable at this address.")
    try:
        everyday.configuration(engine)
        if bug_investigation.CATEGORY not in configuration(engine)["allowed_classes"]:
            raise ValueError("Bug investigation is not configured")
    except OSError, ValueError, RuntimeError:
        raise HTTPException(
            503, "The installation needs attention before investigation can start."
        ) from None
    payload = body.model_dump(mode="json")
    with write_transaction(engine) as c:
        row = intake.require(c, intakes, identity)
        jobs = [
            dict(j)
            for j in c.execute(
                select(planning_jobs)
                .where(planning_jobs.c.intake_id == identity)
                .order_by(planning_jobs.c.revision)
            ).mappings()
        ]
        for job in jobs:
            saved = job["payload"].get("planning_start", {})
            if saved.get("command", {}).get("command_id") == str(body.command_id):
                if saved["command"] != payload:
                    raise HTTPException(
                        409, "This investigation command already belongs to a different choice."
                    )
                return
        if (
            row["payload"].get("kind") != "issue"
            or row["payload"].get("supported_task")
            or row["payload"].get("local_planning")
            or row["status"] not in {"failed", "waiting_for_information"}
            or body.binding != intake.digest(row["payload"])
        ):
            raise HTTPException(
                409, "The saved report changed or already has planning permission. Refresh it."
            )
        for table in (proposals, proposal_decisions, backlog_items):
            if c.execute(select(table.c.id).where(table.c.intake_id == identity)).first():
                raise HTTPException(
                    409, "Existing proposals or work must be reconciled before investigation."
                )
        previous = []
        for job in jobs:
            attempts = (
                c.execute(
                    select(model_attempts)
                    .where(model_attempts.c.job_id == job["id"])
                    .order_by(model_attempts.c.id)
                )
                .mappings()
                .all()
            )
            if not non_dispatch(job, attempts):
                raise HTTPException(
                    409,
                    "An earlier operation is active, uncertain or has provider output. It cannot be replayed here.",
                )
            previous.append(dict(id=job["id"], sha256=fingerprint(job, attempts)))
        original = dict(row["payload"])
        effective={**original,"local_planning":body.provider,**(dict(ai_settings=body.ai_settings.model_dump()) if body.ai_settings else {})}
        at = intake.now()
        receipt = everyday.planning_receipt(effective, at)
        try:
            key, route = provider_routes.planning_binding(c,body.provider,body.ai_settings)
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
        receipt[key] = route
        # The initial scope's exact change_payload and this operation both preserve
        # the owner's original bytes. Only a separate read-only permission is added.
        c.execute(
            intakes.update()
            .where(intakes.c.id == identity)
            .values(payload=effective, status="queued")
        )
        intake.enqueue(
            c,
            identity,
            "planning",
            max((j["revision"] for j in jobs), default=0) + 1,
            dict(
                local_planning_receipt=receipt,
                planning_start=dict(
                    command=payload, approved_at=at, original_input=original, previous_jobs=previous
                ),
            ),
        )
