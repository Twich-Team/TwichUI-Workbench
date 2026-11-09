"""Verified local settlement preserves unknown completion and never starts work."""

from contextlib import nullcontext
import copy
import json
import sqlite3
from uuid import uuid4

import pytest
from sqlalchemy import select, update

from workbench import worker, planning_reconciliation as settle
from workbench.db import planning_jobs, model_attempts
from workbench.planning_provider import PlanningUnavailable
from test_everyday import installed  # noqa: F401
from test_bug_workflow import enable_bugs, report
from test_bug_source_planning import scripted, inspection
from acceptance_policy import campaign_admission, OUTCOME, CAMPAIGN


def interrupted(client, headers, store, monkeypatch, *, dead=True):
    enable_bugs(monkeypatch)
    scripted(
        monkeypatch,
        [inspection(), PlanningUnavailable("Controlled disconnected response.", "transport")],
    )
    identity = client.post("/api/intake", headers=headers, json=report()).json()["id"]
    assert worker.tick(store)
    with store.begin() as c:
        attempt = (
            c.execute(select(model_attempts).where(model_attempts.c.task_id == identity))
            .mappings()
            .one()
        )
        job = (
            c.execute(select(planning_jobs).where(planning_jobs.c.intake_id == identity))
            .mappings()
            .one()
        )
        assert attempt["outcome"] == "interrupted_unknown" and job["status"] == "failed"
        if dead:
            payload = copy.deepcopy(job["payload"])
            payload["scenario_generation_claim"]["pid"] = 99999999
            c.execute(
                update(planning_jobs).where(planning_jobs.c.id == job["id"]).values(payload=payload)
            )
    monkeypatch.setattr("workbench.planning_provider.local_slot", lambda **_: nullcontext())
    probes = []

    def probe(model, task):
        probes.append((model, task))
        return dict(task=task, idle=True, completion_limit=2600)

    monkeypatch.setattr(settle, "probe_local", probe)
    return identity, attempt["id"], job["id"], probes


def row(store, table, identity):
    with sqlite3.connect(store.url.database) as c:
        return settle.rows(c, "SELECT * FROM " + table + " WHERE id=?", (identity,))[0]


@pytest.mark.usefixtures("installed")
def test_local_settlement_is_idempotent_and_preserves_history_usage_and_approvals(
    client, headers, store, monkeypatch
):
    intake, attempt, job, probes = interrupted(client, headers, store, monkeypatch)
    original = row(store, "model_attempts", attempt)
    before = row(store, "planning_jobs", job)
    result = settle.reconcile(store, attempt, 40232)
    assert result["kind"] == "conservative-local-bound" and result["requests_started"] == 2
    assert result["known_completed_values"] == dict(
        prompt_tokens=40, completion_tokens=10, total_tokens=50
    )
    assert (
        result["completion_tokens_upper_bound"] == 2610
        and result["unavailable_usage_requests"] == 1
    )
    assert row(store, "model_attempts", attempt) == original
    after = row(store, "planning_jobs", job)
    data = json.loads(after["payload"])
    assert data["diagnostics"] == json.loads(before["payload"])["diagnostics"]
    assert data["local_interruption_settlement"]["original_message"] == before["message"]
    assert after["status"] == "failed" and after["message"] == settle.MESSAGE
    assert settle.reconcile(store, attempt, 40232) == result and len(probes) == 1
    assert row(store, "planning_jobs", job) == after
    item = client.get("/api/intake/" + intake).json()
    assert item["status"] == "failed" and not item["tasks"] and not item["decisions"]
    assert item["provider_activity"][0]["outcome"] == "interrupted_unknown"


@pytest.mark.parametrize(
    "fault", ["producer", "codex", "implementation", "completed", "prefix", "receipt", "busy"]
)
@pytest.mark.usefixtures("installed")
def test_unsafe_or_unaccounted_settlement_holds_without_writing(
    client, headers, store, monkeypatch, fault
):
    _, attempt, job, _ = interrupted(client, headers, store, monkeypatch, dead=fault != "producer")
    with store.begin() as c:
        if fault in ["codex", "implementation", "completed"]:
            field = {"codex": "backend", "implementation": "purpose", "completed": "outcome"}[fault]
            c.execute(
                update(model_attempts)
                .where(model_attempts.c.id == attempt)
                .values(**{field: fault})
            )
        if fault in ["prefix", "receipt"]:
            payload = c.execute(
                select(planning_jobs.c.payload).where(planning_jobs.c.id == job)
            ).scalar_one()
            payload = copy.deepcopy(payload)
            if fault == "prefix":
                payload["diagnostics"]["known_usage"] = "{}"
            else:
                payload["local_planning_receipt"]["investigation_version"] = 3
            c.execute(
                update(planning_jobs).where(planning_jobs.c.id == job).values(payload=payload)
            )
    if fault == "busy":
        monkeypatch.setattr(
            settle, "probe_local", lambda *args: dict(task=40232, idle=False, completion_limit=2600)
        )
    before = row(store, "planning_jobs", job)
    with pytest.raises(ValueError):
        settle.reconcile(store, attempt, 40232)
    assert row(store, "planning_jobs", job) == before


