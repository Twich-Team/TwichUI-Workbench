"""One owner-approved historical exception; never changes provider history or usage."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat

from workbench.planning_reconciliation import rows, require
from workbench.planning_response import strict_json
from workbench.real_execution import alive
from workbench.visual_scope import digest

ATTEMPT = "18bbf5d0-c5e2-4557-936b-126b24b4f1f4"
BINDING = "10603e4e65758a2e4efe36438ef1f744b025040d34e1a9bbe6c81fd057e94a2d"
AUTHORITY_SHA256 = "6ac363082930d623c1b4858625949006684a92b27a7e96b1265e1b41e0d30147"
ROOT = Path(__file__).resolve().parents[2]
RECEIPTS = ROOT.parent / "artifacts" / "acceptance"
KIND = "owner-quarantined-codex-turn-v1"


def state(connection, identity):
    require(identity == ATTEMPT, "No quarantine exception covers this attempt")
    authority = ROOT / "docs" / "OWNER_CODEX_QUARANTINE_20261108.md"
    require(
        hashlib.sha256(authority.read_bytes()).hexdigest() == AUTHORITY_SHA256,
        "The exact owner quarantine decision is unavailable",
    )
    found = rows(connection, "SELECT * FROM model_attempts WHERE id=?", (identity,))
    require(len(found) == 1, "Historical Codex attempt is unavailable")
    a = found[0]
    [j] = rows(connection, "SELECT * FROM planning_jobs WHERE id=?", (a["job_id"],))
    [i] = rows(connection, "SELECT * FROM intakes WHERE id=?", (a["task_id"],))
    require(
        digest(dict(attempt=a, job=j, intake=i)) == BINDING,
        "The historical records changed; quarantine must not overwrite them",
    )
    payload = json.loads(j["payload"])
    require(
        a["backend"] == "codex"
        and a["purpose"] == "planning"
        and a["outcome"] == "interrupted_unknown"
        and a["finished_at"]
        and a["usage"] in (None, "null")
        and j["status"] == i["status"] == "failed"
        and not alive(payload["scenario_generation_claim"]),
        "Historical planning is not quiescent",
    )
    require(
        not rows(connection, "SELECT id FROM proposals WHERE intake_id=?", (i["id"],))
        and not rows(connection, "SELECT id FROM proposal_decisions WHERE intake_id=?", (i["id"],))
        and len(rows(connection, "SELECT id FROM model_attempts WHERE task_id=?", (i["id"],))) == 1,
        "A plan, approval or another execution prevents this quarantine",
    )
    known = json.loads(payload["diagnostics"]["known_usage"])
    require(
        payload["diagnostics"]["generation_state"] == "uncertain"
        and payload["diagnostics"]["investigation_requests"] == 1
        and known
        == dict(
            prompt_tokens=31352, completion_tokens=157, reasoning_tokens=19, total_tokens=31509
        ),
        "Historical observed accounting differs",
    )
    return dict(
        kind=KIND,
        attempt=identity,
        intake=i["id"],
        job=j["id"],
        historical_binding=BINDING,
        authority_sha256=AUTHORITY_SHA256,
        quarantined_turn=2,
        requests_started=2,
        known_completed_requests=1,
        known_completed_values=known,
        unknown_final_requests=1,
        final_outcome="unknown",
        final_usage=None,
        partial_output_reusable=False,
        successful_cancellation=False,
        permits_fresh_task=True,
    )


def receipt_path(identity):
    require(identity == ATTEMPT, "No quarantine exception covers this attempt")
    require(
        RECEIPTS.resolve() == RECEIPTS
        and RECEIPTS.is_dir()
        and RECEIPTS.stat().st_uid == os.getuid()
        and not RECEIPTS.stat().st_mode & 0o077,
        "Quarantine receipts require a private owned directory",
    )
    return RECEIPTS / ("quarantined-codex-" + identity + ".json")


def accounting(connection, identity):
    expected = state(connection, identity)
    path = receipt_path(identity)
    info = path.lstat()
    require(
        path.resolve() == path
        and stat.S_ISREG(info.st_mode)
        and info.st_uid == os.getuid()
        and not info.st_mode & 0o077
        and info.st_size <= 8192,
        "Quarantine receipt is unsafe",
    )
    actual = strict_json(path.read_text())
    recorded = actual.pop("recorded_at", None)
    require(
        isinstance(recorded, str)
        and datetime.fromisoformat(recorded).tzinfo is not None
        and actual == expected,
        "Quarantine receipt differs from the exact owner decision",
    )
    return expected


def local_generation_absent():
    group = Path("/sys/fs/cgroup/system.slice/workbench-everyday-codex.service")
    if not group.exists():
        return True
    pids = set()
    for p in group.rglob("cgroup.procs"):
        pids.update(p.read_text().split())
    return len(pids) <= 1 and all(
        not Path("/proc/" + p + "/task/" + p + "/children").read_text().strip() for p in pids
    )


def record(connection):
    expected = state(connection, ATTEMPT)
    path = receipt_path(ATTEMPT)
    if os.path.lexists(path):
        return accounting(connection, ATTEMPT)
    # This checks only local process quiescence; it never claims a remote terminal receipt.
    require(local_generation_absent(), "Owned Codex execution is still present")
    raw = (
        json.dumps(
            dict(expected, recorded_at=datetime.now(timezone.utc).isoformat()), sort_keys=True
        )
        + "\n"
    ).encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(raw)
            out.flush()
            os.fsync(out.fileno())
        parent = os.open(RECEIPTS, os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    except BaseException:
        # Preserve any partial receipt for diagnosis; a reader will reject it.
        raise
    return accounting(connection, ATTEMPT)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    path = args.database.absolute()
    require(
        path.resolve() == path
        and path.stat().st_uid == os.getuid()
        and not path.stat().st_mode & 0o077,
        "History must be private and owned",
    )
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
        connection.execute("BEGIN")
        print(json.dumps(record(connection), sort_keys=True))


if __name__ == "__main__":
    main()
