"""Disposable exact-task sample implementation, atomic artifact and separate verification."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, select

from test_admission import ready as approved_task
from workbench import admission, implementation as impl, verification
from workbench.db import (
    metadata,
    engine_for,
    write_transaction,
    intake_scopes,
    proposal_decisions,
    backlog_items,
    planning_jobs,
)

ROOT = Path(__file__).resolve().parents[2]
NEW = {impl.records, impl.attempts, impl.candidates, impl.verifications}


def existing(store):
    with store.connect() as c:
        return {
            t.name: [dict(r) for r in c.execute(select(t)).mappings()]
            for t in metadata.sorted_tables
            if t not in NEW
        }


def ready(client, headers, store):
    identity, task, admission_path, body = approved_task(client, headers, store)
    assert client.post(admission_path, headers=headers, json=body).status_code == 200
    assert admission.process_one(store)
    path = admission_path.removesuffix("admission") + "implementation"
    context = client.get(path).json()
    assert context["current_authority"]["eligible"], context
    authority = context["current_authority"]
    return (
        identity,
        task,
        path,
        dict(
            command_id=str(uuid4()),
            mode="fixture",
            admission_id=authority["admission_id"],
            binding=authority["binding"],
        ),
    )


def sample(client, headers, store):
    identity, task, path, body = ready(client, headers, store)
    response = client.post(path, headers=headers, json=body)
    assert response.status_code == 200, response.text
    assert impl.process_one(store)
    receipt = client.get(path).json()["receipts"][0]
    assert receipt["status"] == "completed" and receipt["candidate"]["current"]
    candidate = receipt["candidate"]
    vpath = path.removesuffix("implementation") + "candidates/" + candidate["id"] + "/verification"
    vbody = dict(command_id=str(uuid4()), mode="fixture", binding=candidate["id"])
    return identity, task, path, body, receipt, vpath, vbody


def real_worker(store, *args):
    return subprocess.run(
        [sys.executable, "-m", "workbench.worker", "--once", *args],
        cwd=ROOT,
        env={
            **os.environ,
            "PYTHONPATH": str(ROOT / "backend"),
            "WORKBENCH_DB": str(store.url.database),
            "WORKBENCH_PLANNER": "unavailable",
        },
        capture_output=True,
        timeout=10,
    )


def test_explicit_end_to_end_existing_worker_no_prior_record_changes(client, headers, store):
    identity, task, path, body = ready(client, headers, store)
    before = existing(store)
    pending = client.post(path, headers=headers, json=body).json()
    assert (
        pending["status"] == "pending"
        and pending["attempt"] is None
        and pending["candidate"] is None
    )
    assert pending["finished_at"] is None
    assert not pending["execution_allowed"] and not pending["publication_allowed"]
    assert client.post(path, headers=headers, json=body).json() == pending
    assert real_worker(store).returncode == 0
    receipt = client.get(path).json()["receipts"][0]
    candidate, attempt = receipt["candidate"], receipt["attempt"]
    assert receipt["status"] == attempt["outcome"] == "completed"
    assert attempt["source"] == candidate["source"] == "deterministic-fixture"
    assert (
        receipt["created_at"]
        <= attempt["started_at"]
        <= attempt["finished_at"]
        == candidate["created_at"]
        == receipt["finished_at"]
    )
    assert (
        candidate["integrity_valid"] and candidate["current"] and candidate["verification"] is None
    )
    assert "model" not in attempt and "usage" not in attempt
    assert (
        hashlib.sha256(candidate["artifact_text"].encode()).hexdigest()
        == candidate["provenance"]["artifact_sha256"]
    )
    assert impl.digest(candidate["provenance"]) == candidate["id"]
    assert candidate["provenance"]["task_id"] == task["id"]
    assert candidate["provenance"]["admission_id"] == body["admission_id"]
    assert candidate["provenance"]["request_binding"] == body["binding"]
    assert not impl.process_one(store)
    assert candidate["verification"] is None  # No implicit verifier request/result.
    vpath = path.removesuffix("implementation") + "candidates/" + candidate["id"] + "/verification"
    vbody = dict(command_id=str(uuid4()), mode="fixture", binding=candidate["id"])
    saved = client.post(vpath, headers=headers, json=vbody).json()
    assert (
        saved["status"] == "pending"
        and saved["verification"] == "unknown"
        and saved["started_at"] is None
    )
    assert client.post(vpath, headers=headers, json=vbody).json() == saved
    assert real_worker(store).returncode == 0
    result = client.get(path).json()["receipts"][0]["candidate"]["verification"]
    assert result["status"] == "completed" and result["verification"] == "passed"
    assert len(result["checks"]) == 2 and all(c["passed"] for c in result["checks"])
    assert saved["created_at"] <= result["started_at"] <= result["finished_at"]
    assert not result["execution_allowed"] and not result["publication_allowed"]
    assert client.post(vpath, headers=headers, json=vbody).json() == result
    assert (
        client.post(path, headers=headers, json=body).json()["candidate"]["verification"] == result
    )
    assert not verification.process_one(store)
    assert existing(store) == before


@pytest.mark.parametrize(
    "stage", ["submission", "worker", "verification_submission", "verification_worker"]
)
@pytest.mark.parametrize(
    "change",
    ["missing_scope", "research", "revoked", "task", "fixture", "admission", "decomposition"],
)
def test_current_authority_required_at_every_boundary(
    client, headers, store, monkeypatch, tmp_path, stage, change
):
    identity, task, path, body = ready(client, headers, store)
    if stage != "submission":
        assert client.post(path, headers=headers, json=body).status_code == 200
    if stage.startswith("verification"):
        assert impl.process_one(store)
        candidate = client.get(path).json()["receipts"][0]["candidate"]
        vpath = (
            path.removesuffix("implementation") + "candidates/" + candidate["id"] + "/verification"
        )
        vbody = dict(command_id=str(uuid4()), mode="fixture", binding=candidate["id"])
        if stage == "verification_worker":
            assert client.post(vpath, headers=headers, json=vbody).status_code == 200
    if change == "fixture":
        fixture = impl.load_fixture()
        fixture["cases"][0]["input"] = " Changed fixture "
        changed = tmp_path / "changed.json"
        changed.write_text(json.dumps(fixture))
        monkeypatch.setattr(impl, "FIXTURE", changed)
    else:
        with write_transaction(store) as c:
            if change == "missing_scope":
                c.execute(intake_scopes.delete())
            elif change == "research":
                c.execute(intake_scopes.update().values(stage="research"))
            elif change == "revoked":
                c.execute(proposal_decisions.update().values(scope_review_required=1))
            elif change == "task":
                c.execute(
                    backlog_items.update()
                    .where(backlog_items.c.id == task["id"])
                    .values(purpose="Changed task")
                )
            elif change == "admission":
                c.execute(admission.records.update().values(status="blocked"))
            else:
                c.execute(
                    planning_jobs.update()
                    .where(planning_jobs.c.kind == "decomposition")
                    .values(status="failed")
                )
    if stage == "submission":
        assert client.post(path, headers=headers, json=body).status_code in {404, 409}
    elif stage == "worker":

        def forbidden(_):
            pytest.fail("Invalid authority reached sample generation")

        monkeypatch.setattr(impl, "generate", forbidden)
        assert impl.process_one(store)
        receipt = client.get(path).json()["receipts"][0]
        assert (
            receipt["status"] in {"stale", "blocked"}
            and receipt["attempt"] is None
            and receipt["candidate"] is None
        )
    elif stage == "verification_submission":
        assert client.post(vpath, headers=headers, json=vbody).status_code in {404, 409}
    else:

        class Forbidden:
            def verify_sample(self, _):
                pytest.fail("Invalid authority reached verifier")

        assert impl.process_verification(store, Forbidden())
        result = client.get(path).json()["receipts"][0]["candidate"]["verification"]
        assert result["status"] in {"stale", "blocked"} and result["verification"] == "unknown"
        assert result["started_at"] is None and result["checks"] == []


@pytest.mark.parametrize(
    "change", ["artifact_partial", "artifact_missing", "provenance", "attempt", "request"]
)
@pytest.mark.parametrize("after_save", [False, True])
def test_artifact_and_candidate_integrity_boundaries(client, headers, store, change, after_save):
    _, _, path, _, receipt, vpath, vbody = sample(client, headers, store)
    candidate = receipt["candidate"]
    if after_save:
        assert client.post(vpath, headers=headers, json=vbody).status_code == 200
    with write_transaction(store) as c:
        if change.startswith("artifact"):
            c.execute(
                impl.candidates.update().values(
                    artifact=b"{" if change.endswith("partial") else b""
                )
            )
        elif change == "provenance":
            c.execute(
                impl.candidates.update().values(
                    provenance={**candidate["provenance"], "task_id": "wrong-task"}
                )
            )
        elif change == "attempt":
            c.execute(impl.attempts.update().values(source="model"))
        else:
            request = c.execute(select(impl.records)).mappings().one()
            pinned = request["snapshot"]
            pinned["fixture_sha256"] = "0" * 64
            c.execute(impl.records.update().values(snapshot=pinned))
    if after_save:
        assert impl.process_verification(store)
        result = client.get(path).json()["receipts"][0]["candidate"]["verification"]
        assert result["status"] == "stale" and result["verification"] == "unknown"
        assert result["started_at"] is None and not result["checks"]
    else:
        assert client.post(vpath, headers=headers, json=vbody).status_code == 409
    historical = client.get(path).json()["receipts"][0]
    assert historical["status"] == "completed" and not historical["candidate"]["current"]
    if change.startswith("artifact") or change == "provenance":
        assert (
            not historical["candidate"]["integrity_valid"]
            and historical["candidate"]["artifact_text"] is None
        )


def test_verifier_checks_produced_content_not_success_flag(client, headers, store, monkeypatch):
    original = impl.generate

    def wrong_output(fixture):
        content = json.loads(original(fixture))
        content["cases"][1]["output"] = "Two inner spaces"
        return json.dumps(content).encode()

    monkeypatch.setattr(impl, "generate", wrong_output)
    _, _, path, _, _, vpath, vbody = sample(client, headers, store)
    assert client.post(vpath, headers=headers, json=vbody).status_code == 200
    assert verification.process_one(store)
    result = client.get(path).json()["receipts"][0]["candidate"]["verification"]
    assert result["status"] == "completed" and result["verification"] == "failed"
    assert [check["passed"] for check in result["checks"]] == [True, False]


@pytest.mark.parametrize("change", ["payload", "admission", "binding", "route", "new_id", "mode"])
def test_implementation_conflicts(client, headers, store, change):
    identity, _, path, body = ready(client, headers, store)
    assert client.post(path, headers=headers, json=body).status_code == 200
    bad = dict(body)
    if change == "route":
        path = path.replace(identity, str(uuid4()), 1)
    elif change == "admission":
        bad["admission_id"] = str(uuid4())
    elif change == "binding":
        bad["binding"] = "0" * 64
    elif change == "new_id":
        bad["command_id"] = str(uuid4())
    elif change == "mode":
        bad["mode"] = "real"
    else:
        bad["unexpected"] = True
    assert client.post(path, headers=headers, json=bad).status_code == (
        422 if change in {"payload", "mode"} else 409
    )


@pytest.mark.parametrize("change", ["binding", "route", "candidate", "new_id", "mode"])
def test_verification_conflicts(client, headers, store, change):
    identity, _, _, _, receipt, vpath, body = sample(client, headers, store)
    assert client.post(vpath, headers=headers, json=body).status_code == 200
    bad = dict(body)
    if change == "route":
        vpath = vpath.replace(identity, str(uuid4()), 1)
    elif change == "candidate":
        vpath = vpath.replace(receipt["candidate"]["id"], "0" * 64)
    elif change == "new_id":
        bad["command_id"] = str(uuid4())
    elif change == "mode":
        bad["mode"] = "real"
    else:
        bad["binding"] = "0" * 64
    assert client.post(vpath, headers=headers, json=bad).status_code == (
        422 if change == "mode" else 409
    )


def test_history_and_exact_retries_survive_changed_current_scope(client, headers, store):
    _, _, path, body, receipt, vpath, vbody = sample(client, headers, store)
    client.post(vpath, headers=headers, json=vbody)
    verification.process_one(store)
    before = client.get(path).json()["receipts"][0]
    with write_transaction(store) as c:
        c.execute(intake_scopes.update().values(revision=2))
    data = client.get(path).json()
    assert not data["current_authority"]["eligible"]
    after = data["receipts"][0]
    assert (
        after["status"] == "completed"
        and after["candidate"]["verification"]["verification"] == "passed"
    )
    assert not after["candidate"]["current"]
    assert after["candidate"]["artifact_text"] == before["candidate"]["artifact_text"]
    assert (
        client.post(vpath, headers=headers, json=vbody).json()
        == before["candidate"]["verification"]
    )
    assert client.post(path, headers=headers, json=body).json() == after
    assert (
        client.post(vpath, headers=headers, json={**vbody, "command_id": str(uuid4())}).status_code
        == 409
    )


@pytest.mark.parametrize("point", ["after-artifact", "before-commit", "after-commit"])
def test_real_crash_atomic_artifact_attempt_and_request_recovery(client, headers, store, point):
    _, _, path, body = ready(client, headers, store)
    client.post(path, headers=headers, json=body)
    script = (
        "from workbench.implementation import process_one; from workbench.db import engine_for; process_one(engine_for(),crash="
        + repr(point)
        + ")"
    )
    run = subprocess.run(
        [sys.executable, "-c", script],
        env={
            **os.environ,
            "PYTHONPATH": str(ROOT / "backend"),
            "WORKBENCH_DB": str(store.url.database),
        },
        timeout=10,
    )
    assert run.returncode == (92 if point == "after-commit" else 91)
    receipt = client.get(path).json()["receipts"][0]
    if point != "after-commit":
        assert (
            receipt["status"] == "pending"
            and receipt["attempt"] is None
            and receipt["candidate"] is None
        )
        with store.connect() as c:
            assert (
                c.scalar(select(impl.candidates.c.id)) is None
                and c.scalar(select(impl.attempts.c.id)) is None
            )
        assert real_worker(store).returncode == 0
    else:
        assert receipt["status"] == "completed"
    result = client.get(path).json()["receipts"][0]
    assert result["candidate"]["integrity_valid"]
    assert not impl.process_one(store)
    with store.connect() as c:
        assert (
            len(c.execute(select(impl.candidates)).all())
            == len(c.execute(select(impl.attempts)).all())
            == 1
        )


@pytest.mark.parametrize("point", ["before-commit", "after-commit"])
def test_verification_crash_and_revalidation(client, headers, store, point):
    _, _, path, _, _, vpath, body = sample(client, headers, store)
    client.post(vpath, headers=headers, json=body)
    run = subprocess.run(
        [
            sys.executable,
            "-c",
            "from workbench.implementation import process_verification; from workbench.db import engine_for; process_verification(engine_for(),crash="
            + repr(point)
            + ")",
        ],
        env={
            **os.environ,
            "PYTHONPATH": str(ROOT / "backend"),
            "WORKBENCH_DB": str(store.url.database),
        },
        timeout=10,
    )
    assert run.returncode == (92 if point == "after-commit" else 91)
    result = client.get(path).json()["receipts"][0]["candidate"]["verification"]
    if point == "before-commit":
        assert (
            result["status"] == "pending"
            and result["verification"] == "unknown"
            and result["started_at"] is None
        )
        with write_transaction(store) as c:
            c.execute(proposal_decisions.update().values(scope_review_required=1))
        assert verification.process_one(store)
        result = client.get(path).json()["receipts"][0]["candidate"]["verification"]
        assert result["status"] == "blocked" and result["verification"] == "unknown"
    else:
        assert result["verification"] == "passed" and not verification.process_one(store)


def test_failed_attempt_and_verifier_error_are_distinct(client, headers, store, monkeypatch):
    _, _, path, body = ready(client, headers, store)
    client.post(path, headers=headers, json=body)

    def fail(_):
        raise OSError("Synthetic failure")

    monkeypatch.setattr(impl, "generate", fail)
    assert impl.process_one(store)
    receipt = client.get(path).json()["receipts"][0]
    assert receipt["status"] == "failed" and receipt["attempt"]["outcome"] == "failed"
    assert receipt["candidate"] is None


def test_verifier_error_retains_candidate_and_unknown_result(client, headers, store):
    _, _, path, _, receipt, vpath, body = sample(client, headers, store)
    client.post(vpath, headers=headers, json=body)

    class Failed:
        def verify_sample(self, _):
            raise OSError("Synthetic checker failure")

    assert impl.process_verification(store, Failed())
    after = client.get(path).json()["receipts"][0]
    assert after["candidate"]["id"] == receipt["candidate"]["id"]
    assert after["candidate"]["verification"]["status"] == "failed"
    assert after["candidate"]["verification"]["verification"] == "unknown"


def test_no_model_executor_verifier_or_forbidden_writes_during_implementation(
    client, headers, store, monkeypatch
):
    from workbench import planning_provider, model_policy

    _, _, path, body = ready(client, headers, store)
    before = existing(store)

    def forbidden(*_, **__):
        pytest.fail("Forbidden provider/executor/verifier/routing hook")

    monkeypatch.setattr(planning_provider.DemoProvider, "generate", forbidden)
    monkeypatch.setattr(planning_provider.LocalProvider, "generate", forbidden)
    monkeypatch.setattr(verification.FixtureVerifier, "verify", forbidden)
    monkeypatch.setattr(verification.FixtureVerifier, "verify_sample", forbidden)
    monkeypatch.setattr(model_policy, "evaluate", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(Path, "write_text", forbidden)
    writes = []

    def observe(_c, _cursor, statement, _p, _ctx, _many):
        if statement.lstrip().upper().startswith(("INSERT ", "UPDATE ", "DELETE ")):
            writes.append(statement)

    event.listen(store, "before_cursor_execute", observe)
    try:
        assert client.post(path, headers=headers, json=body).status_code == 200
        assert impl.process_one(store)
        receipt = client.get(path).json()["receipts"][0]
        assert receipt["candidate"]["verification"] is None
    finally:
        event.remove(store, "before_cursor_execute", observe)
    assert len(writes) == 4
    assert all(
        any(
            name in sql
            for name in [
                "implementation_requests",
                "implementation_attempts",
                "implementation_candidates",
            ]
        )
        for sql in writes
    )
    assert existing(store) == before


def test_security_missing_admission_and_original_fixture_separation(client, headers, store):
    _, _, path, body = ready(client, headers, store)
    assert client.post(path, json=body).status_code == 403
    assert (
        client.post(path, headers={**headers, "origin": "http://evil"}, json=body).status_code
        == 403
    )
    assert client.get(path, headers={"host": "evil"}).status_code == 400
    assert (
        client.post(path, headers=headers, json={**body, "admission_id": str(uuid4())}).status_code
        == 404
    )
    client.post(path, headers=headers, json=body)
    impl.process_one(store)
    candidate = client.get(path).json()["receipts"][0]["candidate"]
    assert client.get("/api/tasks/" + candidate["id"]).status_code == 404
    assert (
        client.post(
            "/api/tasks/cozy-step-review/verification",
            headers=headers,
            json=dict(command_id=str(uuid4()), mode="fixture", version=candidate["id"]),
        ).status_code
        == 409
    )
    with store.connect() as c:
        assert c.execute(select(verification.records)).all() == []


def test_concurrent_duplicate_requests_and_workers(client, headers, store):
    identity, task, path, body = ready(client, headers, store)
    parsed = impl.SimulatedImplementationRequest.model_validate(body)
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(
            pool.map(lambda _: impl.submit(store, identity, task["id"], parsed), range(2))
        )
    assert receipts[0] == receipts[1]
    with ThreadPoolExecutor(max_workers=2) as pool:
        worked = list(pool.map(lambda _: impl.process_one(store), range(2)))
    assert sorted(worked) == [False, True]
    assert client.get(path).json()["receipts"][0]["candidate"]["integrity_valid"]


def test_populated_0007_upgrade_preserves_history_and_restore(tmp_path):
    from workbench.api import create_app
    from workbench.seed import seed
    from workbench import worker

    engine = engine_for(tmp_path / "old.sqlite3")
    config = Config(str(ROOT / "alembic.ini"))
    with engine.connect() as c:
        config.attributes["connection"] = c
        command.upgrade(config, "0007")
        c.commit()
    seed(engine)
    with TestClient(create_app(engine)) as client:
        headers = {
            "origin": "http://testserver",
            "x-csrf-token": client.get("/api/session").json()["csrf_token"],
        }
        task = client.get("/api/tasks/cozy-step-review").json()
        client.post(
            "/api/tasks/cozy-step-review/decisions",
            headers=headers,
            json=dict(
                command_id=str(uuid4()),
                version=task["version"],
                choices={c["id"]: "confirm" for c in task["evidence"]["claims"]},
            ),
        )
        worker.process_one(engine)
        task = client.get("/api/tasks/cozy-step-review").json()
        client.post(
            "/api/tasks/cozy-step-review/verification",
            headers=headers,
            json=dict(command_id=str(uuid4()), version=task["version"], mode="fixture"),
        )
        verification.process_one(engine)
        _, _, admission_path, body = approved_task(client, headers, engine)
        client.post(admission_path, headers=headers, json=body)
        admission.process_one(engine)
    with engine.connect() as c:
        before = {
            name: c.exec_driver_sql('SELECT * FROM "' + name + '" ORDER BY rowid').all()
            for (name,) in c.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table' AND name!='alembic_version'"
            )
        }
        config.attributes["connection"] = c
        command.upgrade(config, "head")
        c.commit()
        command.upgrade(config, "head")
        c.commit()
        command.check(config)
        for name, rows in before.items():
            actual = c.exec_driver_sql('SELECT * FROM "' + name + '" ORDER BY rowid').all()
            if name == 'model_attempts':
                # 0010 adds only a nullable real-execution reference to historical rows.
                assert all(row[-1] is None for row in actual)
                actual = [tuple(row[:-1]) for row in actual]
            assert actual == rows
        assert c.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        assert c.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == "0013"
    with TestClient(create_app(engine)) as client:
        headers = {
            "origin": "http://testserver",
            "x-csrf-token": client.get("/api/session").json()["csrf_token"],
        }
        path = admission_path.removesuffix("admission") + "implementation"
        current = client.get(path).json()["current_authority"]
        body = dict(
            command_id=str(uuid4()),
            mode="fixture",
            admission_id=current["admission_id"],
            binding=current["binding"],
        )
        client.post(path, headers=headers, json=body)
        impl.process_one(engine)
        candidate = client.get(path).json()["receipts"][0]["candidate"]
        vpath = (
            path.removesuffix("implementation") + "candidates/" + candidate["id"] + "/verification"
        )
        client.post(
            vpath,
            headers=headers,
            json=dict(command_id=str(uuid4()), mode="fixture", binding=candidate["id"]),
        )
        verification.process_one(engine)
        expected = client.get(path).json()
    backup_path = tmp_path / "restore.sqlite3"
    with sqlite3.connect(engine.url.database) as source, sqlite3.connect(backup_path) as backup:
        source.backup(backup)
    restored = engine_for(backup_path)
    assert not impl.process_one(restored) and not verification.process_one(restored)
    with TestClient(create_app(restored)) as client:
        assert client.get(path).json() == expected
    restored.dispose()
    engine.dispose()


@pytest.mark.parametrize("invalid", [b"", b"x" * 32769, "not bytes"])
def test_partial_or_oversized_generation_creates_failed_attempt_without_candidate(
    client, headers, store, monkeypatch, invalid
):
    _, _, path, body = ready(client, headers, store)
    client.post(path, headers=headers, json=body)
    monkeypatch.setattr(impl, "generate", lambda _: invalid)
    assert impl.process_one(store)
    receipt = client.get(path).json()["receipts"][0]
    assert receipt["status"] == "failed" and receipt["attempt"]["outcome"] == "failed"
    assert receipt["candidate"] is None
    with store.connect() as c:
        assert c.execute(select(impl.verifications)).all() == []


def test_missing_fixture_keeps_history_and_prevents_pending_generation(
    client, headers, store, monkeypatch, tmp_path
):
    _, _, path, body = ready(client, headers, store)
    client.post(path, headers=headers, json=body)
    monkeypatch.setattr(impl, "FIXTURE", tmp_path / "missing.json")
    assert impl.process_one(store)
    data = client.get(path).json()
    assert not data["current_authority"]["eligible"]
    assert data["receipts"][0]["status"] == "failed" and data["receipts"][0]["attempt"] is None


def test_wrong_task_route_and_forged_candidate_binding_are_rejected(client, headers, store):
    identity, task, path, _, receipt, vpath, body = sample(client, headers, store)
    assert (
        client.post(
            vpath.replace(task["id"], "intake-" + identity), headers=headers, json=body
        ).status_code
        == 404
    )
    candidate = receipt["candidate"]
    with write_transaction(store) as c:
        modified = {**candidate["provenance"], "request_binding": "0" * 64}
        forged_id = impl.digest(modified)
        c.execute(impl.candidates.update().values(id=forged_id, provenance=modified))
    forged_path = vpath.replace(candidate["id"], forged_id)
    assert (
        client.post(forged_path, headers=headers, json={**body, "binding": forged_id}).status_code
        == 409
    )


def test_pending_guard_and_submission_rollback(client, headers, store, monkeypatch):
    identity, task, path, body = ready(client, headers, store)
    original_view = impl.receipt_view

    def fail(*_):
        raise RuntimeError("Failure before acknowledgment")

    monkeypatch.setattr(impl, "receipt_view", fail)
    with pytest.raises(RuntimeError):
        client.post(path, headers=headers, json=body)
    monkeypatch.setattr(impl, "receipt_view", original_view)
    assert client.get(path).json()["receipts"] == []
    assert client.post(path, headers=headers, json=body).status_code == 200
    # A new fixture version of inputs must not bypass the per-task pending guard.
    snapshot = impl.load_fixture()
    snapshot["cases"][0]["input"] = " A different sample "
    original_fixture = impl.load_fixture
    monkeypatch.setattr(impl, "load_fixture", lambda: snapshot)
    authority = client.get(path).json()["current_authority"]
    assert authority["eligible"] and authority["binding"] != body["binding"]
    assert (
        client.post(
            path,
            headers=headers,
            json={**body, "command_id": str(uuid4()), "binding": authority["binding"]},
        ).status_code
        == 409
    )
    monkeypatch.setattr(impl, "load_fixture", original_fixture)


def test_event_capture_surrounds_generator_and_verifier(client, headers, store, monkeypatch):
    _, _, path, body = ready(client, headers, store)
    client.post(path, headers=headers, json=body)
    times = iter(["2026-11-04T04:00:00+00:00", "2026-11-04T04:00:01+00:00"])
    seen = []

    def clock():
        value = next(times)
        seen.append("clock")
        return value

    original_generate = impl.generate

    def generate(fixture):
        seen.append("generate")
        return original_generate(fixture)

    monkeypatch.setattr(impl, "now", clock)
    monkeypatch.setattr(impl, "generate", generate)
    assert impl.process_one(store)
    assert seen == ["clock", "generate", "clock"]
    receipt = client.get(path).json()["receipts"][0]
    assert receipt["attempt"]["started_at"] == "2026-11-04T04:00:00+00:00"
    assert receipt["finished_at"] == "2026-11-04T04:00:01+00:00"
    monkeypatch.setattr(impl, "now", lambda: "2026-11-04T04:00:02+00:00")
    candidate = receipt["candidate"]
    vpath = path.removesuffix("implementation") + "candidates/" + candidate["id"] + "/verification"
    client.post(
        vpath,
        headers=headers,
        json=dict(command_id=str(uuid4()), mode="fixture", binding=candidate["id"]),
    )
    times = iter(["2026-11-04T04:00:03+00:00", "2026-11-04T04:00:04+00:00"])
    seen.clear()
    monkeypatch.setattr(impl, "now", clock)

    class Verifier(verification.FixtureVerifier):
        def verify_sample(self, candidate):
            seen.append("verify")
            return super().verify_sample(candidate)

    assert impl.process_verification(store, Verifier())
    assert seen == ["clock", "verify", "clock"]
    result = client.get(path).json()["receipts"][0]["candidate"]["verification"]
    assert (
        result["started_at"] == "2026-11-04T04:00:03+00:00"
        and result["finished_at"] == "2026-11-04T04:00:04+00:00"
    )


def test_existing_verification_crash_hook_rolls_back_sample_result(client, headers, store):
    _, _, path, _, _, vpath, body = sample(client, headers, store)
    client.post(vpath, headers=headers, json=body)
    with pytest.raises(RuntimeError):
        verification.process_one(store, crash=True)
    result = client.get(path).json()["receipts"][0]["candidate"]["verification"]
    assert result["status"] == "pending" and result["verification"] == "unknown"
    assert result["started_at"] is None
    assert verification.process_one(store)
    assert (
        client.get(path).json()["receipts"][0]["candidate"]["verification"]["verification"]
        == "passed"
    )


def test_concurrent_sample_checks_share_one_request_and_result(client, headers, store):
    identity, task, path, _, receipt, _, body = sample(client, headers, store)
    candidate_id = receipt["candidate"]["id"]
    parsed = impl.CandidateVerificationRequest.model_validate(body)
    with ThreadPoolExecutor(max_workers=2) as pool:
        saved = list(
            pool.map(
                lambda _: impl.submit_verification(
                    store, identity, task["id"], candidate_id, parsed
                ),
                range(2),
            )
        )
    assert saved[0] == saved[1]
    with ThreadPoolExecutor(max_workers=2) as pool:
        worked = list(pool.map(lambda _: impl.process_verification(store), range(2)))
    assert sorted(worked) == [False, True]
    assert (
        client.get(path).json()["receipts"][0]["candidate"]["verification"]["verification"]
        == "passed"
    )


def test_artifact_crash_recovery_rechecks_withdrawn_approval(client, headers, store):
    _, _, path, body = ready(client, headers, store)
    client.post(path, headers=headers, json=body)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from workbench.implementation import process_one;from workbench.db import engine_for;process_one(engine_for(),crash='after-artifact')",
        ],
        env={
            **os.environ,
            "PYTHONPATH": str(ROOT / "backend"),
            "WORKBENCH_DB": str(store.url.database),
        },
        timeout=10,
    )
    assert result.returncode == 91
    with write_transaction(store) as c:
        c.execute(proposal_decisions.update().values(scope_review_required=1))
    assert real_worker(store).returncode == 0
    receipt = client.get(path).json()["receipts"][0]
    assert (
        receipt["status"] == "blocked"
        and receipt["attempt"] is None
        and receipt["candidate"] is None
    )


def test_changed_saved_request_payload_never_generates(client, headers, store):
    _, _, path, body = ready(client, headers, store)
    client.post(path, headers=headers, json=body)
    with write_transaction(store) as c:
        c.execute(impl.records.update().values(payload={**body, "mode": "real"}))
    assert impl.process_one(store)
    receipt = client.get(path).json()["receipts"][0]
    assert (
        receipt["status"] == "stale" and receipt["attempt"] is None and receipt["candidate"] is None
    )
