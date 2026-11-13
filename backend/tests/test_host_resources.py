"""Independent host counters, bounded history and read-only identity observations."""

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import json
import sqlite3
import threading

from fastapi.testclient import TestClient
import pytest

from workbench import host_resources as host, monitoring, worker
from workbench.api import create_app

NOW = 1800000000


@pytest.fixture
def sources(tmp_path):
    proc, drm = tmp_path / "proc", tmp_path / "drm"
    proc.mkdir()
    drm.mkdir()
    (proc / "stat").write_text("cpu  100 0 100 700 100 0 0 0 0 0\n")
    (proc / "meminfo").write_text(
        "MemTotal: 1024 kB\nMemAvailable: 256 kB\nSwapTotal: 512 kB\nSwapFree: 128 kB\n"
    )
    device = drm / "card0/device"
    device.mkdir(parents=True)
    for name, value in {
        "vendor": "0x1002",
        "device": "0x73bf",
        "gpu_busy_percent": "37",
        "mem_info_vram_used": "1024",
        "mem_info_vram_total": "4096",
    }.items():
        (device / name).write_text(value)
    return proc, drm, device


def metadata(**changes):
    value = dict(
        collector="workbench-host-v1",
        model=dict(
            alias="observed-model", file="weight.gguf", bytes=14252845984, mapped=True, api="ready"
        ),
        services=[dict(name="worker", state="active", pid=123, restarts=0)],
        metric_notes={},
    )
    return {**value, **changes}


def test_actual_counter_deltas_units_and_filesystem_available(sources, tmp_path, monkeypatch):
    proc, drm, _ = sources
    monkeypatch.setattr(
        host.os,
        "statvfs",
        lambda _: SimpleNamespace(f_blocks=1000, f_bfree=200, f_bavail=150, f_frsize=4096),
    )
    values, counters, notes = host.measure((800, 700), proc, drm, tmp_path)
    assert counters == (1000, 800) and values["cpu"] == 50
    assert values["ram_used"] == 768 * 1024 and values["ram_total"] == 1024 * 1024
    assert values["swap_used"] == 384 * 1024 and values["swap_total"] == 512 * 1024
    assert values["disk_used"] == 800 * 4096 and values["disk_available"] == 150 * 4096
    assert values["gpu"] == 37 and values["vram_used"] == 1024 and values["vram_total"] == 4096
    assert not notes


def test_partial_gpu_unsupported_counter_does_not_hide_other_observations(sources, tmp_path):
    proc, drm, device = sources
    (device / "gpu_busy_percent").unlink()
    values, _, notes = host.measure((800, 700), proc, drm, tmp_path)
    assert values["cpu"] == 50 and values["gpu"] is None
    assert values["ram_total"] and values["disk_available"] and values["swap_total"]
    assert values["vram_total"] == 4096 and "driver" in notes["gpu"]


def test_startup_reset_bad_counters_and_missing_gpu_do_not_invent_zero(sources, tmp_path):
    proc, drm, device = sources
    first, _, notes = host.measure(None, proc, drm, tmp_path)
    assert first["cpu"] is None and "second observation" in notes["cpu"]
    reset, _, _ = host.measure((2000, 1000), proc, drm, tmp_path)
    assert reset["cpu"] is None
    (device / "vendor").write_text("0x8086")
    (proc / "stat").write_text("cpu invalid\n")
    (proc / "meminfo").write_text("bad data")
    values, _, notes = host.measure(None, proc, drm, tmp_path)
    assert all(values[key] is None for key in ["cpu", "gpu", "ram_used", "swap_used", "vram_used"])
    assert values["disk_total"] and "supported" in notes["gpu"]


def test_zero_swap_is_observed_and_invalid_memory_totals_are_rejected(sources, tmp_path):
    proc, drm, _ = sources
    (proc / "meminfo").write_text(
        "MemTotal: 100 kB\nMemAvailable: 50 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n"
    )
    values, _, _ = host.measure(None, proc, drm, tmp_path)
    assert values["swap_used"] == values["swap_total"] == 0
    (proc / "meminfo").write_text(
        "MemTotal: 100 kB\nMemAvailable: 101 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n"
    )
    assert host.measure(None, proc, drm, tmp_path)[0]["ram_used"] is None


