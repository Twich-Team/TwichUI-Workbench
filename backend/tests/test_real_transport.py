"""NON-MODEL transport readiness: production parser/tools, deterministic HTTP only."""

import http.client
import io
import json
import shutil
import threading
import time
from types import SimpleNamespace

import pytest

from workbench import real_execution as job
from workbench.real_primary import PrimaryHeld
from workbench.real_provider import implement, BudgetError
from workbench.real_sse import (
    events,
    decode,
    merge_chunk,
    assembled_size,
    ASSEMBLED_BYTES_LIMIT,
    AssembledLimit,
)
from workbench.real_stream import NamedStream
from workbench.real_usage import summarize
from workbench.real_workspace import Runtime, durable


def frame(delta=None, finish=None, **extra):
    return dict(
        model="pilot-qwen38-27b",
        id="chatcmpl-NON-MODEL",
        created=1,
        object="chat.completion.chunk",
        system_fingerprint="b11638-23deec025",
        choices=[dict(index=0, delta=delta or {}, finish_reason=finish)],
        **extra,
    )


def wire(chunks, done=True):
    return b"".join(
        b"data: " + json.dumps(c, ensure_ascii=False).encode() + b"\n\n" for c in chunks
    ) + (b"data: [DONE]\n\n" if done else b"")


def response(body, chunked=False):
    """Actual HTTPResponse handles socket/HTTP chunks, not a line-parser substitute."""
    if chunked:
        parts = [body[i : i + 7] for i in range(0, len(body), 7)]
        body = (
            b"".join(format(len(p), "x").encode() + b"\r\n" + p + b"\r\n" for p in parts)
            + b"0\r\n\r\n"
        )
        header = b"Transfer-Encoding: chunked\r\n"
    else:
        header = b"Content-Length: " + str(len(body)).encode() + b"\r\n"

    class Socket:
        def makefile(self, *args):
            return io.BufferedReader(io.BytesIO(b"HTTP/1.1 200 OK\r\n" + header + b"\r\n" + body))

    result = http.client.HTTPResponse(Socket())
    result.begin()
    return result


@pytest.fixture
def http_tape(tmp_path, monkeypatch):
    import workbench.real_stream as stream

    t = SimpleNamespace(
        steps=[],
        bodies=[],
        reports=[],
        counts=[],
        count=100,
        chunked=False,
        observed=threading.Event(),
        deletes=[],
    )

    class Reader:
        def __init__(self, body):
            self.reader = response(body, t.chunked)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.reader.close()

        def readline(self, n):
            assert t.observed.wait(2), "Scripted observer never bound its task"
            return self.reader.readline(n)

    class Client:
        def __init__(self, *args):
            self.ends_at = time.monotonic() + 120
            self.opener = self

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, req, *args, **kwargs):
            if req.full_url.endswith("/input_tokens"):
                t.counts.append(json.loads(req.data))
                return json.dumps(dict(input_tokens=t.count)).encode()
            t.observed.set()
            return b'[{"id":0,"id_task":42,"is_processing":true,"next_token":[{"n_decoded":3}]}]'

        def open(self, req, **kwargs):
            body = json.loads(req.data)
            t.bodies.append(body)
            step = t.steps[len(t.bodies) - 1]
            t.observed.clear()
            return Reader(step(body) if callable(step) else step)

    class Cancel:
        def __init__(self, secret, nonce, task, slot, epoch, expected, **kwargs):
            assert task == 42 and slot == 0 and epoch() == expected
            self.nonce = nonce

        def delete(self, nonce):
            assert nonce == self.nonce
            t.deletes.append(nonce)

        def observe(self, nonce):
            return dict(
                nonce=nonce, task_id=42, producer_done=True, is_processing=False, n_decoded=0
            )

    monkeypatch.setattr(stream, "LocalHTTP", Client)
    monkeypatch.setattr(stream, "NativeCancellation", Cancel)
    t.adapter = NamedStream(
        "SCRIPTED-PRIVATE", tmp_path / "session", lambda: [1, 2], lambda: True, t.reports.append
    )
    return t


