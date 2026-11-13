"""A separate process; all fixture effects and completion commit in one transaction."""

from workbench import worker_operation as operation

import argparse
import logging
import os
import signal
import threading
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from workbench.db import applications, commands, engine_for, tasks, write_transaction
from workbench.service import now, version
from workbench import (
    planning_worker,
    feature_execution,
    verification,
    admission,
    implementation,
    pr_preparation,
    real_execution,
    task_sessions,
    trusted_preparation,
)

log = logging.getLogger("workbench.worker")


@operation.isolated
def process_one(engine, crash=None):
    with write_transaction(engine) as connection:
        command = (
            connection.execute(
                select(commands)
                .where(commands.c.status == "pending")
                .order_by(commands.c.created_at, commands.c.id)
                .limit(1)
            )
            .mappings()
            .first()
        )
        if command is None:
            return False
        row = (
            connection.execute(select(tasks).where(tasks.c.id == command["task_id"]))
            .mappings()
            .one()
        )
        operation.selected(commands, command)
        at = now()
        if version(row) != command["version"]:
            status, message = (
                "stale",
                "Needs another look. The evidence changed; your saved decision was not applied.",
            )
        elif row["state"] != "held":
            status, message = (
                "failed",
                "Could not apply this decision. The task is no longer awaiting review.",
            )
        else:
            confirm = all(value == "confirm" for value in command["payload"]["choices"].values())
            changes = dict(
                state="review_confirmed" if confirm else "changes_requested",
                eligibility="needs_publication_checks" if confirm else "contract_hold",
                revision=row["revision"] + 1,
            )
            connection.execute(tasks.update().where(tasks.c.id == row["id"]).values(**changes))
            connection.execute(
                applications.insert().values(
                    command_id=command["id"],
                    task_id=row["id"],
                    previous_version=command["version"],
                    result_version=version({**row, **changes}),
                    applied_at=at,
                )
            )
            status = "applied"
            message = (
                "Review confirmed. The contract hold is released; publication checks are still required."
                if confirm
                else "Changes requested. The candidate remains held. No new attempt was started."
            )
        connection.execute(
            commands.update()
            .where(commands.c.id == command["id"])
            .values(status=status, message=message, finished_at=at)
        )
        if crash == "before-commit":
            os._exit(91)
    if crash == "after-commit":
        os._exit(92)
    return True


@operation.scheduler
def tick(engine, runtime=None, publisher=None, crash=None, stop_event=None, planning_adapter=None,
         *, feature_provision=None, session_adapter=None):
    """One production worker iteration; injectable controlled adapters are internal only."""
    if stop_event is not None and stop_event.is_set():
        return False
    from workbench.investigation_recovery import recover_one
    from workbench.work_controls import read as work_status
    session_options = dict(advance=session_adapter) if session_adapter is not None else {}
    if work_status(engine).paused:
        if feature_execution.recover_combined(engine):
            return True
        return task_sessions.process_one(engine, stop_event=stop_event, resume_only=True, **session_options)
    from workbench.preparation_resources import process_one as recover_storage
    from workbench import draft_delivery
    from workbench.continuing_repair import process_one as recover_local_repair
    # Retain each stage's oldest-first ordering, but give every stage a turn.
    # The sole worker owns this cursor; it is not another durable queue or claim.
    stages = (
        lambda: recover_one(engine),
        lambda: recover_storage(engine),
        lambda: process_one(engine, crash),
        lambda: verification.process_one(engine),
        lambda: admission.process_one(engine, crash),
        lambda: implementation.process_one(engine, crash),
        lambda: pr_preparation.process_one(engine, crash, stop_event=stop_event, publisher=publisher),
        lambda: draft_delivery.advance(engine, stop_event=stop_event),
        lambda: task_sessions.queue_one(engine),
        lambda: recover_local_repair(engine),
        lambda: task_sessions.process_one(engine, stop_event=stop_event, **session_options),
        lambda: real_execution.process_one(engine, runtime=runtime, stop_event=stop_event),
        lambda: feature_execution.apply_one(engine),
        lambda: feature_execution.advance(engine, adapter=planning_adapter,
            provision=feature_provision, stop_event=stop_event),
        lambda: planning_worker.apply_one(engine),
        lambda: planning_worker.process_one(engine, stop_event=stop_event, adapter=planning_adapter),
        lambda: trusted_preparation.process_one(engine, stop_event=stop_event),
    )
    start = getattr(engine, '_workbench_stage', 0) % len(stages)
    for offset in range(len(stages)):
        index = (start + offset) % len(stages)
        if stop_event is not None and stop_event.is_set():
            return False
        if stages[index]():
            engine._workbench_stage = (index + 1) % len(stages)
            return True
    return False


def preparation_recovery(job_id, binding):
    """The local recovery worker runs only the named job under the offline guard."""
    import json
    import sys
    from subprocess import SubprocessError

    from fastapi import HTTPException
    from acceptance_transport_guard import install_offline_guard

    try:
        install_offline_guard()
        os.umask(0o077)
        os.environ.pop("WORKBENCH_LOCAL_KEY", None)
        database = trusted_preparation.owned(os.environ["WORKBENCH_DB"])
        if not database.is_file():
            raise ValueError("An existing owned SQLite file is required")
        engine = engine_for(database)
        try:
            receipt = (trusted_preparation.request_recovery(engine, job_id, binding)
                       if binding is not None else trusted_preparation.process_recovery(engine, job_id))
        finally:
            engine.dispose()
    except (ValueError, OSError, HTTPException, OperationalError, SubprocessError) as error:
        detail = error.detail if isinstance(error, HTTPException) else str(error)
        print("Preparation recovery held. " + detail[:1600], file=sys.stderr)
        return 2
    print(json.dumps(receipt, sort_keys=True))
    return 2 if receipt["status"] == "failed" else 0


