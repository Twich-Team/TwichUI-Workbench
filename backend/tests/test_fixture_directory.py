"""Disposable browser helpers honor the actual private temporary root, not host/tmp."""

from pathlib import Path
import pytest
import fixture_ctl


def test_current_private_tmpdir_is_accepted_without_host_tmp_access(tmp_path, monkeypatch):
    monkeypatch.setattr(fixture_ctl.tempfile, "gettempdir", lambda: str(tmp_path))
    folder = tmp_path / "workbench-e2e-owned"
    folder.mkdir(mode=0o700)
    db = folder / "test.sqlite3"
    db.write_bytes(b"NON-MODEL path check only")
    assert fixture_ctl.fixture_path(str(db)) == db
    with pytest.raises(SystemExit):
        fixture_ctl.fixture_path("/tmp/workbench-e2e-other/test.sqlite3")


@pytest.mark.parametrize(
    "kind", ["symlink", "parent-symlink", "wrong-name", "wrong-directory", "missing"]
)
def test_non_fixture_and_link_paths_rejected(tmp_path, monkeypatch, kind):
    monkeypatch.setattr(fixture_ctl.tempfile, "gettempdir", lambda: str(tmp_path))
    folder = tmp_path / "workbench-e2e-owned"
    folder.mkdir(mode=0o700)
    db = folder / "test.sqlite3"
    db.write_bytes(b"NON-MODEL")
    if kind == "symlink":
        db.unlink()
        db.symlink_to(tmp_path / "outside")
    elif kind == "parent-symlink":
        folder.rename(tmp_path / "saved")
        folder.symlink_to(tmp_path / "saved", target_is_directory=True)
    elif kind == "wrong-name":
        db = folder / "other.sqlite3"
        db.write_bytes(b"NON-MODEL")
    elif kind == "wrong-directory":
        db = tmp_path / "test.sqlite3"
        db.write_bytes(b"NON-MODEL")
    else:
        db.unlink()
    with pytest.raises(SystemExit):
        fixture_ctl.fixture_path(str(db))


def test_enclosing_task_limit_events_are_visible_even_when_child_events_are_zero(tmp_path):
    from workbench.real_confine import task_limit_events

    parent = tmp_path / "scope"
    child = parent / "command"
    child.mkdir(parents=True)
    (parent / "pids.events").write_text("max 1\n")
    (child / "pids.events").write_text("max 0\n")
    assert task_limit_events(str(parent)) == 1 and task_limit_events(str(child)) == 0


@pytest.mark.parametrize("parent_hit,child_hit", [(False, False), (True, False), (False, True)])
def test_command_result_reports_ancestor_or_child_task_refusal(
    tmp_path, monkeypatch, parent_hit, child_hit
):
    """NON-MODEL cgroup-file tape through the actual command-result path."""
    from workbench import real_confine

    base = tmp_path / "scope"
    base.mkdir()
    (base / "pids.events").write_text("max 0\n")
    monkeypatch.setattr(real_confine, "root", lambda: str(base))
    monkeypatch.setattr(real_confine, "USAGE_LOG", str(tmp_path / "usage.jsonl"))
    monkeypatch.setattr(real_confine, "_kill_and_wait", lambda cg: True)
    make_temp = real_confine.tempfile.mkdtemp
    # No child/output exists in this tape. Keep its fake scratch inside this
    # test's owned TMPDIR so it also runs under the outer verification sandbox.
    monkeypatch.setattr(
        real_confine.tempfile,
        "mkdtemp",
        lambda **kwargs: make_temp(prefix=kwargs["prefix"], dir=tmp_path),
    )

    def control_write(path, value):
        Path(path).write_text(value)
        # A real delegated command directory has this kernel-provided file.
        (Path(path).parent / "cpu.max").touch(exist_ok=True)

    monkeypatch.setattr(real_confine, "_w", control_write)

    class FinishedProcess:
        def __init__(self, argv, **kwargs):
            child = Path(argv[argv.index("--cgroup") + 1])
            (base / "pids.events").write_text(f"max {int(parent_hit)}\n")
            (child / "pids.events").write_text(f"max {int(child_hit)}\n")

        def wait(self, **kwargs):
            return 0

    monkeypatch.setattr(real_confine.subprocess, "Popen", FinishedProcess)
    result = real_confine.run(["true"], str(tmp_path), 1)
    assert result["rc"] == 0
    assert result["status"] == ("fail" if parent_hit or child_hit else "pass")
    assert result["usage"]["pids_limit_hit"] is (parent_hit or child_hit)
    assert result["usage"]["enclosing_pids_limit_delta"] == int(parent_hit)
    assert ("command or enclosing scope" in result["out"]) is (parent_hit or child_hit)
