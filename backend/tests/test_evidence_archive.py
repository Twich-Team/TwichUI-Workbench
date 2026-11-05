"""Lossless closed-tree preservation, including failures across destructive boundaries."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

import evidence_archive as ea


@pytest.fixture
def evidence(tmp_path):
    root = tmp_path / 'workspace'
    storage = tmp_path / 'archives'
    source = 'artifacts/runs/closed/generated'
    directory = root / source
    (directory / 'empty').mkdir(parents=True)
    (directory / 'one.bin').write_bytes(bytes(range(256)) * 100)
    (directory / 'two.json').write_text('{"failure": "retained", "name": "雪"}\n')
    (directory / 'one.bin').chmod(0o640)
    original = ea.inventory(root, [source])
    return root, storage, source, original


def test_archive_lookup_restore_and_repeated_execution_are_lossless(evidence, tmp_path):
    root, storage, source, original = evidence
    first = ea.archive(root, storage, 'closed', [source], remove=True)
    assert not (root / source).exists()
    assert first['files'] == 2
    assert ea.archive(root, storage, 'closed', [source], remove=True) == first
    found = ea.lookup(root, source + '/one.bin')
    assert Path(found['path']).read_bytes() == bytes(range(256)) * 100
    destination = tmp_path / 'restored'
    ea.restore(root, 'closed', destination)
    ea.restore(root, 'closed', destination)
    assert ea.inventory(destination, [source]) == original
    ea.restore(root, 'closed', root)
    assert ea.inventory(root, [source]) == original


def test_interrupted_copy_preserves_sources_and_can_resume(evidence, monkeypatch):
    root, storage, source, original = evidence
    copy = ea.shutil.copyfileobj

    def interrupt(input_stream, output):
        output.write(input_stream.read(10))
        raise KeyboardInterrupt

    monkeypatch.setattr(ea.shutil, 'copyfileobj', interrupt)
    with pytest.raises(KeyboardInterrupt):
        ea.archive(root, storage, 'closed', [source], remove=True)
    assert ea.inventory(root, [source]) == original
    assert not (root / ea.INDEX / 'closed.json').exists()
    assert len(list((storage / 'closed/partial').iterdir())) == 1
    monkeypatch.setattr(ea.shutil, 'copyfileobj', copy)
    ea.archive(root, storage, 'closed', [source], remove=True)
    bundle, manifest = ea.load(root, 'closed')
    ea.verify(bundle / 'data', manifest)


def test_interrupted_removal_has_durable_lookup_and_resumes(evidence, monkeypatch, tmp_path):
    root, storage, source, original = evidence
    unlink = Path.unlink
    deleted = []

    def interrupt(path, *args, **kwargs):
        if path.is_relative_to(root / source):
            if deleted:
                raise KeyboardInterrupt
            deleted.append(str(path))
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', interrupt)
    with pytest.raises(KeyboardInterrupt):
        ea.archive(root, storage, 'closed', [source], remove=True)
    assert len(deleted) == 1
    assert ea.lookup(root, deleted[0][len(str(root)) + 1:])['sha256']
    destination = tmp_path / 'restored'
    ea.restore(root, 'closed', destination)
    assert ea.inventory(destination, [source]) == original
    monkeypatch.setattr(Path, 'unlink', unlink)
    ea.archive(root, storage, 'closed', [source], remove=True)
    assert not (root / source).exists()


@pytest.mark.parametrize('boundary', ['copy', 'remove'])
def test_killed_process_recovery(evidence, tmp_path, boundary):
    root, storage, source, original = evidence
    marker = tmp_path / 'boundary'
    program = '''
import sys,time
from pathlib import Path
import evidence_archive as ea
root,storage,source,marker,boundary=sys.argv[1:]
root,storage,marker=Path(root),Path(storage),Path(marker)
if boundary == 'copy':
    def copy(input_stream, output):
        output.write(input_stream.read(10)); output.flush()
        marker.write_text('partial copy'); time.sleep(30)
    ea.shutil.copyfileobj=copy
else:
    unlink=Path.unlink
    def remove(path,*args,**kwargs):
        result=unlink(path,*args,**kwargs)
        if path.is_relative_to(root/source):
            marker.write_text('original removed'); time.sleep(30)
        return result
    Path.unlink=remove
ea.archive(root,storage,'closed',[source],remove=True)
'''
    env = dict(os.environ, PYTHONPATH=str(Path(ea.__file__).parent))
    process = subprocess.Popen([sys.executable, '-c', program, str(root), str(storage),
                                source, str(marker), boundary], env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 5
        while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.01)
        assert marker.exists(), process.communicate(timeout=1)
        process.kill()  # Only this test's disposable child, at the chosen boundary.
        process.communicate(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
    if boundary == 'copy':
        assert ea.inventory(root, [source]) == original
    else:
        assert ea.lookup(root, source + '/one.bin')['sha256']
    ea.archive(root, storage, 'closed', [source], remove=True)
    destination = tmp_path / 'restored'
    ea.restore(root, 'closed', destination)
    assert ea.inventory(destination, [source]) == original


@pytest.mark.parametrize('change', ['changed', 'new', 'symlink'])
def test_changed_source_refuses_pruning_without_deleting_anything(evidence, change):
    root, storage, source, _ = evidence
    ea.archive(root, storage, 'closed', [source])
    file = root / source / 'one.bin'
    if change == 'changed':
        file.write_bytes(b'changed')
    elif change == 'new':
        (root / source / 'new').write_bytes(b'new evidence')
    else:
        file.unlink()
        file.symlink_to('/nonexistent')
    with pytest.raises(ValueError):
        ea.archive(root, storage, 'closed', [source], remove=True)
    assert (root / source / 'two.json').exists()
    assert file.exists() or file.is_symlink()


def test_corrupt_archive_refuses_pruning_lookup_and_restore(evidence, tmp_path):
    root, storage, source, original = evidence
    ea.archive(root, storage, 'closed', [source])
    (storage / 'closed/data' / source / 'one.bin').write_bytes(b'corruption')
    for action in (
        lambda: ea.archive(root, storage, 'closed', [source], remove=True),
        lambda: ea.lookup(root, source + '/one.bin'),
        lambda: ea.restore(root, 'closed', tmp_path / 'restore'),
    ):
        with pytest.raises(ValueError):
            action()
    assert ea.inventory(root, [source]) == original


def test_restore_refuses_overwrites_and_symlink_ancestors(evidence, tmp_path):
    root, storage, source, _ = evidence
    ea.archive(root, storage, 'closed', [source], remove=True)
    destination = tmp_path / 'restore'
    conflict = destination / source / 'one.bin'
    conflict.parent.mkdir(parents=True)
    conflict.write_bytes(b'unrelated work')
    with pytest.raises(ValueError):
        ea.restore(root, 'closed', destination)
    assert conflict.read_bytes() == b'unrelated work'
    linked = tmp_path / 'linked'
    linked.symlink_to(destination, target_is_directory=True)
    with pytest.raises(ValueError, match='Symlink'):
        ea.restore(root, 'closed', linked)


def test_manifest_tampering_and_archive_id_reuse_are_refused(evidence):
    root, storage, source, _ = evidence
    ea.archive(root, storage, 'closed', [source])
    with pytest.raises(ValueError, match='different sources'):
        ea.archive(root, storage, 'closed', ['artifacts/runs/other'])
    path = storage / 'closed/manifest.json'
    path.write_text(path.read_text() + ' ')
    with pytest.raises(ValueError, match='manifest hash'):
        ea.load(root, 'closed')


@pytest.mark.parametrize('source', ['.local', 'artifacts/runs', '../private', '/private',
                                    'artifacts/runs/closed/../other'])
def test_invalid_sources_are_refused_without_archiving(evidence, source):
    root, storage, _, _ = evidence
    with pytest.raises(ValueError):
        ea.archive(root, storage, 'closed', [source], remove=True)
    assert not storage.exists()


def test_restore_into_workspace_respects_counted_cap(evidence, monkeypatch):
    root, storage, source, _ = evidence
    ea.archive(root, storage, 'closed', [source], remove=True)
    monkeypatch.setattr(ea, 'ARTIFACT_LIMIT', 1)
    with pytest.raises(ValueError, match='counted evidence budget'):
        ea.restore(root, 'closed', root)
    assert not (root / source).exists()


def test_lock_rejects_another_archiver(evidence):
    root, _, _, _ = evidence
    with ea.locked(root):
        with pytest.raises(BlockingIOError):
            with ea.locked(root):
                pytest.fail('overlapping archive operation')


def test_unreadable_tree_does_not_produce_an_incomplete_manifest(evidence, monkeypatch):
    root, storage, source, _ = evidence

    def unreadable(*args, **kwargs):
        kwargs['onerror'](PermissionError('unreadable closed evidence'))
        yield  # Keep the same generator interface as os.walk.

    monkeypatch.setattr(ea.os, 'walk', unreadable)
    with pytest.raises(PermissionError):
        ea.archive(root, storage, 'closed', [source], remove=True)
    assert not (storage / 'closed/manifest.json').exists()
    assert (root / source / 'one.bin').exists()


def test_cli_archive_verify_lookup_restore_status(evidence, monkeypatch, capsys, tmp_path):
    root, storage, source, _ = evidence
    monkeypatch.setattr(ea, 'ROOT', root)
    monkeypatch.setattr(ea, 'ARCHIVES', storage)
    commands = [
        ['archive', 'closed', source, '--remove-originals'],
        ['verify', 'closed'], ['lookup', source + '/one.bin'],
        ['restore', 'closed', '--destination', str(tmp_path / 'restore')], ['status'],
    ]
    for command in commands:
        monkeypatch.setattr('sys.argv', ['evidence_archive.py', *command])
        ea.main()
        assert json.loads(capsys.readouterr().out)
