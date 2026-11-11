#!/usr/bin/python3 -IB
"""WorkBench host operations. Privileged entry point accepts one JSON object on stdin."""

from datetime import datetime, timezone
import errno
import fcntl
import grp
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import selectors
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time

UID = 1000
HOME = Path('/home/rich')
MAIN = HOME / 'TwichUI-Workbench'
STAGES = MAIN / '.local/m5-review'
ROLLBACK = STAGES / 'everyday-system-20261106'
RESEARCH = MAIN / '.local/real-addon-readiness.uQHL9i/addon'
CERT_SOURCE = HOME / '.local/share/TwichUI-Workbench/everyday-install-20261106-tls'
CONNECTOR = '/run/twichui-workbench-codex/connector.sock'
CLI = HOME / '.codex/packages/standalone/releases/0.166.0-x86_64-unknown-linux-musl/bin/codex'
CLI_SHA = 'b8a11fedf24b2dfb59a70c9e52b030f9a508dcd49cd2dd0d41956f4c785156b8'
SELF = '/usr/local/sbin/twichui-workbench-admin'
POLICY = Path('/etc/twichui-workbench-admin/grant.json')
STATE = Path('/var/lib/twichui-workbench-admin')
SYSTEM = Path('/etc/systemd/system')
DROP = 'workbench-everyday-worker.service.d/20-acceptance-private-network.conf'
DROP_BYTES = b'[Service]\nPrivateNetwork=yes\n'
SYSTEMCTL = ('/usr/bin/systemctl', '--system', '--no-pager', '--no-ask-password')
UFW = '/usr/sbin/ufw'
FIREWALL = ('in', 'on', 'enp4s0', 'from', '10.0.0.0/24', 'to', '10.0.0.9', 'port', '9487', 'proto', 'tcp')
FIREWALL_LINE = '10.0.0.9 9487/tcp on enp4s0 ALLOW IN 10.0.0.0/24'
FIREWALL_TAG = 'twichui-workbench-admin'
DENY_CODEX = ('0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 169.254.0.0/16 172.16.0.0/12 '
              '192.168.0.0/16 224.0.0.0/4 240.0.0.0/4 ::/128 ::1/128 fc00::/7 fe80::/10 ff00::/8')
ENV = dict(PATH='/usr/bin:/usr/sbin:/bin', LANG='C.UTF-8', LC_ALL='C.UTF-8', SYSTEMD_COLORS='0', SYSTEMD_PAGER='cat')
ROLES = ('api', 'lan', 'worker', 'codex', 'review-api', 'review-worker')
MODES = ('worker', 'lan', 'codex', 'isolated', 'guard')
LEGACY = {
    'review-api': '668ed8823ec27a9e4231b5a79980bb8e7e255e09ac35a3140a2e5631977857cd',
    'review-worker': '67dea174ba2885f0a339e505c35f968426cb7fdb4f5fdb7d7719ed58668b24b0',
}
UNIT_PROPERTIES = ('Names', 'FragmentPath', 'DropInPaths', 'LoadState', 'ActiveState', 'SubState',
                   'UnitFileState', 'User', 'Group', 'ControlGroup', 'MainPID', 'PrivateNetwork',
                   'IPAddressAllow', 'IPAddressDeny', 'SocketBindAllow', 'SocketBindDeny',
                   'Transient', 'Description', 'NeedDaemonReload')
INACTIVE = {'inactive', 'failed'}
RELEASE = re.compile(r'everyday-[a-z0-9]+(?:-[a-z0-9]+)*\Z')
SESSION = re.compile(r'/user\.slice/user-1000\.slice/session-[0-9]+\.scope\Z')


class Refused(Exception):
    """A bounded failure safe to print to the operator and write to the audit."""


def require(ok, reason):
    if not ok:
        raise Refused(reason)


def limited_read(fd, cap):
    with os.fdopen(fd, 'rb') as stream:
        result = stream.read(cap + 1)
    require(len(result) <= cap, 'Input exceeds its byte limit')
    return result


def protected_dir(path):
    """Every root path component must be a real root-controlled directory."""
    for part in (*reversed(path.parents), path):
        info = part.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
                'Root path has an untrusted directory component')


def checked_file(path, cap, *, owner=0, private=False):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        forbidden = 0o077 if private else 0o022
        require(stat.S_ISREG(info.st_mode) and info.st_uid == owner and info.st_nlink == 1
                and not info.st_mode & forbidden and info.st_size <= cap, 'File ownership, type or mode differs')
    except BaseException:
        os.close(fd)
        raise
    return limited_read(fd, cap)


