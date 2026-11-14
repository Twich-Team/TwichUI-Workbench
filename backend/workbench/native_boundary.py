"""Qualified native workspace boundary, promoted from the November 14 probes.

Runtime writes are disposable. Only a settled, fully scanned, scoped export can
become a candidate. No task authorization, scheduler or publication owner here.
"""
import hashlib
import json
import os
from pathlib import Path
import stat
import time

CLI = Path('/home/rich/.local/bin/codex').resolve()
CLI_SHA256 = 'b8a11fedf24b2dfb59a70c9e52b030f9a508dcd49cd2dd0d41956f4c785156b8'
RESERVED = ('.git', '.codex', '.agents', '.aws')
BINARY_HASHES = {
    str(CLI): CLI_SHA256,
    str(CLI.with_name('codex-code-mode-host')): '657f4e902726e05867c937b583049d5997e057f8cd0ab40450633c9c9ef8d630',
    str(CLI.with_name('codex-exec-server')): '79e4b41b564bc3190146389ecc95d6c05ba2a20d6503e025e5fcafb47db05849',
    str(CLI.parent.parent / 'codex-resources/bwrap'): '77360cb751ccedc5971391444ac86a8a33c15b04d6b4a6fe45f5d25496e62c4c',
    '/usr/bin/bwrap': '523da3e7399044be5163aee6f57a77a6bef7454376e28f0a0627920bae1b76b6',
}