def tool_response(calls, reasoning=""):
    chunks = []
    if reasoning:
        chunks.extend(
            frame({"reasoning_content": reasoning[i : i + 1000]})
            for i in range(0, len(reasoning), 1000)
        )
    for index, (name, args) in enumerate(calls):
        raw = json.dumps(args, ensure_ascii=False)
        chunks.append(
            frame(
                {
                    "tool_calls": [
                        dict(
                            index=index,
                            id=f"fixture-{index}",
                            type="function",
                            function=dict(name=name[:4], arguments=""),
                        )
                    ]
                }
            )
        )
        chunks.append(
            frame({"tool_calls": [dict(index=index, function=dict(name=name, arguments=raw[:3]))]})
        )
        chunks.extend(
            frame({"tool_calls": [dict(index=index, function=dict(arguments=raw[i : i + 997]))]})
            for i in range(3, len(raw), 997)
        )
    chunks.append(frame(finish="tool_calls"))
    chunks.append(dict(choices=[], usage=dict(prompt_tokens=100, completion_tokens=50)))
    return wire(chunks)


def final_response():
    return wire(
        [
            frame({"content": "NON-MODEL readiness finished ✓"}, "stop"),
            dict(choices=[], usage=dict(prompt_tokens=100, completion_tokens=50)),
        ]
    )


@pytest.mark.parametrize('linked', [False, True])
def test_complete_production_path_read_correct_edit_check_freeze(
    http_tape, tmp_path, monkeypatch, record_property, linked
):
    """Fake HTTP/check outcomes; discarded fixture candidate is NEVER a real result."""
    out = tmp_path / "NON_MODEL_CONTROL"
    out.mkdir()
    runtime = Runtime(out)
    if linked:
        runtime.workflow_task = dict(intake_id='NON-MODEL-INTAKE',
            task=dict(id='NON-MODEL-TASK',title='UNTRUSTED <script> task',purpose='Controlled tool readiness',
                      acceptance=['NON-MODEL checks only']),approved_scope=['Fixed Chronicle capability only'])
    runtime.prepare()
    try:
        runtime.primary = http_tape.adapter
        http_tape.chunked = True
        monkeypatch.setattr(
            runtime, "preflight", lambda: [dict(name="NON-MODEL gate stub", status="passed")]
        )
        # No child is launched: verification command results below are stubbed.
        # This marker is not confinement proof; live preflight still requires it.
        monkeypatch.setattr(
            "workbench.real_workspace.confine.root", lambda: str(tmp_path / "NON_MODEL_CGROUP")
        )
        checked = []

        def check(argv, cwd, ro=(), timeout=120, private_loopback=False):
            checked.append(argv)
            return dict(
                status="pass",
                rc=0,
                out="\n".join("ok   " + n for n in runtime.expected_suite())
                + f"\n{len(runtime.expected_suite())} passed, 0 failed",
                command=argv,
                network_mode="private-loopback" if private_loopback else "denied",
                role="NON-MODEL check result stub; not independent verification",
            )

        monkeypatch.setattr(runtime, "check", check)
        p = runtime.workspace / job.PATHS[0]
        first = p.read_text().splitlines(True)[0]
        reads = [
            ("read_file", dict(path=job.PATHS[0], start=90, end=130)),
            ("read_file", dict(path=job.PATHS[1], start=90, end=140)),
            ("read_file", dict(path=job.PATHS[0], start=1, end=1)),
        ]

        def corrected(body):
            error = json.loads(body["messages"][-1]["content"])["error"]
            assert (
                error["continuation_enabled"]
                and error["contract"]["indexing"] == "1-based-inclusive"
            )
            return tool_response([("read_file", dict(path=job.PATHS[0], start=1, end=1))])

        http_tape.steps = [
            tool_response(reads),
            tool_response([("read_file", dict(path=job.PATHS[0], start=0))]),
            corrected,
            tool_response(
                [
                    (
                        "replace_text",
                        dict(
                            path=job.PATHS[0],
                            old=first,
                            new="-- NON-MODEL transport control, never task implementation\n"
                            + first,
                        ),
                    )
                ],
                reasoning="untrusted <script> 中文 ✓ " * 2000,
            ),
            tool_response([("run_check", dict(name="syntax-data"))]),
            final_response(),
        ]
        result = runtime.execute("NON-MODEL-REQUEST", "NON-MODEL-FENCE", lambda: True)
        if linked:
            assert result['manifest']['workflow_task_binding'] == job.digest(runtime.workflow_task)
            assert result['manifest']['workflow_task'] == dict(intake_id='NON-MODEL-INTAKE',task_id='NON-MODEL-TASK')
            assert 'NON-MODEL-TASK' in http_tape.bodies[0]['messages'][1]['content']
            assert 'untrusted data' in http_tape.bodies[0]['messages'][1]['content']
        assert runtime.verify_frozen(result) and result["correction_count"] == 1
        assert result["usage"]["values"]["completion_tokens"] == 300 and len(http_tape.bodies) == 6
        assert checked[0] == ["luac5.1", "-p", job.PATHS[0]] and len(checked) == 7
        assert [f["path"] for f in result["files"]] == [job.PATHS[0]]
        assert "-- NON-MODEL transport control" in result["files"][0]["diff"]
        assert all(r["completed"] and r["fence_state"] == "released" for r in http_tape.reports)
        assert all(r["limits"]["stream_bytes"] == 2 << 20 for r in http_tape.reports)
        record_property(
            "readiness",
            json.dumps(
                dict(
                    role="NON-MODEL scripted fixture, discarded; checks stubbed",
                    candidate_handoff=True,
                    real_candidate=False,
                    requests=6,
                    corrections=1,
                    selected_source_files=2,
                    verification_tool_called=True,
                    maximum_wire_bytes=max(r["wire_bytes"] for r in http_tape.reports),
                    maximum_assembled_bytes=max(r["assembled_bytes"] for r in http_tape.reports),
                )
            ),
        )
    finally:
        shutil.rmtree(runtime.workspace.parent)


