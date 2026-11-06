"""Activate a staged, approved browser demonstration on the isolated loopback port."""

from contextlib import contextmanager
from datetime import datetime, timedelta
from hashlib import sha256
from hmac import compare_digest
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import runpy

from browser_demo.launch import run_owned, utc_now
from browser_demo import stage as layout
from browser_demo.supervisor import RESERVE, RunHeld
from project_env import ARTIFACT_LIMIT, artifact_bytes, require_ports

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
    require(Path(identity["config_path"]).stat().st_mode & 0o777 == identity["config_mode"],
            "The installed model configuration permissions changed.")
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


def preflight(manifest_path, fingerprint):
    stage = manifest_path.parent
    require(manifest_path == stage / layout.MANIFEST and layout.ROOT == stage,
            "Use the supervisor from the tested staged source.")
    manifest, config = layout.preflight(stage, fingerprint)
    fixture = Path(manifest["fixture"])
    git = ["git", "--no-optional-locks", "-C", str(fixture)]
    require(fixture.resolve() == fixture
            and not subprocess.check_output(git + ["status", "--porcelain=v1", "--ignored"])
            and subprocess.check_output(git + ["branch", "--show-current"]).strip() == b"main"
            and subprocess.check_output(git + ["rev-parse", "HEAD"]).decode().strip() == manifest["fixture_base"],
            "The fixture checkout, branch or base changed.")
    if manifest["mode"] == "live":
        verify_model_installation(stage, manifest, config)
    else:
        require(manifest["mode"] == "offline", "Unknown stage mode")
        checked_limit = runpy.run_path(str(stage / "scripts/chronicle_lease.py"))["checked_limit"]
        require(checked_limit((stage / layout.PROOF).parent) == config["total_limit_proof_sha256"],
                "The installed generation limit proof no longer matches.")
    return manifest


def run(manifest_path, evidence, fingerprint, *, rehearsal=False):
    manifest = preflight(manifest_path, fingerprint)
    require(manifest["mode"] == ("offline" if rehearsal else "live"), "Stage mode does not match launcher")
    task = manifest["task"]
    stage = manifest_path.parent
    require(evidence.is_dir() and evidence.stat().st_uid == os.getuid() and evidence.stat().st_mode & 0o077 == 0,
            "Use a new private owned evidence directory.")
    port = 9387 if rehearsal else 9187
    require_ports([port])
    window = json.loads((evidence / "window.json").read_text())
    start = datetime.fromisoformat(window["start"])
    minutes, reserve, review = layout.window_limits(manifest)
    deadline = start + timedelta(minutes=minutes)
    if review:
        require(datetime.fromisoformat(window["deadline"]) == deadline
                and datetime.fromisoformat(window["review_cutoff"]) == deadline - timedelta(minutes=reserve),
                "Announced deadlines do not match the frozen trial window.")
    require(start <= utc_now() < deadline - timedelta(minutes=reserve), "The authorized setup window is unavailable.")
    require(not (evidence / "ingress-phase.json").exists(), "This evidence directory already owns a run.")
    database = stage / layout.DATABASE
    pending = stage / layout.PENDING
    active = stage / layout.ACTIVE
    require(not (evidence / "worker-ready.txt").exists(), "Use fresh worker evidence.")
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

        def activate():
            room(RESERVE)
            layout.sealed_inputs(stage, fingerprint, fresh=True)
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
               "TMPDIR": str(scratch), "WORKBENCH_PLANNER": "local",
               "WORKBENCH_PLANNING_DEADLINE_S": "120", layout.FINGERPRINT_ENV: fingerprint,
               **layout.inputs(stage, manifest)}
        worker_env = {**env, "WORKBENCH_LOCAL_KEY": manifest.get("local_key_path", str(evidence / "inert-key"))}
        if rehearsal:
            worker_env.update(WB_REHEARSAL_CASE=str(evidence), WB_REHEARSAL_OUTCOME=(
                "addon" if task["scenario"]["fixture"] == "loaded-client" else "pass"),
                WB_STAGED_REHEARSAL="1")
            worker_env["PYTHONPATH"] += f":{stage / 'backend/tests'}"
        unit = ("workbench-real-rehearsal-" if rehearsal else "workbench-browser-demo-") + evidence.name
        command = [
            "/usr/bin/systemd-run", "--expand-environment=no", "--user", "--wait", "--pipe",
            "--collect", "--unit=" + unit, "--property=Delegate=yes", "--property=MemoryMax=6G",
            "--property=MemorySwapMax=0", "--property=CPUQuota=200%", "--property=TasksMax=128",
            "--property=TimeoutStopSec=15",
            "--property=IPAddressDeny=any", "--property=IPAddressAllow=localhost",
            "--working-directory=" + str(stage), "/usr/bin/env", "-i",
            *(f"{key}={value}" for key, value in worker_env.items()), "/bin/bash",
            str(stage / layout.WORKER), unit, str(evidence),
            *(["offline"] if rehearsal else []),
        ]

        def stop_worker(proc):
            if proc.poll() is None:
                subprocess.run(["/usr/bin/systemctl", "--user", "stop", unit + ".service"],
                               env=env, check=True, timeout=25)
                proc.wait(timeout=20)

        run_owned(stage=stage, database=database, evidence=evidence, port=port, env=env, deadline=deadline,
                  room=room, stopping=lambda: stopped, activate=activate, worker_command=command,
                  stop_worker=stop_worker, verify_worker=lambda _: (evidence / "worker-ready.txt").is_file(),
                  task=task, budget_reserve_minutes=reserve, owner_review_minutes=review,
                  owner_submits=manifest.get("owner_submits", False))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("fingerprint")
    parser.add_argument("evidence", type=Path, nargs="?")
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    if args.preflight and args.evidence is None:
        verified = preflight(args.manifest.absolute(), args.fingerprint)
        print(f"Verified {len(verified['sha256'])} frozen inputs and dormant runtime and preparation policies.")
    elif not args.preflight and args.evidence:
        run(args.manifest.absolute(), args.evidence.absolute(), args.fingerprint)
    else:
        parser.error("Supply evidence for a live run, or --preflight for read-only verification.")
