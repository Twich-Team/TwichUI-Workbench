"""A stopped owned local epoch is distinct from an uncertain live generation."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from workbench import local_generation_recovery as recovery, real_execution as execution
from workbench.db import write_transaction
from workbench.real_workspace import durable, sha


@pytest.fixture
def terminated(store, tmp_path, monkeypatch):
    release = tmp_path / "release"
    run = release / "artifacts/runs/task-lease-task-session-controlled"
    root = run / "real-run"
    root.mkdir(parents=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "known-edit.lua").write_text("model-authored known edit")
    private = release / ".local" / ("chronicle-primary-lease-" + run.name)
    state = dict(
        established_at=1, deadline=1000, temporary_epoch=[99, "old"], restored_epoch=[100, "new"]
    )
    durable(private / "state.json", json.dumps(state).encode())
    primary = release / ".local/real-primary-session.json"
    record = dict(
        state="quarantined",
        attempt="2",
        task_id=42,
        nonce="wb-real-controlled",
        observations=[],
        server_epoch=[99, "old"],
    )
    durable(primary, json.dumps(record).encode())
    Path(str(primary) + ".lock").touch(mode=0o600)
    report = dict(
        sequence=2,
        conversation_nonce="wb-real-controlled",
        server_epoch=[99, "old"],
        fence_state="quarantined",
        completed=False,
        busy_observation=dict(task=42),
        started_at=10,
        finished_at=20,
    )
    durable(run / "implementation/stream-02.json", json.dumps(report).encode())
    request = dict(turn=1, request_id="controlled-execution", fence="controlled-fence")
    durable(root / "attempt/request-01.json", json.dumps(request).encode())
    owner = dict(pid=999999999, boot="not-running", start="0")
    durable(
        root / "ownership-controlled-execution.json",
        json.dumps(
            dict(owner=owner, cgroup="/sys/fs/cgroup/workbench-real-controlled-absent")
        ).encode(),
    )
    helpers = {}
    for name in ("real_provider.py", "real_stream.py"):
        p = release / "backend/workbench" / name
        durable(p, b"controlled old dispatcher")
        helpers[name] = sha(p.read_bytes())
    snapshot = dict(
        contract=dict(provider="local"),
        runtime=dict(helper_sha256=helpers),
        recovery=dict(root=str(root)),
    )
    with write_transaction(store) as c:
        c.execute(
            execution.grants.insert().values(
                id="controlled-grant",
                snapshot=snapshot,
                binding=execution.digest(snapshot),
                issued_at=execution.now(),
                expires_at=execution.now(),
                revoked_at=None,
            )
        )
        c.execute(
            execution.requests.insert().values(
                id="controlled-execution",
                grant_id="controlled-grant",
                payload={},
                status="running",
                fence="controlled-fence",
                message="Waiting for cleanup",
                created_at=execution.now(),
                result=dict(
                    owner=owner, usage=dict(status="partial", known_values=dict(total_tokens=31))
                ),
            )
        )
    runtime = SimpleNamespace(
        root=root,
        workspace=workspace,
        owns_recovery=lambda g: True,
        scope=lambda **kwargs: (["known-edit.lua"], "controlled-diff"),
        recover_outcome=lambda *args: dict(
            status="failed", result=dict(usage=dict(status="partial"))
        ),
    )
    monkeypatch.setattr(recovery, "Runtime", lambda p: runtime)
    monkeypatch.setattr(recovery, "normal_restored", lambda p, s: True)
    return SimpleNamespace(
        store=store,
        release=release,
        root=root,
        private=private,
        primary=primary,
        runtime=runtime,
        record=record,
        report=report,
        request=request,
    )


def test_terminated_epoch_discharge_preserves_unknown_usage_original_failure_and_edit(terminated):
    t = terminated
    with t.store.connect() as c:
        before = dict(c.execute(select(execution.requests)).mappings().one())
    old = t.primary.read_bytes()
    proof = recovery.reconcile(t.store, "controlled-execution", expected_primary_sha256=sha(old))
    record = json.loads(t.primary.read_text())
    assert (
        record["state"] == "released"
        and record["release_kind"] == "verified-owned-epoch-termination"
    )
    assert proof["generation_final_outcome"] == proof["final_usage"] == "unknown"
    assert proof["partial_response_dispatched"] is False
    assert (
        json.loads(
            (t.primary.parent / "real-primary-session.json.events.jsonl")
            .read_text()
            .splitlines()[0]
        )
        == record
    )
    assert t.runtime.workspace.joinpath("known-edit.lua").read_text() == "model-authored known edit"
    with t.store.connect() as c:
        assert dict(c.execute(select(execution.requests)).mappings().one()) == before
    assert recovery.reconcile(t.store, "controlled-execution") == proof


@pytest.mark.parametrize(
    "fault",
    [
        "state_hash",
        "live_owner",
        "restore",
        "tool_dispatched",
        "different_task",
        "different_request",
        "scope",
        "failure_missing",
        "code_changed",
        "edit_uncertain",
        "successful_request",
        "codex_provider",
    ],
)
def test_no_release_when_termination_or_exact_failed_call_is_unproven(
    terminated, monkeypatch, fault
):
    t = terminated
    if fault == "live_owner":
        monkeypatch.setattr(execution, "alive", lambda owner: True)
    if fault == "restore":
        monkeypatch.setattr(recovery, "normal_restored", lambda p, s: False)
    if fault == "tool_dispatched":
        durable(t.root / "attempt/tool-01-00.json", b"{}")
    if fault == "different_task":
        t.record["task_id"] = 999
        t.primary.write_text(json.dumps(t.record))
    if fault == "different_request":
        t.request["fence"] = "different"
        (t.root / "attempt/request-01.json").write_text(json.dumps(t.request))
    if fault == "scope":
        t.runtime.scope = lambda **kwargs: (_ for _ in ()).throw(ValueError("scope changed"))
    if fault == "failure_missing":
        t.runtime.recover_outcome = lambda *args: None
    if fault == "code_changed":
        (t.release / "backend/workbench/real_provider.py").write_text("changed dispatcher")
    if fault == "edit_uncertain":
        durable(t.root / "edit-batches/unfinished/prepared.json", b"{}")
    if fault == "successful_request":
        with write_transaction(t.store) as c:
            c.execute(execution.requests.update().values(status="checked"))
    if fault == "codex_provider":
        with write_transaction(t.store) as c:
            grant=dict(c.execute(select(execution.grants)).mappings().one())
            snapshot=grant['snapshot']
            snapshot['runtime']['codex_selection']={'model':'controlled-codex'}
            c.execute(execution.grants.update().values(snapshot=snapshot,binding=execution.digest(snapshot)))
    before = t.primary.read_bytes()
    with pytest.raises(ValueError):
        recovery.reconcile(
            t.store,
            "controlled-execution",
            expected_primary_sha256="0" * 64 if fault == "state_hash" else None,
        )
    assert t.primary.read_bytes() == before and not (t.root / "terminated-generation.json").exists()


@pytest.mark.parametrize("fault", [None, "live_epoch", "usage_changed", "codex", "source_changed"])
def test_continuation_accepts_only_the_proven_local_termination_with_unchanged_unknown_usage(
    terminated, monkeypatch, fault
):
    from workbench import repair_continuation, real_workspace

    t = terminated
    recovery.reconcile(t.store, "controlled-execution")
    with t.store.connect() as c:
        row = dict(c.execute(select(execution.requests)).mappings().one())
        grant = dict(c.execute(select(execution.grants)).mappings().one())
    row["grant_snapshot"] = grant["snapshot"]
    original_usage = row["result"]["usage"]
    t.runtime.failure_evidence = lambda *args: dict(usage=original_usage)
    monkeypatch.setattr(real_workspace, "Runtime", lambda p: t.runtime)
    if fault == "live_epoch":
        monkeypatch.setattr(recovery, "absent_epoch", lambda e: False)
    if fault == "usage_changed":
        row["result"] = {
            **row["result"],
            "usage": {"status": "partial", "known_values": {"total_tokens": 0}},
        }
    if fault == "codex":
        row["grant_snapshot"]["runtime"]["codex_selection"] = {"model": "controlled-codex"}
    if fault == "source_changed":
        (t.runtime.workspace / "known-edit.lua").write_text("unexpected change")
    assert repair_continuation.settled_accounting(row) is (fault is None)
