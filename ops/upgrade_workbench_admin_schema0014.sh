#!/bin/bash
# Owner terminal action: only add schema0014 to the installed settlement check.
# Does not create/extend a grant, change its session, or restart any service.
set -euo pipefail
export PATH=/usr/bin:/usr/sbin:/bin
source_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
source=$source_dir/workbench_admin.py
test "$EUID" = 1000
test ! -L "$source"
printf '%s  %s\n' '72ceb24137afba5ebab832a4bc15d3784f49090f7c3e67998d4a800ac6b0e0a4' "$source" | sha256sum -c --quiet
# This password-authorized copy is outside the narrow no-argument helper grant.
# The root program pins both byte strings and permits exactly one substitution.
sudo /usr/bin/python3 -I -B - "$source" <<'PY'
import fcntl
import hashlib
import os
from pathlib import Path
import stat

old_hash = 'd7c20bde1a7fe764caca764965a469ddf6f4516c709747da2984dd4085cabdae'
new_hash = '72ceb24137afba5ebab832a4bc15d3784f49090f7c3e67998d4a800ac6b0e0a4'
helper = Path('/usr/local/sbin/twichui-workbench-admin')
state = Path('/var/lib/twichui-workbench-admin')
grant = Path('/etc/twichui-workbench-admin/grant.json')
backup = state / 'helper-before-schema0014.py'
for directory in {helper.parent, state, grant.parent}:
    for p in (*reversed(directory.parents), directory):
        s = p.lstat()
        assert stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022, 'Untrusted root directory'
lock = os.open(state / 'lock', os.O_RDWR | os.O_NOFOLLOW)
try:
    s = os.fstat(lock)
    assert stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1 and not s.st_mode & 0o077
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    def read(path, owner):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as f:
            s = os.fstat(f.fileno())
            assert stat.S_ISREG(s.st_mode) and s.st_uid == owner and s.st_nlink == 1 and not s.st_mode & 0o022
            return f.read(131073)
    old = read(helper, 0)
    new = read(Path(__import__('sys').argv[1]), 1000)
    assert hashlib.sha256(old).hexdigest() == old_hash, 'Installed helper differs; no change made'
    assert hashlib.sha256(new).hexdigest() == new_hash, 'Replacement differs; no change made'
    before = b"([('0012',)], [('0013',)])"
    after = b"([('0012',)], [('0013',)], [('0014',)])"
    assert old.count(before) == 1 and old.replace(before, after) == new, 'Only schema compatibility may change'
    compile(new, str(helper), 'exec')
    grant_before = read(grant, 0)
    def create(path, content):
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o700)
        with os.fdopen(fd, 'wb') as f:
            f.write(content)
            f.flush()
            os.fchmod(f.fileno(), 0o755)
            os.fsync(f.fileno())
    create(backup, old)
    temporary = helper.parent / '.twichui-workbench-admin-schema0014'
    create(temporary, new)
    assert read(temporary, 0) == new and read(grant, 0) == grant_before
    os.replace(temporary, helper)
    for directory in (helper.parent, state):
        fd = os.open(directory, os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    assert read(helper, 0) == new and read(backup, 0) == old and read(grant, 0) == grant_before
finally:
    os.close(lock)
print('Schema0014 compatibility installed. Original helper backed up. Grant, session binding, expiry and services unchanged.')
print('Codex will verify status from the existing approved executor; no owner-session status call is needed.')
PY