def binaries_valid():
    try:
        for path, expected in BINARY_HASHES.items():
            with open(path, 'rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
                    return False
        return True
    except OSError:
        return False

def profile(control, readable, writable):
    for path in (control, *readable, *writable):
        assert path.is_absolute() and not path.is_symlink()
    features = dict(multi_agent=False, multi_agent_v2=False, hooks=False,
                    plugins=False, remote_plugin=False, plugin_discovery_prefetch=False,
                    skill_search=False, skill_mcp_dependency_install=False,
                    skip_host_skill_discovery=True, enable_mcp_apps=False,
                    shell_snapshot=False, use_legacy_landlock=False,
                    code_mode=False, code_mode_host=True)
    fs = {':minimal': 'read', str(CLI): 'read', str(CLI.with_name('codex-code-mode-host')): 'read'}
    fs.update({str(path): 'read' for path in readable})
    fs.update({str(path): 'write' for path in writable})
    for path in writable:
        if (path / '.git').is_dir():
            for name in ('.git', '.codex', '.agents', '.aws'):
                (path / name).mkdir(exist_ok=True)
                fs[str(path / name)] = 'read'
    config = dict(default_permissions='boundary', approval_policy='never',
                  web_search='disabled', features=features,
                  permissions={'boundary': {'filesystem': fs, 'network': {'enabled': False}}})
    def toml(value):
        if isinstance(value, dict):
            return '{' + ','.join(json.dumps(k) + '=' + toml(v) for k, v in value.items()) + '}'
        return json.dumps(value)
    (control / 'config.toml').write_text('\n'.join(k + '=' + toml(v) for k, v in config.items()) + '\n')
    return config, [arg for k, v in config.items() for arg in ('-c', k + '=' + toml(v))]


def environment(control, scratch):
    return {'PATH': '/usr/bin:/bin', 'HOME': str(control), 'CODEX_HOME': str(control),
            'TMPDIR': str(scratch), 'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1',
            'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_OPTIONAL_LOCKS': '0'}


def scan(root, *, file_bytes=4 << 20, total_bytes=32 << 20):
    """Never follow links, trust Git's changed-file list or execute candidate code."""
    entries, contents = {}, {}
    visited, total = 0, 0
    deadline = time.monotonic() + 10
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    device = os.fstat(root_fd).st_dev
    def visit(fd, prefix):
        nonlocal visited, total
        if prefix.count('/')>16:
            raise ValueError('directory depth budget')
        names=[]
        with os.scandir(fd) as iterator:
            for entry in iterator:
                visited+=1
                if visited>4096 or time.monotonic()>deadline:
                    raise ValueError('entry or scan time budget')
                names.append(entry.name)
        for name in sorted(names):
            if name in ('.', '..') or '/' in name:
                raise ValueError('invalid path')
            path = prefix + name
            if len(name.encode())>240 or len(path.encode())>2048:
                raise ValueError('path budget')
            observed = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if observed.st_dev != device or observed.st_mode & (stat.S_ISUID | stat.S_ISGID):
                raise ValueError('device or privileged mode: ' + path)
            if stat.S_ISDIR(observed.st_mode):
                entries[path] = dict(kind='directory')
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    current = os.fstat(child)
                    if (current.st_dev, current.st_ino) != (observed.st_dev, observed.st_ino):
                        raise ValueError('directory changed during scan')
                    visit(child, path + '/')
                finally:
                    os.close(child)
            elif stat.S_ISREG(observed.st_mode):
                if observed.st_nlink != 1 or observed.st_size > file_bytes:
                    raise ValueError('shared inode or oversized file: ' + path)
                total+=observed.st_size
                if total>total_bytes:
                    raise ValueError('total content budget')
                child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                try:
                    current = os.fstat(child)
                    if (current.st_dev, current.st_ino) != (observed.st_dev, observed.st_ino):
                        raise ValueError('file changed during scan')
                    data = b''
                    while chunk := os.read(child, 65536):
                        data += chunk
                        if len(data) > file_bytes:
                            raise ValueError('file grew during scan')
                    after = os.fstat(child)
                    if (current.st_size,current.st_mtime_ns,current.st_ctime_ns,current.st_nlink) != (after.st_size,after.st_mtime_ns,after.st_ctime_ns,after.st_nlink):
                        raise ValueError('file changed during scan')
                finally:
                    os.close(child)
                entries[path] = dict(kind='file', sha256=hashlib.sha256(data).hexdigest(), executable=bool(observed.st_mode & 0o111))
                contents[path] = data
            else:
                raise ValueError('symlink or non-regular file: ' + path)
    try:
        visit(root_fd, '')
    finally:
        os.close(root_fd)
    if '.git/objects/info/alternates' in entries:
        raise ValueError('Git alternates forbidden')
    return entries, contents


def export(source, baseline, approved, destination):
    inventory, contents = scan(source)
    changes = sorted(path for path in set(baseline) | set(inventory)
                     if baseline.get(path) != inventory.get(path))
    if not set(changes) <= set(approved):
        raise ValueError('unauthorized changes: ' + repr(changes))
    for path in changes:
        if path.split('/')[0] in ('.git','.codex','.agents','.aws'):
            raise ValueError('reserved path')
        if path in inventory and inventory[path]['kind'] != 'file':
            raise ValueError('candidate path must be a regular file')
        if path in baseline and path in inventory and baseline[path].get('executable') != inventory[path].get('executable'):
            raise ValueError('unapproved executable mode change')
    if destination.exists() or destination.is_symlink():
        raise ValueError('never reuse candidate destination')
    destination.mkdir(mode=0o700)
    frozen = {}
    for path in sorted(inventory):
        if path.split('/')[0] in ('.git','.codex','.agents','.aws') or inventory[path]['kind']=='directory':
            continue
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(contents[path])
            stream.flush()
            os.fsync(stream.fileno())
        target.chmod(0o500 if inventory[path]['executable'] else 0o400)
        frozen[path] = inventory[path]
    for path in sorted(destination.rglob('*'), reverse=True):
        if path.is_dir():
            path.chmod(0o500)
    destination.chmod(0o500)
    return dict(changes=changes, manifest=frozen,
                candidate_sha256=hashlib.sha256(json.dumps(frozen,sort_keys=True,separators=(',',':')).encode()).hexdigest())
