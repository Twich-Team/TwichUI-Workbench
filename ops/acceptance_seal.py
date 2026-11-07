"""Verify the private release's exact immutable files and symbolic links."""
import argparse
from hashlib import file_digest
import json
import os
from pathlib import Path
import stat


SEAL = 'ops/bundle.sha256'
LINKS = 'ops/symlinks.json'
RUNTIME_FILES = {
    'ops/everyday.json', 'ops/preparation.json', 'ops/api.env', 'ops/lan.env', 'ops/worker.env',
    'ops/codex-account-review.json', 'ops/tls/server.crt', 'ops/tls/server.key',
    'release/.local/task-runtime.json', 'release/.local/everyday-worker.json', 'release/.local/everyday-worker.lock',
}
RUNTIME_DIRS = {'artifacts', 'scratch', 'release/artifacts/runs'}


def inventory(stage, active):
    files, links = {}, {}
    for folder, dirs, names in os.walk(stage, followlinks=False):
        for name in sorted([*dirs, *names]):
            path = Path(folder) / name
            rel = path.relative_to(stage).as_posix()
            if rel in RUNTIME_DIRS:
                if path.is_symlink() or not path.is_dir():
                    raise ValueError('Unexpected runtime path type')
                if not active and any(file.is_file() or file.is_symlink() for file in path.rglob('*')):
                    raise ValueError('Dormant release contains runtime files')
                dirs.remove(name)
                continue
            if rel == SEAL or (active and rel in RUNTIME_FILES):
                continue
            if path.is_symlink():
                links[rel] = os.readlink(path)
                if name in dirs:
                    dirs.remove(name)
            elif path.is_file():
                info = path.stat()
                if info.st_uid != os.getuid() or info.st_mode & 0o022:
                    raise ValueError('Release file ownership or mode differs')
                with path.open('rb') as stream:
                    files[rel] = file_digest(stream, 'sha256').hexdigest()
            elif not path.is_dir():
                raise ValueError('Unexpected release file type')
    return files, links


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['create', 'verify', 'verify-active'])
    parser.add_argument('stage', type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    root = args.stage
    info = root.lstat()
    if root.resolve() != root or info.st_uid != os.getuid() or not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077:
        raise ValueError('Release root must be an exact private owned directory')
    if args.operation == 'create':
        _, links = inventory(root, False)
        with (root / LINKS).open('x') as f:
            json.dump(links, f, indent=2, sort_keys=True)
            f.write('\n')
    files, links = inventory(root, args.operation == 'verify-active')
    if args.operation == 'create':
        with (root / SEAL).open('x') as f:
            for path, sha in sorted(files.items()):
                f.write(f'{sha}  {path}\n')
    else:
        expected = dict(line.split('  ', 1)[::-1] for line in (root / SEAL).read_text().splitlines())
        if expected != files or json.loads((root / LINKS).read_text()) != links:
            raise ValueError('Sealed hashes or inventory differ')
    print(json.dumps(dict(files=len(files), symlinks=len(links),
                          logical_bytes=sum((root / p).stat().st_size for p in files))))


if __name__ == '__main__':
    main()
