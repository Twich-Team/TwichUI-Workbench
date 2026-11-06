"""Activate a staged, approved browser demonstration on the isolated loopback port."""

from contextlib import closing, contextmanager
from datetime import datetime, timedelta
from hashlib import sha256
from hmac import compare_digest
import json
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import runpy

from browser_demo.launch import run_owned, utc_now
from browser_demo.gate import SYNTHETIC_TASK
from browser_demo.supervisor import RESERVE, RunHeld
from project_env import ARTIFACT_LIMIT, artifact_bytes, require_ports

EMPTY_TABLES = (
    "intakes", "planning_jobs", "proposals", "proposal_decisions", "task_run_offers",
    "task_sessions", "model_attempts", "real_execution_grants", "real_execution_requests",
    "real_publications", "fake_publications", "pr_preparations", "execution_policies",
)


def require(condition, message):
    if not condition:
        raise RunHeld(message)


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def verify_model_installation(stage, manifest, config):
    # Read the running binary/config and the previously established sampler proof.
    # Readiness and cancellation over the wire belong to the authorized run.
    identity = manifest["model_identity"]
    pid = subprocess.check_output(
        ["/usr/bin/systemctl", "show", "twichui-llm.service", "-p", "MainPID", "--value"],
        text=True, timeout=3).strip()
    require(pid.isdigit() and int(pid) > 0, "The existing local model process is unavailable.")
    proc = Path("/proc") / pid
    require(all(digest(path) == identity[key] for path, key in (
        (proc / "exe", "executable_sha256"), (proc / "cmdline", "argv_sha256"),
        (Path(identity["unit_path"]), "unit_sha256"),
        (Path(identity["config_path"]), "config_sha256"))),
        "The installed local model or service configuration changed.")
    running = next((item.partition(b"=")[2] for item in (proc / "environ").read_bytes().split(b"\0")
                    if item.startswith(b"LLAMA_API_KEY=")), b"")
    require(running and compare_digest(running, Path(manifest["local_key_path"]).read_bytes().strip()),
            "The normal private model credential could not be verified.")
    checked_limit = runpy.run_path(str(stage / "scripts/chronicle_lease.py"))["checked_limit"]
    require(checked_limit(Path(config["total_limit_proof"]).parent) == config["total_limit_proof_sha256"],
            "The installed generation limit proof no longer matches.")


@contextmanager
def owned_scratch(evidence):
    # Retain scratch if the controller could not confirm cleanup.
    scratch = Path(tempfile.mkdtemp(prefix="wb-browser-run."))
    (evidence / "scratch.txt").write_text(str(scratch) + "\n")
    try:
        yield scratch
    finally:
        if (evidence / "terminal-session.json").is_file() and not (evidence / "unsettled-session.json").exists():
            if not any(scratch.iterdir()):
                scratch.rmdir()


