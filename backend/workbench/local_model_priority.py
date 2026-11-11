"""Trusted local-model scheduling, separate from generation outcome/accounting.

Reserve the existing cooperative locks for the entire Workbench operation, not
one HTTP request. Only a verified pilot maintenance process group may be yielded.
Never remove lock files, infer completion from process exit, or modify its queue.
The status projection observes real locks/process identities; a stale journal
does not establish either activity or terminal provider evidence.
"""
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import signal
import stat
import tempfile
import time

from sqlalchemy import select

from workbench.db import (intakes, planning_jobs, model_attempts, task_sessions,
                          real_execution_requests, write_transaction)

PILOT = Path('/home/rich/twichui-pilot/batch')
STATE = Path('/home/rich/.local/share/TwichUI-Workbench/model-priority')
KEY = Path('/home/rich/twichui-pilot/backup/.key')
_manager = None


def now():
    return datetime.now(timezone.utc).isoformat()


def configured():
    from workbench.planning_provider import configured_mode
    root = Path(__file__).resolve().parents[2]
    # Disposable tests and fixture previews cannot control the owner's model.
    group = Path('/proc/self/cgroup').read_text()
    return (configured_mode() == 'everyday' and root.is_relative_to(
        '/home/rich/TwichUI-Workbench/.local/m5-review') and
        ('workbench-everyday-worker.service' in group or '/workbench-real-' in group))


def process(pid, proc=Path('/proc')):
    p = proc/str(pid)
    try:
        fields = (p/'stat').read_text().split(') ', 1)[1].split()
        if p.stat().st_uid != os.getuid() or fields[0] == 'Z':
            return None
        return dict(pid=int(pid), start=fields[19], group=int(fields[2]), parent=int(fields[1]),
            cgroup=(p/'cgroup').read_text().strip(),
            boot=(proc/'sys/kernel/random/boot_id').read_text().strip())
    except (OSError, ValueError, IndexError):
        return None


def owned_file(path):
    if path.resolve() != path or path.is_symlink():
        raise ValueError('Model coordination path changed')
    s = path.stat()
    if s.st_uid != os.getuid() or not stat.S_ISREG(s.st_mode):
        raise ValueError('Model coordination ownership changed')
    return s