def atomic_write(path, data, mode=0o600):
    fd, temporary = tempfile.mkstemp(prefix='.workbench-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_dir(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def sync_dir(path):
    fd = os.open(path, os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def run(argv, *, seconds=40, as_rich=False):
    """Bound child output and runtime. No shell, pager, request-supplied environment or cwd."""
    options = dict(user=UID, group=UID, extra_groups=[]) if as_rich else {}
    with subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          start_new_session=True, cwd='/', env=ENV, **options) as child:
        deadline = time.monotonic() + seconds
        output = bytearray()
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                while True:
                    left = deadline - time.monotonic()
                    require(left > 0 and selector.select(left), 'Command timed out; inspect its state before retry')
                    chunk = os.read(child.stdout.fileno(), 8192)
                    if not chunk:
                        break
                    output.extend(chunk)
                    require(len(output) <= 128 << 10, 'Command output exceeded its bound')
            status = child.wait(timeout=max(0.01, deadline - time.monotonic()))
        except (Refused, subprocess.TimeoutExpired):
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            raise Refused('Command output or time limit reached; inspect its state before retry') from None
    return status, output.decode('utf-8', errors='replace').strip()


def command(*argv, **options):
    status, output = run(argv, **options)
    require(status == 0, f'{Path(argv[0]).name} failed (exit {status}); inspect the named service or rule')
    return output


def valid_release(value):
    require(isinstance(value, str) and len(value) <= 64 and RELEASE.fullmatch(value), 'Invalid release identifier')
    return value


def unit_name(role):
    require(role in ROLES, 'Unknown service role')
    return f'workbench-{role if role.startswith("review-") else "everyday-" + role}.service'


def unit_bytes(role, release):
    """Render trusted directives only. No imports of owner-supplied unit or EnvironmentFile text."""
    unit = unit_name(role)
    review = role.startswith('review-')
    require(not review or release == ROLLBACK.name, 'Review services use the fixed rollback build')
    stage = STAGES / valid_release(release)
    base = stage / ('rollback' if review else 'release')
    head = ['[Unit]', f'Description=TwichUI Workbench {unit}']
    if role == 'worker':
        head += ['After=user@1000.service']  # Order only. Never start the user manager here.
    lines = ['[Service]', 'Type=exec', 'User=rich', 'Group=rich', 'Slice=system.slice',
             f'WorkingDirectory={base}', 'UMask=0077', 'Restart=on-failure', 'RestartSec=3']
    if role != 'worker':
        lines += ['NoNewPrivileges=yes']
    args = ['/usr/bin/env', '-i', 'PATH=/usr/bin:/bin', f'HOME={HOME}', 'LANG=C.UTF-8',
            'PYTHONDONTWRITEBYTECODE=1', f'PYTHONPATH={base}/backend:{base}/scripts']
    python = f'{base}/.venv/bin/python'
    if role == 'codex':
        head += [f'ConditionPathExists={stage}/ops/codex-account-review.json']
        lines += ['RuntimeDirectory=twichui-workbench-codex', 'RuntimeDirectoryMode=0700',
                  f'IPAddressDeny={DENY_CODEX}', 'IPAddressAllow=127.0.0.53/32', 'SocketBindDeny=any',
                  'MemoryMax=512M', 'MemorySwapMax=0', 'CPUQuota=100%',
                  'TasksMax=64', 'LimitNOFILE=128', 'KillMode=control-group', 'TimeoutStopSec=8']
        args += [python, f'{base}/scripts/codex_connector.py', '--socket', CONNECTOR, '--binary', str(CLI),
                 '--binary-sha256', CLI_SHA, '--sign-in-file', str(HOME / '.codex/auth.json'),
                 '--account-review', f'{stage}/ops/codex-account-review.json']
    else:
        lines += ['IPAddressDeny=any', f'IPAddressAllow={"10.0.0.0/24" if role == "lan" else "localhost"}']
        args += ['XDG_RUNTIME_DIR=/run/user/1000', 'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus',
                 f'WORKBENCH_STATIC_ROOT={base}/frontend/dist']
        if review:
            args += [f'WORKBENCH_DB={STAGES}/review.sqlite3', 'WORKBENCH_PLANNER=demo']
        else:
            args += [f'WORKBENCH_DB={stage}/artifacts/workbench.sqlite3', 'WORKBENCH_PLANNER=everyday',
                     f'WORKBENCH_EVERYDAY_CONFIG={stage}/ops/everyday.json',
                     f'WORKBENCH_PREPARATION_CONFIG={stage}/ops/preparation.json',
                     'WORKBENCH_PLANNING_DEADLINE_S=120', f'WORKBENCH_RESEARCH_ROOT={RESEARCH}',
                     f'TMPDIR={stage}/scratch', f'WORKBENCH_CODEX_SOCKET={CONNECTOR}']
        if role == 'worker':
            lines += ['Delegate=yes', 'MemoryMax=6G', 'MemorySwapMax=0', 'CPUQuota=200%', 'TasksMax=128',
                      'KillMode=mixed', 'TimeoutStopSec=240']
            args += [f'WORKBENCH_LOCAL_KEY={HOME}/twichui-pilot/backup/.key', '/bin/bash',
                     f'{base}/scripts/browser_demo/delegated-worker.sh', 'workbench-everyday-worker',
                     f'{stage}/scratch', 'everyday']
        elif role == 'review-worker':
            args += [python, '-m', 'workbench.worker']
        else:
            lines += ['SocketBindDeny=any', 'SocketBindAllow=ipv4:tcp:9487']
            host = '10.0.0.9' if role == 'lan' else '127.0.0.1'
            if role == 'lan':
                args += ['WORKBENCH_PRIVATE_LAN_IP=10.0.0.9', 'WORKBENCH_PRIVATE_LAN_SUBNET=10.0.0.0/24']
            args += [python, '-m', 'uvicorn', 'workbench.api:app', '--host', host, '--port', '9487',
                     '--no-proxy-headers', '--no-access-log']
            if role == 'lan':
                args += ['--ssl-certfile', f'{stage}/ops/tls/server.crt', '--ssl-keyfile',
                         f'{stage}/ops/tls/server.key', '--ssl-cert-reqs', '0']
    return ('\n'.join([f'# Workbench admin release {release}', *head, *lines,
                       'ExecStart=' + ' '.join(args), '[Install]', 'WantedBy=multi-user.target', ''])).encode()


def read_unit(role):
    path = SYSTEM / unit_name(role)
    try:
        data = checked_file(path, 8192)
    except FileNotFoundError:
        return None
    if hashlib.sha256(data).hexdigest() == LEGACY.get(role):
        return ROLLBACK.name, True
    match = re.fullmatch(rb'# Workbench admin release ([-a-z0-9]+)', data.split(b'\n', 1)[0])
    require(match is not None, 'Unit is outside the administrative envelope')
    release = match[1].decode()
    require(data == unit_bytes(role, release), 'Unit bytes differ from the trusted template')
    return release, False


def show(unit):
    output = command(*SYSTEMCTL, 'show', '-p', ','.join(UNIT_PROPERTIES), unit)
    return dict(line.split('=', 1) for line in output.splitlines() if '=' in line)


def approved_drop():
    path = SYSTEM / DROP
    if not os.path.lexists(path.parent):
        return False
    protected_dir(path.parent)
    try:
        data = checked_file(path, 128)
    except FileNotFoundError:
        return False
    require(data == DROP_BYTES, 'Worker drop-in differs from its trusted template')
    return True


def check_definition(role, info):
    unit = unit_name(role)
    drop = str(SYSTEM / DROP) if role == 'worker' and approved_drop() else ''
    require(info.get('LoadState') == 'loaded' and info.get('Names') == unit
            and info.get('FragmentPath') == str(SYSTEM / unit) and info.get('DropInPaths', '') == drop
            and info.get('User') == info.get('Group') == 'rich' and info.get('NeedDaemonReload') == 'no',
            'Unexpected effective system service definition')


def require_settled(release, review=False):
    # The root helper never opens an application database or imports application code.
    argv = ['/usr/bin/setpriv', '--no-new-privs', SELF, '--settled', release]
    if review:
        argv += ['review']
    command(*argv, seconds=5, as_rich=True)
    require(not any(name.startswith(('workbench-restore-', 'workbench-real-chronicle-', 'workbench-real-codex-',
                                     'workbench-real-prepare-'))
                    for name in active_user_units()), 'A provider controller or restoration watcher remains active')


def active_user_units():
    # Read process ownership without contacting or activating a user unit. Exclude inactive cgroup directories.
    folder = Path('/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/app.slice')
    for group in folder.glob('workbench-*.service'):
        try:
            if 'populated 1' in (group / 'cgroup.events').read_text().splitlines():
                yield group.name
        except FileNotFoundError:
            continue


def service(action, role, release=None):
    unit = unit_name(role)
    path = SYSTEM / unit
    current = read_unit(role)
    info = show(unit)
    active = info.get('ActiveState')
    if action == 'sync':
        require(active in INACTIVE and (current or info.get('FragmentPath') in {'', str(path)}),
                'Only a stopped Workbench definition can be reconciled')
        command(*SYSTEMCTL, 'daemon-reload')
        if current:
            check_definition(role, show(unit))
        else:
            require(show(unit).get('LoadState') == 'not-found', 'Unexpected replacement unit after reload')
        return 'Reconciled stopped Workbench definition'
    if action == 'write':
        payload = unit_bytes(role, release)
        require(active in INACTIVE and (current is not None or info.get('LoadState') == 'not-found'),
                'Stop the known service before changing its definition')
        if current:
            check_definition(role, info)
        atomic_write(path, payload, 0o644)
        command(*SYSTEMCTL, 'daemon-reload')
        check_definition(role, show(unit))
        return 'Installed verified Workbench definition'
    require(current is not None, 'No managed definition exists for this role')
    check_definition(role, info)
    if action in {'start', 'restart', 'enable'}:
        require(not current[1], 'Render the fixed rollback definition before starting this legacy unit')
    if action in {'stop', 'restart'} and active not in INACTIVE and role in {'worker', 'review-worker', 'codex'}:
        listeners = ('review-api',) if role == 'review-worker' else ('lan', 'api')
        require(all(show(unit_name(listener)).get('ActiveState') in INACTIVE for listener in listeners),
                'Close Workbench ingress before stopping its execution service')
        require_settled(current[0], role == 'review-worker')
    if action in {'start', 'restart', 'enable'}:
        check_release_peers(role, current[0], action == 'enable')
    if action in {'start', 'restart'} and role == 'worker':
        require(show('user@1000.service').get('ActiveState') == 'active', 'Rich user manager is unavailable')
    if action in {'start', 'restart', 'enable'} and role == 'lan':
        check_lan_address()
        require(lan_rule(), 'Exact UFW LAN rule is required')
    if action in {'enable', 'disable'}:
        return boot_link(role, action == 'enable')
    if action == 'remove':
        require(active in INACTIVE and info.get('UnitFileState') == 'disabled', 'Stop and disable the service before removal')
        require(role != 'worker' or not approved_drop(), 'Remove the acceptance drop-in first')
        path.unlink()
        sync_dir(SYSTEM)
        command(*SYSTEMCTL, 'daemon-reload')
        return 'Removed verified Workbench definition'
    return command(*SYSTEMCTL, action, unit, seconds=260 if role == 'worker' else 40)


def boot_link(role, enabled):
    unit = unit_name(role)
    directory = SYSTEM / 'multi-user.target.wants'
    if enabled and not os.path.lexists(directory):
        directory.mkdir(mode=0o755)
    if os.path.lexists(directory):
        protected_dir(directory)
        path = directory / unit
        target = SYSTEM / unit
        if os.path.lexists(path):
            require(path.is_symlink() and path.lstat().st_uid == 0
                    and os.readlink(path) in (str(target), f'../{unit}'), 'Boot link is outside the managed location')
            if not enabled:
                path.unlink()
        elif enabled:
            path.symlink_to(target)
        sync_dir(directory)
    command(*SYSTEMCTL, 'daemon-reload')
    expected = 'enabled' if enabled else 'disabled'
    require(show(unit).get('UnitFileState') == expected, 'Unexpected systemd boot links remain')
    return f'Workbench service is {expected} at boot'


def check_release_peers(role, release, enabling):
    review = role.startswith('review-')
    if not review:
        check_stage_as_rich(release)
    for peer in ROLES:
        if peer == role:
            continue
        info = show(unit_name(peer))
        if info.get('ActiveState') in INACTIVE and not (enabling and info.get('UnitFileState', '').startswith('enabled')):
            continue
        managed = read_unit(peer)
        require(managed and peer.startswith('review-') == review and managed[0] == release,
                'Another Workbench release is active or enabled')
        check_definition(peer, info)


def set_drop(enabled):
    unit = unit_name('worker')
    require(read_unit('worker') is not None, 'Install the worker definition first')
    info = show(unit)
    check_definition('worker', info)
    require(info.get('ActiveState') in INACTIVE, 'Stop the worker before changing the acceptance drop-in')
    path = SYSTEM / DROP
    if enabled:
        if not os.path.lexists(path.parent):
            path.parent.mkdir(mode=0o755)
        protected_dir(path.parent)
        if not approved_drop():
            atomic_write(path, DROP_BYTES, 0o644)
    elif approved_drop():
        path.unlink()
        sync_dir(path.parent)
    command(*SYSTEMCTL, 'daemon-reload')
    check_definition('worker', show(unit))
    return 'Acceptance private network is ' + ('on' if enabled else 'off')


def firewall_status():
    return command(UFW, 'status', 'verbose')


def lan_rule():
    status = firewall_status()
    require('Status: active' in status.splitlines() and 'Default: deny (incoming)' in status
            and 'disabled (routed)' in status, 'UFW baseline or forwarding policy changed')
    lines = [' '.join(line.split()) for line in status.splitlines() if '9487' in line]
    require(not lines or lines in ([FIREWALL_LINE], [FIREWALL_LINE + ' # ' + FIREWALL_TAG]),
            'A conflicting Workbench port rule is present')
    if not lines:
        return None
    return 'tagged' if '#' in lines[0] else 'external'


def check_lan_address():
    rows = json.loads(command('/usr/sbin/ip', '-j', '-4', 'address', 'show', 'dev', 'enp4s0'))
    addresses = [item for row in rows for item in row['addr_info'] if item['local'] == '10.0.0.9']
    require(len(addresses) == 1 and addresses[0]['prefixlen'] == 24, 'Host LAN binding changed')
    for path in ('/proc/sys/net/ipv4/ip_forward', '/proc/sys/net/ipv6/conf/all/forwarding'):
        require(Path(path).read_text().strip() == '0', 'IP forwarding is enabled')


def firewall(action):
    state = STATE / 'firewall'
    try:
        owned = checked_file(state, 64, private=True)
    except FileNotFoundError:
        owned = b''
    require(owned in {b'', b'added\n', b'adding\n', b'removing\n'}, 'Firewall ownership record differs')
    present = lan_rule()
    require(not owned or present != 'external', 'An untagged existing rule is not owned by this helper')
    require(owned or present != 'tagged', 'Tagged rule has no protected ownership record')
    if action == 'add':
        check_lan_address()
        if present:
            if owned:
                atomic_write(state, b'added\n')
                return 'Exact tagged rule already present'
            return 'Exact pre-existing rule retained without adopting it'
        atomic_write(state, b'adding\n')
        command(UFW, 'allow', *FIREWALL, 'comment', FIREWALL_TAG)
        require(lan_rule() == 'tagged', 'Tagged rule did not verify after add')
        atomic_write(state, b'added\n')
        return 'Exact Workbench LAN rule added'
    require(show(unit_name('lan')).get('ActiveState') in INACTIVE, 'Stop the LAN listener before removing its rule')
    if not owned:
        return 'No rule is owned by this grant'
    if present:
        atomic_write(state, b'removing\n')
        command(UFW, 'delete', 'allow', *FIREWALL)
        require(not lan_rule(), 'Rule still present after delete')
    state.unlink()
    sync_dir(STATE)
    return 'Owned Workbench LAN rule absent'


def probe_unit(mode):
    require(mode in MODES, 'Unknown probe')
    return f'workbench-everyday-admin-proof-{mode}.service'


def probe(mode, release=None):
    unit = probe_unit(mode)
    require(show(unit).get('LoadState') == 'not-found', 'An existing probe must settle or be cleaned up first')
    properties = ['User=rich', 'Group=rich', 'RuntimeMaxSec=20', 'IPAddressDeny=any', 'IPAddressAllow=localhost']
    argv = [SELF, '--probe', mode]
    if mode == 'lan':
        properties[-1] = 'IPAddressAllow=10.0.0.0/24'
    elif mode == 'codex':
        properties[-2:] = [f'IPAddressDeny={DENY_CODEX}', 'IPAddressAllow=127.0.0.53/32', 'SocketBindDeny=any']
    if mode in {'isolated', 'guard'}:
        properties += ['PrivateNetwork=yes']
    if mode == 'guard':
        base = STAGES / valid_release(release) / 'release'
        check_stage_as_rich(release)
        properties[2] = 'RuntimeMaxSec=45'
        properties += ['Delegate=yes', 'MemoryMax=6G', 'MemorySwapMax=0', 'TasksMax=128', 'CPUQuota=200%',
                       'RuntimeDirectory=twichui-workbench-admin-guard', 'RuntimeDirectoryMode=0700',
                       f'WorkingDirectory={base}']
        argv = ['/usr/bin/env', '-i', 'PATH=/usr/bin:/bin', f'HOME={HOME}', 'PYTHONDONTWRITEBYTECODE=1',
                '/bin/bash', f'{base}/scripts/browser_demo/delegated-worker.sh', unit.removesuffix('.service'),
                '/run/twichui-workbench-admin-guard', 'preflight-only']
    properties += ['NoNewPrivileges=yes']
    args = [arg for value in properties for arg in ('-p', value)]
    marker = STATE / f'probe-{mode}'
    require(not os.path.lexists(marker), 'Reconcile the prior probe before starting a new one')
    token = os.urandom(12).hex()
    atomic_write(marker, token.encode())
    try:
        return command('/usr/bin/systemd-run', '--system', '--wait', '--pipe', '--collect', '--expand-environment=no',
                       '--unit=' + unit, f'--description=TwichUI Workbench administrative probe {mode} {token}',
                       *args, *argv, seconds=55)
    finally:
        if show(unit).get('LoadState') == 'not-found':
            marker.unlink()
            sync_dir(STATE)


def clean_probe(mode):
    unit = probe_unit(mode)
    marker = STATE / f'probe-{mode}'
    info = show(unit)
    if info.get('LoadState') == 'not-found':
        if os.path.lexists(marker):
            checked_file(marker, 24, private=True)
            marker.unlink()
            sync_dir(STATE)
        return 'Probe absent'
    token = checked_file(marker, 24, private=True).decode()
    require(re.fullmatch(r'[a-f0-9]{24}', token), 'Protected probe ownership record differs')
    require(info.get('Transient') == 'yes' and info.get('Names') == unit
            and info.get('User') == info.get('Group') == 'rich' and not info.get('DropInPaths')
            and info.get('FragmentPath') == f'/run/systemd/transient/{unit}'
            and info.get('Description') == f'TwichUI Workbench administrative probe {mode} {token}',
            'Probe identity differs')
    output = command(*SYSTEMCTL, 'stop', unit)
    if show(unit).get('LoadState') == 'not-found':
        marker.unlink()
        sync_dir(STATE)
    return output


def check_stage_as_rich(release):
    command('/usr/bin/setpriv', '--no-new-privs', SELF, '--stage', valid_release(release), seconds=5, as_rich=True)


def rich_stage(release):
    stage = STAGES / valid_release(release)
    for path in (stage, stage / 'release', stage / 'ops', stage / 'artifacts', stage / 'scratch'):
        info = path.lstat()
        require(path.resolve() == path and stat.S_ISDIR(info.st_mode) and info.st_uid == UID
                and not info.st_mode & 0o022, 'Release paths must be real Rich-owned directories')
    return stage


def settled_check(release, review):
    path = STAGES / 'review.sqlite3' if review else rich_stage(release) / 'artifacts/workbench.sqlite3'
    info = path.lstat()
    require(path.resolve() == path and stat.S_ISREG(info.st_mode) and info.st_uid == UID
            and not info.st_mode & 0o077, 'History store path or mode differs')
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1) as db:
        db.execute('PRAGMA trusted_schema=OFF')
        db.execute('BEGIN')
        require(db.execute('SELECT version_num FROM alembic_version').fetchall() in ([('0012',)], [('0013',)], [('0014',)]),
                'History schema is outside this grant')
        for table, statuses in {
            'commands': ('pending',), 'proposal_decisions': ('pending',), 'planning_jobs': ('queued', 'running'),
            'task_sessions': ('pending', 'active', 'waiting_recovery'),
            'real_execution_requests': ('pending', 'running', 'checking', 'cancel_requested'),
            'real_publications': ('pending', 'preparing', 'pushing', 'creating', 'reconciling'),
            'pr_preparations': ('pending', 'preparing', 'reconciling'),
        }.items():
            markers = ','.join('?' for _ in statuses)
            require(not db.execute(f'SELECT 1 FROM {table} WHERE status IN ({markers}) LIMIT 1', statuses).fetchone(),
                    'History still contains unsettled work')
        require(not db.execute('''SELECT 1 FROM real_execution_grants g
            LEFT JOIN real_execution_requests r ON r.grant_id=g.id
            WHERE r.id IS NULL AND g.revoked_at IS NULL AND julianday(g.expires_at)>julianday('now') LIMIT 1''').fetchone(),
                'Unused run permission still exists')


