#!/bin/bash
# Owner-authenticated installation of the approved fixed portal policy API.
# Does not alter grants, firewall rules, services, application code or Traefik.
set -euo pipefail
export PATH=/usr/bin:/usr/sbin:/bin
source_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
source=$source_dir/workbench_admin.py
test "$EUID" = 1000
test ! -L "$source"
printf '%s  %s\n' '5654057602bf37945b3422d90be16fc8c002775776aede936c615821e1244318' "$source" | sha256sum -c --quiet
# This needs the owner's normal interactive sudo; it is outside the narrow grant.
sudo /usr/bin/python3 -I -B - "$source" <<'PY'
import ast
import fcntl
import hashlib
import os
from pathlib import Path
import stat
import sys

old_hash = '72ceb24137afba5ebab832a4bc15d3784f49090f7c3e67998d4a800ac6b0e0a4'
new_hash = '5654057602bf37945b3422d90be16fc8c002775776aede936c615821e1244318'
helper = Path('/usr/local/sbin/twichui-workbench-admin')
state = Path('/var/lib/twichui-workbench-admin')
grant = Path('/etc/twichui-workbench-admin/grant.json')
backup = state / 'helper-before-portal.py'
for directory in {helper.parent, state, grant.parent}:
    for path in (*reversed(directory.parents), directory):
        info = path.lstat()
        assert stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022, 'Untrusted root directory'
lock = os.open(state / 'lock', os.O_RDWR | os.O_NOFOLLOW)
try:
    info = os.fstat(lock)
    assert stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1 and not info.st_mode & 0o077
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def read(path, owner, private=False):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            assert (stat.S_ISREG(info.st_mode) and info.st_uid == owner and info.st_nlink == 1
                    and not info.st_mode & (0o077 if private else 0o022) and info.st_size <= 131072), 'Unsafe file'
            return stream.read(131073)

    def digest(value):
        return hashlib.sha256(value).hexdigest()

    old = read(helper, 0)
    new = read(Path(sys.argv[1]), 1000)
    assert digest(new) == new_hash, 'Replacement differs; no change made'
    compile(new, str(helper), 'exec')
    grant_before = read(grant, 0, private=True)
    if digest(old) == new_hash:
        assert digest(read(backup, 0)) == old_hash, 'Existing installation backup differs'
        print('Exact portal-capable helper already installed; no change made.')
        sys.exit(0)
    assert digest(old) == old_hash, 'Installed helper differs; no change made'
    functions = lambda source: {node.name: ast.dump(node) for node in ast.parse(source).body
                                if isinstance(node, ast.FunctionDef)}
    before, after = functions(old), functions(new)
    for name in ('privileged_main', 'check_grant', 'checked_file', 'protected_dir', 'audit', 'run',
                 'unit_bytes', 'require_settled', 'check_lan_address'):
        assert before[name] == after[name], 'Preserved boundary changed: ' + name

    def create(path, content):
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o700)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fchmod(stream.fileno(), 0o755)
            os.fsync(stream.fileno())

    create(backup, old)
    temporary = helper.parent / '.twichui-workbench-admin-portal'
    create(temporary, new)
    assert read(temporary, 0) == new and read(grant, 0, private=True) == grant_before
    for directory in (state, helper.parent):
        fd = os.open(directory, os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    os.replace(temporary, helper)
    fd = os.open(helper.parent, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    assert read(helper, 0) == new and read(backup, 0) == old and read(grant, 0, private=True) == grant_before
finally:
    os.close(lock)
print('Reviewed portal-capable helper installed. Prior helper backed up. Grant and all live settings unchanged.')
print('Codex must verify status in its existing approved session before using the new fixed portal operation.')
PY
