"""Native Codex transport owned by the existing private provider connector.

The credential is copied only into trusted controller storage. Core CLI tools
see the qualified namespace; they receive neither credentials nor host network.
One admitted session may contain multiple model calls. Their count is unknown
unless the native transport reports it; a session is never called one model call.
"""
import hashlib
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import tempfile
import time
from uuid import UUID

from workbench import native_boundary as boundary
from workbench.real_workspace import durable

UNIT_PREFIX = 'workbench-native-codex-'
WRAPPER = '''import os,sys,resource
from pathlib import Path
root=Path('/sys/fs/cgroup'+Path('/proc/self/cgroup').read_text().strip().split('::')[1])
for name,expected in {'memory.max':'1073741824','memory.swap.max':'0','pids.max':'128','cpu.max':'100000 100000'}.items():
 if (root/name).read_text().strip()!=expected:raise SystemExit('native resource readback failed')
resource.setrlimit(resource.RLIMIT_FSIZE,(4194304,4194304))
resource.setrlimit(resource.RLIMIT_CORE,(0,0))
resource.setrlimit(resource.RLIMIT_NOFILE,(512,512))
os.execv(sys.argv[1],sys.argv[1:])
'''


def toml(value):
    if isinstance(value, dict):
        return '{' + ','.join(json.dumps(k) + '=' + toml(v) for k, v in value.items()) + '}'
    return json.dumps(value)


def unit_name(request_id):
    return UNIT_PREFIX + UUID(request_id).hex


def manager_environment():
    runtime = Path('/run/user') / str(os.getuid())
    if runtime.resolve() != runtime or runtime.stat().st_uid != os.getuid() or runtime.stat().st_mode & 0o077:
        raise ValueError('Existing private user service manager required')
    return dict(PATH='/usr/bin:/bin', XDG_RUNTIME_DIR=str(runtime),
        DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(runtime / 'bus'))


def stop_unit(name):
    if not name.startswith(UNIT_PREFIX) or len(name) != len(UNIT_PREFIX) + 32:
        raise ValueError('Bound native unit required')
    manager = manager_environment()
    subprocess.run(['systemctl', '--user', 'stop', name], capture_output=True, timeout=8, env=manager)
    observed = subprocess.run(['systemctl', '--user', 'show', name,
        '--property=ActiveState,ControlGroup', '--no-pager'], capture_output=True, text=True, timeout=5, env=manager)
    fields = dict(line.split('=', 1) for line in observed.stdout.splitlines() if '=' in line)
    group = fields.get('ControlGroup')
    if observed.returncode != 0 or fields.get('ActiveState') not in {'inactive', 'failed'}:
        return False
    if group:
        path = Path('/sys/fs/cgroup' + group)
        if path.exists() and 'populated 1' in (path / 'cgroup.events').read_text():
            return False
    return True


def tokens(events):
    terminal = [e for e in events if e.get('type') == 'turn.completed']
    failed = any(e.get('type') == 'turn.failed' for e in events)
    counts = terminal[0].get('usage', {}) if len(terminal) == 1 and not failed else {}
    fields = {'prompt_tokens': 'input_tokens', 'completion_tokens': 'output_tokens',
        'reasoning_tokens': 'reasoning_output_tokens', 'cached_input_tokens': 'cached_input_tokens'}
    values = {key: counts[native] for key, native in fields.items()
        if type(counts.get(native)) is int and counts[native] >= 0}
    if {'prompt_tokens', 'completion_tokens'} <= values.keys():
        values['total_tokens'] = values['prompt_tokens'] + values['completion_tokens']
    return dict(units='tokens', status='complete' if 'total_tokens' in values else 'unavailable',
        values=values or None, known_values=values or None, requests_observed=None,
        native_cli_invocations=1,
        native_sessions_observed=len({event.get('thread_id') for event in events
            if event.get('type') == 'thread.started' and isinstance(event.get('thread_id'), str)}),
        model_requests_observed=None,
        provenance=dict(method='Codex CLI native turn.completed; model request count unavailable',
            reasoning_included_in_completion=True))