def tls_provision(release):
    stage = rich_stage(release)
    ca = CERT_SOURCE / 'workbench-home-lan-ca.crt'
    cert, key = CERT_SOURCE / 'server.crt', CERT_SOURCE / 'server.key'
    require(CERT_SOURCE.resolve() == CERT_SOURCE, 'TLS source directory is a link')
    inputs = {path.name: checked_file(path, 32 << 10, owner=UID, private=True) for path in (ca, cert, key)}
    target = stage / 'ops/tls'
    if not os.path.lexists(target):
        target.mkdir(mode=0o700)
    info = target.lstat()
    require(target.resolve() == target and stat.S_ISDIR(info.st_mode) and info.st_uid == UID
            and not info.st_mode & 0o077, 'TLS destination must be private and Rich-owned')
    # Validate the exact byte copies before either destination is created. OpenSSL is unprivileged.
    with tempfile.TemporaryDirectory(dir=stage / 'scratch') as temp:
        temp = Path(temp)
        for name, data in inputs.items():
            atomic_write(temp / name, data)
        ca, cert, key = (temp / path.name for path in (ca, cert, key))
        fingerprint = command('/usr/bin/openssl', 'x509', '-in', str(ca), '-noout', '-fingerprint', '-sha256')
        require(fingerprint.partition('=')[2].upper() ==
                '06:E8:DE:82:AF:EA:0A:86:57:65:70:D1:02:4B:0D:DC:89:D3:8E:9C:9B:03:1F:95:F0:EE:CC:9D:32:E7:71:BD',
                'TLS issuer differs from the approved CA')
        command('/usr/bin/openssl', 'verify', '-purpose', 'sslserver', '-verify_ip', '10.0.0.9',
                '-CAfile', str(ca), str(cert))
        require(command('/usr/bin/openssl', 'x509', '-in', str(cert), '-noout', '-pubkey') ==
                command('/usr/bin/openssl', 'pkey', '-in', str(key), '-passin', 'pass:', '-pubout'),
                'TLS server key does not match the certificate')
    for name in ('server.crt', 'server.key'):
        path = target / name
        if os.path.lexists(path):
            require(checked_file(path, 32 << 10, owner=UID, private=True) == inputs[name],
                    'A different TLS file already exists; keep it for recovery')
        else:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(inputs[name])
                stream.flush()
                os.fsync(stream.fileno())
    sync_dir(target)
    return 'Approved TLS certificate and private key verified at the release paths'