def test_native_metadata_above_old_cap_with_small_content(http_tape):
    http_tape.steps = [
        wire(
            [frame({"content": "x"}) for _ in range(2496)]
            + [
                frame(finish="stop"),
                dict(choices=[], usage=dict(prompt_tokens=100, completion_tokens=2496)),
            ]
        )
    ]
    r = http_tape.adapter.request({"max_tokens": 2496}, lambda: True, time.monotonic() + 120)
    report = http_tape.reports[0]
    assert r["choices"][0]["message"]["content"] == "x" * 2496
    assert 256 << 10 < report["wire_bytes"] < 2 << 20 and report["assembled_bytes"] < 4000
    assert report["event_count"] == 2499 and report["completed"]


def test_request_decoding_and_safe_finish_diagnostics(http_tape):
    http_tape.steps=[final_response()]
    result=implement(None,'NON-MODEL','','pilot-qwen38-27b',lambda:True,ownership=http_tape.adapter)
    for body in [http_tape.bodies[0],http_tape.counts[0]]:
        assert body['reasoning_effort']=='none' and body['temperature']==0.2
        assert body['max_tokens']==2496
    report=http_tape.reports[0]
    assert report['finish_reason']=='stop' and report['response_parts']['reasoning_bytes']==0
    assert report['response_parts']['content_bytes']>0 and report['response_parts']['tool_count']==0
    assert result['completion_tokens']==50


