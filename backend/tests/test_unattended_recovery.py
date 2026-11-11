"""Controlled worker/recovery sessions. No model, real addon checks or activation."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json
import os
import sqlite3
import subprocess
import sys
import threading
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from workbench import real_execution as job, task_execution
from workbench.db import model_attempts
from workbench.real_provider import BudgetError
from workbench.real_workspace import Runtime
from test_real_handoff import HandoffRuntime
from test_task_execution import linked as linked, request, mutate, context


def busy():
    error = sqlite3.OperationalError("NON-MODEL controlled store outage")
    error.sqlite_errorcode = sqlite3.SQLITE_BUSY
    return OperationalError("NON-MODEL delivery", {}, error)


def failing(runtime, usage=None):
    def execute(*args):
        runtime.calls += 1
        raise BudgetError("NON-MODEL attempt budget exhausted")

    runtime.execute = execute
    runtime.failure_evidence = lambda *a: dict(
        inference_ran=False, usage=usage or dict(status="unavailable", values=None)
    )


def ledger(store, identity):
    with store.connect() as c:
        return dict(
            c.execute(select(model_attempts).where(model_attempts.c.execution_id == identity))
            .mappings()
            .one()
        )


@pytest.mark.parametrize(
    "usage",
    [
        dict(status="complete", values=dict(prompt_tokens=20, completion_tokens=17)),
        dict(status="partial", known_values=dict(completion_tokens=17), values=None),
        dict(status="unavailable", values=None),
    ],
)
def test_repeated_store_outage_retains_failure_and_usage_without_reexecution(
    store, linked, monkeypatch, usage
):
    _, runtime, grant = linked
    failing(runtime, usage)
    original = job.write_transaction
    save = runtime.record_outcome
    armed = False

    def record(*args):
        nonlocal armed
        save(*args)
        armed = True

    runtime.record_outcome = record

    @contextmanager
    def unavailable(engine):
        if armed:
            raise busy()
        with original(engine) as c:
            yield c

    monkeypatch.setattr(job, "write_transaction", unavailable)
    with pytest.raises(OperationalError):
        job.process_one(store, runtime)
    row = request(store, grant)
    original_bytes = (runtime.root / ("outcome-" + row["id"] + ".json")).read_bytes()
    for _ in range(2):
        with pytest.raises(OperationalError):
            job.process_one(store, runtime)
    assert runtime.calls == 1 and row["status"] == "running"
    monkeypatch.setattr(job, "write_transaction", original)
    assert not job.process_one(store, runtime)
    after = request(store, grant)
    assert after["status"] == "failed" and after["result"]["usage"] == usage
    assert ledger(store, row["id"])["usage"] == usage
    assert ledger(store, row["id"])["outcome"] == "failed"
    assert (
        runtime.calls == 1
        and original_bytes == (runtime.root / ("outcome-" + row["id"] + ".json")).read_bytes()
    )
    assert not job.process_one(store, runtime)


@pytest.mark.parametrize("safe", [False, "exception"])
def test_uncertain_cleanup_holds_lane_then_delivers_failure_once(client, store, linked, safe):
    task, runtime, grant = linked
    failing(runtime)
    calls = []

    def cleanup(row):
        calls.append(row["id"])
        if safe == "exception":
            raise OSError("NON-MODEL unavailable cleanup")
        return False

    runtime.cleanup = cleanup
    assert job.process_one(store, runtime)
    row = request(store, grant)
    assert row["status"] == "running" and row["result"]["handler_returned"]
    assert context(client, task)["stage"] == "recovering"
    assert job.review(store, row["id"])["recovery_pending"]
    assert job.listing(store, group="active")["items"][0]["recovery_pending"]
    assert "confirmed cleanup" in row["message"] and not job.process_one(store, runtime)
    assert runtime.calls == 1 and len(calls) >= 2
    runtime.cleanup = lambda row: True
    assert not job.process_one(store, runtime)
    assert request(store, grant)["status"] == "failed" and runtime.calls == 1
    assert not job.review(store, row["id"])["recovery_pending"]


def test_cleanup_hold_prevents_other_eligible_request_from_running(store, linked):
    _, runtime, grant = linked
    failing(runtime)
    runtime.cleanup = lambda row: False
    assert job.process_one(store, runtime)
    next_grant = job.issue(
        store, "NON-MODEL second explicitly scoped permission", "pytest", runtime
    )
    next_request = job.submit(store, next_grant["id"], str(uuid4()))
    assert not job.process_one(store, runtime)
    assert job.review(store, next_request["id"])["status"] == "pending"
    assert runtime.calls == 1
    runtime.cleanup = lambda row: True
    # This separate controlled task retains its own valid permission, no hidden retry.
    assert job.process_one(store, runtime)
    assert request(store, grant)["status"] == "failed"
    assert job.review(store, next_request["id"])["status"] == "failed" and runtime.calls == 2
    assert not job.process_one(store, runtime)


def test_wrong_output_root_cannot_cleanup_recover_or_block_pending_work(
    store, linked, tmp_path, monkeypatch
):
    _, runtime, grant = linked

    def crash(*args):
        runtime.calls += 1
        raise SystemExit("NON-MODEL process interruption")

    runtime.execute = crash
    with pytest.raises(SystemExit):
        job.process_one(store, runtime)
    other_root = tmp_path / "OTHER_NON_MODEL"
    other_root.mkdir()
    other = HandoffRuntime(other_root)
    other.cleanup = lambda row: pytest.fail("Wrong directory cannot clean another run")
    other.preflight = lambda: pytest.fail("Wrong directory cannot inspect activation")
    monkeypatch.setattr(job, "alive", lambda owner: False)
    assert not job.process_one(store, other)
    assert request(store, grant)["status"] == "running"
    assert job.recover(store, runtime)
    assert request(store, grant)["status"] == "interrupted"
    new = job.issue(store, "NON-MODEL pending exact root", "pytest", runtime)
    receipt = job.submit(store, new["id"], str(uuid4()))
    assert not job.process_one(store, other)
    assert job.review(store, receipt["id"])["status"] == "pending"


def test_recovery_only_configuration_never_dispatches_preflights_or_executes(
    store, linked, monkeypatch
):
    _, runtime, grant = linked
    failing(runtime)
    record = runtime.record_outcome

    def crash(*args):
        record(*args)
        raise SystemExit("NON-MODEL exit after failure handoff")

    runtime.record_outcome = crash
    with pytest.raises(SystemExit):
        job.process_one(store, runtime)
    next_grant = job.issue(store, "NON-MODEL eligible but execution disabled", "pytest", runtime)
    next_request = job.submit(store, next_grant["id"], str(uuid4()))
    runtime.preflight = lambda: pytest.fail("Recovery cannot observe activation")
    runtime.execute = lambda *a: pytest.fail("Recovery cannot invoke a model")
    monkeypatch.delenv("WORKBENCH_REAL_EXECUTION_ROOT", raising=False)
    monkeypatch.setenv("WORKBENCH_REAL_RECOVERY_ROOT", str(runtime.root))
    monkeypatch.setattr("workbench.real_workspace.Runtime", lambda *a, **k: runtime)
    assert job.process_one(store)
    assert request(store, grant)["status"] == "failed"
    assert job.review(store, next_request["id"])["status"] == "pending"
    assert not job.process_one(store)
    assert runtime.calls == 1


@pytest.mark.parametrize("change", ["expired", "withdrawn"])
def test_invalid_permission_stops_before_runtime_observation(store, linked, change):
    _, runtime, grant = linked
    assert task_execution.dispatch_one(store)
    values = (
        dict(expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())
        if change == "expired"
        else dict(revoked_at=job.now())
    )
    mutate(store, job.grants, grant["id"], **values)
    runtime.preflight = lambda: pytest.fail("No observation after permission ends")
    runtime.execute = lambda *a: pytest.fail("No inference after permission ends")
    assert job.process_one(store, runtime)
    row = request(store, grant)
    assert row["status"] == "blocked" and row["result"]["inference_ran"] is False
    with store.connect() as c:
        assert not c.execute(
            select(model_attempts).where(model_attempts.c.execution_id == row["id"])
        ).all()


def test_worker_stop_prevents_dispatch_and_permission_callbacks_stop_active_work(store, linked):
    _, runtime, grant = linked
    stop = threading.Event()
    stop.set()
    assert not job.process_one(store, runtime, stop)
    with store.connect() as c:
        assert not c.execute(select(job.requests)).all()
    stop.clear()

    def execute(identity, fence, permitted):
        assert permitted()
        stop.set()
        assert not permitted()
        raise BudgetError("NON-MODEL stopped within current attempt")

    runtime.execute = execute
    assert job.process_one(store, runtime, stop)
    assert request(store, grant)["status"] == "failed"


def test_transient_readiness_exception_reconsiders_same_request_without_attempt(store, linked):
    _, runtime, grant = linked
    original = runtime.preflight

    def unavailable():
        raise OSError("NON-MODEL unavailable local observation")

    runtime.preflight = unavailable
    assert not job.process_one(store, runtime)
    row = request(store, grant)
    assert row["status"] == "pending" and row["result"]["inference_ran"] is False
    assert runtime.calls == 0
    runtime.preflight = original
    assert job.process_one(store, runtime)
    assert request(store, grant)["id"] == row["id"] and runtime.calls == 1


@pytest.mark.parametrize(
    "change",
    [
        "fence",
        "owner",
        "binding",
        "result",
        "success",
        "symlink",
        "oversized",
        "timestamp",
        "malformed",
    ],
)
def test_bad_failure_seal_never_promotes_success_or_replays(store, linked, monkeypatch, change):
    _, runtime, grant = linked
    failing(runtime)
    record = runtime.record_outcome

    def crash(*args):
        record(*args)
        raise SystemExit("NON-MODEL interruption")

    runtime.record_outcome = crash
    with pytest.raises(SystemExit):
        job.process_one(store, runtime)
    row = request(store, grant)
    path = runtime.root / ("outcome-" + row["id"] + ".json")
    seal = json.loads(path.read_text())
    if change == "fence":
        seal["fence"] = str(uuid4())
    if change == "owner":
        seal["owner"]["pid"] += 1
    if change == "binding":
        seal["grant_binding"] = "wrong"
    if change == "result":
        seal["result"]["usage"] = dict(status="complete", values=dict(completion_tokens=99))
    if change == "success":
        seal["status"] = "checked"
        seal["result"]["verification"] = "passed"
        seal["result_digest"] = job.digest(seal["result"])
    if change == "timestamp":
        seal["completed_at"] = "2099-01-01T00:00:00+00:00"
    path.write_text(json.dumps(seal))
    if change == "malformed":
        path.write_text("[]")
    if change == "symlink":
        outside = runtime.root.parent / "outside-failure.json"
        path.rename(outside)
        path.symlink_to(outside)
    if change == "oversized":
        path.write_text("x" * ((4 << 20) + 1))
    monkeypatch.setattr(job, "alive", lambda _: False)
    assert job.recover(store, runtime)
    assert request(store, grant)["status"] == "interrupted"
    assert runtime.calls == 1 and not job.process_one(store, runtime)


def test_cancellation_after_failure_handoff_wins_and_retains_usage(store, linked):
    _, runtime, grant = linked
    usage = dict(status="complete", values=dict(completion_tokens=7, prompt_tokens=9))
    failing(runtime, usage)
    record = runtime.record_outcome

    def cancel(*args):
        record(*args)
        job.cancel(store, args[1], str(uuid4()))

    runtime.record_outcome = cancel
    assert job.process_one(store, runtime)
    row = request(store, grant)
    assert row["status"] == "cancelled" and row["result"]["usage"] == usage
    assert not job.recover(store, runtime)
    assert ledger(store, row["id"])["outcome"] == "cancelled" and runtime.calls == 1


def test_failure_after_expiry_is_delivered_without_renewing_permission(store, linked):
    _, runtime, grant = linked

    def execute(identity, fence, permitted):
        runtime.calls += 1
        mutate(store, job.grants, grant["id"], revoked_at=job.now())
        assert not permitted()
        raise BudgetError("NON-MODEL permission ended")

    runtime.execute = execute
    assert job.process_one(store, runtime)
    row = request(store, grant)
    assert row["status"] == "failed" and not job.review(store, row["id"])["current_grant"]
    assert (runtime.root / ("outcome-" + row["id"] + ".json")).exists()
    assert not job.process_one(store, runtime) and runtime.calls == 1


def test_process_exit_after_failure_handoff_recovers_once_in_new_worker(store, linked, monkeypatch):
    _, runtime, grant = linked
    assert task_execution.dispatch_one(store)
    script = """