@pytest.mark.usefixtures("installed")
def test_outcome_admission_needs_verified_settlement_and_rejects_later_tampering(
    client, headers, store, monkeypatch
):
    _, attempt, job, _ = interrupted(client, headers, store, monkeypatch)
    opened = "2026-01-01T00:00:00+00:00"
    request = dict(
        action="submit",
        choice="bug-flight-paths",
        opened=opened,
        prior=[attempt],
        outcome=dict(attempt=2, revision=1, opened_at=opened, prior_attempts=[attempt]),
    )
    case = dict(choice="bug-flight-paths", provider="local")

    def check():
        with sqlite3.connect(store.url.database) as c:
            campaign_admission(c, request, case, dict(outcome=OUTCOME))

    with pytest.raises(ValueError, match="unsettled"):
        check()
    settle.reconcile(store, attempt, 40232)
    check()
    legacy = copy.deepcopy(request)
    legacy["campaign"] = legacy.pop("outcome")
    with (
        sqlite3.connect(store.url.database) as connection,
        pytest.raises(ValueError, match="unsettled"),
    ):
        campaign_admission(connection, legacy, case, dict(campaign=CAMPAIGN))
    same = copy.deepcopy(request)
    same["intake"] = row(store, "model_attempts", attempt)["task_id"]
    with sqlite3.connect(store.url.database) as connection, pytest.raises(ValueError):
        campaign_admission(connection, same, case, dict(outcome=OUTCOME))
    with store.begin() as c:
        payload = c.execute(
            select(planning_jobs.c.payload).where(planning_jobs.c.id == job)
        ).scalar_one()
        payload = copy.deepcopy(payload)
        payload["local_interruption_settlement"]["accounting"]["completion_tokens_upper_bound"] = 0
        c.execute(update(planning_jobs).where(planning_jobs.c.id == job).values(payload=payload))
    with pytest.raises(ValueError, match="unsettled"):
        check()


def test_invalid_attempt_identity_does_not_query_or_probe():
    with pytest.raises(ValueError):
        settle.state(None, str(uuid4()) + "../")


@pytest.mark.parametrize("fault", ["valid", "busy", "task", "limit", "debug", "model"])
def test_probe_only_reads_bounded_authenticated_metadata_and_never_generation(
    monkeypatch, tmp_path, fault
):
    from workbench import planning_transport

    key = tmp_path / "fixture-key"
    key.write_text("CONTROLLED-NOT-A-CREDENTIAL")
    key.chmod(0o600)
    monkeypatch.setenv("WORKBENCH_LOCAL_KEY", str(key))
    slot = dict(id_task=40232, is_processing=False, params=dict(n_predict=2600))
    if fault == "busy":
        slot["is_processing"] = True
    if fault == "task":
        slot["id_task"] = 40233
    if fault == "limit":
        slot["params"]["n_predict"] = -1
    if fault == "debug":
        slot["prompt"] = "PRIVATE_MODEL_INPUT"
    calls = []

    class Probe:
        def __enter__(self):
            assert self.seconds == 15
            return self

        def __exit__(self, *args):
            pass

        def models(self, secret):
            assert secret == "CONTROLLED-NOT-A-CREDENTIAL"
            calls.append("models")
            return "wrong-model" if fault == "model" else "local-model"

        def read(self, request, limit, timeout):
            assert (
                request.get_method() == "GET" and request.full_url == "http://127.0.0.1:8080/slots"
            )
            assert limit == 65536 and timeout == 3
            calls.append("slots")
            return json.dumps([slot]).encode()

    monkeypatch.setattr(planning_transport, "LocalHTTP", Probe)
    if fault == "valid":
        assert settle.probe_local("local-model", 40232) == dict(
            task=40232, idle=True, completion_limit=2600
        )
    else:
        with pytest.raises(ValueError):
            settle.probe_local("local-model", 40232)
    assert calls == (["models"] if fault == "model" else ["models", "slots"])


@pytest.mark.usefixtures("installed")
def test_changed_job_during_metadata_read_cannot_gain_settlement(
    client, headers, store, monkeypatch
):
    _, attempt, job, _ = interrupted(client, headers, store, monkeypatch)

    def changed(*args):
        with store.begin() as connection:
            connection.execute(
                update(planning_jobs)
                .where(planning_jobs.c.id == job)
                .values(message="Changed concurrently.")
            )
        return dict(task=40232, idle=True, completion_limit=2600)

    monkeypatch.setattr(settle, "probe_local", changed)
    with pytest.raises(ValueError, match="changed"):
        settle.reconcile(store, attempt, 40232)
    data = json.loads(row(store, "planning_jobs", job)["payload"])
    assert "local_interruption_settlement" not in data
