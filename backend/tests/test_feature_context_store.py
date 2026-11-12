"""Installation copying must retain drafts and reject altered private evidence."""

import importlib.util
import os
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('feature_context_store', Path(__file__).parents[2] / 'scripts/feature_context_store.py')
store = importlib.util.module_from_spec(spec)
spec.loader.exec_module(store)


def roots(tmp_path):
    source, target = tmp_path / 'source', tmp_path / 'target'
    source.mkdir(mode=0o700)
    target.mkdir(mode=0o700)
    draft = source / 'feature-context' / 'attachments' / 'unsubmitted-draft'
    draft.mkdir(parents=True, mode=0o700)
    draft.parent.chmod(0o700)
    draft.parent.parent.chmod(0o700)
    file = draft / 'owner-note.txt'
    file.write_bytes(b'Original owner note, not an instruction to execute.\n')
    file.chmod(0o600)
    return source, target, file


def test_exact_copy_preserves_unsubmitted_drafts_and_is_idempotent(tmp_path):
    source, target, file = roots(tmp_path)
    first = store.transfer(source, target)
    assert first['exact_copy'] and first['files'] == 1
    assert first == store.transfer(source, target)
    copied = target / file.relative_to(source)
    assert copied.read_bytes() == file.read_bytes()
    assert copied.stat().st_ino != file.stat().st_ino
    assert copied.stat().st_mode & 0o077 == 0
    assert store.snapshot(source / 'feature-context') == store.snapshot(target / 'feature-context')


@pytest.mark.parametrize('change', ['symlink', 'hardlink', 'public'])
def test_invalid_source_is_rejected_before_destination_creation(tmp_path, change):
    source, target, file = roots(tmp_path)
    if change == 'symlink':
        file.with_suffix('.link').symlink_to(file)
    elif change == 'hardlink':
        os.link(file, file.with_suffix('.link'))
    else:
        file.chmod(0o644)
    with pytest.raises(ValueError):
        store.transfer(source, target)
    assert not (target / 'feature-context').exists()


def test_existing_different_evidence_is_never_overwritten(tmp_path):
    source, target, file = roots(tmp_path)
    store.transfer(source, target)
    copied = target / file.relative_to(source)
    copied.write_bytes(b'Changed evidence')
    with pytest.raises(ValueError, match='different immutable'):
        store.transfer(source, target)
    assert copied.read_bytes() == b'Changed evidence'


def test_empty_old_installation_and_overlapping_roots(tmp_path):
    source, target = tmp_path / 'source', tmp_path / 'target'
    source.mkdir(mode=0o700)
    target.mkdir(mode=0o700)
    assert store.transfer(source, target)['source_absent']
    with pytest.raises(ValueError, match='separate'):
        store.transfer(source, source)
