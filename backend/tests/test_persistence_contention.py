"""Real rollback-journal contention between disposable application processes."""
import multiprocessing
from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
import time
from threading import Thread

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError

from workbench import real_execution, task_execution
from workbench.db import engine_for, write_transaction
from test_task_execution import linked  # noqa: F401


def status_reader(database, ref, ready, release, report):
    engine = engine_for(database)

    @event.listens_for(engine, "rollback")
    def pause_snapshot(connection):
        # This is the actual execution status transaction, after its final
        # SELECT and before its read lock is released. No provider is involved.
        report.send(dict(pid=os.getpid(), connection=id(connection),
                         journal=connection.connection.driver_connection.execute(
                             "PRAGMA journal_mode").fetchone()[0]))
        ready.set()
        assert release.wait(5)

    try:
        task_execution.context(engine, ref["intake_id"], ref["task_id"])
    finally:
        engine.dispose()
        report.close()


@pytest.mark.parametrize(("release_reader", "legacy_commit"),
                         [(True, False), (False, False), (True, True)])
def test_actual_dispatch_commit_with_overlapping_status_process(
    store, linked, release_reader, legacy_commit, monkeypatch,  # noqa: F811
):
    ref, runtime, grant = linked
    if legacy_commit:
        @contextmanager
        def prior_transaction(engine):
            # f507278 used SQLAlchemy commit directly after BEGIN IMMEDIATE.
            # With the same process overlap this lost the uncommitted request.
            with engine.connect() as connection:
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                try:
                    yield connection
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
        monkeypatch.setattr(real_execution, "write_transaction", prior_transaction)
    process = multiprocessing.get_context("spawn")
    ready, release = process.Event(), process.Event()
    parent, child = process.Pipe(duplex=False)
    reader = process.Process(target=status_reader,
                             args=(store.url.database, ref, ready, release, child))
    reader.start()
    child.close()
    thread = None
    inserts = []

    @event.listens_for(store, "after_cursor_execute")
    def record_insert(connection, cursor, statement, parameters, context, many):
        if statement.startswith("INSERT INTO real_execution_requests"):
            inserts.append(True)

    try:
        assert ready.wait(5)
        actor = parent.recv()
        assert actor["pid"] == reader.pid != os.getpid() and actor["journal"] == "delete"
        # Verify the contending process against the kernel's actual file lock.
        info = Path(store.url.database).stat()
        lock = f"{os.major(info.st_dev):02x}:{os.minor(info.st_dev):02x}:{info.st_ino}"
        assert any(f"READ {reader.pid} {lock}" in row
                   for row in Path("/proc/locks").read_text().splitlines())
        succeeds = release_reader and not legacy_commit
        if release_reader:
            thread = Thread(target=lambda: (time.sleep(.35), release.set()))
            thread.start()
        started = time.monotonic()
        command = task_execution.request_id(grant["id"])
        if succeeds:
            result = real_execution.submit(store, grant["id"], command)
            assert result["id"] == command
        else:
            with pytest.raises(OperationalError):
                real_execution.submit(store, grant["id"], command)
        assert .15 <= time.monotonic() - started < 2
        assert len(inserts) == 1  # Retried COMMIT never replayed the insert.
    finally:
        release.set()
        if thread:
            thread.join(2)
        reader.join(5)
        if reader.is_alive():
            reader.terminate()
            reader.join(2)
        parent.close()
        event.remove(store, "after_cursor_execute", record_insert)
    assert reader.exitcode == 0
    with store.connect() as connection:
        rows = connection.execute(select(real_execution.requests).where(
            real_execution.requests.c.grant_id == grant["id"])).mappings().all()
    assert len(rows) == int(succeeds)
    # Reusing an uncommitted local command after contention is safe. This calls
    # no provider and does not create a second request when the first committed.
    saved = real_execution.submit(store, grant["id"], command)
    assert saved["id"] == command
    assert runtime.calls == 0


