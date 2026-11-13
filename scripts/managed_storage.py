"""Lifecycle receipts for disposable copies, called by existing housekeeping.

Only directories allocated here are candidates. An old name, age, absent process,
or missing task is never ownership evidence. History and archive stores are not
managed pools. Unknown legacy data and failed/unfinished runs remain untouched.
"""

from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import time
from uuid import uuid4

KINDS = {"workspace", "test-scratch", "test-artifact", "release", "cache"}
MIN_AGE = 86400
KEEP = 2
MAX_ITEMS = 8
MAX_BYTES = 2 << 30
MAX_FILES = 200000
MAX_REGISTRY = 2048
MAX_SCAN = 32
PROCESSES = Path("/proc")


def index(state):
    path = state / "index"
    if not path.exists():
        return {}
    plain(path, False)
    if path.stat().st_size > 2 << 20:
        raise ValueError("Retention registry exceeds its read budget")
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or len(value) > MAX_REGISTRY:
        raise ValueError("Invalid retention registry")
    return value


def plain(path, directory=True):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError("Retention requires an exact path without links")
    s = path.lstat()
    if (
        s.st_uid != os.getuid()
        or s.st_mode & 0o022
        or not (stat.S_ISDIR(s.st_mode) if directory else stat.S_ISREG(s.st_mode))
    ):
        raise ValueError("Retention ownership or type differs")
    return s


