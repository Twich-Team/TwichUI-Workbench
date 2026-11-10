"""Physical-capacity checks and verified archival of closed development browsers.

The working-set threshold triggers housekeeping, never an assignment cap.
Runtime/history/recovery trees and unrelated files are never candidates.
"""
import hashlib
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import time

import evidence_archive as archive
from project_env import ROOT, artifact_bytes

SOFT_TARGET = 768 << 20
SOFT_TRIGGER = 1 << 30
RESERVE = 4 << 30


def referenced(path):
    """Any same-user open fd/cwd prevents pruning an evidence tree."""
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():
            continue
        try:
            if process.stat().st_uid != os.getuid():
                continue
            links = [process/'cwd', *list((process/'fd').iterdir())]
            for link in links:
                try:
                    target = Path(os.readlink(link).removesuffix(' (deleted)'))
                    if target.is_absolute() and target.is_relative_to(path):
                        return True
                except FileNotFoundError:
                    pass
        except FileNotFoundError:
            continue
        except PermissionError:
            return True
    return False


def closed_owned(root, path):
    archive.plain(path)
    if path.parent != root/'artifacts/runs' or not path.name.startswith('browser-'):
        return False
    if time.time()-path.stat().st_mtime < 3600 or referenced(path):
        return False
    for item in [path, *path.rglob('*')]:
        info = item.lstat()
        if (info.st_uid != os.getuid() or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
                or stat.S_ISREG(info.st_mode) and info.st_nlink != 1):
            return False
    return True


def archive_closed(root, path, storage):
    if not closed_owned(root, path):
        raise ValueError('Evidence is active, too recent, unowned or outside closed browser output.')
    sources = [str(path.relative_to(root))]
    size = sum(p.stat().st_size for p in path.rglob('*') if p.is_file())
    if min(shutil.disk_usage(p).free for p in [root, storage]) < RESERVE+size*2:
        raise RuntimeError('Insufficient physical space for archive and independent restore.')
    identity = 'auto-'+hashlib.sha256(str(root).encode()).hexdigest()[:10]+'-'+path.name
    copied = archive.archive(root, storage, identity, sources, remove=False)
    verification = Path(tempfile.mkdtemp(prefix='restore-proof-', dir=storage))
    archive.restore(root, identity, verification)
    bundle, manifest = archive.load(root, identity)
    archive.verify(verification, manifest)
    files = [name for name, entry in manifest['entries'].items() if entry['kind'] == 'file']
    for name in files:
        assert archive.lookup(root, name)['sha256'] == manifest['entries'][name]['sha256']
    if not closed_owned(root, path):
        raise ValueError('Evidence acquired an active reference before pruning; copies preserved.')
    archive.atomic_json(bundle/'retention-proof.json', dict(
        owner_authority='STANDING_OWNER_DIRECTION_AUTONOMOUS_DELIVERY.md',
        independent_restore_verified=True, original_paths_verified=len(files),
        ownership_and_open_references_checked=True, originals=sources))
    result = archive.archive(root, storage, identity, sources, remove=True)
    # This is only the verified temporary duplicate created by this operation.
    # The archive, lookup index and original/recovery evidence remain preserved.
    archive.verify(verification, manifest)
    shutil.rmtree(verification)
    return {**copied, **result, 'independent_restore_verified': True}


def maintain(root=ROOT, storage=archive.ARCHIVES):
    root = root.resolve()
    archive.plain(root)
    storage.mkdir(parents=True, exist_ok=True, mode=0o700)
    archive.plain(storage)
    used = artifact_bytes(root)
    archived = []
    deferred = False
    if used > SOFT_TRIGGER:
        with ExitStack() as stack:
            try:
                stack.enter_context(archive.locked(root))
            except BlockingIOError:
                deferred = True  # Another housekeeping owner is active; never clear its lock.
            if not deferred:
                candidates = sorted((root/'artifacts/runs').glob('browser-*'), key=lambda p:p.stat().st_mtime)
                for path in candidates:
                    if used <= SOFT_TARGET:
                        break
                    if closed_owned(root, path):
                        archived.append(archive_closed(root, path, storage))
                        used = artifact_bytes(root)
    physical = min(shutil.disk_usage(p).free for p in [root, storage, '/dev/shm'])
    if physical < RESERVE:
        raise RuntimeError('Actual physical/shared-memory recovery reserve is below 4 GiB.')
    return dict(counted_bytes=used, physical_free=physical, archived=archived, archival_deferred=deferred,
                soft_working_set_target=SOFT_TARGET, cumulative_delivery_cap=False)


if __name__ == '__main__':
    print(json.dumps(maintain(), indent=2))