def tcp_probe(mode):
    qwen, lan, dns = ('127.0.0.1', 8080), ('10.0.0.9', 8765), ('127.0.0.53', 53)
    blocked = {'timed-out', 'EPERM', 'EACCES'}
    cases = {
        'baseline': [(qwen, {'connected'}), (lan, {'connected'}), (dns, {'connected'})],
        'worker': [(qwen, {'connected'}), (lan, blocked)],
        'lan': [(lan, {'connected'}), (qwen, blocked)],
        'codex': [(dns, {'connected'}), (qwen, blocked), (lan, blocked)],
        'isolated': [(qwen, {'ECONNREFUSED'})],
    }
    require(mode in cases, 'Unknown TCP check')
    for address, accepted in cases[mode]:
        try:
            with socket.create_connection(address, timeout=2):
                observed = 'connected'
        except TimeoutError:
            observed = 'timed-out'
        except OSError as error:
            observed = errno.errorcode.get(error.errno)
        print(address, observed)
        require(observed in accepted, 'TCP check differed from its expected result')
    if mode == 'codex':
        with socket.socket() as client:
            try:
                client.bind(('127.0.0.1', 0))
            except OSError as error:
                require(error.errno in (errno.EPERM, errno.EACCES), 'Bind failure did not prove filtering')
                print('Explicit IP bind denied')
            else:
                raise Refused('Explicit IP bind unexpectedly permitted')


