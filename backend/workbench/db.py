from contextlib import contextmanager
import os
import logging
import sqlite3
import time
import traceback
from sqlalchemy.exc import OperationalError
from pathlib import Path

from sqlalchemy import (Column, ForeignKey, Index, Integer, JSON, LargeBinary, MetaData, String, Table,
                        UniqueConstraint, create_engine, event)

metadata = MetaData()
log = logging.getLogger("workbench.sqlite")


def busy_owners(engine, phase, connection):
    """Record only lock owners for this database, without SQL or task content."""
    try:
        path = Path(engine.url.database)
        info = path.stat()
        identity = f"{os.major(info.st_dev):02x}:{os.minor(info.st_dev):02x}:{info.st_ino}"
        rows = Path("/proc/locks").read_text().splitlines()
        owners = sorted({int(parts[4]) for row in rows
                         if len(parts := row.split()) >= 6 and parts[5] == identity
                         and parts[4].isdecimal()})[:16]
    except OSError:
        owners = []
    log.warning("SQLite busy phase=%s pid=%s connection=%s lock_pids=%s",
                phase, os.getpid(), id(connection.driver_connection), owners)
tasks = Table(
    "tasks", metadata,
    Column("id", String, primary_key=True),
    Column("title", String, nullable=False),
    Column("summary", String, nullable=False),
    Column("subsystem", String, nullable=False),
    Column("state", String, nullable=False),
    Column("revision", Integer, nullable=False),
    Column("evidence", JSON, nullable=False),
    Column("attempt_outcome", String, nullable=False),
    Column("verification", String, nullable=False),
    Column("publication", String, nullable=False),
    Column("eligibility", String, nullable=False),
)
commands = Table(
    "commands", metadata,
    Column("id", String, primary_key=True),
    Column("task_id", ForeignKey("tasks.id"), nullable=False),
    Column("version", String, nullable=False),
    Column("payload", JSON, nullable=False),
    Column("reviewed_evidence", JSON, nullable=False),
    Column("reviewed_revision", Integer, nullable=False),
    Column("status", String, nullable=False),
    Column("message", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("finished_at", String),
    UniqueConstraint("task_id", "version", name="uq_task_review"),
)
Index("ix_commands_pending", commands.c.status, commands.c.created_at)
applications = Table(
    "applications", metadata,
    Column("command_id", ForeignKey("commands.id"), primary_key=True),
    Column("task_id", ForeignKey("tasks.id"), nullable=False),
    Column("previous_version", String, nullable=False),
    Column("result_version", String, nullable=False),
    Column("applied_at", String, nullable=False),
)


def database_path():
    return Path(os.environ.get("WORKBENCH_DB", ".local/workbench.sqlite3")).resolve()


def engine_for(path=None):
    path = Path(path or database_path()).resolve()
    engine = create_engine(f"sqlite:///{path}", connect_args={"timeout": 0.15})

    def commit(connection):
        # COMMIT BUSY leaves the SQLite transaction active. Keep SQLAlchemy's
        # commit and rollback lifecycle intact by waiting inside its dialect
        # operation. Retry this same commit, never the body or provider work.
        deadline = time.monotonic() + 1.0
        while True:
            try:
                connection.commit()
                return
            except sqlite3.OperationalError as error:
                if (getattr(error, "sqlite_errorcode", 0) & 0xFF != 5
                        or not connection.in_transaction or time.monotonic() >= deadline):
                    raise
                busy_owners(engine, "commit", connection)
                time.sleep(min(.02, max(0, deadline-time.monotonic())))

    engine.dialect.do_commit = commit

    @event.listens_for(engine, "connect")
    def pragmas(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=150")
        connection.execute("PRAGMA synchronous=FULL")

    @event.listens_for(engine, "begin")
    def started(connection):
        connection.info["transaction_started"] = time.monotonic()

    @event.listens_for(engine, "rollback")
    @event.listens_for(engine, "commit")
    def finished(connection):
        started_at = connection.info.pop("transaction_started", None)
        if started_at is not None and (elapsed := time.monotonic()-started_at) >= .1:
            sites = [(Path(frame.filename).name, frame.name, frame.lineno)
                     for frame in traceback.extract_stack()
                     if "/workbench/" in frame.filename and frame.filename != __file__]
            log.warning("SQLite transaction closed pid=%s connection=%s seconds=%.3f site=%s",
                        os.getpid(), id(connection.connection.driver_connection), elapsed, sites[-1:])

    return engine


@contextmanager
def write_transaction(engine):
    # All writers acquire the lock before reading the revision or queue.
    # Lock acquisition may wait before reading or applying any operation.
    # Never replay a transaction body, commit or external effect.
    with engine.connect() as connection:
        deadline = time.monotonic() + 1.0
        while True:
            try:
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                break
            except OperationalError as error:
                connection.rollback()
                if getattr(error.orig, "sqlite_errorcode", 0) & 0xFF not in {5, 6} or time.monotonic() >= deadline:
                    raise
                busy_owners(engine, "begin", connection.connection)
                time.sleep(min(0.02, max(0, deadline-time.monotonic())))
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise

# Application-owned display preferences only; never consumed by a worker.
display_settings = Table(
    'display_settings', metadata,
    Column('id', Integer, primary_key=True),
    Column('revision', Integer, nullable=False),
    Column('resource_hours', Integer, nullable=False),
    Column('refresh_seconds', Integer, nullable=False),
)

# Intake uses the same application database and backlog projection, never pilot state.

intakes = Table('intakes', metadata,
    Column('id', String, primary_key=True), Column('payload', JSON, nullable=False),
    Column('status', String, nullable=False), Column('revision', Integer, nullable=False),
    Column('created_at', String, nullable=False), Column('priority_override', Integer))
planning_jobs = Table('planning_jobs', metadata,
    Column('id', String, primary_key=True), Column('intake_id', ForeignKey('intakes.id'), nullable=False),
    Column('kind', String, nullable=False), Column('revision', Integer, nullable=False),
    Column('status', String, nullable=False), Column('attempts', Integer, nullable=False),
    Column('lease', String), Column('lease_until', String), Column('payload', JSON, nullable=False),
    Column('message', String, nullable=False), Column('model', String),
    Column('created_at', String, nullable=False), Column('finished_at', String),
    UniqueConstraint('intake_id', 'kind', 'revision', name='uq_planning_job'))
proposals = Table('proposals', metadata,
    Column('id', String, primary_key=True), Column('intake_id', ForeignKey('intakes.id'), nullable=False),
    Column('revision', Integer, nullable=False), Column('scope_revision', Integer, nullable=False, server_default='0'), Column('content', JSON, nullable=False),
    Column('model', String, nullable=False), Column('basis', String, nullable=False),
    Column('created_at', String, nullable=False),
    UniqueConstraint('intake_id', 'revision', name='uq_proposal_revision'))
proposal_artifacts = Table('proposal_artifacts', metadata,
    Column('id', String, primary_key=True), Column('proposal_id', ForeignKey('proposals.id'), nullable=False),
    Column('role', String, nullable=False), Column('label', String, nullable=False),
    Column('sha256', String, nullable=False), Column('media_type', String, nullable=False),
    Column('data', LargeBinary, nullable=False), Column('created_at', String, nullable=False))
proposal_decisions = Table('proposal_decisions', metadata,
    Column('id', String, primary_key=True), Column('intake_id', ForeignKey('intakes.id'), nullable=False),
    Column('proposal_id', ForeignKey('proposals.id'), nullable=False),
    Column('scope_revision', Integer), Column('required_artifacts', JSON),
    Column('scope_review_required', Integer, nullable=False, server_default='1'),
    Column('binding', String, nullable=False), Column('payload', JSON, nullable=False),
    Column('snapshot', JSON, nullable=False), Column('status', String, nullable=False),
    Column('message', String, nullable=False), Column('created_at', String, nullable=False),
    Column('finished_at', String),
    UniqueConstraint('proposal_id', 'binding', name='uq_proposal_decision'))
# Extension of the existing backlog: organizational/actionable local planning records.
backlog_items = Table('backlog_items', metadata,
    Column('id', String, primary_key=True), Column('intake_id', ForeignKey('intakes.id'), nullable=False),
    Column('proposal_id', ForeignKey('proposals.id')), Column('parent', ForeignKey('backlog_items.id')),
    Column('kind', String, nullable=False), Column('title', String, nullable=False),
    Column('purpose', String, nullable=False), Column('acceptance', JSON, nullable=False),
    Column('model', String, nullable=False), Column('created_at', String, nullable=False))
backlog_dependencies = Table('backlog_dependencies', metadata,
    Column('item_id', ForeignKey('backlog_items.id'), primary_key=True),
    Column('requires_id', ForeignKey('backlog_items.id'), primary_key=True))

# Append-only human scope history, independent of planner content and original input.
intake_scopes = Table('intake_scopes', metadata,
    Column('intake_id', ForeignKey('intakes.id'), primary_key=True),
    Column('revision', Integer, primary_key=True),
    Column('command_id', String, unique=True, nullable=False),
    Column('visual_requirement', String, nullable=False),
    Column('stage', String, nullable=False), Column('origin', String, nullable=False),
    Column('note', String, nullable=False), Column('change_payload', JSON, nullable=False),
    Column('created_at', String, nullable=False))

# Append-only Workbench preferences for future controlled work, never pilot policy.
execution_policies = Table('execution_policies', metadata,
    Column('revision', Integer, primary_key=True), Column('payload', JSON, nullable=False),
    Column('saved_at', String, nullable=False), Column('actor', String, nullable=False))
# One event per durable planning lease, no reconstruction of historical attempts.
model_attempts = Table('model_attempts', metadata,
    Column('id', String, primary_key=True), Column('job_id', ForeignKey('planning_jobs.id')),
    Column('execution_id', ForeignKey('real_execution_requests.id'), unique=True),
    Column('task_id', String, nullable=False), Column('source', String, nullable=False),
    Column('backend', String), Column('purpose', String, nullable=False),
    Column('requested_model', String), Column('actual_model', String),
    Column('requested_effort', String), Column('actual_effort', String),
    Column('policy_revision', Integer), Column('routing_reason', String, nullable=False),
    Column('started_at', String, nullable=False), Column('finished_at', String),
    Column('outcome', String, nullable=False), Column('verification', String),
    Column('usage', JSON), Column('account_ref', String))

verification_requests = Table('verification_requests', metadata,
    Column('id', String, primary_key=True), Column('task_id', ForeignKey('tasks.id'), nullable=False),
    Column('version', String, nullable=False), Column('payload', JSON, nullable=False),
    Column('snapshot', JSON, nullable=False), Column('status', String, nullable=False),
    Column('attempt_outcome', String, nullable=False), Column('verification', String, nullable=False),
    Column('checks', JSON, nullable=False), Column('message', String, nullable=False),
    Column('created_at', String, nullable=False), Column('started_at', String), Column('finished_at', String),
    UniqueConstraint('task_id', 'version', name='uq_fixture_verification'))

# Admission audits planned feature scope, never a Phase 1 candidate or model job.
admission_requests = Table('admission_requests', metadata,
    Column('id', String, primary_key=True),
    Column('task_id', ForeignKey('backlog_items.id'), nullable=False),
    Column('approval_id', ForeignKey('proposal_decisions.id'), nullable=False),
    Column('binding', String, nullable=False), Column('payload', JSON, nullable=False),
    Column('snapshot', JSON, nullable=False), Column('status', String, nullable=False),
    Column('message', String, nullable=False), Column('created_at', String, nullable=False),
    Column('finished_at', String),
    UniqueConstraint('task_id', 'binding', name='uq_fixture_admission'))
Index('ix_admission_pending', admission_requests.c.status, admission_requests.c.created_at)

# Exact synthetic task implementation only; never model planning/execution accounting.
implementation_requests = Table('implementation_requests', metadata,
    Column('id', String, primary_key=True),
    Column('task_id', ForeignKey('backlog_items.id'), nullable=False),
    Column('admission_id', ForeignKey('admission_requests.id'), nullable=False),
    Column('binding', String, nullable=False), Column('payload', JSON, nullable=False),
    Column('snapshot', JSON, nullable=False), Column('status', String, nullable=False),
    Column('message', String, nullable=False), Column('created_at', String, nullable=False),
    Column('finished_at', String),
    UniqueConstraint('task_id', 'binding', name='uq_fixture_implementation'))
Index('ix_implementation_pending', implementation_requests.c.status, implementation_requests.c.created_at)
implementation_attempts = Table('implementation_attempts', metadata,
    Column('id', String, primary_key=True),
    Column('request_id', ForeignKey('implementation_requests.id'), nullable=False, unique=True),
    Column('outcome', String, nullable=False), Column('source', String, nullable=False),
    Column('started_at', String, nullable=False), Column('finished_at', String, nullable=False))
implementation_candidates = Table('implementation_candidates', metadata,
    Column('id', String, primary_key=True),
    Column('attempt_id', ForeignKey('implementation_attempts.id'), nullable=False, unique=True),
    Column('provenance', JSON, nullable=False), Column('artifact', LargeBinary, nullable=False),
    Column('created_at', String, nullable=False))
candidate_verification_requests = Table('candidate_verification_requests', metadata,
    Column('id', String, primary_key=True),
    Column('task_id', ForeignKey('backlog_items.id'), nullable=False),
    Column('candidate_id', ForeignKey('implementation_candidates.id'), nullable=False, unique=True),
    Column('payload', JSON, nullable=False), Column('snapshot', JSON, nullable=False),
    Column('status', String, nullable=False), Column('verification', String, nullable=False),
    Column('checks', JSON, nullable=False), Column('message', String, nullable=False),
    Column('created_at', String, nullable=False), Column('started_at', String),
    Column('finished_at', String))
Index('ix_candidate_verification_pending', candidate_verification_requests.c.status,
      candidate_verification_requests.c.created_at)


# Delivery evidence for existing synthetic candidates; no model or Phase 1 records.
pr_preparations = Table('pr_preparations', metadata,
    Column('id', String, primary_key=True),
    Column('task_id', ForeignKey('backlog_items.id'), nullable=False),
    Column('candidate_id', ForeignKey('implementation_candidates.id'), nullable=False),
    Column('binding', String, nullable=False, unique=True),
    Column('payload', JSON, nullable=False), Column('snapshot', JSON, nullable=False),
    Column('status', String, nullable=False), Column('message', String, nullable=False),
    Column('created_at', String, nullable=False), Column('started_at', String), Column('finished_at', String),
    Column('result', JSON))
Index('ix_pr_preparation_pending', pr_preparations.c.status, pr_preparations.c.created_at)
fake_publications = Table('fake_publications', metadata,
    Column('binding', String, primary_key=True),
    Column('request_id', ForeignKey('pr_preparations.id'), nullable=False, unique=True),
    Column('evidence', JSON, nullable=False), Column('evidence_sha256', String, nullable=False),
    Column('created_at', String, nullable=False))


# Explicit owner-authorized real work: no synthetic/planning approval relationships.
real_execution_grants = Table('real_execution_grants', metadata,
    Column('id', String, primary_key=True), Column('snapshot', JSON, nullable=False),
    Column('binding', String, nullable=False, unique=True),
    Column('issued_at', String, nullable=False), Column('expires_at', String, nullable=False),
    Column('revoked_at', String))
real_execution_requests = Table('real_execution_requests', metadata,
    Column('id', String, primary_key=True),
    Column('grant_id', ForeignKey('real_execution_grants.id'), nullable=False, unique=True),
    Column('payload', JSON, nullable=False), Column('status', String, nullable=False),
    Column('fence', String), Column('message', String, nullable=False),
    Column('created_at', String, nullable=False), Column('started_at', String),
    Column('finished_at', String), Column('result', JSON))


# Trusted recipe offers advertise capabilities; only a human decision grants a budget.
task_run_offers = Table('task_run_offers', metadata,
    Column('id', String, primary_key=True), Column('proposal_id', ForeignKey('proposals.id'),nullable=False),
    Column('fingerprint', String,nullable=False,unique=True), Column('snapshot',JSON,nullable=False),
    Column('created_at',String,nullable=False))
# Real delivery is linked to real execution, never a synthetic candidate/Phase1 task.
real_publications = Table('real_publications', metadata,
    Column('id',String,primary_key=True),Column('execution_id',ForeignKey('real_execution_requests.id'),nullable=False,unique=True),
    Column('task_id',ForeignKey('backlog_items.id'),nullable=False),Column('binding',String,nullable=False,unique=True),
    Column('snapshot',JSON,nullable=False),Column('status',String,nullable=False),Column('message',String,nullable=False),
    Column('state',JSON,nullable=False),Column('created_at',String,nullable=False),Column('finished_at',String))

# Preparation/controller lifecycle only. Existing grants/requests remain execution owners.
task_sessions = Table('task_sessions', metadata,
    Column('id', String, ForeignKey('proposal_decisions.id'), primary_key=True),
    Column('task_id', String, ForeignKey('backlog_items.id'), nullable=False),
    Column('snapshot', JSON, nullable=False), Column('binding', String, nullable=False),
    Column('status', String, nullable=False), Column('message', String, nullable=False),
    Column('state', JSON, nullable=False), Column('created_at', String, nullable=False),
    Column('finished_at', String))