def execute(root, source, scratch, request_id, model, effort, prompt, credential, *, stop, seconds=900, watched=None):
    """Bounded stream supervision and whole-cgroup settlement before export."""
    if not 30 <= seconds <= 900 or not boundary.binaries_valid():
        raise ValueError('Pinned native CLI and bounded execution required')
    root, source, scratch = map(Path, (root, source, scratch))
    name = unit_name(request_id)
    # Create-only intent fences dispatch even if the connector dies before Popen.
    durable(root / 'native-dispatch.json', json.dumps(dict(unit=name, request_id=request_id,
        model=model, requested_effort=effort, started_at=time.time()), sort_keys=True).encode())
    marker = root / 'native-process-started.json'
    if marker.exists():
        raise ValueError('Native dispatch was already started; reconcile without replay')
    durable(marker, b'{"reserved":true}')
    streams = dict(stdout=bytearray(), stderr=bytearray())
    reason = None
    first_edit = None
    started = time.monotonic()
    process = None
    settled = False
    auth_unchanged = True
    with tempfile.TemporaryDirectory(prefix='workbench-native-controller-', dir='/dev/shm') as private:
        control = Path(private)
        control.chmod(0o700)
        config, _ = boundary.profile(control, [], [source, scratch])
        config.update(forced_login_method='chatgpt', cli_auth_credentials_store='file',
            model_provider='openai', chatgpt_base_url='https://chatgpt.com/backend-api/',
            model=model, model_reasoning_effort=effort, analytics={'enabled': False})
        credential = Path(credential)
        original_hash = hashlib.sha256(credential.read_bytes()).hexdigest()
        shutil.copyfile(credential, control / 'auth.json')
        (control / 'auth.json').chmod(0o600)
        env = boundary.environment(control, scratch)
        env.update(CODEX_REFRESH_TOKEN_URL_OVERRIDE='http://127.0.0.1:0',
            CODEX_REVOKE_TOKEN_URL_OVERRIDE='http://127.0.0.1:0')
        command = [str(boundary.CLI), '--no-daemon', 'exec', '--ignore-user-config',
            '--ignore-rules', '--skip-git-repo-check', '--ephemeral', '--json', '--color', 'never',
            *[arg for key, value in config.items() for arg in ('-c', key + '=' + toml(value))],
            '-C', str(source), '-']
        # Same approved controller egress policy as the installed connector.
        properties = ['MemoryMax=1073741824', 'MemorySwapMax=0', 'TasksMax=128',
            'CPUQuota=100%', 'RuntimeMaxSec=' + str(seconds), 'KillMode=control-group',
            'TimeoutStopSec=1s', 'NoNewPrivileges=yes', 'SocketBindDeny=any',
            'IPAddressAllow=127.0.0.53/32',
            'IPAddressDeny=0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 169.254.0.0/16 172.16.0.0/12 192.168.0.0/16 224.0.0.0/4 240.0.0.0/4 ::/128 ::1/128 fc00::/7 fe80::/10 ff00::/8']
        argv = ['systemd-run', '--user', '--quiet', '--wait', '--pipe', '--collect',
            '--service-type=exec', '--unit=' + name, '--working-directory=' + str(source),
            *[arg for prop in properties for arg in ('-p', prop)], '/usr/bin/env', '-i',
            *[key + '=' + value for key, value in env.items()], '/usr/bin/python3', '-c', WRAPPER, *command]
        durable(root / 'native-launch.json', json.dumps(dict(config=config, unit=name,
            limits=properties, prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest()), sort_keys=True).encode())
        try:
            process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=manager_environment())
            pending_input = memoryview(prompt.encode())
            os.set_blocking(process.stdin.fileno(), False)
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdin, selectors.EVENT_WRITE, 'stdin')
                for label, stream in [('stdout', process.stdout), ('stderr', process.stderr)]:
                    selector.register(stream, selectors.EVENT_READ, label)
                deadline = time.monotonic() + seconds + 10
                while selector.get_map():
                    if stop() or time.monotonic() >= deadline:
                        reason = 'canceled' if stop() else 'timeout'
                        break
                    if first_edit is None:
                        for path, original in (watched or {}).items():
                            try:
                                fd = os.open(source / path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                                try:
                                    info = os.fstat(fd)
                                    if info.st_size > 65536 or info.st_nlink != 1:
                                        continue
                                    current = os.read(fd, 65537)
                                finally:
                                    os.close(fd)
                                if hashlib.sha256(current).hexdigest() != original:
                                    first_edit = time.monotonic() - started
                                    durable(root / 'native-first-edit.json', json.dumps(dict(path=path,
                                        seconds=first_edit, observed_at=time.time(),
                                        sha256=hashlib.sha256(current).hexdigest()), sort_keys=True).encode())
                                    break
                            except OSError:
                                pass
                    for key, _ in selector.select(.1):
                        if key.data == 'stdin':
                            try:
                                pending_input = pending_input[os.write(key.fd, pending_input[:65536]):]
                            except BlockingIOError:
                                continue
                            if not pending_input:
                                selector.unregister(key.fileobj)
                                process.stdin.close()
                            continue
                        data = os.read(key.fd, 65536)
                        if not data:
                            selector.unregister(key.fileobj)
                            continue
                        if sum(map(len, streams.values())) + len(data) > 4 << 20:
                            reason = 'output_limit'
                            break
                        streams[key.data].extend(data)
                    if reason:
                        break
        finally:
            settled = stop_unit(name)
            if process is not None:
                if not process.stdin.closed:
                    process.stdin.close()
                process.wait(timeout=8)
                process.stdout.close()
                process.stderr.close()
            (control / 'auth.json').unlink(missing_ok=True)
            auth_unchanged = hashlib.sha256(credential.read_bytes()).hexdigest() == original_hash
            for label, data in streams.items():
                durable(root / ('native-' + label + '.jsonl'), bytes(data))
    if not auth_unchanged:
        reason = reason or 'authentication_identity_changed'
    events = []
    for line in streams['stdout'].splitlines():
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                events.append(value)
        except ValueError:
            reason = reason or 'invalid_native_event'
    terminal = sum(event.get('type') == 'turn.completed' for event in events) == 1
    usage = tokens(events)
    usage['provenance'].update(request_id=request_id,
        event_sha256=hashlib.sha256(streams['stdout']).hexdigest())
    result = dict(terminal=terminal, settled=settled, exit_code=process.returncode if process else None,
        stop_reason=reason, usage=usage, actual_model=None, actual_effort=None,
        first_edit_seconds=first_edit,
        authentication_unchanged=auth_unchanged,
        # Requested choices are exact; absent native actual-identity fields stay unknown.
        requested_model=model, requested_effort=effort)
    durable(root / 'native-provider-result.json', json.dumps(result, sort_keys=True).encode())
    return result
