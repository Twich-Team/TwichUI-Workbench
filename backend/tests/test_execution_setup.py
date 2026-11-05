import fcntl
import json
from pathlib import Path
import socket
import subprocess
import sys
import time

import pytest

import milestone
import project_env


def test_config_keeps_workspace_and_loopback_boundaries():
    args = project_env.config_arguments()
    values = args[1::2]
    assert 'default_permissions="workbench"' in values
    assert any('"extends"=":workspace"' in value for value in values)
    assert any('"allow_local_binding"=false' in value for value in values)
    assert any('"network_proxy"=true' in value for value in values)
    assert not any('danger-full-access' in value or 'network_access=true' in value for value in values)
    command = milestone.command_for('test-model', Path('/tmp/result.json'))
    assert '--no-daemon' in command and '--ignore-user-config' in command
    assert '--output-schema' in command
    assert 'never' in command


def test_conservative_session_guard_never_assumes_daemon_is_idle(tmp_path):
    process = tmp_path / '1234'
    process.mkdir()
    (process / 'cmdline').write_bytes(b'/path/codex\0app-server\0--managed-daemon\0')
    assert 'cannot be verified' in milestone.active_session_guard(tmp_path)
    (process / 'cmdline').write_bytes(b'/path/codex\0exec\0')
    assert milestone.active_session_guard(tmp_path)
    (process / 'cmdline').write_bytes(b'/path/python3\0app.py\0')
    assert milestone.active_session_guard(tmp_path) is None


def test_busy_port_is_refused_without_replacement():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        with pytest.raises(RuntimeError, match='no service will be replaced'):
            project_env.require_ports([listener.getsockname()[1]])


def test_artifact_measurement_excludes_database_and_does_not_follow_symlinks(tmp_path):
    artifacts = tmp_path / 'artifacts'
    artifacts.mkdir()
    (artifacts / 'one.log').write_bytes(b'x' * 15)
    (tmp_path / 'private.sqlite3').write_bytes(b'x' * 100)
    (artifacts / 'link').symlink_to(tmp_path / 'private.sqlite3')
    assert project_env.artifact_bytes(tmp_path) == 15


def test_ready_requires_real_desktop_and_phone_artifacts(tmp_path):
    result = dict(status='READY_FOR_REVIEW', summary='done', screenshots_inspected=[], blockers=[])
    with pytest.raises(ValueError, match='desktop/phone'):
        milestone.validate_result(result, tmp_path)
    directory = tmp_path / 'artifacts/screenshots'
    directory.mkdir(parents=True)
    for width in [1440, 390]:
        (directory / f'{width}.png').write_bytes(b'\x89PNG\r\n\x1a\n' + b'\0' * 8 + width.to_bytes(4, 'big') + (900).to_bytes(4, 'big'))
    result['screenshots_inspected'] = ['artifacts/screenshots/1440.png', 'artifacts/screenshots/390.png']
    milestone.validate_result(result, tmp_path)
    result['screenshots_inspected'][0] = '../outside.png'
    with pytest.raises(ValueError):
        milestone.validate_result(result, tmp_path)


def test_timeout_stops_a_process_without_retry_and_writes_safe_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(milestone, 'artifact_bytes', lambda: 0)
    log = tmp_path / 'events.json'
    status, code = milestone.execute_bounded(
        [sys.executable, '-c', 'import time; time.sleep(30)'], {}, time.monotonic() + .15, log)
    assert (status, code) == ('BUDGET_EXHAUSTED', None)
    assert json.loads(log.read_text())['exit_code'] is not None


def test_logs_do_not_retain_tool_text_secrets_or_stderr(tmp_path, monkeypatch):
    monkeypatch.setattr(milestone, 'artifact_bytes', lambda: 0)
    secret = 'SYNTHETIC_SECRET_DO_NOT_RETAIN'
    program = f'import json; print(json.dumps({{"type":"item.completed", "output":"{secret}"}}))'
    log = tmp_path / 'events.json'
    status, code = milestone.execute_bounded([sys.executable, '-c', program], {}, time.monotonic()+5, log)
    assert status is None and code == 0
    assert secret not in log.read_text()
    assert json.loads(log.read_text())['event_counts'] == {'item.completed': 1}


