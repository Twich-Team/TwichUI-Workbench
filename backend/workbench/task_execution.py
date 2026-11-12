"""Task provenance and delivery bridge. Existing grants/requests own all execution state."""

import os
import json
import re
from uuid import UUID, uuid5

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import select, inspect, func

from workbench import intake
from workbench.db import (
    backlog_items,
    backlog_dependencies,
    intakes,
    proposal_decisions,
    planning_jobs,
    real_execution_grants as grants,
    real_execution_requests as requests,
    real_publications,
)
from workbench.visual_scope import digest


class Waiting(ValueError):
    pass


def require(c, table, identity):
    try:
        return intake.require(c, table, identity)
    except HTTPException:
        raise Waiting("The task or its saved plan is unavailable.") from None


def snapshot(c, intake_id, task_id, approval_id):
    """Pin validated planning inputs, never infer execution permission or repository paths."""
    from workbench.planning_worker import validate_decomposition

    task = require(c, backlog_items, task_id)
    if task["intake_id"] != intake_id or task["kind"] != "task" or not task["proposal_id"]:
        raise Waiting("Only an implementation task from this request can be connected.")
    original = require(c, intakes, intake_id)
    approval = require(c, proposal_decisions, approval_id)
    if approval["intake_id"] != intake_id or approval["proposal_id"] != task["proposal_id"]:
        raise Waiting("The task and applied plan approval do not match.")
    try:
        proposal = intake.validate_approval(c, approval)
        review = intake.proposal_view(c, proposal)
    except HTTPException:
        raise Waiting("Review the current plan before implementation can proceed.") from None
    scope = review["human_scope"]
    if (
        not approval["finished_at"]
        or approval["snapshot"] != review
        or scope["origin"] != "human"
        or scope["stage"] != "implementation"
        or scope["visual_requirement"] != "nonvisual"
        or review["research_only"]
        or proposal["content"]["delivery"] != "implementation"
        or proposal["content"]["visual"] != "nonvisual"
    ):
        raise Waiting("A current applied nonvisual implementation plan approval is required.")
    scope_approval_id = approval_id
    continuation = approval["payload"].get("execution_continuation")
    if continuation:
        parent = require(c, proposal_decisions, continuation["scope_approval_id"])
        try:
            intake.validate_approval(c, parent)
        except HTTPException:
            raise Waiting("The original approved scope is no longer current.") from None
        if (
            parent["intake_id"] != intake_id
            or parent["proposal_id"] != approval["proposal_id"]
            or parent["binding"] != continuation.get("scope_binding")
            or parent["snapshot"] != review
            or not (approval["payload"].get("run_budget") or approval["payload"].get("feature_run"))
            or not isinstance(continuation.get("owner_provenance"), dict)
        ):
            raise Waiting(
                "Continuation must retain the exact original scope and explicit new owner budget."
            )
        scope_approval_id = parent["id"]
    job = (
        c.execute(
            select(planning_jobs).where(
                planning_jobs.c.intake_id == intake_id,
                planning_jobs.c.kind == "decomposition",
                planning_jobs.c.revision == proposal["revision"],
            )
        )
        .mappings()
        .first()
    )
    if (
        not job
        or job["status"] != "completed"
        or not job["finished_at"]
        or any(
            job["payload"].get(k) != v
            for k, v in dict(
                proposal_id=proposal["id"],
                approval_id=scope_approval_id,
                binding=review["binding"],
                scope_revision=scope["revision"],
            ).items()
        )
    ):
        raise Waiting("Task planning for this exact approved plan has not finished.")
    data = job["payload"].get("decomposition")
    if not data:
        raise Waiting(
            "This older task has no saved scope mapping. It cannot be connected automatically."
        )
    try:
        parsed = validate_decomposition(data, proposal["content"])
    except ValueError, KeyError, TypeError:
        raise Waiting("The saved task scope mapping is invalid.") from None
    root = "intake-" + intake_id
    root_row = require(c, backlog_items, root)
    if (
        root_row["intake_id"] != intake_id
        or root_row["kind"] != "request"
        or root_row["parent"] is not None
    ):
        raise Waiting("The task request relationship does not match its submitted request.")
    ids = {t.key: root + "-" + str(proposal["revision"]) + "-" + t.key for t in parsed.tasks}
    persisted = {
        t["id"]: dict(t)
        for t in c.execute(
            select(backlog_items).where(
                backlog_items.c.intake_id == intake_id,
                backlog_items.c.proposal_id == proposal["id"],
                backlog_items.c.kind != "request",
            )
        ).mappings()
    }
    if set(persisted) != set(ids.values()):
        raise Waiting("The task list differs from its saved approved scope mapping.")
    for spec in parsed.tasks:
        item = persisted[ids[spec.key]]
        deps = sorted(
            c.execute(
                select(backlog_dependencies.c.requires_id).where(
                    backlog_dependencies.c.item_id == item["id"]
                )
            ).scalars()
        )
        if (
            any(item[k] != getattr(spec, k) for k in ("kind", "title", "purpose", "acceptance"))
            or item["parent"] != (ids[spec.parent] if spec.parent else root)
            or deps != sorted(ids[k] for k in spec.depends_on)
            or item["model"] != job["model"]
        ):
            raise Waiting(
                "A task or prerequisite changed after planning. Its earlier permission cannot be reused."
            )
    spec = next(t for t in parsed.tasks if ids[t.key] == task_id)
    dependencies = sorted(ids[k] for k in spec.depends_on)
    result = dict(
        version=1,
        intake_id=intake_id,
        original=original["payload"],
        task=task,
        scope_indices=spec.scope_indices,
        approved_scope=[proposal["content"]["scope"][i] for i in spec.scope_indices],
        dependencies=dependencies,
        proposal_review=review,
        approval={k: approval[k] for k in ("id", "binding", "payload", "snapshot", "finished_at")},
        decomposition=dict(
            id=job["id"],
            revision=job["revision"],
            payload=job["payload"],
            model=job["model"],
            finished_at=job["finished_at"],
        ),
    )
    if len(json.dumps(result, ensure_ascii=True).encode()) > 65536:
        raise Waiting(
            "Task evidence exceeds the bounded execution snapshot size. Execution is held."
        )
    return result


