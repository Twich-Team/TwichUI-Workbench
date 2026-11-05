"""Explicit local copy/verify/prune, lookup and restore for CLOSED evidence trees.

No automatic retention or application integration. The operator must first check
ownership/references and obtain authorization for each source directory.
"""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile

from project_env import ARTIFACT_LIMIT, ROOT, artifact_bytes

ARCHIVES = Path.home() / '.local/share/TwichUI-Workbench/evidence-archives'
INDEX = 'artifacts/archive-index'


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def plain(path):
    """Reject links in every existing component, including destination ancestors."""
    for part in [path, *path.parents]:
        if part.is_symlink():
            raise ValueError(f'Symlink refused: {part}')


def relative(value):
    path = Path(value)
    if path.is_absolute() or '..' in path.parts or str(path) != value:
        raise ValueError(f'Expected canonical relative path: {value}')
    return path


def inventory(base, roots, missing=False):
    entries = {}
    for name in roots:
        path = base / relative(name)
        plain(path)
        if not path.exists() and missing:
            continue
        if not path.is_dir():
            raise ValueError(f'Expected closed directory: {path}')
        def unreadable(error):
            raise error

        for current, dirs, files in os.walk(path, followlinks=False, onerror=unreadable):
            for item in [Path(current), *(Path(current) / n for n in files)]:
                info = item.lstat()
                kind = 'directory' if stat.S_ISDIR(info.st_mode) else 'file'
                if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                    raise ValueError(f'Nonregular evidence refused: {item}')
                entry = dict(kind=kind, mode=stat.S_IMODE(info.st_mode), mtime_ns=info.st_mtime_ns)
                if kind == 'file':
                    entry.update(size=info.st_size, sha256=digest(item))
                entries[str(item.relative_to(base))] = entry
            for name in dirs:
                plain(Path(current) / name)
    return dict(sorted(entries.items()))


def bytes_match(actual, expected):
    return all(actual.get(k) == expected.get(k) for k in ('kind', 'size', 'sha256'))


def verify(base, manifest, missing=False):
    actual = inventory(base, manifest['roots'], missing=missing)
    expected = manifest['entries']
    if not missing and actual.keys() != expected.keys():
        raise ValueError(f'Archive inventory mismatch: {base}')
    for name, entry in actual.items():
        if name not in expected or not bytes_match(entry, expected[name]):
            raise ValueError(f'Evidence mismatch: {base / name}')
        if not missing and any(entry[k] != expected[name][k] for k in ('mode', 'mtime_ns')):
            raise ValueError(f'Evidence metadata mismatch: {base / name}')
    return actual


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    plain(path)
    fd, temporary = tempfile.mkstemp(prefix='.json-', dir=path.parent)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    sync_dir(path.parent)


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def locked(root):
    path = root / '.local/evidence-archive.lock'
    plain(path)
    path.parent.mkdir(exist_ok=True, mode=0o700)
    with path.open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def copy_entries(source, destination, manifest, staging=None):
    for name, entry in manifest['entries'].items():
        target = destination / name
        plain(target)
        if entry['kind'] == 'directory':
            target.mkdir(parents=True, exist_ok=True, mode=0o700)
            continue
        if target.exists():
            if not target.is_file() or target.stat().st_size != entry['size'] or digest(target) != entry['sha256']:
                raise ValueError(f'Refusing to overwrite existing bytes: {target}')
            continue
        # Partial copies stay outside data; reruns copy missing files only.
        partial = staging or destination.parent / 'partial'
        plain(partial)
        partial.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(prefix='copy-', dir=partial)
        with os.fdopen(fd, 'wb') as output, (source / name).open('rb') as input_stream:
            shutil.copyfileobj(input_stream, output)
            output.flush()
            os.fsync(output.fileno())
        temporary = Path(temporary)
        if temporary.stat().st_size != entry['size'] or digest(temporary) != entry['sha256']:
            raise ValueError(f'Source changed during copy: {source / name}')
        os.chmod(temporary, entry['mode'])
        os.utime(temporary, ns=(entry['mtime_ns'], entry['mtime_ns']))
        with temporary.open('rb') as stream:
            os.fsync(stream.fileno())
        os.link(temporary, target)  # Create only: never replace a concurrent file.
        temporary.unlink()
        sync_dir(target.parent)
    for name, entry in reversed(list(manifest['entries'].items())):
        if entry['kind'] == 'directory':
            target = destination / name
            os.chmod(target, entry['mode'])
            os.utime(target, ns=(entry['mtime_ns'], entry['mtime_ns']))
            sync_dir(target)


def load(root, archive_id):
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}', archive_id):
        raise ValueError('Invalid archive ID')
    receipt = root / INDEX / f'{archive_id}.json'
    plain(receipt)
    info = json.loads(receipt.read_text())
    bundle = Path(info['archive'])
    plain(bundle / 'manifest.json')
    if digest(bundle / 'manifest.json') != info['manifest_sha256']:
        raise ValueError('Archive manifest hash mismatch')
    manifest = json.loads((bundle / 'manifest.json').read_text())
    if manifest['workspace'] != str(root):
        raise ValueError('Archive belongs to another workspace')
    for name in manifest['entries']:
        relative(name)
        if not any(Path(name).is_relative_to(relative(r)) for r in manifest['roots']):
            raise ValueError('Manifest path outside source roots')
    return bundle, manifest


