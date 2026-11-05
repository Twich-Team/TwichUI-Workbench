"""Owner-approved task ingress and real-worker dispatch, NON-MODEL disposable data."""

from pathlib import Path
import hashlib
import importlib.util
import json
import time

import pytest
from sqlalchemy import select

from workbench import owner_task, task_execution, real_execution as job
from workbench.db import model_attempts, planning_jobs, proposals, write_transaction
from workbench.real_package import Package
from test_real_handoff import HandoffRuntime
from workbench.real_workspace import durable


@pytest.fixture
def owner(store, tmp_path):
    auth = tmp_path / "owner.txt"
    auth.write_text("NON-MODEL test authorization for the exact connected Chronicle replay")
    h = hashlib.sha256(auth.read_bytes()).hexdigest()
    ref = owner_task.record(store, auth, h)
    return ref, auth, h


def test_owner_ingress_is_honest_idempotent_and_has_no_inference_rows(store, client, owner):
    ref, auth, h = owner
    assert owner_task.record(store, auth, h) == ref
    result = client.get("/api/intake/" + ref["intake_id"])
    assert result.status_code == 200, result.text
    data = result.json()
    assert (
        data["proposals"][0]["basis"] == "owner"
        and data["proposals"][0]["model"] == "owner-supplied"
    )
    assert data["decisions"][0]["status"] == "applied" and data["human_scope"]["origin"] == "human"
    with store.connect() as c:
        pin = task_execution.snapshot(c, **ref)
        assert pin["decomposition"]["payload"]["owner_provenance"]["authorization_sha256"] == h
        assert not c.execute(
            select(model_attempts).where(model_attempts.c.task_id == ref["task_id"])
        ).all()
        jobs = (
            c.execute(select(planning_jobs).where(planning_jobs.c.intake_id == ref["intake_id"]))
            .mappings()
            .all()
        )
        assert len(jobs) == 2 and all(
            j["attempts"] == 0 and j["model"] == "owner-supplied" for j in jobs
        )
    context = client.get(f"/api/intake/{ref['intake_id']}/tasks/{ref['task_id']}/execution").json()
    assert context["stage"] == "waiting" and "separate execution permission" in context["message"]
    assert "/api/owner-task" not in client.get("/openapi.json").json()["paths"]


def test_changed_instruction_or_scope_cannot_be_overwritten(store, owner):
    ref, auth, h = owner
    auth.write_text("changed")
    with pytest.raises(ValueError, match="identity"):
        owner_task.record(store, auth, h)
    auth.write_text("NON-MODEL test authorization for the exact connected Chronicle replay")
    with write_transaction(store) as c:
        c.execute(
            proposals.update()
            .where(proposals.c.intake_id == ref["intake_id"])
            .values(scope_revision=99)
        )
    with pytest.raises(Exception):
        owner_task.record(store, auth, h)


def test_missing_wrong_hash_and_symlink_instruction_denied(store, tmp_path):
    p = tmp_path / "x"
    p.write_text("safe")
    with pytest.raises(ValueError):
        owner_task.record(store, p, "0" * 64)
    link = tmp_path / "link"
    link.symlink_to(p)
    with pytest.raises(ValueError):
        owner_task.record(store, link, hashlib.sha256(p.read_bytes()).hexdigest())


def test_actual_owner_task_grant_automatic_worker_seal_and_review(store, client, owner, tmp_path):
    ref, auth, h = owner
    root = tmp_path / "NON_MODEL"
    root.mkdir()
    durable(root / "oracle.lua", b"-- NON-MODEL")
    durable(root / "base-manifest.json", b"{}")
    rt = HandoffRuntime(root)
    grant = job.issue(store, auth.read_text(), str(auth), rt, task=ref)
    # No manual submit: production worker dispatch creates the request.
    assert job.process_one(store, rt)
    request = task_execution.request_id(grant["id"])
    view = client.get("/api/real-executions/" + request).json()
    assert view["status"] == "checked" and view["workflow_task"]["task_id"] == ref["task_id"]
    assert view["result"]["manifest"]["workflow_task_binding"] == job.digest(
        grant["snapshot"]["workflow_task"]
    )
    assert not job.process_one(store, rt)
    with store.connect() as c:
        records = (
            c.execute(select(model_attempts).where(model_attempts.c.execution_id == request))
            .mappings()
            .all()
        )
        assert len(records) == 1 and records[0]["task_id"] == ref["task_id"]


def test_controller_existing_store_link_never_initializes_or_migrates(store, owner, tmp_path):
    ref, auth, h = owner
    spec = importlib.util.spec_from_file_location(
        "test_connected_controller", Path("scripts/chronicle_lease.py")
    )
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    cycle = tmp_path / "cycle"
    cycle.mkdir()
    real = cycle / "real-run"
    real.mkdir()
    db = Path(store.url.database)
    s = db.stat()
    link = dict(
        database=str(db), database_identity=[s.st_dev, s.st_ino], task=ref, authorization_sha256=h
    )
    p = cycle / "workflow-link.json"
    p.write_text(json.dumps(link))
    p.chmod(0o600)
    assert m.workflow(cycle, auth) == link
    opened = m.store(real)
    assert opened.url.database == str(db)
    opened.dispose()
    assert not (real / "acceptance.sqlite3").exists()
    link["database_identity"][1] += 1
    p.write_text(json.dumps(link))
    with pytest.raises(ValueError, match="identity"):
        m.workflow(cycle, auth)


def test_ninety_minute_one_cycle_profile_cannot_allocate_a_second(tmp_path):
    root = tmp_path / "chronicle-connected-budget"
    root.mkdir()
    started = time.time()
    data = dict(
        started_at=started,
        deadline=started + 5400,
        max_cycles=1,
        max_probes=1,
        max_attempts=1,
        max_generated_tokens=24000,
        reserved_tokens_per_cycle=24000,
        original_attempt_token_ceiling=24000,
        original_turns=20,
    )
    (root / "BUDGET_BASELINE.json").write_text(json.dumps(data))
    package = Package(root)
    c = root.parent / ("chronicle-lease-" + root.name + "-01")
    c.mkdir()
    package.reserve(c)
    second = root.parent / ("chronicle-lease-" + root.name + "-02")
    second.mkdir()
    with pytest.raises(ValueError, match="exhausted"):
        package.reserve(second)


def test_original_real_run_clock_is_not_reset_when_controller_restarts():
    spec = importlib.util.spec_from_file_location(
        "test_connected_deadline", Path("scripts/chronicle_lease.py")
    )
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    start = time.time() - 100
    link = dict(run_started_at=start, run_deadline=start + 2400)
    first = m.connected_deadline(link)
    assert first.timestamp() == pytest.approx(start + 2400, rel=0, abs=0.000001)
    assert m.connected_deadline(link) == first
    for bad in [
        dict(run_started_at=start, run_deadline=start + 2401),
        dict(run_started_at=start + 3000, run_deadline=start + 5400),
        dict(run_started_at=float("nan"), run_deadline=start + 2400),
    ]:
        with pytest.raises(ValueError):
            m.connected_deadline(bad)
