"""Site approval -> existing finite-session worker, never a second execution engine.

Capability configuration grants nothing. Each operation requires an exact new
human scope/budget command with activate_once, validated decomposition, fixed
recipe and original expiry. Existing controller owns auth/probe/grant/attempt/
checks/restoration; existing publication owner alone owns GitHub writes.
"""

from datetime import datetime
import json
import os
from pathlib import Path
import threading
import subprocess
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, inspect
from workbench.db import (
    task_sessions as sessions,
    proposal_decisions,
    backlog_items,
    write_transaction,
)
from workbench import real_execution as execution, task_budget, task_execution, task_session
from workbench.real_workspace import durable, sha

ROOT = Path(__file__).resolve().parents[2]
ACTIVE = {"pending", "active", "waiting_recovery"}


def read_capability(engine, path):
    """Validate the same capability bytes before publication and at admission."""
    if (
        path.resolve() != path or not path.is_file()
        or path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077
        or path.stat().st_size > 8192
    ):
        raise ValueError("Private owned one-off runtime configuration required")
    value = json.loads(path.read_text())
    db = Path(engine.url.database).absolute()
    if (
        value.get("mode") != "explicit-one-off-only"
        or db.resolve() != db or value.get("database") != str(db)
        or value.get("database_identity") != [db.stat().st_dev, db.stat().st_ino]
        or value.get("repository") != "Twich-Team/TwichUI"
        or value.get("base_branch") != "main"
        or value.get("model") != "pilot-qwen38-27b"
        or value.get("automatic_fallback") is not False
        or not inspect(engine).has_table(sessions.name)
    ):
        raise ValueError("One-off runtime configuration does not match the application store")
    templates = ROOT / ".local/supported-task-templates"
    if value.get("templates_root") != str(templates):
        raise ValueError("One-off runtime templates must belong to the current stage")
    if "templates" in value:
        from workbench.supported_tasks import read_template
        expected = value["templates"]
        if (not isinstance(expected, list) or templates.resolve() != templates
                or not templates.is_dir() or templates.stat().st_uid != os.getuid()
                or templates.stat().st_mode & 0o077
                or sorted(p.name for p in templates.iterdir()) != sorted(expected)):
            raise ValueError("Staged template inventory changed")
        for name in expected:
            if not read_template(templates, name.removesuffix(".json")):
                raise ValueError("Staged template is missing")
    proof = Path(value["total_limit_proof"])
    if (
        proof.resolve() != proof or not proof.is_file()
        or proof.stat().st_uid != os.getuid() or proof.stat().st_size > 16384
        or sha(proof.read_bytes()) != value["total_limit_proof_sha256"]
    ):
        raise ValueError("One-off runtime sampler proof changed")
    return value


def capability(engine):
    """Read-only fixed installation capability, never a model/HTTP activation check."""
    path = ROOT / ".local/task-runtime.json"
    try:
        return read_capability(engine, path)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, HTTPException):
        return None


def queue_one(engine):
    """Same worker discovers only explicit one-off decisions, never background findings."""
    config = capability(engine)
    if not config:
        return False
    with write_transaction(engine) as c:
        approval = (
            c.execute(
                select(proposal_decisions)
                .outerjoin(sessions, sessions.c.id == proposal_decisions.c.id)
                .where(
                    proposal_decisions.c.status == "applied",
                    sessions.c.id.is_(None),
                    proposal_decisions.c.payload["run_budget"]["activate_once"]
                    .as_boolean()
                    .is_(True),
                )
                .order_by(proposal_decisions.c.created_at, proposal_decisions.c.id)
                .limit(1)
            )
            .mappings()
            .first()
        )
        if not approval:
            return False
        tasks = (
            c.execute(
                select(backlog_items).where(
                    backlog_items.c.proposal_id == approval["proposal_id"],
                    backlog_items.c.kind == "task",
                )
            )
            .mappings()
            .all()
        )
        if len(tasks) != 1:
            return False  # Decomposition application is still pending; it owns task creation.
        ref = dict(
            intake_id=approval["intake_id"], task_id=tasks[0]["id"], approval_id=approval["id"]
        )
        try:
            task_execution.snapshot(c, **ref)
            policy = task_budget.permission(c, approval)
            contract = policy["offer"]["snapshot"]["specification"]["contract"] if policy else {}
            if (
                not policy
                or contract.get("provider") != "local"
                or contract.get("model") != config["model"]
                or contract.get("authoring")
                or policy["offer"]["snapshot"]["specification"]["contract"]["repository"]
                != config["repository"]
            ):
                raise ValueError("Supported task budget unavailable")
            snapshot = dict(
                task=ref,
                budget_fingerprint=execution.digest(policy),
                capability_sha256=sha((ROOT / ".local/task-runtime.json").read_bytes()),
                started_at=approval["created_at"],
                deadline=policy["expires_at"],
            )
            status, message = (
                "pending",
                "Waiting for the worker to prepare the approved task. No model has started.",
            )
        except (ValueError, HTTPException, task_execution.Waiting) as error:
            snapshot = dict(task=ref, started_at=approval["created_at"])
            status, message = "held", str(error)[:400]
        c.execute(
            sessions.insert().values(
                id=approval["id"],
                task_id=ref["task_id"],
                snapshot=snapshot,
                binding=execution.digest(snapshot),
                status=status,
                message=message,
                state={},
                created_at=execution.now(),
                finished_at=None if status == "pending" else execution.now(),
            )
        )
    return True


