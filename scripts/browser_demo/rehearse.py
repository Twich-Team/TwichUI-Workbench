"""Exercise the shared browser supervisor and worker using only scripted responses."""

from contextlib import closing, contextmanager
from datetime import timedelta
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError

from browser_demo.launch import api_client, run_owned, stop_process, utc_now
from browser_demo.gate import SYNTHETIC_TASK
from browser_demo.supervisor import RunHeld
from project_env import ARTIFACT_LIMIT, ROOT, artifact_bytes, environment, require_ports
from reviewed_offer_fixture import setup
from test_grounded_workflow import policy_file
from workbench.db import engine_for

PORT = 9387
ORIGIN = f"http://127.0.0.1:{PORT}"
APP = "browser_demo.offline_app:create_app"
REVIEW_APP = "browser_demo.readonly_review:create_app"
PYTHON = ROOT / ".venv/bin/python"
CASES = {
    "pass": ("checked", ""),
    "addon": ("checked", ""),
    "incomplete": ("failure", "Planning failed"),
    "blind": ("failure", "Planning interrupted"),
    "ledger": ("failure", "Planning interrupted"),
    "capacity": ("failure", "Planning interrupted"),
    "publication": ("failure", "Planning interrupted"),
    "deadline": ("failure", "Planning interrupted"),
    "crash": ("failure", "Planning interrupted"),
    "accounting": ("ready", ""),
    "isolation": ("none", ""),
}


@contextmanager
def store(path):
    with closing(sqlite3.connect(path)) as connection, connection:
        yield connection


def inspect(evidence, mode, identity, label, env):
    return subprocess.run(["node", str(Path(__file__).with_name("review.cjs")), ORIGIN, str(evidence),
                           mode, identity, label], env=environment() | {"TMPDIR": env["TMPDIR"]},
                          cwd=ROOT, check=True, timeout=40)


def wait_api(process, request):
    for _ in range(60):
        assert process.poll() is None
        try:
            request("/api/session")
            return
        except URLError:
            time.sleep(.1)
    raise RuntimeError("Owned rehearsal review did not start")


