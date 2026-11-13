"""Host observations sampled by the existing worker, never a model generation.

Resource history has its own bounded SQLite file. Application/task history and
the legacy collector are neither initialized nor changed by this module.
"""

from contextlib import closing, contextmanager
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import threading
import time
import urllib.error
import urllib.request

from workbench.monitoring import MAX_ROWS, RETENTION

SERVICES = {
    "api": "workbench-everyday-api.service",
    "worker": "workbench-everyday-worker.service",
    "connector": "workbench-everyday-codex.service",
    "private_access": "workbench-everyday-lan.service",
    "model": "twichui-llm.service",
}
_previous = None
INTERVAL = 15
log = logging.getLogger(__name__)


def database(store):
    return Path(store).parent / "machine-resources.sqlite3"


def number(path):
    try:
        value = int(path.read_text().strip())
        return value if value >= 0 else None
    except OSError, ValueError:
        return None


def counters(proc):
    line = (proc / "stat").read_text().splitlines()[0].split()
    if line[0] != "cpu":
        raise ValueError("Aggregate CPU counters missing")
    values = [int(v) for v in line[1:9]]
    if len(values) < 5 or any(v < 0 for v in values):
        raise ValueError("Invalid CPU counters")
    return sum(values), values[3] + values[4]


def measure(previous=None, proc=Path("/proc"), drm=Path("/sys/class/drm"), disk=Path("/")):
    values = dict.fromkeys(
        (
            "cpu",
            "gpu",
            "ram_used",
            "ram_total",
            "vram_used",
            "vram_total",
            "disk_used",
            "disk_total",
            "disk_available",
            "swap_used",
            "swap_total",
            "gpu_device",
        )
    )
    notes = {}
    current = None
    try:
        current = counters(proc)
        if previous:
            total, idle = current[0] - previous[0], current[1] - previous[1]
            if total > 0 and 0 <= idle <= total:
                values["cpu"] = 100 * (total - idle) / total
        if values["cpu"] is None:
            notes["cpu"] = "CPU utilization needs a second observation after startup."
    except OSError, ValueError, IndexError:
        notes["cpu"] = "Host CPU counters could not be read."
    try:
        memory = {}
        for line in (proc / "meminfo").read_text().splitlines():
            key, body = line.split(":", 1)
            memory[key] = int(body.split()[0]) * 1024
        total, available = memory["MemTotal"], memory["MemAvailable"]
        if not 0 <= available <= total:
            raise ValueError("Invalid memory counters")
        values.update(ram_used=total - available, ram_total=total)
        total, free = memory["SwapTotal"], memory["SwapFree"]
        if not 0 <= free <= total:
            raise ValueError("Invalid swap counters")
        values.update(swap_used=total - free, swap_total=total)
    except OSError, ValueError, KeyError, IndexError:
        notes["ram"] = "Host memory counters could not be read completely."
        notes["swap"] = "Host swap counters could not be read completely."
    try:
        info = os.statvfs(disk)
        values.update(
            disk_total=info.f_blocks * info.f_frsize,
            disk_used=(info.f_blocks - info.f_bfree) * info.f_frsize,
            disk_available=info.f_bavail * info.f_frsize,
        )
    except OSError:
        notes["disk"] = "Available space on the application filesystem could not be read."
    found = False
    for card in sorted(drm.glob("card*")):
        if not re.fullmatch(r"card[0-9]+", card.name):
            continue
        try:
            vendor = (card / "device/vendor").read_text().strip()
            device = (card / "device/device").read_text().strip()
            if vendor != "0x1002":
                continue
            found = True
            values["gpu_device"] = "AMD " + card.name + " · " + device
            busy = number(card / "device/gpu_busy_percent")
            values["gpu"] = busy if busy is not None and busy <= 100 else None
            used, total = (
                number(card / "device/mem_info_vram_used"),
                number(card / "device/mem_info_vram_total"),
            )
            if used is not None and total is not None and 0 <= used <= total and total > 0:
                values.update(vram_used=used, vram_total=total)
            if values["gpu"] is None:
                notes["gpu"] = "This GPU driver does not provide a readable utilization counter."
            if values["vram_total"] is None:
                notes["vram"] = "This GPU driver does not provide dedicated VRAM counters."
            break
        except OSError:
            continue
    if not found:
        notes["gpu"] = "No supported AMD DRM telemetry device was found."
        notes["vram"] = "Dedicated VRAM observations are unavailable on this device."
    return values, current, notes