def _config(engine, row):
    """Create-only package/config derived from the saved command, not caller paths."""
    cached = ROOT / "artifacts/runs" / ("task-session-" + str(UUID(row["id"]))) / "session.json"
    if cached.exists():
        if (
            cached.is_symlink()
            or cached.stat().st_uid != os.getuid()
            or cached.stat().st_mode & 0o077
            or cached.stat().st_size > 8192
        ):
            raise ValueError("Saved private session configuration changed")
        value = json.loads(cached.read_text())
        expected_cycle = (
            ROOT / "artifacts/runs" / ("task-lease-task-session-" + str(UUID(row["id"])) + "-01")
        )
        if (
            value["task"] != row["snapshot"]["task"]
            or value["run_root"] != str(expected_cycle)
            or value["task_budget_fingerprint"] != row["snapshot"]["budget_fingerprint"]
            or value["started_at"] != row["snapshot"]["started_at"]
            or value["deadline"] != row["snapshot"]["deadline"]
        ):
            raise ValueError("Saved session identity differs from its original decision")
        return (
            cached  # Cleanup/reconciliation remain possible after expiry or capability withdrawal.
        )
    config = capability(engine)
    if not config:
        raise ValueError("One-off runtime capability is unavailable; no activation")
    expected = row["snapshot"].get("capability_sha256")
    if expected and sha((ROOT / ".local/task-runtime.json").read_bytes()) != expected:
        raise ValueError("One-off runtime configuration changed after the saved decision")
    ref = row["snapshot"]["task"]
    with engine.connect() as c:
        approval = (
            c.execute(
                select(proposal_decisions).where(proposal_decisions.c.id == ref["approval_id"])
            )
            .mappings()
            .one()
        )
        policy = task_budget.permission(c, approval)
        task_execution.snapshot(c, **ref)
    if execution.digest(policy) != row["snapshot"]["budget_fingerprint"]:
        raise ValueError("The approved task budget changed")
    from workbench import everyday
    if everyday.enabled():
        everyday.require_room(everyday.configuration(engine))
    package_root = ROOT / "artifacts/runs" / ("task-session-" + str(UUID(row["id"])))
    package_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if package_root.is_symlink() or package_root.stat().st_uid != os.getuid():
        raise ValueError("Owned task package required")
    start = datetime.fromisoformat(row["snapshot"]["started_at"])
    end = datetime.fromisoformat(row["snapshot"]["deadline"])
    budget = dict(
        started_at=start.timestamp(),
        deadline=end.timestamp(),
        max_cycles=1,
        max_probes=1,
        max_attempts=1,
        max_generated_tokens=24000,
        reserved_tokens_per_cycle=24000,
        original_attempt_token_ceiling=24000,
        original_turns=20,
        exchange_policy='progress-aware-v1' if policy['budget']['turns'] is None else 'historical-twenty-turn-v1',
        evidence_ceiling_bytes=64 << 20,
    )
    durable(package_root / "BUDGET_BASELINE.json", json.dumps(budget, sort_keys=True).encode())
    instruction = dict(
        source="Actual saved owner scope-and-run command in Workbench, not generated chat text",
        decision_id=row["id"],
        payload=approval["payload"],
        scope=approval["snapshot"],
    )
    raw = json.dumps(instruction, sort_keys=True).encode()
    if len(raw) > 65536:
        raise ValueError("Owner instruction exceeds the bounded session input")
    auth = package_root / "owner-command.json"
    durable(auth, raw)
    auth.chmod(0o600)
    cycle = package_root.parent / ("task-lease-" + package_root.name + "-01")
    from workbench.real_package import Package

    package = Package(package_root)
    if not package.reservations():
        cycle.mkdir(mode=0o700)
        package.reserve(cycle)
    else:
        package.verify(cycle)
    value = dict(
        started_at=row["snapshot"]["started_at"],
        deadline=row["snapshot"]["deadline"],
        max_attempts=1,
        max_probes=1,
        max_leases=1,
        owner_instruction=str(auth),
        owner_instruction_sha256=sha(raw),
        run_root=str(cycle),
        task=ref,
        task_budget_fingerprint=execution.digest(policy),
        total_limit_proof=config["total_limit_proof"],
        total_limit_proof_sha256=config["total_limit_proof_sha256"],
        allow_publication=policy["budget"]["publication"] == "draft-pr",
    )
    target = package_root / "session.json"
    durable(target, json.dumps(value, sort_keys=True).encode())
    target.chmod(0o600)
    return target