def run(case, source, backup, output, task=SYNTHETIC_TASK):
    mode, label = CASES[case]
    root = output / case
    root.mkdir(mode=0o700)
    (root / "rehearsal-task.json").write_text(json.dumps(task))
    work = root / "store"
    work.mkdir(mode=0o700)
    database = work / "review.sqlite3"
    shutil.copyfile(backup, database)
    setup(database)
    runtime_root = work / "controlled-session-owner"
    capability = runtime_root / ".local/task-runtime.json"
    pending = capability.with_suffix(".pending")
    capability.rename(pending)
    scratch = Path(tempfile.mkdtemp(prefix="wb-browser-rehearsal-"))
    (root / "scratch-location.txt").write_text(str(scratch) + "\n")
    (root / "inert-key").write_text("inert")
    engine = engine_for(database)
    policy, _ = policy_file(engine, work)
    engine.dispose()
    env = {"PATH": environment()["PATH"], "LANG": "C.UTF-8", "TMPDIR": str(scratch),
           "PYTHONPATH": ":".join(str(ROOT / p) for p in ("backend", "backend/tests", "scripts")),
           "PYTHONDONTWRITEBYTECODE": "1", "WORKBENCH_DB": str(database),
           "WORKBENCH_STATIC_ROOT": str(ROOT / "frontend/dist"),
           "WORKBENCH_RESEARCH_ROOT": str(source), "WORKBENCH_PREPARATION_CONFIG": str(policy),
           "WORKBENCH_PLANNER": "local", "WORKBENCH_LOCAL_KEY": str(root / "inert-key"),
           "WB_REHEARSAL_CASE": str(root), "WB_REHEARSAL_OUTCOME": case}
    worker_command = [str(PYTHON), "-m", "browser_demo.offline_app"]
    stop_worker = stop_process
    if case == "isolation":
        worker_command = [str(PYTHON), "-c", "raise SystemExit(17)"]
    if case == "addon":
        unit = "workbench-real-rehearsal-" + output.name.lower()
        env.update({key: os.environ[key] for key in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS")
                    if key in os.environ})
        worker_command = [
            "/usr/bin/systemd-run", "--expand-environment=no", "--user", "--wait", "--pipe",
            "--collect", "--unit=" + unit, "--property=Delegate=yes", "--property=MemoryMax=6G",
            "--property=MemorySwapMax=0", "--property=CPUQuota=200%", "--property=TasksMax=128",
            "--property=IPAddressDeny=any", "--property=IPAddressAllow=localhost",
            "--working-directory=" + str(ROOT), "/usr/bin/env", "-i",
            *(f"{key}={value}" for key, value in env.items()), "WB_REHEARSAL_UNIT=" + unit, *worker_command,
        ]

        def stop_worker(process):
            if process.poll() is None:
                subprocess.run(["/usr/bin/systemctl", "--user", "stop", unit + ".service"],
                               env=env, check=True, timeout=25)
                process.wait(timeout=20)
    request = api_client(ORIGIN)
    deadline = utc_now() + timedelta(minutes=90)
    observation = False
    simulated_late = False
    blind_reads = 0

    def now():
        if simulated_late:
            return deadline
        return utc_now() + timedelta(seconds=151 if blind_reads >= 2 else 0)

    def activate():
        assert not capability.exists()
        pending.rename(capability)
        return lambda: capability.rename(pending)

    def room(_reserve):
        if case == "capacity" and observation:
            raise RunHeld("Evidence capacity is below the approved reserve.")
        if case == "addon":
            counted = artifact_bytes()
            try:
                scratch_bytes = sum(p.stat().st_size for p in scratch.rglob("*")
                                    if p.is_file() and not p.is_symlink())
            except FileNotFoundError:
                # A browser can unlink files during measurement. The next
                # supervisor tick rechecks while Runtime owns its own limit.
                scratch_bytes = None
            if counted >= ARTIFACT_LIMIT or (scratch_bytes is not None and scratch_bytes > 1 << 30):
                raise RunHeld("Rehearsal exceeded its evidence or scratch budget.")
            return {"offline": True, "counted": counted, "scratch": scratch_bytes}
        return {"offline": True}

    def wrap(send):
        def read(path, data=None, csrf=None):
            nonlocal observation, simulated_late, blind_reads
            if (path.startswith("/api/intake/") and data is None and not observation
                    and (root / "planning-started").exists()):
                assert send(path)["status"] == "investigating", case
                observation = True
                if case == "ledger":
                    with store(database) as connection:
                        connection.execute("ALTER TABLE fake_publications RENAME TO held_publications")
                if case == "publication":
                    with store(database) as connection:
                        # Deliberate sentinel in the disposable sample table. No publisher runs.
                        connection.execute("INSERT INTO fake_publications VALUES ('unexpected','sentinel','{}','hash','now')")
                if case == "deadline":
                    simulated_late = True
                if case in {"pass", "addon", "incomplete", "accounting"}:
                    (root / "return-response").touch()
                if case == "pass":
                    with store(database) as connection:
                        # The actual historical SQLite error, in an optional
                        # observation. Independent safety reads still execute.
                        connection.execute("SELECT id FROM fake_publications").fetchall()
            if case == "blind" and observation and path.startswith("/api/intake/"):
                blind_reads += 1
                raise TimeoutError("Controlled status timeout")
            return send(path, data, csrf)
        return read

    owner = None
    with (root / "owner-browser.log").open("x") as log:
        if case in {"pass", "addon"}:
            owner = subprocess.Popen(["node", str(Path(__file__).with_name("review.cjs")),
                ORIGIN, str(root), "approve"], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                env=environment() | {"TMPDIR": str(scratch)})
        try:
            run_owned(stage=ROOT, database=database, evidence=root, port=PORT, env=env,
                      deadline=deadline, room=room, stopping=lambda: False, activate=activate,
                      worker_command=worker_command, stop_worker=stop_worker,
                      verify_worker=lambda _: (root / "worker-ready").is_file(),
                      api_app=APP, now=now, request_wrapper=wrap, runtime_root=runtime_root, task=task)
            assert case in {"pass", "addon"}
            assert owner.wait(timeout=10) == 0
        except RunHeld as error:
            expected = dict(incomplete="reviewable proposal", capacity="capacity", publication="publication",
                            deadline="deadline", crash="worker stopped", accounting="token record",
                            isolation="isolation and readiness", blind="planning deadline",
                            ledger="limits could not be verified")
            assert case in expected and expected[case] in str(error), (case, str(error))
        finally:
            if owner is not None:
                stop_process(owner)

    assert not capability.exists() and pending.is_file()
    assert json.loads((root / "ingress-phase.json").read_text())["state"] == "read-only"
    require_ports([PORT])
    if case == "isolation":
        assert not (root / "ingress-submit.json").exists()
        assert not (root / "scripted-calls.jsonl").exists()
        return

    # A restarted worker reconciles only durable evidence. Even hard death
    # during the response leaves one attempt and no second transport call.
    if case == "ledger":
        with store(database) as connection:
            connection.execute("ALTER TABLE held_publications RENAME TO fake_publications")
    if case not in {"pass", "addon"}:
        with (root / "recovery-worker.log").open("x") as log:
            subprocess.run([str(PYTHON), "-m", "browser_demo.offline_app"],
                           env=env | {"WB_REHEARSAL_RECOVERY": "1"}, cwd=ROOT, check=True,
                           stdout=log, stderr=subprocess.STDOUT, timeout=10)
    calls = (root / "scripted-calls.jsonl").read_text().splitlines()
    assert len(calls) == 1
    with store(database) as c:
        intake, = c.execute("SELECT id FROM intakes").fetchone()
        attempts = c.execute("SELECT outcome,usage FROM model_attempts WHERE purpose='planning'").fetchall()
        assert len(attempts) == 1
        expected = ("completed" if case in {"pass", "addon", "accounting"} else
                    "failed" if case == "incomplete" else "interrupted_unknown")
        assert attempts[0][0] == expected, (case, attempts)
        if case == "incomplete":
            assert json.loads(attempts[0][1])["values"]["total_tokens"] == 120
        counts = {table: c.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in (
            "proposal_decisions", "task_sessions", "real_execution_requests",
            "real_execution_grants", "real_publications", "fake_publications")}
        assert tuple(counts.values()) == ((2, 1, 1, 1, 0, 0) if case in {"pass", "addon"} else
                                         (0, 0, 0, 0, 0, int(case == "publication"))), counts
    if case == "pass":
        events = [json.loads(line) for line in (root / "supervisor-observations.jsonl").read_text().splitlines()]
        assert [(e["observation"], e["state"]) for e in events] == [
            ("intake status", "unavailable"), ("intake status", "recovered")]
        assert events[0]["error"] == "OperationalError"

    # Reopen only the read-only API on the same store and inspect the saved
    # outcome. Browser refresh and a denied retry cannot enqueue new work.
    with (root / "review-api.log").open("x") as log:
        api = subprocess.Popen([str(PYTHON), "-m", "uvicorn", REVIEW_APP, "--factory", "--host", "127.0.0.1",
                                "--port", str(PORT), "--no-proxy-headers"], cwd=ROOT,
                               env=env | {"WORKBENCH_DEMO_EVIDENCE": str(root)}, stdout=log, stderr=subprocess.STDOUT)
        try:
            wait_api(api, request)
            view = request("/api/intake/" + intake)
            if mode == "failure":
                assert view["status"] == "failed" and not view["proposals"] and view["result"] is None
            csrf = request("/api/session")["csrf_token"]
            try:
                request(f"/api/intake/{intake}/retry", {"job_id": view["jobs"][0]["id"]}, csrf)
                raise AssertionError("The read-only gate accepted a retry")
            except HTTPError as error:
                assert error.code == 405
            inspect(root, mode, intake, label, env)
        finally:
            stop_process(api)
    require_ports([PORT])
    (root / "closeout.json").write_text(json.dumps(dict(
        counts=counts, attempt=expected, scripted_planning_calls=1,
        capability_inactive=True, owned_api_and_worker_stopped=True), indent=2))
    shutil.rmtree(scratch)


if __name__ == "__main__":
    os.umask(0o077)
    source, backup, output = (Path(arg).resolve() for arg in sys.argv[1:4])
    assert output.is_dir() and not list(output.iterdir())
    for case in sys.argv[4:] or [case for case in CASES if case != "addon"]:
        run(case, source, backup, output)
        print(case + " passed", flush=True)