class Host:
    def __init__(self, pilot=PILOT, directory=STATE, proc=Path('/proc')):
        self.pilot, self.directory, self.proc = pilot, directory, proc
        self.paths = [pilot/'cycle.lock', pilot/'queue/model-request.lock']

    def identity(self, pid):
        return process(pid,self.proc)

    def holders(self):
        targets = {}
        for path in self.paths:
            s = owned_file(path)
            targets[(os.major(s.st_dev), os.minor(s.st_dev), s.st_ino)] = path
        result = {}
        for line in (self.proc/'locks').read_text().splitlines():
            words = line.split()
            if '->' in words or len(words) < 6 or words[1:4] != ['FLOCK','ADVISORY','WRITE']:
                continue
            device = words[5].split(':')
            key = (int(device[0],16), int(device[1],16), int(device[2]))
            if key in targets:
                owner = self.identity(int(words[4]))
                if owner:
                    result[str(targets[key])] = owner
        return result

    def maintenance(self, owner):
        """Verify actual cycle ownership and group; never trust a stale PID file."""
        if not owner or owner['cgroup'] != '0::/system.slice/twichui-worker.service/main':
            return None
        current = owner
        for _ in range(16):
            p = self.proc/str(current['pid'])
            try:
                argv = (p/'cmdline').read_bytes().split(b'\0')
                if argv[:3] == [b'/usr/bin/python3',b'-I',os.fsencode(self.pilot/'cycle.py')]:
                    saved = json.loads((self.pilot/'queue/cycle-state.json').read_text()).get('running') or {}
                    if saved.get('pid') == current['pid'] == current['group'] and self.identity(current['pid']) == current:
                        return current
                    return None
                parent = self.identity(current['parent'])
                if not parent or parent['cgroup'] != owner['cgroup']:
                    return None
                current = parent
            except (OSError, ValueError):
                return None
        return None

    def interrupt(self, owner, *, force=False):
        cycle = self.maintenance(owner)
        if not cycle:
            return False
        members = []
        for p in self.proc.iterdir():
            if not p.name.isdigit():
                continue
            member = self.identity(int(p.name))
            if member and member['group'] == cycle['group']:
                if member['cgroup'] != cycle['cgroup']:
                    return False
                members.append(member)
        if not members or self.identity(cycle['pid']) != cycle:
            return False
        sig = signal.SIGKILL if force else signal.SIGTERM
        self.save_event('yield',dict(cycle=cycle,members=members,requested_signal=sig.name,
            maintenance_final_outcome='unknown',maintenance_final_usage='unknown',
            workbench_execution_authorized=False,
            preservation='Maintenance queue, isolated source and interruption accounting remain with their original owner.'))
        # PID/start/cgroup/group were checked immediately before this supported
        # maintenance interruption. No arbitrary command, user, or service target.
        if self.identity(cycle['pid']) != cycle:
            return False
        os.killpg(cycle['group'], sig)
        return True

    def idle(self):
        from workbench.real_lease import request, epoch
        before = epoch()
        owned_file(KEY)
        if KEY.stat().st_mode & 0o077:
            raise ValueError('Local model authentication permissions changed')
        status, raw = request(KEY.read_text().strip(), 'GET', '/slots')
        slots = json.loads(raw)
        if status != 200 or not isinstance(slots,list) or len(slots) != 1:
            raise ValueError('Local model readiness is unavailable')
        return bool(before and epoch() == before and slots[0].get('is_processing') is False)

    def prepare(self):
        self.directory.mkdir(mode=0o700,exist_ok=True)
        if self.directory.resolve() != self.directory or self.directory.stat().st_uid != os.getuid() or self.directory.stat().st_mode & 0o077:
            raise ValueError('Private model scheduling directory changed')

    def save(self, value):
        self.prepare()
        if (self.directory/'status.json').is_symlink():
            raise ValueError('Model scheduling status path changed')
        fd, name = tempfile.mkstemp(prefix='.status-',dir=self.directory)
        try:
            with os.fdopen(fd,'w') as f:
                json.dump(value,f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(name,self.directory/'status.json')
        finally:
            Path(name).unlink(missing_ok=True)

    def save_event(self, kind, value):
        from uuid import uuid4
        self.prepare()
        # Only ownership transitions/interruption proofs, not a polling log.
        p = self.directory/(kind+'-'+str(uuid4())+'.json')
        with p.open('x') as f:
            json.dump(dict(observed_at=now(),**value),f)
            f.flush()
            os.fsync(f.fileno())


class Reservation:
    def __init__(self, host=None):
        self.host = host or Host()
        self.guard = None
        self.handles = {}
        self.owner = self.host.identity(os.getpid())
        self.yielded = {}
        self.ready = False
        self.ids = []
        self.state = 'waiting'

    def publish(self, state, message):
        self.state = state
        self.host.save(dict(version=1,owner=self.owner,state=state,message=message,
            intake_ids=self.ids,observed_at=now(),automatic=state != 'held',
            generation_outcome_inferred=False))

    def step(self, identities):
        self.ids = list(dict.fromkeys(identities))
        if not self.ids:
            self.close()
            return False
        for path, handle in self.handles.items():
            a,b = os.fstat(handle.fileno()),owned_file(path)
            if (a.st_dev,a.st_ino)!=(b.st_dev,b.st_ino):
                raise ValueError('Held model lock ownership changed')
        if self.guard is None:
            self.host.prepare()
            path = self.host.directory/'reservation.lock'
            fd = os.open(path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
            self.guard = os.fdopen(fd,'a+b')
            owned_file(path)
            try:
                fcntl.flock(self.guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                self.guard.close()
                self.guard=None
                return False  # Another actual Workbench owner; do not overwrite its journal.
            old = self.host.directory/'status.json'
            if old.is_file():
                previous = json.loads(old.read_text())
                if previous.get('owner') != self.owner and previous.get('state') != 'released':
                    self.host.save_event('reconciled-reservation',dict(previous=previous,
                        replacement=self.owner,provider_completion_inferred=False))
        if self.ready:
            return True
        holders = self.host.holders()
        for owner in holders.values():
            cycle = self.host.maintenance(owner)
            if cycle:
                key = (cycle['pid'],cycle['start'],cycle['boot'])
                if key not in self.yielded:
                    self.publish('yielding','Waiting for local Qwen. Workbench is asking the verified maintenance cycle to yield; this saved request will resume automatically.')
                    self.yielded[key] = dict(at=time.monotonic(),stage='requested')
                    if self.host.interrupt(cycle):
                        self.yielded[key]['stage'] = 'term'
                elif self.yielded[key]['stage']=='term' and time.monotonic()-self.yielded[key]['at']>=15:
                    self.yielded[key]['stage'] = 'forced'
                    self.host.interrupt(cycle,force=True)
                elif self.yielded[key]['stage'] in {'forced','requested'}:
                    self.publish('held','Local model access needs checking. The verified maintenance owner has not yielded after bounded interruption. Your revision is saved; no new generation started.')
                return False
        for path in self.host.paths:
            if path in self.handles:
                continue
            owned_file(path)
            f = path.open('rb')
            try:
                fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                f.close()
                self.publish('waiting','Waiting for local Qwen. Another verified model reservation is still held. Workbench will resume this saved request automatically after it yields.')
                return False
            self.handles[path] = f
        if not self.host.idle():
            self.publish('reconciling','Waiting for local Qwen. Workbench holds the model reservation and is checking that earlier generation has stopped. No new request has started.')
            return False
        self.ready = True
        self.publish('reserved','Local Qwen is reserved for Workbench. The saved request is next; background maintenance cannot take this reservation between its model requests.')
        return True

    def borrows(self, path):
        if not self.ready or self.host.identity(os.getpid()) != self.owner:
            return False
        f = self.handles.get(Path(path))
        if f is None or f.closed:
            return False
        a,b=os.fstat(f.fileno()),owned_file(Path(path))
        return (a.st_dev,a.st_ino)==(b.st_dev,b.st_ino)

    def close(self):
        self.ready = False
        if self.guard is not None:
            try:
                self.publish('released','Workbench released its local-model reservation. No provider outcome or usage was changed.')
            finally:
                for f in reversed(list(self.handles.values())):
                    f.close()
                self.handles.clear()
                self.guard.close()
                self.guard=None
        self.ids = []


def connection(source):
    return source.connect() if hasattr(source,'connect') else nullcontext(source)


def planning_demand(engine, *, eligible=False):
    with connection(engine) as c:
        rows = c.execute(select(intakes.c.id,intakes.c.revision,planning_jobs.c.revision.label('job_revision'),
            planning_jobs.c.kind,
            planning_jobs.c.status,planning_jobs.c.payload).join(planning_jobs,planning_jobs.c.intake_id==intakes.c.id).where(
            ((planning_jobs.c.kind=='planning') | ((planning_jobs.c.kind=='decomposition')
                & intakes.c.payload['feature_planning'].as_boolean().is_(True))),
            planning_jobs.c.status.in_(['queued','running']),
            intakes.c.payload['local_planning'].as_string().in_(
                ['qwen-once','qwen-with-codex-fallback','codex-with-local-fallback']),
            intakes.c.payload['supported_task'].as_string().is_(None))).mappings().all()
        return [r['id'] for r in rows if not eligible or r['status']=='running' or
            (r['kind']=='decomposition' and r['job_revision']==r['revision'] and r['payload'].get('feature_base')) or
            (r['job_revision']>r['revision'] and r['payload'].get('local_planning_receipt'))]


def reconcile(engine):
    global _manager
    if not configured():
        return
    if _manager is None:
        _manager = Reservation()
    demand = planning_demand(engine,eligible=True)
    with engine.connect() as c:
        execution_pending = c.execute(select(task_sessions.c.id).where(
            task_sessions.c.status.in_(['pending','active','waiting_recovery']))).first()
        execution_pending = execution_pending or c.execute(select(real_execution_requests.c.id).where(
            real_execution_requests.c.status.in_(['pending','running','checking','cancel_requested']))).first()
    if execution_pending:
        # The existing scheduler settles execution before planning. Its separate
        # controller acquires its own reservation; never block that handoff by
        # retaining these locks in the parent worker.
        _manager.close()
        return
    try:
        ready = _manager.step(demand)
    except (OSError,ValueError,RuntimeError):
        _manager.ready = False
        if _manager.guard:
            _manager.publish('held','Local model access needs attention. Workbench could not safely verify the reservation or earlier activity. The saved request is held; no generation started.')
        ready = False
    if demand and not ready:
        state = snapshot(engine) or {}
        with write_transaction(engine) as c:
            for job in c.execute(select(planning_jobs).where(planning_jobs.c.intake_id.in_(demand),
                    planning_jobs.c.kind.in_(['planning','decomposition']),planning_jobs.c.status=='queued')).mappings():
                message = state.get('message','Waiting for local Qwen. Workbench is reconciling model access; your saved request remains scheduled.')
                if job['message'] != message:
                    c.execute(planning_jobs.update().where(planning_jobs.c.id==job['id']).values(message=message))


def borrowing(path):
    return bool(_manager and _manager.borrows(path))


def planning_ready():
    return _manager is None or not _manager.ids or _manager.ready


def waiting_message():
    if _manager:
        try:
            return json.loads((_manager.host.directory/'status.json').read_text())['message']
        except (OSError,ValueError,KeyError):
            pass
    return 'Waiting for local Qwen. Workbench is checking who holds the model; the saved request remains scheduled.'


def close():
    if _manager:
        _manager.close()


@contextmanager
def execution_reservation():
    """A local execution controller owns its reservation through model restoration."""
    global _manager
    if not configured():
        yield
        return
    previous = _manager
    manager = previous or Reservation()
    _manager = manager
    try:
        end = time.monotonic()+120
        while not manager.step(['approved-local-execution']):
            if time.monotonic() >= end:
                raise RuntimeError('Local model priority handoff did not settle; no model configuration or generation changed')
            time.sleep(.25)
        yield
    finally:
        if previous is None:
            manager.close()
        _manager = previous


def snapshot(engine):
    """Bounded local reads only; no model call or process control during rendering."""
    from workbench.planning_provider import configured_mode
    if configured_mode() != 'everyday':
        return None
    host = Host()
    try:
        holders = host.holders()
        p = host.directory/'status.json'
        record = json.loads(p.read_text()) if p.is_file() and not p.is_symlink() and p.stat().st_size < 16384 else {}
        owner = record.get('owner') or {}
        alive = bool(owner and host.identity(owner.get('pid')) == owner)
        observed = record.get('observed_at') if alive else None
        maintenance = any(host.maintenance(o) for o in holders.values())
        demand = planning_demand(engine)
        with connection(engine) as c:
            claims = c.execute(select(planning_jobs.c.payload).join(model_attempts,
                model_attempts.c.job_id==planning_jobs.c.id).where(model_attempts.c.backend=='local',
                model_attempts.c.outcome=='running',model_attempts.c.finished_at.is_(None),
                planning_jobs.c.status=='running')).scalars().all()
        from workbench.real_execution import alive as generation_owner_alive
        active = any(generation_owner_alive(claim) and any(
            h['pid']==claim.get('pid') and h['start']==claim.get('start') and 'workbench-' in h['cgroup']
            for h in holders.values()) for p in claims if (claim:=p.get('scenario_generation_claim')))
        locked = any(o['pid']==owner.get('pid') for o in holders.values())
        if maintenance:
            state = 'yielding' if demand and alive and record.get('state')=='yielding' else 'waiting' if demand else 'maintenance'
            message = ('Waiting for local Qwen. A verified maintenance cycle currently holds the model. Workbench will yield it and resume this saved request automatically.' if demand else
                'No addon work is running. The local model is reserved by a verified background maintenance cycle.')
        elif active:
            state,message='working','Local Qwen is working on a saved Workbench request.'
        elif demand and alive and set(demand) & set(record.get('intake_ids',[])):
            state,message=record.get('state','waiting'),record.get('message','Waiting for local Qwen.')
        elif demand:
            state,message='reconciling','Your revision is queued. Workbench is reconciling model ownership and will resume it automatically; an old reservation alone does not prove maintenance is running.'
        elif alive and locked:
            state,message='reserved','Local Qwen is reserved for approved Workbench work.'
        else:
            state,message='idle','No Workbench request is waiting for the local model.'
        return dict(state=state,message=message,automatic=state!='held',intake_ids=demand,
            maintenance_active=maintenance,reservation_owner_alive=alive,reservation_locked=locked,
            observed_at=observed)
    except (OSError,ValueError,KeyError,TypeError):
        return dict(state='reconciling',message='Local-model ownership information is unavailable. Workbench is checking access; no maintenance activity is inferred from an old reservation.',automatic=True,
            intake_ids=[],maintenance_active=False,reservation_owner_alive=False,reservation_locked=False,observed_at=None)
