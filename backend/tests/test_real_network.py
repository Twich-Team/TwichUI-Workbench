"""Small deterministic private-network regression; no model or candidate execution."""

from pathlib import Path
import json
import os
import socket
import subprocess
import pytest
from workbench import real_confine


def test_broad_network_and_unconfined_flags_are_rejected(tmp_path):
    assert real_confine.run(["true"], str(tmp_path), 1, net=True)["status"] == "error"
    assert real_confine.run(["true"], str(tmp_path), 1, sandbox=False)["status"] == "error"


def test_verification_loopback_is_private_and_host_ipc_denied(tmp_path):
    if not Path("/usr/bin/bwrap").is_file():
        pytest.skip("Runtime capability unavailable; real activation must remain blocked")
    script = Path(__file__).resolve().parents[1] / "workbench/real_sandbox.py"
    with socket.socket() as marker:
        marker.bind(("127.0.0.1", 0))
        marker.listen()
        inherited = os.environ.get('WORKBENCH_VERIFY_PARENT_MARKER')
        host_port = marker.getsockname()[1]
        if inherited:
            # The complete suite is already inside the real launcher. Ubuntu's
            # installed bwrap AppArmor profile denies nested namespace capabilities.
            # Exercise that actual current sandbox, not a skip or canned receipt.
            boundary = json.loads(inherited)
            assert os.readlink('/proc/self/ns/net') != boundary['netns']
            assert os.readlink('/proc/self/ns/net') == os.environ['WORKBENCH_VERIFY_ACTIVE_NETNS']
            host_port = boundary['port']
            marker.close()  # Do not accidentally occupy the same port in this private namespace.
        code = f"""import socket,errno,threading
s=socket.socket();s.bind(('127.0.0.1',0));s.listen()
c=socket.socket();c.connect(s.getsockname());p,_=s.accept();c.sendall(b'private');assert p.recv(7)==b'private';p.close();c.close();s.close()
s=socket.socket();s.settimeout(.5);assert s.connect_ex(('127.0.0.1',{host_port}))!=0;s.close()
s=socket.socket();s.settimeout(.5);assert s.connect_ex(('192.0.2.1',9))==errno.ENETUNREACH;s.close()
try:socket.socket(socket.AF_UNIX);raise AssertionError('host IPC allowed')
except PermissionError:pass
print('private loopback verified')
"""
        result = subprocess.run(
            (["/usr/bin/python3", "-c", code] if inherited else [
                "/usr/bin/python3",
                "-I",
                str(script),
                "--private-loopback",
                "--",
                "/usr/bin/python3",
                "-c",
                code,
            ]),
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr
        assert "private loopback verified" in result.stdout