def archive(root, storage, archive_id, sources, remove=False):
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}', archive_id):
        raise ValueError('Invalid archive ID')
    sources = sorted(set(sources))
    if not sources:
        raise ValueError('Explicit closed source directories required')
    for source in sources:
        path = relative(source)
        if not path.is_relative_to('artifacts/runs') or len(path.parts) < 3:
            raise ValueError('Only explicit artifacts/runs descendants can be archived')
        if any(path != Path(other) and path.is_relative_to(other) for other in sources):
            raise ValueError('Overlapping source directories refused')
    plain(storage)
    if storage.is_relative_to(root):
        raise ValueError('Archive storage must be outside the workspace')
    bundle = storage / archive_id
    plain(bundle)
    bundle.mkdir(parents=True, exist_ok=True, mode=0o700)
    manifest_path = bundle / 'manifest.json'
    plain(manifest_path)
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if manifest['workspace'] != str(root) or manifest['roots'] != sources:
            raise ValueError('Archive ID already belongs to different sources')
        for name in manifest['entries']:
            relative(name)
            if not any(Path(name).is_relative_to(r) for r in sources):
                raise ValueError('Manifest path outside source roots')
        verify(root, manifest, missing=True)
    else:
        manifest = dict(version=1, workspace=str(root), roots=sources,
                        entries=inventory(root, sources))
        atomic_json(manifest_path, manifest)
    data = bundle / 'data'
    plain(data)
    data.mkdir(exist_ok=True, mode=0o700)
    copy_entries(root, data, manifest)
    verify(data, manifest)
    verify(root, manifest, missing=True)
    receipt = dict(archive=str(bundle), manifest_sha256=digest(manifest_path))
    index = root / INDEX / f'{archive_id}.json'
    plain(index)
    if index.exists() and json.loads(index.read_text()) != receipt:
        raise ValueError('Existing archive receipt differs')
    atomic_json(index, receipt)  # Durable mapping precedes ANY removal.
    if remove:
        for name, entry in manifest['entries'].items():
            path = root / name
            plain(path)
            if entry['kind'] == 'file' and path.exists():
                if path.stat().st_size != entry['size'] or digest(path) != entry['sha256']:
                    raise ValueError(f'Source changed before removal: {path}')
                path.unlink()
                sync_dir(path.parent)
        for name, entry in reversed(list(manifest['entries'].items())):
            path = root / name
            plain(path)
            if entry['kind'] == 'directory' and path.exists():
                path.rmdir()  # Never recursively remove unexpected files.
                sync_dir(path.parent)
    verify(data, manifest)
    return dict(archive_id=archive_id, files=sum(e['kind'] == 'file' for e in manifest['entries'].values()),
                bytes=sum(e.get('size', 0) for e in manifest['entries'].values()),
                originals_removed=remove, receipt=str(index))


def restore(root, archive_id, destination):
    bundle, manifest = load(root, archive_id)
    verify(bundle / 'data', manifest)
    plain(destination)
    surviving = verify(destination, manifest, missing=True)
    if destination == root:
        required = sum(e.get('size', 0) for n, e in manifest['entries'].items() if n not in surviving)
        if artifact_bytes(root) + required > ARTIFACT_LIMIT:
            raise ValueError('Restore would exceed the counted evidence budget; use an isolated destination')
    copy_entries(bundle / 'data', destination, manifest,
                 staging=destination / '.local/evidence-restore-partials')
    verify(destination, manifest)


def lookup(root, original):
    relative(original)
    for receipt in sorted((root / INDEX).glob('*.json')):
        bundle, manifest = load(root, receipt.stem)
        entry = manifest['entries'].get(original)
        if entry and entry['kind'] == 'file':
            path = bundle / 'data' / original
            plain(path)
            if path.stat().st_size != entry['size'] or digest(path) != entry['sha256']:
                raise ValueError(f'Archived evidence mismatch: {original}')
            return dict(original=original, archive_id=receipt.stem, path=str(path), **entry)
    raise ValueError('Original path is not indexed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('archive')
    create.add_argument('archive_id')
    create.add_argument('sources', nargs='+', help='Authorized CLOSED workspace-relative directories')
    create.add_argument('--remove-originals', action='store_true')
    for command in ('verify', 'restore'):
        sub = commands.add_parser(command)
        sub.add_argument('archive_id')
        if command == 'restore':
            sub.add_argument('--destination', type=Path, default=ROOT,
                             help='Workspace root or isolated restore root; never overwrites different files')
    commands.add_parser('lookup').add_argument('original')
    commands.add_parser('status')
    args = parser.parse_args()
    try:
        with locked(ROOT):
            if args.command == 'archive':
                result = archive(ROOT, ARCHIVES, args.archive_id, args.sources, args.remove_originals)
            elif args.command == 'lookup':
                result = lookup(ROOT, args.original)
            elif args.command == 'status':
                used = artifact_bytes()
                result = dict(counted_bytes=used, limit_bytes=ARTIFACT_LIMIT,
                              headroom_bytes=ARTIFACT_LIMIT - used)
            elif args.command == 'restore':
                restore(ROOT, args.archive_id, args.destination.absolute())
                result = dict(restored_to=str(args.destination.absolute()))
            else:
                bundle, manifest = load(ROOT, args.archive_id)
                verify(bundle / 'data', manifest)
                result = dict(verified=args.archive_id)
        print(json.dumps(result, indent=2))
    except (ValueError, OSError) as error:
        parser.exit(2, f'BLOCKED: {error}\n')


if __name__ == '__main__':
    main()
