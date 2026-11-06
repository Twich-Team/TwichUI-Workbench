"""Offline Workbench administrator checks. Fakes never invoke a privileged command."""

import json
import os
import subprocess

import pytest

from ops import workbench_admin as admin


@pytest.mark.parametrize('request_body', [
    {'op': 'service', 'action': 'restart', 'role': 'sshd'},
    {'op': 'service', 'action': 'write', 'role': 'lan', 'release': 'everyday-../../etc'},
    {'op': 'service', 'action': 'write', 'role': 'api', 'release': 'everyday-a\nUser=root'},
    {'op': 'service', 'action': 'write', 'role': 'api', 'release': 'everyday-a %i'},
    {'op': 'service', 'action': 'write', 'role': 'api', 'release': 'everyday-a', 'ExecStart': '/bin/bash'},
    {'op': 'service', 'action': 'start', 'role': 'worker', 'release': 'everyday-a'},
    {'op': 'drop-in', 'action': 'on', 'contents': '[Service]\nUser=root'},
    {'op': 'firewall', 'action': 'add', 'from': '0.0.0.0/0'},
    {'op': 'firewall', 'action': 'delete'},
    {'op': 'probe', 'mode': 'guard'},
    {'op': 'probe', 'mode': 'codex', 'host': 'example.org'},
    {'op': 'probe-clean', 'mode': 'ssh'},
    {'op': 'inspect', 'target': 'logs', 'role': 'worker', 'lines': True},
    {'op': 'inspect', 'target': 'logs', 'role': 'worker', 'lines': 201},
    {'op': 'inspect', 'target': 'unit', 'role': 'networkd'},
    {'op': 'inspect', 'target': 'environ', 'role': 'codex'},
    {'op': 'extend', 'expires': 999999999},
    {'op': 'revoke', 'force': True},
    [],
])
def test_rejects_unapproved_commands_arguments_and_paths(request_body):
    with pytest.raises(admin.Refused):
        admin.validate(request_body)


def test_json_duplicate_keys_rejected():
    with pytest.raises(admin.Refused, match='Duplicate'):
        json.loads('{"op":"status","op":"revoke"}', object_pairs_hook=admin.unique_keys)


@pytest.mark.parametrize('changed', [
    {'now': 200}, {'now': 99}, {'boot_id': 'different'}, {'cgroup': '/system.slice/workbench-everyday-worker.service'},
    {'cgroup': '/user.slice/user-1000.slice/session-79.scope'}, {'login_uid': '0'}, {'session_id': '79'},
])
def test_grant_requires_the_live_original_session_and_window(changed):
    group = '/user.slice/user-1000.slice/session-78.scope'
    grant = dict(created=100, expires=200, boot_id='boot', cgroup=group, session_id='78')
    observed = dict(now=101, boot_id='boot', cgroup=group, login_uid='1000', session_id='78')
    admin.check_grant(grant, **observed)
    with pytest.raises(admin.Refused):
        admin.check_grant(grant, **(observed | changed))
    with pytest.raises(admin.Refused):
        admin.check_grant(grant | {'expires': 100 + 7 * 86400 + 1}, **observed)


@pytest.mark.parametrize('role', admin.ROLES)
def test_renderer_cannot_import_untrusted_root_directives(role):
    release = admin.ROLLBACK.name if role.startswith('review-') else 'everyday-system-20261106'
    output = admin.unit_bytes(role, release).decode()
    assert 'User=rich\nGroup=rich\nSlice=system.slice\n' in output
    assert output.count('\nExecStart=') == 1
    assert '\nExecStart=/usr/bin/env -i ' in output
    for absent in ('EnvironmentFile=', 'ExecStartPre=', 'ExecStop=', 'ExecStart=+', 'Wants=', 'RootDirectory='):
        assert absent not in output
    assert ('NoNewPrivileges=yes\n' in output) == (role != 'worker')
    if role == 'lan':
        assert '--host 10.0.0.9 --port 9487' in output
        assert 'IPAddressAllow=10.0.0.0/24\n' in output
    if role == 'codex':
        assert 'SocketBindDeny=any\n' in output
        assert 'IPAddressAllow=127.0.0.53/32\n' in output


