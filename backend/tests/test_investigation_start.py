"""Actual ordinary-input regression: no permission must never invoke a dead adapter."""

from uuid import uuid4
import pytest
from sqlalchemy import select
from workbench import intake, worker, everyday
from workbench.db import planning_jobs, model_attempts, intakes, write_transaction
from workbench.investigation_start import first, validate_prefix
from test_everyday import installed  # noqa: F401
from test_bug_workflow import enable_bugs, report, response
from test_bug_source_planning import scripted

VENDOR = dict(
    kind="issue",
    visual_requirement="unclassified",
    title="Vendor items showing item number instead of name for some items",
    description="When viewing items a vendor sells using Vendors and Trainers, some entries show Silken Thread and Yellow Dye while others show Item 4289. The icon and hover tooltip are correct; the displayed item name or link is wrong.",
)


def saved(client, headers):
    body = dict(VENDOR, request_id=str(uuid4()))
    result = client.post("/api/intake", headers=headers, json=body)
    assert result.status_code == 200, result.text
    return body, result.json()


def legacy(store, identity):
    """Exact old non-dispatch shape, in a disposable store only."""
    with write_transaction(store) as c:
        job_id = intake.enqueue(c, identity, "planning", 1)
        c.execute(
            planning_jobs.update()
            .where(planning_jobs.c.id == job_id)
            .values(
                status="failed",
                attempts=2,
                payload=dict(scope_revision=1, diagnostics=dict(category="unavailable")),
                finished_at=intake.now(),
                message="Planning unavailable or invalid output.",
            )
        )
        for _ in range(2):
            c.execute(
                model_attempts.insert().values(
                    id=str(uuid4()),
                    job_id=job_id,
                    task_id=identity,
                    source="unknown",
                    purpose="planning",
                    routing_reason="Existing planning adapter configuration; new Workbench policy is not consumed or acknowledged.",
                    started_at=intake.now(),
                    finished_at=intake.now(),
                    outcome="failed",
                )
            )
        c.execute(intakes.update().where(intakes.c.id == identity).values(status="failed"))
    return job_id


def start(client, headers, item, **changes):
    command = dict(
        command_id=str(uuid4()),
        binding=item["planning_start_binding"],
        provider="qwen-once",
        **changes,
    )
    return command, client.post(
        "/api/intake/" + item["id"] + "/investigation", headers=headers, json=command
    )


@pytest.mark.usefixtures("installed")
def test_default_vendor_description_is_saved_without_a_fake_planning_attempt(
    client, headers, store, monkeypatch
):
    enable_bugs(monkeypatch)
    body, item = saved(client, headers)
    assert item["status"] == "waiting_for_information" and item["planning_start_binding"]
    assert item["jobs"] == [] and not worker.tick(store)
    assert client.post("/api/intake", headers=headers, json=body).json()["id"] == item["id"]
    with store.connect() as c:
        assert not c.execute(select(model_attempts)).first()
    assert (
        client.post(
            "/api/intake/" + item["id"] + "/clarification",
            headers=headers,
            json=dict(command_id=str(uuid4()), revision=0, text="Continue."),
        ).status_code
        == 409
    )


@pytest.mark.usefixtures("installed")
def test_existing_two_failures_recover_same_report_with_fresh_binding_and_no_replay(
    client, headers, store, monkeypatch
):
    enable_bugs(monkeypatch)
    body, item = saved(client, headers)
    job_id = legacy(store, item["id"])
    item = client.get("/api/intake/" + item["id"]).json()
    assert "No model was contacted" in item["jobs"][0]["message"]
    with store.connect() as c:
        before = intake.require(c, planning_jobs, job_id)
        attempts = [dict(a) for a in c.execute(select(model_attempts)).mappings()]
    cmd, result = start(client, headers, item)
    assert result.status_code == 200, result.text
    current = result.json()
    assert len(current["jobs"]) == 2 and current["jobs"][-1]["status"] == "queued"
    assert (
        not current.get("planning_start_binding")
        and current["original"]["description"] == body["description"]
    )
    assert (
        current["human_scope"]["visual_requirement"] == "unclassified"
        and not current["decisions"]
        and not current["tasks"]
    )
    assert (
        client.post(
            "/api/intake/" + item["id"] + "/investigation", headers=headers, json=cmd
        ).json()["id"]
        == item["id"]
    )
    assert start(client, headers, item)[1].status_code == 409
    assert (
        client.post(
            "/api/intake/" + item["id"] + "/investigation",
            headers=headers,
            json={**cmd, "provider": "codex-once"},
        ).status_code
        == 409
    )
    assert client.post("/api/intake", headers=headers, json=body).status_code == 200
    with store.connect() as c:
        assert intake.require(c, planning_jobs, job_id) == before
        assert [dict(a) for a in c.execute(select(model_attempts)).mappings()] == attempts
        initial = first(c, item["id"])
        assert initial["revision"] == 2 and validate_prefix(c, initial) == {job_id}
        row = intake.require(c, intakes, item["id"])
        everyday.check_planning(c, row, initial)
        assert (
            initial["payload"]["planning_start"]["original_input"]["description"]
            == body["description"]
        )
    # Commit-time fencing rejects changes to retained old accounting instead of ignoring it.
    with store.begin() as c:
        c.execute(
            model_attempts.update()
            .where(model_attempts.c.id == attempts[0]["id"])
            .values(outcome="interrupted_unknown")
        )
    with store.connect() as c, pytest.raises(ValueError, match="evidence changed"):
        validate_prefix(c, initial)


