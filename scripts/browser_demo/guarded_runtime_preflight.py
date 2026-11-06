"""Synthetic suite and Lua-launched network denial under the offline guard."""
import json
import os
from pathlib import Path
import socket
import sys
from tempfile import TemporaryDirectory

from acceptance_transport_guard import install_offline_guard
from workbench.real_workspace import Runtime, require_safe_check

root = Path(sys.argv[1])
group = Path(os.environ["WORKBENCH_CG_ROOT"])
assert group.stat().st_uid == os.getuid()
assert set((group / "cgroup.subtree_control").read_text().split()) == {"cpu", "memory", "pids"}
marker = root / "read-denial-marker"
marker.write_text("synthetic offline probe marker")

# This unrelated server exists to detect any leak from the guarded parent or Lua.
with socket.socket() as unrelated:
    unrelated.bind(("127.0.0.1", 0))
    unrelated.listen()
    unrelated.setblocking(False)
    install_offline_guard()

    with TemporaryDirectory(prefix="workbench-verify-") as temp:
        work = Path(temp) / "workspace"
        tests = work / "tests"
        tests.mkdir(parents=True)
        runtime = Runtime(root)
        runtime.recipe = {"offline_boundary_probe": True}
        runtime.workspace = work
        (tests / "run.sh").write_text(
            "set -eu\n"
            "/usr/bin/python3 -I tests/trusted.py\n"
            "/usr/bin/lua5.1 tests/test_offline_fixture.lua\n"
            "echo 'ok   test_offline_fixture.lua'\n"
        )
        (tests / "test_offline_fixture.lua").write_text("assert(3 + 5 == 8)\n")
        (tests / "trusted.py").write_text("""
import errno
import json
import os
import resource
import socket

marker = json.loads(os.environ["WORKBENCH_VERIFY_PARENT_MARKER"])
active = os.readlink("/proc/self/ns/net")
assert active == os.environ["WORKBENCH_VERIFY_ACTIVE_NETNS"] != marker["netns"]
assert resource.getrlimit(resource.RLIMIT_FSIZE) == (128 << 20, 128 << 20)
with socket.socket() as client:
    client.settimeout(1)
    assert client.connect_ex(("127.0.0.1", marker["port"])) == errno.ECONNREFUSED
with socket.socket() as listener:
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    with socket.create_connection(listener.getsockname(), timeout=1) as client:
        with listener.accept()[0] as peer:
            client.sendall(b"private")
            assert peer.recv(7) == b"private"
try:
    socket.socket(socket.AF_UNIX)
except PermissionError:
    pass
else:
    raise AssertionError("trusted child can create a host IPC socket")
print("parent marker live; private loopback passed; parent marker and host IPC denied")
""")
        (tests / "untrusted.lua").write_text(
            'assert(os.execute("/usr/bin/python3 -I tests/untrusted.py") == 0)\n'
        )
        (tests / "untrusted.py").write_text(f"""
import ctypes
import errno
import os
import resource
import socket

assert "WORKBENCH_VERIFY_PARENT_MARKER" not in os.environ
assert resource.getrlimit(resource.RLIMIT_FSIZE) == (16 << 20, 16 << 20)
libc = ctypes.CDLL(None, use_errno=True)
for family in (socket.AF_INET, socket.AF_INET6, socket.AF_UNIX):
    assert libc.socket(family, socket.SOCK_STREAM, 0) == -1
    assert ctypes.get_errno() == errno.EACCES
try:
    open({str(marker)!r}).close()
except PermissionError:
    pass
else:
    raise AssertionError("untrusted Lua descendant read its parent marker")
for target in ({unrelated.getsockname()!r}, ("192.0.2.1", 80)):
    try:
        socket.create_connection(target, timeout=1)
    except PermissionError:
        pass
    else:
        raise AssertionError("untrusted Lua descendant reached a service")
print("untrusted Lua descendant: kernel socket creation and service access denied")
""")

        results = {}
        for name, cmd, trusted in (
            ("suite", ["bash", "tests/run.sh"], True),
            ("untrusted_lua", ["lua5.1", "tests/untrusted.lua"], False),
        ):
            result = runtime.check(cmd, work, timeout=10, private_loopback=trusted)
            results[name] = result
            (root / "runtime-results.json").write_text(json.dumps(results, indent=2) + "\n")
            require_safe_check(result)
            assert result["status"] == "pass" and result["rc"] == 0, result
            assert result["usage"]["trusted_verification"] is trusted
            assert not list(group.glob("cmd-*"))
            print(name, result["out"], flush=True)

    try:
        unrelated.accept()
    except BlockingIOError:
        pass
    else:
        raise AssertionError("unexpected host-loopback connection")