def inspect(target, role=None, lines=None):
    if target == 'firewall':
        return '\n'.join((firewall_status(), command(UFW, 'show', 'raw'), command('/usr/sbin/nft', 'list', 'ruleset')))
    if target == 'sockets':
        tcp = command('/usr/bin/ss', '-H', '-ltnp', '( sport = :9487 or sport = :8080 or sport = :8765 or sport = :53 )')
        unix = command('/usr/bin/ss', '-H', '-xlnp')
        return tcp + '\n' + '\n'.join(line for line in unix.splitlines() if CONNECTOR in line.split())
    unit = 'twichui-llm.service' if role == 'model' else unit_name(role)
    info = show(unit)
    if target == 'unit':
        return info
    if target == 'logs':
        # Root reads administrative fields only. Rich can read app logs directly.
        return command('/usr/bin/journalctl', '--system', '--no-pager', '--quiet', '--boot', '--unit=' + unit,
                       '--lines=' + str(lines), '--output=json',
                       '--output-fields=__REALTIME_TIMESTAMP,PRIORITY,MESSAGE_ID,UNIT,JOB_RESULT,RESULT,EXIT_CODE,EXIT_STATUS')
    group = info.get('ControlGroup')
    require(group == f'/system.slice/{unit}', 'No live cgroup for this named service')
    folder = Path('/sys/fs/cgroup') / group.lstrip('/')
    protected_dir(folder.parent)
    require(folder.resolve() == folder, 'Cgroup is a link')
    if target == 'filters':
        return command('/usr/sbin/bpftool', 'cgroup', 'show', str(folder), 'effective')
    pid = int(info.get('MainPID', '0'))
    require(pid > 0, 'Service has no main process')
    process = Path('/proc') / str(pid)
    require(group in (process / 'cgroup').read_text().split('::', 1)[-1].splitlines(), 'Main process moved')
    fields = (process / 'stat').read_text().rsplit(') ', 1)[1].split()
    return dict(pid=pid, uid=process.stat().st_uid, state=fields[0], start_ticks=fields[19], cgroup=group)


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'Duplicate JSON key')
        result[key] = value
    return result