def test_atomic_status_has_terminal_state(tmp_path):
    path = tmp_path / 'status.json'
    milestone.atomic_json(path, {'status': 'INTERRUPTED'})
    assert json.loads(path.read_text())['status'] == 'INTERRUPTED'
    assert not path.with_suffix('.tmp').exists()


def test_lock_prevents_a_second_launcher(tmp_path):
    with (tmp_path / 'lock').open('w') as first, (tmp_path / 'lock').open('w') as second:
        fcntl.flock(first, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            fcntl.flock(second, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_setup_does_not_implicitly_approve_an_execution():
    result = subprocess.run([sys.executable, str(project_env.ROOT/'scripts/milestone.py')],
                            capture_output=True, timeout=5)
    assert result.returncode == 2
    assert b'explicitly --approve milestone-2' in result.stderr


def test_successful_model_exit_does_not_override_failed_acceptance(tmp_path, monkeypatch):
    monkeypatch.setattr(milestone, 'ROOT', tmp_path)
    monkeypatch.setattr(milestone, 'current_model', lambda: 'fixture-model')
    monkeypatch.setattr(milestone, 'active_session_guard', lambda: None)
    monkeypatch.setattr(milestone, 'require_ports', lambda _: None)
    monkeypatch.setattr(milestone, 'artifact_bytes', lambda: 0)
    monkeypatch.setattr(milestone.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a[0], 0, b'ChatGPT', b''))
    (tmp_path / 'milestones').mkdir()
    (tmp_path / 'milestones/milestone-2.md').write_text('Sanitized test prompt, no real agent.')
    screenshots = tmp_path / 'artifacts/screenshots'
    screenshots.mkdir(parents=True)
    for width in [1440, 390]:
        (screenshots / f'{width}.png').write_bytes(b'\x89PNG\r\n\x1a\n'+b'\0'*8+width.to_bytes(4, 'big')+b'\0'*4)
    calls = []
    def fake_execute(command, *args, **kwargs):
        calls.append(command)
        if '--output-last-message' in command:
            Path(command[command.index('--output-last-message')+1]).write_text(json.dumps({
                'status': 'READY_FOR_REVIEW', 'summary': 'Synthetic success', 'blockers': [],
                'screenshots_inspected': ['artifacts/screenshots/1440.png', 'artifacts/screenshots/390.png']}))
            return None, 0
        return None, 1
    monkeypatch.setattr(milestone, 'execute_bounded', fake_execute)
    monkeypatch.setattr(sys, 'argv', ['milestone.py', '--approve', 'milestone-2'])
    assert milestone.main() == 2
    state = json.loads((tmp_path/'.local/milestone-status.json').read_text())
    assert state['status'] == 'BLOCKED'
    assert state['codex_exit_code'] == 0 and state['verify_exit_code'] == 1
    assert len(calls) == 2


@pytest.mark.parametrize('argv', [
    ['app-server', 'daemon', 'pid-update-loop'],
    ['exec', 'task'],
    ['app-server', '--listen', 'unix://'],
    ['app-server', 'daemon', 'start'],
    ['app-server', 'daemon', 'pid-update-loop', '--restore-release', 'other'],
    ['app-server', 'daemon', 'pid-update-loop-extra'],
    ['unknown-command'],
    [],
])
def test_codex_updater_execution_and_unknown_invocations_stay_blocked(tmp_path, argv):
    process = tmp_path / '12345'
    process.mkdir()
    command = b'\0'.join([b'/path/codex', *[arg.encode() for arg in argv]]) + b'\0'
    (process / 'cmdline').write_bytes(command)
    reason = milestone.active_session_guard(tmp_path)
    assert reason is not None
    if argv == ['app-server', 'daemon', 'pid-update-loop']:
        assert 'update helper' in reason and 'SIGTERM' in reason
    else:
        assert 'session idleness cannot be verified' in reason