def services():
    result = {key: dict(name=key, state="unknown", pid=None, restarts=None) for key in SERVICES}
    try:
        output = subprocess.run(
            [
                "/usr/bin/systemctl",
                "--system",
                "--no-pager",
                "show",
                "-p",
                "Id",
                "-p",
                "ActiveState",
                "-p",
                "MainPID",
                "-p",
                "NRestarts",
                *SERVICES.values(),
            ],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
        if len(output.stdout) > 8192:
            return list(result.values())
        reverse = {value: key for key, value in SERVICES.items()}
        for block in output.stdout.strip().split("\n\n"):
            fields = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
            key = reverse.get(fields.get("Id"))
            state = fields.get("ActiveState")
            if key and state in {
                "active",
                "inactive",
                "failed",
                "activating",
                "deactivating",
                "reloading",
            }:
                result[key] = dict(
                    name=key,
                    state=state,
                    pid=int(fields["MainPID"]) if fields.get("MainPID", "").isdigit() else None,
                    restarts=int(fields["NRestarts"])
                    if fields.get("NRestarts", "").isdigit()
                    else None,
                )
    except OSError, subprocess.SubprocessError, ValueError:
        pass
    return list(result.values())


def model(service, proc=Path("/proc")):
    result = dict(alias=None, file=None, bytes=None, mapped=None, api="unavailable")
    pid = service.get("pid")
    if pid:
        try:
            args = (proc / str(pid) / "cmdline").read_bytes().decode().split("\0")
            option = next((flag for flag in ("--model", "-m") if flag in args), None)
            if option:
                path = Path(args[args.index(option) + 1])
                if path.is_absolute() and path.is_file():
                    result.update(
                        file=path.name,
                        bytes=path.stat().st_size,
                        mapped=any(
                            line.split(maxsplit=5)[-1] == str(path)
                            for line in (proc / str(pid) / "maps").read_text().splitlines()
                        ),
                    )
            if "--alias" in args:
                result["alias"] = args[args.index("--alias") + 1][:120]
        except OSError, ValueError, IndexError, UnicodeError:
            pass
    key = os.environ.get("WORKBENCH_LOCAL_KEY")
    if not key:
        return result
    try:
        path = Path(key)
        info = path.lstat()
        if (
            path.resolve() != path
            or not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or not 0 < info.st_size <= 4096
        ):
            return result
        secret = path.read_text().strip()
        if not secret:
            return result
        for field in ("alias", "file"):
            if result[field] and secret in result[field]:
                result[field] = None
        from workbench.planning_transport import NoRedirect

        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        request = urllib.request.Request(
            "http://127.0.0.1:8080/v1/models", headers={"Authorization": "Bearer " + secret}
        )
        with opener.open(request, timeout=1) as response:
            data = json.loads(response.read(16385))
            names = data.get("data", [])
            alias = names[0].get("id") if len(names) == 1 else None
            if (
                response.status == 200
                and isinstance(alias, str)
                and 0 < len(alias) <= 120
                and secret not in alias
            ):
                result.update(alias=alias, api="ready")
    except urllib.error.HTTPError as error:
        result["api"] = "authentication_required" if error.code in {401, 403} else "unavailable"
    except OSError, ValueError, TypeError, KeyError, IndexError, AttributeError:
        pass
    return result


def store(path, at, values, status):
    path = Path(path)
    if path.resolve() != path or not path.parent.is_dir():
        raise ValueError("Exact existing telemetry directory required")
    parent = path.parent.stat()
    if parent.st_uid != os.getuid() or parent.st_mode & 0o077:
        raise ValueError("Private owned telemetry directory required")
    for file in [path, Path(str(path) + "-wal"), Path(str(path) + "-shm")]:
        if file.exists() or file.is_symlink():
            info = file.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError("Telemetry file ownership differs")
    body, latest = json.dumps(values, allow_nan=False), json.dumps(status, allow_nan=False)
    if max(len(body), len(latest)) > 4096:
        raise ValueError("Telemetry sample exceeds its bound")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    with closing(sqlite3.connect(path, timeout=0.15)) as c, c:
        c.execute("PRAGMA journal_mode=WAL")
        page_size = c.execute("PRAGMA page_size").fetchone()[0]
        c.execute("PRAGMA max_page_count=" + str((64 << 20) // page_size))
        c.execute("PRAGMA journal_size_limit=1048576")
        c.execute("PRAGMA wal_autocheckpoint=100")
        c.execute("CREATE TABLE IF NOT EXISTS samples(at INTEGER PRIMARY KEY, body TEXT)")
        c.execute(
            "CREATE TABLE IF NOT EXISTS collector_status(id INTEGER PRIMARY KEY CHECK(id=1), at INTEGER, body TEXT)"
        )
        c.execute("INSERT OR REPLACE INTO samples VALUES(?,?)", (at, body))
        c.execute("INSERT OR REPLACE INTO collector_status VALUES(1,?,?)", (at, latest))
        c.execute("DELETE FROM samples WHERE at < ?", (at - RETENTION,))
        c.execute(
            "DELETE FROM samples WHERE at NOT IN (SELECT at FROM samples ORDER BY at DESC LIMIT ?)",
            (MAX_ROWS,),
        )


def collect(policy, now=None):
    global _previous
    now = time.time() if now is None else now
    prior = _previous[1] if _previous and 0 < now - _previous[0] <= 45 else None
    values, current, notes = measure(prior, disk=Path(policy.database).parent)
    _previous = (now, current)
    health = services()
    active_model = model(next(s for s in health if s["name"] == "model"))
    status = dict(
        collector="workbench-host-v1", model=active_model, services=health, metric_notes=notes
    )
    store(database(policy.database), int(now) // 15 * 15, values, status)


@contextmanager
def observations(policy, stopped):
    """One bounded sampler in the existing worker, including during long tasks."""
    if not policy or not getattr(policy, "host_observations", False):
        yield
        return
    closing_event = threading.Event()

    def sample():
        while not closing_event.is_set() and not stopped.is_set():
            try:
                collect(policy)
            except Exception:
                # This owner only reads host state and writes its own derived
                # metrics. A failure leaves an honest aged/missing observation.
                log.exception(
                    "Host observation unavailable; retained readings keep their original age"
                )
            closing_event.wait(INTERVAL)

    thread = threading.Thread(target=sample, name="workbench-host-observations", daemon=True)
    thread.start()
    try:
        yield
    finally:
        closing_event.set()
        thread.join(timeout=4)
