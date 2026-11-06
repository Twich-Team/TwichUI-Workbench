"""Script the two browser decisions against a disposable stage and zero-wire worker."""

from datetime import timedelta
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

from browser_demo import live, stage
from browser_demo.launch import stop_process, utc_now
from project_env import environment, require_ports


def run(manifest, fingerprint, evidence):
    assert live.preflight(manifest, fingerprint)["mode"] == "offline"
    task = json.loads(manifest.read_text())["task"]
    require_ports([9387])
    evidence.mkdir(mode=0o700)
    (evidence / "rehearsal-task.json").write_text(json.dumps(task))
    (evidence / "inert-key").write_text("inert")
    start = utc_now()
    (evidence / "window.json").write_text(json.dumps(dict(start=start.isoformat(),
        review_cutoff=(start + timedelta(minutes=33)).isoformat(),
        deadline=(start + timedelta(minutes=105)).isoformat())))
    with (evidence / "owner-browser.log").open("x") as log:
        owner = subprocess.Popen(["node", str(stage.ROOT / "scripts/browser_demo/review.cjs"),
            "http://127.0.0.1:9387", str(evidence), "approve"], env=environment(),
            stdout=log, stderr=subprocess.STDOUT)
        try:
            live.run(manifest, evidence, fingerprint, rehearsal=True)
            assert owner.wait(timeout=10) == 0
        finally:
            stop_process(owner)
    require_ports([9387])
    stage.sealed_inputs(stage.ROOT, fingerprint)
    assert not (stage.ROOT / stage.ACTIVE).exists()
    assert not Path((evidence / "worker-ready.txt").read_text().strip()).exists()
    assert not (evidence / "unsettled-session.json").exists()
    database = stage.ROOT / stage.DATABASE
    with sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1", uri=True) as c:
        assert c.execute("SELECT status FROM real_execution_requests").fetchall() == [("checked",)]
        assert c.execute("SELECT status FROM task_sessions").fetchall() == [("completed",)]
        assert c.execute("SELECT count(*) FROM proposal_decisions").fetchone() == (2,)
        assert c.execute("SELECT count(*) FROM real_execution_grants WHERE revoked_at IS NULL").fetchone() == (0,)
        assert c.execute("SELECT count(*) FROM real_publications").fetchone() == (0,)
    assert len((evidence / "scripted-calls.jsonl").read_text().splitlines()) == 1
    scratch = Path((evidence / "scratch.txt").read_text().strip())
    if scratch.exists():
        shutil.rmtree(scratch)
    print("Staged browser run produced a checked simulated result with two fresh approvals.", flush=True)


if __name__ == "__main__":
    os.umask(0o077)
    run(Path(sys.argv[1]).absolute(), sys.argv[2], Path(sys.argv[3]).absolute())
