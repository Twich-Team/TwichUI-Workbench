"""Disposable ownership callbacks, never shared-server generation or cancellation."""

import json
import pytest
from workbench.real_primary import PrimarySession, PrimaryHeld, capabilities, proposed_limits


def inputs(tmp_path):
    locks = [tmp_path / "cycle.lock", tmp_path / "model.lock"]
    for p in locks:
        p.touch()
    return tmp_path / "state.json", locks


def test_idle_or_disconnect_never_releases_uncertain_owner(tmp_path):
    state, locks = inputs(tmp_path)
    with PrimarySession(state, locks, "attempt-1", lambda: True) as session:
        headers = session.begin(streaming=True)
        assert headers["X-Conversation-Id"].startswith("wb-real-")
    assert json.loads(state.read_text())["state"] == "quarantined"
    with pytest.raises(PrimaryHeld, match="quarantined"):
        with PrimarySession(state, locks, "replacement", lambda: True):
            pass


def test_delete_ack_does_not_prove_cancellation_or_allow_replay(tmp_path):
    state, locks = inputs(tmp_path)
    deleted = []
    with PrimarySession(state, locks, "attempt-1", lambda: True) as session:
        session.begin(streaming=True)
        session.bind_observed_task(42)
        with pytest.raises(PrimaryHeld, match="unproven"):
            session.cancel_and_reconcile(
                deleted.append,
                lambda nonce: dict(
                    nonce=nonce, task_id=42, is_processing=True, producer_done=True, n_decoded=7
                ),
                timeout=0.08,
            )
    assert len(deleted) == 1 and json.loads(state.read_text())["state"] == "quarantined"


def test_wrong_task_idle_is_not_owned_completion(tmp_path):
    state, locks = inputs(tmp_path)
    with PrimarySession(state, locks, "attempt-1", lambda: True) as session:
        session.begin(streaming=True)
        session.bind_observed_task(42)
        with pytest.raises(PrimaryHeld):
            session.cancel_and_reconcile(
                lambda _: None,
                lambda nonce: dict(
                    nonce=nonce, task_id=99, is_processing=False, producer_done=True, n_decoded=7
                ),
                timeout=0.08,
            )


def test_fenced_task_cannot_bind_a_late_response(tmp_path):
    state, locks = inputs(tmp_path)
    current = True
    with PrimarySession(state, locks, "attempt-1", lambda: current) as session:
        session.begin(streaming=True)
        current = False
        with pytest.raises(PrimaryHeld, match="Current owned"):
            session.bind_observed_task(42)
    assert json.loads(state.read_text())["state"] == "quarantined"


def test_cancel_reconciles_only_correlated_stable_stopped_task(tmp_path):
    state, locks = inputs(tmp_path)
    with PrimarySession(state, locks, "attempt-1", lambda: True) as session:
        session.begin(streaming=True)
        session.bind_observed_task(42)
        session.cancel_and_reconcile(
            lambda _: None,
            lambda nonce: dict(
                nonce=nonce, task_id=42, is_processing=False, producer_done=True, n_decoded=7
            ),
        )
    assert json.loads(state.read_text())["state"] == "released"
    with PrimarySession(state, locks, "new-explicit-attempt", lambda: True):
        pass


def test_non_streaming_and_conflicting_concurrent_ownership_rejected(tmp_path):
    state, locks = inputs(tmp_path)
    with PrimarySession(state, locks, "attempt-1", lambda: True) as session:
        with pytest.raises(PrimaryHeld):
            session.begin(streaming=False)
        with pytest.raises(BlockingIOError):
            with PrimarySession(state, locks, "attempt-2", lambda: True):
                pass


def test_reasoning_classification_is_not_a_changed_request_limit():
    from workbench.real_execution import LIMITS

    evidence = capabilities("b11638-23deec025")
    assert not evidence["aggregate_reasoning_ceiling_proven"]
    assert not capabilities("unknown")["known_build"]
    proposal = proposed_limits()
    assert proposal["max_completion_tokens_per_request"] == 2500
    assert proposal["requires_owner_approval"]
    assert LIMITS["request_tokens"] == 2500 and 'reasoning_tokens' not in LIMITS