@pytest.mark.parametrize(
    "case",
    [
        "malformed",
        "utf8",
        "nonobject",
        "duplicate_keys",
        "constant",
        "missing_done",
        "missing_usage",
        "error",
        "bad_delta",
        "model_change",
        "delta_after_finish",
        "done_before_finish",
        "interrupted",
    ],
)
def test_failures_do_not_dispatch_or_hide_usage(http_tape, tmp_path, case):
    head = frame({"content": "literal <script> safe"})
    bad = {
        "malformed": b"data: {oops}\n\n",
        "utf8": b'data: {"x":"\xff"}\n\n',
        "nonobject": b"data: []\n\n",
        "duplicate_keys": b'data: {"choices":[],"choices":[]}\n\n',
        "constant": b'data: {"usage":NaN}\n\n',
        "missing_done": wire([frame(finish="stop")], False),
        "missing_usage": wire([frame(finish="stop")]),
        "error": wire([dict(error=dict(message="PRIVATE_ERROR"))]),
        "bad_delta": wire([dict(choices=[dict(delta="PRIVATE_BAD")])]),
        "model_change": wire([dict(model="PRIVATE_OTHER", choices=[])]),
        "delta_after_finish": wire([frame(finish="stop"), frame({"content": "PRIVATE_LATE"})]),
        "done_before_finish": b"data: [DONE]\n\n",
        "interrupted": b'data: {"choices":',
    }[case]
    http_tape.steps = [wire([head], False) + bad]
    requested = []
    observed = []

    def record(kind, e):
        e = dict(e, request_id="fixture", fence="fixture")
        durable(tmp_path / "attempt" / f"{kind}-00.json", json.dumps(e).encode())
        (requested if kind == "request" else observed).append(e)

    with pytest.raises((PrimaryHeld, BudgetError)):
        implement(
            None,
            "NON-MODEL",
            "",
            "pilot-qwen38-27b",
            lambda: True,
            ownership=http_tape.adapter,
            on_request=lambda e: record("request", e),
            on_turn=lambda e: record("turn", e),
        )
    assert len(http_tape.bodies) == 1 and len(requested) == 1 and len(http_tape.deletes) == 1
    report = http_tape.reports[0]
    assert report["fence_state"] == "released" and report["usage"] is None
    assert "PRIVATE_" not in json.dumps(report)
    assert (
        summarize(tmp_path, "fixture", "fixture", "pilot-qwen38-27b")["usage"]["values"][
            "completion_tokens"
        ]
        is None
    )


def test_receipt_before_late_error_survives_as_usage_not_completed_work(http_tape, tmp_path):
    http_tape.steps = [
        wire(
            [
                frame({"content": "done"}, "stop"),
                dict(choices=[], usage=dict(prompt_tokens=100, completion_tokens=50)),
                dict(error="PRIVATE_ERROR"),
            ]
        )
    ]

    def record(kind, e):
        durable(
            tmp_path / "attempt" / f"{kind}-00.json",
            json.dumps(dict(e, request_id="fixture", fence="fixture")).encode(),
        )

    with pytest.raises(PrimaryHeld):
        implement(
            None,
            "NON-MODEL",
            "",
            "pilot-qwen38-27b",
            lambda: True,
            ownership=http_tape.adapter,
            on_request=lambda e: record("request", e),
            on_turn=lambda e: record("turn", e),
        )
    r = summarize(tmp_path, "fixture", "fixture", "pilot-qwen38-27b")["usage"]
    assert r["values"]["completion_tokens"] == 50 and r["completed_responses"] == 0
    assert r["incomplete_response_receipts"] == 1


def test_assembled_byte_limit_before_oversized_append(http_tape):
    http_tape.steps = [wire([frame({"reasoning_content": "x" * 60000}) for _ in range(5)])]
    with pytest.raises(PrimaryHeld, match="assembled_bytes"):
        http_tape.adapter.request({"max_tokens": 2496}, lambda: True, time.monotonic() + 120)
    r = http_tape.reports[0]
    assert r["output_limits_exceeded"] == ["assembled_bytes"] and r["assembled_bytes"] <= 256 << 10
    assert r["rejected_assembled_bytes"] > 256 << 10 and r["wire_bytes"] < 2 << 20


def test_assembled_exact_boundary_and_utf8_tool_fragments():
    r = dict(choices=[dict(message={"role": "assistant", "content": ""}, finish_reason=None)])
    remaining = ASSEMBLED_BYTES_LIMIT - assembled_size(r)
    merge_chunk(r, dict(choices=[dict(delta={"content": "x" * remaining})]))
    assert assembled_size(r) == ASSEMBLED_BYTES_LIMIT
    with pytest.raises(AssembledLimit):
        merge_chunk(r, dict(choices=[dict(delta={"content": "✓"})]))
    assert assembled_size(r) == ASSEMBLED_BYTES_LIMIT


