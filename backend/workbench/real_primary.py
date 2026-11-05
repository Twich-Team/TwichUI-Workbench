"""Application-owned request fencing/quarantine, not a shared-server exclusive lease.

The installed stream cancellation route is identified in source. Live proof and
all-client lease coverage remain activation prerequisites; no generation probe here.
"""

from contextlib import ExitStack
import fcntl
import json
import os
from pathlib import Path
import tempfile
import time
from uuid import uuid4


class PrimaryHeld(RuntimeError):
    pass


def capabilities(build):
    known = build == "b11638-23deec025"
    return dict(
        known_build=known,
        aggregate_reasoning_ceiling_proven=False,
        cancellation_route="/v1/stream?conv_id=<owned nonce>" if known else None,
        cancellation_requires_stream=True,
        evidence=(
            "Installed sampler re-arms the budget for each thinking block, permits UTF-8 "
            "completion after depletion, and reports no reasoning-token usage subtotal."
            if known
            else "Unknown build; no capability inferred from alias or echoed settings."
        ),
        exclusive_lease_proven=False,
        ownership_evidence="Cycle/model locks omit the live manual driver.py path; "
        "idle/DELETE acknowledgments are not exclusive ownership or completion proof.",
    )


def proposed_limits():
    """Proposal only. Never applied to the authorized provider request by this function."""
    return dict(
        requires_owner_approval=True,
        max_completion_tokens_per_request=2500,
        max_completion_tokens_per_attempt=24000,
        turns=20,
        request_seconds=120,
        implementation_seconds=1200,
        acceptance_seconds=2400,
        assurance="All generated tokens share the cap, conservatively bounding generated "
        "reasoning too; no independently reported reasoning subtotal or reserved answer budget.",
    )


