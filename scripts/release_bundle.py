"""The hosted application artifact: committed source plus the checked frontend.

Private configuration, runtime evidence and installed dependencies are separately
inventoried by installation preflight. Nothing is rebuilt during activation.
"""
import argparse
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = 'application-manifest.json'


def build(root, destination):
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    source = subprocess.check_output(['git', 'archive', commit], cwd=root)
    files = {}
    modes = {}
    with tarfile.open(fileobj=BytesIO(source)) as archive:
        for member in archive.getmembers():
            if member.isdir():
                continue
            if not member.isfile():
                raise ValueError('Hosted source must contain regular files only')
            files[member.name] = archive.extractfile(member).read()
            modes[member.name] = member.mode
    for path in sorted((root / 'frontend/dist').rglob('*')):
        if path.is_symlink():
            raise ValueError('Frontend links are not application artifacts')
        if path.is_file():
            name = path.relative_to(root).as_posix()
            files[name] = path.read_bytes()
            modes[name] = 0o644
    if 'frontend/dist/index.html' not in files:
        raise ValueError('Checked frontend is required')
    manifest = dict(version=1, commit=commit, python='3.14',
                    files={name: sha256(raw).hexdigest() for name, raw in sorted(files.items())},
                    runtime_dependencies=['requirements.lock', 'package-lock.json'])
    files[MANIFEST] = json.dumps(manifest, sort_keys=True).encode()
    modes[MANIFEST] = 0o644
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(destination, 'w:gz') as archive:
        for name, raw in sorted(files.items()):
            member = tarfile.TarInfo(name)
            member.size, member.mode = len(raw), modes[name]
            archive.addfile(member, BytesIO(raw))
    return dict(commit=commit, sha256=sha256(destination.read_bytes()).hexdigest(), files=len(files))


def verify(artifact, digest, commit, release=None):
    if sha256(artifact.read_bytes()).hexdigest() != digest:
        raise ValueError('Hosted application digest differs')
    with tarfile.open(artifact, 'r:gz') as archive:
        members = archive.getmembers()
        names = [m.name for m in members]
        if len(names) != len(set(names)) or any(not m.isfile() or m.name.startswith('/')
                or '..' in Path(m.name).parts for m in members):
            raise ValueError('Unsafe application inventory')
        manifest = json.loads(archive.extractfile(MANIFEST).read())
        if manifest['commit'] != commit or set(names) != {*manifest['files'], MANIFEST}:
            raise ValueError('Application source revision or inventory differs')
        for member in members:
            raw = archive.extractfile(member).read()
            if member.name != MANIFEST and sha256(raw).hexdigest() != manifest['files'][member.name]:
                raise ValueError('Application inventory digest differs')
            if release is not None:
                path = release / member.name
                if path.resolve() != path or path.read_bytes() != raw:
                    raise ValueError('Staged application differs: ' + member.name)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    print(json.dumps(build(ROOT, args.destination)))


if __name__ == '__main__':
    main()
