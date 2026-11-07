"""One read-only Codex eligibility probe in a named system-manager unit."""

import argparse
import errno
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time

from workbench import codex_provider as codex
from codex_connector import private_inputs, resolve_backend


UNIT_PREFIX = 'workbench-codex-eligibility-'
MODEL = 'gpt-6.1-sol'
BIN = Path('/home/rich/.codex/packages/standalone/releases/0.166.0-x86_64-unknown-linux-musl/bin/codex')
BIN_SHA = 'b8a11fedf24b2dfb59a70c9e52b030f9a508dcd49cd2dd0d41956f4c785156b8'
SIGN_IN = Path('/home/rich/.codex/auth.json')
DENY = frozenset(('0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 169.254.0.0/16 172.16.0.0/12 '
                  '192.168.0.0/16 224.0.0.0/4 240.0.0.0/4 ::/128 ::1/128 fc00::/7 fe80::/10 ff00::/8').split())


def inspect_eligibility(rpc, record):
    """Emit only allowlisted checks. This path has no thread or turn RPC."""
    def read(method, parameters):
        record('read_attempt', method=method)
        reply = rpc.call(method, parameters)
        record('read_response', method=method)
        return reply

    def report(checks, phase):
        for check in checks:
            record('eligibility_check', phase=phase, **check)
        return next((check for check in checks if check['reason']), None)

    record('initialize_attempt')
    codex.initialize(rpc)
    record('initialized')
    phase = 'account/read'
    account = report(codex.account_checks(read(phase, {'refreshToken': False})), phase)
    if account:
        return account

    phase = 'account/rateLimits/read'
    first = report(codex.allowance_checks(read(phase, {})), phase)
    catalog = 'model/list'
    model = report(codex.catalog_checks(read(catalog, {'limit': 64, 'includeHidden': False}), MODEL, None), catalog)
    if first or model:
        return first or model
    return report(codex.allowance_checks(read(phase, {})), phase)


def private_json(path):
    info = path.lstat()
    if (not path.is_file() or path.resolve() != path or info.st_uid != os.getuid()
            or info.st_mode & 0o077 or info.st_size > 8192):
        raise ValueError('Private probe control receipt required')
    return json.loads(path.read_bytes())


def confinement(slot):
    unit = f'{UNIT_PREFIX}{slot}.service'
    if os.getuid() != 1000 or os.getgid() != 1000 or Path('/proc/self/cgroup').read_text().strip() != f'0::/system.slice/{unit}':
        raise ValueError('Probe requires the named Rich-owned system-manager cgroup')
    status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
    if status['NoNewPrivs'].strip() != '1' or int(status['CapEff'], 16) or int(status['CapPrm'], 16):
        raise ValueError('Expected no-new-privileges and zero capabilities')
    properties = ('User', 'Group', 'IPAddressAllow', 'IPAddressDeny', 'SocketBindDeny', 'PrivateNetwork',
                  'NoNewPrivileges', 'KillMode', 'RuntimeMaxUSec', 'TimeoutStopUSec')
    output = subprocess.run(['/usr/bin/systemctl', 'show', '-p', ','.join(properties), unit],
                            check=True, capture_output=True, text=True, timeout=3).stdout
    values = dict(line.split('=', 1) for line in output.splitlines())
    if (values['User'] != 'rich' or values['Group'] != 'rich' or values['NoNewPrivileges'] != 'yes'
            or values['PrivateNetwork'] != 'no' or values['KillMode'] != 'control-group'
            or values['IPAddressAllow'].split() != ['127.0.0.53/32']
            or frozenset(values['IPAddressDeny'].split()) != DENY or values['SocketBindDeny'] != 'any'
            or values['RuntimeMaxUSec'] != '2min' or values['TimeoutStopUSec'] != '5s'):
        raise ValueError('System service policy differs from the reviewed probe')
    for family, address in ((socket.AF_INET, ('0.0.0.0', 0)), (socket.AF_INET6, ('::', 0))):
        with socket.socket(family) as client:
            try:
                client.bind(address)
            except OSError as error:
                if error.errno not in {errno.EPERM, errno.EACCES}:
                    raise
            else:
                raise ValueError('Kernel allowed an explicit IP bind')
    for destination in (('127.0.0.1', 8080), ('10.0.0.9', 8765)):
        try:
            with socket.create_connection(destination, timeout=.75):
                raise ValueError('Kernel allowed a private-address connection')
        except TimeoutError:
            pass
        except OSError as error:
            if error.errno not in {errno.EPERM, errno.EACCES}:
                raise
    runtime = Path('/run') / (UNIT_PREFIX + str(slot))
    if Path(os.environ.get('RUNTIME_DIRECTORY', '')) != runtime:
        raise ValueError('Unexpected diagnostic runtime directory')
    return runtime


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('slot', type=int, choices=(1, 2))
    args = parser.parse_args()
    os.umask(0o077)
    started = time.monotonic()
    runtime = confinement(args.slot)
    evidence = Path(__file__).resolve().parents[2]
    window = private_json(evidence / 'window.json')
    now = time.time()
    opened = window['opened_epoch']
    if (type(opened) is not int or not opened <= now <= opened + 475
            or window['boot_id'] != Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            or window['settled_and_capacity_verified'] is not True):
        raise ValueError('Ten-minute diagnostic window or preflight differs')
    if args.slot == 2:
        followup = private_json(evidence / 'followup.json')
        if followup.get('purpose') not in {'confirm_corrected_check', 'verify_settled_account_signal'}:
            raise ValueError('Specific follow-up purpose is required')
        first = evidence / 'probe-1.jsonl'
        if first.resolve() != first or first.stat().st_size > 8192 or first.stat().st_mode & 0o077:
            raise ValueError('Invalid first probe evidence')
        receipt = json.loads(first.read_bytes().splitlines()[-1])
        if receipt.get('event') != 'closed' or receipt.get('settled') is not True:
            raise ValueError('First probe outcome is uncertain')
    with (evidence / f'probe-{args.slot}.jsonl').open('x') as receipt:
        def record(event, **details):
            receipt.write(json.dumps(dict(event=event, time=time.time(), **details), allow_nan=False) + '\n')
            receipt.flush()
            os.fsync(receipt.fileno())

        record('open', slot=args.slot, kernel_bind_and_private_egress_verified=True)
        try:
            private_inputs(runtime / 'no-listener.sock', BIN, BIN_SHA, SIGN_IN)
            deadline = started + codex.SECONDS - 1  # Leave startup slack under the 120-second unit cap.
            mapping = resolve_backend(lambda: False, deadline)
            record('resolver', state='observed')
            with tempfile.TemporaryDirectory(dir=runtime, prefix='hosts-') as directory:
                hosts = Path(directory) / 'hosts'
                hosts.write_text(mapping, encoding='ascii')
                with codex.app_server(codex.command(BIN, SIGN_IN, hosts=hosts), lambda: False, deadline) as rpc:
                    failed = inspect_eligibility(rpc, record)
            record('closed', settled=True, eligible=failed is None, first_hold=failed)
            print('Read-only eligibility probe settled. See the redacted local receipt.')
            return 0 if failed is None else 2
        except codex.Hold as error:
            record('closed', settled=False, eligible=False, reason=error.reason, **error.metadata)
        except (OSError, ValueError, subprocess.SubprocessError):
            record('closed', settled=False, eligible=False, reason='local_probe_failure')
        print('Eligibility probe held. See the redacted local receipt.')
        return 2


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, TypeError, KeyError, IndexError, subprocess.SubprocessError):
        raise SystemExit('Eligibility probe refused before signed-in reads. Verify confinement and the local window.') from None