def current(c, linked):
    try:
        actual = snapshot(c, linked["intake_id"], linked["task"]["id"], linked["approval"]["id"])
        return actual == linked
    except Waiting, KeyError, TypeError:
        return False


def prerequisite_reason(c, linked, feature_input=None):
    """A checked result is evidence, not acceptance. Require matching current task provenance."""
    for identity in linked["dependencies"]:
        rows = c.execute(
            select(
                requests.c.result["verification"].as_string().label("verification"),
                requests.c.result["candidate_id"].as_string().label("candidate_id"),
                requests.c.result["manifest"]["workflow_task_binding"]
                .as_string()
                .label("task_binding"),
                grants.c.snapshot.label("grant_snapshot"),
            )
            .join(grants, requests.c.grant_id == grants.c.id)
            .where(
                grants.c.snapshot["workflow_task"]["task"]["id"].as_string() == identity,
                requests.c.status == "checked",
            )
            .order_by(requests.c.created_at.desc())
            .limit(16)
        ).mappings()
        good = False
        for row in rows:
            parent = row["grant_snapshot"].get("workflow_task")
            if (
                parent
                and current(c, parent)
                and row["verification"] == "passed"
                and row["candidate_id"]
                and row["task_binding"] == digest(parent)
            ):
                good = True
                break
        if not good:
            return "Waiting for a prerequisite task to finish with current independently checked changes."
    if feature_input is not None:
        try:
            from workbench import feature_source
            from workbench.trusted_preparation import configuration
            if not linked['original'].get('feature_planning'):
                raise ValueError('Only a Feature Workshop task can use a checked feature input.')
            carried = feature_source.prerequisite_tasks(c, feature_input, linked, configuration(c.engine))
            if not set(linked['dependencies']) <= carried:
                raise ValueError('Required predecessor changes are missing from this checked input.')
        except (ValueError, OSError, KeyError, TypeError, HTTPException):
            return 'The checked predecessor source is unavailable, stale, or does not carry every required change.'
        return None
    if linked["dependencies"]:
        return (
            "Prerequisite checks passed, but their changes are not part of this pinned base. "
            "Dependent execution waits for a supported verified-base handoff."
        )
    return None


