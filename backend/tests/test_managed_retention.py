"""Real disposable filesystem/SQLite/process boundaries for automatic retention."""

import json
import os
from pathlib import Path
import sqlite3
import shutil
import tempfile
import socket

import pytest

import managed_storage as retention


@pytest.fixture
def rig(tmp_path, monkeypatch, request):
    # The command's outer scratch is itself disposable. These ownership tests
    # need a separate filesystem fixture so their retained proof is genuinely
    # outside every managed pool, exactly as required in production.
    tmp_path = Path(tempfile.mkdtemp(prefix="wbr-retention-", dir="/dev/shm"))
    failures_before = request.session.testsfailed
    pool = tmp_path / "managed"
    db = tmp_path / "history.sqlite3"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE history(body TEXT, other BLOB)")
    monkeypatch.setattr(retention, "MIN_AGE", 0)
    monkeypatch.setattr(retention, "KEEP", 0)
    # Model the relevant same-user process with actual native fd/maps/cwd links.
    # Unrelated browser/CI processes may legitimately hold real host cleanup.
    processes = tmp_path / "processes"
    processes.mkdir()
    (processes / str(os.getpid())).symlink_to(
        Path("/proc") / str(os.getpid()), target_is_directory=True
    )
    monkeypatch.setattr(retention, "PROCESSES", processes)
    yield pool, db
    if request.session.testsfailed == failures_before:
        shutil.rmtree(tmp_path)
    else:
        print("Retained failed synthetic retention fixture:", tmp_path)


def finished(pool, kind="test-scratch", evidence=None):
    path = retention.allocate(pool, kind)
    (path / "copy.txt").write_bytes(b"disposable fixture data" * 200)
    if kind != "test-scratch":
        evidence = evidence or pool.parent / (path.name + "-retained.json")
        evidence.write_text('{"checked":true,"original_evidence_retained":true}')
        evidence.chmod(0o600)
    retention.close(path, True, evidence)
    return path


@pytest.mark.parametrize("kind", sorted(retention.KINDS))
def test_all_closed_copy_classes_prune_and_keep_a_durable_receipt(rig, kind):
    pool, db = rig
    path = finished(pool, kind)
    before = db.read_bytes()
    result = retention.maintain([pool], databases=[db])
    assert result["removed"] == [str(path)] and not path.exists()
    assert result["reclaimed_bytes"] > 0
    receipt = next((pool / ".retention").glob("*.json"))
    assert json.loads(receipt.read_text())["state"] == "removed"
    assert list((pool / ".retention").glob("*.intent"))
    assert db.read_bytes() == before
    assert retention.maintain([pool], databases=[db])["removed"] == []


def test_preview_matches_cleanup_and_changes_no_contents_or_paths(rig):
    pool, db = rig
    path = finished(pool)
    before = {str(p): p.read_bytes() for p in pool.rglob("*") if p.is_file()}
    preview = retention.maintain([pool], databases=[db], preview=True)
    assert not preview["removed"] and preview["candidates"][0]["path"] == str(path)
    assert {str(p): p.read_bytes() for p in pool.rglob("*") if p.is_file()} == before
    result = retention.maintain([pool], databases=[db])
    assert result["candidates"] == preview["candidates"]


@pytest.mark.parametrize("kind", ["json", "blob", "relative", "allocation_id"])
def test_references_in_every_saved_row_block_deletion(rig, kind):
    pool, db = rig
    path = finished(pool)
    location = str(path / "copy.txt")
    if kind == "relative":
        location = str(path.relative_to(pool)) + "/copy.txt"
    if kind == "allocation_id":
        location = retention.record(pool, path)[1]["id"]
    with sqlite3.connect(db) as c:
        c.execute(
            "INSERT INTO history VALUES(?,?)",
            (
                json.dumps({"nested": {"evidence": location}}) if kind != "blob" else "",
                str(path).encode() if kind == "blob" else b"",
            ),
        )
    result = retention.maintain([pool], databases=[db])
    assert path.exists() and not result["removed"]
    assert any("saved history" in r["reason"] for r in result["deferred"])