def test_delegate_never_writes_bytecode(monkeypatch):
    from workbench.task_session import delegated_command
    monkeypatch.delenv("WORKBENCH_PLANNER", raising=False)
    command = delegated_command(["--user"], ["/test/python", "/test/task.py"])
    assert "PYTHONDONTWRITEBYTECODE=1" in command
    assert command[-2:] == ["/test/python", "/test/task.py"]


def test_nonbusy_statement_failure_never_replays_or_commits(tmp_path):
    database = tmp_path / "fixture.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE example (value TEXT UNIQUE)")
    engine = engine_for(database)
    try:
        with pytest.raises(ValueError, match="controlled failure"):
            with write_transaction(engine) as connection:
                connection.exec_driver_sql("INSERT INTO example VALUES ('one')")
                raise ValueError("controlled failure")
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT count(*) FROM example").scalar_one() == 0
    finally:
        engine.dispose()


@pytest.mark.parametrize("after_external", [False, True])
def test_codex_controller_waits_for_database_without_replaying_provider(
    store, monkeypatch, after_external,
):
    from threading import Event
    from codex_task import process_with_contention
    from test_real_execution import granted

    runtime, grant, request = granted(store)
    runtime.deadline = time.monotonic() + 10
    original = real_execution.process_one
    iterations = []

    def contention(*args, **kwargs):
        iterations.append(True)
        if len(iterations) == 1:
            if after_external:
                assert original(*args, **kwargs)
            error = sqlite3.OperationalError("controlled busy")
            error.sqlite_errorcode = sqlite3.SQLITE_BUSY
            raise OperationalError("COMMIT", {}, error)
        return original(*args, **kwargs)

    monkeypatch.setattr(real_execution, "process_one", contention)
    process_with_contention(store, runtime, Event(), busy_seconds=1)
    assert len(iterations) == 2 and runtime.calls == 1
    with store.connect() as connection:
        rows = connection.execute(select(real_execution.requests).where(
            real_execution.requests.c.grant_id == grant["id"])).mappings().all()
        assert len(rows) == 1 and rows[0]["id"] == request["id"] and rows[0]["status"] == "checked"


@pytest.mark.parametrize("boundary", ["before_claim", "after_fence", "after_external"])
def test_process_loss_preserves_pending_work_and_holds_uncertain_execution(
    store, tmp_path, boundary,
):
    from test_real_execution import granted
    runtime, grant, request = granted(store)
    ctx = multiprocessing.get_context("fork")
    paused = ctx.Event()
    marker = tmp_path / "scripted-external-submissions.jsonl"

    def run():
        store.dispose()
        execute = runtime.execute

        def pause():
            paused.set()
            time.sleep(10)

        def external(*args):
            # A separate exclusive connection proves no application transaction
            # spans this stand-in for the irreversible provider operation.
            with sqlite3.connect(store.url.database, timeout=.15) as connection:
                connection.execute("BEGIN EXCLUSIVE")
                connection.rollback()
            if boundary == "after_fence":
                pause()
            with marker.open("a") as stream:
                stream.write(request["id"]+"\n")
                stream.flush()
                os.fsync(stream.fileno())
            if boundary == "after_external":
                pause()
            return execute(*args)

        runtime.execute = external
        if boundary == "before_claim":
            pause()
        real_execution.process_one(store, runtime)

    store.dispose()
    child = ctx.Process(target=run)
    child.start()
    try:
        assert paused.wait(5)
    finally:
        child.terminate()
        child.join(5)
    assert not child.is_alive()
    if boundary == "before_claim":
        assert real_execution.process_one(store, runtime)
        assert runtime.calls == 1
        expected = "checked"
    else:
        assert real_execution.recover(store, runtime)
        assert not real_execution.process_one(store, runtime)
        assert runtime.calls == 0
        expected = "interrupted"
    with store.connect() as connection:
        row = connection.execute(select(real_execution.requests).where(
            real_execution.requests.c.id == request["id"])).mappings().one()
        assert row["status"] == expected
    assert (marker.read_text().splitlines() if marker.exists() else []) == (
        [request["id"]] if boundary == "after_external" else [])
