"""Pure policy fixtures and disposable transport stubs. Never invoke a provider."""

from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError
from sqlalchemy import select, func

from workbench import model_policy as policy, model_accounting, planning_worker
from workbench.api import create_app
from workbench.db import execution_policies, model_attempts, planning_jobs, write_transaction
from workbench.planning_provider import DemoProvider, LocalProvider
from test_intake import submit


@pytest.fixture
def report():
    return policy.CapabilityReport(
        executors=[
            policy.ExecutorCapability(
                backend=b,
                executor="Fixture executor",
                source="Deterministic test fixture",
                observed_at=policy.now(),
                default_model="test-" + b,
                models=[
                    policy.ModelCapability(
                        model="test-" + b, efforts=["low", "high"] if b == "codex" else []
                    )
                ],
                limitation="No real invocation",
            )
            for b in ("local", "codex", "claude")
        ]
    )


@pytest.fixture
def configured():
    return policy.ExecutionPolicy(
        substitution="configured_fallback_only",
        fallback=["codex", "claude"],
        providers={
            b: policy.BackendPreference(model="test-" + b, enabled=b != "claude")
            for b in ("local", "codex", "claude")
        },
    )


def inputs(**updates):
    values = dict(
        permission=False,
        observations=[
            policy.Observation(
                backend=b,
                availability="available",
                capability="supported",
                observed_at=policy.now(),
                capacity="available",
                capacity_at=policy.now(),
                source="Synthetic fixture",
            )
            for b in ("local", "codex", "claude")
        ],
    )
    values.update(updates)
    return policy.RoutingInput(**values)


def route(configured, report, value=None):
    return policy.evaluate(
        configured, report, value or inputs(), frozenset({"local", "codex", "claude"})
    )


def test_preference_order_disabled_and_permission_hold(configured, report):
    value = inputs()
    assert route(configured, report, value).backend == "local"
    value.observations[0].availability = "unavailable"
    result = route(configured, report, value)
    assert result.backend == "codex" and not result.execution_allowed
    assert "Permission" in " ".join(result.constraints)
    configured.preferred, configured.fallback = "codex", ["local", "claude"]
    assert route(configured, report).backend == "codex"
    configured.preferred, configured.fallback = "claude", ["local", "codex"]
    result = route(configured, report)
    assert result.backend == "local" and "disabled" in result.evidence[0]


def test_saved_configuration_never_activates(configured, report):
    result = policy.evaluate(configured, report, inputs())
    assert result.state == "selected" and result.backend == "local" and not result.execution_allowed
    assert "activation is off" in " ".join(result.constraints)
    with pytest.raises(ValidationError):
        policy.BackendPreference(activated=True)


@pytest.mark.parametrize(
    "field,value,condition",
    [
        ("availability", "unavailable", "unavailable"),
        ("availability", "unknown", "unknown"),
        ("capability", "limited", "capability"),
        ("capability", "unknown", "capability"),
        ("observed_at", "2000-01-01T00:00:00Z", "stale"),
        ("observed_at", None, "unknown"),
        ("observed_at", "2999-01-01T00:00:00Z", "unknown"),
        ("observed_at", "2026-01-01", "unknown"),
    ],
)
def test_failure_classes_do_not_collapse(configured, report, field, value, condition):
    data = inputs()
    setattr(data.observations[0], field, value)
    configured.fallback_on = [condition]
    assert route(configured, report, data).backend == "codex"
    configured.fallback_on = []
    assert route(configured, report, data).backend is None


@pytest.mark.parametrize(
    "capacity,stamp,condition",
    [
        ("exhausted", "fresh", "capacity"),
        ("available", "stale", "stale"),
        ("unknown", "fresh", "unknown"),
        ("available", "unknown", "unknown"),
    ],
)
def test_paid_capacity_is_not_allowance_guess(configured, report, capacity, stamp, condition):
    configured.preferred, configured.fallback = "codex", []
    data = inputs()
    data.observations[1].capacity = capacity
    data.observations[1].capacity_at = {
        "fresh": policy.now(),
        "stale": "2000-01-01T00:00:00Z",
        "unknown": None,
    }[stamp]
    result = route(configured, report, data)
    assert result.state == "waiting" and f"Condition: {condition}" in result.evidence[0]


def test_no_silent_model_substitution_or_retry(configured, report):
    configured.providers["local"].model = "model-not-in-catalog"
    configured.substitution = "deny"
    result = route(configured, report)
    assert result.backend is None and result.model is None
    configured.substitution = "configured_fallback_only"
    assert route(configured, report).backend == "codex"
    assert route(configured, report, inputs(attempts=3)).state == "blocked"
    assert route(configured, report, inputs(verification_failed=True)).state == "blocked"


