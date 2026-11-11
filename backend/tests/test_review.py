import concurrent.futures
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from threading import Event
import time
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import event, func, select
from sqlalchemy.exc import OperationalError
from workbench.db import applications, commands, tasks, write_transaction
from workbench.schemas import Decision
from workbench.service import submit
from workbench.worker import process_one

TASK = "cozy-step-review"
URL = f"/api/tasks/{TASK}/decisions"
ROOT = Path(__file__).resolve().parents[2]


def decision(client, choice="confirm"):
    task = client.get(f"/api/tasks/{TASK}").json()
    return dict(command_id=str(uuid4()), version=task["version"], notes="",
                choices={claim["id"]: choice for claim in task["evidence"]["claims"]})


def count(store, table):
    with store.connect() as connection:
        return connection.scalar(select(func.count()).select_from(table))


def test_durable_save_is_not_applied(client, headers, store):
    body = decision(client)
    response = client.post(URL, headers=headers, json=body)
    assert response.status_code == 200
    receipt = response.json()
    assert receipt["status"] == "pending"
    assert client.get(f"/api/commands/{body['command_id']}").json() == receipt
    task = client.get(f"/api/tasks/{TASK}").json()
    assert (task["state"], task["attempt_outcome"], task["verification"], task["publication"]) == (
        "held", "accepted", "passed", "not_published")
    assert not task["review_available"]
    assert count(store, applications) == 0


def test_response_loss_retry_before_and_after_application(client, headers, store):
    body = decision(client)
    # Deliberately discard the committed HTTP response.
    client.post(URL, headers=headers, json=body)
    assert client.post(URL, headers=headers, json=body).json()["status"] == "pending"
    assert process_one(store)
    assert not process_one(store)
    assert client.post(URL, headers=headers, json=body).json()["status"] == "applied"
    assert count(store, commands) == count(store, applications) == 1
    task = client.get(f"/api/tasks/{TASK}").json()
    assert task["state"] == "review_confirmed"
    assert task["eligibility"] == "needs_publication_checks"
    assert task["revision"] == 2
    assert task["publication"] == "not_published"


def test_changes_remain_held(client, headers, store):
    body = decision(client, "change")
    body["notes"] = "Keep the player's picked step until it stops applying."
    client.post(URL, headers=headers, json=body)
    process_one(store)
    task = client.get(f"/api/tasks/{TASK}").json()
    assert task["state"] == "changes_requested"
    assert task["eligibility"] == "contract_hold"
    assert task["latest_command"]["payload"]["notes"] == body["notes"]


def test_changed_content_under_same_id_is_rejected(client, headers, store):
    body = decision(client)
    client.post(URL, headers=headers, json=body)
    body["notes"] = "Different content"
    assert client.post(URL, headers=headers, json=body).status_code == 409
    assert count(store, commands) == 1


def test_different_id_same_review_conflicts(client, headers, store):
    body = decision(client)
    client.post(URL, headers=headers, json=body)
    body["command_id"] = str(uuid4())
    assert client.post(URL, headers=headers, json=body).status_code == 409
    assert count(store, commands) == 1


def change_evidence(store):
    with write_transaction(store) as connection:
        evidence = connection.execute(select(tasks.c.evidence)).scalar_one()
        evidence["claims"][0]["text"] += " Updated contract."
        connection.execute(tasks.update().values(evidence=evidence))


def test_stale_before_submission_rejects_without_save(client, headers, store):
    body = decision(client)
    change_evidence(store)
    assert client.post(URL, headers=headers, json=body).status_code == 409
    assert count(store, commands) == 0


def test_stale_after_save_retains_decision_without_effect(client, headers, store):
    body = decision(client)
    client.post(URL, headers=headers, json=body)
    change_evidence(store)
    assert process_one(store)
    receipt = client.get(f"/api/commands/{body['command_id']}").json()
    assert receipt["status"] == "stale"
    assert receipt["payload"] == body
    assert "Updated contract" not in receipt["reviewed_evidence"]["claims"][0]["text"]
    assert receipt["reviewed_revision"] == 1
    assert count(store, applications) == 0
    assert client.get(f"/api/tasks/{TASK}").json()["review_available"]
    fresh = decision(client)
    assert client.post(URL, headers=headers, json=fresh).status_code == 200
    assert process_one(store)
    assert count(store, applications) == 1


def test_contention_is_bounded_and_retry_keeps_same_id(client, headers, store):
    body = decision(client)
    with write_transaction(store):
        started = time.monotonic()
        response = client.post(URL, headers=headers, json=body)
        elapsed = time.monotonic() - started
        assert response.status_code == 503
        assert response.headers["retry-after"] == "1"
        assert elapsed < 2
    assert client.post(URL, headers=headers, json=body).status_code == 200
    with write_transaction(store):
        response = client.post(URL, headers=headers, json=body)
        assert response.status_code == 503
    assert client.post(URL, headers=headers, json=body).json()["status"] == "pending"
    assert count(store, commands) == 1


