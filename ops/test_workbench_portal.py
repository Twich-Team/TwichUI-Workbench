"""Exercise the exact portal exception and interrupted cleanup without privileges."""

import ast
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from ops import workbench_admin as admin


@pytest.fixture
def host(tmp_path, monkeypatch):
    state, system = tmp_path / 'state', tmp_path / 'system'
    state.mkdir(mode=0o700)
    system.mkdir()
    monkeypatch.setattr(admin, 'STATE', state)
    monkeypatch.setattr(admin, 'SYSTEM', system)
    checked = admin.checked_file
    monkeypatch.setattr(admin, 'checked_file', lambda p, cap, **kw: checked(p, cap, owner=os.getuid(),
                                                                          private=kw.get('private', False)))
    monkeypatch.setattr(admin, 'protected_dir', lambda p: None)
    monkeypatch.setattr(admin, 'check_lan_address', lambda: None)
    release = 'everyday-portal-test'
    (system / admin.unit_name('lan')).write_bytes(admin.unit_bytes('lan', release))
    (system / admin.unit_name('lan')).chmod(0o644)
    env = SimpleNamespace(active='inactive', loaded=False, rules=[admin.FIREWALL_LINE + ' # ' + admin.FIREWALL_TAG],
                          calls=[], after=lambda *_: None, before=lambda *_: None)
    drop = system / admin.PORTAL_DROP

    def show(unit):
        assert unit == admin.unit_name('lan')
        return dict(LoadState='loaded', Names=unit, FragmentPath=str(system / unit),
                    DropInPaths=str(drop) if env.loaded else '', User='rich', Group='rich',
                    NeedDaemonReload='yes' if drop.exists() != env.loaded else 'no',
                    ActiveState=env.active, UnitFileState='enabled')

    def command(*args, **_kw):
        env.before(args)
        env.calls.append(args)
        if args == (*admin.SYSTEMCTL, 'daemon-reload'):
            env.loaded = drop.exists()
        elif args == (admin.UFW, 'allow', *admin.PORTAL_FIREWALL, 'comment', admin.PORTAL_TAG):
            env.rules.append(admin.PORTAL_LINE + ' # ' + admin.PORTAL_TAG)
        elif args == (admin.UFW, 'delete', 'allow', *admin.PORTAL_FIREWALL):
            env.rules.remove(admin.PORTAL_LINE + ' # ' + admin.PORTAL_TAG)
        else:
            raise AssertionError('Unexpected privileged command: ' + repr(args))
        env.after(args)
        return ''

    monkeypatch.setattr(admin, 'show', show)
    monkeypatch.setattr(admin, 'command', command)
    monkeypatch.setattr(admin, 'firewall_status', lambda: 'Status: active\n'
                        'Default: deny (incoming), allow (outgoing), disabled (routed)\n'
                        + '\n'.join(env.rules))
    return env


def test_exact_add_repeat_remove_preserves_original_rule_and_unit(host):
    original = (admin.SYSTEM / admin.unit_name('lan')).read_bytes()
    assert 'exception present' in admin.portal('add')
    assert (admin.SYSTEM / admin.PORTAL_DROP).read_bytes() == b'[Service]\nIPAddressAllow=10.0.4.3/32\n'
    assert admin.portal_state() == b'added\n'
    assert admin.lan_rule() == admin.portal_rule() == 'tagged'
    admin.portal('add')
    assert sum(args[0] == admin.UFW for args in host.calls) == 1
    with pytest.raises(admin.Refused, match='Remove the portal policy'):
        admin.firewall('remove')
    admin.portal('remove')
    assert host.rules == [admin.FIREWALL_LINE + ' # ' + admin.FIREWALL_TAG]
    assert not (admin.SYSTEM / admin.PORTAL_DROP).exists()
    assert not admin.portal_state()
    assert (admin.SYSTEM / admin.unit_name('lan')).read_bytes() == original
    assert 'No portal policy' in admin.portal('remove')


@pytest.mark.parametrize('action', ['add', 'remove'])
def test_listener_must_be_stopped_before_portal_mutation(host, action):
    host.active = 'active'
    with pytest.raises(admin.Refused, match='Stop the LAN listener'):
        admin.portal(action)
    assert not host.calls
    assert not admin.portal_state()


@pytest.mark.parametrize('rule', [
    admin.PORTAL_LINE,
    admin.PORTAL_LINE + ' # ' + admin.PORTAL_TAG,
    admin.PORTAL_LINE.replace('10.0.4.3', '10.0.4.0/24') + ' # ' + admin.PORTAL_TAG,
    admin.PORTAL_LINE.replace('enp4s0', 'ens18') + ' # ' + admin.PORTAL_TAG,
    admin.PORTAL_LINE.replace('10.0.0.9', 'Anywhere') + ' # ' + admin.PORTAL_TAG,
    '9487/tcp ALLOW IN Anywhere',
])
def test_external_or_broader_rule_is_preserved_and_refused(host, rule):
    host.rules.append(rule)
    before = host.rules.copy()
    with pytest.raises(admin.Refused, match='conflicting'):
        admin.portal('add')
    assert before == host.rules
    assert not host.calls
    assert not admin.portal_state()


def test_duplicate_owned_rule_is_refused(host):
    admin.portal('add')
    host.rules.append(admin.PORTAL_LINE + ' # ' + admin.PORTAL_TAG)
    with pytest.raises(admin.Refused, match='duplicate'):
        admin.portal('remove')


