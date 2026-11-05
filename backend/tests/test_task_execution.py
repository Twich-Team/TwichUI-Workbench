"""Controlled application-owned submission→task→worker→frozen handoff. Never model evidence."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import copy
import json
from uuid import uuid4

import pytest
from sqlalchemy import select
from fastapi import HTTPException

from workbench import (
    admission,
    planning_worker,
    real_execution as execution,
    task_execution as bridge,
)
from workbench.db import (
    backlog_items,
    backlog_dependencies,
    intakes,
    proposal_decisions,
    planning_jobs,
    intake_scopes,
    model_attempts,
    write_transaction,
)
from workbench.schemas import DecompositionOutput
from test_real_handoff import HandoffRuntime
from workbench.real_workspace import durable


class ControlledPlanner:
    """Retained deterministic data, no model or semantic capability-selection proof."""

    def generate(self, packet, schema):
        fixture = json.loads(admission.FIXTURE.read_text())
        result = copy.deepcopy(
            fixture["decomposition"] if schema is DecompositionOutput else fixture["planning"]
        )
        if schema is DecompositionOutput:
            result["tasks"][0]["title"] = "NON-MODEL controlled task"
        return result, "NON-MODEL planner", "demo"


def prepared(client, headers, store, planner=None):
    planner = planner or ControlledPlanner()
    identity = str(uuid4())
    body = dict(
        request_id=identity,
        kind="feature",
        title="NON-MODEL task bridge exercise",
        description="Controlled task-to-result integration only; no addon or model run.",
        visual_requirement="nonvisual",
    )
    assert client.post("/api/intake", headers=headers, json=body).status_code == 200
    assert planning_worker.process_one(store, planner)
    data = client.get("/api/intake/" + identity).json()
    p = data["proposals"][0]
    decision = dict(
        command_id=str(uuid4()),
        proposal_id=p["id"],
        binding=p["binding"],
        scope_revision=p["scope_revision"],
        action="approve",
        note="DISPOSABLE TEST approval; no real authority",
    )
    assert (
        client.post(
            "/api/intake/" + identity + "/decisions", headers=headers, json=decision
        ).status_code
        == 200
    )
    assert planning_worker.apply_one(store)
    assert planning_worker.process_one(store, planner)
    data = client.get("/api/intake/" + identity).json()
    task = next(t for t in data["tasks"] if t["kind"] == "task")
    return dict(intake_id=identity, task_id=task["id"], approval_id=decision["command_id"])


@pytest.fixture
def linked(client, headers, store, tmp_path):
    task = prepared(client, headers, store)
    root = tmp_path / "NON_MODEL"
    root.mkdir()
    durable(root / "oracle.lua", b"-- NON-MODEL stub, never executed")
    durable(root / "base-manifest.json", b"{}")
    runtime = HandoffRuntime(root)
    runtime.calls = 0
    original = runtime.execute

    def execute(*a):
        runtime.calls += 1
        return original(*a)

    runtime.execute = execute
    grant = execution.issue(
        store,
        "NON-MODEL trusted task binding " + str(uuid4()),
        "disposable pytest",
        runtime,
        task=task,
    )
    return task, runtime, grant


def context(client, task):
    result = client.get(f"/api/intake/{task['intake_id']}/tasks/{task['task_id']}/execution")
    assert result.status_code == 200, result.text
    return result.json()


def request(store, grant):
    with store.connect() as c:
        return dict(
            c.execute(
                select(execution.requests).where(execution.requests.c.grant_id == grant["id"])
            )
            .mappings()
            .one()
        )


def mutate(store, table, identity, **values):
    with write_transaction(store) as c:
        c.execute(table.update().where(table.c.id == identity).values(**values))


def test_complete_submission_to_task_frozen_result_and_get_review(client, store, linked):
    task, runtime, grant = linked
    before = context(client, task)
    assert before["stage"] == "waiting" and not before["execution_configured"]
    assert "disabled" in before["message"]
    assert execution.process_one(store, runtime)
    row = request(store, grant)
    assert row["id"] == bridge.request_id(grant["id"]) and row["status"] == "checked"
    assert not execution.process_one(store, runtime) and runtime.calls == 1
    view = client.get("/api/real-executions/" + row["id"]).json()
    assert (
        view["title"] == "NON-MODEL controlled task"
        and view["workflow_task"]["task_id"] == task["task_id"]
    )
    assert (
        view["current_task"] and view["source"] == "real" and view["publication_allowed"] is False
    )
    assert view["result"]["inference_ran"] is False
    assert view["result"]["manifest"]["workflow_task"] == dict(
        intake_id=task["intake_id"], task_id=task["task_id"]
    )
    with store.connect() as c:
        ledger = (
            c.execute(select(model_attempts).where(model_attempts.c.execution_id == row["id"]))
            .mappings()
            .one()
        )
    assert (
        ledger["task_id"] == task["task_id"]
        and ledger["outcome"] == "checked"
        and ledger["usage"]["status"] == "complete"
    )
    after = context(client, task)
    assert (
        after["stage"] == "checked" and not after["accepted"] and not after["publication_allowed"]
    )
    assert len(after["runs"]) == 1 and after["runs"][0]["current_task"]
    assert execution.listing(store)["items"][0]["title"] == "NON-MODEL controlled task"


def test_approval_alone_never_issues_grant_or_runs(client, headers, store, monkeypatch):
    task = prepared(client, headers, store)
    monkeypatch.delenv("WORKBENCH_REAL_EXECUTION_ROOT", raising=False)
    assert "separate execution permission" in context(client, task)["message"]
    assert not execution.process_one(store)
    with store.connect() as c:
        assert (
            not c.execute(select(execution.grants)).all()
            and not c.execute(select(execution.requests)).all()
        )


def test_disabled_worker_does_not_auto_submit_a_linked_grant(store, linked, monkeypatch):
    _, runtime, grant = linked
    monkeypatch.delenv("WORKBENCH_REAL_EXECUTION_ROOT", raising=False)
    assert not execution.process_one(store) and runtime.calls == 0
    with store.connect() as c:
        assert not c.execute(select(execution.requests)).all()


@pytest.mark.parametrize(
    "change",
    [
        "task",
        "original",
        "approval",
        "human_scope",
        "mapping",
        "mapping_missing",
        "decomposition",
        "dependency",
    ],
)
def test_changed_task_provenance_does_not_dispatch_or_consume(client, store, linked, change):
    task, runtime, grant = linked
    pin = grant["snapshot"]["workflow_task"]
    if change == "task":
        mutate(store, backlog_items, task["task_id"], purpose="tampered purpose")
    elif change == "original":
        with store.connect() as c:
            row = (
                c.execute(select(intakes).where(intakes.c.id == task["intake_id"])).mappings().one()
            )
        mutate(
            store, intakes, task["intake_id"], payload={**row["payload"], "description": "changed"}
        )
    elif change == "approval":
        mutate(store, proposal_decisions, task["approval_id"], status="stale")
    elif change == "human_scope":
        with write_transaction(store) as c:
            c.execute(
                intake_scopes.update()
                .where(intake_scopes.c.intake_id == task["intake_id"])
                .values(stage="research")
            )
    elif change in ["mapping", "mapping_missing", "decomposition"]:
        payload = copy.deepcopy(pin["decomposition"]["payload"])
        if change == "mapping":
            payload["decomposition"]["tasks"][0]["scope_indices"] = [99]
        elif change == "mapping_missing":
            payload.pop("decomposition")
        else:
            payload["approval_id"] = str(uuid4())
        mutate(store, planning_jobs, pin["decomposition"]["id"], payload=payload)
    else:
        with write_transaction(store) as c:
            c.execute(
                backlog_dependencies.insert().values(
                    item_id=task["task_id"], requires_id="intake-" + task["intake_id"]
                )
            )
    assert not execution.process_one(store, runtime) and runtime.calls == 0
    with store.connect() as c:
        assert not c.execute(select(execution.requests)).all()
    assert not context(client, task)["runs"]


def test_claim_revalidates_after_request_saved_and_permission_rechecks_during_work(
    client, store, linked
):
    task, runtime, grant = linked
    row = execution.submit(store, grant["id"], bridge.request_id(grant["id"]))
    assert row["status"] == "pending"
    mutate(store, proposal_decisions, task["approval_id"], scope_review_required=1)
    assert execution.process_one(store, runtime)
    assert request(store, grant)["status"] == "blocked" and runtime.calls == 0


def test_changed_approval_during_work_fails_and_retains_history(client, store, linked):
    task, runtime, grant = linked
    original = runtime.execute

    def execute(*args):
        result = original(*args)
        mutate(store, proposal_decisions, task["approval_id"], scope_review_required=1)
        return result

    runtime.execute = execute
    assert execution.process_one(store, runtime)
    assert request(store, grant)["status"] == "failed" and runtime.calls == 1
    assert not context(client, task)["current_approval"]


def test_duplicate_dispatch_delivery_and_concurrent_workers_use_one_attempt(store, linked):
    _, runtime, grant = linked

    def handle(_):
        from sqlalchemy.exc import OperationalError

        try:
            return execution.process_one(store, runtime)
        except OperationalError:
            return False

    with ThreadPoolExecutor(2) as pool:
        list(pool.map(handle, range(2)))
    row = request(store, grant)
    assert runtime.calls == 1 and row["status"] == "checked"
    assert execution.submit(store, grant["id"], row["id"])["id"] == row["id"]
    with pytest.raises(ValueError):
        execution.submit(store, grant["id"], str(uuid4()))
    assert not bridge.dispatch_one(store)
    with store.connect() as c:
        assert (
            len(
                c.execute(
                    select(model_attempts).where(model_attempts.c.execution_id == row["id"])
                ).all()
            )
            == 1
        )


def test_distinct_grants_cannot_run_same_task_twice(store, linked):
    task, runtime, grant = linked
    other = execution.issue(
        store, "NON-MODEL second explicit instruction", "pytest", runtime, task=task
    )
    assert other["id"] != grant["id"]
    assert execution.process_one(store, runtime)
    assert not execution.process_one(store, runtime)
    assert runtime.calls == 1
    with store.connect() as c:
        assert len(c.execute(select(execution.requests)).all()) == 1


def test_failed_candidate_stays_consumed_without_automatic_retry(store, linked):
    _, runtime, grant = linked
    original = runtime.execute

    def execute(*args):
        value = original(*args)
        value["verification"] = "failed"
        return value

    runtime.execute = execute
    runtime.verify_frozen = lambda _: True
    assert execution.process_one(store, runtime)
    assert request(store, grant)["status"] == "failed"
    assert not execution.process_one(store, runtime) and runtime.calls == 1


def test_pending_cancel_exact_retry_no_attempt_and_no_replay(client, headers, store, linked):
    task, runtime, grant = linked
    assert bridge.dispatch_one(store)
    row = request(store, grant)
    command = str(uuid4())
    path = "/api/real-executions/" + row["id"] + "/stop"
    assert client.post(path, json={"command_id": command}).status_code == 403
    assert (
        client.post(path, headers=headers, json={"command_id": command}).json()["status"]
        == "cancelled"
    )
    assert (
        client.post(path, headers=headers, json={"command_id": command}).json()["status"]
        == "cancelled"
    )
    assert client.post(path, headers=headers, json={"command_id": str(uuid4())}).status_code == 409
    assert not execution.process_one(store, runtime) and runtime.calls == 0
    assert context(client, task)["stage"] == "cancelled"
    with store.connect() as c:
        assert not c.execute(
            select(model_attempts).where(model_attempts.c.execution_id == row["id"])
        ).all()


@pytest.mark.parametrize("safe", [True, False])
def test_active_cancellation_fences_late_success_and_holds_uncertain_cleanup(store, linked, safe):
    task, runtime, grant = linked
    original = runtime.execute
    cleanup = []

    def execute(identity, fence, permitted):
        value = original(identity, fence, permitted)
        execution.cancel(store, identity, str(uuid4()))
        assert not permitted()
        return value

    runtime.execute = execute
    runtime.cleanup = lambda row: cleanup.append(row["id"]) or safe
    assert execution.process_one(store, runtime)
    row = request(store, grant)
    assert row["status"] == ("cancelled" if safe else "cancel_requested") and runtime.calls == 1
    assert len(cleanup) == 1 and "candidate_id" not in row["result"]
    if not safe:
        assert row["result"]["handler_returned"]
        runtime.cleanup = lambda row: True
        execution.recover(store, runtime)
        assert request(store, grant)["status"] == "cancelled"
    assert not execution.process_one(store, runtime) and runtime.calls == 1


def test_stop_cannot_touch_standalone_or_completed_records(store, linked):
    _, runtime, grant = linked
    execution.process_one(store, runtime)
    with pytest.raises(HTTPException) as error:
        execution.cancel(store, request(store, grant)["id"], str(uuid4()))
    assert error.value.status_code == 409


def test_crash_after_seal_recovers_task_result_without_duplicate(store, linked, monkeypatch):
    task, runtime, grant = linked
    save = runtime.record_completion

    def crash(*a):
        save(*a)
        raise SystemExit("controlled interruption after immutable seal")

    runtime.record_completion = crash
    with pytest.raises(SystemExit):
        execution.process_one(store, runtime)
    monkeypatch.setattr(execution, "alive", lambda _: False)
    runtime.execute = lambda *a: pytest.fail("No model or check replay")
    execution.recover(store, runtime)
    execution.recover(store, runtime)
    assert request(store, grant)["status"] == "checked" and runtime.calls == 1
    view = bridge.context(store, task["intake_id"], task["task_id"])
    assert view["runs"][0]["current_task"]


def test_crash_before_seal_is_consumed_and_retains_task_link(store, linked, monkeypatch):
    task, runtime, grant = linked

    def crash(*a):
        raise SystemExit("controlled interruption before result")

    runtime.execute = crash
    with pytest.raises(SystemExit):
        execution.process_one(store, runtime)
    monkeypatch.setattr(execution, "alive", lambda _: False)
    execution.recover(store, runtime)
    assert request(store, grant)["status"] == "interrupted"
    assert not execution.process_one(store, runtime)
    assert (
        bridge.context(store, task["intake_id"], task["task_id"])["runs"][0]["id"]
        == request(store, grant)["id"]
    )


def test_checked_history_remains_when_current_scope_changes(client, store, linked):
    task, runtime, grant = linked
    execution.process_one(store, runtime)
    mutate(store, proposal_decisions, task["approval_id"], scope_review_required=1)
    data = context(client, task)
    assert (
        data["stage"] == "checked"
        and not data["current_approval"]
        and not data["runs"][0]["current_task"]
    )
    view = execution.review(store, request(store, grant)["id"])
    assert view["current_task"] is False and view["result"]["verification"] == "passed"


def test_wrong_frozen_task_binding_cannot_qualify(store, linked):
    _, runtime, grant = linked
    original = runtime.execute

    def execute(*args):
        value = original(*args)
        value["manifest"]["workflow_task_binding"] = "0" * 64
        return value

    runtime.execute = execute
    runtime.verify_frozen = lambda _: True
    execution.process_one(store, runtime)
    assert request(store, grant)["status"] == "failed"


def test_expired_or_revoked_grant_never_dispatches(store, linked):
    _, runtime, grant = linked
    mutate(
        store,
        execution.grants,
        grant["id"],
        expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
    )
    assert not execution.process_one(store, runtime) and runtime.calls == 0


def test_wrong_intake_and_unsupported_root_have_no_execution_context(client, linked):
    task, _, _ = linked
    assert client.get(f"/api/intake/{uuid4()}/tasks/{task['task_id']}/execution").status_code == 404
    assert (
        client.get(
            f"/api/intake/{task['intake_id']}/tasks/intake-{task['intake_id']}/execution"
        ).status_code
        == 404
    )


def test_unsealed_live_cancellation_never_cleans_active_handler(store, linked):
    _, runtime, grant = linked

    def crash(identity, fence, permitted):
        execution.cancel(store, identity, str(uuid4()))
        raise SystemExit("simulated live handler pause")

    runtime.execute = crash
    with pytest.raises(SystemExit):
        execution.process_one(store, runtime)
    runtime.cleanup = lambda _: pytest.fail("Live unreturned handler cannot be cleaned by recovery")
    execution.recover(store, runtime)
    assert request(store, grant)["status"] == "cancel_requested"


def test_cancellation_recovery_respects_other_live_owner_and_dead_cleanup(
    store, linked, monkeypatch
):
    _, runtime, grant = linked

    def crash(identity, fence, permitted):
        execution.cancel(store, identity, str(uuid4()))
        raise SystemExit("simulated worker exit")

    runtime.execute = crash
    with pytest.raises(SystemExit):
        execution.process_one(store, runtime)
    owner = execution.owner_identity()
    monkeypatch.setattr(
        execution, "owner_identity", lambda: {**owner, "pid": owner["pid"] + 100000}
    )
    calls = []
    runtime.cleanup = lambda row: calls.append(row["id"]) or True
    execution.recover(store, runtime)
    assert calls == []
    monkeypatch.setattr(execution, "alive", lambda _: False)
    execution.recover(store, runtime)
    assert request(store, grant)["status"] == "cancelled" and len(calls) == 1


def test_cleanup_exception_holds_lane_and_preserves_stop_command(store, linked):
    _, runtime, grant = linked
    original = runtime.execute

    def execute(identity, fence, permitted):
        result = original(identity, fence, permitted)
        execution.cancel(store, identity, str(uuid4()))
        return result

    def unsafe(_):
        raise OSError("controlled cleanup unavailable")

    runtime.execute = execute
    runtime.cleanup = unsafe
    assert execution.process_one(store, runtime)
    row = request(store, grant)
    assert (
        row["status"] == "cancel_requested"
        and row["result"]["handler_returned"]
        and row["result"]["stop_command"]
    )
    execution.recover(store, runtime)
    assert request(store, grant)["status"] == "cancel_requested"


def test_cancellation_after_verified_seal_does_not_recover_success(store, linked, monkeypatch):
    _, runtime, grant = linked
    original = runtime.record_completion

    def stop_after_seal(g, identity, fence, result, at):
        original(g, identity, fence, result, at)
        execution.cancel(store, identity, str(uuid4()))

    runtime.record_completion = stop_after_seal
    assert execution.process_one(store, runtime)
    assert request(store, grant)["status"] == "cancelled"
    execution.recover(store, runtime)
    assert request(store, grant)["status"] == "cancelled"
    with store.connect() as c:
        attempt = (
            c.execute(
                select(model_attempts).where(
                    model_attempts.c.execution_id == request(store, grant)["id"]
                )
            )
            .mappings()
            .one()
        )
    assert attempt["usage"]["status"] == "complete" and attempt["outcome"] == "cancelled"


def test_infrastructure_failure_keeps_known_usage_and_task_identity(store, linked):
    task, runtime, grant = linked
    runtime.failure_evidence = lambda *args: dict(
        usage=dict(
            status="partial",
            values={"completion_tokens": 17},
            provenance={"method": "NON-MODEL retained turn observation"},
        )
    )

    def fail(*args):
        raise OSError("NON-MODEL transport interruption")

    runtime.execute = fail
    execution.process_one(store, runtime)
    row = request(store, grant)
    assert row["status"] == "failed" and row["result"]["usage"]["values"]["completion_tokens"] == 17
    with store.connect() as c:
        ledger = (
            c.execute(select(model_attempts).where(model_attempts.c.execution_id == row["id"]))
            .mappings()
            .one()
        )
    assert ledger["task_id"] == task["task_id"] and ledger["usage"]["status"] == "partial"


def test_current_grant_identity_does_not_authorize_a_different_task(client, headers, store, linked):
    task, runtime, grant = linked
    other = prepared(client, headers, store)
    with pytest.raises(ValueError):
        execution.issue(
            store, grant["snapshot"]["owner_instruction"], "disposable pytest", runtime, task=other
        )
    assert not context(client, other)["runs"]


def test_legacy_task_mapping_is_not_reconstructed_or_backfilled(client, store, linked):
    task, runtime, grant = linked
    job = grant["snapshot"]["workflow_task"]["decomposition"]
    payload = {k: v for k, v in job["payload"].items() if k != "decomposition"}
    mutate(store, planning_jobs, job["id"], payload=payload)
    with pytest.raises(ValueError, match="older task"):
        execution.issue(store, "NON-MODEL no mapping", "pytest", runtime, task=task)
    assert "older task" in context(client, task)["message"]
    with store.connect() as c:
        assert (
            c.execute(
                select(planning_jobs.c.payload).where(planning_jobs.c.id == job["id"])
            ).scalar_one()
            == payload
        )


def test_dependencies_wait_even_after_checked_result_until_base_handoff_exists(
    client, headers, store, tmp_path
):
    class Dependent(ControlledPlanner):
        def generate(self, packet, schema):
            value, model, basis = super().generate(packet, schema)
            if schema is DecompositionOutput:
                parent = value["tasks"][0]
                child = copy.deepcopy(parent)
                child.update(
                    key="followup", title="NON-MODEL dependent task", depends_on=[parent["key"]]
                )
                value["tasks"].append(child)
            return value, model, basis

    task = prepared(client, headers, store, Dependent())
    with store.connect() as c:
        child = (
            c.execute(
                select(backlog_items).where(
                    backlog_items.c.intake_id == task["intake_id"],
                    backlog_items.c.title == "NON-MODEL dependent task",
                )
            )
            .mappings()
            .one()
        )
    root = tmp_path / "deps"
    root.mkdir()
    durable(root / "oracle.lua", b"-- NON-MODEL")
    durable(root / "base-manifest.json", b"{}")
    runtime = HandoffRuntime(root)
    first = execution.issue(store, "NON-MODEL first dependency", "pytest", runtime, task=task)
    assert first["snapshot"]["workflow_task"]["task"]["id"] == task["task_id"]
    next_task = {**task, "task_id": child["id"]}
    second = execution.issue(store, "NON-MODEL dependent grant", "pytest", runtime, task=next_task)
    with store.connect() as c:
        assert "prerequisite task" in bridge.prerequisite_reason(
            c, second["snapshot"]["workflow_task"]
        )
    assert execution.process_one(store, runtime)
    with store.connect() as c:
        assert "pinned base" in bridge.prerequisite_reason(c, second["snapshot"]["workflow_task"])
    assert not execution.process_one(store, runtime)
    with store.connect() as c:
        assert not c.execute(
            select(execution.requests).where(execution.requests.c.grant_id == second["id"])
        ).all()


def test_temporary_readiness_waits_then_runs_same_unconsumed_request(client, store, linked):
    task, runtime, grant = linked
    runtime.preflight = lambda: [dict(name="controlled resource availability", status="blocked")]
    assert not execution.process_one(store, runtime)
    row = request(store, grant)
    assert row["status"] == "pending" and "Waiting for execution safety checks" in row["message"]
    with store.connect() as c:
        assert not c.execute(
            select(model_attempts).where(model_attempts.c.execution_id == row["id"])
        ).all()
    runtime.preflight = lambda: [dict(name="controlled resource availability", status="passed")]
    assert execution.process_one(store, runtime)
    assert request(store, grant)["id"] == row["id"] and request(store, grant)["status"] == "checked"
    assert runtime.calls == 1


def test_prepared_runtime_identity_mismatch_is_not_a_waiting_or_retarget_permission(store, linked):
    _, runtime, grant = linked
    assert bridge.dispatch_one(store)
    original = runtime.identity
    runtime.identity = lambda: {**original(), "model": "WRONG MODEL"}
    assert execution.process_one(store, runtime)
    assert request(store, grant)["status"] == "blocked" and runtime.calls == 0


def test_oversized_task_metadata_never_bypasses_snapshot_bound(store, linked):
    task, runtime, grant = linked
    job = grant["snapshot"]["workflow_task"]["decomposition"]
    payload = {**job["payload"], "oversized_untrusted": "x" * 65536}
    mutate(store, planning_jobs, job["id"], payload=payload)
    with pytest.raises(ValueError, match="bounded execution snapshot"):
        execution.issue(store, "NON-MODEL oversized metadata", "pytest", runtime, task=task)
    assert not execution.process_one(store, runtime) and runtime.calls == 0


def test_saved_request_explains_expiry_even_when_worker_is_disabled(client, store, linked):
    task, runtime, grant = linked
    assert bridge.dispatch_one(store)
    mutate(
        store,
        execution.grants,
        grant["id"],
        expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
    )
    data = context(client, task)
    assert data["stage"] == "pending" and "permission is no longer current" in data["message"]
    assert runtime.calls == 0


def test_delivery_progress_does_not_repeat_the_pre_publication_history_message():
    from workbench.task_execution import result_message

    original = dict(
        status="checked",
        message="Nothing accepted or published.",
        publication=dict(status="published", published=True),
    )
    assert "Draft PR delivered" in result_message(original)
    assert original["message"] == "Nothing accepted or published."
    original["publication"] = dict(status="reconciling", published=False)
    assert "in progress" in result_message(original)
    original["publication"] = dict(
        status="held", published=False, message="Read-only reconciliation needs attention."
    )
    assert "needs attention" in result_message(original)
    original["status"] = "failed"
    assert result_message(original) == original["message"]
