import os
from pathlib import Path
import socket
import subprocess
import sys
import threading

import pytest


def test_offline_acceptance_guard():
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    # Python audit hooks persist for the life of the process.
    subprocess.run(
        [sys.executable, __file__],
        env={**os.environ, "PYTHONPATH": str(scripts)},
        check=True,
        timeout=15,
    )


def check_guard():
    from acceptance_transport_guard import OfflineTransportDenied, install_offline_guard

    with socket.socket() as existing, socket.socket() as marker, socket.socket() as client:
        existing.bind(("127.0.0.1", 0))
        existing.listen()
        install_offline_guard()
        client.settimeout(1)
        with pytest.raises(OfflineTransportDenied):
            client.connect(existing.getsockname())

        marker.bind(("127.0.0.1", 0))
        address = marker.getsockname()
        with pytest.raises(OfflineTransportDenied):
            client.connect(address)
        marker.listen()

        # A bind in one thread cannot authorize a different caller.
        denied = threading.Event()

        def other_thread():
            with socket.socket() as other_client:
                try:
                    other_client.connect(address)
                except OfflineTransportDenied:
                    denied.set()

        thread = threading.Thread(target=other_thread)
        thread.start()
        thread.join(timeout=2)
        assert denied.is_set()

        client.connect(address)
        with marker.accept()[0] as accepted:
            client.sendall(b"marker")
            assert accepted.recv(6) == b"marker"
        with socket.socket() as second:
            with pytest.raises(OfflineTransportDenied):
                second.connect_ex(address)

        # Exercise transport audit events without issuing either model request.
        for path, method, body in (
            ("/v1/models", "GET", None),
            ("/v1/chat/completions", "POST", b"{}"),
        ):
            for _ in range(2):
                with pytest.raises(OfflineTransportDenied):
                    sys.audit("urllib.Request", "http://127.0.0.1:8080" + path, body, {}, method)
        for address in (("127.0.0.1", 8080), ("192.0.2.1", 80), "/unused.socket"):
            with pytest.raises(OfflineTransportDenied):
                sys.audit("socket.connect", client, address)


if __name__ == "__main__":
    check_guard()