def _result(engine, row, status, message, state):
    with write_transaction(engine) as c:
        actual = c.execute(select(sessions).where(sessions.c.id == row["id"])).mappings().one()
        if actual["state"].get("owner") != row["state"].get("owner"):
            return False
        if (status == "waiting_recovery" and actual["status"] == status
                and actual["message"] == message and actual["state"] == state):
            return True
        c.execute(
            sessions.update()
            .where(sessions.c.id == row["id"])
            .values(
                status=status,
                message=message[:1000],
                state={
                    **actual["state"],
                    **state,
                    **(
                        {
                            k: actual["state"][k]
                            for k in ("cancel_requested", "stop_command")
                            if k in actual["state"]
                        }
                    ),
                },
                finished_at=None if status in ACTIVE else execution.now(),
            )
        )
    return True


def reconcile_controller(root):
    report = root / "lease-controller-result.json"
    from workbench import everyday

    if not (root / "session-claimed.json").exists():
        return
    if report.exists():
        if not everyday.enabled() or json.loads(report.read_text()).get("restoration", {}).get("restored"):
            return
        report = root / 'lease-restoration-reconciled.json'
        if report.exists():
            return
    owner_path = root / "controller-owner.json"
    if owner_path.exists() and execution.alive(json.loads(owner_path.read_text())):
        return
    codex = root / 'codex-controller.json'
    if codex.exists():
        unit = 'workbench-real-codex-' + root.name + '.service'
        if json.loads(codex.read_text()) != {'unit': unit}:
            return
        try:
            state = subprocess.run(['/usr/bin/systemctl', '--user', 'show', unit,
                                    '--property=ActiveState', '--value'],
                                   capture_output=True, text=True, timeout=4)
        except (OSError, subprocess.TimeoutExpired):
            return
        if state.returncode or state.stdout.strip() not in {'inactive', 'failed'}:
            return
        durable(report, b'{"restoration":{"restored":true,"no_lease":true},"recovered_after_controller_interruption":true}')
        return
    private = ROOT / ".local" / ("chronicle-primary-lease-" + root.name)
    state_path = private / "state.json"
    if state_path.is_file() and not state_path.is_symlink():
        state = json.loads(state_path.read_text())
        if not (
            state.get("restored")
            and state.get("original_config_verified")
            and state.get("original_access_verified")
            and state.get("temporary_access_rejected")
        ):
            return
        restoration = {
            k: state.get(k)
            for k in (
                "restored",
                "restored_at",
                "restored_epoch",
                "original_config_verified",
                "original_access_verified",
                "temporary_access_rejected",
            )
        }
        durable(
            report,
            json.dumps(
                dict(
                    restoration=restoration,
                    recovered_after_controller_interruption=True,
                    provenance=dict(
                        method="Dead owned controller; verified retained lease-restoration state",
                        state_sha256=sha(state_path.read_bytes()),
                    ),
                ),
                sort_keys=True,
            ).encode(),
        )
    elif ((root / "controller-started.json").exists() or (everyday.enabled() and owner_path.is_file())) and not (
        root / "lease-established.json"
    ).exists():
        # Lease persists state BEFORE modifying authentication; absence proves no lease effect.
        durable(
            report,
            json.dumps(
                dict(
                    restoration=dict(restored=True, lease_not_established=True),
                    recovered_after_controller_interruption=True,
                    inference_ran=False,
                    provenance=dict(
                        method="Controller dead before persisted lease state; no restart or inference replay"
                    ),
                ),
                sort_keys=True,
            ).encode(),
        )


