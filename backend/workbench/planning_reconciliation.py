"""Same-user local settlement of a stopped tool-free LOCAL planning request.

No response is recovered, completed, approved or replayed. No HTTP write endpoint.
Original model outcome/usage and diagnostics remain unchanged.
"""

import argparse
import copy
import json
import os
import time
import urllib.request
from uuid import UUID

from workbench.db import engine_for, write_transaction
from workbench.real_execution import alive
from workbench.trusted_preparation import configuration, owned
from workbench.visual_scope import digest
from workbench.planning_limits import consent_cap, final_bound

KIND = "stopped-local-planning-v1"
MESSAGE = (
    "The interrupted local investigation is stopped. Its final response and usage remain unavailable. "
    "No implementation started. Another investigation requires a fresh task and planning consent."
)


def require(ok, message):
    if not ok:
        raise ValueError(message)


def rows(connection, sql, args=()):
    cursor = (
        connection.exec_driver_sql(sql, args)
        if hasattr(connection, "exec_driver_sql")
        else connection.execute(sql, args)
    )
    keys = (
        list(cursor.keys())
        if hasattr(cursor, "keys")
        else [column[0] for column in cursor.description]
    )
    return [dict(zip(keys, row)) for row in cursor.fetchall()]


def state(connection, identity):
    require(str(UUID(identity)) == identity, "Invalid model attempt identity")
    found = rows(connection, "SELECT * FROM model_attempts WHERE id=?", (identity,))
    require(len(found) == 1, "Missing local planning attempt")
    attempt = found[0]
    require(
        attempt["source"] == "workbench"
        and attempt["backend"] == "local"
        and attempt["purpose"] == "planning"
        and attempt["outcome"] == "interrupted_unknown"
        and attempt["finished_at"]
        and attempt["job_id"],
        "Only interrupted local planning may be settled",
    )
    require(attempt["usage"] in (None, "null"), "A complete usage receipt must not be replaced")
    [job] = rows(connection, "SELECT * FROM planning_jobs WHERE id=?", (attempt["job_id"],))
    require(
        job["intake_id"] == attempt["task_id"]
        and job["kind"] == "planning"
        and job["status"] == "failed",
        "The planning job must have stopped",
    )
    payload = json.loads(job["payload"])
    claim = payload.get("scenario_generation_claim")
    require(
        isinstance(claim, dict)
        and {"pid", "boot", "start"} <= set(claim)
        and type(claim["pid"]) is int
        and claim["pid"] > 0
        and isinstance(claim["boot"], str)
        and isinstance(claim["start"], str)
        and claim["start"].isdigit()
        and not alive(claim),
        "The original planning producer must be stopped before settlement",
    )
    [intake] = rows(connection, "SELECT * FROM intakes WHERE id=?", (attempt["task_id"],))
    require(
        intake["status"] == "failed"
        and not rows(connection, "SELECT id FROM proposals WHERE intake_id=?", (intake["id"],))
        and len(rows(connection, "SELECT id FROM model_attempts WHERE task_id=?", (intake["id"],)))
        == 1,
        "A proposed plan or another attempt prevents this settlement",
    )
    receipt = payload.get("local_planning_receipt", {})
    require(
        receipt.get("investigation_version") in (4, 5, 6, 7, 8) and receipt.get("max_local_requests") == 8,
        "The local investigation request bound is unavailable",
    )
    diag = payload.get("diagnostics", {})
    require(
        diag.get("generation_state") == "uncertain"
        and type(diag.get("investigation_requests")) is int
        and 1 <= diag["investigation_requests"] < 8,
        "The completed local response prefix is unavailable",
    )
    known = json.loads(diag["known_usage"])
    require(
        set(known) == {"prompt_tokens", "completion_tokens", "total_tokens"}
        and all(type(value) is int and value >= 0 for value in known.values())
        and known["prompt_tokens"] + known["completion_tokens"] == known["total_tokens"]
        and known["completion_tokens"] <= diag["investigation_requests"] * consent_cap(receipt),
        "The known response-prefix accounting is malformed",
    )
    binding = digest(
        dict(
            attempt=attempt,
            intake_payload=intake["payload"],
            claim=claim,
            diagnostics=diag,
            planning_receipt=receipt,
        )
    )
    return attempt, job, payload, known, binding


def accounting(connection, identity):
    """Read-only validation; returning an upper bound never invents actual usage."""
    attempt, job, payload, known, binding = state(connection, identity)
    receipt = payload.get("local_interruption_settlement")
    require(
        isinstance(receipt, dict)
        and receipt.get("kind") == KIND
        and receipt.get("binding") == binding
        and receipt.get("attempt") == identity
        and receipt.get("job") == job["id"]
        and receipt.get("intake") == attempt["task_id"]
        and receipt.get("model") == attempt["requested_model"]
        and receipt.get("external_execution_stopped") is True
        and receipt.get("completion_receipt") == "unavailable"
        and receipt.get("snapshot")
        == dict(task=receipt.get("server_task"), idle=True, completion_limit=final_bound(payload))
        and type(receipt.get("server_task")) is int
        and receipt["server_task"] >= 0
        and job["message"] == MESSAGE,
        "Local interruption has no current verified settlement",
    )
    expected = dict(
        units="tokens",
        kind="conservative-local-bound",
        known_completed_values=known,
        completed_requests=payload["diagnostics"]["investigation_requests"],
        requests_started=payload["diagnostics"]["investigation_requests"] + 1,
        unavailable_usage_requests=1,
        final_completion_upper_bound=final_bound(payload),
        completion_tokens_upper_bound=known["completion_tokens"] + final_bound(payload),
    )
    require(digest(receipt.get("accounting")) == digest(expected), "Local interruption accounting changed")
    return expected