def checked_delivery(args):
    """Use the same delivery state machine from a scoped host process.

    The everyday API and persistent worker keep their existing loopback-only
    network restrictions. This process can deliver only one named execution.
    """
    import json
    import sys
    from fastapi import HTTPException
    from sqlalchemy import create_engine
    from workbench import real_publication

    os.umask(0o077)
    path = Path(os.environ["WORKBENCH_DB"]).absolute()
    engine = None
    try:
        if path.resolve() != path or not path.is_file() or path.stat().st_uid != os.getuid():
            raise ValueError("An existing owned Workbench database is required.")
        if args.review_checked:
            engine = create_engine("sqlite:///" + path.as_uri() + "?mode=ro&uri=true")
            print(json.dumps(real_publication.review_checked(engine, args.review_checked), indent=2))
            return 0
        engine = engine_for(path)
        stopped = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stopped.set())
        timer = threading.Timer(600, stopped.set)
        timer.start()
        try:
            result = real_publication.deliver_checked(
                engine, args.deliver_checked, args.publication_approval_dir.absolute(), stopped
            )
        finally:
            timer.cancel()
        print(json.dumps({k: result[k] for k in ("status", "message", "published", "url")}))
        return 0 if result["published"] else 2
    except (ValueError, OSError, HTTPException, OperationalError) as error:
        detail = error.detail if isinstance(error, HTTPException) else str(error)
        print("Checked draft delivery held. " + detail[:600], file=sys.stderr)
        return 2
    finally:
        if engine is not None:
            engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--test-crash",
        choices=["before-commit", "after-commit"],
        help="Fixture recovery acceptance only; forcibly exit at transaction boundary",
    )
    parser.add_argument(
        "--session-config",
        type=Path,
        help="Private one-session operational authorization; never recurring",
    )
    parser.add_argument("--preparation-recovery", metavar="JOB_ID", help="Dispatch only one authorized preparation recovery")
    parser.add_argument("--authorize-review-binding", metavar="SHA256", help="Record authorization without dispatch")
    delivery = parser.add_mutually_exclusive_group()
    delivery.add_argument("--review-checked", metavar="EXECUTION", help="Show the exact checked draft proposal offline")
    delivery.add_argument("--deliver-checked", metavar="EXECUTION", help="Publish the exact separately approved checked draft")
    parser.add_argument("--publication-approval-dir", type=Path)
    args = parser.parse_args()
    if args.review_checked or args.deliver_checked:
        if (args.once or args.test_crash or args.session_config or args.preparation_recovery
                or args.authorize_review_binding or not os.environ.get("WORKBENCH_DB")
                or bool(args.deliver_checked) != bool(args.publication_approval_dir)):
            parser.error("Checked delivery requires an explicit existing WORKBENCH_DB and an approval directory only for delivery")
        return checked_delivery(args)
    if args.publication_approval_dir is not None:
        parser.error("--publication-approval-dir requires --deliver-checked")
    if args.preparation_recovery:
        if args.once or args.test_crash or args.session_config or not os.environ.get("WORKBENCH_DB"):
            parser.error("Targeted preparation recovery requires only the named job and explicit WORKBENCH_DB")
        return preparation_recovery(args.preparation_recovery, args.authorize_review_binding)
    if args.authorize_review_binding is not None:
        parser.error("--authorize-review-binding requires --preparation-recovery")
    logging.basicConfig(level=logging.INFO)
    stopped = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopped.set())
    from contextlib import nullcontext
    from workbench import everyday

    if everyday.enabled() and (args.session_config or args.test_crash or args.once):
        parser.error('Everyday operation requires its dedicated persistent worker.')
    engine = engine_for()
    owner = everyday.worker_owner(engine) if everyday.enabled() else nullcontext()
    with owner as policy:
        try:
            run(engine, args, stopped, policy)
        finally:
            from workbench.local_model_priority import close
            close()


def run(engine, args, stopped, policy):
    from workbench.host_resources import observations
    with observations(policy, stopped):
        _run(engine, args, stopped, policy)


def _run(engine, args, stopped, policy):
    from workbench.everyday import watch_capacity
    import time

    next_housekeeping = 0

    while not stopped.is_set():
        if policy and time.monotonic() >= next_housekeeping:
            from workbench.everyday import housekeep
            try:
                housekeep(policy)
            except (OSError, RuntimeError, ValueError):
                log.exception('Retention could not safely archive; originals remain preserved')
            next_housekeeping = time.monotonic()+60
        try:
            if policy:
                from workbench.local_model_priority import reconcile
                reconcile(engine)
            if args.session_config:
                from workbench import task_session

                worked = task_session.process_one(engine, args.session_config, stop_event=stopped)
                if task_session.finished(args.session_config):
                    return
            elif policy:
                with watch_capacity(policy, stopped) as guard:
                    worked = tick(engine, stop_event=guard)
            else:
                worked = tick(engine, crash=args.test_crash, stop_event=stopped)
        except OperationalError as error:
            if getattr(error.orig, "sqlite_errorcode", 0) & 0xFF not in {5, 6}:
                log.exception("Review store failed; pending commands remain durable")
                raise
            log.warning("Review store busy; commands remain pending")
            worked = False
        if args.once:
            return
        stopped.wait(0.15 if worked else 0.6)


if __name__ == "__main__":
    raise SystemExit(main())