class PrimarySession:
    """Durable cooperative ownership for application callers that honor these locks.

    Callers supply an actual fenced grant/attempt predicate. Unknown completion
    persists after lock/process exit and blocks future cooperating callers. Never
    treats a disconnect, an idle sample or a successful DELETE as completion.
    """

    def __init__(self, state, locks, attempt, permitted):
        self.path = Path(state)
        self.locks = [Path(p) for p in locks]
        self.attempt, self.permitted = attempt, permitted
        self.stack = ExitStack()
        self.record = None

    def __enter__(self):
        try:
            for path in self.locks:
                # Existing lifecycle locks only: never create pilot controls.
                f = self.stack.enter_context(path.open("rb"))
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fd = os.open(str(self.path) + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            f = self.stack.enter_context(os.fdopen(fd, "a+b"))
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if self.path.is_symlink():
                raise PrimaryHeld("Symlink ownership evidence refused")
            if self.path.exists():
                old = json.loads(self.path.read_text())
                if old["state"] != "released":
                    raise PrimaryHeld(
                        "Uncertain prior generation quarantined; no new request or replay"
                    )
            if not self.permitted():
                raise PrimaryHeld("Grant/attempt fence is not current")
            self.record = dict(
                attempt=self.attempt,
                nonce="wb-real-" + str(uuid4()),
                state="reserved",
                observations=[],
            )
            self._save()
            return self
        except BaseException:
            self.stack.close()
            raise

    def _save(self):
        journal = Path(str(self.path) + ".events.jsonl")
        event = (json.dumps(self.record, sort_keys=True) + "\n").encode()
        if len(event) > 16384 or (
            journal.exists() and journal.stat().st_size + len(event) > 8 << 20
        ):
            raise PrimaryHeld("Ownership evidence budget exhausted; keep the lane held")
        journal_fd = os.open(journal, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        with os.fdopen(journal_fd, "ab") as stream:
            stream.write(event)
            stream.flush()
            os.fsync(stream.fileno())
        fd, name = tempfile.mkstemp(prefix=".primary-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                os.fchmod(stream.fileno(), 0o600)
                json.dump(self.record, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
            fd = os.open(self.path.parent, os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            if Path(name).exists():
                Path(name).unlink()

    def begin(self, *, streaming):
        if not streaming or self.record["state"] != "reserved" or not self.permitted():
            raise PrimaryHeld("Named streaming request and current attempt required")
        self.record["state"] = "in_flight"
        self._save()  # Persist uncertainty before a request can reach the server.
        return {"X-Conversation-Id": self.record["nonce"]}

    def bind_observed_task(self, task_id):
        if (
            type(task_id) is not int
            or task_id < 0
            or self.record["state"] != "in_flight"
            or not self.permitted()
        ):
            raise PrimaryHeld("Current owned request/task evidence required")
        if "task_id" in self.record and self.record["task_id"] != task_id:
            raise PrimaryHeld("Conflicting task identity cannot replace owned evidence")
        self.record["task_id"] = task_id
        self._save()

    def capture_probe_decision(self, decision):
        from copy import deepcopy
        if self.record['state'] != 'in_flight' or 'probe_decision' in self.record:
            raise PrimaryHeld('Probe decision cannot be replaced')
        self.record['probe_decision'] = deepcopy(decision)
        self._save()

    def cancel_and_reconcile(self, delete_owned, observe_owned, timeout=5):
        # Callbacks are trusted transport adapters, never model-supplied URLs.
        if self.record["state"] != "in_flight" or "task_id" not in self.record:
            self.record["state"] = "quarantined"
            self._save()
            raise PrimaryHeld("Cannot correlate cancellation with an observed owned task")
        self.record["state"] = "cancel_requested"
        self.record['cancel_requested_at'] = time.monotonic()
        self._save()
        try:
            end = time.monotonic() + min(5, max(0, timeout))
            delete_owned(self.record["nonce"])
            stable = 0
            last_count = None
            while time.monotonic() < end:
                evidence = observe_owned(self.record["nonce"])
                if time.monotonic() >= end:
                    raise PrimaryHeld('Cancellation observation arrived after reconciliation deadline')
                correlated = (
                    evidence.get("nonce") == self.record["nonce"]
                    and evidence.get("task_id") == self.record["task_id"]
                )
                stopped = (
                    correlated
                    and evidence.get("producer_done") is True
                    and evidence.get("is_processing") is False
                )
                count = evidence.get("n_decoded")
                stable = (
                    stable + 1
                    if stopped and type(count) is int and count == last_count
                    else (1 if stopped and type(count) is int else 0)
                )
                last_count = count
                observed = {
                    key: evidence.get(key)
                    for key in ["nonce", "task_id", "producer_done", "is_processing", "n_decoded"]
                }
                self.record["observations"] = (self.record["observations"] + [observed])[-32:]
                if stable >= 2:
                    self.record["state"] = "released"
                    self.record['reconciled_at'] = time.monotonic()
                    self._save()
                    return
                time.sleep(0.05)
        except Exception:
            self.record["state"] = "quarantined"
            self._save()
            raise
        self.record["state"] = "quarantined"
        self._save()
        raise PrimaryHeld(
            "Cancellation completion unproven; attempt remains consumed and lane held"
        )

    def __exit__(self, *_):
        try:
            if self.record and self.record["state"] not in {"released", "quarantined"}:
                self.record["state"] = (
                    "released" if self.record["state"] == "reserved" else "quarantined"
                )
                self._save()
        finally:
            self.stack.close()


def quarantined(path):
    path = Path(path)
    return path.is_file() and json.loads(path.read_text()).get("state") not in {
        "released",
        "reserved",
    }


class NativeCancellation:
    """Exact installed named-stream API, bound to one observed task and server epoch.

    Only used after a separately certified ownership/probe contract. Never issues a
    generation request. An acknowledgment is followed by correlated decoder-state
    observations; unknown/malformed state cannot release the lane.
    """

    def __init__(self, secret, nonce, task_id, slot_id, epoch, expected_epoch, transport=None, deadline=None):
        from workbench.planning_transport import LocalHTTP

        if (
            not nonce.startswith("wb-real-")
            or len(nonce) > 100
            or not all(c.isalnum() or c == "-" for c in nonce)
        ):
            raise PrimaryHeld("Invalid owned conversation nonce")
        self.secret, self.nonce = secret, nonce
        self.task_id, self.slot_id = task_id, slot_id
        self.epoch, self.expected_epoch = epoch, expected_epoch
        self.transport = transport or LocalHTTP
        self.end = None
        self.deadline = deadline
        self.delete_acknowledged = False

    def _request(self, method, path):
        import urllib.request

        if self.epoch() != self.expected_epoch or self.end is None or time.monotonic() >= self.end:
            raise PrimaryHeld("Server epoch or cancellation deadline changed")
        req = urllib.request.Request(
            "http://127.0.0.1:8080" + path,
            method=method,
            headers={"Authorization": "Bearer " + self.secret},
        )
        with self.transport() as client:
            client.ends_at = min(client.ends_at, self.end)
            data = client.read(req, 65536, timeout=min(2, max(0.01, self.end - time.monotonic())))
        if self.epoch() != self.expected_epoch:
            raise PrimaryHeld("Server epoch changed while canceling")
        return data

    def delete(self, nonce):
        if nonce != self.nonce:
            raise PrimaryHeld("Cannot cancel another conversation")
        self.end = min(time.monotonic() + 5, self.deadline or float('inf'))
        self._request("DELETE", "/v1/stream?conv_id=" + self.nonce)
        self.delete_acknowledged = True  # Never interpreted as generation-stop proof.

    def observe(self, nonce):
        if nonce != self.nonce or not self.delete_acknowledged:
            raise PrimaryHeld("Owned cancellation request required")
        slots = json.loads(self._request("GET", "/slots"))
        if not isinstance(slots, list):
            raise PrimaryHeld("Malformed decoder evidence")
        matching = [
            s for s in slots if s.get("id") == self.slot_id and s.get("id_task") == self.task_id
        ]
        if len(matching) != 1:
            raise PrimaryHeld("Owned decoder task is unavailable; cannot reconcile")
        slot = matching[0]
        count = slot.get("next_token", [{}])[0].get("n_decoded")
        stopped = slot.get("is_processing") is False
        return dict(
            nonce=nonce,
            task_id=self.task_id,
            is_processing=slot.get("is_processing"),
            producer_done=stopped,
            n_decoded=count,
            epoch=self.expected_epoch,
            observed_at=time.monotonic(),
        )
