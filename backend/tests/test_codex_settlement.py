"""Cancellation receipts are independent of model success and never enable replay."""

import json
import os
import time

import pytest
from workbench import codex_provider as codex
from workbench.codex_journal import Journal, MAX_BYTES
from test_codex_provider import Transcript, completed


def test_terminal_interrupt_is_read_after_the_generation_deadline_and_stop_signal():
    read_fd, write_fd = os.pipe()
    channel = codex.Channel(read_fd, lambda _: None, lambda: True, time.monotonic() - 1)
    try:
        events = [
            dict(id=999, result={}),
            completed()[1],
            dict(
                method="turn/completed",
                params=dict(
                    threadId="thread", turnId="turn", turn=dict(id="turn", status="interrupted")
                ),
            ),
        ]
        os.write(write_fd, b"".join((json.dumps(e) + "\n").encode() for e in events))
        status, counted = channel.settle_interrupt("thread", "turn")
        assert status == "interrupted" and counted["total_tokens"] == 24
        assert channel.stop() and channel.deadline < time.monotonic()
    finally:
        channel.close()
        os.close(read_fd)
        os.close(write_fd)


@pytest.mark.parametrize("terminal", ["completed", "failed", "interrupted"])
def test_timeout_with_confirmed_terminal_receipt_remains_held_without_another_turn(terminal):
    rpc = Transcript(
        events=[dict(method="item/agentMessage/delta", params=dict(delta="private partial source"))]
    )
    receipts = []
    rpc.evidence = lambda phase, **values: receipts.append(dict(phase=phase, **values))
    rpc.settle_interrupt = lambda thread, turn: (
        terminal,
        dict(prompt_tokens=20, completion_tokens=4, reasoning_tokens=1, total_tokens=24),
    )
    with pytest.raises(codex.Hold) as held:
        codex.turn(rpc, "fixture-model", None, "private prompt", {})
    assert held.value.terminal and held.value.partial and held.value.counted["total_tokens"] == 24
    assert [m for m, _ in rpc.calls].count("turn/start") == 1
    assert receipts[0] == dict(phase="thread-created", thread_id="thread")
    assert receipts[1] == dict(phase="turn-started", thread_id="thread", turn_id="turn")
    assert receipts[-2]["acknowledged"] is True
    assert receipts[-1]["output_excerpt"] == "private partial source"
    assert all("private prompt" not in json.dumps(r) for r in receipts)


def test_missing_interrupt_receipt_preserves_uncertainty_and_known_output():
    rpc = Transcript(events=[dict(method="item/agentMessage/delta", params=dict(delta="partial"))])
    rpc.settle_interrupt = lambda *_: (None, None)
    with pytest.raises(codex.Hold) as held:
        codex.turn(rpc, "fixture-model", None, "private prompt", {})
    assert not held.value.terminal and held.value.partial and held.value.counted is None
    assert len(rpc.interrupted) == 1


def test_private_journal_is_persistent_bounded_and_excludes_request_and_auth_data(tmp_path):
    root = tmp_path / "evidence"
    journal = Journal(root, b"PRIVATE SOURCE AND PROMPT")
    journal.record("turn-started", thread_id="thread-1", turn_id="turn-2")
    journal.record("turn-held", output_excerpt="x" * 20000, partial=True, status="uncertain")
    journal.close()
    assert root.stat().st_mode & 0o777 == 0o700
    assert journal.path.stat().st_mode & 0o777 == 0o600 and journal.path.stat().st_size < MAX_BYTES
    rows = [json.loads(line) for line in journal.path.read_text().splitlines()]
    assert "PRIVATE SOURCE AND PROMPT" not in journal.path.read_text()
    assert len(rows[-1]["output_excerpt"]) == 8192
    assert rows[1]["thread_id"] == "thread-1"


