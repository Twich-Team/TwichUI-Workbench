"""Standing read-only quarantine: immutable history, separate receipts, fresh work only."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
from uuid import UUID

from workbench.planning_reconciliation import rows, require
from workbench.planning_response import strict_json
from workbench.visual_scope import digest

ROOT = Path(__file__).resolve().parents[2]
RECEIPTS = ROOT.parent / "artifacts" / "acceptance"
EVIDENCE = ROOT.parent / "artifacts" / "codex-evidence"
AUTHORITY = "15a89f2d817fb9804b2f2bde99803f87a0760d042e90426bbe1096d2a2bdefbf"
HISTORICAL = "eb72eb02-c968-4c57-b51a-5106bf7e6236"
HISTORICAL_BINDING = "3a8fade7ab2c54fd601c5c389bb9c6425439928ebf26ed1c943388cac7adea8f"
HISTORICAL_ROOT = Path("/home/rich/TwichUI-Workbench/.local/m5-review/everyday-outcome-52c54af")
CAMPAIGN = Path("/home/rich/.local/share/TwichUI-Workbench/delivery-campaign-7b5963a")


def capabilities():
    """Code-owned actual transport contract; never supplied by task/model text."""
    from workbench.codex_provider import FEATURES_OFF, command

    argv = command("/pinned-cli", "/readonly-auth")
    require(
        all(
            x in argv for x in ("--unshare-all", "--clearenv", "--cap-drop", "--ro-bind", "--tmpfs")
        )
        and "--disable" in argv
        and "shell_tool" in FEATURES_OFF
        and "multi_agent" in FEATURES_OFF
        and "code_mode_host" in FEATURES_OFF,
        "Tool-free process isolation is unavailable",
    )
    return dict(
        kind="tool-free-codex-investigation-v1",
        source_writes=False,
        execution_dispatch=False,
        external_action_tools=False,
        empty_work_directory=True,
        owner_config_mounted=False,
        shell_disabled=True,
        sandbox="readOnly",
        approval_policy="never",
        code={
            p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest()
            for p in (
                "backend/workbench/codex_provider.py",
                "scripts/codex_connector.py",
                "backend/workbench/regression_preparation.py",
            )
        },
    )


def private_json(path, bound=65536, lines=False):
    info = path.lstat()
    require(
        path.resolve() == path
        and stat.S_ISREG(info.st_mode)
        and info.st_uid == os.getuid()
        and not info.st_mode & 0o077
        and info.st_size <= bound,
        "Recovery evidence is unsafe",
    )
    return (
        [strict_json(line) for line in path.read_text().splitlines()]
        if lines
        else strict_json(path.read_text())
    )


def historical_proof(binding):
    """Audited immutable deployed code, not inference from absence of a write."""
    require(binding == HISTORICAL_BINDING, "Historical incident changed")
    files = {
        "backend/workbench/codex_provider.py": "1d75cd497822e0cf041f954cb7f854d6efa438efe256ccead7766a956645f104",
        "scripts/codex_connector.py": "9a955def9e000847a7dd5a9b7e1589dbb6ede14838e1f77a402b10348eceb446",
        "backend/workbench/regression_preparation.py": "97cdfad6b01028cccc848c9d05bfcc9f1e34ae877ac6e40ac27ee6cf0280e0b0",
    }
    for path, sha in files.items():
        p = HISTORICAL_ROOT / "release" / path
        require(
            p.resolve() == p and hashlib.sha256(p.read_bytes()).hexdigest() == sha,
            "Audited historical isolation code changed",
        )
    evidence = {}
    for name in ("thread-read-offline-diagnostic.json", "thread-resume-offline-diagnostic.json"):
        p = CAMPAIGN / "standing-recovery-history-proof" / name
        record = private_json(p)
        require(
            record.get("network_disabled") is True
            and record.get("no_provider_turn") is True
            and record.get("no_source_transmission") is True,
            "Supported recovery proof unavailable",
        )
        evidence[name] = hashlib.sha256(p.read_bytes()).hexdigest()
    return dict(kind="audited-historical-tool-free-v1", code=files, recovery=evidence)


def state(connection, identity):
    require(str(UUID(identity)) == identity, "Invalid investigation identity")
    authority = ROOT / "docs/OWNER_READONLY_RECOVERY_20261108.md"
    require(
        hashlib.sha256(authority.read_bytes()).hexdigest() == AUTHORITY,
        "Standing recovery authority unavailable",
    )
    found = rows(connection, "SELECT * FROM model_attempts WHERE id=?", (identity,))
    require(len(found) == 1, "Investigation is unavailable")
    a = found[0]
    require(
        a["source"] == "workbench"
        and a["backend"] == "codex"
        and a["purpose"] == "planning"
        and a["outcome"] == "interrupted_unknown"
        and a["finished_at"]
        and a["usage"] in (None, "null"),
        "Not an uncertain read-only investigation",
    )
    [j] = rows(connection, "SELECT * FROM planning_jobs WHERE id=?", (a["job_id"],))
    [i] = rows(connection, "SELECT * FROM intakes WHERE id=?", (a["task_id"],))
    require(
        j["kind"] == "planning"
        and j["status"] == i["status"] == "failed"
        and j["lease"] is None
        and j["intake_id"] == i["id"],
        "Old task is not permanently fenced",
    )
    require(
        not rows(connection, "SELECT id FROM proposals WHERE intake_id=?", (i["id"],))
        and not rows(connection, "SELECT id FROM proposal_decisions WHERE intake_id=?", (i["id"],))
        and len(rows(connection, "SELECT id FROM model_attempts WHERE task_id=?", (i["id"],))) == 1,
        "Plan, approval or execution prevents read-only quarantine",
    )
    binding = digest(dict(attempt=a, job=j, intake=i))
    payload = json.loads(j["payload"])
    diag = payload.get("diagnostics", {})
    known = strict_json(diag.get("known_usage", "{}"))
    require(
        isinstance(known, dict) and all(type(v) is int and v >= 0 for v in known.values()),
        "Known usage malformed",
    )
    count = diag.get("investigation_requests", 0)
    require(type(count) is int and 0 <= count <= 8, "Investigation accounting unavailable")
    if identity == HISTORICAL:
        proof = historical_proof(binding)
        evidence_root = HISTORICAL_ROOT / "artifacts/codex-evidence"
    else:
        proof = payload.get("recovery_capabilities")
        require(
            isinstance(proof, dict)
            and {k: v for k, v in proof.items() if k != "code"}
            == {k: v for k, v in capabilities().items() if k != "code"}
            and isinstance(proof.get("code"), dict)
            and len(proof["code"]) == 3
            and proof["code"].keys() == capabilities()["code"].keys()
            and all(re.fullmatch(r"[a-f0-9]{64}", v) for v in proof["code"].values()),
            "Actual planning capabilities were not retained",
        )
        evidence_root = EVIDENCE
    receipt = diag.get("provider_receipt")
    require(
        isinstance(receipt, str) and str(UUID(receipt)) == receipt,
        "Provider recovery identity unavailable",
    )
    p = evidence_root / (receipt + ".jsonl")
    events = private_json(p, lines=True)
    phases = {e.get("phase") for e in events}
    if identity != HISTORICAL:
        require(
            any(
                e.get("phase") == "isolation-verified" and e.get("capabilities") == proof
                for e in events
            )
            and "connection-closed" in phases
            and "status-retrieval" in phases,
            "Provider isolation or supported recovery is unverified",
        )
    require(
        any(e.get("phase") in {"turn-started", "lifecycle-turn"} for e in events)
        and "turn-held" in phases,
        "Turn lifecycle evidence unavailable",
    )
    return dict(
        kind="standing-read-only-quarantine-v1",
        attempt=identity,
        intake=i["id"],
        job=j["id"],
        historical_binding=binding,
        authority_sha256=AUTHORITY,
        capability_proof=proof,
        provider_evidence_sha256=hashlib.sha256(p.read_bytes()).hexdigest(),
        requests_started=count + 1,
        known_completed_requests=count,
        known_completed_values=known,
        unknown_final_requests=1,
        final_outcome="unknown",
        final_usage=None,
        partial_output_reusable=False,
        successful_cancellation=False,
        permits_fresh_task=True,
        old_task_fenced=True,
    )


def path_for(identity):
    require(str(UUID(identity)) == identity, "Invalid investigation identity")
    require(
        RECEIPTS.resolve() == RECEIPTS
        and RECEIPTS.is_dir()
        and RECEIPTS.stat().st_uid == os.getuid()
        and not RECEIPTS.stat().st_mode & 0o077,
        "Recovery directory must be private",
    )
    return RECEIPTS / ("readonly-quarantine-" + identity + ".json")


def accounting(connection, identity):
    expected = state(connection, identity)
    actual = private_json(path_for(identity), 16384)
    at = actual.pop("recorded_at", None)
    require(
        isinstance(at, str)
        and datetime.fromisoformat(at).tzinfo is not None
        and actual == expected,
        "Quarantine receipt does not match immutable historical records",
    )
    return expected


def record(connection, identity):
    from workbench.codex_quarantine import local_generation_absent

    require(local_generation_absent(), "Owned local generation is still present")
    expected = state(connection, identity)
    path = path_for(identity)
    if os.path.lexists(path):
        return accounting(connection, identity)
    raw = (
        json.dumps(
            dict(expected, recorded_at=datetime.now(timezone.utc).isoformat()), sort_keys=True
        )
        + "\n"
    )
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as out:
        out.write(raw)
        out.flush()
        os.fsync(out.fileno())
    directory = os.open(RECEIPTS, os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return accounting(connection, identity)


def recover_one(engine):
    """Automatic disposition only. Never starts a model, retries a job or grants approval."""
    if not RECEIPTS.is_dir():
        return False
    with engine.connect() as c:
        candidates = rows(
            c,
            "SELECT id FROM model_attempts WHERE backend='codex' AND purpose='planning' AND outcome='interrupted_unknown' ORDER BY started_at",
        )
        for item in candidates:
            try:
                if os.path.lexists(path_for(item["id"])):
                    continue
                record(c, item["id"])
            except ValueError, OSError, KeyError, TypeError:
                continue
            return True
    return False


def no_generation_accounting(connection, identity):
    """A planning claim is not a model turn. Never reinterpret an unknown turn as zero."""
    [a] = rows(connection, "SELECT * FROM model_attempts WHERE id=?", (identity,))
    [j] = rows(connection, "SELECT * FROM planning_jobs WHERE id=?", (a["job_id"],))
    [i] = rows(connection, "SELECT * FROM intakes WHERE id=?", (a["task_id"],))
    require(
        a["source"] == "workbench"
        and a["backend"] == "codex"
        and a["purpose"] == "planning"
        and a["finished_at"]
        and j["status"] == "failed"
        and j["lease"] is None,
        "Not a stopped pre-generation claim",
    )
    payload = strict_json(j["payload"])
    zero = dict(prompt_tokens=0, completion_tokens=0, reasoning_tokens=0, total_tokens=0)
    if a["usage"] not in (None, "null"):
        usage = strict_json(a["usage"])
        require(
            usage.get("status") == "not_started"
            and usage.get("units") == "tokens"
            and type(usage.get("requests_observed")) is int
            and usage["requests_observed"] == 0
            and usage.get("values") == zero
            and all(type(value) is int for value in usage['values'].values())
            and payload.get("diagnostics", {}).get("generation_state") == "not_started"
            and a["outcome"] != "interrupted_unknown",
            "No verified zero-dispatch accounting",
        )
    else:
        # One audited local context failure predates the explicit nonstart record.
        # Preserve its NULL historical usage; expose the independently verified
        # application fact separately. It never reached the provider loop.
        require(
            identity == "8a477e53-0a61-4995-922e-d389d3d679b8"
            and digest(dict(attempt=a, job=j, intake=i))
            == "97d63357fce5316c342837aea6d47d6edd440295fd280f98c2238ac61af5b18d"
            and a["outcome"] == "failed"
            and payload.get("diagnostics", {}).get("category") == "context_budget",
            "Historical generation absence is not established",
        )
        stage = HISTORICAL_ROOT.parent / "everyday-outcome-0cb1432"
        for path, sha in {
            "backend/workbench/bug_source_planning.py": "63d02f6a36a36d7b47ee90699dbf5e8693281c78c647005b12d20c7fdf538dc3",
            "backend/workbench/source_memory.py": "44a075573a15b697d85e3f42ac91d55a4d80323b4839d742490ef193d5c2f462",
            "backend/workbench/regression_preparation.py": "8596888014aec227db43d65f0557e5daccf00cd12907d5fb5763848f862a573e",
        }.items():
            require(
                hashlib.sha256((stage / "release" / path).read_bytes()).hexdigest() == sha,
                "Historical pre-dispatch code changed",
            )
        evidence = (
            stage / "release/artifacts/runs" / ("planning-evidence-" + identity) / "events.jsonl"
        )
        events = private_json(evidence, lines=True)
        require(
            not any(
                e.get("phase") in {"request-schema", "provider-result", "request-stopped"}
                for e in events
            )
            and not list((stage / "artifacts/codex-evidence").glob("*.jsonl")),
            "Historical provider activity is not absent",
        )
    return dict(
        kind="verified-no-generation-v1",
        requests_started=0,
        known_completed_values=zero,
        historical_binding=digest(dict(attempt=a, job=j, intake=i)),
        original_records_unchanged=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("attempt")
    args = parser.parse_args()
    db = args.database.absolute()
    require(
        db.resolve() == db and db.stat().st_uid == os.getuid() and not db.stat().st_mode & 0o077,
        "Private owned history required",
    )
    with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True) as c:
        c.execute("BEGIN")
        print(json.dumps(record(c, args.attempt), sort_keys=True))


if __name__ == "__main__":
    main()