def process_one(engine, stop_event=None, advance=None, *, resume_only=False):
    """Called by the existing worker. No service work occurs without a queued command."""
    if not inspect(engine).has_table(sessions.name):
        return False
    with write_transaction(engine) as c:
        row = (
            c.execute(
                select(sessions)
                .where(sessions.c.status.in_(ACTIVE - {"pending"} if resume_only else ACTIVE))
                .order_by(sessions.c.created_at, sessions.c.id)
                .limit(1)
            )
            .mappings()
            .first()
        )
        if not row:
            return False
        row = dict(row)
        state = dict(row["state"])
        owner = state.get("owner")
        if owner and owner != execution.owner_identity() and execution.alive(owner):
            return False
        if row["binding"] != execution.digest(row["snapshot"]):
            raise ValueError("Saved task-session identity changed")
        state["owner"] = execution.owner_identity()
        row["state"] = state
        if row["status"] == "waiting_recovery":
            if owner != state["owner"]:
                c.execute(sessions.update().where(sessions.c.id == row["id"]).values(state=state))
        else:
            c.execute(
                sessions.update()
                .where(sessions.c.id == row["id"])
                .values(
                    status="active",
                    state=state,
                    message="Preparing an isolated workspace and running readiness checks.",
                )
            )
    cancelled = threading.Event()
    done = threading.Event()

    def watch():
        while not done.wait(0.2):
            if stop_event is not None and stop_event.is_set():
                cancelled.set()
                return
            with engine.connect() as c:
                value = c.execute(
                    select(sessions.c.state).where(sessions.c.id == row["id"])
                ).scalar()
            if value.get("cancel_requested"):
                cancelled.set()
                return

    watcher = threading.Thread(target=watch, daemon=True, name="one-off-task-stop")
    watcher.start()
    root = None
    try:
        cfg = _config(engine, row)
        value = json.loads(cfg.read_text())
        root = Path(value["run_root"])
        if row["state"].get("cancel_requested"):
            _propagate_stop(engine, row["id"], row["state"]["stop_command"])
        # A restarted worker waits for the original trusted preparation child.
        # Its log is a durable launch intent; never launch a duplicate if child
        # ownership is still being recorded. Failed readiness grants no inference.
        unfinished_preparation = (
            (root / "preparation-controller.log").exists()
            and not (root / "real-run/preparation-completed.json").exists()
            and not (root / "session-claimed.json").exists()
        )
        if unfinished_preparation and datetime.now().astimezone() < datetime.fromisoformat(
            value["deadline"]
        ):
            owner_file = root / "preparation-owner.json"
            if owner_file.exists():
                if owner_file.is_symlink() or owner_file.stat().st_size > 4096:
                    raise ValueError("Trusted preparation ownership changed")
                preparation = json.loads(owner_file.read_text())
                if preparation["recipe_sha256"] != sha(
                    (root / "real-run/recipe.json").read_bytes()
                ):
                    raise ValueError("Preparation belongs to different task inputs")
                if not execution.alive(preparation["owner"]):
                    return _result(
                        engine,
                        row,
                        "held",
                        "Readiness checks ended without passing evidence. No model started; retained diagnostics need attention.",
                        state,
                    )
            return _result(
                engine,
                row,
                "waiting_recovery",
                "Waiting for the original readiness checks and their passing evidence. No duplicate preparation or model run will start.",
                state,
            )
        controller = root / "controller-owner.json"
        if controller.exists():
            owned = json.loads(controller.read_text())
            if (
                execution.alive(owned)
                and owned != execution.owner_identity()
                and not (root / "lease-controller-result.json").exists()
            ):
                return _result(
                    engine,
                    row,
                    "waiting_recovery",
                    "The original task controller is still running. Waiting for its result; no duplicate execution.",
                    state,
                )
        reconcile_controller(root)
        from workbench.local_generation_recovery import reconcile_for_root
        try:
            reconcile_for_root(engine, root/'real-run')
        except (ValueError, OSError, KeyError, TypeError):
            pass  # Unproven termination never releases a generation or task fence.
        (advance or task_session.process_one)(engine, cfg, stop_event=cancelled)
        # advance can launch AND await the entire controller, including model
        # restoration. Reconcile again after it returns, before closing this
        # session; a terminal failed session is no longer selected next tick.
        try:
            reconcile_for_root(engine, root/'real-run')
        except (ValueError, OSError, KeyError, TypeError):
            pass  # Preserve unknown usage and every fence when proof is absent.
        if reason := getattr(stop_event, 'reason', None):
            state['resource_hold'] = reason
        terminal = root / "session-finished.json"
        if terminal.exists():
            outcome = json.loads(terminal.read_text())
            if outcome["task"] != value["task"]:
                raise ValueError("Saved session result belongs to another task")
            state["outcome"] = outcome
            return _result(
                engine,
                row,
                "completed"
                if outcome["status"] in {"published", "verified"}
                else outcome["status"],
                "Draft PR and independently checked result are ready for human review."
                if outcome["status"] == "published"
                else outcome["message"],
                state,
            )
        from workbench import everyday
        if everyday.enabled():
            error = ROOT / '.local' / ('chronicle-primary-lease-' + root.name) / 'restoration-error.json'
            if error.is_file():
                return _result(engine, row, 'waiting_recovery',
                               'Model access needs attention. Workbench could not verify restoration. New local work is held.',
                               state)
        return _result(
            engine, row, "waiting_recovery",
            "Waiting for saved result delivery and verified restoration. Implementation will not be replayed.", state,
        )
    except (ValueError, HTTPException, OSError, subprocess.SubprocessError) as error:
        reason = getattr(stop_event, 'reason', None)
        state["error_type"] = type(error).__name__
        # Keep claimed work recoverable until original controller/restoration settles.
        uncertain = (
            root is not None
            and (root / "session-claimed.json").exists()
            and not (root / "lease-controller-result.json").exists()
        )
        return _result(
            engine,
            row,
            "waiting_recovery" if uncertain else "held",
            "Task preparation needs attention: "
            + (reason or str(error))[:400]
            + ". Saved work is retained; no automatic replay.",
            state,
        )
    finally:
        done.set()
        watcher.join(timeout=1)