@pytest.mark.parametrize(
    "bad", ["symlink", "public", "invalid-field", "invalid-identity", "capacity"]
)
def test_journal_never_writes_outside_owned_private_bounds(tmp_path, bad):
    root = tmp_path / "root"
    if bad == "symlink":
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir(mode=0o700)
        root.symlink_to(elsewhere)
        with pytest.raises(ValueError):
            Journal(root, b"request")
        assert not list(elsewhere.iterdir())
        return
    root.mkdir(mode=0o755 if bad == "public" else 0o700)
    if bad == "public":
        root.chmod(0o755)
        with pytest.raises(ValueError):
            Journal(root, b"request")
        return
    journal = Journal(root, b"request")
    try:
        with pytest.raises(ValueError):
            if bad == "invalid-field":
                journal.record("account", access_token="NEVER")
            elif bad == "invalid-identity":
                journal.record("turn-started", thread_id="../../private")
            else:
                for _ in range(40):
                    journal.record("output", output_excerpt="x" * 8192)
        assert journal.path.stat().st_size <= MAX_BYTES
        assert "NEVER" not in journal.path.read_text()
    finally:
        journal.close()


def test_real_pipe_child_timeout_waits_for_interrupt_terminal_receipt(tmp_path):
    import sys
    from test_codex_provider import completed

    rpc_fixture = Transcript()
    responses = rpc_fixture.responses
    cli = tmp_path / "controlled_cli.py"
    calls = tmp_path / "methods.jsonl"
    cli.write_text("""import json,sys
responses=json.loads(sys.argv[1])
def send(value):
 print(json.dumps(value),flush=True)
for line in sys.stdin:
 request=json.loads(line)
 method=request.get('method')
 with open(sys.argv[2],'a') as record:record.write(json.dumps(method)+'\\n')
 if method=='initialized':continue
 if method=='turn/interrupt':
  send({'id':request['id'],'result':{}})
  send(json.loads(sys.argv[3]))
  send({'method':'turn/completed','params':{'threadId':'thread','turnId':'turn','turn':{'id':'turn','status':'interrupted'}}})
 else:
  send({'id':request['id'],'result':responses[method]})
  if method=='turn/start':send({'method':'item/agentMessage/delta','params':{'threadId':'thread','turnId':'turn','delta':'private partial model output'}})
""")
    journal = Journal(tmp_path / "receipt", b"PRIVATE REQUEST")
    try:
        with codex.app_server(
            [
                sys.executable,
                str(cli),
                json.dumps(responses),
                str(calls),
                json.dumps(completed()[1]),
            ],
            lambda: False,
            time.monotonic() + 0.5,
        ) as rpc:
            rpc.evidence = journal.record
            with pytest.raises(codex.Hold) as held:
                codex.turn(rpc, "fixture-model", None, "private task source", {})
            assert held.value.reason == "timeout"
            assert held.value.terminal and held.value.partial
            assert held.value.counted["total_tokens"] == 24
    finally:
        journal.close()
    methods = [json.loads(line) for line in calls.read_text().splitlines()]
    assert methods.count("turn/start") == methods.count("turn/interrupt") == 1
    events = [json.loads(line) for line in journal.path.read_text().splitlines()]
    assert events[-2]["phase"] == "interrupt-result" and events[-2]["acknowledged"]
    assert (
        events[-1]["status"] == "terminal"
        and events[-1]["output_excerpt"] == "private partial model output"
    )
    assert "private task source" not in journal.path.read_text()


def test_private_output_excerpt_redacts_credentials_and_bounds_unicode_bytes(tmp_path):
    journal = Journal(tmp_path / "private", b"not retained")
    try:
        journal.record(
            "first-output", output_excerpt='api_key="sk-FAKEEXAMPLEKEY" Bearer FAKEBEARERTOKEN'
        )
        journal.record("turn-held", output_excerpt="界" * 20000)
    finally:
        journal.close()
    raw = journal.path.read_text()
    assert "FAKEEXAMPLEKEY" not in raw and "FAKEBEARERTOKEN" not in raw
    rows = [json.loads(line) for line in raw.splitlines()]
    assert len(json.dumps(rows[-1]["output_excerpt"]).encode()) <= 8200
    assert journal.path.stat().st_size < MAX_BYTES