def test_private_history_is_bounded_and_partial_metrics_remain_readable(
    sources, tmp_path, monkeypatch
):
    proc, drm, device = sources
    tmp_path.chmod(0o700)
    path = tmp_path / "resources.sqlite3"
    values, _, _ = host.measure((800, 700), proc, drm, tmp_path)
    host.store(path, NOW - host.RETENTION - 15, values, metadata())
    host.store(path, NOW - 60, values, metadata())
    host.store(
        path, NOW, {**values, "gpu": None}, metadata(metric_notes={"gpu": "Driver unavailable"})
    )
    before = path.read_bytes()
    result = monitoring.resources(path, now=NOW)
    assert result["freshness"] == result["status_freshness"] == "fresh"
    assert result["label"] == "Observed host · Workbench collector"
    assert result["latest"]["disk_available"] and result["latest"]["swap_used"]
    assert result["latest"]["gpu"] is None and result["latest"]["vram_total"] == 4096
    assert result["model"]["file"] == "weight.gguf" and result["services"][0]["restarts"] == 0
    assert result["metric_notes"]["gpu"] == "Driver unavailable"
    assert monitoring.resources(path, now=NOW + 60)["status_freshness"] == "stale"
    assert path.read_bytes() == before and path.stat().st_mode & 0o077 == 0
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT count(*) FROM samples").fetchone()[0] == 2
        assert (
            c.execute("PRAGMA page_size").fetchone()[0]
            * c.execute("PRAGMA page_count").fetchone()[0]
            <= 64 << 20
        )


def test_bad_status_cannot_hide_good_host_metrics_or_leak_private_paths(sources, tmp_path):
    proc, drm, _ = sources
    tmp_path.chmod(0o700)
    path = tmp_path / "resources.sqlite3"
    values, _, _ = host.measure((800, 700), proc, drm, tmp_path)
    host.store(
        path,
        NOW,
        values,
        metadata(
            model={
                "alias": "secret=abcdef",
                "file": "/home/private/weight.gguf",
                "bytes": -5,
                "mapped": "yes",
                "api": "invented",
            }
        ),
    )
    data = monitoring.resources(path, now=NOW)
    assert data["model"]["bytes"] is None and data["model"]["mapped"] is None
    assert "abcdef" not in json.dumps(data) and "/home/private" not in json.dumps(data)
    with sqlite3.connect(path) as c:
        c.execute("UPDATE collector_status SET body=?", ("not json",))
    data = monitoring.resources(path, now=NOW)
    assert data["latest"]["cpu"] == 50 and data["model"] is None


def test_telemetry_never_creates_through_links_or_shared_files(sources, tmp_path):
    proc, drm, _ = sources
    tmp_path.chmod(0o700)
    outside = tmp_path / "saved-history"
    outside.write_bytes(b"protected")
    link = tmp_path / "linked.sqlite3"
    link.symlink_to(outside)
    values, _, _ = host.measure(None, proc, drm, tmp_path)
    with pytest.raises(ValueError):
        host.store(link, NOW, values, metadata())
    assert outside.read_bytes() == b"protected"
    outside.chmod(0o666)
    with pytest.raises(ValueError):
        host.store(outside, NOW, values, metadata())


