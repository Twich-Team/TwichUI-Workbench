"""Stop reading early enough to retain the actual native cancellation reserve."""

import time
from types import SimpleNamespace

import pytest

from workbench.real_primary import PrimaryHeld
from test_real_transport import http_tape as http_tape, wire, frame


def test_named_implementation_uses_its_own_bound_and_socket_revocation(http_tape):
    # A settled reply after the unrelated120-second planning timer remains
    # admissible within the explicit300-second grant, less its native reserve.
    http_tape.steps = [wire([frame({'content': 'completed'}),frame(finish='stop',
        usage=dict(prompt_tokens=30,completion_tokens=1,total_tokens=31))])]
    allowed=[True]
    deadline=time.monotonic()+300
    reply=http_tape.adapter.request({'max_tokens': 100},lambda:allowed[0],deadline)
    assert reply['choices'][0]['finish_reason']=='stop'
    ends_at,timeout,stop_event=http_tape.socket_deadlines[0]
    assert ends_at==deadline-5 and 120 < timeout <= 295
    assert not stop_event.is_set()
    allowed[0]=False
    assert stop_event.is_set()


def test_sse_read_cutoff_leaves_native_reconciliation_time(http_tape, monkeypatch):
    from workbench import real_stream as stream

    base = time.monotonic()
    clock = [base]
    deadline = base + 120
    monkeypatch.setattr(stream, "time", SimpleNamespace(monotonic=lambda: clock[0], time=time.time))
    real_events = stream.events

    def slow_source(response, report, allowed):
        values = real_events(response, report, allowed)
        yield next(values)  # Binds the scripted actual task before interruption.
        clock[0] = deadline - 4
        assert not allowed(), "Reading must stop before the cancellation deadline"
        raise PrimaryHeld("Controlled response read deadline")

    monkeypatch.setattr(stream, "events", slow_source)
    http_tape.steps = [wire([frame({"content": "a"}), frame({"content": "b"})])]
    with pytest.raises(PrimaryHeld, match="Controlled response read deadline"):
        http_tape.adapter.request({"max_tokens": 100}, lambda: True, deadline)
    report = http_tape.reports[0]
    assert not report["completed"] and report["usage"] is None
    assert report["fence_state"] == "released" and len(http_tape.deletes) == 1