def test_grounding_and_purpose_are_explicit(configured, report):
    data = inputs()
    data.observations[0].capability = "limited"
    assert route(configured, report, data).backend == "codex"
    data.purpose = "summary"
    data.requires_grounded_planning = False
    assert route(configured, report, data).backend == "local"
    configured.when_unavailable = "block"
    configured.fallback = []
    configured.providers["local"].enabled = False
    assert route(configured, report, data).state == "blocked"


def test_unsupported_and_unknown_effort(configured, report):
    configured.providers["local"].effort = "high"
    assert route(configured, report).backend == "codex"
    report.executors[1].observed_at = "2000-01-01T00:00:00Z"
    assert route(configured, report).backend is None
    assert policy.support(report, "codex", "test-codex", "high") == "unknown"


def test_revision_conflict_retry_restart_and_no_false_ack(store, report, tmp_path):
    path = tmp_path / "capabilities.json"
    path.write_text(report.model_dump_json())
    with TestClient(create_app(store, capabilities_path=str(path))) as client:
        headers = {
            "origin": "http://testserver",
            "x-csrf-token": client.get("/api/session").json()["csrf_token"],
        }
        body = client.get("/api/models").json()["policy"]
        body["providers"]["codex"].update(model="test-codex", effort="high")
        saved = client.put("/api/models/policy", headers=headers, json=body)
        assert saved.status_code == 200
        assert saved.json()["revision"] == 1
        assert client.put("/api/models/policy", headers=headers, json=body).json() == saved.json()
        body["preferred"] = "claude"
        assert client.put("/api/models/policy", headers=headers, json=body).status_code == 409
        body["revision"] = 1
        body["providers"]["claude"]["activated"] = True
        assert client.put("/api/models/policy", headers=headers, json=body).status_code == 422
        body["providers"]["claude"]["activated"] = False
        body["providers"]["codex"]["effort"] = "unsupported"
        assert client.put("/api/models/policy", headers=headers, json=body).status_code == 422
        body["providers"]["codex"].update(model="unverified-identifier", effort=None)
        assert client.put("/api/models/policy", headers=headers, json=body).status_code == 200
    with TestClient(create_app(store, capabilities_path=str(path))) as restarted:
        result = restarted.get("/api/models").json()
        assert result["policy"]["revision"] == 2
        assert (
            result["acknowledged_revision"] is None and "not activated" in result["acknowledgment"]
        )


@pytest.mark.parametrize("case", list(policy.CASES))
def test_previews_cannot_execute_or_mutate(client, store, case, monkeypatch):
    monkeypatch.setattr(
        LocalProvider, "generate", lambda *_: pytest.fail("Preview invoked transport")
    )
    with store.connect() as c:
        before = [
            c.scalar(select(func.count()).select_from(t))
            for t in (execution_policies, model_attempts, planning_jobs)
        ]
    result = client.get("/api/models/preview", params={"case": case}).json()
    assert result["preview"] and not result["execution_allowed"]
    if case == "fallback":
        assert result["backend"] == "codex" and "Synthetic" in result["basis"]
    with store.connect() as c:
        assert before == [
            c.scalar(select(func.count()).select_from(t))
            for t in (execution_policies, model_attempts, planning_jobs)
        ]


def test_policy_security_unknown_quota_and_no_raw_tokens(client, headers):
    body = client.get("/api/models").json()
    assert "unavailable" in body["usage_message"]
    assert client.put("/api/models/policy", json=body["policy"]).status_code == 403
    value = {**body["policy"], "subscription_token": "secret-fixture"}
    assert client.put("/api/models/policy", json=value, headers=headers).status_code == 422
    value = {**body["policy"], "fallback": ["codex", "codex"]}
    assert client.put("/api/models/policy", json=value, headers=headers).status_code == 422
    assert client.get("/api/models/preview?case=execute").status_code == 422
    assert client.get("/api/models", headers={"host": "evil.test"}).status_code == 400