def test_multiline_crlf_comments_utf8_and_fragmented_reads():
    raw = b': ping\r\nevent: message\r\nid: metadata\r\ndata: {"choices": [],\r\ndata: "model": "\xe4\xb8\xad\xe6\x96\x87"}\r\n\r\n'

    class Short(io.BytesIO):
        def readline(self, n):
            return super().readline(min(n, 2))

    metrics = dict(
        wire_bytes=0,
        wire_lines=0,
        largest_line_bytes=0,
        event_count=0,
        comment_lines=0,
        metadata_lines=0,
    )
    result = list(events(Short(raw), metrics, lambda: True))
    assert decode(result[0], metrics)["model"] == "中文" and metrics["wire_bytes"] == len(raw)
    assert (
        metrics["event_count"] == 1
        and metrics["comment_lines"] == 1
        and metrics["metadata_lines"] == 2
    )


@pytest.mark.parametrize("count", [32768 - 2496, 32768 - 2496 + 1])
def test_actual_token_count_includes_tools_and_replaces_small_json_byte_guard(http_tape, count):
    http_tape.count = count
    http_tape.steps = [final_response()]
    if count == 32768 - 2496:
        assert (
            implement(
                None,
                "NON-MODEL " + "long untrusted context " * 2000,
                "",
                "pilot-qwen38-27b",
                lambda: True,
                ownership=http_tape.adapter,
            )["completion_tokens"]
            == 50
        )
        assert http_tape.counts[0]["tools"] and len(json.dumps(http_tape.counts[0])) > 32768
    else:
        with pytest.raises(BudgetError, match="Tokenized context"):
            implement(
                None, "NON-MODEL", "", "pilot-qwen38-27b", lambda: True, ownership=http_tape.adapter
            )
        assert not http_tape.bodies


@pytest.mark.parametrize("tokens,finish", [(2497, "stop"), (50, "length")])
def test_streamed_token_limit_and_truncation_never_retry(http_tape, tokens, finish):
    http_tape.steps = [
        wire(
            [
                frame({"content": "NON-MODEL"}, finish),
                dict(choices=[], usage=dict(prompt_tokens=100, completion_tokens=tokens)),
            ]
        )
    ]
    with pytest.raises(BudgetError):
        implement(
            None, "NON-MODEL", "", "pilot-qwen38-27b", lambda: True, ownership=http_tape.adapter
        )
    assert len(http_tape.bodies) == 1


def test_installed_names_ids_are_snapshots_arguments_are_deltas():
    r = dict(choices=[dict(message={"role": "assistant"}, finish_reason=None)])
    merge_chunk(
        r,
        frame(
            {
                "tool_calls": [
                    dict(index=0, id="call-fixed", function=dict(name="read_", arguments='{"p'))
                ]
            }
        ),
    )
    merge_chunk(
        r,
        frame(
            {
                "tool_calls": [
                    dict(
                        index=0, id="call-fixed", function=dict(name="read_file", arguments='ath":')
                    )
                ]
            }
        ),
    )
    merge_chunk(
        r,
        frame(
            {
                "tool_calls": [
                    dict(
                        index=0, id="call-fixed", function=dict(name="read_file", arguments='"✓"}')
                    )
                ]
            }
        ),
    )
    call = r["choices"][0]["message"]["tool_calls"][0]
    assert call["id"] == "call-fixed" and call["function"] == dict(
        name="read_file", arguments='{"path":"✓"}'
    )
    with pytest.raises(PrimaryHeld, match="identity"):
        merge_chunk(r, frame({"tool_calls": [dict(index=0, function=dict(name="edit_file"))]}))


def test_usage_diagnostics_discard_strings_and_validate_numeric_totals(http_tape):
    http_tape.steps = [
        wire(
            [
                frame({"content": "done"}, "stop"),
                dict(
                    choices=[],
                    usage=dict(
                        prompt_tokens=100,
                        completion_tokens=50,
                        total_tokens=150,
                        secret="PRIVATE",
                        prompt_tokens_details=dict(cached_tokens=0, secret="PRIVATE"),
                    ),
                ),
            ]
        )
    ]
    http_tape.adapter.request({"max_tokens": 2496}, lambda: True, time.monotonic() + 120)
    assert "PRIVATE" not in json.dumps(http_tape.reports)
    assert http_tape.reports[0]["usage"]["total_tokens"] == 150
    for usage in [
        dict(prompt_tokens="PRIVATE"),
        dict(prompt_tokens=True),
        dict(prompt_tokens=1, completion_tokens=2, total_tokens=4),
    ]:
        with pytest.raises(PrimaryHeld):
            merge_chunk(dict(choices=[dict(message={}, finish_reason=None)]), dict(usage=usage))


