"""Managed test scratch propagates to child processes and checks file entries."""
import json
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from pathlib import Path

import pytest
import wb


def test_child_process_uses_private_project_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(wb, 'ROOT', tmp_path)
    monkeypatch.setattr(wb, 'TEST_CACHE', tmp_path / 'cache')
    monkeypatch.setattr(wb, 'environment', lambda: {'PATH': '/usr/bin:/bin'})
    env = wb.test_environment()
    storage = Path(env['TMPDIR'])
    assert storage.stat().st_mode & 0o077 == 0
    child = subprocess.run([sys.executable, '-c',
        'import tempfile,json;print(json.dumps(dict(root=tempfile.gettempdir(),created=tempfile.mkdtemp())))'],
        env=env, capture_output=True, text=True, check=True, timeout=10)
    value = json.loads(child.stdout)
    assert Path(value['root']) == storage
    assert Path(value['created']).parent == storage


def test_inode_pressure_holds_before_another_run(tmp_path, monkeypatch):
    monkeypatch.setattr(wb, 'environment', lambda: {'TMPDIR': str(tmp_path)})
    monkeypatch.setattr(wb.os, 'statvfs', lambda _: SimpleNamespace(f_files=1000000, f_favail=3))
    with pytest.raises(RuntimeError, match='file entries'):
        wb.test_environment()


def test_explicit_temporary_storage_is_preserved(tmp_path, monkeypatch):
    monkeypatch.setattr(wb, 'environment', lambda: {'TMPDIR': str(tmp_path)})
    assert wb.test_environment()['TMPDIR'] == str(tmp_path)


def test_default_storage_refuses_a_link_before_writing_outside(tmp_path, monkeypatch):
    root = tmp_path / 'project'
    root.mkdir()
    outside = tmp_path / 'not-created'
    cache = root / 'cache'
    cache.symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(wb, 'ROOT', root)
    monkeypatch.setattr(wb, 'TEST_CACHE', cache)
    monkeypatch.setattr(wb, 'environment', lambda: {'PATH': '/usr/bin:/bin'})
    with pytest.raises(RuntimeError, match='private owned'):
        wb.test_environment()
    assert not outside.exists()


def test_fixture_scratch_and_child_scratch_stay_in_the_managed_test(tmp_path):
    assert Path(tempfile.mkdtemp()).parent == tmp_path
    child = subprocess.run([sys.executable, '-c', 'import tempfile;print(tempfile.mkdtemp())'],
                           capture_output=True, text=True, check=True, timeout=10)
    assert Path(child.stdout.strip()).parent == tmp_path