def test_record_new_demo_attempts_without_backfilling(client, headers, store):
    data, _ = submit(client, headers)
    planning_worker.process_one(store, DemoProvider())
    read = model_accounting.read(store)
    assert read.demo_attempts == 1 and read.attempts == 0 and read.tasks == 0
    assert read.rows[0].policy_revision is None
    assert read.rows[0].actual_effort is None and read.rows[0].verification is None
    assert read.rows[0].finished_at and read.rows[0].actual_model == "deterministic-demo-v1"
    with store.connect() as connection:
        job = dict(connection.execute(select(planning_jobs)).mappings().one())
    with write_transaction(store) as connection:
        for n, source, backend, model in [
            (1, "workbench", "local", "model-a"),
            (2, "workbench", "codex", "model-b"),
            (3, "pilot", "codex", None),
            (4, "unknown", None, None),
        ]:
            connection.execute(
                model_attempts.insert().values(
                    id=str(uuid4()),
                    job_id=job["id"],
                    task_id=data["id"],
                    source=source,
                    backend=backend,
                    actual_model=model,
                    purpose="planning",
                    routing_reason="Synthetic acceptance fixture",
                    started_at=policy.now(),
                    outcome="failed",
                )
            )
    read = model_accounting.read(store)
    assert (
        read.tasks,
        read.attempts,
        read.demo_attempts,
        read.imported_attempts,
        read.unattributed_attempts,
    ) == (1, 2, 1, 1, 1)
    assert read.verified == 0
    assert (
        sum(row.tasks for row in read.distribution) == 2
    )  # Do not sum provider subtotals as distinct tasks.
    assert any(row.actual_model is None and row.finished_at is None for row in read.rows)


def test_failed_transport_stub_is_attributed_but_never_verified(client, headers, store):
    submit(client, headers)

    class Stub(LocalProvider):
        def generate(self, *_):
            from workbench.planning_provider import PlanningUnavailable

            self.diagnostics = {"configured_model": "fixture-model"}
            raise PlanningUnavailable("Synthetic timeout", "timeout")

    planning_worker.process_one(store, Stub())
    row = model_accounting.read(store).rows[0]
    assert row.source == "workbench" and row.backend == "local" and row.outcome == "failed"
    assert row.requested_model == "fixture-model" and row.actual_model is None
    assert row.policy_revision is None and row.usage is None


def test_capability_metadata_missing_malformed_private_fields_and_no_inference(
    tmp_path, monkeypatch
):
    path = tmp_path / "metadata.json"
    assert not policy.capabilities(str(path)).executors[0].models
    path.write_text('{"secret":"fixture-secret"}')
    assert "fixture-secret" not in policy.capabilities(str(path)).model_dump_json()
    from discover_models import discover

    monkeypatch.setattr("discover_models.version", lambda _: "fixture-version")
    cache = tmp_path / "cache.json"
    config = tmp_path / "config.toml"
    cache.write_text(
        json.dumps(
            {
                "fetched_at": policy.now(),
                "identity": "private-fixture",
                "models": [
                    {
                        "slug": "test-codex",
                        "visibility": "list",
                        "supported_reasoning_levels": [{"effort": "high"}],
                    }
                ],
            }
        )
    )
    config.write_text(
        'model="test-codex"\nmodel_reasoning_effort="high"\nsecret="private-fixture"\n'
    )
    result = discover(cache, config).model_dump_json()
    assert "private-fixture" not in result and "test-codex" in result
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    assert policy.freshness(future) == "unknown"


def test_legacy_counters_remain_unknown_after_new_recording(client, headers, store):
    submit(client, headers)
    with write_transaction(store) as c:
        c.execute(planning_jobs.update().values(attempts=2))
    planning_worker.process_one(store, DemoProvider())
    counts = model_accounting.read(store)
    assert counts.legacy_job_attempts == 2 and counts.demo_attempts == 1
    assert len(counts.rows) == 1  # Never manufacture missing historical event rows.


def test_expired_lease_retains_uncertain_attempt_and_no_invented_end(client, headers, store):
    submit(client, headers)
    old = planning_worker.claim(store)
    with write_transaction(store) as c:
        c.execute(planning_jobs.update().values(lease_until="2000-01-01T00:00:00Z"))
    planning_worker.process_one(store, DemoProvider())
    with store.connect() as c:
        row = (
            c.execute(select(model_attempts).where(model_attempts.c.id == old["lease"]))
            .mappings()
            .one()
        )
    assert row["outcome"] == "interrupted_unknown" and row["finished_at"] is None
    assert row["actual_model"] is None


def test_response_usage_and_reported_substitution_are_retained_without_acceptance():
    from workbench.planning_provider import PlanningUnavailable
    from workbench.schemas import PlanningOutput

    class Transport:
        seconds = 120

        def models(self, _):
            return "requested-model"

        def read(self, *_):
            return json.dumps(
                {
                    "model": "other-model",
                    "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
                    "usage": {
                        "prompt_tokens": 9,
                        "completion_tokens": 4,
                        "total_tokens": 13,
                        "private": "never retain",
                    },
                }
            ).encode()

    adapter = LocalProvider()
    with pytest.raises(PlanningUnavailable):
        adapter._infer(Transport(), "dummy-test-key", PlanningOutput, "{}")
    assert adapter.diagnostics["configured_model"] == "requested-model"
    assert adapter.diagnostics["actual_model"] == "other-model"
    assert adapter.diagnostics["usage"]["values"] == {
        "prompt_tokens": 9,
        "completion_tokens": 4,
        "total_tokens": 13,
    }
    assert "private" not in adapter.diagnostics["usage"]["values"]