def public_view(c, task_id):
    if not inspect(c).has_table(sessions.name):
        return None
    row = (
        c.execute(
            select(sessions)
            .where(sessions.c.task_id == task_id)
            .order_by(sessions.c.created_at.desc())
            .limit(1)
        )
        .mappings()
        .first()
    )
    if not row:
        return None
    return dict(
        id=row["id"],
        status=row["status"],
        message=' '.join(filter(None, [row["state"].get("resource_hold"), row["message"]])),
        cancel_requested=bool(row["state"].get("cancel_requested")),
        can_stop=row["status"] in ACTIVE and not row["state"].get("cancel_requested"),
    )


def cancel(engine, identity, command_id):
    UUID(command_id)
    with write_transaction(engine) as c:
        row = c.execute(select(sessions).where(sessions.c.id == identity)).mappings().first()
        if not row:
            raise HTTPException(404, "Task session not found")
        state = dict(row["state"])
        if state.get("stop_command"):
            if state["stop_command"] != command_id:
                raise HTTPException(409, "Another stop request is already saved")
            return dict(id=identity, status=row["status"])
        if row["status"] not in ACTIVE:
            raise HTTPException(409, "This task session has finished; its history is retained")
        state.update(cancel_requested=True, stop_command=command_id)
        queued = row["status"] == "pending"
        c.execute(
            sessions.update()
            .where(sessions.c.id == identity)
            .values(
                state=state,
                status="cancelled" if queued else row["status"],
                finished_at=execution.now() if queued else None,
                message="Stopped before preparation. No model started."
                if queued
                else "Stopping this task. Waiting for owned work to settle and the server to be restored.",
            )
        )
    if not queued:
        _propagate_stop(engine, identity, command_id)
    return dict(id=identity, status="cancelled" if queued else "cancel_requested")


def _propagate_stop(engine, decision_id, command_id):
    """Stop only this session's existing request, including after worker restart."""
    with engine.connect() as c:
        selected = (
            c.execute(
                select(execution.requests.c.id)
                .join(execution.grants, execution.requests.c.grant_id == execution.grants.c.id)
                .where(
                    execution.grants.c.snapshot["task_budget"]["decision_id"].as_string()
                    == decision_id,
                    execution.requests.c.status.in_(["pending", "running", "checking"]),
                )
            )
            .scalars()
            .all()
        )
    for identity in selected:
        try:
            execution.cancel(engine, identity, command_id)
        except HTTPException as error:
            if error.status_code != 409:
                raise
