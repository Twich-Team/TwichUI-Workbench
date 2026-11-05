"""Process-local transport guard for a zero-request acceptance window."""

import socket
import sys
import threading
import weakref


class OfflineTransportDenied(PermissionError):
    """The offline operator process denied a transport operation."""


def install_offline_guard():
    """Install before Runtime preflight. Run untrusted code only via Runtime."""
    markers = weakref.WeakKeyDictionary()
    lock = threading.Lock()

    def audit(event, args):
        if event in ("urllib.Request", "socket.sendto", "socket.sendmsg"):
            raise OfflineTransportDenied("Offline acceptance denied a network request")
        if event == "socket.bind":
            listener, address = args
            if (listener.family == socket.AF_INET and listener.type == socket.SOCK_STREAM
                    and address == ("127.0.0.1", 0)
                    and listener.getsockname() == ("0.0.0.0", 0)):
                with lock:
                    markers[listener] = threading.current_thread()
        elif event == "socket.connect":
            client, address = args
            if (client.family == socket.AF_INET and client.type == socket.SOCK_STREAM
                    and isinstance(address, tuple) and len(address) == 2
                    and address[0] == "127.0.0.1" and address[1] != 8080):
                with lock:
                    for listener, owner in markers.items():
                        if owner is not threading.current_thread() or listener.fileno() < 0:
                            continue
                        if (listener.getsockname() == address
                                and listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN)):
                            # Consume even if the subsequent kernel connect fails.
                            del markers[listener]
                            return
            raise OfflineTransportDenied("Offline acceptance denied an unowned connection")

    sys.addaudithook(audit)
    # CPython can silently refuse installation if a prior audit hook raises.
    try:
        sys.audit("urllib.Request", "about:blank", None, {}, "GET")
    except OfflineTransportDenied:
        return
    raise RuntimeError("Offline acceptance transport guard was not installed")