@pytest.mark.parametrize("kind,extra", [("line", 0), ("line", 1), ("raw", 0), ("raw", 1)])
def test_wire_and_line_exact_boundaries(http_tape, kind, extra):
    tail = final_response()
    if kind == "line":
        payload = b":" + b"x" * (65536 - 2 + extra) + b"\n" + tail
    else:
        left = (2 << 20) - len(tail)
        padding = []
        while left:
            n = min(65536, left)
            padding.append(b":" + b"x" * (n - 2) + b"\n" if n >= 2 else b"\n")
            left -= n
        payload = b"\n" * extra + b"".join(padding) + tail
    http_tape.steps = [payload]
    if extra:
        with pytest.raises(PrimaryHeld, match="bounded output"):
            http_tape.adapter.request({"max_tokens": 2496}, lambda: True, time.monotonic() + 120)
        assert http_tape.reports[0]["output_limits_exceeded"] == [
            "line_bytes" if kind == "line" else "stream_bytes"
        ]
    else:
        http_tape.adapter.request({"max_tokens": 2496}, lambda: True, time.monotonic() + 120)
        assert http_tape.reports[0]["wire_bytes"] == len(payload)
        assert http_tape.reports[0]["largest_line_bytes"] == 65536


@pytest.mark.parametrize("identity", ["", "duplicate"])
def test_ambiguous_completed_calls_fail_before_any_dispatch(http_tape, identity):
    calls = [
        dict(
            index=i,
            id=identity,
            function=dict(name="run_check", arguments='{"name":"syntax-data"}'),
        )
        for i in range(2)
    ]
    http_tape.steps = [
        wire(
            [
                frame({"tool_calls": calls}, "tool_calls"),
                dict(choices=[], usage=dict(prompt_tokens=100, completion_tokens=50)),
            ]
        )
    ]

    class Tools:
        def handle(self, *args):
            pytest.fail("Malformed stream dispatched a tool")

    with pytest.raises(PrimaryHeld, match="identity"):
        implement(
            Tools(), "NON-MODEL", "", "pilot-qwen38-27b", lambda: True, ownership=http_tape.adapter
        )
    assert len(http_tape.bodies) == 1


