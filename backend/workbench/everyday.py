"""Installation readiness and admission for ordinary, separately approved local work."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import shutil
import threading

from pydantic import Field
from sqlalchemy import select

from workbench.schemas import StrictModel

ROOT = Path(__file__).resolve().parents[2]
MODEL = 'pilot-qwen38-27b'
START_RESERVE = 72 << 20
CLOSEOUT_RESERVE = 4 << 20
PLANNING_MINUTES = 30


class LocalWorkStatus(StrictModel):
    enabled: bool
    ready: bool
    message: str
    headroom_bytes: int | None = None
    reserve_bytes: int = START_RESERVE
    codex_configured: bool = False
    codex_available: bool = False
    bug_investigation: bool = False


class Installation(StrictModel):
    database: str
    database_identity: list[int] = Field(min_length=2, max_length=2)
    evidence_roots: list[str] = Field(min_length=1)
    scratch: str
    # Legacy field name: this is now a retention watermark, not a delivery cap.
    evidence_limit_bytes: int = Field(default=1073741824, ge=64 << 20)


def enabled():
    return os.environ.get('WORKBENCH_PLANNER') == 'everyday'


def configuration(engine=None):
    from workbench.trusted_preparation import owned

    path = owned(os.environ.get('WORKBENCH_EVERYDAY_CONFIG') or '/nonexistent')
    if not path.is_file() or path.stat().st_mode & 0o077 or path.stat().st_size > 1 << 20:
        raise ValueError('The private Workbench installation policy is unavailable.')
    policy = Installation.model_validate_json(path.read_bytes())
    database = owned(policy.database)
    if (not database.is_file() or policy.database != str(database)
            or policy.database_identity != [database.stat().st_dev, database.stat().st_ino]
            or (engine is not None and engine.url.database != str(database))):
        raise ValueError('The Workbench policy belongs to a different history store.')
    roots = [Path(root) for root in policy.evidence_roots]
    scratch = owned(policy.scratch)
    # Existing development evidence can be in a group-writable checkout.
    # These roots supply accounting only, never configuration or authority.
    if (len(roots) != len(set(roots)) or ROOT not in roots
            or not all(root.is_absolute() and root.resolve() == root and root.is_dir()
                       and root.stat().st_uid == os.getuid() for root in roots)
            or policy.scratch != str(scratch) or not scratch.is_dir()
            or scratch.stat().st_mode & 0o077 or os.environ.get('TMPDIR') != str(scratch)):
        raise ValueError('The Workbench storage or scratch configuration changed.')
    return policy


def measure(policy):
    # Admission uses actual physical recovery room. Historical evidence totals
    # remain measured for retention, without reinstating a cumulative cap.
    roots = [Path(root) for root in policy.evidence_roots]
    scratch = Path(policy.scratch)
    free = min(shutil.disk_usage(path).free for path in (*roots, scratch, Path('/dev/shm')))
    scratch_bytes = sum(path.stat().st_size for path in scratch.rglob('*')
                        if path.is_file() and not path.is_symlink())
    return free-(4 << 30), free, scratch_bytes


def housekeep(policy):
    from evidence_retention import maintain
    from project_env import artifact_bytes

    # Generic retention only touches closed owned browser evidence. Histories,
    # execution/recovery records and original stores are never candidates.
    for name in policy.evidence_roots:
        root = Path(name)
        if artifact_bytes(root) > policy.evidence_limit_bytes:
            maintain(root)


class CapacityHold(ValueError):
    def __init__(self, message, headroom):
        super().__init__(message)
        self.headroom = headroom


def require_room(policy, reserve=START_RESERVE):
    for _ in range(3):
        try:
            remaining, free, scratch = measure(policy)
            break
        except FileNotFoundError:
            continue  # Owned temporary check files may settle during a scan.
    else:
        raise ValueError('Workbench could not measure storage capacity.') from None
    if free < 4 << 30 or scratch > 1 << 30:
        raise CapacityHold('Waiting for resources. Workbench needs 4 GiB free and at most 1 GiB of scratch in use.', remaining)
    if remaining < reserve:
        raise CapacityHold(f'Waiting for storage. Workbench needs {reserve >> 20} MiB beyond its physical recovery reserve.', remaining)
    return remaining


def status(engine):
    from workbench.real_execution import alive
    from workbench.task_sessions import capability
    from workbench.trusted_preparation import configuration as preparation_config, owned

    if not enabled():
        return LocalWorkStatus(enabled=False, ready=False, message='Ordinary local execution is unavailable.')
    try:
        policy = configuration(engine)
        prepared = preparation_config(engine)
        if not capability(engine) or not prepared['scenario_generation']:
            raise ValueError('Workbench setup or its installed model proof needs attention.')
        marker = owned(ROOT / '.local/everyday-worker.json')
        if not marker.is_file() or marker.stat().st_size > 1024:
            raise ValueError('Workbench worker readiness could not be verified.')
        receipt = json.loads(marker.read_text())
        if receipt['database_identity'] != policy.database_identity or not alive(receipt['owner']):
            raise ValueError('Waiting for the Workbench worker to start.')
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        return LocalWorkStatus(enabled=True, ready=False,
                               message='Waiting for the Workbench worker or its local setup. Check again after it is ready.')
    try:
        remaining = require_room(policy)
    except CapacityHold as error:
        return LocalWorkStatus(enabled=True, ready=False, message=str(error), headroom_bytes=error.headroom)
    except (OSError, ValueError, RuntimeError):
        return LocalWorkStatus(enabled=True, ready=False, message='Workbench could not measure storage capacity.')
    from workbench.codex_provider import connector_path
    return LocalWorkStatus(enabled=True, ready=True, headroom_bytes=remaining,
                           bug_investigation='lua-offline-bugfix' in prepared['allowed_classes'],
                           codex_configured=bool(os.environ.get('WORKBENCH_CODEX_SOCKET')),
                           codex_available=connector_path() is not None,
                           message='The worker is available. It checks model access before any approved work starts.')


@contextmanager
def worker_owner(engine):
    from workbench.real_execution import owner_identity
    from workbench.real_lease import atomic

    policy = configuration(engine)
    directory = ROOT / '.local'
    fd = os.open(directory / 'everyday-worker.lock', os.O_CREAT | os.O_NOFOLLOW | os.O_RDWR, 0o600)
    with os.fdopen(fd, 'r+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        atomic(directory / 'everyday-worker.json',
               json.dumps(dict(owner=owner_identity(), database_identity=policy.database_identity)).encode())
        yield policy


@contextmanager
def watch_capacity(policy, stopping):
    """Stop owned work using its existing cancellation and restoration path."""
    cancelled = threading.Event()
    finished = threading.Event()

    def monitor():
        while not finished.wait(0.5):
            try:
                require_room(policy, CLOSEOUT_RESERVE)
            except (OSError, RuntimeError, ValueError) as error:
                cancelled.reason = str(error) if isinstance(error, ValueError) else 'Workbench could not verify storage capacity.'
                cancelled.set()
                return
            if stopping.is_set():
                cancelled.set()
                return

    monitor_thread = threading.Thread(target=monitor, daemon=True, name='workbench-capacity')
    monitor_thread.start()
    try:
        yield cancelled
    finally:
        finished.set()
        monitor_thread.join()


def clean_workspace(evidence):
    """Delete only a settled scratch copy created and recorded by this installation."""
    from workbench.real_workspace import durable
    from workbench.trusted_preparation import owned

    marker = evidence / 'everyday-scratch.json'
    if not marker.exists():
        return True
    try:
        receipt = json.loads(owned(marker).read_text())
        path = Path(receipt['path'])
        scratch = Path(configuration().scratch)
        if path.parent != scratch or not path.name.startswith('workbench-verify-'):
            raise ValueError('Scratch location changed')
        if path.exists() or path.is_symlink():
            info = owned(path).stat()
            if [info.st_dev, info.st_ino] != receipt['identity'] or not path.is_dir():
                raise ValueError('Scratch ownership changed')
            shutil.rmtree(path)
        durable(evidence / 'scratch-cleaned.json', b'{"owned_disposable_copy_removed":true}')
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def local_selection(connection):
    """Resolve the only model eligible for an everyday run before it starts."""
    from workbench.model_policy import read_connection
    from workbench.schemas import ScenarioModelSelection

    saved = read_connection(connection)
    local = saved.providers['local']
    if saved.revision and local.enabled and local.model == MODEL and local.effort is None:
        return ScenarioModelSelection(policy_revision=saved.revision, model=local.model)
    raise ValueError('Enable the installed local Qwen model in Models and leave its effort unspecified.')


def planning_receipt(original, created_at):
    from workbench.intake import digest

    from workbench.bug_investigation import requested
    return dict(binding=digest(original), expires_at=(
        datetime.fromisoformat(created_at) + timedelta(minutes=PLANNING_MINUTES)).isoformat(),
        **(dict(investigation_version=2, max_local_requests=4, max_codex_requests=1,
                planning_deadline_seconds=120) if requested(original) else {}))


def check_planning(c, original, job=None):
    from workbench.db import planning_jobs
    jobs = c.execute(select(planning_jobs.c.payload).where(
        planning_jobs.c.intake_id == original['id'], planning_jobs.c.kind == 'planning')).scalars().all()
    from workbench.provider_routes import CHOICES
    expected = planning_receipt(original['payload'], original['created_at'])
    if (original['payload'].get('local_planning') not in CHOICES
            or not any(all(job.get('local_planning_receipt', {}).get(k) == v for k, v in expected.items()) for job in jobs)):
        raise ValueError('A current saved planning approval is required. Open a new task to give that permission.')
    claimed = sum(bool(payload.get('scenario_generation_claim')) for payload in jobs)
    followup = job['payload'].get('bug_followup') if job else None
    if followup:
        from workbench.intake import digest
        from workbench.provider_routes import approved

        data = job['payload']
        binding = digest(dict(original=original['payload'], revision=original['revision'],
                              feedback=data['feedback'], clarification_id=data['clarification_id']))
        if (original['revision'] != 1 or job['revision'] != 2 or claimed > 1
                or followup['binding'] != binding or followup['route'] != approved(c, original)
                or data.get('scenario_generation_claim')):
            raise ValueError('The follow-up no longer matches the approved investigation.')
        expected = followup
    elif claimed:
        raise ValueError('The approved planning operation has already been claimed. Start a new task with updated details.')
    if datetime.now(timezone.utc) + timedelta(seconds=120) >= datetime.fromisoformat(expected['expires_at']):
        raise ValueError('The planning approval expired before the worker could start. Start a new task to approve planning again.')