def validate(c, grant, prerequisites=True):
    linked = grant["snapshot"].get("workflow_task")
    if linked is None:
        return  # Legacy real grants keep their existing meaning.
    if not current(c, linked):
        raise Waiting("The task, plan approval or scope mapping changed. Execution is held.")
    if grant['snapshot'].get('operation_kind')=='feature-verification-v1':
        from workbench.feature_verification import authority
        try:
            authority(c,grant['snapshot'])
        except (ValueError,OSError,HTTPException,KeyError,TypeError):
            raise Waiting('The exact combined feature check authority or source changed.') from None
        return
    if grant["snapshot"].get("task_budget"):
        from workbench.task_budget import permission

        try:
            actual = permission(c, require(c, proposal_decisions, linked["approval"]["id"]),
                task_id=linked['task']['id'], offer_id=grant['snapshot']['task_budget']['offer']['id'])
        except ValueError, HTTPException:
            raise Waiting("The explicit task budget expired or changed.") from None
        if actual != grant["snapshot"]["task_budget"]:
            raise Waiting("The explicit task budget changed or expired.")
    if prerequisites:
        feature_input = grant['snapshot'].get('feature_input')
        reason = prerequisite_reason(c, linked, feature_input)
        if not reason and feature_input and (
                grant['snapshot']['contract']['source'] != feature_input['source']
                or grant['snapshot']['contract']['base'] != feature_input['commit']):
            reason = 'The execution source does not match the checked predecessor input.'
        if reason:
            raise Waiting(reason)


def request_id(grant_id):
    return str(uuid5(UUID(grant_id), "workbench-task-execution-v1"))


def dispatch_one(engine):
    """Existing worker calls this only with an explicitly configured runtime; never issue a grant."""
    from workbench import real_execution as execution

    with engine.connect() as c:
        candidates = (
            c.execute(
                select(grants)
                .outerjoin(requests, requests.c.grant_id == grants.c.id)
                .where(
                    requests.c.id.is_(None),
                    grants.c.revoked_at.is_(None),
                    grants.c.expires_at > execution.now(),
                    grants.c.snapshot["workflow_task"]["task"]["id"].as_string().is_not(None),
                )
                .order_by(grants.c.issued_at, grants.c.id)
                .limit(64)
            )
            .mappings()
            .all()
        )
    for grant in candidates:
        try:
            execution.submit(engine, grant["id"], request_id(grant["id"]))
            return True
        except ValueError:
            continue  # Read-only task context explains the waiting reason; no attempt consumed.
    return False


class TaskPublication(BaseModel):
    status: str
    message: str
    url: str | None = None
    published: bool = False


class TaskRun(BaseModel):
    publication: TaskPublication | None = None
    id: str
    status: str
    message: str
    verification: str | None = None
    candidate_id: str | None = None
    current_task: bool
    recovery_pending: bool = False


class TaskSessionView(BaseModel):
    id: str
    status: str
    message: str
    cancel_requested: bool
    can_stop: bool


class TaskExecutionContext(BaseModel):
    session: TaskSessionView | None = None
    run_budget: dict | None = None
    intake_id: str
    task_id: str
    stage: str
    message: str
    current_approval: bool
    execution_configured: bool
    runs: list[TaskRun]
    publication_allowed: bool = False
    accepted: bool = False


