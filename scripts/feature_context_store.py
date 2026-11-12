"""Copy passive feature context during a stopped, same-user installation.

Includes unsubmitted drafts. This copies evidence, never approvals or executable
inputs. The destination must be the new installation's preparation output root.
"""

import argparse
import hashlib
import json
from pathlib import Path
import stat

from workbench.feature_context_storage import publish, read
from workbench.trusted_preparation import owned

MAX_FILE = 4 * 1024 * 1024
MAX_TOTAL = 1024 * 1024 * 1024
MAX_ENTRIES = 100000


def snapshot(root):
    owned(root)
    if not root.is_dir() or root.stat().st_mode & 0o077:
        raise ValueError('Feature context requires a private directory.')
    files, directories, total = {}, [], 0
    pending = [root]
    while pending:
        directory = pending.pop()
        for path in sorted(directory.iterdir()):
            owned(path)
            info = path.lstat()
            relative = path.relative_to(root).as_posix()
            if info.st_mode & 0o077:
                raise ValueError('Feature context has non-private permissions.')
            if stat.S_ISDIR(info.st_mode):
                directories.append(relative)
                pending.append(path)
            elif stat.S_ISREG(info.st_mode):
                data = read(path, MAX_FILE)
                total += len(data)
                files[relative] = dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
            else:
                raise ValueError('Feature context contains an unsupported filesystem entry.')
            if total > MAX_TOTAL or len(files) + len(directories) > MAX_ENTRIES:
                raise ValueError('Feature context exceeds this bounded transfer.')
    return dict(files=files, directories=sorted(directories), bytes=total)


def transfer(source_output, destination_output):
    source_output, destination_output = (owned(Path(p).absolute()) for p in (source_output, destination_output))
    if (source_output == destination_output or source_output in destination_output.parents
            or destination_output in source_output.parents):
        raise ValueError('Feature context transfer requires separate installation roots.')
    source, destination = (p / 'feature-context' for p in (source_output, destination_output))
    if not source.exists() and not source.is_symlink():
        if destination.exists() or destination.is_symlink():
            raise ValueError('The destination contains context absent from the source.')
        return dict(version=1, files=0, bytes=0, source_absent=True, exact_copy=True)
    before = snapshot(source)
    # Validate the whole source before creating anything at the destination.
    if destination.exists() or destination.is_symlink():
        snapshot(destination)
    destination.mkdir(mode=0o700, exist_ok=True)
    owned(destination)
    for relative in before['directories']:
        (destination / relative).mkdir(mode=0o700, exist_ok=True)
        owned(destination / relative)
    for relative in before['files']:
        publish(destination / relative, read(source / relative, MAX_FILE), limit=MAX_FILE)
    if snapshot(source) != before or snapshot(destination) != before:
        raise ValueError('Feature context changed during transfer or the destination differs.')
    binding = hashlib.sha256(json.dumps(before, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return dict(version=1, files=len(before['files']), bytes=before['bytes'],
                manifest_sha256=binding, source_absent=False, exact_copy=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source_output', type=Path)
    parser.add_argument('destination_output', type=Path)
    args = parser.parse_args()
    print(json.dumps(transfer(args.source_output, args.destination_output), sort_keys=True))