def validate(request):
    require(isinstance(request, dict) and isinstance(request.get('op'), str), 'Expected a JSON request object')
    op = request['op']
    allowed = {
        'service': {'op', 'action', 'role'}, 'drop-in': {'op', 'action'},
        'firewall': {'op', 'action'}, 'probe': {'op', 'mode'}, 'probe-clean': {'op', 'mode'},
        'inspect': {'op', 'target'}, 'audit': {'op', 'lines'}, 'status': {'op'}, 'revoke': {'op'},
    }
    require(op in allowed, 'Unknown operation')
    keys = allowed[op].copy()
    action, target, mode = (request.get(key) for key in ('action', 'target', 'mode'))
    if op == 'service':
        require(action in ('write', 'sync', 'start', 'stop', 'restart', 'enable', 'disable', 'remove'), 'Unknown service action')
        unit_name(request.get('role'))
        if action == 'write':
            keys.add('release')
            valid_release(request.get('release'))
    if op == 'drop-in':
        require(action in ('on', 'off'), 'Unknown drop-in action')
    if op == 'firewall':
        require(action in ('add', 'remove'), 'Unknown firewall action')
    if op in ('probe', 'probe-clean'):
        probe_unit(mode)
        if op == 'probe' and mode == 'guard':
            keys.add('release')
            valid_release(request.get('release'))
    if op == 'inspect':
        require(target in ('unit', 'logs', 'filters', 'process', 'firewall', 'sockets'), 'Unknown inspection target')
        if target not in ('firewall', 'sockets'):
            keys.add('role')
            if request.get('role') != 'model':
                unit_name(request.get('role'))
        if target == 'logs':
            keys.add('lines')
    if 'lines' in keys:
        lines = request.get('lines')
        require(type(lines) is int and 1 <= lines <= 200, 'Inspection lines must be between 1 and 200')
    require(set(request) == keys, 'Unexpected or missing request fields')
    return request


