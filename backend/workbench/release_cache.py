"""Coalesce identical inactive browser copies; preserve every path and seal.

Only browser distribution files in private Workbench release stages are eligible.
No history, source, configuration, credentials, results or recovery files are pruned.
Current and the two latest complete browser copies keep independent inodes.
"""

from hashlib import file_digest
from pathlib import Path
import json
import os
import re
import shutil
import stat
from uuid import uuid4

STAGES = Path("/home/rich/TwichUI-Workbench/.local/m5-review")
STATE = Path("/home/rich/.local/share/TwichUI-Workbench/storage-retention")
TARGET_FREE = 8 << 30  # Soft working headroom; the hard reserve remains 4 GiB.


def digest(path):
    with path.open("rb") as f:
        return file_digest(f, "sha256").hexdigest()


def safe(path, directory=False):
    if path.resolve() != path:
        raise ValueError("Browser retention path contains a link")
    info = path.lstat()
    if (
        info.st_uid != os.getuid()
        or info.st_mode & 0o022
        or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
    ):
        raise ValueError("Browser retention ownership or file type changed")
    return info


def coalesce(source, target, state, protected=frozenset()):
    """Atomic replacement by byte/metadata-identical hard links; no missing path."""
    safe(source, True)
    safe(target, True)
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    safe(state, True)
    if state.stat().st_mode & 0o077:
        raise ValueError("Private retention journal required")
    if source.stat().st_dev != target.stat().st_dev:
        return 0
    # A private journal precedes changes. Temp links live outside sealed stages;
    # interruption leaves the original target path complete and readable.
    journal = state / ("browser-" + str(uuid4()) + ".jsonl")
    reclaimed = 0
    with open(journal, "x", opener=lambda p, flags: os.open(p, flags, 0o600)) as out:
        for p in sorted(source.rglob("*")):
            if not p.is_file() or p.is_symlink():
                continue
            q = target / p.relative_to(source)
            if not q.is_file() or q.is_symlink():
                continue
            a, b = safe(p), safe(q)
            if (b.st_dev, b.st_ino) in protected:
                continue  # An independently retained copy still owns these bytes.

            def same(x):
                return (x.st_size, stat.S_IMODE(x.st_mode), x.st_uid, x.st_gid, x.st_mtime_ns)

            def stable(x):
                return (x.st_dev, x.st_ino, *same(x))

            if a.st_ino == b.st_ino or same(a) != same(b):
                continue
            sha = digest(p)
            if digest(q) != sha:
                continue
            temporary = state / ("link-" + str(uuid4()))
            record = dict(
                source=str(p),
                target=str(q),
                sha256=sha,
                bytes=b.st_size,
                original_inode=b.st_ino,
                retained_inode=a.st_ino,
                all_paths_preserved=True,
                history_source_credentials_results_untouched=True,
            )
            out.write(json.dumps(record) + "\n")
            out.flush()
            os.fsync(out.fileno())
            try:
                os.link(p, temporary, follow_symlinks=False)
                # Guard intervening edits and changed ownership immediately
                # before replacement. The existing seal still verifies bytes.
                if (
                    stable(safe(p)) != stable(a)
                    or stable(safe(q)) != stable(b)
                    or digest(temporary) != sha
                ):
                    raise ValueError("Browser file changed during verified retention")
                os.replace(temporary, q)
                assert digest(q) == sha and same(q.stat()) == same(b)
                reclaimed += b.st_size if b.st_nlink == 1 else 0
            finally:
                temporary.unlink(missing_ok=True)
    return reclaimed


def maintain(policy, current):
    current = Path(current)
    if current.parent != STAGES or not re.fullmatch(
        r"everyday-outcome-[a-f0-9]{7,40}", current.name
    ):
        return 0  # Disposable previews cannot touch real release storage.
    safe(STAGES, True)
    safe(current, True)
    if shutil.disk_usage(current).free >= TARGET_FREE:
        return 0
    # Only explicit private installation stages, never accounting roots as write authority.
    stages = [
        p
        for p in STAGES.iterdir()
        if re.fullmatch(r"everyday-outcome-[a-f0-9]{7,40}", p.name)
        and p.is_dir()
        and not p.is_symlink()
        and (p / "browser").is_dir()
    ]
    keep = {current, *sorted(stages, key=lambda p: p.stat().st_mtime, reverse=True)[:3]}
    protected = {
        (info.st_dev, info.st_ino)
        for stage in keep
        for p in (stage / "browser").rglob("*")
        if p.is_file() and not p.is_symlink()
        for info in [p.stat()]
    }
    total = 0
    for stage in sorted(stages, key=lambda p: p.stat().st_mtime):
        if stage in keep:
            continue
        safe(stage, True)
        # Immutable source/target release inventories independently bind every
        # eligible binary. Different distributions are left intact.
        safe(current / "ops/bundle.sha256")
        safe(stage / "ops/bundle.sha256")
        source_seal = (current / "ops/bundle.sha256").read_text().splitlines()
        target_seal = set((stage / "ops/bundle.sha256").read_text().splitlines())
        browser = [line for line in source_seal if "  browser/" in line]
        if not browser or not set(browser) <= target_seal:
            continue
        # Already shared files need no repeated binary hashing or journal. This
        # keeps ordinary low-space monitoring cheap after successful retention.
        candidates = []
        for line in browser:
            _, rel = line.split("  ", 1)
            if not rel.startswith("browser/") or ".." in Path(rel).parts or Path(rel).is_absolute():
                raise ValueError("Unsafe browser inventory path")
            a, b = (current / rel).stat(), (stage / rel).stat()
            if (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino) and (
                b.st_dev,
                b.st_ino,
            ) not in protected:
                candidates.append(rel)
        if not candidates:
            continue
        for line in browser:
            sha, rel = line.split("  ", 1)
            if not rel.startswith("browser/") or ".." in Path(rel).parts:
                raise ValueError("Unsafe browser inventory path")
            if digest(current / rel) != sha or digest(stage / rel) != sha:
                raise ValueError("Retained browser differs from sealed inventory")
        total += coalesce(current / "browser", stage / "browser", STATE, protected)
        if shutil.disk_usage(current).free >= TARGET_FREE:
            break
    return total
