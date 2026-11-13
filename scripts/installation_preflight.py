"""Prevalidate the existing private installer and retain an interrupted rollback.

The envelope is private, never a hosted artifact. It inventories the complete
site-specific inputs and existing install/rollback scripts; activation only runs
those already prepared scripts. No application reconstruction is allowed.
"""
import argparse
from contextlib import closing
from functools import wraps
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import subprocess

from everyday_store import fingerprint, require_settled
from release_bundle import verify

ROLES = {'installer', 'rollback', 'configuration', 'runtime', 'evidence_manifest', 'history'}
CONFIG_FILES = {'ops/everyday.json', 'ops/preparation.json', 'ops/api.env', 'ops/lan.env',
                'ops/worker.env', 'ops/codex-account-review.json', 'ops/tls/server.crt',
                'ops/tls/server.key', 'release/.local/task-runtime.json',
                'artifacts/draft-authority.json'}


def private(path):
    info = path.lstat()
    if (path.resolve() != path or not path.is_file() or info.st_uid != os.getuid()
            or info.st_mode & 0o077):
        raise ValueError('Installation dependency must be a private owned regular file: ' + str(path))
    return path


def read(path):
    return json.loads(private(path).read_text())


def file_entry(entry):
    path = private(Path(entry['path']))
    if sha256(path.read_bytes()).hexdigest() != entry['sha256']:
        raise ValueError('Installation dependency digest differs: ' + str(path))
    return path


def history(path):
    private(path)
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as c:
        c.execute('BEGIN')
        require_settled(c)
        return fingerprint(c)


def rollback_ready(envelope):
    entries = envelope['files']
    required = {envelope['rollback_script'], envelope['rollback_store']}
    rollback = [e for e in entries if e['role'] == 'rollback']
    if not required <= {e['path'] for e in rollback}:
        raise ValueError('Incomplete rollback prerequisites')
    for entry in rollback:
        file_entry(entry)
    if history(Path(envelope['rollback_store'])) != envelope['history']:
        raise ValueError('Rollback history differs')
    subprocess.run([envelope['python'], envelope['seal_script'], 'verify-active',
                    envelope['rollback_stage']], check=True, stdout=subprocess.DEVNULL)


def preflight(path):
    envelope = read(path)
    if envelope['version'] != 1 or {e['role'] for e in envelope['files']} != ROLES:
        raise ValueError('Complete installation dependency roles are required')
    indexed = {e['path']: e for e in envelope['files']}
    if len(indexed) != len(envelope['files']):
        raise ValueError('Duplicate installation dependency')
    for field, role in [('activation_script', 'installer'), ('configuration', 'configuration'),
                        ('rejection_manifest', 'evidence_manifest'), ('store', 'history')]:
        if envelope[field] not in indexed or indexed[envelope[field]]['role'] != role:
            raise ValueError('Missing required installation input: ' + field)
    for entry in envelope['files']:
        file_entry(entry)
    stage = Path(envelope['release']).parent
    if not {str(stage / name) for name in CONFIG_FILES} <= set(indexed):
        raise ValueError('Required runtime configuration dependency is missing')
    if envelope['seal_script'] != str(Path(envelope['release']) / 'ops/acceptance_seal.py'):
        raise ValueError('The accepted application must own installation sealing')
    externals = envelope['external_files']
    if not {envelope['python'], '/usr/bin/bash'} <= {e['path'] for e in externals}:
        raise ValueError('Pinned installer executables are required')
    for entry in externals:
        dependency = Path(entry['path'])
        if (str(dependency.resolve()) != entry['resolved'] or not dependency.is_file()
                or dependency.stat().st_mode & 0o022
                or sha256(dependency.read_bytes()).hexdigest() != entry['sha256']):
            raise ValueError('External runtime dependency differs: ' + str(dependency))
    acceptance = read(Path(envelope['acceptance']))
    if (acceptance.get('full_release_acceptance') is not True
            or acceptance['commit'] != envelope['commit']
            or acceptance['application_sha256'] != envelope['application_sha256']):
        raise ValueError('Exact hosted application acceptance is required')
    # Validate all accepted bytes again. Secrets/configuration live outside these.
    verify(Path(envelope['application']), acceptance['application_sha256'],
           envelope['commit'], Path(envelope['release']))
    configuration = read(Path(envelope['configuration']))
    if configuration['candidate'] != envelope['commit'] or configuration['schema'] != envelope['history']['schema']:
        raise ValueError('Incompatible installation configuration')
    for name in configuration['dependencies']:
        if name not in indexed:
            raise ValueError('Configuration dependency is not inventoried: ' + name)
    if history(Path(envelope['store'])) != envelope['history']:
        raise ValueError('Prepared history differs')
    authority = read(stage / 'artifacts/draft-authority.json')
    old_authority = read(Path(envelope['rollback_stage']) / 'artifacts/draft-authority.json')
    if ({k: v for k, v in authority.items() if k != 'database_identity'} !=
            {k: v for k, v in old_authority.items() if k != 'database_identity'}
            or authority['database_identity'] != [Path(envelope['store']).stat().st_dev,
                                                 Path(envelope['store']).stat().st_ino]):
        raise ValueError('Original draft permissions or database identity differ')
    # Referenced evidence is never disposable, even when it resides in an old release.
    for name in envelope['evidence_roots']:
        root = Path(name)
        if root.resolve() != root or not root.is_dir() or root.stat().st_uid != os.getuid():
            raise ValueError('Historical evidence root is unavailable: ' + name)
    rejection = read(Path(envelope['rejection_manifest']))
    for item in rejection['files']:
        for stage in [Path(rejection['source_stage']), Path(envelope['release']).parent]:
            evidence = stage / 'artifacts/acceptance/rejected-results' / item['name']
            if evidence.resolve() != evidence or not evidence.is_file() or evidence.stat().st_nlink != 1:
                raise ValueError('Historical rejection evidence unavailable')
            if evidence.stat().st_size != item['bytes'] or sha256(evidence.read_bytes()).hexdigest() != item['sha256']:
                raise ValueError('Historical rejection evidence differs')
    rollback_ready(envelope)
    subprocess.run([envelope['python'], envelope['seal_script'], 'verify-active',
                    str(Path(envelope['release']).parent)], check=True, stdout=subprocess.DEVNULL)
    return envelope