def check_grant(grant, *, now, boot_id, cgroup, login_uid, session_id):
    require(isinstance(grant, dict) and set(grant) == {'created', 'expires', 'boot_id', 'cgroup', 'session_id'},
            'Protected grant has the wrong format')
    require(type(grant['created']) is int and type(grant['expires']) is int
            and 0 < grant['expires'] - grant['created'] <= 7 * 86400
            and grant['created'] <= now < grant['expires'], 'Administrative grant is inactive or expired')
    require(grant['boot_id'] == boot_id and isinstance(cgroup, str) and SESSION.fullmatch(cgroup)
            and grant['cgroup'] == cgroup and login_uid == str(UID)
            and isinstance(session_id, str) and session_id.isascii() and session_id.isdecimal()
            and grant['session_id'] == session_id, 'Invocation is outside the approved host login session')


def audit(value):
    path = STATE / 'audit.jsonl'
    fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_WRONLY | os.O_NONBLOCK, 0o600)
    with os.fdopen(fd, 'ab') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                and not info.st_mode & 0o077 and info.st_size < 8 << 20, 'Protected audit file is unsafe or full')
        stream.write(json.dumps(dict(time=datetime.now(timezone.utc).isoformat(), **value)).encode() + b'\n')
        stream.flush()
        os.fsync(stream.fileno())


