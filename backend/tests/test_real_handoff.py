"""NON-MODEL completion recovery using real file seals/worker; checks are stub evidence."""

from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

import pytest
from sqlalchemy import select

from workbench import real_execution as job
from workbench.db import model_attempts, engine_for
from workbench.real_workspace import Runtime, durable, sha, PRELUDE


class HandoffRuntime(Runtime):
    def preflight(self):
        return [dict(name="NON-MODEL disposable", status="passed")]

    def identity(self):
        return dict(
            model="NON-MODEL",
            oracle_sha256=sha((self.root / "oracle.lua").read_bytes()),
            base_manifest_sha256=sha(self.initial.read_bytes()),
        )

    def enforce_storage(self):
        pass

    def cleanup(self, row):
        return True

    def failure_evidence(self, *args):
        return dict(usage=dict(status="unavailable", values=None))

    def execute(self, identity, fence, permitted):
        assert permitted()
        from workbench.candidate_review import compare
        text = b"-- NON-MODEL discarded evidence fixture\n"
        comparison = compare(job.PATHS[0], b'-- NON-MODEL base fixture\n', text)
        patch = comparison['diff'].encode()
        manifest = dict(
            request_id=identity,
            fence=fence,
            base=job.BASE,
            tree={job.PATHS[0]: sha(text)},
            patch_sha256=sha(patch),
            oracle_sha256=self.identity()["oracle_sha256"],
        )
        linked = getattr(self, 'workflow_task', None)
        if linked:
            manifest['workflow_task_binding'] = job.digest(linked)
            manifest['workflow_task'] = dict(intake_id=linked['intake_id'],task_id=linked['task']['id'])
        blob = json.dumps(manifest, sort_keys=True).encode()
        candidate = sha(blob)
        folder = self.root / "candidates" / candidate
        durable(folder / "manifest.json", blob)
        durable(folder / "changes.patch", patch)
        durable(folder / "files" / job.PATHS[0], text)
        commands = [["luac5.1", "-p", p] for p in job.PATHS] + [
            ["lua5.1", "-e", PRELUDE, "test_chronicle_journey.lua"],
            ["lua5.1", "-e", PRELUDE, str(self.root / "oracle.lua")],
            ["bash", "tests/run.sh"],
        ]
        output = (
            "\n".join("ok   " + n for n in self.expected_suite())
            + f"\n{len(self.expected_suite())} passed, 0 failed"
        )
        result = dict(
            candidate_id=candidate,
            manifest=manifest,
            verification="passed",
            inference_ran=False,
            files=[comparison],
            usage=dict(status="complete", values=dict(completion_tokens=0)),
            checks=[
                dict(
                    status="pass",
                    rc=0,
                    command=c,
                    out=output,
                    network_mode="private-loopback" if i == 5 else "denied",
                )
                for i, c in enumerate(commands)
            ],
        )
        durable(folder / "result.json", json.dumps(result, sort_keys=True).encode())
        return result


@pytest.fixture
def runtime(tmp_path):
    root = tmp_path / "NON_MODEL"
    root.mkdir()
    durable(root / "oracle.lua", b"-- NON-MODEL stub, never executed")
    durable(root / "base-manifest.json", b"{}")
    return HandoffRuntime(root)


def interrupted(store, runtime, monkeypatch):
    grant = job.issue(store, "NON-MODEL test authorization", "pytest", runtime)
    request = job.submit(store, grant["id"], str(uuid4()))
    save = runtime.record_completion

    def crash(*args):
        save(*args)
        raise SystemExit("NON-MODEL crash after fsync, before terminal commit")

    monkeypatch.setattr(runtime, "record_completion", crash)
    with pytest.raises(SystemExit):
        job.process_one(store, runtime)
    return grant, request


