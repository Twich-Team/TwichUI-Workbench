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


@pytest.mark.parametrize("mismatch", [None, "sealed-owner", "sealed-result", "finished-at"])
def test_normal_terminal_failure_reuses_bound_owner_without_rewriting_unknown_usage(
    terminated, mismatch
):
    t = terminated
    with write_transaction(t.store) as c:
        row = c.execute(select(execution.requests)).mappings().one()
        owner = row["result"]["owner"]
        result = {k: v for k, v in row["result"].items() if k != "owner"}
        finished = execution.now()
        c.execute(
            execution.requests.update().values(status="failed", result=result, finished_at=finished)
        )

    def sealed(row, grant):
        assert row["result"]["owner"] == owner
        if mismatch == "sealed-owner":
            return None
        return dict(
            status="failed",
            result={} if mismatch == "sealed-result" else result,
            completed_at="different" if mismatch == "finished-at" else finished,
        )

    t.runtime.recover_outcome = sealed
    if mismatch:
        with pytest.raises(ValueError, match="Exact owned local failure evidence"):
            recovery.reconcile(t.store, "controlled-execution")
        assert not (t.root / "terminated-generation.json").exists()
    else:
        proof = recovery.reconcile(t.store, "controlled-execution")
        assert proof["final_usage"] == "unknown" and proof["partial_response_dispatched"] is False
    with t.store.connect() as c:
        saved = c.execute(select(execution.requests)).mappings().one()
        assert saved["result"] == result and saved["finished_at"] == finished


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
            grant = dict(c.execute(select(execution.grants)).mappings().one())
            snapshot = grant["snapshot"]
            snapshot["runtime"]["codex_selection"] = {"model": "controlled-codex"}
            c.execute(
                execution.grants.update().values(
                    snapshot=snapshot, binding=execution.digest(snapshot)
                )
            )
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


@pytest.fixture
def context_terminated(terminated, monkeypatch):
    t = terminated
    from workbench.real_workspace import tree

    (t.root.parent / "implementation/stream-02.json").unlink()
    (t.root / "attempt/request-01.json").unlink()
    previous = dict(kind="never-dispatched-feature-slot-v1", evidence_role="controlled")
    durable(t.root / "feature-recovery.json", json.dumps(previous).encode())
    helper = t.release / "backend/workbench/real_workspace.py"
    durable(helper, b"controlled frozen instruction-construction failure before provider")
    h = sha(helper.read_bytes())
    monkeypatch.setattr(recovery, "CONTEXT_FAILURE_HELPER_SHA256", h)
    t.runtime.initial = t.root / "base-manifest.json"
    durable(t.runtime.initial, json.dumps(tree(t.runtime.workspace)).encode())
    usage = dict(
        status="unavailable",
        values=dict(prompt_tokens=None, completion_tokens=None, total_tokens=None),
        requests_observed=0,
    )
    result = dict(
        error="KeyError: 'feedback'", usage=usage, verification="unknown", inference_ran=None
    )
    finished = execution.now()
    t.runtime.failure_evidence = lambda *args: dict(usage=usage)
    t.runtime.recover_outcome = lambda *args: dict(result=result, completed_at=finished)
    t.record.update(state="released")
    t.primary.write_text(json.dumps(t.record))
    with write_transaction(t.store) as c:
        grant = c.execute(select(execution.grants)).mappings().one()
        snapshot = grant["snapshot"]
        snapshot["runtime"]["helper_sha256"]["real_workspace.py"] = h
        snapshot["runtime"]["feature_recovery_sha256"] = sha(
            (t.root / "feature-recovery.json").read_bytes()
        )
        snapshot["task_budget"] = dict(
            offer=dict(snapshot=dict(feature_step=dict(recovery=previous)))
        )
        c.execute(
            execution.grants.update().values(
                snapshot=snapshot, binding=execution.digest(snapshot), revoked_at=finished
            )
        )
        c.execute(
            execution.requests.update().values(status="failed", finished_at=finished, result=result)
        )
    return t


def test_native_context_fault_can_be_inspected_then_sealed_without_rewriting_unknown_usage(
    context_terminated,
):
    t = context_terminated
    with t.store.connect() as c:
        before = dict(c.execute(select(execution.requests)).mappings().one())
    old = t.primary.read_bytes()
    preview = recovery.reconcile(
        t.store, "controlled-execution", expected_primary_sha256=sha(old), inspect_only=True
    )
    assert preview["qualification"] == "frozen-native-context-failure-v1"
    assert preview["final_usage"] == "unknown" and preview["generation_final_outcome"] == "unknown"
    assert t.primary.read_bytes() == old and not (t.root / "terminated-generation.json").exists()
    proof = recovery.reconcile(t.store, "controlled-execution", expected_primary_sha256=sha(old))
    assert (
        proof["provider_epoch_terminated"] is True and proof["partial_response_dispatched"] is False
    )
    with t.store.connect() as c:
        assert dict(c.execute(select(execution.requests)).mappings().one()) == before


@pytest.mark.parametrize(
    "fault",
    [
        "helper",
        "changed_source",
        "request",
        "context",
        "epoch",
        "active_owner",
        "restoration",
        "no_binding",
    ],
)
def test_pre_provider_context_qualification_keeps_all_native_fences(
    context_terminated, monkeypatch, fault
):
    t = context_terminated
    if fault == "helper":
        (t.release / "backend/workbench/real_workspace.py").write_text("different implementation")
    elif fault == "changed_source":
        (t.runtime.workspace / "known-edit.lua").write_text("changed after baseline")
    elif fault == "request":
        durable(t.root / "attempt/request-00.json", b"{}")
    elif fault == "context":
        (t.root / "feature-recovery.json").write_text("{}")
    elif fault == "epoch":
        value = json.loads(t.primary.read_text())
        value["server_epoch"] = [1, "different"]
        t.primary.write_text(json.dumps(value))
    elif fault == "active_owner":
        monkeypatch.setattr(execution, "alive", lambda _: True)
    elif fault == "restoration":
        monkeypatch.setattr(recovery, "normal_restored", lambda *args: False)
    expected = sha(t.primary.read_bytes()) if fault != "no_binding" else None
    with pytest.raises((ValueError, OSError, KeyError)):
        recovery.reconcile(t.store, "controlled-execution", expected_primary_sha256=expected)
    assert not (t.root / "terminated-generation.json").exists()
