"""Verify the terminal implementation, immutable receipts, and restoration without generation."""
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from shutil import copyfile
from datetime import datetime, timezone, timedelta
import subprocess
import sys

from sqlalchemy import create_engine, select
from workbench import real_handoff, real_execution
from workbench.db import model_attempts, proposal_decisions, real_execution_grants, real_execution_requests, task_sessions
from workbench.real_workspace import Runtime, suite_inventory
from browser_demo.live import verify_model_installation
from browser_demo import stage as layout

output = Path(sys.argv[1])
isolated = layout.ROOT
manifest = layout.sealed_inputs(isolated, sys.argv[2])
fixture = Path(manifest["fixture"])
database = isolated / layout.DATABASE
before = isolated / layout.BACKUP
terminal = json.loads((output / "terminal-session.json").read_text()) if (output / "terminal-session.json").exists() else {}
decision = json.loads((output / "ingress-budget.json").read_text())["body"] if (output / "ingress-budget.json").exists() else {}
intake = json.loads((output / "ingress-submit.json").read_text())["body"]["request_id"] if (output / "ingress-submit.json").exists() else None
decision_id = terminal.get("decision_id") or decision.get("command_id")
cycle = isolated / "artifacts/runs" / ("task-lease-task-session-" + str(decision_id) + "-01")
real = cycle / "real-run"

def sha(file):
    return sha256(file.read_bytes()).hexdigest()

def engine(path):
    return create_engine(f"sqlite:///{path}", creator=lambda: sqlite3.connect(
        path.as_uri() + "?mode=ro&immutable=1", uri=True))

policy = json.loads((isolated / layout.POLICY).read_text())
assert [database.stat().st_dev,database.stat().st_ino] == policy["database_identity"]
assert not any(Path(str(database) + suffix).exists() for suffix in ("-wal", "-shm", "-journal"))
assert sha(before) == manifest["sha256"][str(layout.DATABASE)]
preserved = 0
for index, fingerprint in manifest["history"].items():
    index = Path(index)
    assert sha(index) == fingerprint
    history = json.loads(index.read_text())
    if all(isinstance(value, dict) for value in history.values()):
        roots = history
    else:
        project = index.parents[3]
        roots = {str(project): history}
    for root, files in roots.items():
        for name, expected in files.items():
            assert sha(Path(root) / name) == expected, name
            preserved += 1
window = json.loads((output / "window.json").read_text())
outer = datetime.fromisoformat(window["deadline"])
cutoff = datetime.fromisoformat(window["review_cutoff"])
base = manifest["fixture_base"]
checkouts = [fixture]
if manifest["preserve_checkout"]:
    checkouts.append(Path(manifest["preserve_checkout"]))
for checkout in checkouts:
    git = ["git", "--no-optional-locks", "-C", str(checkout)]
    assert not subprocess.check_output(git + ["status", "--porcelain=v1", "--ignored"])
    assert subprocess.check_output(git + ["rev-parse", "HEAD"], text=True).strip() == base
assert not (isolated / layout.ACTIVE).exists()