@pytest.mark.parametrize("raw", [b"null\n", b"[]\n", b"false\n", b'"invalid"\n'])
def test_invalid_connector_envelope_never_starts_a_provider_or_breaks_the_broker(
    tmp_path, monkeypatch, raw
):
    import socket
    from threading import Thread
    import codex_connector as connector

    monkeypatch.setattr(connector, "allowed_cgroup", lambda *_: True)
    monkeypatch.setattr(
        connector,
        "resolve_backend",
        lambda *_: pytest.fail("Invalid envelope reached network resolution"),
    )
    left, right = socket.socketpair()
    with left, right:
        left.settimeout(2)
        thread = Thread(
            target=connector.handle,
            args=(right, "/fixture/cli", "/fixture/auth", tmp_path, tmp_path / "evidence"),
        )
        thread.start()
        left.sendall(raw)
        result = json.loads(left.makefile("rb").readline())
        thread.join(2)
    assert result["error"] == "protocol" and result["started"] is False
    assert result["partial"] is False and result["counted"] is None
    assert not (tmp_path / "evidence").exists() and not thread.is_alive()


@pytest.mark.parametrize(
    "title,total,held",
    [("ProgramResponse", 40000, False), ("ReviewResponse", 70000, True), ("ordinary", 40000, True)],
)
def test_observed_guard_keeps_actual_usage_and_terminal_receipt(title, total, held):
    from types import MethodType

    events = completed()
    events[1]["params"]["tokenUsage"]["total"] = dict(
        inputTokens=total - 100, outputTokens=100, reasoningOutputTokens=0, totalTokens=total
    )
    rpc = Transcript(events=events)
    rpc.settle_interrupt = MethodType(codex.Channel.settle_interrupt, rpc)
    if held:
        with pytest.raises(codex.Hold) as error:
            codex.turn(rpc, "fixture-model", None, "task data", dict(title=title))
        assert error.value.terminal and error.value.counted["total_tokens"] == total
        assert error.value.reason == "accounting"
    else:
        result = codex.turn(rpc, "fixture-model", None, "task data", dict(title=title))
        assert result["usage"]["total_tokens"] == total
    assert [m for m, _ in rpc.calls].count("turn/start") == 1


def test_cancellation_has_a_separate_byte_reserve_after_generation_wire_exhaustion():
    read_fd, write_fd = os.pipe()
    channel = codex.Channel(read_fd, lambda _: None, lambda: True, time.monotonic() - 1)
    channel.used = codex.MAX_WIRE + 1
    try:
        os.write(
            write_fd,
            b"{invalid stale frame}\n"
            + (
                json.dumps(
                    dict(
                        method="turn/completed",
                        params=dict(
                            threadId="thread",
                            turnId="turn",
                            turn=dict(id="turn", status="interrupted"),
                        ),
                    )
                )
                + "\n"
            ).encode(),
        )
        status, counted = channel.settle_interrupt("thread", "turn")
        assert status == "interrupted" and counted is None and channel.used > codex.MAX_WIRE
        assert channel.stop() and channel.deadline < time.monotonic()
    finally:
        channel.close()
        os.close(read_fd)
        os.close(write_fd)


@pytest.mark.parametrize("inside", [True, False])
def test_whitespace_guard_does_not_accept_partial_or_reject_lua_string_spacing(inside):
    from types import MethodType

    text = '{"code":"' + " " * 1100 + '"}' if inside else '{"inspection":[{}' + " " * 1100
    events = [dict(method="item/agentMessage/delta", params=dict(delta=text))]
    if inside:
        events.extend(
            [
                dict(
                    method="item/completed",
                    params=dict(item=dict(type="agentMessage", phase="final_answer", text=text)),
                ),
                completed()[1],
                completed()[2],
            ]
        )
    else:
        events.extend(
            [
                completed()[1],
                dict(
                    method="turn/completed", params=dict(turn=dict(id="turn", status="interrupted"))
                ),
            ]
        )
    rpc = Transcript(events=events)
    rpc.settle_interrupt = MethodType(codex.Channel.settle_interrupt, rpc)
    if inside:
        result = codex.turn(rpc, "fixture-model", None, "task", {})
        assert result["output"]["code"] == " " * 1100
    else:
        with pytest.raises(codex.Hold) as held:
            codex.turn(rpc, "fixture-model", None, "task", {})
        assert held.value.reason == "format" and held.value.terminal and held.value.partial
        assert held.value.metadata["protocol_guard"] == "whitespace"
        assert held.value.metadata["terminal_status"] == "interrupted"
        assert held.value.counted["total_tokens"] == 24