def test_saved_completion_recovers_once_after_owner_exit_without_replay(
    store, runtime, monkeypatch
):
    _, request = interrupted(store, runtime, monkeypatch)
    owner = job.owner_identity()
    monkeypatch.setattr(job, "owner_identity", lambda: {**owner, "pid": owner["pid"] + 1000000})
    job.recover(store, runtime)
    assert job.review(store, request["id"])["status"] == "running"
    monkeypatch.setattr(job, "alive", lambda _: False)
    monkeypatch.setattr(runtime, "execute", lambda *a: pytest.fail("Never replay inference"))
    job.recover(store, runtime)
    job.recover(store, runtime)
    view = job.review(store, request["id"])
    assert view["status"] == "checked" and view["result"]["verification"] == "passed"
    assert "Nothing was rerun" in view["message"] and view["publication_allowed"] is False
    with store.connect() as c:
        rows = (
            c.execute(select(model_attempts).where(model_attempts.c.execution_id == request["id"]))
            .mappings()
            .all()
        )
    assert (
        len(rows) == 1
        and rows[0]["outcome"] == "checked"
        and rows[0]["usage"]["status"] == "complete"
    )


@pytest.mark.parametrize(
    "change",
    [
        "fence",
        "grant_binding",
        "candidate_id",
        "timestamp",
        "result",
        "oracle",
        "missing",
        "cleanup",
        "base",
        "oversized",
        "symlink",
    ],
)
def test_bad_or_unsafe_handoff_cannot_be_promoted(store, runtime, monkeypatch, change):
    _, request = interrupted(store, runtime, monkeypatch)
    path = runtime.root / ("completion-" + request["id"] + ".json")
    seal = json.loads(path.read_text())
    if change in ("fence", "grant_binding"):
        seal[change] = "wrong"
    elif change == "candidate_id":
        seal[change] = "../../outside"
    elif change == "timestamp":
        seal["completed_at"] = "2099-01-01T00:00:00+00:00"
    elif change == "result":
        (runtime.root / "candidates" / seal["candidate_id"] / "result.json").write_text("{}")
    elif change == "oracle":
        (runtime.root / "oracle.lua").write_text("changed")
    elif change == "oversized":
        path.write_text("x" * 4097)
    elif change == "symlink":
        folder = runtime.root / "candidates" / seal["candidate_id"]
        outside = runtime.root.parent / "outside-candidate"
        folder.rename(outside)
        folder.symlink_to(outside, target_is_directory=True)
    elif change == "base":
        runtime.initial.write_text('{"changed": "NON-MODEL"}')
    elif change == "missing":
        path.unlink()
    elif change == "cleanup":
        monkeypatch.setattr(runtime, "cleanup", lambda _: False)
    if path.exists() and change != "oversized":
        path.write_text(json.dumps(seal))
    monkeypatch.setattr(job, "alive", lambda _: False)
    job.recover(store, runtime)
    assert job.review(store, request["id"])["status"] == (
        "running" if change == "cleanup" else "interrupted"
    )


def test_expired_current_grant_does_not_erase_preexpiry_verified_outcome(
    store, runtime, monkeypatch
):
    _, request = interrupted(store, runtime, monkeypatch)

    # Advance the clock used for current eligibility; never alter the original seal/grant.
    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(timezone.utc) + timedelta(hours=2)

    monkeypatch.setattr(job, "datetime", Later)
    monkeypatch.setattr(job, "alive", lambda _: False)
    job.recover(store, runtime)
    view = job.review(store, request["id"])
    assert view["status"] == "checked" and view["current_grant"] is False


def test_listing_discovers_worker_outcomes_filters_and_has_no_effect(store, client, runtime):
    grant = job.issue(store, "NON-MODEL test", "pytest", runtime)
    receipt = job.submit(store, grant["id"], str(uuid4()))
    assert client.get("/api/real-executions?group=active").json()["items"][0]["id"] == receipt["id"]
    assert job.process_one(store, runtime)
    response = client.get("/api/real-executions?group=checked")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1 and data["items"][0]["verification"] == "passed"
    assert data["publication_allowed"] is False
    assert client.get("/api/real-executions?group=active").json()["total"] == 0
    assert client.get("/api/real-executions?page=0").status_code == 422
    assert client.get("/api/real-executions?group=run-now").status_code == 422
    assert client.post("/api/real-executions", json={}).status_code != 200