with engine(database).connect() as current, engine(before).connect() as old:
    assert current.exec_driver_sql("PRAGMA integrity_check").scalar_one() == "ok"
    assert not current.exec_driver_sql("PRAGMA foreign_key_check").first()
    assert current.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == "0014"
    planning=select(model_attempts).where(model_attempts.c.purpose=="planning").order_by(model_attempts.c.id)
    assert not old.execute(planning).first()
    planned=list(current.execute(planning).mappings())
    assert len(planned)<=1
    planned_usage=None
    if planned:
        one,=planned
        assert one["task_id"]==intake and one["requested_model"]=="pilot-qwen38-27b" and one["policy_revision"]==1
        assert one["outcome"] not in {"running"}
        if one["usage"] is not None:
            assert one["source"]=="workbench" and one["backend"]=="local" and one["actual_model"]=="pilot-qwen38-27b"
            assert one["usage"]["source"] == "local llama.cpp response"
            planned_usage=one["usage"]["values"]
            assert 0<planned_usage["completion_tokens"]<=2600
            assert 0<planned_usage["prompt_tokens"] and planned_usage["total_tokens"] == planned_usage["completion_tokens"]+planned_usage["prompt_tokens"]<=32768
    if (output/"ingress-scope.json").exists():
        receipt=json.loads((output/"ingress-scope.json").read_text())
        assert datetime.fromisoformat(receipt["at"])<=cutoff
        body=receipt["body"]
        scope=current.execute(select(proposal_decisions).where(proposal_decisions.c.id==body["command_id"])).mappings().one()
        assert scope["binding"]==body["binding"] and scope["proposal_id"]==body["proposal_id"]
        assert scope["payload"]["expectation_review"]==body["expectation_review"] and not scope["payload"].get("run_budget")
    counts = {name: current.exec_driver_sql(f"SELECT count(*) FROM {name}").scalar_one() for name in (
        "real_publications", "fake_publications", "pr_preparations", "task_run_offers", "proposal_decisions",
        "task_sessions", "real_execution_grants", "real_execution_requests")}
    assert counts["real_publications"] == counts["fake_publications"] == counts["pr_preparations"] == 0
    assert counts["task_run_offers"] <= 1 and counts["task_sessions"] <= 1 and counts["proposal_decisions"] <= 2
    assert counts["real_execution_grants"] <= 1 and counts["real_execution_requests"] <= 1
    saved = current.execute(select(proposal_decisions).where(proposal_decisions.c.id == decision_id)).mappings().first()
    if saved:
        assert datetime.fromisoformat(saved["created_at"])<=cutoff
        outer=min(outer, datetime.fromisoformat(saved["created_at"])+timedelta(minutes=90))
        assert saved["payload"]["execution_continuation"]["browser_request"]=={
            **decision,"task_id":saved["payload"]["execution_continuation"]["browser_request"]["task_id"]}
        budget = saved["payload"]["run_budget"]
        assert budget == decision["run_budget"]
        assert budget["activate_once"] and budget["publication"] == "hold" and budget["fallback"] is False
    session = current.execute(select(task_sessions)).mappings().first()
    if session:
        assert session["id"] == decision_id and session["status"] not in ("pending","active","waiting_recovery")
        assert not terminal or session["status"] == terminal["status"]
    attempts = [dict(row) for row in current.execute(select(model_attempts)).mappings()]
    assert all(row["purpose"] in ("planning","capability_validation","implementation") for row in attempts)
    attempts=[row for row in attempts if row["purpose"]!="planning"]
    assert len([row for row in attempts if row["purpose"] == "capability_validation"]) <= 1
    assert len([row for row in attempts if row["purpose"] == "implementation"]) <= 1
    grants = [dict(row) for row in current.execute(select(real_execution_grants)).mappings()]
    requests = [dict(row) for row in current.execute(select(real_execution_requests)).mappings()]
    if grants:
        grant,=grants
        assert grant["revoked_at"] is not None
        assert grant["snapshot"]["task_budget"]["decision_id"]==decision_id
        assert grant["snapshot"]["workflow_task"]["intake_id"]==intake
        assert grant["binding"]==real_execution.digest(grant["snapshot"])
        if requests:
            assert requests[0]["grant_id"]==grant["id"]

report = json.loads((cycle / "lease-controller-result.json").read_text()) if (cycle / "lease-controller-result.json").is_file() else {}
restoration = report.get("restoration") or {}
probe_file = cycle / "probe-result.json"
probe = json.loads(probe_file.read_text()) if probe_file.is_file() else {}
streams = [json.loads(file.read_text()) for file in sorted((cycle / "implementation").glob("stream-*.json"))]
wire = [row for row in streams if row["wire_started"]]
assert len(wire) <= 20 and all(row["max_generated"] <= 2496 for row in wire)
probe_streams=list((cycle/"probe").glob("stream-*.json"))
assert len(probe_streams)<=1
assert len(wire)+int(bool(probe.get("wire_started")))<=21
if probe.get("wire_started"):
    assert probe["generated_upper_bound"]==60 and probe["elapsed_s"]<=30 and probe["input_tokens"]<=128