@pytest.mark.usefixtures("installed")
@pytest.mark.parametrize(
    "change",
    [
        {"outcome": "running", "finished_at": None},
        {"backend": "local"},
        {"usage": {"status": "unknown"}},
        {"actual_model": "some-model"},
    ],
)
def test_missing_or_uncertain_old_state_is_never_exempted(
    client, headers, store, monkeypatch, change
):
    enable_bugs(monkeypatch)
    _, item = saved(client, headers)
    legacy(store, item["id"])
    with store.begin() as c:
        c.execute(model_attempts.update().values(**change))
    item = client.get("/api/intake/" + item["id"]).json()
    assert start(client, headers, item)[1].status_code == 409
    with store.connect() as c:
        assert len(c.execute(select(planning_jobs)).all()) == 1


@pytest.mark.usefixtures("installed")
def test_new_permission_reaches_real_decoder_and_typed_plan_with_unknown_visual_scope(
    client, headers, store, monkeypatch
):
    enable_bugs(monkeypatch)
    # The executable protocol fixture remains a controlled name-normalization module;
    # the separate vendor-shape tests above bind the reported real-use intake failure.
    body = report()
    body.pop("local_planning")
    body["visual_requirement"] = "unclassified"
    item = client.post("/api/intake", headers=headers, json=body).json()
    legacy(store, item["id"])
    item = client.get("/api/intake/" + item["id"]).json()
    assert start(client, headers, item)[1].status_code == 200
    calls = scripted(monkeypatch, [response()])
    assert worker.tick(store)
    current = client.get("/api/intake/" + item["id"]).json()
    assert current["status"] == "completed", current
    assert calls and current["proposals"]
    assert current["human_scope"]["visual_requirement"] == "unclassified"
    assert current["provider_activity"][0]["backend"] == "local"
    assert current["jobs"][0]["status"] == "failed" and current["jobs"][1]["status"] == "completed"
    assert current["decisions"] == [] and current["tasks"] == []
    assert current["proposals"][0]["approval_blockers"]  # Unknown visual scope still gates repair.
    from workbench.delivery_phase import observe, renewable
    from pathlib import Path
    import os

    with store.connect() as c:
        phases = observe(c, item["id"], [Path(os.environ["WORKBENCH_RESEARCH_ROOT"]).parent])
    assert phases["next_sequence"] == 3 and renewable(phases)
    assert len(phases["phases"]) == 1 and phases["phases"][0]["sequence"] == 2


@pytest.mark.usefixtures("installed")
@pytest.mark.parametrize("permission_at_intake", [False, True])
def test_model_lock_hold_keeps_unused_consent_and_prior_attempt_without_replay(
    client, headers, store, monkeypatch, permission_at_intake
):
    enable_bugs(monkeypatch)
    body = report()
    if not permission_at_intake:
        body.pop("local_planning")
    body["visual_requirement"] = "unclassified"
    item = client.post("/api/intake", headers=headers, json=body).json()
    if not permission_at_intake:
        assert start(client, headers, item)[1].status_code == 200
    from workbench.planning_provider import LocalProvider, PlanningUnavailable

    def busy(self, *args, **kwargs):
        self.diagnostics = dict(generation_state="not_started", category="contention")
        raise PlanningUnavailable("Model is locked.", "contention")

    monkeypatch.setattr(LocalProvider, "generate", busy)
    assert worker.tick(store)
    stopped = client.get("/api/intake/" + item["id"]).json()
    assert stopped["planning_resume_binding"]
    parent = stopped["jobs"][-1]
    with store.connect() as c:
        before = intake.require(c, planning_jobs, parent["id"])
        original_attempt = dict(c.execute(select(model_attempts)).mappings().one())
    command = dict(
        job_id=parent["id"],
        command_id=str(uuid4()),
        review_binding=stopped["planning_resume_binding"],
        resume_before_generation=True,
    )
    endpoint = "/api/intake/" + item["id"] + "/retry"
    result = client.post(endpoint, headers=headers, json=command)
    assert result.status_code == 200, result.text
    assert client.post(endpoint, headers=headers, json=command).status_code == 200
    with store.connect() as c:
        assert intake.require(c, planning_jobs, parent["id"]) == before
        assert dict(c.execute(select(model_attempts)).mappings().one()) == original_attempt
        initial = first(c, item["id"])
        assert (
            initial["payload"]["local_planning_receipt"]
            == before["payload"]["local_planning_receipt"]
        )
        assert validate_prefix(c, initial) == {parent["id"]}
        everyday.check_planning(c, intake.require(c, intakes, item["id"]), initial)
    calls = scripted(monkeypatch, [response()])
    assert worker.tick(store)
    current = client.get("/api/intake/" + item["id"]).json()
    assert current["status"] == "completed" and calls and len(current["jobs"]) == 2
    assert current["decisions"] == [] and current["tasks"] == []


