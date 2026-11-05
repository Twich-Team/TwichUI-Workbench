import hashlib
import json
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select

from workbench.db import commands, tasks, write_transaction
from workbench.schemas import Decision


def now():
    return datetime.now(timezone.utc).isoformat()


def version(row):
    # Includes all eligibility inputs, patch identity, claims and displayed sources.
    data = {key: row[key] for key in tasks.c.keys()}
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def task_view(connection, row):
    latest = connection.execute(select(commands).where(commands.c.task_id == row["id"])
                                .order_by(commands.c.created_at.desc(), commands.c.id).limit(1)).mappings().first()
    return {**row, "version": version(row), "latest_command": latest,
            "review_available": row["state"] == "held" and not (
                latest and latest["status"] == "pending")}


def get_task(connection, task_id):
    row = connection.execute(select(tasks).where(tasks.c.id == task_id)).mappings().first()
    if row is None:
        raise HTTPException(404, "That task could not be found.")
    return row


def submit(engine, task_id, decision: Decision):
    payload = decision.model_dump(mode="json")
    command_id = str(decision.command_id)
    with write_transaction(engine) as connection:
        old = connection.execute(select(commands).where(commands.c.id == command_id)).mappings().first()
        if old:
            if old["task_id"] != task_id or old["payload"] != payload:
                raise HTTPException(409, "This command ID already belongs to a different decision.")
            return dict(old)
        row = get_task(connection, task_id)
        if version(row) != decision.version:
            raise HTTPException(409, "The evidence changed. Open the refreshed review before deciding.")
        if row["state"] != "held":
            raise HTTPException(409, "This review has already been resolved.")
        prior = connection.execute(select(commands.c.id).where(
            commands.c.task_id == task_id,
            (commands.c.version == decision.version) | (commands.c.status == "pending"))).first()
        if prior:
            raise HTTPException(409, "A decision is already saved for this review. Refresh to see its receipt.")
        if set(decision.choices) != {claim["id"] for claim in row["evidence"]["claims"]}:
            raise HTTPException(422, "Choose an answer for every behavior.")
        receipt = dict(id=command_id, task_id=task_id, version=decision.version, payload=payload,
                       reviewed_evidence=row["evidence"], reviewed_revision=row["revision"],
                       status="pending", message="Decision saved. Waiting for the worker to apply it.",
                       created_at=now(), finished_at=None)
        connection.execute(commands.insert().values(**receipt))
    # Commit has completed before the API can acknowledge the receipt.
    return receipt