private = isolated / ".local" / ("chronicle-primary-lease-" + cycle.name)
state_file=private/"state.json"
state=json.loads(state_file.read_text()) if state_file.is_file() else {}
if state.get("applied") or (cycle / "lease-established.json").is_file():
    assert all(state.get(k) and restoration.get(k) for k in (
        "restored","original_config_verified","original_access_verified","temporary_access_rejected"))
    established_path=cycle/"lease-established.json"
    established=json.loads(established_path.read_text()) if established_path.is_file() else {}
    rejections=established.get("auth_rejections",[])
    assert not established or len(rejections)==111 and all(row["status"]==401 for row in rejections)
    lease_seconds=round(state["restored_at"]-state["established_at"],2) if state.get("established_at") else None
    if state.get("established_at"):
        assert datetime.fromtimestamp(state["established_at"],timezone.utc)<=outer-timedelta(minutes=50)
    assert datetime.fromtimestamp(state["restored_at"],timezone.utc)<=outer
    assert lease_seconds is None or 0<lease_seconds<=3600
    lease_checks=dict(lease_rejections=len(rejections),lease_seconds=lease_seconds,restoration_verified=True,
                      original_access_verified=True,temporary_access_rejected=True)
else:
    lease_checks=dict(lease_rejections=0,lease_seconds=None,restoration_verified=False,no_temporary_auth_applied=True)
pid = int(subprocess.check_output(["systemctl", "show", "twichui-llm.service", "-p", "MainPID", "--value"]))
assert pid
proc = Path("/proc") / str(pid)
env_path = Path(manifest["model_identity"]["config_path"])
if lease_checks["restoration_verified"]:
    assert env_path.read_bytes() == (private / "serve.env.original").read_bytes()
    assert env_path.stat().st_mode & 0o777 == state["original_mode"]
    assert sha(proc / "exe") == state["executable_sha256"]
    assert sha(proc / "cmdline") == state["argv_sha256"]
    assert state["restored_epoch"][0] == pid

# The frozen controller already performed the authenticated restore reads.
# Independently recheck the installed process, files and credential without another model request.
verify_model_installation(isolated, manifest, json.loads((isolated / layout.PENDING).read_text()))
lease_checks["normal_authenticated_access_verified"]=bool(state.get("original_access_verified") or planned_usage)

