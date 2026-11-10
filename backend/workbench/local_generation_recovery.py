"""Discharge a local generation fence only after its owned server epoch is dead.

This is termination evidence, never a completion/cancellation or usage receipt.
No model request, tool dispatch, task approval or new execution is created here.
"""

import fcntl
import json
import os
from pathlib import Path

from sqlalchemy import select

from workbench import real_execution as execution, real_lease as lease
from workbench.real_primary import PrimarySession
from workbench.real_workspace import Runtime, durable, sha, tree
from workbench.repair_continuation import private_json


def absent_epoch(epoch):
    pid, start = epoch
    try:
        return (Path("/proc") / str(pid) / "stat").read_text().split(") ", 1)[1].split()[
            19
        ] != start
    except FileNotFoundError:
        return True


def normal_restored(private, state):
    """Check current configuration/access again, without exporting either key."""
    return (
        all(
            state.get(k) is True
            for k in (
                "restored",
                "original_config_verified",
                "original_access_verified",
                "temporary_access_rejected",
            )
        )
        and absent_epoch(state["temporary_epoch"])
        and lease.epoch() == state["restored_epoch"]
        and not lease.ENV.is_symlink()
        and lease.ENV.stat().st_uid == os.getuid()
        and lease.ENV.stat().st_mode & 0o777 == state["original_mode"]
        and lease.ENV.read_bytes() == (private / "serve.env.original").read_bytes()
        and sha(lease.UNIT.read_bytes()) == state["unit_sha256"]
        and lease.process_matches(state, (private / "original.key").read_text())
        and lease.request((private / "original.key").read_text(), "GET", "/props")[0] == 200
        and lease.request((private / "temporary.key").read_text(), "GET", "/props")[0] == 401
    )