import os,sys
from pathlib import Path
from test_real_handoff import HandoffRuntime
from workbench import real_execution as job
from workbench.db import engine_for
r=HandoffRuntime(Path(sys.argv[2]))
def execute(*args): raise RuntimeError('NON-MODEL completed-response failure')
r.execute=execute
r.failure_evidence=lambda *a: dict(inference_ran=False,usage=dict(status='partial',known_values=dict(completion_tokens=13),values=None))
record=r.record_outcome
def crash(*args):
 record(*args)
 os._exit(93)
r.record_outcome=crash
job.process_one(engine_for(sys.argv[1]),r)
"""
    env = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).resolve().parent)
        + os.pathsep
        + str(Path(__file__).resolve().parents[1]),
    }
    child = subprocess.run(
        [sys.executable, "-c", script, str(store.url.database), str(runtime.root)],
        env=env,
        timeout=20,
        capture_output=True,
    )
    assert child.returncode == 93, child.stderr.decode()
    row = request(store, grant)
    assert row["status"] == "running"
    runtime.execute = lambda *a: pytest.fail("No replay after actual process exit")
    assert job.recover(store, runtime)
    assert not job.recover(store, runtime)
    row = request(store, grant)
    assert (
        row["status"] == "failed"
        and row["result"]["usage"]["known_values"]["completion_tokens"] == 13
    )
    assert ledger(store, row["id"])["outcome"] == "failed"


def test_concurrent_recovery_delivers_one_atomic_outcome_and_ledger(store, linked, monkeypatch):
    _, runtime, grant = linked
    failing(runtime)
    record = runtime.record_outcome

    def crash(*args):
        record(*args)
        raise SystemExit("NON-MODEL after seal")

    runtime.record_outcome = crash
    with pytest.raises(SystemExit):
        job.process_one(store, runtime)
    monkeypatch.setattr(job, "alive", lambda _: False)

    def reconcile(_):
        try:
            return job.recover(store, runtime)
        except OperationalError:
            return False

    with ThreadPoolExecutor(3) as pool:
        results = list(pool.map(reconcile, range(3)))
    assert results.count(True) == 1 and request(store, grant)["status"] == "failed"
    assert ledger(store, request(store, grant)["id"])["outcome"] == "failed" and runtime.calls == 1


def test_failure_artifact_refusal_keeps_known_usage_without_replay(store, linked):
    _, runtime, grant = linked
    usage = dict(status="partial", known_values=dict(completion_tokens=12), values=None)
    failing(runtime, usage)
    runtime.enforce_storage = lambda: (_ for _ in ()).throw(ValueError("NON-MODEL storage ceiling"))
    assert job.process_one(store, runtime)
    row = request(store, grant)
    assert (
        row["status"] == "failed"
        and row["result"]["usage"] == usage
        and row["result"]["delivery_error"]
    )
    assert not job.process_one(store, runtime) and runtime.calls == 1


def test_legacy_recovery_requires_exact_workspace_not_missing_marker(tmp_path):
    root = tmp_path / "owned"
    root.mkdir()
    runtime = Runtime(root)
    grant = dict(snapshot=dict(runtime=dict(workspace=str(runtime.workspace))))
    assert runtime.owns_recovery(grant)
    grant["snapshot"]["runtime"]["workspace"] = "/wrong/workspace"
    assert not runtime.owns_recovery(grant)
    assert not runtime.owns_recovery(dict(snapshot=dict(runtime={})))


def test_cancellation_recovery_keeps_sealed_partial_usage(store, linked, monkeypatch):
    _, runtime, grant = linked
    usage = dict(
        status="partial",
        known_values=dict(completion_tokens=17),
        values=None,
        provenance=dict(method="NON-MODEL completed response"),
    )
    failing(runtime, usage)
    record = runtime.record_outcome

    def crash(*args):
        record(*args)
        job.cancel(store, args[1], str(uuid4()))
        raise SystemExit("NON-MODEL interruption before cancellation delivery")

    runtime.record_outcome = crash
    with pytest.raises(SystemExit):
        job.process_one(store, runtime)
    runtime.failure_evidence = lambda *a: dict(usage=dict(status="unavailable", values=None))
    monkeypatch.setattr(job, "alive", lambda _: False)
    assert job.recover(store, runtime)
    row = request(store, grant)
    assert row["status"] == "cancelled" and row["result"]["usage"] == usage
    assert ledger(store, row["id"])["usage"] == usage


def test_completion_seal_failure_retains_returned_usage(store, linked):
    _, runtime, grant = linked
    original = runtime.execute
    usage = dict(
        status="complete",
        values=dict(completion_tokens=23, prompt_tokens=42),
        provenance=dict(method="NON-MODEL completed response"),
    )

    def execute(*args):
        result = original(*args)
        result["usage"] = usage
        # Keep the controlled frozen result consistent; force only seal storage failure.
        folder = runtime.root / "candidates" / result["candidate_id"]
        (folder / "result.json").write_text(json.dumps(result, sort_keys=True))
        return result

    runtime.execute = execute
    runtime.record_completion = lambda *a: (_ for _ in ()).throw(
        OSError("NON-MODEL temporary seal failure")
    )
    assert job.process_one(store, runtime)
    row = request(store, grant)
    assert row["status"] == "failed" and row["result"]["usage"] == usage
    assert ledger(store, row["id"])["usage"] == usage and runtime.calls == 1


def test_repeated_success_delivery_outage_preserves_original_seal_and_candidate(
    store, linked, monkeypatch
):
    _, runtime, grant = linked
    original = job.write_transaction
    record = runtime.record_completion
    armed = False

    def seal(*args):
        nonlocal armed
        record(*args)
        armed = True

    @contextmanager
    def unavailable(engine):
        if armed:
            raise busy()
        with original(engine) as c:
            yield c

    runtime.record_completion = seal
    monkeypatch.setattr(job, "write_transaction", unavailable)
    with pytest.raises(OperationalError):
        job.process_one(store, runtime)
    row = request(store, grant)
    seal_path = runtime.root / ("completion-" + row["id"] + ".json")
    before = seal_path.read_bytes()
    with pytest.raises(OperationalError):
        job.process_one(store, runtime)
    assert runtime.calls == 1
    monkeypatch.setattr(job, "write_transaction", original)
    assert not job.process_one(store, runtime)
    assert request(store, grant)["status"] == "checked" and seal_path.read_bytes() == before
    assert ledger(store, row["id"])["outcome"] == "checked" and runtime.calls == 1


def test_replaced_directory_cannot_inherit_old_recovery_owner(store, linked):
    _, runtime, grant = linked
    old = runtime.root
    old.rename(old.with_name("NON_MODEL_original_preserved"))
    old.mkdir()
    replacement = HandoffRuntime(old)
    assert not replacement.owns_recovery(grant)


def test_continuous_existing_worker_recovers_busy_store_without_human_handoff(store, linked):
    _, runtime, grant = linked
    script = """