@pytest.mark.parametrize("failure_via_tool", [False, True])
def test_complete_working_session_failed_final_feedback_repair_and_handoff(
    http_tape, tmp_path, monkeypatch, failure_via_tool
):
    """NON-MODEL HTTP tape, comment-only edits and stub checks; never task success."""
    out = tmp_path / "NON_MODEL_REPAIR_CONTROL"
    out.mkdir()
    runtime = Runtime(out)
    runtime.prepare()
    try:
        runtime.primary = http_tape.adapter
        monkeypatch.setattr(
            runtime, "preflight", lambda: [dict(name="NON-MODEL gates", status="passed")]
        )
        monkeypatch.setattr(
            "workbench.real_workspace.confine.root", lambda: str(tmp_path / "NON_MODEL_CGROUP")
        )
        path = runtime.workspace / job.PATHS[0]
        first = path.read_text().splitlines(True)[0]
        incomplete = "-- NON_MODEL_INCOMPLETE, discarded fixture\n"
        repaired = "-- NON_MODEL_REPAIRED, discarded fixture\n"
        checked = []

        def check(argv, cwd, ro=(), timeout=120, private_loopback=False):
            checked.append(argv)
            failing = str(out / "oracle.lua") in argv and incomplete in path.read_text()
            return dict(
                status="fail" if failing else "pass",
                rc=1 if failing else 0,
                out="NON-MODEL stub: callback must receive unavailable for an invalid total"
                if failing
                else "\n".join("ok   " + n for n in runtime.expected_suite())
                + f"\n{len(runtime.expected_suite())} passed, 0 failed",
                command=argv,
                network_mode="private-loopback" if private_loopback else "denied",
            )

        monkeypatch.setattr(runtime, "check", check)

        def after_failure(body):
            feedback = body["messages"][-1]["content"]
            assert "callback must receive unavailable" in feedback
            if failure_via_tool:
                assert json.loads(feedback)["status"] == "fail"
            else:
                assert "Independent candidate checks failed" in feedback
            names = {t["function"]["name"] for t in body["tools"]}
            assert names == {"replace_text", "read_file", "run_check"}
            return tool_response(
                [
                    ("read_file", dict(path=job.PATHS[0], start=1, end=1)),
                    ("replace_text", dict(path=job.PATHS[0], old=incomplete, new=repaired)),
                ]
            )

        http_tape.steps = [
            tool_response([("read_file", dict(path=job.PATHS[0], start=1, end=1))] * 4),
            tool_response(
                [("replace_text", dict(path=job.PATHS[0], old=first, new=incomplete + first))]
            ),
            tool_response([("run_check", dict(name="candidate"))])
            if failure_via_tool
            else final_response(),
            after_failure,
            tool_response([("run_check", dict(name="candidate"))]),
            final_response(),
        ]
        result = runtime.execute("NON-MODEL-REQUEST", "NON-MODEL-FENCE", lambda: True)
        assert result["verification"] == "passed" and runtime.verify_frozen(result)
        assert (
            result["usage"]["values"]["completion_tokens"] == 300
            and result["correction_count"] == 0
        )
        assert len(http_tape.bodies) == 6 and len(checked) == 11
        assert json.loads((out / "working-checks/00/outcome.json").read_text())["status"] == "fail"
        assert json.loads((out / "working-checks/01/outcome.json").read_text())["status"] == "pass"
        assert (out / "working-checks/00/files" / job.PATHS[0]).read_text().startswith(incomplete)
        assert (out / "working-checks/01/files" / job.PATHS[0]).read_text().startswith(repaired)
        assert len(list((out / "candidates").iterdir())) == 1
    finally:
        shutil.rmtree(runtime.workspace.parent)


@pytest.mark.parametrize(
    "status,rc,usage",
    [
        ("timeout", None, {}),
        ("error", 97, {}),
        ("fail", 1, {"oom_kill": True}),
        ("fail", 1, {"pids_limit_hit": True}),
        ("pass", 0, {"cgroup_removed": False}),
        ("pass", 0, {"leaked_after_kill": True}),
    ],
)
def test_candidate_infrastructure_or_budget_cannot_be_normal_repair(
    tmp_path, monkeypatch, status, rc, usage
):
    from workbench.real_tool_contract import ToolDenied

    out = tmp_path / "NON_MODEL_INFRA"
    out.mkdir()
    runtime = Runtime(out)
    runtime.prepare()
    try:
        runtime._context_sha = None
        runtime._request_id = "NON-MODEL"
        runtime._fence = "NON-MODEL"
        p = runtime.workspace / job.PATHS[0]
        p.write_text("-- NON-MODEL discarded\n" + p.read_text())
        monkeypatch.setattr(
            runtime,
            "check",
            lambda *a, **k: dict(status=status, rc=rc, usage=usage, out="NON-MODEL"),
        )
        with pytest.raises(ToolDenied, match="infrastructure"):
            runtime.candidate_feedback(lambda: True)
        assert (out / "working-checks/00/check-01.json").exists()
        assert not hasattr(runtime, "_last_candidate_check")
    finally:
        shutil.rmtree(runtime.workspace.parent)


def test_candidate_out_of_scope_change_never_becomes_repair_feedback(tmp_path):
    from workbench.real_tool_contract import ToolDenied

    out = tmp_path / "NON_MODEL_SCOPE"
    out.mkdir()
    runtime = Runtime(out)
    runtime.prepare()
    try:
        runtime._context_sha = None
        runtime._request_id = "NON-MODEL"
        runtime._fence = "NON-MODEL"
        (runtime.workspace / "UNAUTHORIZED").write_text("NON-MODEL")
        with pytest.raises(ValueError, match="out-of-scope"):
            runtime.candidate_feedback(lambda: True)
        with pytest.raises(ToolDenied):
            runtime.candidate_feedback(lambda: False)
        assert not (out / "working-checks").exists()
    finally:
        shutil.rmtree(runtime.workspace.parent)
