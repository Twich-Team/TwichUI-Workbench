from types import SimpleNamespace
import json
import os

import pytest

from workbench import storage_resources as storage, release_cache


def test_operation_counts_exclude_workspace_and_links_but_keep_hard_limits(tmp_path, monkeypatch):
    root = tmp_path / "evidence"
    root.mkdir()
    workspace = root / "workspace"
    workspace.mkdir()
    (root / "event").write_bytes(b"abc")
    (workspace / "source").write_bytes(b"12345")
    (root / "alias").symlink_to(workspace / "source")
    monkeypatch.setattr(
        storage.shutil, "disk_usage", lambda p: SimpleNamespace(free=storage.RECOVERY + 100)
    )
    measured = storage.require(root, workspace, 99)
    assert measured["evidence_bytes"] == 3 and measured["scratch_bytes"] == 5
    with pytest.raises(storage.StorageHold) as held:
        storage.require(root, workspace, 101)
    assert held.value.code == "low_disk" and held.value.observation["proposed_growth_bytes"] == 101
    assert "safe recovery" in str(held.value)
    with (root / "large-diagnostic").open("wb") as f:
        f.truncate(storage.EVIDENCE + 1)
    with pytest.raises(storage.StorageHold) as held:
        storage.require(root, workspace)
    assert held.value.code == "evidence_limit"
    (root / "large-diagnostic").unlink()
    with (workspace / "too-large").open("wb") as f:
        f.truncate(storage.SCRATCH + 1)
    with pytest.raises(storage.StorageHold) as held:
        storage.require(root, workspace)
    assert held.value.code == "scratch_limit"


def test_file_entry_pressure_is_distinct_and_no_old_cumulative_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(
        storage.shutil, "disk_usage", lambda p: SimpleNamespace(free=storage.RECOVERY + (2 << 30))
    )
    monkeypatch.setattr(storage.os, "statvfs", lambda p: SimpleNamespace(f_favail=8000))
    with pytest.raises(storage.StorageHold) as held:
        storage.require(tmp_path, tmp_path / "missing")
    assert held.value.code == "low_inodes"
    monkeypatch.setattr(storage.os, "statvfs", lambda p: SimpleNamespace(f_favail=100000))
    # Unrelated retained historical evidence is not part of this operation.
    history = tmp_path.parent / "history"
    history.mkdir()
    with (history / "large").open("wb") as f:
        f.truncate(600 << 20)
    assert storage.require(tmp_path, tmp_path / "missing")["evidence_bytes"] == 0


def copies(tmp_path):
    source = tmp_path / "current" / "browser"
    source.mkdir(parents=True, mode=0o700)
    target = tmp_path / "old" / "browser"
    target.mkdir(parents=True, mode=0o700)
    for name in ["binary", "different"]:
        (source / name).write_bytes(b"original-browser")
        (target / name).write_bytes(b"original-browser")
        (source / name).chmod(0o644)
        (target / name).chmod(0o644)
        info = (source / name).stat()
        os.utime(target / name, ns=(info.st_atime_ns, info.st_mtime_ns))
    (target / "different").write_bytes(b"different-bytes!")
    history = target.parent / "history.sqlite3"
    history.write_bytes(b"owner history")
    return source, target, history


def test_verified_retention_preserves_files_metadata_history_and_restore_paths(tmp_path):
    source, target, history = copies(tmp_path)
    before = (target / "binary").stat()
    assert release_cache.coalesce(source, target, tmp_path / "private-journal") == len(
        b"original-browser"
    )
    assert (target / "binary").stat().st_ino == (source / "binary").stat().st_ino
    assert (target / "binary").read_bytes() == b"original-browser"
    assert (target / "binary").stat().st_mtime_ns == before.st_mtime_ns
    assert (
        target / "different"
    ).read_bytes() == b"different-bytes!" and history.read_bytes() == b"owner history"
    records = [
        json.loads(s)
        for p in (tmp_path / "private-journal").glob("*.jsonl")
        for s in p.read_text().splitlines()
    ]
    assert len(records) == 1 and records[0]["all_paths_preserved"]
    assert release_cache.coalesce(source, target, tmp_path / "private-journal") == 0


def test_retention_refuses_links_and_preserves_original_on_interruption(tmp_path, monkeypatch):
    source, target, _ = copies(tmp_path)
    original = (target / "binary").read_bytes()
    monkeypatch.setattr(
        release_cache.os,
        "replace",
        lambda *a: (_ for _ in ()).throw(OSError("controlled interruption")),
    )
    with pytest.raises(OSError, match="controlled interruption"):
        release_cache.coalesce(source, target, tmp_path / "journal")
    assert (target / "binary").read_bytes() == original
    assert not list((tmp_path / "journal").glob("link-*"))
    target.rename(target.with_name("preserved"))
    target.symlink_to(target.with_name("preserved"))
    with pytest.raises(ValueError, match="link"):
        release_cache.coalesce(source, target, tmp_path / "journal")


def test_automatic_browser_retention_keeps_three_complete_independent_copies(tmp_path, monkeypatch):
    from hashlib import sha256
    from types import SimpleNamespace

    stages = tmp_path / "stages"
    stages.mkdir(mode=0o700)
    copies = []
    for n in range(5):
        stage = stages / ("everyday-outcome-" + format(n + 1, "07x"))
        stage.mkdir(mode=0o700)
        (stage / "browser").mkdir(parents=True, mode=0o700)
        (stage / "ops").mkdir(mode=0o700)
        binary = stage / "browser/browser"
        binary.write_bytes(b"identical distribution")
        binary.chmod(0o644)
        os.utime(binary, ns=(1000000000, 1000000000))
        (stage / "ops/bundle.sha256").write_text(
            sha256(binary.read_bytes()).hexdigest() + "  browser/browser\n"
        )
        (stage / "ops/bundle.sha256").chmod(0o600)
        (stage / "history.sqlite3").write_bytes(b"preserve owner history")
        os.utime(stage, ns=(1000000000 + n, 1000000000 + n))
        copies.append(stage)
    monkeypatch.setattr(release_cache, "STAGES", stages)
    monkeypatch.setattr(release_cache, "STATE", tmp_path / "journals")
    monkeypatch.setattr(
        release_cache.shutil, "disk_usage", lambda p: SimpleNamespace(free=storage.RECOVERY)
    )
    current = copies[-1]
    release_cache.maintain(None, current)
    assert (copies[0] / "browser/browser").stat().st_ino == (
        current / "browser/browser"
    ).stat().st_ino
    assert len({(p / "browser/browser").stat().st_ino for p in copies[-3:]}) == 3
    assert all((p / "history.sqlite3").stat().st_nlink == 1 for p in copies)
    assert release_cache.maintain(None, current) == 0
    assert release_cache.maintain(None, tmp_path / "disposable") == 0