@pytest.mark.parametrize('point', ['state', 'drop', 'reload', 'rule', 'complete'])
def test_add_reconciles_each_interrupted_effect(host, monkeypatch, point):
    original = admin.atomic_write

    def write(path, data, *args):
        original(path, data, *args)
        if ((point == 'state' and path == admin.STATE / 'portal' and data == b'adding\n')
                or (point == 'drop' and path == admin.SYSTEM / admin.PORTAL_DROP)
                or (point == 'complete' and data == b'added\n')):
            raise InterruptedError('Injected after durable write')

    def after(args):
        if ((point == 'reload' and args[0] == admin.SYSTEMCTL[0])
                or (point == 'rule' and args[0] == admin.UFW)):
            raise InterruptedError('Injected after command effect')

    monkeypatch.setattr(admin, 'atomic_write', write)
    host.after = after
    with pytest.raises(InterruptedError):
        admin.portal('add')
    monkeypatch.setattr(admin, 'atomic_write', original)
    host.after = lambda *_: None
    admin.portal('add')
    assert admin.portal_state() == b'added\n'
    assert host.rules.count(admin.PORTAL_LINE + ' # ' + admin.PORTAL_TAG) == 1
    assert host.loaded
    admin.portal('remove')


@pytest.mark.parametrize('point', ['state', 'rule', 'drop', 'reload'])
def test_remove_reconciles_each_interrupted_effect(host, monkeypatch, point):
    admin.portal('add')
    original = admin.atomic_write

    def write(path, data, *args):
        original(path, data, *args)
        if point == 'state' and data == b'removing\n':
            raise InterruptedError('Injected after ownership write')

    def before(args):
        if point == 'drop' and args[0] == admin.SYSTEMCTL[0]:
            raise InterruptedError('Injected after drop removal, before reload')

    def after(args):
        if ((point == 'rule' and args[0] == admin.UFW)
                or (point == 'reload' and args[0] == admin.SYSTEMCTL[0])):
            raise InterruptedError('Injected after command effect')

    monkeypatch.setattr(admin, 'atomic_write', write)
    host.before, host.after = before, after
    with pytest.raises(InterruptedError):
        admin.portal('remove')
    monkeypatch.setattr(admin, 'atomic_write', original)
    host.before = host.after = lambda *_: None
    admin.portal('remove')
    assert not admin.portal_state()
    assert not host.loaded
    assert host.rules == [admin.FIREWALL_LINE + ' # ' + admin.FIREWALL_TAG]


def test_changed_or_orphan_dropin_is_never_overwritten(host):
    path = admin.SYSTEM / admin.PORTAL_DROP
    path.parent.mkdir()
    path.write_bytes(admin.PORTAL_BYTES)
    path.chmod(0o644)
    for owned in (False, True):
        if owned:
            (admin.STATE / 'portal').write_bytes(b'added\n')
            (admin.STATE / 'portal').chmod(0o600)
            path.write_bytes(b'[Service]\nIPAddressAllow=10.0.4.0/24\n')
        before = path.read_bytes()
        with pytest.raises(admin.Refused, match='not owned or differs'):
            admin.portal('add')
        assert path.read_bytes() == before
        assert not host.calls


def test_recovery_can_close_but_never_open_portal():
    admin.recovery_request(admin.validate(dict(op='portal', action='remove')))
    with pytest.raises(admin.Refused, match='only closes'):
        admin.recovery_request(admin.validate(dict(op='portal', action='add')))
    for extra in ({'source': '10.0.4.0/24'}, {'release': 'everyday-test'}, {'action': 'restart'}):
        with pytest.raises(admin.Refused):
            admin.validate(dict(op='portal', action='add') | extra)


@pytest.mark.parametrize('phase', ['adding', 'removing', 'missing-rule', 'missing-drop'])
def test_lan_cannot_start_with_incomplete_portal_policy(host, monkeypatch, phase):
    admin.portal('add')
    if phase in {'adding', 'removing'}:
        (admin.STATE / 'portal').write_bytes((phase + '\n').encode())
    elif phase == 'missing-rule':
        host.rules.remove(admin.PORTAL_LINE + ' # ' + admin.PORTAL_TAG)
    else:
        (admin.SYSTEM / admin.PORTAL_DROP).unlink()
        host.loaded = False
    monkeypatch.setattr(admin, 'check_release_peers', lambda *_: None)
    before = host.calls.copy()
    with pytest.raises(admin.Refused, match='Reconcile the portal policy'):
        admin.service('start', 'lan')
    assert host.calls == before


def test_fixed_dropin_parses_with_the_original_lan_unit(tmp_path):
    import subprocess

    unit = tmp_path / admin.unit_name('lan')
    unit.write_bytes(admin.unit_bytes('lan', admin.ROLLBACK.name))
    folder = tmp_path / (unit.name + '.d')
    folder.mkdir()
    (folder / '30-portal.conf').write_bytes(admin.PORTAL_BYTES)
    result = subprocess.run(['/usr/bin/systemd-analyze', 'verify', str(unit)],
                            capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr.decode()


def test_privileged_admission_and_all_base_unit_templates_unchanged():
    # The root-owned original is readable; never execute or import it here.
    installed = Path('/usr/local/sbin/twichui-workbench-admin')
    if not installed.exists():
        pytest.skip('Installed baseline is host-specific')
    baseline = ast.parse(installed.read_text())
    candidate = ast.parse(Path(admin.__file__).read_text())
    old = {node.name: ast.dump(node) for node in baseline.body if isinstance(node, ast.FunctionDef)}
    new = {node.name: ast.dump(node) for node in candidate.body if isinstance(node, ast.FunctionDef)}
    for name in ('privileged_main', 'check_grant', 'checked_file', 'protected_dir', 'audit',
                 'run', 'unit_bytes', 'require_settled', 'check_lan_address'):
        assert old[name] == new[name], name