def run(manifest_path, evidence):
    manifest = json.loads(manifest_path.read_text())
    task = manifest.get("task", SYNTHETIC_TASK)
    require(task["base"] == manifest["fixture_base"], "The approved browser task has a different source base.")
    stage = manifest_path.parent.resolve()
    require(Path(__file__).resolve().parents[2] == stage, "Use the supervisor from the tested staged source.")
    require(evidence.is_dir() and evidence.stat().st_uid == os.getuid() and evidence.stat().st_mode & 0o077 == 0,
            "Use a new private owned evidence directory.")
    require_ports([9187])
    window = json.loads((evidence / "window.json").read_text())
    start = datetime.fromisoformat(window["start"])
    minutes = manifest.get("window_minutes", 90)
    reserve = manifest.get("budget_reserve_minutes", 55)
    require(type(minutes) is int and type(reserve) is int and 90 <= minutes <= 120
            and 55 <= reserve < minutes, "The manifest does not specify a supported bounded window.")
    deadline = start + timedelta(minutes=minutes)
    require(start <= utc_now() < deadline - timedelta(minutes=reserve), "The authorized setup window is unavailable.")
    require(not (evidence / "ingress-phase.json").exists(), "This evidence directory already owns a run.")
    for name, checksum in manifest["sha256"].items():
        path = stage / name
        require(path.resolve().is_relative_to(stage) and digest(path) == checksum,
                "The tested source, assets, policy or preflight files changed.")
    database = stage / "artifacts/runs/browser-demo-store/review.sqlite3"
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=1)) as connection:
        require(connection.execute("PRAGMA quick_check").fetchone() == ("ok",), "The staged store is unhealthy.")
        require(all(not connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
                    for table in EMPTY_TABLES), "The staged store already contains work or authority.")
    pending = stage / ".local/task-runtime.pending.json"
    active = pending.with_name("task-runtime.json")
    config = json.loads(pending.read_text())
    require(config["database"] == str(database) and config["database_identity"] ==
            [database.stat().st_dev, database.stat().st_ino] and config["automatic_fallback"] is False,
            "The private runtime capability does not belong to the staged store.")
    require(not active.exists() and not active.is_symlink(), "The capability is already active.")
    verify_model_installation(stage, manifest, config)
    require(not (evidence / "guarded-runtime-preflight.py").exists(), "Use fresh run evidence.")
    shutil.copyfile(stage / ".local/guarded-runtime-preflight.py", evidence / "guarded-runtime-preflight.py")
    stopped = False

    def stop_requested(*_):
        nonlocal stopped
        stopped = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, stop_requested)
    with owned_scratch(evidence) as scratch:
        counted_roots = {stage, *(Path(root).resolve() for root in manifest["counted_roots"])}
        require(any(evidence.is_relative_to(root / "artifacts/runs") for root in counted_roots),
                "The run evidence is outside the aggregate storage limit.")

        def room(reserve):
            for _ in range(3):
                try:
                    counted = sum(artifact_bytes(root) for root in counted_roots)
                    scratch_bytes = sum(path.stat().st_size for path in scratch.rglob("*")
                                        if path.is_file() and not path.is_symlink())
                    break
                except FileNotFoundError:
                    time.sleep(.05)
            else:
                raise RunHeld("Evidence and scratch capacity could not be verified.")
            physical = min(shutil.disk_usage(path).free for path in (evidence, stage, scratch, "/dev/shm"))
            require(counted + reserve <= ARTIFACT_LIMIT and scratch_bytes <= 1 << 30 and physical >= 4 << 30,
                    "Evidence headroom, physical space or scratch exceeded the approved limit.")
            return dict(counted=counted, headroom=ARTIFACT_LIMIT-counted, scratch=scratch_bytes, physical_free=physical)

        fixture = Path(manifest["fixture"])
        require(not subprocess.check_output(["git", "-C", str(fixture), "status", "--porcelain=v1", "--ignored"]),
                "The fixture checkout is not clean.")
        require(subprocess.check_output(["git", "-C", str(fixture), "symbolic-ref", "--short", "HEAD"]).strip() == b"main"
                and subprocess.check_output(["git", "-C", str(fixture), "rev-parse", "HEAD"]).decode().strip()
                == manifest["fixture_base"], "The fixture branch or base changed.")

        def activate():
            room(RESERVE)
            original = pending.read_bytes()
            with active.open("xb") as stream:
                stream.write(original)
                stream.flush()
                os.fsync(stream.fileno())
            identity = active.stat().st_ino

            def deactivate():
                require(not active.is_symlink() and active.stat().st_ino == identity
                        and active.read_bytes() == original,
                        "The active capability changed. Retained for inspection after stopping the owned processes.")
                active.unlink()

            return deactivate

        env = {"PATH": "/usr/bin:/bin", "HOME": str(Path.home()), "LANG": "C.UTF-8",
               "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}",
               "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{os.getuid()}/bus",
               "PYTHONPATH": f"{stage/'backend'}:{stage/'scripts'}", "PYTHONDONTWRITEBYTECODE": "1",
               "TMPDIR": str(scratch), "WORKBENCH_DB": str(database), "WORKBENCH_PLANNER": "local",
               "WORKBENCH_PLANNING_DEADLINE_S": "120", "WORKBENCH_RESEARCH_ROOT": str(fixture),
               "WORKBENCH_PREPARATION_CONFIG": str(database.parent / "preparation-policy.json"),
               "WORKBENCH_STATIC_ROOT": str(stage / "frontend/dist")}
        worker_env = {**env, "WORKBENCH_LOCAL_KEY": manifest["local_key_path"]}
        unit = "workbench-browser-demo-" + evidence.name
        command = [
            "/usr/bin/systemd-run", "--expand-environment=no", "--user", "--wait", "--pipe",
            "--collect", "--unit=" + unit, "--property=Delegate=yes", "--property=MemoryMax=6G",
            "--property=MemorySwapMax=0", "--property=CPUQuota=200%", "--property=TasksMax=128",
            "--property=TimeoutStopSec=15",
            "--property=IPAddressDeny=any", "--property=IPAddressAllow=localhost",
            "--working-directory=" + str(stage), "/usr/bin/env", "-i",
            *(f"{key}={value}" for key, value in worker_env.items()), "/bin/bash",
            str(stage / ".local/delegated-worker.sh"), unit, str(evidence),
        ]

        def stop_worker(proc):
            if proc.poll() is None:
                subprocess.run(["/usr/bin/systemctl", "--user", "stop", unit + ".service"],
                               env=env, check=True, timeout=25)
                proc.wait(timeout=20)

        run_owned(stage=stage, database=database, evidence=evidence, port=9187, env=env, deadline=deadline,
                  room=room, stopping=lambda: stopped, activate=activate, worker_command=command,
                  stop_worker=stop_worker, verify_worker=lambda _: (evidence / "worker-ready.txt").is_file(),
                  task=task, budget_reserve_minutes=reserve)


if __name__ == "__main__":
    os.umask(0o077)
    run(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
