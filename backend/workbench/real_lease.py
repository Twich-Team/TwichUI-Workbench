"""One bounded, private authentication lease for the existing owned primary.

No credentials in argv, reports or subprocess output. Privileged/same-UID processes
remain trusted: this is not an OS boundary against reading the runner's credential.
"""
from contextlib import ExitStack
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

from workbench.planning_transport import LocalHTTP

SERVICE = 'twichui-llm.service'
ENV = Path('/home/rich/twichui-pilot/serve.env')
UNIT = Path('/etc/systemd/system/twichui-llm.service')
KEY = Path('/home/rich/twichui-pilot/backup/.key')
LOCKS = [Path('/home/rich/twichui-pilot/batch/cycle.lock'),
         Path('/home/rich/twichui-pilot/batch/queue/model-request.lock')]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic(path, data, mode=0o600):
    fd, name = tempfile.mkstemp(prefix='.lease-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            os.fchmod(f.fileno(), mode)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
        fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        Path(name).unlink(missing_ok=True)


def system(*args):
    r = subprocess.run(['/usr/bin/systemctl', *args, SERVICE], capture_output=True, timeout=15)
    if r.returncode:
        raise RuntimeError('Owned service metadata unavailable')
    return r.stdout.decode().strip()


def epoch():
    pid = int(system('show', '--property=MainPID', '--value'))
    if not pid:
        return None
    p = Path('/proc')/str(pid)
    if p.stat().st_uid != os.getuid() or '/system.slice/'+SERVICE not in (p/'cgroup').read_text():
        raise RuntimeError('Service ownership mismatch')
    return [pid, (p/'stat').read_text().split(') ', 1)[1].split()[19]]


def request(secret, method, path, data=None, header='Authorization'):
    headers = {'Content-Type': 'application/json'}
    if secret:
        headers[header] = ('Bearer ' if header == 'Authorization' else '')+secret
    req = urllib.request.Request('http://127.0.0.1:8080'+path, data=data, headers=headers, method=method)
    try:
        with LocalHTTP() as c:
            raw = c.read(req, 131072, timeout=2)
        return 200, raw
    except Exception as error:
        status = getattr(error, 'metadata', {}).get('http_status')
        if status:
            return status, b''
        raise RuntimeError('Fixed primary request unavailable') from None


def process_matches(record, expected_secret=None):
    current = epoch()
    if not current:
        return True  # A failed/stopped unit may still have its exact config restored.
    p = Path('/proc')/str(current[0])
    if digest((p/'cmdline').read_bytes()) != record['argv_sha256']:
        return False
    if digest((p/'exe').read_bytes()) != record['executable_sha256']:
        return False
    if expected_secret is not None:
        env = dict(v.split(b'=', 1) for v in (p/'environ').read_bytes().split(b'\0') if b'=' in v)
        if env.get(b'LLAMA_API_KEY') != expected_secret.encode():
            return False
    return True


def restart():
    for action in ('stop', 'start'):
        r = subprocess.run(['sudo', '-n', '/usr/bin/systemctl', action, SERVICE],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        if r.returncode:
            raise RuntimeError('Owned '+action+' failed; inspect private lease state')


def wait_ready(secret, end):
    while time.monotonic() < end:
        try:
            status, raw = request(secret, 'GET', '/props')
            if status == 200:
                return json.loads(raw)
        except (RuntimeError, ValueError):
            pass
        time.sleep(.25)
    raise RuntimeError('Owned service readiness deadline exceeded')


def restore(private):
    """Idempotent operator/watchdog path; refuses unexpected config or ownership."""
    private = Path(private)
    with (private/'restore.lock').open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads((private/'state.json').read_text())
        if state.get('restored'):
            return state
        original = (private/'serve.env.original').read_bytes()
        temporary = (private/'serve.env.temporary').read_bytes()
        secret = (private/'temporary.key').read_text()
        if ENV.is_symlink() or ENV.stat().st_uid != os.getuid():
            raise RuntimeError('Restoration held: environment ownership changed')
        current = ENV.read_bytes()
        expected_mode = 0o600 if current == temporary else state['original_mode']
        if stat.S_IMODE(ENV.stat().st_mode) != expected_mode:
            raise RuntimeError('Restoration held: environment permissions changed')
        if digest(UNIT.read_bytes()) != state['unit_sha256'] or current not in (original, temporary):
            raise RuntimeError('Restoration held: intervening unit/environment change; original backup retained')
        if current == temporary:
            old_key = (private/'original.key').read_text()
            owned_initial = epoch() == state['original_epoch'] and process_matches(state, old_key)
            if not process_matches(state, secret) and not owned_initial:
                raise RuntimeError('Restoration held: current process does not match owned temporary service')
            state['restoration_started'] = True
            atomic(private/'state.json', json.dumps(state).encode())
            atomic(ENV, original, state['original_mode'])
            if state.get('restart_begun', True):
                restart()
        elif state.get('applied'):
            # Retry only our recorded restoration, never an intervening operator change.
            if not state.get('restoration_started'):
                raise RuntimeError('Restoration held: original file changed outside recorded restoration')
            old_key = (private/'original.key').read_text()
            if not process_matches(state, old_key):
                if not process_matches(state, secret) or epoch() != state.get('temporary_epoch'):
                    raise RuntimeError('Restoration held: changed service identity')
                restart()
        old = (private/'original.key').read_text()
        props = wait_ready(old, time.monotonic()+150)
        if (ENV.read_bytes() != original or stat.S_IMODE(ENV.stat().st_mode) != state['original_mode']
                or not process_matches(state) or props.get('model_path') != state['model_path']
                or props.get('default_generation_settings', {}).get('n_ctx') != 32768):
            raise RuntimeError('Restoration verification failed')
        rejected = [request(key, 'POST', '/v1/chat/completions', b'{')[0] for key in ('', secret)]
        if rejected != [401, 401]:
            raise RuntimeError('Restored access verification failed')
        state.update(restored=True, restored_at=time.time(), restored_epoch=epoch(),
                     original_config_verified=True, original_access_verified=True,
                     temporary_access_rejected=True)
        atomic(private/'state.json', json.dumps(state).encode())
        return state


class Lease:
    def __init__(self, private):
        self.private = Path(private)
        self.stack = ExitStack()
        self.secret = None
        self.established = None
        self.watchdog = None
        self.metadata = None

    def current_epoch(self):
        """Fast fence inside bounded operations; full systemd ownership is checked at lease entry.

        Never adopt a replacement PID. Avoid a15s systemctl timeout inside a5s
        cancellation budget. Only the already verified process epoch can match.
        """
        try:
            pid, start = self.server_epoch
            p = Path('/proc')/str(pid)
            actual = (p/'stat').read_text().split(') ', 1)[1].split()[19]
            if (actual == start and p.stat().st_uid == os.getuid() and
                    '/system.slice/'+SERVICE in (p/'cgroup').read_text()):
                return [pid, actual]
        except (OSError, AttributeError, ValueError):
            pass
        return None

    def current(self):
        try:
            state = json.loads((self.private/'state.json').read_text())
            return bool(self.established and time.time() < state['deadline']-300 and
                        not state.get('restored') and self.current_epoch() == self.server_epoch and
                        ENV.read_bytes() == (self.private/'serve.env.temporary').read_bytes())
        except (OSError, ValueError, KeyError):
            return False

    def __enter__(self):
        try:
            return self.apply()
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise

    def apply(self):
        self.private.mkdir(mode=0o700, parents=False, exist_ok=False)
        for path in LOCKS:
            f = self.stack.enter_context(path.open('rb'))
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if ENV.is_symlink() or ENV.stat().st_uid != os.getuid() or UNIT.stat().st_uid != 0:
            raise RuntimeError('Environment/unit ownership mismatch')
        if KEY.is_symlink() or KEY.stat().st_uid != os.getuid() or KEY.stat().st_mode & 0o077:
            raise RuntimeError('Original key must be private and owned')
        for conflict in ('llama.service', 'llama-80b.service', 'llama-qwen38.service'):
            active = subprocess.run(['/usr/bin/systemctl', 'is-active', conflict], capture_output=True, timeout=3).stdout.strip()
            if active in (b'active', b'activating', b'deactivating'):
                raise RuntimeError('Conflicting service active; no lifecycle change allowed')
        old = KEY.read_text().strip()
        status, raw = request(old, 'GET', '/props')
        if status != 200:
            raise RuntimeError('Original authenticated metadata unavailable')
        props = json.loads(raw)
        if props.get('build_info') != 'b11638-23deec025' or props.get('default_generation_settings', {}).get('n_ctx') != 32768:
            raise RuntimeError('Unexpected installed primary')
        expected = epoch()
        p = Path('/proc')/str(expected[0])
        argv = (p/'cmdline').read_bytes().split(b'\0')
        if b'127.0.0.1' not in argv or b'8080' not in argv or b'--api-key' in argv:
            raise RuntimeError('Unexpected bind/port or command-line credential')
        # A bounded drain is observational; no unrelated active work is canceled.
        end = time.monotonic()+120
        stable = 0
        while time.monotonic() < end:
            slots = json.loads(request(old, 'GET', '/slots')[1])
            idle = len(slots) == 1 and all(s.get('is_processing') is False for s in slots)
            stable = stable+1 if idle else 0
            if stable >= 6:
                break
            time.sleep(.5)
        if stable < 6 or epoch() != expected:
            raise RuntimeError('Could not drain a stable owned server; no restart')
        original = ENV.read_bytes()
        if any(line.lstrip().startswith(b'LLAMA_API_KEY=') for line in original.splitlines()):
            raise RuntimeError('Unexpected existing override; inspect before lease')
        self.secret = secrets.token_hex(32)+'-lease'  # Public suffix if installed trace logging masks all but last4.
        temporary = original+(b'' if original.endswith(b'\n') else b'\n')+b'LLAMA_API_KEY='+self.secret.encode()+b'\n'
        state = dict(original_epoch=expected, original_mode=stat.S_IMODE(ENV.stat().st_mode),
                     unit_sha256=digest(UNIT.read_bytes()), argv_sha256=digest((p/'cmdline').read_bytes()),
                     executable_sha256=digest((p/'exe').read_bytes()), model_path=props['model_path'],
                     setup_deadline=time.time()+240, owner_pid=os.getpid(),
                     owner_start=Path('/proc/self/stat').read_text().split(') ',1)[1].split()[19],
                     applied=False, restart_begun=False, restored=False)
        for name, data in [('serve.env.original', original), ('serve.env.temporary', temporary),
                           ('original.key', old.encode()), ('temporary.key', self.secret.encode())]:
            atomic(self.private/name, data)
        # Recoverability verified without changing the actual configuration.
        atomic(self.private/'restore-check', (self.private/'serve.env.original').read_bytes(), state['original_mode'])
        if (self.private/'restore-check').read_bytes() != original:
            raise RuntimeError('Backup round-trip failed')
        (self.private/'restore-check').unlink()
        atomic(self.private/'state.json', json.dumps(state).encode())
        # Watchdog is temporary; restores on owner loss/setup timeout/lease expiration.
        env = {'PATH': os.environ['PATH'], 'PYTHONPATH': str(Path(__file__).resolve().parents[1])}
        self.watchdog = subprocess.Popen([sys.executable, '-m', 'workbench.real_lease', str(self.private)],
                                        env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, start_new_session=True)
        if epoch() != expected or ENV.read_bytes() != original:
            raise RuntimeError('Ownership/configuration changed before apply')
        slots = json.loads(request(old, 'GET', '/slots')[1])
        if any(slot.get('is_processing') for slot in slots):
            raise RuntimeError('Work resumed during backup; no configuration or restart applied')
        atomic(ENV, temporary)
        state['applied'] = True
        atomic(self.private/'state.json', json.dumps(state).encode())
        slots = json.loads(request(old, 'GET', '/slots')[1])
        if any(slot.get('is_processing') for slot in slots):
            state['restart_begun'] = False
            atomic(self.private/'state.json', json.dumps(state).encode())
            raise RuntimeError('Work resumed before restart; restore file without stopping active work')
        state['restart_begun'] = True
        atomic(self.private/'state.json', json.dumps(state).encode())
        restart()
        # Start conservatively at the beginning of the first successful auth observation,
        # including model-loading time: readiness is later than credential isolation.
        auth_end = time.monotonic()+150
        while time.monotonic() < auth_end:
            observed_at = time.time()
            try:
                new_code, _ = request(self.secret, 'GET', '/props')
                old_code, _ = request(old, 'GET', '/props')
                if new_code in (200, 503) and old_code == 401:
                    self.established = observed_at
                    break
            except RuntimeError:
                pass
            time.sleep(.1)
        if not self.established:
            raise RuntimeError('Exclusive authenticated service not established')
        self.server_epoch = epoch()
        state.update(established_at=self.established, deadline=self.established+3600,
                     temporary_epoch=self.server_epoch)
        atomic(self.private/'state.json', json.dumps(state).encode())
        current_props = wait_ready(self.secret, auth_end)
        if not process_matches(state, self.secret) or current_props != props:
            # Some volatile props may differ: compare the actual model/context/build fields.
            for k in ('build_info', 'model_path', 'model_alias'):
                if current_props.get(k) != props.get(k):
                    raise RuntimeError('Model/configuration identity changed')
            if not process_matches(state, self.secret):
                raise RuntimeError('Process did not receive the exclusive credential')
        source = Path('/home/rich/llama.cpp/tools/server/server.cpp').read_text()
        import re
        routes = sorted(set(re.findall(r'ctx_http\.post\("([^"]+)"', source)))
        auth = []
        for route in routes:
            for label, key, header in [('unauthenticated', '', 'Authorization'),
                                       ('original', old, 'Authorization'), ('original-x-api-key', old, 'X-Api-Key')]:
                code, _ = request(key, 'POST', route, b'{', header)
                auth.append(dict(path=route, credential_class=label, status=code))
                if code != 401:
                    raise RuntimeError('Generation-capable route not authentication protected')
        slots = json.loads(request(self.secret, 'GET', '/slots')[1])
        if any(s.get('is_processing') for s in slots):
            raise RuntimeError('Unexpected active work after authentication tests')
        self.metadata = dict(backup_verified=True, original_mode=state['original_mode'],
                             auth_rejections=auth, established_at=self.established,
                             deadline=state['deadline'], epoch=self.server_epoch,
                             trust_boundary='Credential excludes ordinary old-key clients; privileged and same-UID processes remain trusted.')
        return self

    def __exit__(self, *_):
        try:
            if (self.private/'state.json').is_file():
                state = json.loads((self.private/'state.json').read_text())
                if state.get('applied') or ENV.read_bytes() == (self.private/'serve.env.temporary').read_bytes():
                    restore(self.private)
                else:
                    state['restored'] = True
                    atomic(self.private/'state.json', json.dumps(state).encode())
        finally:
            self.stack.close()
            if self.watchdog and json.loads((self.private/'state.json').read_text()).get('restored'):
                self.watchdog.wait(timeout=3)


def watchdog(private):
    private = Path(private)
    while True:
        state = json.loads((private/'state.json').read_text())
        if state.get('restored'):
            return
        try:
            p = Path('/proc')/str(state['owner_pid'])
            alive = (p/'stat').read_text().split(') ',1)[1].split()[19] == state['owner_start']
        except FileNotFoundError:
            alive = False
        if not alive or time.time() >= state.get('watchdog_deadline', state.get('deadline', state['setup_deadline'])):
            try:
                restore(private)
            except Exception as e:
                atomic(private/'restoration-error.json', json.dumps(dict(error=type(e).__name__,
                       state='Restoration held or failed; inspect exact owned config before manual recovery')).encode())
            return
        time.sleep(.5)


if __name__ == '__main__':
    watchdog(sys.argv[1])
