"""Immutable release checks distinguish settled private runtime from new code."""
import json
import runpy
from pathlib import Path

import pytest

SEAL = runpy.run_path(str(Path(__file__).resolve().parents[2] / 'ops/acceptance_seal.py'))
RUN = 'task-lease-task-session-003b2b6d-e130-475e-8aba-1ad6de1f691c-01'


def fixture(tmp_path):
    stage = tmp_path / 'stage'
    local = stage / 'release/.local'
    local.mkdir(parents=True, mode=0o700)
    (stage / 'release/safe.py').write_text('# immutable application\n')
    (stage / 'release/safe.py').chmod(0o600)
    baseline = SEAL['inventory'](stage, True)
    run = stage / 'release/artifacts/runs' / RUN
    run.mkdir(parents=True, mode=0o700)
    lease = local / ('chronicle-primary-lease-' + RUN)
    lease.mkdir(mode=0o700)
    state = {k: True for k in ['restored', 'original_config_verified',
                              'original_access_verified', 'temporary_access_rejected']}
    for name in SEAL['LEASE_FILES']:
        value = json.dumps(state) if name == 'state.json' else '' if name == 'restore.lock' else 'CONTROLLED NON-SECRET FIXTURE'
        if name == 'restoration-error.json':
            value = json.dumps(dict(error='TimeoutExpired', state=SEAL['RESTORATION_ERROR_STATE']))
        p = lease / name
        p.write_text(value)
        p.chmod(0o600)
    for name in SEAL['PRIMARY_FILES']:
        p = stage / name
        p.write_text(json.dumps({'state': 'released'}) if name.endswith('/real-primary-session.json') else '')
        p.chmod(0o600)
    return stage, lease, baseline


def test_private_settled_data_does_not_change_immutable_inventory(tmp_path, monkeypatch):
    stage, lease, baseline = fixture(tmp_path)
    read = Path.read_text

    def guarded(path, *args, **kwargs):
        assert path.name not in {'original.key', 'temporary.key', 'serve.env.original', 'serve.env.temporary'}
        return read(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', guarded)
    assert SEAL['inventory'](stage, True) == baseline
    with pytest.raises(ValueError, match='Dormant'):
        SEAL['inventory'](stage, False)


@pytest.mark.parametrize('fault', ['extra', 'symlink', 'public', 'large', 'unsettled', 'session', 'owner-run', 'run-link', 'diagnostic-payload', 'diagnostic-public'])
def test_runtime_classification_rejects_untrustworthy_data(tmp_path, fault):
    stage, lease, _ = fixture(tmp_path)
    if fault == 'extra':
        (lease / 'arbitrary.py').write_text('not runtime')
    elif fault == 'symlink':
        p = lease / 'temporary.key'
        p.unlink()
        p.symlink_to(lease / 'original.key')
    elif fault == 'public':
        (lease / 'temporary.key').chmod(0o644)
    elif fault == 'large':
        (lease / 'temporary.key').write_bytes(b'x' * 4097)
    elif fault == 'unsettled':
        (lease / 'state.json').write_text(json.dumps({'restored': False}))
    elif fault == 'session':
        (stage / 'release/.local/real-primary-session.json').write_text(json.dumps({'state': 'unknown'}))
    elif fault == 'owner-run':
        (stage / 'release/artifacts/runs' / RUN).rmdir()
    elif fault == 'diagnostic-payload':
        (lease/'restoration-error.json').write_text(json.dumps(dict(error='TimeoutExpired',state=SEAL['RESTORATION_ERROR_STATE'],unexpected='untrusted payload')))
    elif fault == 'diagnostic-public':
        (lease/'restoration-error.json').chmod(0o644)
    else:
        run = stage / 'release/artifacts/runs' / RUN
        run.rmdir()
        run.symlink_to(stage, target_is_directory=True)
    with pytest.raises(ValueError):
        SEAL['inventory'](stage, True)


def test_runtime_exception_does_not_hide_modified_or_added_application(tmp_path):
    stage, _, baseline = fixture(tmp_path)
    (stage / 'release/safe.py').write_text('changed application')
    assert SEAL['inventory'](stage, True) != baseline
    (stage / 'release/new.py').write_text('unexpected application')
    (stage / 'release/new.py').chmod(0o600)
    assert 'release/new.py' in SEAL['inventory'](stage, True)[0]