def journal(path, value):
    temporary = path.with_suffix('.new')
    with open(temporary, 'w', opener=lambda p, flags: os.open(p, flags | os.O_NOFOLLOW, 0o600)) as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def single_installer(function):
    @wraps(function)
    def locked(path, *args, **kwargs):
        fd = os.open(path.with_name('installation.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return function(path, *args, **kwargs)
        finally:
            os.close(fd)
    return locked


@single_installer
def activate(path, rollback=False):
    path = path.absolute()
    receipt = path.with_name('installation-journal.json')
    envelope = read(path)
    binding = sha256(path.read_bytes()).hexdigest()
    if receipt.exists():
        prior = read(receipt)
        if prior['envelope_sha256'] != binding:
            raise ValueError('Interrupted installation belongs to another envelope')
        if not rollback:
            raise ValueError('Installation already attempted; inspect journal and use verified rollback')
    if rollback:
        rollback_ready(envelope)
        subprocess.run(['bash', envelope['rollback_script']], check=True)
        journal(receipt, dict(envelope_sha256=binding, state='rolled_back', history=envelope['history']))
        return
    # All failure paths before this durable intent leave running services untouched.
    preflight(path)
    journal(receipt, dict(envelope_sha256=binding, state='activating',
                          rollback_script=envelope['rollback_script']))
    try:
        subprocess.run(['bash', envelope['activation_script']], check=True)
        verify(Path(envelope['application']), envelope['application_sha256'],
               envelope['commit'], Path(envelope['release']))
    except BaseException:
        # SIGKILL/power loss leaves the durable intent for the explicit rollback entry.
        rollback_ready(envelope)
        subprocess.run(['bash', envelope['rollback_script']], check=True)
        journal(receipt, dict(envelope_sha256=binding, state='rolled_back', history=envelope['history']))
        raise
    journal(receipt, dict(envelope_sha256=binding, state='activated',
                          application_sha256=envelope['application_sha256']))


def cleanup_disposable(path, referenced):
    """Only remove an explicitly owned scratch copy with no historical references."""
    import shutil
    from evidence_retention import referenced as open_reference
    path = path.absolute()
    if path.resolve() != path or not path.is_dir() or path.stat().st_uid != os.getuid():
        raise ValueError('Disposable workspace ownership differs')
    for root in map(Path, referenced):
        if path == root or path in root.parents or root in path.parents:
            raise ValueError('Disposable workspace overlaps referenced evidence')
    if open_reference(path):
        raise ValueError('Disposable workspace is still in use')
    marker = read(path / '.disposable-installation.json')
    if marker != {'purpose': 'installation-rehearsal', 'evidence_retained': True}:
        raise ValueError('Disposable workspace lacks verified retention')
    shutil.rmtree(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['preflight', 'activate', 'rollback'])
    parser.add_argument('envelope', type=Path)
    args = parser.parse_args()
    if args.operation == 'preflight':
        value = preflight(args.envelope)
        print(json.dumps(dict(complete=True, commit=value['commit'], history=value['history'])))
    else:
        activate(args.envelope, rollback=args.operation == 'rollback')


if __name__ == '__main__':
    main()