def sync(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def save(path, value):
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".receipt-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(value, f, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
        sync(path.parent)
    finally:
        Path(name).unlink(missing_ok=True)


@contextmanager
def locked(pool, preview=False):
    plain(pool)
    state = pool / ".retention"
    if not preview:
        state.mkdir(mode=0o700, exist_ok=True)
    if plain(state).st_mode & 0o077:
        raise ValueError("Private retention receipts required")
    lock = state / "lock"
    if lock.exists():
        plain(lock, False)
    with open(lock, "r" if preview else "a", opener=lambda p, flags: os.open(p, flags, 0o600)) as f:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield state


def allocate(pool, kind, name=None):
    """New empty owned copy; never adopt an unknown existing directory."""
    pool = Path(pool)
    if kind not in KINDS:
        raise ValueError("Unknown disposable storage class")
    pool.mkdir(parents=True, mode=0o700, exist_ok=True)
    identity = str(uuid4())
    name = name or kind + "-" + identity
    if Path(name).name != name or name.startswith(".") or name in {"", ".", ".."}:
        raise ValueError("Expected one new directory name")
    with locked(pool) as state:
        catalog = index(state)
        if len(catalog) >= MAX_REGISTRY:
            raise ValueError("Retention registry is full; preserve existing copies")
        path = pool / name
        path.mkdir(mode=0o700)  # Exclusive allocation, never reuse.
        s = plain(path)
        receipt = dict(
            version=1,
            id=identity,
            path=str(path),
            kind=kind,
            state="active",
            device=s.st_dev,
            inode=s.st_ino,
            allocated_at=time.time(),
            closed_at=None,
        )
        save(state / (identity + ".json"), receipt)
        catalog[str(path)] = {k: receipt[k] for k in ("id", "kind", "state", "closed_at")}
        save(state / "index", catalog)
    return path


def tree(path, deadline=None, test_fixtures=False):
    """Bounded no-follow inventory; disposable bytes need no second archive."""
    result, allocated = {}, 0
    deadline = deadline or time.monotonic() + 20
    for directory, dirs, files in os.walk(path, followlinks=False):
        for name in sorted([*dirs, *files]):
            item = Path(directory) / name
            rel = str(item.relative_to(path))
            s = item.lstat()
            if len(result) >= MAX_FILES or time.monotonic() > deadline:
                raise ValueError("Retention inventory budget exceeded; copy preserved")
            if s.st_uid != os.getuid() or not (
                stat.S_ISDIR(s.st_mode) or stat.S_ISREG(s.st_mode) or stat.S_ISLNK(s.st_mode)
            ):
                raise ValueError("Unexpected owner or special file; copy preserved")
            # Model weights are never disposable cache or workspace data.
            if item.suffix.lower() in {".gguf", ".safetensors"}:
                invalid_fixture = False
                if (
                    test_fixtures
                    and item.suffix.lower() == ".gguf"
                    and s.st_size < 1 << 20
                    and stat.S_ISREG(s.st_mode)
                ):
                    with item.open("rb") as f:
                        header = f.read(24)
                    invalid_fixture = len(header) < 24 or header[:4] != b"GGUF"
                if not invalid_fixture:
                    raise ValueError("Model weights are outside automatic retention")
            if stat.S_ISLNK(s.st_mode):
                result[rel] = ["link", os.readlink(item)]
            elif stat.S_ISDIR(s.st_mode):
                result[rel] = ["directory", stat.S_IMODE(s.st_mode)]
            else:
                result[rel] = [
                    "file",
                    s.st_size,
                    s.st_mtime_ns,
                    s.st_ctime_ns,
                    stat.S_IMODE(s.st_mode),
                ]
                allocated += s.st_blocks * 512 if s.st_nlink == 1 else 0
    return result, allocated


def prune(path, expected, budget, deadline):
    """Descriptor-relative bounded unlink; retain unknown or changed material."""
    reclaimed = 0
    count = 0
    if os.unlink not in os.supports_dir_fd or os.stat not in os.supports_dir_fd:
        raise ValueError("Descriptor-relative deletion is unavailable")
    for directory, dirs, files, fd in os.fwalk(path, topdown=False, follow_symlinks=False):
        for name in [*files, *dirs]:
            if count >= 2048 or reclaimed >= budget or time.monotonic() > deadline:
                return reclaimed, False
            rel = str((Path(directory) / name).relative_to(path))
            s = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if s.st_uid != os.getuid() or rel not in expected:
                raise ValueError("Unexpected material during retention; remainder preserved")
            kind = expected[rel][0]
            if stat.S_ISDIR(s.st_mode) and kind == "directory":
                os.rmdir(name, dir_fd=fd)
            elif stat.S_ISREG(s.st_mode) and kind == "file":
                current = ["file", s.st_size, s.st_mtime_ns, s.st_ctime_ns, stat.S_IMODE(s.st_mode)]
                if current != expected[rel]:
                    raise ValueError("Disposable file changed before unlink")
                os.unlink(name, dir_fd=fd)
                reclaimed += s.st_blocks * 512 if s.st_nlink == 1 else 0
            elif (
                stat.S_ISLNK(s.st_mode)
                and kind == "link"
                and os.readlink(name, dir_fd=fd) == expected[rel][1]
            ):
                os.unlink(name, dir_fd=fd)
            else:
                raise ValueError("Disposable type differs before unlink")
            count += 1
    path.rmdir()
    return reclaimed, True


def record(pool, path, budget=None):
    path = Path(path)
    if path.parent != pool or path.name.startswith("."):
        raise ValueError("Disposable copy is outside its allocation pool")
    entry = index(pool / ".retention").get(str(path))
    if not entry:
        raise ValueError("Unique allocation receipt required")
    file = pool / ".retention" / (entry["id"] + ".json")
    size = plain(file, False).st_size
    if size > 32 << 20 or budget is not None and size > budget[0]:
        raise ValueError("Retention receipt read budget exceeded")
    if budget is not None:
        budget[0] -= size
    value = json.loads(file.read_text())
    if value["path"] != str(path) or any(value[k] != entry[k] for k in ("id", "kind")):
        raise ValueError("Retention index and receipt differ")
    if value["state"] != entry["state"] and (entry["state"], value["state"]) not in {
        ("active", "closed"),
        ("active", "failed"),
        ("closed", "removed"),
    }:
        raise ValueError("Retention lifecycle differs")
    if value["version"] != 1 or value["kind"] not in KINDS or file.stem != value["id"]:
        raise ValueError("Invalid allocation receipt")
    if value["state"] not in {"active", "failed", "closed", "removed"}:
        raise ValueError("Unknown disposable lifecycle")
    if value["state"] in {"closed", "removed"} and (
        type(value.get("closed_at")) not in (int, float)
        or not math.isfinite(value["closed_at"])
        or value["closed_at"] < 0
        or type(value.get("allocated_bytes")) is not int
        or value["allocated_bytes"] < 0
    ):
        raise ValueError("Invalid disposable closeout")
    return file, value


def evidence_digest(path):
    if plain(path, False).st_size > 32 << 20:
        raise ValueError("Retained result manifest exceeds its read budget")
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def close(path, success, evidence=None):
    """Only successful disposable copies close; failed evidence remains active.

    A non-test producer must retain its result/evidence outside this copy first.
    This does not turn a task result, database or original source into disposable data.
    """
    path = Path(path)
    with locked(path.parent) as state:
        file, value = record(path.parent, path)
        s = plain(path)
        if value["state"] != "active" or [s.st_dev, s.st_ino] != [value["device"], value["inode"]]:
            raise ValueError("Copy lifecycle or identity differs")
        if not success:
            value.update(state="failed", closed_at=time.time())
        else:
            if value["kind"] != "test-scratch":
                evidence = Path(evidence or "/nonexistent")
                plain(evidence, False)
                if evidence.is_relative_to(path.parent) or any(
                    (parent / ".retention").is_dir() for parent in evidence.parents
                ):
                    raise ValueError("Retained evidence must be outside managed disposable pools")
                value["evidence"] = dict(path=str(evidence), sha256=evidence_digest(evidence))
            entries, size = tree(path, test_fixtures=value["kind"] == "test-scratch")
            value.update(
                state="closed", closed_at=time.time(), entries=entries, allocated_bytes=size
            )
        save(file, value)
        catalog = index(state)
        catalog[str(path)] = {k: value[k] for k in ("id", "kind", "state", "closed_at")}
        save(state / "index", catalog)


def process_reference(path):
    needle = str(path).encode()
    for p in PROCESSES.iterdir():
        if not p.name.isdigit():
            continue
        try:
            if p.stat().st_uid != os.getuid():
                continue
            args = (p / "cmdline").read_bytes()
            if needle in args or needle in (p / "maps").read_bytes():
                return True
            for link in [p / "cwd", *(p / "fd").iterdir()]:
                try:
                    target = Path(os.readlink(link).removesuffix(" (deleted)"))
                    if target == path or target.is_relative_to(path):
                        return True
                except FileNotFoundError:
                    pass
        except FileNotFoundError, ProcessLookupError:
            pass
        except PermissionError:
            # Known nondumpable login/service-manager daemons are not data owners.
            # All other unreadable same-user processes conservatively hold cleanup.
            try:
                if (p / "comm").read_text().strip() in {
                    "systemd",
                    "(sd-pam)",
                    "sshd-session",
                } and needle not in (p / "cmdline").read_bytes():
                    continue
            except OSError, ProcessLookupError:
                pass
            return True
    return False


@contextmanager
def history_guard(databases, write=False):
    """Brief writer reservation prevents a new saved reference during pruning.

    No rows or schema are written. Read failures/busy/oversized stores hold cleanup.
    """
    connections = []
    bodies = []
    budget = 64 << 20
    deadline = time.monotonic() + 3
    try:
        for path in sorted(set(map(Path, databases))):
            plain(path, False)
            c = sqlite3.connect(
                path.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True, timeout=0.1
            )
            connections.append(c)
            c.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            c.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            tables = c.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            for (name,) in tables:
                quoted = '"' + name.replace('"', '""') + '"'
                for row in c.execute("SELECT * FROM " + quoted):
                    for cell in row:
                        if isinstance(cell, (str, bytes)):
                            data = cell.encode() if isinstance(cell, str) else cell
                            budget -= len(data)
                            if budget < 0 or time.monotonic() > deadline:
                                raise ValueError("History reference scan budget exceeded")
                            bodies.append(data)
                            try:
                                decoded = (
                                    json.dumps(json.loads(data), ensure_ascii=False)
                                    .encode()
                                    .replace(b"\\/", b"/")
                                )
                            except ValueError, UnicodeDecodeError, TypeError:
                                continue
                            budget -= len(decoded)
                            if budget < 0:
                                raise ValueError("Decoded history reference budget exceeded")
                            bodies.append(decoded)
        if not write:
            for c in reversed(connections):
                c.rollback()
                c.close()
            connections.clear()
        yield bodies
    finally:
        for c in reversed(connections):
            c.rollback()
            c.close()


def overlap(path, roots):
    return any(
        path == p or path.is_relative_to(p) or p.is_relative_to(path) for p in map(Path, roots)
    )


def reason(path, value, protected, bodies, now, physical=None, deadline=None):
    physical = physical or path
    if value["state"] != "closed":
        return "unfinished or failed lifecycle"
    if now - value["closed_at"] < MIN_AGE:
        return "within retention age"
    if overlap(path, protected) or any(str(path).encode() in body for body in bodies):
        return "saved history, evidence, installation or rollback reference"
    s = plain(physical)
    if [s.st_dev, s.st_ino] != [value["device"], value["inode"]]:
        return "allocation identity changed"
    if process_reference(path) or physical != path and process_reference(physical):
        return "active or unreadable process reference"
    if "evidence" in value:
        p = Path(value["evidence"]["path"])
        plain(p, False)
        if evidence_digest(p) != value["evidence"]["sha256"]:
            return "retained evidence differs"
    actual, _ = tree(physical, deadline, value["kind"] == "test-scratch")
    expected = value["entries"]
    if (physical == path and actual != expected) or any(
        key not in expected or expected[key] != entry for key, entry in actual.items()
    ):
        return "copy changed after closeout"
    return None


def maintain(pools, protected=(), databases=(), preview=False, now=None):
    """Bounded oldest-first pass; same reference rules for preview and pruning."""
    now = time.time() if now is None else now
    result = dict(preview=preview, candidates=[], removed=[], deferred=[], reclaimed_bytes=0)
    result["in_progress"] = []
    planned_bytes = 0
    deadline = time.monotonic() + 8
    for pool in map(Path, pools):
        if not (pool / ".retention").exists():
            continue  # No registry means no authority, even for very old directories.
        try:
            plain(pool)
            plain(pool / ".retention")
            with locked(pool, preview) as state, history_guard(databases) as bodies:
                catalog = index(state)
                names = sorted(catalog)
                cursor_file = state / "cursor"
                if cursor_file.exists():
                    plain(cursor_file, False)
                cursor = cursor_file.read_text() if cursor_file.exists() else ""
                names = [n for n in names if n > cursor] + [n for n in names if n <= cursor]
                records = []
                read_budget = [64 << 20]
                for name in names[:MAX_SCAN]:
                    try:
                        entry, v = record(pool, Path(name), read_budget)
                    except (OSError, ValueError, KeyError, TypeError) as error:
                        result["deferred"].append(
                            dict(path=name, reason="Invalid copy receipt: " + type(error).__name__)
                        )
                        continue
                    plain(entry, False)
                    if entry.stat().st_size > 32 << 20:
                        raise ValueError("Registry read budget exceeded; copies preserved")
                    path = Path(v["path"])
                    metadata = {k: v[k] for k in ("id", "kind", "state", "closed_at")}
                    if catalog[name] != metadata:
                        catalog[name] = metadata
                        if not preview:
                            save(state / "index", catalog)
                    if path.parent != pool or entry.stem != v["id"] or v["kind"] not in KINDS:
                        raise ValueError("Registry boundary differs")
                    physical = (
                        path if path.exists() else pool / ".retention" / (".discard-" + v["id"])
                    )
                    if physical.exists():
                        records.append((entry, path, physical, v))
                    elif v["state"] in {"closed", "removed"} and not preview:
                        if v["state"] == "closed":
                            v.update(
                                state="removed",
                                removed_at=now,
                                already_absent_after_intent=(entry.with_suffix(".intent").exists()),
                            )
                            save(entry, v)
                        catalog.pop(name)
                        save(state / "index", catalog)
                if not preview and names:
                    fd = os.open(
                        cursor_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600
                    )
                    with os.fdopen(fd, "w") as f:
                        f.write(names[:MAX_SCAN][-1])
                        f.flush()
                        os.fsync(f.fileno())
                keep = set()
                for kind in KINDS:
                    closed = sorted(
                        (
                            v
                            for v in catalog.values()
                            if v["kind"] == kind and v["state"] == "closed"
                        ),
                        key=lambda v: v["closed_at"],
                        reverse=True,
                    )
                    keep.update(v["id"] for v in closed[:KEEP])
                for entry, path, physical, v in sorted(
                    records, key=lambda item: item[3].get("closed_at") or now
                ):
                    if time.monotonic() > deadline:
                        result["deferred"].append(
                            dict(path=str(path), reason="bounded pass time budget")
                        )
                        break
                    why = (
                        "retained recent copy"
                        if v["id"] in keep
                        else reason(
                            path,
                            v,
                            protected,
                            bodies,
                            now,
                            physical,
                            min(deadline, time.monotonic() + 2),
                        )
                    )
                    if not databases and v["kind"] != "test-scratch":
                        why = "history source required"
                    if why:
                        result["deferred"].append(dict(path=str(path), reason=why))
                        continue
                    if len(result["candidates"]) >= MAX_ITEMS or planned_bytes >= MAX_BYTES:
                        result["deferred"].append(
                            dict(path=str(path), reason="bounded pass budget")
                        )
                        continue
                    planned = min(v["allocated_bytes"], MAX_BYTES - planned_bytes)
                    result["candidates"].append(dict(path=str(path), kind=v["kind"], bytes=planned))
                    planned_bytes += planned
                    if preview:
                        continue
                    # Durable intent and final reference/inventory recheck precede any unlink.
                    save(
                        entry.with_suffix(".intent"),
                        dict(
                            path=str(path),
                            id=v["id"],
                            at=now,
                            retained_evidence=v.get("evidence"),
                            references_checked=True,
                        ),
                    )
                    with history_guard(databases, write=True) as fresh:
                        if reason(path, v, protected, fresh, now, physical, time.monotonic() + 2):
                            raise ValueError("Disposable copy acquired a reference or changed")
                        if physical == path:
                            physical = pool / ".retention" / (".discard-" + v["id"])
                            if physical.exists():
                                raise ValueError("Unexpected retention quarantine")
                            os.rename(path, physical)
                            sync(pool)
                            sync(physical.parent)
                    # The short writer reservation ends after rename, before recursive
                    # deletion. New producers cannot claim the closed, absent path.
                    # A crash resumes only an unchanged subset of the known inventory.
                    if process_reference(physical):
                        raise ValueError("Quarantined copy is still in use")
                    reclaim, complete = prune(
                        physical, v["entries"], MAX_BYTES - result["reclaimed_bytes"], deadline
                    )
                    result["reclaimed_bytes"] += reclaim
                    if not complete:
                        result["in_progress"].append(str(path))
                        continue
                    sync(pool)
                    v.update(state="removed", removed_at=now)
                    save(entry, v)
                    catalog.pop(str(path))
                    save(state / "index", catalog)
                    result["removed"].append(str(path))
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            result["deferred"].append(
                dict(pool=str(pool), reason=type(error).__name__ + ": " + str(error))
            )
    return result
