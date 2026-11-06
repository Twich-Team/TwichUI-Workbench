"""Cancellable fixed-loopback HTTP transport; never a provider router."""

import http.client
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request

MAX_DEADLINE = 120
SCENARIO_FAILURE_MESSAGES = {
    "canceled": "The worker stopped before the local model finished planning. The model’s final usage is unknown.",
    "interrupted_unknown": "The worker stopped during planning. Model completion is unknown.",
    "incomplete_output": "The local model stopped before it returned a complete plan.",
    "timeout": "The local model did not finish planning before the time limit.",
    "invalid_json": "The local model returned a response Workbench could not read.",
    "model_identity": "The model service did not match the selected planning model.",
    "transport": "Workbench lost its connection to the local model while planning.",
    "provider_rejection": "The local model service rejected the planning request.",
    "contention": "Workbench could not get access to the local model.",
    "credential_or_lock": "Workbench could not verify access to the local model.",
}


def scenario_failure_message(category, detail=None):
    cause = SCENARIO_FAILURE_MESSAGES.get(category, detail or
        "Workbench could not validate the local planning response.")
    return f"{cause} Your original request and any example are saved."


class PlanningUnavailable(Exception):
    def __init__(self, message, reason="unavailable", metadata=None):
        super().__init__(message)
        self.reason = reason
        self.metadata = metadata or {}


def deadline_seconds():
    try:
        value = int(os.environ.get("WORKBENCH_PLANNING_DEADLINE_S", str(MAX_DEADLINE)))
        if not 15 <= value <= MAX_DEADLINE:
            raise ValueError()
        return value
    except ValueError:
        raise PlanningUnavailable(
            "Choose a planning deadline from 15 to 120 seconds.", "configuration"
        ) from None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class LocalHTTP:
    """Closes the active socket on deadline or worker shutdown, including header wait."""

    def __init__(self, stop_event=None):
        self.stop_event = stop_event or threading.Event()
        self.done = threading.Event()
        self.expired = threading.Event()
        self.canceled = threading.Event()
        self.sockets = []
        self.lock = threading.Lock()
        self.seconds = deadline_seconds()
        self.ends_at = time.monotonic() + self.seconds
        owner = self

        class Connection(http.client.HTTPConnection):
            def connect(self):
                super().connect()
                with owner.lock:
                    owner.sockets.append(self.sock)
                if owner.expired.is_set() or owner.stop_event.is_set():
                    self.close()
                    raise OSError("Planning canceled")

        class Handler(urllib.request.HTTPHandler):
            def http_open(self, req):
                return self.do_open(Connection, req)

        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect(), Handler()
        )
        self.monitor = threading.Thread(
            target=self._monitor, daemon=True, name="local-planning-deadline"
        )

    def _close_sockets(self):
        with self.lock:
            for sock in self.sockets:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                sock.close()

    def _monitor(self):
        while not self.done.wait(0.05):
            if self.stop_event.is_set():
                self.canceled.set()
                self._close_sockets()
                return
            if time.monotonic() >= self.ends_at:
                self.expired.set()
                self._close_sockets()
                return

    def __enter__(self):
        if self.stop_event.is_set():
            raise PlanningUnavailable("Worker shutdown canceled planning.", "canceled")
        self.monitor.start()
        return self

    def __exit__(self, *_):
        self.done.set()
        self._close_sockets()
        self.monitor.join(timeout=0.2)

    def read(self, request, limit, timeout=None):
        try:
            remaining = self.ends_at - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            with self.opener.open(
                request, timeout=min(timeout or self.seconds, remaining)
            ) as response:
                raw = response.read(limit + 1)
            if self.stop_event.is_set() or self.canceled.is_set():
                raise PlanningUnavailable("Worker shutdown canceled planning.", "canceled")
            if self.expired.is_set() or time.monotonic() >= self.ends_at:
                raise TimeoutError()
            if len(raw) > limit:
                raise PlanningUnavailable("Local response exceeds output budget.", "output_limit")
            return raw
        except urllib.error.HTTPError as error:
            raise PlanningUnavailable(
                "Local provider rejected the request.",
                "provider_rejection",
                {"http_status": error.code},
            ) from None
        except (OSError, urllib.error.URLError, http.client.HTTPException) as error:
            if self.stop_event.is_set() or self.canceled.is_set():
                reason = "canceled"
            elif (
                self.expired.is_set()
                or isinstance(error, TimeoutError)
                or isinstance(getattr(error, "reason", None), TimeoutError)
            ):
                reason = "timeout"
            else:
                reason = "transport"
            raise PlanningUnavailable(
                "Local planning transport did not complete.", reason
            ) from None

    def models(self, secret):
        try:
            raw = self.read(
                urllib.request.Request(
                    "http://127.0.0.1:8080/v1/models", headers={"Authorization": "Bearer " + secret}
                ),
                8192,
                timeout=3,
            )
            models = json.loads(raw)["data"]
            if (
                len(models) != 1
                or not isinstance(models[0]["id"], str)
                or not 1 <= len(models[0]["id"]) <= 160
            ):
                raise ValueError()
            return models[0]["id"]
        except ValueError, KeyError, TypeError:
            raise PlanningUnavailable(
                "Configured local model identity is unavailable.", "model_identity"
            ) from None