def test_uncertified_real_transport_never_sends_a_request():
    from workbench.real_provider import implement, BudgetError

    with pytest.raises(BudgetError, match="not certified"):
        implement(None, "no network", "", "pilot-qwen38-27b", lambda: True)


def test_complete_suite_requires_every_group_and_private_policy():
    from workbench.real_workspace import suite_inventory

    expected = ["test_one.lua", "test_two.py"]
    record = dict(
        status="pass",
        rc=0,
        command=["bash", "tests/run.sh"],
        network_mode="private-loopback",
        out="ok   test_one.lua\nok   test_two.py\n2 passed, 0 failed\n",
    )
    assert suite_inventory(record, expected)
    assert not suite_inventory(
        {**record, "out": "ok   test_one.lua\n1 passed, 0 failed\n"}, expected
    )
    assert not suite_inventory({**record, "network_mode": "host"}, expected)


def test_terminal_history_is_append_only_and_private(tmp_path):
    state, locks = inputs(tmp_path)
    with PrimarySession(state, locks, "first", lambda: True):
        pass
    prior = (tmp_path / "state.json.events.jsonl").read_bytes()
    with PrimarySession(state, locks, "second", lambda: True):
        pass
    journal = tmp_path / "state.json.events.jsonl"
    assert journal.read_bytes().startswith(prior)
    assert not journal.stat().st_mode & 0o077


def test_workbench_planning_honors_real_quarantine(tmp_path, monkeypatch):
    from workbench import planning_provider
    from workbench.planning_transport import PlanningUnavailable

    # No live locks or providers: inject only the existing lock handles.
    fake_pilot = tmp_path / "pilot"
    (fake_pilot / "queue").mkdir(parents=True)
    for name in ["cycle.lock", "queue/model-request.lock"]:
        (fake_pilot / name).touch()
    original_path = planning_provider.Path
    monkeypatch.setattr(
        planning_provider,
        "Path",
        lambda value: (
            fake_pilot if str(value) == "/home/rich/twichui-pilot/batch" else original_path(value)
        ),
    )
    monkeypatch.setattr(planning_provider, "ROOT", tmp_path)
    (tmp_path / ".local").mkdir()
    (tmp_path / ".local/real-primary-session.json").write_text(json.dumps({"state": "quarantined"}))
    with pytest.raises(PlanningUnavailable, match="quarantined"):
        with planning_provider.local_slot():
            pass


def test_native_cancel_is_own_nonce_only_and_fences_server_restart():
    from workbench.real_primary import NativeCancellation

    class Transport:
        requests = []

        def __init__(self):
            self.ends_at = float("inf")

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def read(self, request, limit, timeout):
            self.requests.append((request.method, request.full_url))
            if request.method == "DELETE":
                return b""
            return json.dumps(
                [dict(id=0, id_task=42, is_processing=False, next_token=[dict(n_decoded=7)])]
            ).encode()

    epoch = ["server-a"]
    native = NativeCancellation(
        "SECRET", "wb-real-controlled", 42, 0, lambda: epoch[0], "server-a", Transport
    )
    with pytest.raises(PrimaryHeld):
        native.delete("wb-real-foreign")
    assert Transport.requests == []
    native.delete("wb-real-controlled")
    observed = native.observe("wb-real-controlled")
    assert observed["n_decoded"] == 7 and observed["task_id"] == 42
    assert Transport.requests == [
        ("DELETE", "http://127.0.0.1:8080/v1/stream?conv_id=wb-real-controlled"),
        ("GET", "http://127.0.0.1:8080/slots"),
    ]
    epoch[0] = "server-b"
    with pytest.raises(PrimaryHeld, match="epoch"):
        native.observe("wb-real-controlled")