import os,sys,sqlite3,signal,threading,time
from pathlib import Path
from sqlalchemy import event,select
from test_real_handoff import HandoffRuntime
from workbench import real_execution as job,real_workspace,worker
from workbench.db import engine_for
r=HandoffRuntime(Path(sys.argv[2]));calls=[];outages=[];grant_id=sys.argv[3]
def execute(*args):
 calls.append('NON-MODEL')
 raise RuntimeError('NON-MODEL completed-response failure')
r.execute=execute
r.failure_evidence=lambda *a:dict(inference_ran=False,usage=dict(status='partial',known_values=dict(completion_tokens=11),values=None))
real_workspace.Runtime=lambda *a,**kw:r
e=engine_for(sys.argv[1])
@event.listens_for(e,'before_cursor_execute')
def transient(c,cur,statement,parameters,context,many):
 if statement.startswith('UPDATE real_execution_requests') and parameters and parameters[0]=='failed' and len(outages)<3:
  outages.append(1);err=sqlite3.OperationalError('NON-MODEL busy');err.sqlite_errorcode=5
  raise job.OperationalError(statement,{},err)
observer_outages=[]
@event.listens_for(e,'before_cursor_execute')
def monitor_busy(c,cur,statement,parameters,context,many):
 if threading.current_thread().name=='recovery-observer' and statement.startswith('SELECT') and not observer_outages:
  observer_outages.append(1)
  err=sqlite3.OperationalError('NON-MODEL expected observer read busy');err.sqlite_errorcode=5
  raise job.OperationalError(statement,{},err)