def test_rollback_cannot_target_a_different_release():
    with pytest.raises(admin.Refused, match='fixed rollback'):
        admin.unit_bytes('review-api', 'everyday-another')


def test_checked_read_refuses_symlink_hardlink_fifo_and_wide_modes(tmp_path):
    source = tmp_path / 'source'
    source.write_bytes(b'test')
    source.chmod(0o600)
    assert admin.checked_file(source, 4, owner=os.getuid(), private=True) == b'test'
    link = tmp_path / 'link'
    link.symlink_to(source)
    with pytest.raises(OSError):
        admin.checked_file(link, 16, owner=os.getuid())
    link.unlink()
    os.link(source, link)
    with pytest.raises(admin.Refused):
        admin.checked_file(source, 16, owner=os.getuid())
    link.unlink()
    source.chmod(0o666)
    with pytest.raises(admin.Refused):
        admin.checked_file(source, 16, owner=os.getuid())
    os.mkfifo(link)
    with pytest.raises(admin.Refused):
        admin.checked_file(link, 16, owner=os.getuid())


def test_unit_loader_compares_whole_file(monkeypatch):
    monkeypatch.setattr(admin, 'checked_file', lambda *_: admin.unit_bytes('api', 'everyday-test'))
    assert admin.read_unit('api') == ('everyday-test', False)
    monkeypatch.setattr(admin, 'checked_file', lambda *_: admin.unit_bytes('api', 'everyday-test') + b'User=root\n')
    with pytest.raises(admin.Refused, match='bytes differ'):
        admin.read_unit('api')


def test_effective_unit_alias_dropins_and_mapped_path_rejected(monkeypatch):
    monkeypatch.setattr(admin, 'approved_drop', lambda: False)
    base = {'LoadState': 'loaded', 'Names': 'workbench-everyday-worker.service',
            'FragmentPath': '/etc/systemd/system/workbench-everyday-worker.service', 'DropInPaths': '',
            'User': 'rich', 'Group': 'rich', 'NeedDaemonReload': 'no'}
    admin.check_definition('worker', base)
    for mutation in ({'DropInPaths': '/etc/systemd/system/service.d/overrides.conf'},
                     {'FragmentPath': '/run/systemd/transient/workbench-everyday-worker.service'},
                     {'User': 'root'}, {'Names': base['Names'] + ' sshd.service'}, {'NeedDaemonReload': 'yes'}):
        with pytest.raises(admin.Refused, match='Unexpected effective'):
            admin.check_definition('worker', base | mutation)


def test_stop_fences_new_intake_and_unsettled_execution(monkeypatch):
    monkeypatch.setattr(admin, 'read_unit', lambda _: ('everyday-test', False))
    monkeypatch.setattr(admin, 'check_definition', lambda *_: None)
    states = {'worker': 'active', 'api': 'active', 'lan': 'inactive'}
    monkeypatch.setattr(admin, 'show', lambda unit: {'ActiveState': states[unit.split('-')[-1][:-8]]})
    monkeypatch.setattr(admin, 'command', lambda *_a, **_kw: pytest.fail('Unsafe stop called'))
    with pytest.raises(admin.Refused, match='Close Workbench ingress'):
        admin.service('stop', 'worker')
    states['api'] = 'inactive'

    def pending(*_):
        raise admin.Refused('unsettled')

    monkeypatch.setattr(admin, 'require_settled', pending)
    with pytest.raises(admin.Refused, match='unsettled'):
        admin.service('stop', 'worker')
    calls = []
    monkeypatch.setattr(admin, 'require_settled', lambda *_: calls.append('settled'))
    monkeypatch.setattr(admin, 'command', lambda *argv, **kw: calls.append(argv[-2:]))
    admin.service('stop', 'worker')
    assert calls == ['settled', ('stop', 'workbench-everyday-worker.service')]


def test_two_release_writers_and_legacy_start_refused(monkeypatch):
    monkeypatch.setattr(admin, 'check_stage_as_rich', lambda _: None)
    monkeypatch.setattr(admin, 'show', lambda _: {'ActiveState': 'active'})
    monkeypatch.setattr(admin, 'read_unit', lambda _: ('everyday-old', False))
    with pytest.raises(admin.Refused, match='Another Workbench release'):
        admin.check_release_peers('worker', 'everyday-new', False)
    monkeypatch.setattr(admin, 'read_unit', lambda _: (admin.ROLLBACK.name, True))
    monkeypatch.setattr(admin, 'check_definition', lambda *_: None)
    with pytest.raises(admin.Refused, match='Render the fixed rollback'):
        admin.service('start', 'review-api')