def test_short_contention_can_clear_within_timeout(client, headers, store):
    body = decision(client)
    with concurrent.futures.ThreadPoolExecutor() as pool:
        with write_transaction(store):
            future = pool.submit(client.post, URL, headers=headers, json=body)
            time.sleep(.04)
        assert future.result().status_code == 200
    assert count(store, commands) == 1


def test_concurrent_duplicate_submission_and_workers(client, store):
    body = Decision(**decision(client))
    busy_observed = Event()

    def retry_busy(operation):
        deadline = time.monotonic() + 8
        while True:
            try:
                return operation()
            except OperationalError as error:
                if (getattr(error.orig, "sqlite_errorcode", 0) & 0xFF not in {5, 6}
                        or time.monotonic() >= deadline):
                    raise
                busy_observed.set()
                time.sleep(.02)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: retry_busy(lambda: submit(store, TASK, body)), range(2)))
    assert results[0]["id"] == results[1]["id"]
    busy_observed.clear()
    lock_held = Event()

    def hold_first_writer(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("SELECT commands") and not lock_held.is_set():
            lock_held.set()
            # Exercise the same bounded busy retry used by the continuous worker.
            assert busy_observed.wait(5)

    event.listen(store, "before_cursor_execute", hold_first_writer)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(retry_busy, lambda: process_one(store))
            assert lock_held.wait(5)
            second = pool.submit(retry_busy, lambda: process_one(store))
            assert sum((first.result(timeout=10), second.result(timeout=10))) == 1
    finally:
        event.remove(store, "before_cursor_execute", hold_first_writer)
    assert busy_observed.is_set()
    assert count(store, commands) == count(store, applications) == 1
    assert client.get(f"/api/tasks/{TASK}").json()["revision"] == 2


def worker_process(store, *arguments):
    return subprocess.run([sys.executable, "-m", "workbench.worker", "--once", *arguments],
                          cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT / "backend"),
                                         "WORKBENCH_DB": str(store.url.database)},
                          capture_output=True, timeout=10)


def test_worker_exits_busy_and_recovers_later(client, headers, store):
    body = decision(client)
    client.post(URL, headers=headers, json=body)
    with write_transaction(store):
        result = worker_process(store)
        assert result.returncode == 0
        assert b"busy" in result.stderr
    assert count(store, applications) == 0
    assert worker_process(store).returncode == 0
    assert count(store, applications) == 1


def test_real_worker_crash_before_and_after_commit(client, headers, store):
    body = decision(client)
    client.post(URL, headers=headers, json=body)
    assert worker_process(store, "--test-crash", "before-commit").returncode == 91
    assert count(store, applications) == 0
    assert client.get(f"/api/commands/{body['command_id']}").json()["status"] == "pending"
    assert worker_process(store, "--test-crash", "after-commit").returncode == 92
    assert worker_process(store).returncode == 0
    assert count(store, applications) == 1
    task = client.get(f"/api/tasks/{TASK}").json()
    assert task["revision"] == 2
    assert task["latest_command"]["status"] == "applied"


def test_migration_repeat_backup_and_restore(client, headers, store, tmp_path):
    client.post(URL, headers=headers, json=decision(client))
    config = Config(str(ROOT / "alembic.ini"))
    with store.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        command.upgrade(config, "head")
        connection.commit()
    restored = tmp_path / "restored.sqlite3"
    with sqlite3.connect(store.url.database) as source, sqlite3.connect(restored) as backup:
        source.backup(backup)
    with sqlite3.connect(restored) as backup:
        assert backup.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert backup.execute("PRAGMA foreign_key_check").fetchall() == []
        assert backup.execute("SELECT status FROM commands").fetchone() == ("pending",)
    assert count(store, commands) == 1


def test_write_rollback_and_foreign_key_enforcement(store):
    import pytest
    from sqlalchemy.exc import IntegrityError
    with pytest.raises(RuntimeError):
        with write_transaction(store) as connection:
            connection.execute(tasks.update().values(revision=500))
            raise RuntimeError("simulated interruption")
    with store.connect() as connection:
        assert connection.scalar(select(tasks.c.revision)) == 1
    with pytest.raises(IntegrityError):
        with write_transaction(store) as connection:
            connection.execute(applications.insert().values(
                command_id="absent", task_id=TASK, previous_version="v1", result_version="v2", applied_at="now"))
