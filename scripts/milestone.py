"""One approved run, no retries, services, agent teams or notification delivery."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import select
import signal
import struct
import subprocess
import sys
import time
import tomllib
from uuid import uuid4

from project_env import (ARTIFACT_LIMIT, CODEX, ROOT, artifact_bytes, config_arguments,
                         environment, require_ports)

STATUSES = {'READY_FOR_REVIEW', 'BLOCKED', 'BUDGET_EXHAUSTED', 'INTERRUPTED'}
EXIT_CODES = {'READY_FOR_REVIEW': 0, 'BLOCKED': 2, 'BUDGET_EXHAUSTED': 4, 'INTERRUPTED': 130}


def active_session_guard(proc=Path('/proc')):
    """Conservative: an idle daemon is also refused when activity cannot be verified.

    Two bounded metadata-query attempts failed on the installed daemon. Do not
    infer idle from process age or inspect private thread databases as a fallback.
    """
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            args = (entry / 'cmdline').read_bytes().split(b'\0')
            if not args or Path(os.fsdecode(args[0])).name != 'codex':
                continue
            # 0.166.0 updater executes installers and can restart a running daemon.
            # It is not an idle/bookkeeping exception. Strip only /proc's final NUL.
            argv = args[:-1] if args[-1:] == [b''] else args
            if argv[1:] == [b'app-server', b'daemon', b'pid-update-loop']:
                return ('Codex update helper is still running; daemon stop does not stop it. '
                        'See docs/EXECUTION.md for verified targeted SIGTERM shutdown, then retry preflight.')
            return 'Another Codex process or shared daemon exists; session idleness cannot be verified.'

        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
    return None


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    with open(temporary, 'w') as output:
        json.dump(value, output, indent=2)
        output.write('\n')
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


def current_model():
    config = tomllib.loads(Path('/home/rich/.codex/config.toml').read_text())
    model = config.get('model')
    models = json.loads(Path('/home/rich/.codex/models_cache.json').read_text()).get('models', [])
    if not isinstance(model, str) or not any(
        row.get('slug') == model and any(level.get('effort') == 'high' for level in
                                        row.get('supported_reasoning_levels', [])) for row in models
    ):
        raise RuntimeError('Current configured model/High effort is not established by the local catalog.')
    return model


def command_for(model, result_path):
    # --ignore-user-config leaves existing CODEX_HOME authentication in place.
    # No provider override, API key, paid fallback, resume, fork or daemon service.
    return [str(CODEX), '--no-daemon', '--ask-for-approval', 'never', 'exec',
            '--ignore-user-config', '--ephemeral', '--strict-config', *config_arguments(),
            '--model', model, '--cd', str(ROOT), '--json', '--color', 'never',
            '--output-schema', str(ROOT / 'tools/milestone-result.schema.json'),
            '--output-last-message', str(result_path), '-']


def validate_result(result, root=ROOT):
    if set(result) != {'status', 'summary', 'screenshots_inspected', 'blockers'}:
        raise ValueError('Invalid terminal result fields')
    if result['status'] not in STATUSES or not isinstance(result['summary'], str):
        raise ValueError('Invalid terminal status')
    if (len(result['summary']) > 2000 or not isinstance(result['blockers'], list)
            or len(result['blockers']) > 10 or not all(isinstance(item, str) for item in result['blockers'])):
        raise ValueError('Invalid terminal summary')
    paths = result['screenshots_inspected']
    if not isinstance(paths, list) or len(paths) > 20:
        raise ValueError('Invalid screenshot inspection record')
    if result['status'] != 'READY_FOR_REVIEW':
        return
    widths = []
    for name in paths:
        if not isinstance(name, str):
            raise ValueError('Invalid screenshot path')
        path = (root / name).resolve()
        if not path.is_relative_to((root / 'artifacts/screenshots').resolve()) or path.suffix != '.png':
            raise ValueError('Screenshot outside the retained evidence directory')
        with path.open('rb') as screenshot:
            header = screenshot.read(24)
        if header[:8] != b'\x89PNG\r\n\x1a\n' or len(header) != 24:
            raise ValueError('Screenshot is not a PNG')
        widths.append(struct.unpack('>I', header[16:20])[0])
    if result['blockers'] or not any(width >= 1024 for width in widths) or not any(width <= 430 for width in widths):
        raise ValueError('Ready requires no blockers and reported desktop/phone screenshot inspection')


def stop_group(process):
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


def execute_bounded(command, env, deadline, event_log, prompt=None, interrupted=lambda: False):
    """Keep only event types/counts; never retain tool stdout, prompts or stderr text."""
    counts = Counter()
    byte_count = 0
    process = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.PIPE if prompt else subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    if prompt:
        process.stdin.write(prompt.encode())
        process.stdin.close()
    buffer = b''
    try:
        while True:
            if interrupted():
                return 'INTERRUPTED', None
            if time.monotonic() >= deadline or artifact_bytes() > ARTIFACT_LIMIT or byte_count > 8 * 1024 * 1024:
                return 'BUDGET_EXHAUSTED', None
            if select.select([process.stdout], [], [], .2)[0]:
                raw = os.read(process.stdout.fileno(), 65536)
                if not raw:
                    break
                byte_count += len(raw)
                buffer = (buffer + raw)[-1024 * 1024:]
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1)
                    try:
                        event = json.loads(line)
                        kind = event.get('type')
                        # Fixed vocabulary only: malicious/arbitrary event strings are not logged.
                        if kind in {'thread.started', 'turn.started', 'turn.completed', 'turn.failed',
                                    'item.started', 'item.updated', 'item.completed', 'error'}:
                            counts[kind] += 1
                    except (ValueError, AttributeError):
                        pass
            if process.poll() is not None and not select.select([process.stdout], [], [], 0)[0]:
                break
        return None, process.wait(timeout=5)
    finally:
        stop_group(process)
        atomic_json(event_log, {'event_counts': dict(counts), 'output_bytes_discarded': byte_count,
                                'exit_code': process.poll()})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--approve', choices=['milestone-2'], help='Explicitly authorize exactly PLAN.md Milestone 2')
    parser.add_argument('--preflight', action='store_true', help='Read-only environment/session checks; never starts an agent')
    parser.add_argument('--minutes', type=int, default=120)
    args = parser.parse_args()
    if not args.preflight and not args.approve:
        parser.error('Use --preflight or explicitly --approve milestone-2; setup does not authorize Milestone 2')
    if not 1 <= args.minutes <= 240:
        parser.error('--minutes must be 1–240')
    os.umask(0o077)
    local = ROOT / '.local'
    if local.is_symlink():
        raise SystemExit('BLOCKED: unsafe local state directory')
    local.mkdir(mode=0o700, exist_ok=True)
    lock_fd = os.open(local / 'milestone.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock_fd, 'w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('BLOCKED: another supervised milestone run owns this project.')
            return 2
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        run = local / 'runs' / (stamp + '-' + uuid4().hex[:8])
        run.mkdir(parents=True, mode=0o700)
        state = {'status': 'INTERRUPTED', 'phase': 'preflight', 'run': str(run.relative_to(ROOT)),
                 'milestone': args.approve, 'started_at': datetime.now(timezone.utc).isoformat(),
                 'reason': 'No verified terminal result yet.', 'codex_exit_code': None, 'verify_exit_code': None}
        def finish(status, reason):
            state.update(status=status, reason=reason, phase='terminal', finished_at=datetime.now(timezone.utc).isoformat())
            atomic_json(run / 'status.json', state)
            atomic_json(local / 'milestone-status.json', state)
            print(f'{status}: {reason}')
            print(f'Local indication: {local / "milestone-status.json"}')
            return EXIT_CODES[status]
        atomic_json(local / 'milestone-status.json', state)
        try:
            require_ports([5180, 8787, 5280, 9187, 19880, 19881])
            if artifact_bytes() > ARTIFACT_LIMIT:
                return finish('BUDGET_EXHAUSTED', 'Local evidence exceeds 512 MiB; archive explicitly, then retry.')
            auth = subprocess.run([str(CODEX), 'login', 'status'], capture_output=True, timeout=10)
            if auth.returncode or b'ChatGPT' not in auth.stdout + auth.stderr:
                return finish('BLOCKED', 'Existing ChatGPT authentication could not be verified; no login change was attempted.')
            model = current_model()
            if (ROOT / '.codex/config.toml').exists():
                return finish('BLOCKED', 'Project Codex config needs explicit reconciliation with the launch policy.')
            conflict = active_session_guard()
            if conflict:
                return finish('BLOCKED', conflict)
            if args.preflight:
                state.update(phase='preflight_passed', reason='Preflight passed; no milestone was executed or accepted.')
                atomic_json(run / 'status.json', state)
                atomic_json(local / 'milestone-status.json', state)
                print('PREFLIGHT_PASSED: no milestone was executed or accepted.')
                return 0
            interrupted = []
            for sig in [signal.SIGINT, signal.SIGTERM, signal.SIGHUP]:
                signal.signal(sig, lambda *_: interrupted.append(True))
            deadline = time.monotonic() + args.minutes * 60
            env = environment()
            for key in list(env):
                if key in {'OPENAI_API_KEY', 'CODEX_API_KEY', 'OPENAI_BASE_URL', 'ANTHROPIC_API_KEY', 'GH_TOKEN', 'GITHUB_TOKEN'}:
                    env.pop(key)
            if env.get('WORKBENCH_MILESTONE_RUN'):
                return finish('BLOCKED', 'Recursive milestone execution is forbidden.')
            env['WORKBENCH_MILESTONE_RUN'] = '1'
            prompt = (ROOT / 'milestones/milestone-2.md').read_text()
            state.update(phase='running', model=model, effort='high', runtime_minutes=args.minutes)
            atomic_json(local / 'milestone-status.json', state)
            status, code = execute_bounded(command_for(model, run / 'result.json'), env, deadline,
                                           run / 'events.json', prompt, lambda: bool(interrupted))
            state['codex_exit_code'] = code
            if status:
                return finish(status, 'Execution ended at its runtime, output, artifact or interruption boundary; no automatic retry.')
            if code:
                return finish('INTERRUPTED', 'Codex exited without verified milestone acceptance; inspect local run metadata.')
            result = json.loads((run / 'result.json').read_text())
            validate_result(result, ROOT)
            if result['status'] != 'READY_FOR_REVIEW':
                return finish(result['status'], 'Agent recorded a non-ready terminal result; review the private result file.')
            status, code = execute_bounded(['npm', 'run', 'verify'], env, deadline, run / 'verification.json',
                                           interrupted=lambda: bool(interrupted))
            state['verify_exit_code'] = code
            if status:
                return finish(status, 'Acceptance verification reached a bounded execution limit.')
            if code:
                return finish('BLOCKED', 'Independent final verification failed; a successful model exit was not accepted.')
            return finish('READY_FOR_REVIEW', 'Approved milestone passed verification with reported desktop/phone inspection; review the working result.')
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            return finish('BLOCKED', 'A required local preflight, result or process check failed. No retry or trust expansion was attempted.')


if __name__ == '__main__':
    sys.exit(main())