def test_firewall_never_adopts_external_rule_and_recovers_interruption(monkeypatch, tmp_path):
    monkeypatch.setattr(admin, 'STATE', tmp_path)
    monkeypatch.setattr(admin, 'checked_file', lambda path, *_a, **_kw: path.read_bytes())
    monkeypatch.setattr(admin, 'check_lan_address', lambda: None)
    monkeypatch.setattr(admin, 'show', lambda _: {'ActiveState': 'inactive'})
    live = [None]
    monkeypatch.setattr(admin, 'lan_rule', lambda: live[0])
    calls = []

    def command(*argv):
        calls.append(argv)
        live[0] = 'tagged' if argv[1] == 'allow' else None

    monkeypatch.setattr(admin, 'command', command)
    live[0] = 'external'
    assert 'pre-existing' in admin.firewall('add')
    assert 'No rule is owned' in admin.firewall('remove')
    assert not calls
    live[0] = None
    assert 'added' in admin.firewall('add')
    assert calls[-1] == (admin.UFW, 'allow', *admin.FIREWALL, 'comment', admin.FIREWALL_TAG)
    (tmp_path / 'firewall').write_bytes(b'adding\n')
    assert 'already present' in admin.firewall('add')
    assert (tmp_path / 'firewall').read_bytes() == b'added\n'
    (tmp_path / 'firewall').write_bytes(b'removing\n')
    assert 'absent' in admin.firewall('remove')
    assert calls[-1] == (admin.UFW, 'delete', 'allow', *admin.FIREWALL)
    assert not (tmp_path / 'firewall').exists()
    live[0] = 'tagged'
    with pytest.raises(admin.Refused, match='no protected ownership'):
        admin.firewall('remove')


def test_firewall_reports_conflicting_and_duplicate_rules(monkeypatch):
    baseline = 'Status: active\nDefault: deny (incoming), allow (outgoing), disabled (routed)\n'
    for text in ('9487/tcp ALLOW IN Anywhere', admin.FIREWALL_LINE + '\n' + admin.FIREWALL_LINE):
        monkeypatch.setattr(admin, 'firewall_status', lambda: baseline + text)
        with pytest.raises(admin.Refused, match='conflicting'):
            admin.lan_rule()
    monkeypatch.setattr(admin, 'firewall_status', lambda: baseline + admin.FIREWALL_LINE)
    assert admin.lan_rule() == 'external'


def test_probe_command_uses_only_static_rich_entrypoints(monkeypatch, tmp_path):
    monkeypatch.setattr(admin, 'STATE', tmp_path)
    monkeypatch.setattr(admin, 'show', lambda _: {'LoadState': 'not-found'})
    monkeypatch.setattr(admin, 'check_stage_as_rich', lambda _: None)
    calls = []
    monkeypatch.setattr(admin, 'command', lambda *argv, **kw: calls.append(argv))
    admin.probe('codex')
    assert calls[-1][-3:] == (admin.SELF, '--probe', 'codex')
    admin.probe('guard', 'everyday-safe')
    for argv in calls:
        assert argv[0] == '/usr/bin/systemd-run'
        assert 'User=rich' in argv and 'Group=rich' in argv and 'NoNewPrivileges=yes' in argv
        assert '--expand-environment=no' in argv


def test_temp_cleanup_requires_the_protected_probe_identity(monkeypatch, tmp_path):
    monkeypatch.setattr(admin, 'STATE', tmp_path)
    unit = admin.probe_unit('worker')
    marker = tmp_path / 'probe-worker'
    marker.write_bytes(b'a' * 24)
    monkeypatch.setattr(admin, 'checked_file', lambda path, *_a, **_kw: path.read_bytes())
    info = {'LoadState': 'loaded', 'Transient': 'yes', 'Names': unit, 'User': 'rich', 'Group': 'rich',
            'FragmentPath': '/run/systemd/transient/' + unit,
            'Description': 'TwichUI Workbench administrative probe worker ' + 'b' * 24}
    monkeypatch.setattr(admin, 'show', lambda _: info)
    monkeypatch.setattr(admin, 'command', lambda *_a, **_kw: pytest.fail('Unowned transient stop'))
    with pytest.raises(admin.Refused, match='identity differs'):
        admin.clean_probe('worker')
    assert marker.exists()


