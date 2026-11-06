"""Rich's private connector. Start only as an explicitly installed user service."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import select
import socket
import stat
import struct
import time

from workbench import codex_provider as codex


def private_inputs(socket_path, binary, binary_hash, credential):
    parent = socket_path.parent
    if (parent.resolve() != parent or parent.stat().st_uid != os.getuid()
            or parent.stat().st_mode & 0o077 or os.path.lexists(socket_path)
            or binary.resolve() != binary or not binary.is_file()
            or sha256(binary.read_bytes()).hexdigest() != binary_hash
            or credential.resolve() != credential or credential.stat().st_uid != os.getuid()
            or not stat.S_ISREG(credential.stat().st_mode) or credential.stat().st_mode & 0o077):
        raise ValueError('The connector requires its private runtime directory, pinned CLI and owned sign-in file.')


def handle(connection, argv):
    connection.settimeout(3)
    pid, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    if uid != os.getuid():
        return
    try:
        components = Path(f'/proc/{pid}/cgroup').read_text().strip().split('::', 1)[1].split('/')
    except (OSError, IndexError):
        return
    if not any(part == 'workbench-everyday-worker.service' or re.fullmatch(
            r'workbench-real-codex-task-lease-[A-Za-z0-9-]+\.service', part) for part in components):
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

        with codex.app_server(argv, stop, time.monotonic() + codex.SECONDS) as rpc:
            try:
                result = codex.turn(rpc, **request)
            finally:
                started = rpc.started
    except codex.Hold as error:
        result = dict(error=error.reason, started=started or error.started, terminal=error.terminal,
                      partial=error.partial, counted=error.counted)
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
    parser.add_argument('--included-only-confirmed', action='store_true',
                        help='Start only after owner review of Automatic reload and the additional usage-credit balance.')
    args = parser.parse_args()
    if not args.included_only_confirmed:
        parser.error('Explicit included-only account confirmation is required.')
    os.umask(0o077)
    private_inputs(args.socket, args.binary, args.binary_sha256, args.sign_in_file)
    argv = codex.command(args.binary, args.sign_in_file, network=True)
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(str(args.socket))
        listener.listen(1)
        while True:
            connection, _ = listener.accept()
            with connection:
                handle(connection, argv)


if __name__ == '__main__':
    main()