def test_old_store_read_does_not_initialize_or_migrate(tmp_path):
    engine = engine_for(tmp_path / "empty.sqlite3")
    try:
        view = job.listing(engine)
        assert not view["available"] and not view["items"]
        from sqlalchemy import inspect

        assert inspect(engine).get_table_names() == []
    finally:
        engine.dispose()


def test_same_worker_reconciles_sealed_completion_after_failed_terminal_commit(
    store, runtime, monkeypatch
):
    _, request = interrupted(store, runtime, monkeypatch)
    monkeypatch.setattr(runtime, "execute", lambda *a: pytest.fail("No second implementation"))
    assert not job.process_one(store, runtime)
    assert job.review(store, request["id"])["status"] == "checked"


def test_listing_pages_are_bounded_and_do_not_load_frozen_results(store, client, runtime):
    for i in range(13):
        grant = job.issue(store, "NON-MODEL " + str(i), "pytest", runtime)
        job.submit(store, grant["id"], str(uuid4()))
    first = client.get("/api/real-executions").json()
    second = client.get("/api/real-executions?page=2").json()
    assert first["total"] == second["total"] == 13
    assert len(first["items"]) == 12 and len(second["items"]) == 1
    assert not ({r["id"] for r in first["items"]} & {r["id"] for r in second["items"]})
    assert all("result" not in row for row in first["items"])


def test_transient_final_database_failure_is_reconciled_by_same_worker(store, runtime, monkeypatch):
    from contextlib import contextmanager
    import sqlite3
    from sqlalchemy.exc import OperationalError

    grant = job.issue(store, "NON-MODEL DB failure", "pytest", runtime)
    request = job.submit(store, grant["id"], str(uuid4()))
    original = job.write_transaction
    transactions = 0

    @contextmanager
    def transient(engine):
        nonlocal transactions
        transactions += 1
        if transactions == 2:
            busy_error = sqlite3.OperationalError("database is locked")
            busy_error.sqlite_errorcode = 5
            raise OperationalError(
                "NON-MODEL terminal commit", {}, busy_error
            )
        with original(engine) as connection:
            yield connection

    monkeypatch.setattr(job, "write_transaction", transient)
    assert job.process_one(store, runtime)
    assert job.review(store, request["id"])["status"] == "checked"
    monkeypatch.setattr(job, "write_transaction", original)
    monkeypatch.setattr(runtime, "execute", lambda *a: pytest.fail("Do not replay implementation"))
    assert not job.process_one(store, runtime)
    assert job.review(store, request["id"])["status"] == "checked"


def test_storage_refusal_cannot_publish_a_recoverable_success(store, runtime, monkeypatch):
    grant = job.issue(store, "NON-MODEL seal publication", "pytest", runtime)
    receipt = job.submit(store, grant["id"], str(uuid4()))
    with store.connect() as c:
        saved = c.execute(select(job.grants).where(job.grants.c.id == grant["id"])).mappings().one()
    fence = str(uuid4())
    result = runtime.execute(receipt["id"], fence, lambda: True)
    calls = 0

    def exhausted():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("NON-MODEL storage exhausted after writing staging evidence")

    monkeypatch.setattr(runtime, "enforce_storage", exhausted)
    with pytest.raises(ValueError, match="storage exhausted"):
        runtime.record_completion(saved, receipt["id"], fence, result, job.now())
    assert runtime.recover_completion({"id": receipt["id"], "fence": fence}, saved) is None


def test_live_owner_without_completion_cannot_enter_cleanup(store, runtime, monkeypatch):
    grant = job.issue(store, "NON-MODEL unsealed owner", "pytest", runtime)
    request = job.submit(store, grant["id"], str(uuid4()))

    def unsealed(*args):
        raise SystemExit("NON-MODEL active operation, no completion seal")

    monkeypatch.setattr(runtime, "execute", unsealed)
    with pytest.raises(SystemExit):
        job.process_one(store, runtime)
    monkeypatch.setattr(
        runtime, "cleanup", lambda _: pytest.fail("Active unsealed work must not be cleaned up")
    )
    job.recover(store, runtime)
    assert job.review(store, request["id"])["status"] == "running"