def test_service_probe_uses_one_fixed_read_only_command(monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(
            stdout="Id=workbench-everyday-worker.service\nActiveState=active\nMainPID=55\nNRestarts=0\n\nId=twichui-llm.service\nActiveState=failed\nMainPID=0\nNRestarts=3\n"
        )

    monkeypatch.setattr(host.subprocess, "run", run)
    rows = {r["name"]: r for r in host.services()}
    assert rows["worker"]["pid"] == 55 and rows["model"]["state"] == "failed"
    assert rows["api"]["state"] == "unknown"
    assert calls[0][0][:4] == ["/usr/bin/systemctl", "--system", "--no-pager", "show"]
    assert calls[0][1]["timeout"] == 2


def test_model_uses_actual_process_mapping_and_never_reads_arbitrary_key(
    sources, tmp_path, monkeypatch
):
    proc, _, _ = sources
    pid = proc / "42"
    pid.mkdir()
    weight = tmp_path / "real-observed.gguf"
    weight.write_bytes(b"controlled model metadata")
    (pid / "cmdline").write_bytes(
        b"llama-server\0--model\0" + str(weight).encode() + b"\0--alias\0actual-alias\0"
    )
    (pid / "maps").write_text("000-100 r--p 0000 00:00 1 " + str(weight) + "\n")
    monkeypatch.delenv("WORKBENCH_LOCAL_KEY", raising=False)
    value = host.model({"pid": 42}, proc)
    assert value == dict(
        alias="actual-alias",
        file=weight.name,
        bytes=weight.stat().st_size,
        mapped=True,
        api="unavailable",
    )
    assert str(tmp_path) not in json.dumps(value)
    private = tmp_path / "key"
    private.write_text("not displayed")
    private.chmod(0o600)
    linked = tmp_path / "key-link"
    linked.symlink_to(private)
    monkeypatch.setenv("WORKBENCH_LOCAL_KEY", str(linked))
    monkeypatch.setattr(
        host.urllib.request, "build_opener", lambda *args: pytest.fail("No network for linked key")
    )
    assert host.model({"pid": 42}, proc)["api"] == "unavailable"


def test_installed_mode_reads_the_store_sibling_without_environment_wiring(
    store, tmp_path, monkeypatch, sources
):
    proc, drm, _ = sources
    directory = Path(store.url.database).parent
    directory.chmod(0o700)
    path = host.database(store.url.database)
    values, _, _ = host.measure((800, 700), proc, drm, tmp_path)
    host.store(path, int(__import__("time").time()), values, metadata())
    monkeypatch.setenv("WORKBENCH_PLANNER", "everyday")
    monkeypatch.delenv("WORKBENCH_RESOURCE_DB", raising=False)
    original = Path(store.url.database).read_bytes()
    with TestClient(create_app(store)) as client:
        data = client.get("/api/resources").json()
        assert data["freshness"] == "fresh" and data["latest"]["cpu"] == 50
        assert (
            data["model"]["alias"] == "observed-model" and data["services"][0]["state"] == "active"
        )
    assert Path(store.url.database).read_bytes() == original


def test_collector_failure_does_not_crash_or_stop_the_task_worker(monkeypatch):
    from workbench import everyday, local_model_priority

    seen = threading.Event()

    def fail(policy):
        seen.set()
        raise OSError("unavailable counter")

    monkeypatch.setattr(host, "collect", fail)
    monkeypatch.setattr(everyday, "housekeep", lambda policy: None)
    monkeypatch.setattr(local_model_priority, "reconcile", lambda engine: None)

    @contextmanager
    def guard(*args):
        yield threading.Event()

    monkeypatch.setattr(everyday, "watch_capacity", guard)
    calls = []

    def tick(*args, **kwargs):
        assert seen.wait(1)
        calls.append(True)
        return False

    monkeypatch.setattr(worker, "tick", tick)
    worker.run(
        None,
        SimpleNamespace(session_config=None, once=True),
        threading.Event(),
        SimpleNamespace(host_observations=True),
    )
    assert calls == [True]


def test_sampler_continues_during_a_blocked_task_and_recovers_after_failure(monkeypatch):
    calls = []
    recovered = threading.Event()

    def sample(policy):
        calls.append(True)
        if len(calls) == 1:
            raise ValueError("first observation failed")
        recovered.set()

    monkeypatch.setattr(host, "collect", sample)
    monkeypatch.setattr(host, "INTERVAL", 0.01)
    with host.observations(SimpleNamespace(host_observations=True), threading.Event()):
        assert recovered.wait(1)
    assert len(calls) >= 2
