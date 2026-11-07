"""Retention preserves recoverable bytes; working-set size is not a run gate."""
import json
import os
import time

import pytest

import evidence_archive as archive
import evidence_retention as retention


def closed(tmp_path, monkeypatch):
    root = tmp_path/'workspace'
    path = root/'artifacts/runs/browser-closed'
    path.mkdir(parents=True)
    (path/'evidence.txt').write_text('Unique failure and recovery evidence.\n')
    os.chmod(path/'evidence.txt', 0o600)
    old = time.time()-7200
    os.utime(path, (old, old))
    storage = tmp_path/'archives'
    storage.mkdir(mode=0o700)
    monkeypatch.setattr(retention, 'referenced', lambda p:False)
    return root, path, storage


def test_copy_metadata_restore_and_lookup_precede_matching_original_prune(tmp_path, monkeypatch):
    root, path, storage = closed(tmp_path, monkeypatch)
    original = (path/'evidence.txt').read_bytes()
    info = (path/'evidence.txt').stat()
    result = retention.archive_closed(root, path, storage)
    assert result['independent_restore_verified'] and result['originals_removed']
    assert not path.exists()
    found = archive.lookup(root, 'artifacts/runs/browser-closed/evidence.txt')
    from pathlib import Path
    copy = Path(found['path'])
    assert copy.read_bytes() == original and copy.stat().st_mtime_ns == info.st_mtime_ns
    proof = json.loads((storage/result['archive_id']/'retention-proof.json').read_text())
    assert proof['independent_restore_verified'] and proof['original_paths_verified'] == 1


def test_failed_independent_restore_preserves_originals_and_verified_archive(tmp_path, monkeypatch):
    root, path, storage = closed(tmp_path, monkeypatch)

    def fail(*args):
        raise ValueError('Injected restoration failure')

    monkeypatch.setattr(archive, 'restore', fail)
    with pytest.raises(ValueError, match='Injected'):
        retention.archive_closed(root, path, storage)
    assert (path/'evidence.txt').exists()
    assert list(storage.glob('auto-*/manifest.json'))


def test_active_browser_and_runtime_history_are_not_retention_candidates(tmp_path, monkeypatch):
    root, path, storage = closed(tmp_path, monkeypatch)
    monkeypatch.setattr(retention, 'referenced', lambda p:True)
    with pytest.raises(ValueError):
        retention.archive_closed(root, path, storage)
    assert (path/'evidence.txt').exists()
    monkeypatch.setattr(retention, 'referenced', lambda p:False)
    assert not retention.closed_owned(root, root/'artifacts/runs/task-history')


def test_large_working_set_is_monitored_without_reinstating_campaign_cap(tmp_path, monkeypatch):
    root = tmp_path/'workspace'
    root.mkdir()
    storage = tmp_path/'archives'
    monkeypatch.setattr(retention, 'artifact_bytes', lambda root:retention.SOFT_TRIGGER+1)
    result = retention.maintain(root, storage)
    assert result['counted_bytes'] > retention.SOFT_TRIGGER
    assert result['cumulative_delivery_cap'] is False