def test_archive_current_installation_and_rollback_ancestors_and_descendants_are_held(rig):
    pool, db = rig
    path = finished(pool)
    for relation in ["ancestor", "self", "descendant"]:
        protected = (
            path.parent
            if relation == "ancestor"
            else path / "copy.txt"
            if relation == "descendant"
            else path
        )
        assert not retention.maintain([pool], [protected], [db])["removed"]
        assert path.exists()


def test_unregistered_failed_active_young_and_recent_copies_remain(rig, monkeypatch):
    pool, db = rig
    active = retention.allocate(pool, "workspace")
    failed = retention.allocate(pool, "test-scratch")
    retention.close(failed, False)
    unknown = pool / "old-unregistered"
    unknown.mkdir()
    (unknown / "history").write_text("keep")
    young = finished(pool)
    monkeypatch.setattr(retention, "MIN_AGE", 86400)
    assert not retention.maintain([pool], databases=[db])["removed"]
    monkeypatch.setattr(retention, "MIN_AGE", 0)
    monkeypatch.setattr(retention, "KEEP", 2)
    assert not retention.maintain([pool], databases=[db])["removed"]
    assert all(p.exists() for p in [active, failed, unknown, young])


def test_open_file_and_a_mapping_prevent_cleanup(rig):
    import mmap

    pool, db = rig
    path = finished(pool)
    with (path / "copy.txt").open("rb") as f:
        view = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            assert not retention.maintain([pool], databases=[db])["removed"]
        finally:
            view.close()
    assert retention.maintain([pool], databases=[db])["removed"] == [str(path)]


def test_changed_bytes_or_missing_retained_evidence_preserve_the_copy(rig):
    pool, db = rig
    changed = finished(pool)
    (changed / "copy.txt").write_text("new information")
    evidence = pool.parent / "retained.json"
    missing = finished(pool, "workspace", evidence)
    evidence.unlink()
    assert not retention.maintain([pool], databases=[db])["removed"]
    assert changed.exists() and missing.exists()


def test_model_weights_never_close_as_disposable_data(rig):
    pool, _ = rig
    path = retention.allocate(pool, "cache")
    (path / "model.gguf").write_bytes(b"GGUF")
    proof = pool.parent / "receipt"
    proof.write_text("retained")
    proof.chmod(0o600)
    with pytest.raises(ValueError, match="Model weights"):
        retention.close(path, True, proof)
    assert (path / "model.gguf").exists()


def test_symlink_deletion_never_follows_an_external_target(rig):
    pool, db = rig
    outside = pool.parent / "protected"
    outside.write_text("saved history")
    path = retention.allocate(pool, "test-scratch")
    (path / "link").symlink_to(outside)
    retention.close(path, True)
    assert retention.maintain([pool], databases=[db])["removed"] == [str(path)]
    assert outside.read_text() == "saved history"


def test_changed_pool_link_and_unowned_copy_fail_closed(rig, monkeypatch):
    pool, db = rig
    path = finished(pool)
    changed = pool.parent / "moved"
    pool.rename(changed)
    pool.symlink_to(changed, target_is_directory=True)
    assert not retention.maintain([pool], databases=[db])["removed"]
    assert (changed / path.name).exists()


def test_live_database_writer_or_cleanup_owner_defers_without_retrying_writes(rig):
    pool, db = rig
    path = finished(pool)
    with retention.locked(pool):
        assert not retention.maintain([pool], databases=[db])["removed"]
    with sqlite3.connect(db) as c:
        c.execute("BEGIN IMMEDIATE")
        result = retention.maintain([pool], databases=[db])
        assert path.exists() and not result["removed"]
    assert retention.maintain([pool], databases=[db])["removed"]


def test_late_reference_between_preview_and_prune_is_not_removed(rig, monkeypatch):
    pool, db = rig
    path = finished(pool)
    save = retention.save

    def late_reference(file, value):
        save(file, value)
        if file.suffix == ".intent":
            with sqlite3.connect(db) as c:
                c.execute("INSERT INTO history VALUES(?,?)", (str(path), b""))

    monkeypatch.setattr(retention, "save", late_reference)
    assert not retention.maintain([pool], databases=[db])["removed"]
    assert path.exists()


