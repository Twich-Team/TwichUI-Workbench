"""Rich's private connector. Start only under an explicitly approved account review."""
import argparse
from hashlib import sha256
import ipaddress
import json
import os
from pathlib import Path
import re
import select
import socket
import stat
import struct
import tempfile
import time

from workbench import codex_provider as codex

RESOLVER = '/run/systemd/resolve/io.systemd.Resolve'


def host_mapping(response):
    """Accept only bounded public IPv4 answers for the fixed backend hostname."""
    if not isinstance(response, dict) or 'error' in response:
        raise ValueError('Resolver refused lookup')
    records = response['parameters']['addresses']
    if not isinstance(records, list) or not 1 <= len(records) <= 16:
        raise ValueError('Unexpected resolver answer count')
    addresses = []
    for record in records:
        raw = record['address']
        if (record['family'] != socket.AF_INET or not isinstance(raw, list) or len(raw) != 4
                or any(type(value) is not int or not 0 <= value <= 255 for value in raw)):
            raise ValueError('Unexpected resolver address')
        address = ipaddress.IPv4Address(bytes(raw))
        if not address.is_global or address.is_multicast or address.is_reserved:
            raise ValueError('Non-public resolver answer')
        addresses.append(address)
    return ''.join(f'{address} {codex.BACKEND_HOST}\n' for address in dict.fromkeys(addresses))


def resolve_backend(stop, deadline):
    # The connector's cgroup denies explicit IP binds. Let the host resolver
    # do DNS over its Unix socket rather than enabling a bind in the sandbox.
    end = min(deadline, time.monotonic() + 3)
    request = {'method': 'io.systemd.Resolve.ResolveHostname',
               'parameters': {'name': codex.BACKEND_HOST, 'family': socket.AF_INET, 'flags': 1}}
    try:
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(.1)
            if stop():
                raise codex.Hold('canceled', phase='resolve')
            client.connect(RESOLVER)
            client.sendall(json.dumps(request).encode() + b'\0')
            response = bytearray()
            while time.monotonic() < end:
                if stop():
                    raise codex.Hold('canceled', phase='resolve')
                try:
                    chunk = client.recv(4097 - len(response))
                except TimeoutError:
                    continue
                response.extend(chunk)
                if not chunk or len(response) > 4096:
                    break
                if response.endswith(b'\0'):
                    return host_mapping(json.loads(response[:-1]))
    except (OSError, ValueError, TypeError, KeyError):
        pass
    raise codex.Hold('unavailable', phase='resolve')


def private_inputs(socket_path, binary, binary_hash, credential):
    parent = socket_path.parent
    if (parent.resolve() != parent or parent.stat().st_uid != os.getuid()
            or parent.stat().st_mode & 0o077 or os.path.lexists(socket_path)
            or binary.resolve() != binary or not binary.is_file()
            or sha256(binary.read_bytes()).hexdigest() != binary_hash
            or credential.resolve() != credential or credential.stat().st_uid != os.getuid()
            or not stat.S_ISREG(credential.stat().st_mode) or credential.stat().st_mode & 0o077):
        raise ValueError('The connector requires its private runtime directory, pinned CLI and owned sign-in file.')


def check_account_review(path):
    info = path.lstat()
    if (path.resolve() != path or info.st_uid != os.getuid() or info.st_mode & 0o077
            or not stat.S_ISREG(info.st_mode) or info.st_size > 2048):
        raise ValueError('The connector requires a private owner account review.')
    review = json.loads(path.read_text())
    if (not isinstance(review, dict) or review.get('automatic_reload') != 'off'
            or type(review.get('additional_usage_credit_balance')) is not int
            or review['additional_usage_credit_balance'] != 0
            or review.get('allowance_snapshot_risk_accepted') is not True
            or not isinstance(review.get('approval_reference'), str)
            or not review['approval_reference'].strip()):
        raise ValueError('The connector requires the approved included-allowance account conditions.')


def allowed_cgroup(group, uid):
    if group.is_relative_to('/system.slice/workbench-everyday-worker.service'):
        return True
    parent = Path(f'/user.slice/user-{uid}.slice/user@{uid}.service/app.slice')
    if not group.is_relative_to(parent):
        return False
    child = group.relative_to(parent).parts
    return bool(child and re.fullmatch(r'workbench-real-codex-task-lease-[A-Za-z0-9-]+\.service', child[0]))


def handle(connection, binary, credential, runtime):
    connection.settimeout(3)
    pid, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    if uid != os.getuid():
        return
    try:
        group = Path(Path(f'/proc/{pid}/cgroup').read_text().strip().split('::', 1)[1])
    except (OSError, IndexError):
        return
    if not allowed_cgroup(group, uid):
        return
    started = False
    try:
        with connection.makefile('rb') as stream:
            raw = stream.readline(codex.MAX_PACKET + 2)
        if not raw.endswith(b'\n') or len(raw) > codex.MAX_PACKET + 1:
            raise codex.Hold('protocol')
        request = json.loads(raw)
        if (set(request) != {'model','effort','prompt','schema'}
                or not isinstance(request['model'], str) or not 1 <= len(request['model']) <= 160
                or request['effort'] is not None and not isinstance(request['effort'], str)
                or not isinstance(request['prompt'], str) or not isinstance(request['schema'], dict)):
            raise codex.Hold('protocol')

        def stop():
            return bool(select.select([connection], [], [], 0)[0])

        deadline = time.monotonic() + codex.SECONDS
        mapping = resolve_backend(stop, deadline)
        with tempfile.TemporaryDirectory(prefix='codex-hosts-', dir=runtime) as directory:
            hosts = Path(directory) / 'hosts'
            hosts.write_text(mapping, encoding='ascii')
            argv = codex.command(binary, credential, hosts=hosts)
            with codex.app_server(argv, stop, deadline) as rpc:
                try:
                    result = codex.turn(rpc, **request)
                finally:
                    started = rpc.started
    except codex.Hold as error:
        result = dict(error=error.reason, started=started or error.started, terminal=error.terminal,
                      partial=error.partial, counted=error.counted, **error.metadata)
    except (KeyError, TypeError, ValueError, OSError):
        result = {'error': 'protocol', 'started': started}
    try:
        connection.sendall(json.dumps(result, ensure_ascii=True, allow_nan=False).encode() + b'\n')
    except OSError:
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket', required=True, type=Path)
    parser.add_argument('--binary', required=True, type=Path)
    parser.add_argument('--binary-sha256', required=True)
    parser.add_argument('--sign-in-file', required=True, type=Path)
    parser.add_argument('--account-review', required=True, type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    check_account_review(args.account_review)
    private_inputs(args.socket, args.binary, args.binary_sha256, args.sign_in_file)
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(str(args.socket))
        listener.listen(1)
        while True:
            connection, _ = listener.accept()
            with connection:
                check_account_review(args.account_review)
                handle(connection, args.binary, args.sign_in_file, args.socket.parent)


if __name__ == '__main__':
    main()