def probe_local(model, server_task, completion_limit=2600):
    require(completion_limit in (2600, 6144), "Unsupported local request bound")
    """Fixed authenticated metadata endpoints only. Never a generation request."""
    key = owned(os.environ.get("WORKBENCH_LOCAL_KEY") or "/nonexistent")
    require(
        key.is_file() and key.stat().st_mode & 0o077 == 0 and key.stat().st_size <= 8192,
        "Private local model credential is unavailable",
    )
    secret = key.read_text().strip()
    from workbench.planning_transport import LocalHTTP

    transport = LocalHTTP()
    transport.seconds = 15
    transport.ends_at = time.monotonic() + 15
    with transport:
        require(transport.models(secret) == model, "The selected local model changed")
        request = urllib.request.Request(
            "http://127.0.0.1:8080/slots", headers={"Authorization": "Bearer " + secret}
        )
        slots = json.loads(transport.read(request, 65536, timeout=3))
    require(
        isinstance(slots, list)
        and len(slots) == 1
        and isinstance(slots[0], dict)
        and slots[0].get("is_processing") is False
        and type(slots[0].get("id_task")) is int
        and slots[0].get("id_task") == server_task
        and isinstance(slots[0].get("params"), dict)
        and slots[0].get("params", {}).get("n_predict") == completion_limit
        and "prompt" not in slots[0]
        and "generated" not in slots[0],
        "The exact local model request must be idle with its supported bound",
    )
    return dict(task=server_task, idle=True, completion_limit=completion_limit)


def reconcile(engine, identity, server_task):
    configuration(engine)  # Owner-private, inode-bound installed store.
    with engine.connect() as connection:
        attempt, job, payload, known, binding = state(connection, identity)
        if payload.get("local_interruption_settlement"):
            return accounting(connection, identity)
    require(type(server_task) is int and server_task >= 0, "Invalid local server task identity")
    from workbench.planning_provider import local_slot

    with local_slot():
        limit = final_bound(payload)
        snapshot = (probe_local(attempt["requested_model"], server_task) if limit == 2600 else
                    probe_local(attempt["requested_model"], server_task, limit))
        require(
            snapshot == dict(task=server_task, idle=True, completion_limit=limit),
            "Local settlement probe changed",
        )
        receipt = dict(
            kind=KIND,
            binding=binding,
            attempt=identity,
            job=job["id"],
            intake=attempt["task_id"],
            model=attempt["requested_model"],
            server_task=server_task,
            snapshot=snapshot,
            external_execution_stopped=True,
            completion_receipt="unavailable",
            recorded_at=time.time(),
            original_message=job["message"],
            operator="same-user local CLI; no task or model authority",
        )
        receipt["accounting"] = dict(
            units="tokens",
            kind="conservative-local-bound",
            known_completed_values=known,
            completed_requests=payload["diagnostics"]["investigation_requests"],
            requests_started=payload["diagnostics"]["investigation_requests"] + 1,
            unavailable_usage_requests=1,
            final_completion_upper_bound=final_bound(payload),
            completion_tokens_upper_bound=known["completion_tokens"] + final_bound(payload),
        )
        changed = copy.deepcopy(payload)
        changed["local_interruption_settlement"] = receipt
        with write_transaction(engine) as connection:
            current = state(connection, identity)
            require(
                current[1] == job and current[4] == binding,
                "Planning state changed during settlement",
            )
            from sqlalchemy import update
            from workbench.db import planning_jobs

            connection.execute(
                update(planning_jobs)
                .where(planning_jobs.c.id == job["id"])
                .values(payload=changed, message=MESSAGE)
            )
            return accounting(connection, identity)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("attempt")
    parser.add_argument("--server-task", type=int, required=True)
    args = parser.parse_args()
    from workbench.planning_transport import PlanningUnavailable
    from sqlalchemy.exc import SQLAlchemyError

    try:
        result = reconcile(engine_for(), args.attempt, args.server_task)
    except ValueError, OSError, KeyError, TypeError, PlanningUnavailable, SQLAlchemyError:
        print(
            json.dumps(
                dict(
                    held=True,
                    reason="Local planning settlement checks failed; no new work authorized.",
                )
            )
        )
        raise SystemExit(2) from None
    print(json.dumps(dict(settled=True, completion_receipt="unavailable", accounting=result)))


if __name__ == "__main__":
    main()