worker.engine_for=lambda:e
finished=[]
def observe():
 try:
  end=time.monotonic()+8
  while time.monotonic()<end:
   try:
    with e.connect() as c:
     if c.scalar(select(job.requests.c.status).where(job.requests.c.grant_id==grant_id))=='failed':
      finished.append(1);break
   except job.OperationalError as error:
    if getattr(error.orig,'sqlite_errorcode',0)&0xff!=sqlite3.SQLITE_BUSY:
     raise
   time.sleep(.05)
 finally:
  os.kill(os.getpid(),signal.SIGTERM)
threading.Thread(target=observe,name='recovery-observer',daemon=True).start()
sys.argv=['worker'];worker.main()
assert len(calls)==1 and len(outages)==3 and len(observer_outages)==1 and finished,(calls,outages,observer_outages,finished)
print('NON-MODEL continuous worker: one execution, three delivery failures, automatic atomic result/ledger recovery')
"""
    env = {
        **os.environ,
        "WORKBENCH_DB": str(store.url.database),
        "WORKBENCH_REAL_EXECUTION_ROOT": str(runtime.root),
        "PYTHONPATH": str(Path(__file__).resolve().parent)
        + os.pathsep
        + str(Path(__file__).resolve().parents[1]),
    }
    env.pop("WORKBENCH_LOCAL_KEY", None)
    child = subprocess.run(
        [sys.executable, "-c", script, str(store.url.database), str(runtime.root), grant["id"]],
        env=env,
        timeout=15,
        capture_output=True,
    )
    assert child.returncode == 0, child.stderr.decode()
    row = request(store, grant)
    assert (
        row["status"] == "failed"
        and ledger(store, row["id"])["usage"]["known_values"]["completion_tokens"] == 11
    )


def test_symlink_substitution_cannot_keep_recovery_identity(store, linked):
    _, runtime, grant = linked
    old = runtime.root
    saved = old.with_name("NON_MODEL_original")
    old.rename(saved)
    old.symlink_to(saved, target_is_directory=True)
    assert not runtime.owns_recovery(grant)


def test_dead_worker_read_projection_is_honest_without_runtime_or_store_writes(
    client, store, linked, monkeypatch
):
    task, runtime, grant = linked

    def crash(*args):
        raise SystemExit("NON-MODEL worker interruption before outcome")

    runtime.execute = crash
    with pytest.raises(SystemExit):
        job.process_one(store, runtime)
    row = request(store, grant)
    monkeypatch.setattr(job, "alive", lambda owner: False)
    view = job.review(store, row["id"])
    assert view["recovery_pending"] and "worker stopped" in view["message"]
    listed = job.listing(store)["items"][0]
    assert listed["recovery_pending"] and listed["message"] == view["message"]
    projected = context(client, task)
    assert projected["stage"] == "recovering" and projected["message"] == view["message"]
    assert request(store, grant) == row  # GETs never recover, clean up or start work.
    assert job.recover(store, runtime)
    assert (
        request(store, grant)["status"] == "interrupted"
        and not job.review(store, row["id"])["recovery_pending"]
    )
