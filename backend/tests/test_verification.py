from uuid import uuid4
import pytest
from sqlalchemy import select, func
from workbench import verification, worker
from workbench.db import tasks, applications, write_transaction

URL = "/api/tasks/cozy-step-review/verification"


def approve(client, headers, store):
    task = client.get("/api/tasks/cozy-step-review").json()
    assert (
        client.post(
            "/api/tasks/cozy-step-review/decisions",
            headers=headers,
            json={
                "command_id": str(uuid4()),
                "version": task["version"],
                "choices": {c["id"]: "confirm" for c in task["evidence"]["claims"]},
            },
        ).status_code
        == 200
    )
    worker.process_one(store)
    return client.get("/api/tasks/cozy-step-review").json()


def body(task):
    return dict(command_id=str(uuid4()), version=task["version"], mode="fixture")


def test_gate_and_security(client, headers, store):
    task = client.get("/api/tasks/cozy-step-review").json()
    assert client.post(URL, headers=headers, json=body(task)).status_code == 403
    task = approve(client, headers, store)
    assert client.post(URL, json=body(task)).status_code == 403
    assert client.post(URL, headers=headers, json={**body(task), "mode": "live"}).status_code == 422
    with write_transaction(store) as c:
        c.execute(applications.delete())
    assert client.post(URL, headers=headers, json=body(task)).status_code == 403


def test_pending_atomic_recovery_and_duplicate(client, headers, store):
    task = approve(client, headers, store)
    command = body(task)
    saved = client.post(URL, headers=headers, json=command).json()
    assert (
        saved["status"] == "pending"
        and saved["verification"] == "unknown"
        and saved["started_at"] is None
    )
    assert client.post(URL, headers=headers, json=command).json() == saved
    assert client.post(URL, headers=headers, json=body(task)).status_code == 409
    assert (
        client.post(URL, headers=headers, json={**command, "version": "0" * 64}).status_code == 409
    )
    with pytest.raises(RuntimeError):
        verification.process_one(store, crash=True)
    assert client.get(URL).json()[0]["status"] == "pending"
    verification.process_one(store)
    result = client.get(URL).json()[0]
    assert result["status"] == "completed" and result["verification"] == "passed"
    assert (
        result["attempt_outcome"] == "completed" and result["started_at"] and result["finished_at"]
    )
    assert not result["execution_activated"] and not result["publication_allowed"]
    assert client.post(URL, headers=headers, json=command).json() == result
    assert not verification.process_one(store)
    assert client.get("/api/tasks/cozy-step-review").json() == task
    with store.connect() as c:
        assert c.scalar(select(func.count()).select_from(verification.records)) == 1


@pytest.mark.parametrize("outcome", ["passed", "failed_check", "adapter_error"])
def test_lifecycle_times_surround_verifier(client, headers, store, monkeypatch, outcome):
    from datetime import datetime, timedelta, timezone

    task = approve(client, headers, store)
    saved_at = datetime(2026, 11, 4, tzinfo=timezone.utc)
    clock = {"time": saved_at}
    observations = []

    def event_time():
        observations.append(clock["time"])
        return clock["time"].isoformat()

    monkeypatch.setattr(verification, "now", event_time)
    client.post(URL, headers=headers, json=body(task))
    started_at = saved_at + timedelta(seconds=1)
    finished_at = started_at + timedelta(seconds=2)
    clock["time"] = started_at

    class Adapter:
        def verify(self, _):
            # Start must be captured before entry; finish must be captured after exit.
            assert observations == [saved_at, started_at]
            clock["time"] = finished_at
            if outcome == "adapter_error":
                raise OSError("Synthetic verifier error")
            return [{"label": "Synthetic check", "passed": outcome == "passed"}]

    verification.process_one(store, Adapter())
    assert observations == [saved_at, started_at, finished_at]
    result = client.get(URL).json()[0]
    expected = dict(created_at=saved_at.isoformat(), started_at=started_at.isoformat(),
                    finished_at=finished_at.isoformat())
    assert {key: result[key] for key in expected} == expected
    with store.connect() as c:
        persisted = c.execute(select(verification.records)).mappings().one()
        assert {key: persisted[key] for key in expected} == expected
    assert result["status"] == ("failed" if outcome == "adapter_error" else "completed")


def test_stale_at_submit_and_apply(client, headers, store):
    task = approve(client, headers, store)
    command = body(task)
    assert client.post(URL, headers=headers, json=command).status_code == 200
    with write_transaction(store) as c:
        c.execute(tasks.update().values(revision=3))
    assert client.post(URL, headers=headers, json=body(task)).status_code == 409

    class Never:
        def verify(self, _):
            pytest.fail("Stale request invoked adapter")

    verification.process_one(store, Never())
    result = client.get(URL).json()[0]
    assert (
        result["status"] == "stale"
        and result["attempt_outcome"] == "not_run"
        and result["verification"] == "unknown"
        and result["started_at"] is None
        and result["finished_at"] is not None
    )


def test_permission_revoked_before_worker(client, headers, store):
    task = approve(client, headers, store)
    client.post(URL, headers=headers, json=body(task))
    with write_transaction(store) as c:
        c.execute(applications.delete())
    verification.process_one(store)
    result = client.get(URL).json()[0]
    assert result["status"] == "blocked"
    assert result["started_at"] is None and result["finished_at"] is not None


@pytest.mark.parametrize("failure", [False, True])
def test_failed_check_distinct_from_adapter_failure(client, headers, store, failure):
    task = approve(client, headers, store)
    client.post(URL, headers=headers, json=body(task))

    class Adapter:
        def verify(self, _):
            if failure:
                raise OSError("private raw error must not be displayed")
            return [{"label": "Synthetic failed check", "passed": False}]

    verification.process_one(store, Adapter())
    result = client.get(URL).json()[0]
    assert result["verification"] == ("unknown" if failure else "failed")
    assert result["attempt_outcome"] == ("failed" if failure else "completed")
    assert "private" not in result["message"] and not result["publication_allowed"]
