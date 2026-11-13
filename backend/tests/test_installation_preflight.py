"""Actual installer entry point and sealed release fixtures; no live service calls."""
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tarfile

import pytest

from everyday_store import fingerprint
from installation_preflight import CONFIG_FILES, cleanup_disposable
from release_bundle import build

ROOT = Path(__file__).resolve().parents[2]
ENTRY = ROOT / 'scripts/installation_preflight.py'


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(value))
    path.chmod(0o600)


@pytest.fixture
def installation(tmp_path, store):
    source = tmp_path / 'source'
    source.mkdir(mode=0o700)
    (source / 'ops').mkdir()
    shutil.copyfile(ROOT / 'ops/acceptance_seal.py', source / 'ops/acceptance_seal.py')
    for name in ['requirements.lock', 'package-lock.json']:
        (source / name).write_text('Controlled dependency fixture')
    for argv in [['init', '-q'], ['add', '.'], ['-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                                           'commit', '-qm', 'Controlled application']]:
        subprocess.run(['git', *argv], cwd=source, check=True)
    (source / 'frontend/dist').mkdir(parents=True)
    (source / 'frontend/dist/index.html').write_text('Checked frontend')
    artifact = tmp_path / 'application.tar.gz'
    application = build(source, artifact)
    stages = [tmp_path / 'candidate', tmp_path / 'previous']
    for stage in stages:
        stage.mkdir(mode=0o700)
        (stage / 'release').mkdir()
        (stage / 'ops').mkdir()
        (stage / 'artifacts').mkdir()
        with tarfile.open(artifact) as archive:
            archive.extractall(stage / 'release', filter='data')
        subprocess.run([sys.executable, str(ROOT / 'ops/acceptance_seal.py'), 'create', str(stage)], check=True,
                       stdout=subprocess.DEVNULL)
        db = stage / 'artifacts/workbench.sqlite3'
        with sqlite3.connect(store.url.database) as original, sqlite3.connect(db) as target:
            original.backup(target)
        db.chmod(0o600)
        for name in CONFIG_FILES - {'artifacts/draft-authority.json'}:
            save(stage / name, {})
        save(stage / 'artifacts/draft-authority.json', dict(database_identity=[db.stat().st_dev, db.stat().st_ino],
             owner_instruction_sha256='c' * 64, automatic_since='2026-11-11T02:00:52+00:00'))
    stage, previous = stages
    with sqlite3.connect(stage / 'artifacts/workbench.sqlite3') as c:
        baseline = fingerprint(c)
    acceptance = tmp_path / 'acceptance.json'
    save(acceptance, dict(commit=application['commit'], full_release_acceptance=True,
                         application_sha256=application['sha256']))
    config = tmp_path / 'installation-config.json'
    save(config, dict(candidate=application['commit'], schema=baseline['schema'],
                     dependencies=[str(stage / p) for p in CONFIG_FILES]))
    rejection = tmp_path / 'rejected-evidence-manifest.json'
    raw = b'Original rejected evidence is still evidence.'
    for tree in stages:
        path = tree / 'artifacts/acceptance/rejected-results/retained.json'
        path.parent.mkdir(parents=True)
        path.write_bytes(raw)
    save(rejection, dict(source_stage=str(previous), execution='controlled',
                         files=[dict(name='retained.json', bytes=len(raw), sha256=sha256(raw).hexdigest())]))
    activation = tmp_path / 'install.sh'
    activation.write_text('echo activated >> "'+str(tmp_path / 'events')+'"\n')
    activation.chmod(0o600)
    rollback = tmp_path / 'rollback.sh'
    rollback.write_text('echo rolled_back >> "'+str(tmp_path / 'events')+'"\n')
    rollback.chmod(0o600)
    files = {str(stage / p): 'configuration' for p in CONFIG_FILES}
    files.update({str(activation): 'installer', str(rollback): 'rollback',
                  str(previous / 'artifacts/workbench.sqlite3'): 'rollback',
                  str(stage / 'artifacts/workbench.sqlite3'): 'history',
                  str(rejection): 'evidence_manifest', str(config): 'configuration',
                  str(acceptance): 'runtime'})
    manifest = dict(version=1, commit=application['commit'], application=str(artifact),
        application_sha256=application['sha256'], acceptance=str(acceptance),
        release=str(stage / 'release'), configuration=str(config),
        store=str(stage / 'artifacts/workbench.sqlite3'), history=baseline,
        rollback_store=str(previous / 'artifacts/workbench.sqlite3'), rollback_stage=str(previous),
        python=sys.executable, seal_script=str(stage / 'release/ops/acceptance_seal.py'),
        activation_script=str(activation), rollback_script=str(rollback), rejection_manifest=str(rejection),
        evidence_roots=[str(previous / 'artifacts')],
        external_files=[dict(path=p, resolved=str(Path(p).resolve()), sha256=sha256(Path(p).read_bytes()).hexdigest())
                        for p in [sys.executable, '/usr/bin/bash']],
        files=[dict(path=p, role=role, sha256=sha256(Path(p).read_bytes()).hexdigest()) for p, role in files.items()])
    envelope = tmp_path / 'envelope.json'
    save(envelope, manifest)
    return envelope, manifest, tmp_path


def invoke(envelope, operation='activate'):
    return subprocess.run([sys.executable, str(ENTRY), operation, str(envelope)],
                          capture_output=True, text=True, timeout=20,
                          env={**os.environ, 'PYTHONPATH':str(ROOT / 'backend') + ':' + str(ROOT / 'scripts')})


@pytest.mark.parametrize('fault', ['missing_manifest', 'wrong_digest', 'wrong_revision', 'missing_config',
                                   'unlisted_config', 'rollback', 'configuration_schema', 'changed_application'])
def test_incomplete_package_refuses_before_any_service_operation(installation, fault):
    envelope, value, root = installation
    if fault == 'missing_manifest':
        Path(value['rejection_manifest']).unlink()
    elif fault == 'wrong_digest':
        Path(value['application']).write_bytes(b'Altered application')
    elif fault == 'wrong_revision':
        value['commit'] = 'a' * 40
    elif fault == 'missing_config':
        (Path(value['release']).parent / 'ops/worker.env').unlink()
    elif fault == 'unlisted_config':
        value['files'] = [e for e in value['files'] if not e['path'].endswith('/ops/worker.env')]
    elif fault == 'rollback':
        Path(value['rollback_store']).unlink()
    elif fault == 'configuration_schema':
        config = json.loads(Path(value['configuration']).read_text())
        config['schema'] = 'unsupported'
        save(Path(value['configuration']), config)
        for item in value['files']:
            if item['path'] == value['configuration']:
                item['sha256'] = sha256(Path(item['path']).read_bytes()).hexdigest()
    else:
        (Path(value['release']) / 'frontend/dist/index.html').write_text('Rebuilt after acceptance')
    save(envelope, value)
    result = invoke(envelope)
    assert result.returncode != 0
    assert not (root / 'events').exists(), result.stderr
    assert not (root / 'installation-journal.json').exists()


def test_success_retains_exact_history_evidence_and_permissions(installation):
    envelope, value, root = installation
    result = invoke(envelope)
    assert result.returncode == 0, result.stderr
    assert (root / 'events').read_text().splitlines() == ['activated']
    assert json.loads((root / 'installation-journal.json').read_text())['state'] == 'activated'
    with sqlite3.connect(value['store']) as c:
        assert fingerprint(c) == value['history']
    assert invoke(envelope).returncode != 0  # No implicit repeat activation.
    assert invoke(envelope, 'rollback').returncode == 0
    assert (root / 'events').read_text().splitlines() == ['activated', 'rolled_back']
    assert (root / 'previous/artifacts/acceptance/rejected-results/retained.json').read_bytes() == (
        root / 'candidate/artifacts/acceptance/rejected-results/retained.json').read_bytes()


def test_killed_installer_retains_verified_rollback(installation):
    envelope, value, root = installation
    script = Path(value['activation_script'])
    script.write_text('echo interrupted >> "'+str(root / 'events')+'"\nkill -KILL "$PPID"\n')
    for item in value['files']:
        if item['path'] == str(script):
            item['sha256'] = sha256(script.read_bytes()).hexdigest()
    save(envelope, value)
    assert invoke(envelope).returncode < 0
    assert json.loads((root / 'installation-journal.json').read_text())['state'] == 'activating'
    assert invoke(envelope).returncode != 0
    assert invoke(envelope, 'rollback').returncode == 0
    assert (root / 'events').read_text().splitlines() == ['interrupted', 'rolled_back']


def test_cleanup_preserves_referenced_evidence_and_removes_only_owned_disposable(tmp_path, monkeypatch):
    scratch = tmp_path / 'scratch'
    scratch.mkdir(mode=0o700)
    evidence = scratch / 'historical-evidence'
    evidence.mkdir()
    (evidence / 'record').write_text('Preserve')
    save(scratch / '.disposable-installation.json', dict(purpose='installation-rehearsal', evidence_retained=True))
    with pytest.raises(ValueError, match='referenced evidence'):
        cleanup_disposable(scratch, [str(evidence)])
    assert (evidence / 'record').read_text() == 'Preserve'
    retained = tmp_path / 'retained'
    evidence.rename(retained)
    # The host may deny inspection of another same-user process: that must hold cleanup.
    import evidence_retention
    monkeypatch.setattr(evidence_retention, 'referenced', lambda _: False)
    cleanup_disposable(scratch, [str(retained)])
    assert not scratch.exists() and (retained / 'record').read_text() == 'Preserve'