def test_interrupted_cleanup_resumes_only_the_known_remaining_inventory(rig, monkeypatch):
    pool, db = rig
    path = retention.allocate(pool, "test-scratch")
    (path / "a").write_text("a")
    (path / "b").write_text("b")
    retention.close(path, True)
    remove = retention.prune

    def interrupt(trash, *args):
        (trash / "a").unlink()
        raise OSError("injected interruption after one disposable unlink")

    monkeypatch.setattr(retention, "prune", interrupt)
    result = retention.maintain([pool], databases=[db])
    assert not result["removed"] and not path.exists()
    trash = next((pool / ".retention").glob(".discard-*"))
    assert (trash / "b").exists()
    monkeypatch.setattr(retention, "prune", remove)
    assert retention.maintain([pool], databases=[db])["removed"] == [str(path)]
    assert not trash.exists()


def test_interrupted_cleanup_with_new_data_stays_held(rig, monkeypatch):
    pool, db = rig
    finished(pool)

    def interrupt(trash, *args):
        raise OSError("injected interruption")

    monkeypatch.setattr(retention, "prune", interrupt)
    retention.maintain([pool], databases=[db])
    trash = next((pool / ".retention").glob(".discard-*"))
    (trash / "new-evidence").write_text("keep")
    assert not retention.maintain([pool], databases=[db])["removed"]
    assert (trash / "new-evidence").exists()


def test_repeated_cycles_are_bounded_and_reclaim_closed_growth(rig, monkeypatch):
    pool, db = rig
    monkeypatch.setattr(retention, "MAX_ITEMS", 2)
    preserved = []
    for _ in range(3):
        paths = [finished(pool) for _ in range(4)]
        proof = pool.parent / "saved.json"
        proof.write_text(json.dumps([str(p) for p in preserved]))
        first = retention.maintain([pool], databases=[db])
        second = retention.maintain([pool], databases=[db])
        assert len(first["removed"]) == len(second["removed"]) == 2
        assert all(not p.exists() for p in paths)
        assert not retention.maintain([pool], databases=[db])["removed"]


def test_non_test_copies_require_a_history_source_and_retained_external_evidence(rig):
    pool, _ = rig
    path = finished(pool, "release")
    assert not retention.maintain([pool])["removed"] and path.exists()
    with pytest.raises(FileNotFoundError):
        other = retention.allocate(pool, "workspace")
        retention.close(other, True)


def test_allocations_never_adopt_existing_or_escaping_paths(rig):
    pool, _ = rig
    first = retention.allocate(pool, "workspace", "new")
    with pytest.raises(FileExistsError):
        retention.allocate(pool, "workspace", "new")
    for name in ["../outside", ".", "/tmp/outside"]:
        with pytest.raises(ValueError):
            retention.allocate(pool, "cache", name)
    assert first.exists()


def test_housekeeping_uses_explicit_pool_and_required_rollback(rig, monkeypatch):
    from types import SimpleNamespace
    from workbench import everyday, release_cache

    pool, db = rig
    removable = finished(pool)
    rollback = finished(pool, "release")
    monkeypatch.setattr(release_cache, "maintain", lambda *args: 0)
    policy = SimpleNamespace(
        retention_pools=[str(pool)],
        retention_keep=[str(rollback)],
        database=str(db),
        evidence_roots=[],
        evidence_limit_bytes=1 << 30,
    )
    result = everyday.housekeep(policy)
    assert result["managed"]["removed"] == [str(removable)]
    assert rollback.exists()


def test_escaped_json_references_are_decoded_before_pruning(rig):
    pool, db = rig
    path = finished(pool)
    escaped = json.dumps({"path": str(path)}).replace("/", "\\/")
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO history VALUES(?,?)", (escaped, b""))
    assert not retention.maintain([pool], databases=[db])["removed"]
    assert path.exists()