@pytest.mark.usefixtures("installed")
def test_busy_local_admission_waits_without_claim_or_provider_call(
    client, headers, store, monkeypatch
):
    from contextlib import contextmanager

    enable_bugs(monkeypatch)
    body = report()
    item = client.post("/api/intake", headers=headers, json=body).json()
    monkeypatch.setenv("WORKBENCH_LOCAL_KEY", "unused-probe-key")

    @contextmanager
    def busy():
        raise BlockingIOError("Controlled existing maintenance owner")
        yield

    monkeypatch.setattr("workbench.planning_provider.local_slot", busy)
    assert not worker.tick(store)
    current = client.get("/api/intake/" + item["id"]).json()
    assert current["status"] == "queued" and current["jobs"][0]["status"] == "queued"
    assert current["jobs"][0]["message"].startswith("Waiting for local Qwen")
    assert current["jobs"][0]["attempts"] == 0
    with store.connect() as c:
        assert not c.execute(select(model_attempts)).first()


def test_local_lock_race_waits_before_transport_and_honors_cancellation(tmp_path, monkeypatch):
    import fcntl
    import time
    import threading
    from workbench import planning_provider

    lock = tmp_path / "owned-test.lock"
    lock.touch()
    monkeypatch.setattr(planning_provider, "LOCAL_LOCKS", (str(lock),))
    monkeypatch.setattr("workbench.real_primary.quarantined", lambda p: False)
    with lock.open("rb") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        timer = threading.Timer(0.05, lambda: fcntl.flock(owner, fcntl.LOCK_UN))
        timer.start()
        with planning_provider.local_slot(ends_at=time.monotonic() + 2):
            pass
        timer.join()
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stopping = threading.Event()
        stopping.set()
        with pytest.raises(planning_provider.PlanningUnavailable, match="No generation started"):
            with planning_provider.local_slot(ends_at=time.monotonic() + 2, stop_event=stopping):
                pass


@pytest.mark.parametrize("packing_failure", ["input_budget", "context_budget", "cancelled"])
@pytest.mark.usefixtures("installed")
def test_completed_reads_survive_later_preflight_failure_and_normal_renewal(
    client, headers, store, monkeypatch, packing_failure
):
    from workbench.planning_provider import LocalProvider, PlanningUnavailable
    from test_bug_source_planning import inspection
    from test_bug_workflow import CLIENT_SOURCE

    enable_bugs(monkeypatch)
    calls = scripted(monkeypatch, [inspection(), inspection(CLIENT_SOURCE)])
    generated = LocalProvider.generate

    def stop_third(self, *args, **kwargs):
        if len(calls) == 2:
            self.diagnostics = dict(generation_state="not_started", category=packing_failure)
            raise PlanningUnavailable("Controlled working packet overflow.", packing_failure)
        return generated(self, *args, **kwargs)

    monkeypatch.setattr(LocalProvider, "generate", stop_third)
    item = client.post("/api/intake", headers=headers, json=report()).json()
    assert worker.tick(store)
    failed = client.get("/api/intake/" + item["id"]).json()
    assert failed["status"] == "failed" and not failed["proposals"]
    assert ("completed responses and usage are saved" if packing_failure=="cancelled" else "before sending") in failed["jobs"][-1]["message"]
    with store.connect() as c:
        initial = first(c, item["id"])
        old = dict(c.execute(select(model_attempts)).mappings().one())
        assert old["usage"]["status"] == "complete" and old["usage"]["requests_observed"] == 2
        assert old["actual_model"] == everyday.MODEL
        assert initial["payload"]["bug_inspection_packet"]["inspection_windows"]
    endpoint = "/api/intake/" + item["id"] + "/clarification"
    reply = dict(
        command_id=str(uuid4()),
        revision=failed["revision"],
        text="Continue the original report using the retained source.",
        continue_investigation=True,
        renew_operation=True,
    )
    renewed = client.post(endpoint, headers=headers, json=reply)
    assert renewed.status_code == 200, renewed.text
    assert client.post(endpoint, headers=headers, json=reply).status_code == 200
    renewed_calls=scripted(monkeypatch, [response()])
    assert worker.tick(store)
    current = client.get("/api/intake/" + item["id"]).json()
    assert current["status"] == "completed" and current["proposals"]
    windows=renewed_calls[0].get('source_memory',{}).get('read_windows',[])
    keys=[(w['path'],w['line'],w['through_line']) for w in windows]
    assert len(keys)==len(set(keys))  # A cached continuation must not append the same windows again.
    with store.connect() as c:
        assert (
            dict(
                c.execute(select(model_attempts).where(model_attempts.c.id == old["id"]))
                .mappings()
                .one()
            )
            == old
        )
    assert not current["tasks"] and not current["decisions"]