def test_explicit_clock_controls_catalog_freshness(configured, report):
    clock = datetime(2024, 1, 1, tzinfo=timezone.utc)
    for entry in report.executors:
        entry.observed_at = clock.isoformat()
    value = inputs()
    for o in value.observations:
        o.observed_at = o.capacity_at = clock.isoformat()
    result = policy.evaluate(configured, report, value, frozenset({"local", "codex"}), clock)
    assert result.backend == "local"
    assert not result.execution_allowed


def test_review_shutdown_refuses_changed_process_identity(tmp_path, monkeypatch):
    import os
    import model_review
    path = tmp_path / 'identity.json'
    path.write_text(json.dumps({'pid': os.getpid(), 'boot_id': 'different-boot', 'start': 'wrong', 'argv': 'wrong'}))
    monkeypatch.setattr(model_review, 'IDENTITY', path)
    monkeypatch.setattr(model_review.signal, 'pidfd_send_signal', lambda *_: pytest.fail('Signaled a changed identity'))
    with pytest.raises(SystemExit, match='Identity changed'):
        model_review.stop()


def test_stale_demo_exposes_availability_and_paid_capacity_separately(configured, report):
    result = policy.preview(configured, report, 'stale')
    assert result.state == 'waiting' and result.backend is None
    assert 'Paid capacity is unknown or stale' in result.reason
    assert 'Availability observation is stale' in result.evidence[0]
    assert 'capacity stale' in result.evidence[1]
    assert not result.execution_allowed and 'Synthetic' in result.basis


def test_demo_activity_uses_real_accounting_and_isolates_observed_totals(client, store):
    before = client.get('/api/models/activity').json()
    demo = client.get('/api/models/activity?source=demo')
    assert demo.status_code == 200
    data = demo.json()
    assert data['source'] == 'demo' and (data['tasks'], data['attempts']) == (3, 6)
    assert {r['backend'] for r in data['providers']} == {'local', 'codex', 'claude', 'unknown'}
    assert len({r['task_id'] for r in data['rows'] if r['purpose'] == 'planning'}) == 1
    assert sum(r['attempts'] for r in data['distribution']) == 6
    assert data['verified'] == 0 and data['legacy_job_attempts'] == 1
    assert any(r['actual_model'] is None and r['finished_at'] is None for r in data['rows'])
    assert data['outcomes'] and data['usage_points'][0]['source'].startswith('Synthetic')
    assert client.get('/api/models/activity').json() == before
    with store.connect() as c:
        assert c.scalar(select(func.count()).select_from(model_attempts)) == 0
    assert client.get('/api/models/activity?source=invalid').status_code == 422


def test_demo_dated_coverage_does_not_invent_interrupted_end_time(client):
    data = client.get('/api/models/activity?source=demo').json()
    assert data['attempts'] == 6
    assert sum(row['attempts'] for row in data['outcomes']) == 5
    missing = [row for row in data['rows'] if row['finished_at'] is None]
    assert len(missing) == 1 and missing[0]['outcome'] == 'interrupted_unknown'


def test_installed_model_overview_does_not_poll_providers_or_grant_execution(client, store, monkeypatch):
    from workbench import codex_provider

    monkeypatch.setenv('WORKBENCH_PLANNER', 'everyday')
    monkeypatch.setattr(LocalProvider, 'generate', lambda *_: pytest.fail('Overview invoked local generation'))
    monkeypatch.setattr(codex_provider, 'generate', lambda *_: pytest.fail('Overview invoked Codex generation'))
    with store.connect() as c:
        before = [c.scalar(select(func.count()).select_from(t))
                  for t in (execution_policies, model_attempts, planning_jobs)]
    response = client.get('/api/models')
    assert response.status_code == 200
    result = response.json()
    assert result['acknowledged_revision'] is None
    assert not result['routing']['execution_allowed']
    assert all(o['availability'] == 'unknown' and o['capacity'] == 'unknown' for o in result['observations'])
    with store.connect() as c:
        assert before == [c.scalar(select(func.count()).select_from(t))
                          for t in (execution_policies, model_attempts, planning_jobs)]
