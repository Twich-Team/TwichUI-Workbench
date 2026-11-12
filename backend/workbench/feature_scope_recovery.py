"""Explicit additional read-only scope requests in the SAME feature parent.

Original run limits/history are immutable. A real browser command grants only
three further bounded source-preparation requests, within the original parent
clock. It grants no implementation, publication, fallback or provider change.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field
from sqlalchemy import select

from workbench import feature_steps, intake
from workbench.db import (
    planning_jobs,
    task_sessions,
    model_attempts,
    real_execution_requests,
    write_transaction,
)
from workbench.schemas import StrictModel
from workbench.visual_scope import digest


class FeatureScopeCorrectionInput(StrictModel):
    command_id: UUID
    binding: str = Field(pattern=r"^[a-f0-9]{64}$")


class FeatureScopeCorrectionOffer(StrictModel):
    binding: str
    task_title: str
    generation_requests: int = 3
    request_seconds: int = 120
    completion_tokens: int = 2600
    message: str


def _history(job, task_id):
    return [
        record for record in job["payload"].get("scope_history", []) if record["task_id"] == task_id
    ]


def _confirmed(c, job, records, permission):
    """Finished calls or native pre-send rejects; never infer absent generation."""
    if not records:
        return False
    for record in records:
        attempt = (
            c.execute(select(model_attempts).where(model_attempts.c.id == record["operation_id"]))
            .mappings()
            .first()
        )
        diagnostics = record.get("diagnostics") or {}
        if (
            not attempt
            or attempt["job_id"] != job["id"]
            or attempt["task_id"] != job["intake_id"]
            or attempt["backend"] != permission["scope"]["provider"]
            or attempt["requested_model"] != permission["scope"]["route"]["model"]
            or attempt["purpose"] != "feature_step_scope"
            or not attempt["finished_at"]
            or attempt["outcome"] not in {"completed", "failed"}
        ):
            return False
        if not record.get('confirmed_finished'):
            from workbench.feature_scope_settlement import not_started
            if not not_started(c.engine, record, attempt):
                return False
        elif (not attempt['usage'] or attempt['usage'] != diagnostics.get('usage')
                or diagnostics.get('generation_state') != 'finished'
                or diagnostics.get('accounting_state') != 'complete'):
            return False
    return True


def _not_started_bindings(c, records):
    from workbench.feature_scope_settlement import not_started
    result = []
    for record in records:
        if record.get('confirmed_finished'):
            continue
        attempt = c.execute(select(model_attempts).where(
            model_attempts.c.id == record['operation_id'])).mappings().one()
        proof = not_started(c.engine, record, attempt)
        if not proof:
            raise ValueError('The original preparation rejection is not independently established.')
        result.append(proof)
    return result


def allowance(c, job, task_id, permission, *, active=True):
    history = _history(job, task_id)
    maximum = permission["budget"]["scope_attempts_per_step"]
    reference = "original-parent-run"
    deadline = datetime.fromisoformat(permission["expires_at"])
    for receipt in job["payload"].get("scope_recoveries", []):
        saved = receipt["approved_inputs"]
        if saved["task_id"] != task_id:
            continue
        count = saved["starts_after"]
        carried = job["payload"].get("completed_steps", [])
        actual_input = carried[-1]["input"] if carried else None
        if (
            receipt["binding"] != digest(saved)
            or saved.get("feature_input_binding") != digest(actual_input)
            or saved["graph_binding"] != job["payload"]["graph_binding"]
            or receipt.get("owner_provenance") != "browser-feature-readonly-correction"
            or type(count) is not int
            or count < 1
            or receipt["receipt_binding"]
            != digest({k: v for k, v in receipt.items() if k != "receipt_binding"})
            or saved["decision_id"] != job["payload"]["feature_decision_id"]
            or saved["parent_permission_binding"] != digest(permission)
            or count > len(history)
            or digest(history[:count]) != saved["history_binding"]
            or not _confirmed(c, job, history[:count], permission)
            or saved.get('verified_not_started', []) != _not_started_bindings(c, history[:count])
            or receipt["generation_requests"] != 3
            or receipt["request_seconds"] != 120
            or receipt["completion_tokens"] != 2600
            or any(
                receipt[k] is not False
                for k in ("source_writes", "implementation_dispatch", "publication", "fallback")
            )
            or receipt["source_profile"] != "committed-feature-source-v1"
            or receipt["expires_at"]
            != min(
                datetime.fromisoformat(receipt["approved_at"]) + timedelta(minutes=30),
                datetime.fromisoformat(permission["expires_at"]),
            ).isoformat()
        ):
            raise ValueError(
                "The separate read-only correction permission changed or has uncertain prior work."
            )
        maximum = count + 3
        reference = receipt["command_id"]
        deadline = datetime.fromisoformat(receipt["expires_at"])
    if (
        active
        and reference != "original-parent-run"
        and datetime.now(timezone.utc) + timedelta(seconds=120) >= deadline
    ):
        raise ValueError(
            "The separate read-only correction permission expired; the original feature clock is unchanged."
        )
    return maximum, reference, deadline


def offer(c, identity):
    job = (
        c.execute(
            select(planning_jobs).where(
                planning_jobs.c.intake_id == identity, planning_jobs.c.kind == "feature_execution"
            )
        )
        .mappings()
        .first()
    )
    if (
        not job
        or job["status"] != "failed"
        or job["payload"].get("phase") != "held"
        or job["lease"]
        or not job["finished_at"]
    ):
        return None
    try:
        approval, permission = feature_steps.parent(c, job["payload"]["feature_decision_id"])
        parent = intake.require(c, task_sessions, approval["id"])
        history = job["payload"].get("scope_history", [])
        if (
            permission["scope"]["provider"] != "local"
            or not history
            or parent["status"] != "held"
            or parent["state"].get("cancel_requested")
            or job["payload"].get("current_step_scope")
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
            or datetime.now(timezone.utc) + timedelta(seconds=120)
            >= datetime.fromisoformat(permission["expires_at"])
        ):
            return None
        task_id = history[-1]["task_id"]
        records = _history(job, task_id)
        if not _confirmed(c, job, records, permission) or records[-1].get("scope_binding"):
            return None
        scope = next(item for item in permission["scope"]["graph"] if item["task_id"] == task_id)
        from workbench.db import backlog_items

        task = intake.require(c, backlog_items, task_id)
        # Previous permission may be spent or expired. Its records remain; this
        # command is a separate finite allowance, not an old-clock extension.
        inputs = dict(
            decision_id=approval["id"],
            parent_permission_binding=digest(permission),
            task_id=task_id,
            task_binding=scope["binding"],
            history_binding=digest(records),
            starts_after=len(records),
            previous_failure=job["message"],
            finished_at=job["finished_at"],
            parent_state_binding=digest(parent["state"]),
            graph_binding=job["payload"]["graph_binding"],
            feature_input_binding=digest(
                job["payload"]["completed_steps"][-1]["input"]
                if job["payload"].get("completed_steps")
                else None
            ),
            **({'verified_not_started': _not_started_bindings(c, records)}
               if any(not r.get('confirmed_finished') for r in records) else {}),
        )
        return dict(
            binding=digest(inputs),
            task_title=task["title"],
            generation_requests=3,
            request_seconds=120,
            completion_tokens=2600,
            approved_inputs=inputs,
            message="Prepare this step from its actual committed definitions. This permits up to three additional read-only requests; the original feature run, earlier usage, Stop, implementation limits and publication hold remain unchanged.",
        )
    except ValueError, OSError, HTTPException, KeyError, TypeError, StopIteration:
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
            raise HTTPException(409, "This feature has no saved preparation to continue.")
        commands = job["payload"].get("scope_recoveries", [])
        prior = next((r for r in commands if r["command_id"] == str(body.command_id)), None)
        if prior:
            if prior["binding"] != body.binding:
                raise HTTPException(
                    409, "This correction command is already saved for different work."
                )
            return prior
        selected = offer(c, identity)
        if not selected or selected["binding"] != body.binding:
            raise HTTPException(
                409,
                "The original scope, stopped state, source or settled history changed. Refresh before continuing preparation.",
            )
        inputs = selected["approved_inputs"]
        _, permission = feature_steps.parent(c, inputs["decision_id"])
        at = intake.now()
        value = dict(
            command_id=str(body.command_id),
            binding=body.binding,
            approved_inputs=inputs,
            approved_at=at,
            expires_at=min(
                datetime.fromisoformat(at) + timedelta(minutes=30),
                datetime.fromisoformat(permission["expires_at"]),
            ).isoformat(),
            generation_requests=3,
            request_seconds=120,
            completion_tokens=2600,
            source_writes=False,
            implementation_dispatch=False,
            publication=False,
            fallback=False,
            source_profile="committed-feature-source-v1",
            owner_provenance="browser-feature-readonly-correction",
        )
        receipt = {**value, "receipt_binding": digest(value)}
        retained = dict(
            previous_failure=job["message"],
            finished_at=job["finished_at"],
            previous_claim=job["payload"].get("feature_scope_claim"),
        )
        payload = {
            **job["payload"],
            "phase": "prepare_scopes",
            "scope_recoveries": commands + [receipt],
            "scope_recovery_history": job["payload"].get("scope_recovery_history", []) + [retained],
        }
        c.execute(
            planning_jobs.update()
            .where(planning_jobs.c.id == job["id"])
            .values(
                status="queued",
                finished_at=None,
                payload=payload,
                message="Additional read-only step preparation approved. Earlier attempts and original run limits remain saved.",
            )
        )
        c.execute(
            task_sessions.update()
            .where(task_sessions.c.id == inputs["decision_id"])
            .values(
                status="pending",
                finished_at=None,
                message="Preparing actual source definitions under a separate read-only allowance.",
            )
        )
        return receipt