checked = None
if requests and requests[0]["status"] == "checked":
    row, = requests
    grant, = grants
    assert grant["revoked_at"] is not None
    assert session["status"] == "completed" and lease_checks["restoration_verified"]
    assert probe["passed"] and probe["wire_started"]
    assert probe["generated_upper_bound"] == 60 and probe["elapsed_s"] <= 30 and probe["input_tokens"] <= 128
    runtime = Runtime(real)
    sealed = real_handoff.load(runtime, row, grant)
    assert sealed and sealed["result"]["verification"] == "passed"
    result = sealed["result"]
    assert result["inference_ran"] and result["candidate_id"] == row["result"]["candidate_id"]
    assert result["usage"]["status"] == "complete"
    assert 0 < result["usage"]["values"]["completion_tokens"] <= 24000
    assert result["correction_count"] <= 2
    assert len(wire) >= 1 and all(row["completed"] for row in wire)
    assert planned_usage and len(planned)==1
    assert min(row["started_at"] for row in wire)<= (outer-timedelta(minutes=45)).timestamp()
    assert max(row["finished_at"] for row in wire)-min(row["started_at"] for row in wire)<=1200
    for wire_row in wire:
        assert wire_row["elapsed_s"]<=120 and wire_row["fence_state"]=="released"
        assert wire_row["wire_bytes"]<=2<<20 and wire_row["largest_line_bytes"]<=64<<10 and wire_row["assembled_bytes"]<=256<<10
        assert not wire_row["output_limits_exceeded"] and not wire_row["stream_error"]
    assert sum(r["usage"]["completion_tokens"] for r in wire)==result["usage"]["values"]["completion_tokens"]
    assert result["usage"]["values"]["completion_tokens"]+planned_usage["completion_tokens"]+60<=26660
    assert len(wire)+int(bool(probe.get("wire_started")))+len(planned)<=22
    expected_path, = manifest["task"]["intake"]["regression_tests"]["test_paths"]
    assert len(result["files"]) == 1 and result["files"][0]["path"] == expected_path
    assert result["files"][0]["additions"] <= 150 and result["files"][0]["deletions"] == 0
    candidate = real / "candidates" / result["candidate_id"]
    patch = candidate / "changes.patch"
    assert patch.stat().st_size <= 65536
    assert subprocess.check_output(["git","apply","--numstat","--",str(patch)],cwd=fixture,
        text=True).split() == [str(result["files"][0]["additions"]),"0",expected_path]
    assert sha(patch)==result["manifest"]["patch_sha256"]
    assert not any(line.startswith(("old mode ","new mode ","new file mode ","deleted file mode "))
                   for line in patch.read_text().splitlines())
    expected_fixture=real/"recipe.json"
    assert json.loads(expected_fixture.read_text())["contract"]["base"]==base
    assert len(runtime.expected_suite()) == manifest["task"]["suite_groups"]
    assert suite_inventory(result["checks"][-1], runtime.expected_suite())
    assert len(result["checks"]) == 4 and all(check["status"] == "pass" for check in result["checks"])
    for check in result["checks"]:
        telemetry=check["usage"]
        assert telemetry["cgroup_removed"] and not any(telemetry[k] for k in ("oom_kill","pids_limit_hit","leaked_after_kill"))
        assert telemetry["file_limit_bytes"]==(128<<20 if telemetry["trusted_verification"] else 16<<20)
        assert check["network_mode"]==("private-loopback" if telemetry["trusted_verification"] else "denied")
    controls=json.loads((real/"profile-evidence.json").read_text())["controls"]
    assert {k:v["checks"][2]["status"] for k,v in controls.items()}=={
        "known-good-oracle-control":"pass","original":"fail","vacuous":"fail","wrong-behavior":"fail"}
    assert all(row["status"]=="passed" for row in json.loads((cycle/"pre-grant-gates.json").read_text()))
    counts_actual=[json.loads(p.read_text()) for p in (real/"attempt").glob("request-*.json")]
    assert len(counts_actual)==len(wire) and len(counts_actual)+1<=21
    assert all(row["counted_input_tokens"]+row["request_limit"]<=32768 for row in counts_actual)
    assert datetime.fromisoformat(sealed["completed_at"])<=outer
    retained=output/"checked-map-fade.patch"
    if retained.exists():
        assert sha(retained)==sha(patch)
    else:
        copyfile(patch,retained)
        retained.chmod(0o600)
    checked = dict(request_id=row["id"], candidate_id=result["candidate_id"], patch=str(retained),patch_sha256=sha(retained),
        generated_tokens=result["usage"]["values"]["completion_tokens"], prompt_tokens=result["usage"]["values"]["prompt_tokens"],
        corrections=result["correction_count"], additions=result["files"][0]["additions"],
        checked_at=sealed["completed_at"], check_status=[check["status"] for check in result["checks"]],
        suite_groups=runtime.expected_suite(), completion_seal_verified=True)
print(json.dumps(dict(session=terminal, requests=[{"id":row["id"],"status":row["status"]} for row in requests],
    counts=counts, attempts=[dict(purpose=row["purpose"], outcome=row["outcome"]) for row in attempts],
    probe_passed=probe.get("passed"), probe_seconds=probe.get("elapsed_s"), probe_requests=int(bool(probe.get("wire_started"))),
    implementation_requests=len(wire), restoration=lease_checks, normal_credential_loaded=True,
    restored_pid=pid, historical_files_preserved=preserved,
    planning_claims=len(planned), planning_outcome=planned[0]["outcome"] if planned else None,
    planning_usage=planned_usage, intake=intake, checked=checked,
    verified_at=datetime.now(timezone.utc).isoformat()), indent=2))
