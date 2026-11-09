"""Trusted fixed GitHub download. Run isolated with only /state writable.

No task input, authentication, model calls, source upload, repository hooks,
submodules, publication, existing-checkout mutation or arbitrary command API.
"""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from uuid import uuid4

URL = "https://github.com/Twich-Team/TwichUI.git"
FLOOR = 4 * 2**30
MAX_BYTES = 512 * 2**20


def git(root, *args, seconds=90, watch=None):
    command = [
        "/usr/bin/git",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "init.templateDir=",
        "-c",
        "protocol.allow=never",
        "-c",
        "protocol.https.allow=always",
        "-c",
        "credential.helper=",
        "-c",
        "core.askPass=",
        "-c",
        "submodule.recurse=false",
        "-c",
        "core.fsmonitor=false",
        "-C",
        str(root),
        *args,
    ]
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as error:
        process = subprocess.Popen(
            command,
            stdout=output,
            stderr=error,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": "/tmp/home",
                "LANG": "C.UTF-8",
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_TERMINAL_PROMPT": "0",
            },
            start_new_session=True,
        )
        end = time.monotonic() + seconds
        try:
            while process.poll() is None:
                size = (
                    sum(p.lstat().st_size for p in Path(watch).rglob("*") if not p.is_symlink())
                    if watch and Path(watch).exists()
                    else 0
                )
                if (
                    time.monotonic() > end
                    or shutil.disk_usage(root).free < FLOOR
                    or size > MAX_BYTES
                    or os.fstat(output.fileno()).st_size > 262144
                    or os.fstat(error.fileno()).st_size > 65536
                ):
                    raise ValueError(
                        "Fixed source download exceeded its operation or resource bound."
                    )
                time.sleep(0.2)
            if process.returncode:
                raise ValueError("Fixed source download or verification failed.")
            output.seek(0)
            return output.read(262145)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, 9)
            process.wait()


def atomic(root, name, value):
    temporary = root / (".record-" + uuid4().hex)
    with temporary.open("x") as out:
        json.dump(value, out, indent=2)
        out.write("\n")
        out.flush()
        os.fsync(out.fileno())
    os.replace(temporary, root / name)
    descriptor = os.open(root, os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def sync(root):
    root = Path(root)
    if root.resolve() != root or root.is_symlink() or root.stat().st_uid != os.getuid():
        raise ValueError("Owned source state is required.")
    if shutil.disk_usage(root).free < FLOOR + MAX_BYTES:
        raise ValueError("Source refresh needs its recovery reserve.")
    snapshots = root / "snapshots"
    snapshots.mkdir(mode=0o700, exist_ok=True)
    if snapshots.is_symlink() or snapshots.stat().st_uid != os.getuid():
        raise ValueError("Unsafe source snapshot directory.")
    with (root / "refresh.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        remote = git(root, "ls-remote", URL, "refs/heads/main", seconds=30).decode().strip().split()
        if (
            len(remote) != 2
            or not re.fullmatch("[a-f0-9]{40}", remote[0])
            or remote[1] != "refs/heads/main"
        ):
            raise ValueError("The fixed project main branch is unavailable.")
        base = remote[0]
        target = snapshots / base
        temporary = snapshots / (".fetch-" + uuid4().hex)
        if not target.exists():
            try:
                git(
                    root,
                    "clone",
                    "--quiet",
                    "--no-checkout",
                    "--no-hardlinks",
                    URL,
                    str(temporary),
                    watch=temporary,
                )
                git(temporary, "checkout", "--quiet", "-B", "main", base, watch=temporary)
                total = 0
                for path in temporary.rglob("*"):
                    info = path.lstat()
                    if info.st_uid != os.getuid():
                        raise ValueError("Source ownership changed.")
                    if path.is_file() and not path.is_symlink():
                        total += info.st_size
                    if total > MAX_BYTES or shutil.disk_usage(root).free < FLOOR:
                        raise ValueError("Source snapshot exceeded its physical resource bound.")
                os.rename(temporary, target)
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
        if target.is_symlink() or target.resolve() != target or target.stat().st_uid != os.getuid():
            raise ValueError("Unsafe retained project snapshot.")
        if (
            git(target, "rev-parse", "HEAD").decode().strip() != base
            or git(target, "rev-parse", "refs/heads/main").decode().strip() != base
            or git(target, "config", "--get", "remote.origin.url").decode().strip() != URL
            or git(target, "status", "--porcelain=v1", "--ignored")
        ):
            raise ValueError("The project snapshot failed independent Git checks.")
        value = dict(
            version=1,
            repository="Twich-Team/TwichUI",
            base=base,
            tree=git(target, "rev-parse", base + "^{tree}").decode().strip(),
            committed_at=git(target, "show", "-s", "--format=%cI", base).decode().strip(),
            synchronized_at=time.time(),
            source="Fixed project main; public GitHub read without credentials; immutable local snapshot.",
        )
        atomic(root, "current.json", value)
        atomic(
            root,
            "last-refresh.json",
            dict(
                status="complete",
                metadata=value,
                metadata_sha256=hashlib.sha256(
                    json.dumps(value, sort_keys=True).encode()
                ).hexdigest(),
            ),
        )
        return value


if __name__ == "__main__":
    os.umask(0o077)
    # This executable never accepts task input and refuses an unisolated launch.
    status = Path("/proc/self/status").read_text()
    if (
        any(Path(p).exists() for p in ("/home/rich", "/run/user", "/run/twichui-workbench-codex"))
        or not re.search(r"^NoNewPrivs:\s+1$", status, re.M)
        or not re.search(r"^CapEff:\s+0+$", status, re.M)
    ):
        raise ValueError("Source refresh requires the credential-free isolated user launcher.")
    print(json.dumps(sync(Path("/state"))))