def reconcile(engine, identity, *, expected_primary_sha256=None):
    """Trusted recovery only; prove the exact failed call cannot act again.

    The original partial stream, failure, source edits and unknown usage stay
    immutable. The normal worker subsequently delivers that sealed failure.
    """
    with engine.connect() as c:
        row = dict(
            c.execute(select(execution.requests).where(execution.requests.c.id == identity))
            .mappings()
            .one()
        )
        grant = dict(
            c.execute(select(execution.grants).where(execution.grants.c.id == row["grant_id"]))
            .mappings()
            .one()
        )
    if row['status']=='cancelled':
        from workbench.db import task_sessions,proposal_decisions
        from workbench.continuing_repair import engineering_stop
        with engine.connect() as c:
            decision=grant['snapshot']['task_budget']['decision_id']
            session=c.execute(select(task_sessions).where(task_sessions.c.id==decision)).mappings().one()
            approval=c.execute(select(proposal_decisions).where(proposal_decisions.c.id==decision)).mappings().one()
        if not engineering_stop(session,approval):
            raise ValueError('An owner-stopped operation cannot be revived by engineering recovery')
    elif row["status"] not in {"running", "checking", "failed", "interrupted"}:
        raise ValueError("Only a returned failed local operation may be reconciled")
    runtime = Runtime(grant["snapshot"]["recovery"]["root"])
    root = runtime.root
    if len(root.parents) < 4 or not root.parent.name.startswith("task-lease-task-session-"):
        raise ValueError("The exact application-owned local run directory is required")
    release = root.parents[3]
    folder = release / ".local"
    private = folder / ("chronicle-primary-lease-" + root.parent.name)
    primary = folder / "real-primary-session.json"
    proof_path = root / "terminated-generation.json"
    if (
        not runtime.owns_recovery(grant)
        or execution.digest(grant["snapshot"]) != grant["binding"]
        or grant["snapshot"]["contract"]["provider"] != "local"
        or grant["snapshot"]["runtime"].get("codex_selection")
    ):
        raise ValueError("Exact owned local failure evidence is required")
    owned = private_json(root / ("ownership-" + identity + ".json"))
    owner = row['result'].get('owner')
    # The normal terminal transaction stores the returned result without its
    # transient owner field. The immutable failure seal and process marker
    # still bind that identity. Recover it for proof inspection only; neither
    # replace the terminal row nor accept a different sealed result.
    terminal = owner is None and row['status']=='failed' and row['finished_at']
    proof_row = row
    if terminal:
        owner = owned['owner']
        proof_row = {**row, 'result': {**row['result'], 'owner': owner}}
    failure = runtime.recover_outcome(proof_row, grant)
    if not failure or (terminal and (failure['result'] != row['result']
            or failure['completed_at'] != row['finished_at'])):
        raise ValueError("Exact owned local failure evidence is required")
    for name in ("real_provider.py", "real_stream.py"):
        if (
            sha((release / "backend/workbench" / name).read_bytes())
            != grant["snapshot"]["runtime"]["helper_sha256"][name]
        ):
            raise ValueError("The original tool-dispatch implementation changed")
    group = Path(owned["cgroup"])
    if (
        owned["owner"] != owner
        or execution.alive(owned["owner"])
        or not group.is_relative_to("/sys/fs/cgroup")
        or not group.name.startswith("workbench-real-")
        or group.exists()
        and any(p.read_text().strip() for p in group.rglob("cgroup.procs"))
    ):
        raise ValueError("Original tool owner and descendants must be settled")
    for prepared in (root / "edit-batches").glob("*/prepared.json"):
        if not any((prepared.parent / n).is_file() for n in ("applied.json", "rolled-back.json")):
            raise ValueError("An uncertain edit batch must be reconciled first")
    runtime.scope(allow_empty=True)  # Exact base, approved paths and preserved tests.
    state = private_json(private / "state.json")
    if not normal_restored(private, state):
        raise ValueError(
            "Original server termination and normal authenticated restoration are not proven"
        )
    # Lock the actual lane. A new/late operation must never be overwritten.
    fd = os.open(str(primary) + ".lock", os.O_RDWR | os.O_NOFOLLOW)
    with os.fdopen(fd, "a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        record = private_json(primary)
        if record.get("state") == "released" and proof_path.is_file():
            proof = private_json(proof_path)
            if (
                record.get("termination_proof_sha256") == sha(proof_path.read_bytes())
                and proof["execution_id"] == identity
            ):
                return proof
        original_sha = sha(primary.read_bytes())
        if expected_primary_sha256 is not None and original_sha != expected_primary_sha256:
            raise ValueError("The exact held generation changed; nothing was released")
        sequence = int(record["attempt"])
        stream = root.parent / "implementation" / f"stream-{sequence:02}.json"
        report = private_json(stream)
        request = private_json(root / "attempt" / f"request-{sequence - 1:02}.json")
        bound = report.get("conversation_nonce") == record["nonce"] and report.get(
            "server_epoch"
        ) == state["temporary_epoch"] == record.get("server_epoch")
        if not bound and (
            report.get("conversation_nonce") is not None or expected_primary_sha256 is None
        ):
            raise ValueError(
                "Exact native identity is required; historical evidence needs an explicit held-file binding"
            )
        if (
            record["state"] not in {"quarantined", "released"}
            or report["fence_state"] != record["state"]
            or report["sequence"] != sequence
            or report["completed"] is not False
            or record.get("task_id") != report.get("busy_observation", {}).get("task")
            or request["request_id"] != identity
            or request["fence"] != row["fence"]
            or request["turn"] != sequence - 1
            or not state["established_at"]
            <= report["started_at"]
            <= report["finished_at"]
            < state["deadline"]
            or list((root / "attempt").glob(f"tool-{sequence - 1:02}-*.json"))
        ):
            raise ValueError(
                "The partial response and non-dispatch evidence do not match this held call"
            )
        proof = dict(
            version=1,
            execution_id=identity,
            fence=row["fence"],
            original_primary_sha256=original_sha,
            stream_sha256=sha(stream.read_bytes()),
            restored_state_sha256=sha((private / "state.json").read_bytes()),
            terminated_epoch=state["temporary_epoch"],
            normal_epoch=state["restored_epoch"],
            source_tree_sha256=execution.digest(tree(runtime.workspace)),
            outcome="failed",
            generation_final_outcome="unknown",
            final_usage="unknown",
            partial_response_dispatched=False,
            provider_epoch_terminated=True,
            native_identity_bound=bound,
            observed_at=execution.now(),
        )
        durable(proof_path, json.dumps(proof, sort_keys=True).encode())
        journal = PrimarySession(primary, [], record["attempt"], lambda: False)
        journal.record = dict(
            record,
            state="released",
            release_kind="verified-owned-epoch-termination",
            termination_proof_sha256=sha(proof_path.read_bytes()),
        )
        journal._save()
        return proof


def reconcile_for_root(engine, root):
    """Worker recovery after restoration; never steal an active/other lane."""
    root = Path(root)
    if len(root.parents) < 4 or not root.parent.name.startswith("task-lease-task-session-"):
        return False
    primary = root.parents[3] / ".local/real-primary-session.json"
    if not primary.is_file() or private_json(primary).get("state") not in {
        "quarantined",
        "released",
    }:
        return False
    with engine.connect() as c:
        identities = (
            c.execute(
                select(execution.requests.c.id)
                .join(execution.grants, execution.requests.c.grant_id == execution.grants.c.id)
                .where(
                    execution.grants.c.snapshot["recovery"]["root"].as_string() == str(root),
                    execution.requests.c.status.in_(["running", "checking", "failed", "cancelled"]),
                    execution.requests.c.result["usage"]["status"]
                    .as_string()
                    .in_(["partial", "unavailable"]),
                )
            )
            .scalars()
            .all()
        )
    if len(identities) != 1:
        return False
    reconcile(engine, identities[0])
    return True