def dispatch(request, grant):
    op = request['op']
    args = {key: value for key, value in request.items() if key != 'op'}
    if op == 'service':
        if args['action'] == 'write' and not args['role'].startswith('review-'):
            check_stage_as_rich(args['release'])
        return service(**args)
    if op == 'drop-in':
        return set_drop(args['action'] == 'on')
    if op == 'firewall':
        return firewall(**args)
    if op == 'probe':
        return probe(**args)
    if op == 'probe-clean':
        return clean_probe(**args)
    if op == 'inspect':
        return inspect(**args)
    if op == 'audit':
        return b'\n'.join(checked_file(STATE / 'audit.jsonl', 9 << 20, private=True).splitlines()[-args['lines']:]).decode()
    if op == 'revoke':
        atomic_write(STATE / 'revoked', b'revoked\n')
        return 'Administrative grant revoked; current services and evidence retained'
    return {key: grant[key] for key in ('expires', 'cgroup', 'session_id')}


def request_deadline(_signum, _frame):
    raise Refused('Request input timed out')


def recovery_request(request):
    op, action, role = (request.get(key) for key in ('op', 'action', 'role'))
    safe = op in {'inspect', 'audit', 'probe-clean', 'revoke'} or (
        op in {'firewall', 'drop-in'} and action == {'firewall': 'remove', 'drop-in': 'off'}[op])
    if op == 'service':
        safe = action in {'stop', 'disable', 'remove', 'sync'} or (
            role.startswith('review-') and action in {'write', 'start'})
    require(safe, 'Owner recovery only closes owned effects or restores the fixed review build')


def privileged_main():
    args = sys.argv[1:]
    require(args in ([], ['--owner-recovery'])
            and os.environ.get('SUDO_UID') == str(UID) and os.environ.get('SUDO_USER') == 'rich',
            'Use the exact administrator command through sudo as Rich')
    require(pwd.getpwnam('rich').pw_uid == grp.getgrnam('rich').gr_gid == UID, 'Host service identity changed')
    recovery = bool(args)
    for path in (STATE, POLICY.parent, SYSTEM):
        protected_dir(path)
    os.umask(0o077)
    lock = os.open(STATE / 'lock', os.O_CREAT | os.O_NOFOLLOW | os.O_RDWR, 0o600)
    with os.fdopen(lock, 'r+b') as file:
        info = os.fstat(file.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1 and not info.st_mode & 0o077,
                'Unsafe administrator lock')
        fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        request = None
        invocation = os.urandom(8).hex()
        receipt = dict(id=invocation, authority='owner-recovery' if recovery else 'session')
        try:
            grant = None
            if not recovery:
                require(not os.path.lexists(STATE / 'revoked'), 'Administrative grant was revoked')
                grant = json.loads(checked_file(POLICY, 2048, private=True), object_pairs_hook=unique_keys)
                cgroup = Path('/proc/self/cgroup').read_text().strip().removeprefix('0::')
                check_grant(grant, now=int(time.time()), boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                            cgroup=cgroup, login_uid=Path('/proc/self/loginuid').read_text().strip(),
                            session_id=Path('/proc/self/sessionid').read_text().strip())
                protected_dir(Path('/sys/fs/cgroup') / cgroup.lstrip('/'))
            signal.signal(signal.SIGALRM, request_deadline)
            signal.alarm(5)
            try:
                raw = sys.stdin.buffer.read(4097)
                require(len(raw) <= 4096, 'Request exceeds its byte limit')
                request = validate(json.loads(raw, object_pairs_hook=unique_keys))
            finally:
                signal.alarm(0)
            if recovery:
                recovery_request(request)
            else:
                require(int(time.time()) < grant['expires'], 'Administrative grant expired before execution')
            audit(dict(**receipt, event='begin', request=request))
            result = dispatch(request, grant)
        except (Refused, ValueError, OSError, TypeError, KeyError, RecursionError) as error:
            reason = str(error) if isinstance(error, Refused) else type(error).__name__
            audit(dict(**receipt, event='refused', request=request, reason=reason))
            raise Refused(reason) from None
        audit(dict(**receipt, event='complete', request=request))
    print(json.dumps(result) if isinstance(result, dict) else result)


def main():
    try:
        if os.geteuid() == 0:
            privileged_main()
        else:
            require(os.geteuid() == UID, 'This helper is scoped to Rich')
            args = sys.argv[1:]
            if len(args) == 2 and args[0] == '--probe':
                tcp_probe(args[1])
            elif len(args) == 2 and args[0] == '--tls-provision':
                print(tls_provision(args[1]))
            elif len(args) == 2 and args[0] == '--stage':
                rich_stage(args[1])
            elif (len(args) == 2 or len(args) == 3 and args[2] == 'review') and args[0] == '--settled':
                settled_check(valid_release(args[1]), len(args) == 3)
            else:
                raise Refused('Use the documented administrative request or unprivileged TLS operation')
    except (Refused, ValueError, OSError, sqlite3.Error, TypeError, KeyError, RecursionError) as error:
        print(f'Workbench administrator held: {error if isinstance(error, Refused) else type(error).__name__}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
