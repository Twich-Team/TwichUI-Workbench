"""A separate process; all fixture effects and completion commit in one transaction."""

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
    verification,
    admission,
    implementation,
    pr_preparation,
    real_execution,
    task_sessions,
)

log = logging.getLogger("workbench.worker")


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


def tick(engine, runtime=None, publisher=None, crash=None, stop_event=None):
    """One production worker iteration; injectable controlled adapters are internal only."""
    if stop_event is not None and stop_event.is_set():
        return False
    return (
        process_one(engine, crash)
        or verification.process_one(engine)
        or admission.process_one(engine, crash)
        or implementation.process_one(engine, crash)
        or pr_preparation.process_one(engine, crash, stop_event=stop_event, publisher=publisher)
        or task_sessions.queue_one(engine)
        or task_sessions.process_one(engine, stop_event=stop_event)
        or real_execution.process_one(engine, runtime=runtime, stop_event=stop_event)
        or planning_worker.apply_one(engine)
        or planning_worker.process_one(engine, stop_event=stop_event)
    )


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
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    stopped = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopped.set())
    engine = engine_for()
    while not stopped.is_set():
        try:
            if args.session_config:
                from workbench import task_session

                worked = task_session.process_one(engine, args.session_config, stop_event=stopped)
                if task_session.finished(args.session_config):
                    return
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
    main()