def context(engine, intake_id, task_id):
    with engine.connect() as c:
        c.exec_driver_sql("BEGIN")
        return context_in(c, intake_id, task_id)


def context_in(c, intake_id, task_id):
    """Project a task inside the caller's consistent read transaction."""
    from workbench import real_execution as execution
    from workbench.task_sessions import public_view, capability

    configured = bool(os.environ.get("WORKBENCH_REAL_EXECUTION_ROOT"))
    one_off = capability(c.engine) is not None
    task = intake.require(c, backlog_items, task_id)
    if task["intake_id"] != intake_id or task["kind"] != "task":
        raise HTTPException(404, "Task not found for this request.")
    linked = None
    reason = "The current plan needs an applied implementation approval."
    approval = (
        c.execute(
            select(proposal_decisions)
            .where(
                proposal_decisions.c.proposal_id == task["proposal_id"],
                proposal_decisions.c.status == "applied",
                proposal_decisions.c.payload["action"].as_string() == "approve",
            )
            .order_by(proposal_decisions.c.created_at.desc())
            .limit(1)
        )
        .mappings()
        .first()
    )
    if approval:
        try:
            linked = snapshot(c, intake_id, task_id, approval["id"])
            reason = "Waiting for separate execution permission. Plan approval alone starts no implementation."
        except Waiting as error:
            reason = str(error)
    available = inspect(c).has_table(requests.name)
    records = (
        c.execute(
            select(
                requests.c.id,
                requests.c.status,
                func.substr(requests.c.message, 1, 1000).label("message"),
                requests.c.result["verification"].as_string().label("verification"),
                requests.c.result["candidate_id"].as_string().label("candidate_id"),
                requests.c.result["delivery"]["state"].as_string().label("recovery_state"),
                requests.c.result["owner"].label("execution_owner"),
                grants.c.snapshot.label("grant_snapshot"),
            )
            .join(grants, requests.c.grant_id == grants.c.id)
            .where(
                grants.c.snapshot["workflow_task"]["intake_id"].as_string() == intake_id,
                grants.c.snapshot["workflow_task"]["task"]["id"].as_string() == task_id,
            )
            .order_by(requests.c.created_at.desc(), requests.c.id.desc())
            .limit(10)
        )
        .mappings()
        .all()
        if available
        else []
    )
    publications = {}
    if records and inspect(c).has_table(real_publications.name):
        for publication in c.execute(
            select(real_publications).where(
                real_publications.c.execution_id.in_([r["id"] for r in records])
            )
        ).mappings():
            url = publication["state"].get("url")
            real = (
                publication["state"].get("source") == "github"
                and isinstance(url, str)
                and re.fullmatch(
                    r"https://github.com/Twich-Team/TwichUI/pull/[1-9][0-9]{0,9}", url
                )
            )
            publications[publication["execution_id"]] = dict(
                status=publication["status"],
                message=publication["message"],
                url=url if real else None,
                published=bool(real and publication["status"] == "published"),
            )
    runs = []
    for row in records:
        recovering, notice = execution.recovery_notice(
            row["status"],
            dict(owner=row["execution_owner"], delivery=dict(state=row["recovery_state"])),
        )
        runs.append(
            dict(
                id=row["id"],
                publication=publications.get(row["id"]),
                status=row["status"],
                message=notice or row["message"],
                verification=row["verification"],
                candidate_id=row["candidate_id"],
                recovery_pending=recovering,
                current_task=linked is not None
                and linked == row["grant_snapshot"].get("workflow_task"),
            )
        )
    runs.sort(key=lambda r: not r["current_task"])
    stage = "waiting"
    if linked is not None and available:
        grant = (
            c.execute(
                select(grants)
                .where(
                    grants.c.snapshot["workflow_task"]["task"]["id"].as_string() == task_id,
                    grants.c.snapshot["workflow_task"]["intake_id"].as_string() == intake_id,
                    grants.c.revoked_at.is_(None),
                    grants.c.expires_at > execution.now(),
                )
                .order_by(grants.c.issued_at.desc(), grants.c.id.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
        if (
            grant
            and execution.valid(grant)
            and grant["snapshot"].get("workflow_task") == linked
        ):
            reason = prerequisite_reason(c, linked) or (
                "Waiting for the worker to pick up this task."
                if configured or one_off
                else "Execution is disabled here. The task is saved; no model will be invoked."
            )
    if runs:
        relevant = next((r for r in runs if r["current_task"]), runs[0])
        stage = relevant["status"]
        reason = result_message(relevant)
        if relevant["recovery_pending"]:
            stage = "recovering"
        if stage == "pending":
            pending_grant = (
                c.execute(
                    select(grants)
                    .join(requests, requests.c.grant_id == grants.c.id)
                    .where(requests.c.id == relevant["id"])
                )
                .mappings()
                .one()
            )
            if not execution.valid(pending_grant):
                reason = (
                    "Execution permission is no longer current. The saved request cannot start."
                )
            elif not relevant["current_task"]:
                reason = (
                    "Task inputs changed. The saved request cannot use the earlier permission."
                )
            elif not (configured or one_off):
                reason = "Execution is disabled here. The saved request is waiting; no model will start."
    if not available:
        reason = (
            "Task execution records are unavailable in this store version. No work starts here."
        )
    budget_status = None
    if approval and approval["payload"].get("run_budget"):
        from workbench.task_budget import permission
        from datetime import datetime, timezone

        try:
            approved_budget = permission(c, approval, require_current=False)
            budget_status = dict(
                expires_at=approved_budget["expires_at"],
                publication=approved_budget["budget"]["publication"],
                current=datetime.now(timezone.utc)
                < datetime.fromisoformat(approved_budget["expires_at"]),
            )
            if stage == "waiting":
                reason = (
                    ("Approval applied. Waiting for the worker to prepare the approved task."
                     if configured or one_off else
                     "Approved task budget is saved. Execution is disabled here.")
                    if budget_status["current"]
                    else "The approved task budget expired. Saved work remains; no execution or publication will start."
                )
        except ValueError, HTTPException:
            if stage == "waiting":
                reason = "The saved task budget or supported recipe changed. Review is required; no execution starts."
    operation = public_view(c, task_id)
    if operation and operation["status"] == "waiting_recovery":
        stage, reason = "recovering", operation["message"]
    elif operation and operation["status"] == "active" and any(
        r["current_task"] and r["status"] == "checked" for r in runs
    ):
        stage, reason = "settling", "Checks passed. Waiting for the worker to finish cleanup."
    elif operation and operation["status"] in {
        "pending",
        "active",
        "held",
        "cancelled",
        "expired",
        "ended-before-result",
    }:
        if not any(
            r["current_task"]
            and r["status"] in {"running", "checking", "checked", "cancel_requested"}
            for r in runs
        ):
            stage = (
                "preparing"
                if operation["status"] in {"pending", "active"}
                else "held" if operation["status"] in {"expired", "ended-before-result"}
                else operation["status"]
            )
            reason = operation["message"]
    return dict(
        session=operation,
        run_budget=budget_status,
        intake_id=intake_id,
        task_id=task_id,
        stage=stage,
        message=reason,
        current_approval=linked is not None,
        execution_configured=configured or one_off,
        runs=runs,
        publication_allowed=False,
        accepted=False,
    )


def result_message(run):
    """Current delivery projection; never rewrite the historical execution message."""
    publication = run.get("publication")
    if run["status"] == "checked" and publication:
        if publication.get("published"):
            return "Independent checks passed. Draft PR delivered for human review and merge. This result has not been accepted."
        if publication["status"] in {"pending", "preparing", "pushing", "creating", "reconciling"}:
            return "Independent checks passed. Draft PR delivery is in progress; its outcome is recorded separately."
        if publication["status"] == "held":
            return (
                "Independent checks passed. Draft delivery needs attention: "
                + publication["message"][:600]
            )
    return run["message"]