def test_tls_provision_only_writes_the_pinned_private_pair(monkeypatch, tmp_path):
    monkeypatch.setattr(admin, 'UID', os.getuid())
    stage, source = tmp_path / 'everyday-test', tmp_path / 'cert-source'
    monkeypatch.setattr(admin, 'STAGES', tmp_path)
    monkeypatch.setattr(admin, 'CERT_SOURCE', source)
    source.mkdir(mode=0o700)
    stage.mkdir(mode=0o700)
    for directory in ('ops', 'release', 'scratch', 'artifacts'):
        (stage / directory).mkdir(mode=0o700)
    for name in ('workbench-home-lan-ca.crt', 'server.crt', 'server.key'):
        (source / name).write_text(name)
        (source / name).chmod(0o600)

    def openssl(*argv):
        if '-fingerprint' in argv:
            return 'sha256 Fingerprint=06:E8:DE:82:AF:EA:0A:86:57:65:70:D1:02:4B:0D:DC:89:D3:8E:9C:9B:03:1F:95:F0:EE:CC:9D:32:E7:71:BD'
        return 'matching public key'

    monkeypatch.setattr(admin, 'command', openssl)
    admin.tls_provision(stage.name)
    assert {path.name for path in (stage / 'ops/tls').iterdir()} == {'server.crt', 'server.key'}
    admin.tls_provision(stage.name)
    target = stage / 'ops/tls/server.key'
    target.write_text('different')
    with pytest.raises(admin.Refused, match='different TLS file'):
        admin.tls_provision(stage.name)
    assert target.read_text() == 'different'


def test_status_and_synthetic_commands_are_bounded():
    assert admin.run(('/usr/bin/printf', 'abc')) == (0, 'abc')
    with pytest.raises(admin.Refused, match='limit reached'):
        admin.run(('/usr/bin/yes',), seconds=2)
    with pytest.raises(admin.Refused, match='limit reached'):
        admin.run(('/usr/bin/sleep', '1'), seconds=0.05)


def test_systemd_parses_all_rendered_unit_definitions(tmp_path):
    paths = []
    for role in admin.ROLES:
        path = tmp_path / admin.unit_name(role)
        path.write_bytes(admin.unit_bytes(role, admin.ROLLBACK.name))
        paths.append(str(path))
    result = subprocess.run(['/usr/bin/systemd-analyze', 'verify', *paths], capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr.decode()


def test_owner_recovery_may_restore_review_but_cannot_activate_candidate():
    for request in ({'op': 'service', 'action': 'stop', 'role': 'codex'},
                    {'op': 'service', 'action': 'write', 'role': 'review-api', 'release': admin.ROLLBACK.name},
                    {'op': 'firewall', 'action': 'remove'}, {'op': 'drop-in', 'action': 'off'}):
        admin.recovery_request(admin.validate(request))
    for request in ({'op': 'service', 'action': 'start', 'role': 'codex'},
                    {'op': 'service', 'action': 'write', 'role': 'api', 'release': 'everyday-test'},
                    {'op': 'service', 'action': 'enable', 'role': 'review-api'},
                    {'op': 'firewall', 'action': 'add'}, {'op': 'drop-in', 'action': 'on'},
                    {'op': 'probe', 'mode': 'guard', 'release': 'everyday-test'}):
        with pytest.raises(admin.Refused, match='only closes'):
            admin.recovery_request(admin.validate(request))


def test_boot_link_refuses_a_nonmanaged_dependency(tmp_path, monkeypatch):
    monkeypatch.setattr(admin, 'SYSTEM', tmp_path)
    monkeypatch.setattr(admin, 'protected_dir', lambda _: None)
    directory = tmp_path / 'multi-user.target.wants'
    directory.mkdir()
    path = directory / admin.unit_name('api')
    path.symlink_to('/etc/systemd/system/sshd.service')
    with pytest.raises(admin.Refused, match='outside the managed location'):
        admin.boot_link('api', False)
    assert path.is_symlink()