def test_large_directory_pruning_is_bounded_and_resumes(rig):
    pool, db = rig
    path = retention.allocate(pool, "test-scratch")
    for i in range(2050):
        (path / str(i)).write_text("fixture")
    retention.close(path, True)
    first = retention.maintain([pool], databases=[db])
    assert first["in_progress"] == [str(path)] and not first["removed"]
    second = retention.maintain([pool], databases=[db])
    assert second["removed"] == [str(path)]


def test_retained_evidence_cannot_live_in_another_disposable_copy(rig):
    pool, _ = rig
    first = retention.allocate(pool, "workspace")
    second = retention.allocate(pool, "cache")
    proof = second / "result"
    proof.write_text("unique result")
    proof.chmod(0o600)
    with pytest.raises(ValueError, match="outside managed"):
        retention.close(first, True, proof)
    assert proof.exists()


def test_production_cli_preview_does_not_archive_or_prune(rig):
    import subprocess
    import sys
    from pathlib import Path

    pool, db = rig
    path = finished(pool)
    cli = Path(__file__).resolve().parents[2] / "scripts/wb.py"
    result = subprocess.run(
        [
            sys.executable,
            str(cli),
            "retention",
            "--pool",
            str(pool),
            "--database",
            str(db),
            "--preview",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    data = json.loads(result.stdout)
    assert data["preview"] is True and data["removed"] == [] and path.exists()


def test_unreadable_unknown_process_conservatively_holds_cleanup(rig, monkeypatch):
    pool, db = rig
    path = finished(pool)
    process = retention.PROCESSES / "1234"
    process.mkdir()
    (process / "cmdline").write_bytes(b"unknown-client\0")
    (process / "comm").write_text("unknown-client")
    read = Path.read_bytes

    def blocked(item):
        if item == process / "maps":
            raise PermissionError("nondumpable process")
        return read(item)

    monkeypatch.setattr(Path, "read_bytes", blocked)
    assert not retention.maintain([pool], databases=[db])["removed"]
    assert path.exists()


def test_invalid_one_copy_receipt_preserves_it_and_does_not_block_other_copies(rig):
    pool, db = rig
    bad = finished(pool)
    good = finished(pool)
    entry, value = retention.record(pool, bad)
    value["closed_at"] = float("nan")
    retention.save(entry, value)
    result = retention.maintain([pool], databases=[db])
    assert result["removed"] == [str(good)] and bad.exists()


@pytest.mark.parametrize("escaped", [False, True, "relative"])
def test_retained_report_that_links_back_into_a_copy_preserves_that_copy(rig, escaped):
    pool, db = rig
    path = retention.allocate(pool, "workspace")
    (path / "snapshot.txt").write_text("still referenced by the report")
    proof = pool.parent / "report.json"
    location = (
        str(path.relative_to(proof.parent) / "snapshot.txt")
        if escaped == "relative"
        else str(path / "snapshot.txt")
    )
    body = json.dumps({"evidence": location})
    proof.write_text(body.replace("/", "\\/") if escaped is True else body)
    proof.chmod(0o600)
    retention.close(path, True, proof)
    result = retention.maintain([pool], databases=[db])
    assert not result["removed"] and (path / "snapshot.txt").exists()
    assert any(
        item["reason"] == "retained result references the copy" for item in result["deferred"]
    )


def test_short_directory_names_do_not_match_inside_unrelated_digests(rig):
    pool, db = rig
    path = retention.allocate(pool, "test-scratch", "a123b")
    (path / "copy.txt").write_text("disposable copy")
    retention.close(path, True)
    with sqlite3.connect(db) as c:
        c.execute(
            "INSERT INTO history VALUES(?,?)", (json.dumps({"digest": "0a123b0123456789"}), b"")
        )
    assert retention.maintain([pool], databases=[db])["removed"] == [str(path)]


def test_named_test_socket_holds_while_active_and_prunes_only_after_close(rig):
    pool, db = rig
    path = retention.allocate(pool, "test-scratch", "ipc")
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(str(path / "s"))
        server.listen()
        retention.close(path, True)
        assert not retention.maintain([pool], databases=[db])["removed"]
        assert (path / "s").exists()
    assert retention.maintain([pool], databases=[db])["removed"] == [str(path)]
