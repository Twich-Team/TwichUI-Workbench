"""Actual decoding, completion/accounting gates and private pre-validation evidence."""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import urllib.request
from uuid import uuid4

import pytest
from pydantic import ValidationError

from test_bug_workflow import response
from workbench.bug_evidence import BugPlanningResponse
from workbench.planning_provider import LocalProvider, PlanningUnavailable
from workbench.planning_response import Evidence, MAX_EVIDENCE, payload, wire_schema
from workbench.planning_response import ResponseValidationError, strict_json

MODEL = "controlled-model"


@pytest.mark.parametrize("wrapper", ["plain", "bom", "fence"])
def test_only_unambiguous_formatting_is_normalized(wrapper):
    value = response()
    value.update(question="  ", limitation="")
    text = json.dumps(value)
    text = (
        "\ufeff" + text
        if wrapper == "bom"
        else "```json\n" + text + "\n```"
        if wrapper == "fence"
        else text
    )
    decoded, changes = payload(text, bug=True)
    assert decoded == response()
    assert "empty-question" in changes and "empty-limitation" in changes


@pytest.mark.parametrize(
    "text",
    [
        '{"finding":',
        '{"finding":"a","finding":"b"}',
        "[]",
        "before\n```json\n{}\n```",
        "{} {}",
        '{"number":NaN}',
    ],
)
def test_malformed_ambiguous_and_nonfinite_text_is_not_repaired(text):
    with pytest.raises(ValueError):
        payload(text, bug=True)


def test_missing_conflicting_and_out_of_scope_content_still_fails():
    missing = response()
    del missing["finding"]
    conflict = response()
    conflict["question"] = "Another outcome"
    outside = response()
    outside["plan"]["writable_paths"] = ["../credentials"]
    for value in (missing, conflict, outside):
        decoded, _ = payload(json.dumps(value), bug=True)
        with pytest.raises(ValidationError):
            BugPlanningResponse.model_validate(decoded)


@pytest.mark.parametrize(
    "fault,state,category",
    [
        ("good", "finished", None),
        ("payload", "finished", "invalid_json"),
        ("envelope", "uncertain", "invalid_json"),
        ("duplicate", "finished", "invalid_json"),
        ("choices", "uncertain", "invalid_json"),
        ("truncated", "partial", "incomplete_output"),
        ("unknown-finish", "uncertain", "incomplete_output"),
        ("tools", "finished", "invalid_json"),
        ("usage", "finished", "accounting"),
        ("negative", "finished", "accounting"),
        ("identity", "finished", "model_identity"),
        ("journal-full", "uncertain", "diagnostic_io"),
    ],
)
def test_wire_is_durable_before_parse_and_classification_is_conservative(
    tmp_path, monkeypatch, fault, state, category
):
    evidence = Evidence(tmp_path, str(uuid4()), BugPlanningResponse)
    if fault == "journal-full":
        os.write(evidence.fd, b"x" * (MAX_EVIDENCE - evidence.file.stat().st_size))
    provider = LocalProvider()
    provider.response_evidence = evidence
    calls = []
    value = response()
    envelope = dict(
        id="controlled-response",
        model=MODEL,
        usage=dict(prompt_tokens=40, completion_tokens=10, total_tokens=50),
        choices=[dict(finish_reason="stop", message=dict(content=json.dumps(value)))],
    )
    if fault == "payload":
        envelope["choices"][0]["message"]["content"] = '{"finding":'
    if fault == "duplicate":
        envelope["choices"][0]["message"]["content"] = '{"finding":"a","finding":"b"}'
    if fault == "truncated":
        envelope["choices"][0]["finish_reason"] = "length"
    if fault == "unknown-finish":
        envelope["choices"][0].pop("finish_reason")
    if fault == "tools":
        envelope["choices"][0]["message"]["tool_calls"] = [{"name": "forbidden"}]
    if fault == "usage":
        envelope.pop("usage")
    if fault == "negative":
        envelope["usage"]["completion_tokens"] = -1
    if fault == "identity":
        envelope["model"] = "different-model"
    if fault == "choices":
        envelope["choices"] *= 2
    wire = json.dumps(envelope).encode() if fault != "envelope" else b'{"choices":'

    class Transport:
        seconds = 120
        response_observer = None

        def models(self, _):
            return MODEL

        def read(self, request, limit):
            calls.append(request.data)
            assert len(request.data) < 24000
            if self.response_observer:
                self.response_observer(wire, True)
            return wire

    import workbench.planning_response as module

    decode = module.payload

    def inspect_before_decode(text, **kwargs):
        events = [json.loads(row) for row in evidence.file.read_text().splitlines()]
        assert [row["phase"] for row in events][:3] == ["schema", "wire-received", "envelope"]
        assert events[2]["usage"]["values"]["total_tokens"] == 50
        return decode(text, **kwargs)

    monkeypatch.setattr(module, "payload", inspect_before_decode)
    if category:
        with pytest.raises(PlanningUnavailable) as error:
            provider._infer(Transport(), "inert", BugPlanningResponse, "{}", expected_model=MODEL)
        assert error.value.reason == category
    else:
        result, model, basis = provider._infer(
            Transport(), "inert", BugPlanningResponse, "{}", expected_model=MODEL
        )
        assert (result, model, basis) == (value, MODEL, "local")
    assert len(calls) == 1 and provider.diagnostics["generation_state"] == state
    assert evidence.file.stat().st_mode & 0o777 == 0o600
    assert evidence.directory.stat().st_mode & 0o777 == 0o700
    assert evidence.file.stat().st_size <= MAX_EVIDENCE
    evidence.close()


def test_private_validation_has_constraint_and_observed_type_without_source_or_secrets(tmp_path):
    evidence = Evidence(tmp_path, str(uuid4()), BugPlanningResponse)
    value = response()
    value["finding"] = "SENSITIVE_SOURCE_AND_CREDENTIAL" * 40
    evidence.parsed(value, dict(generation_state="finished"))
    with pytest.raises(ValidationError) as error:
        BugPlanningResponse.model_validate(value)
    evidence.rejected(error.value, value)
    rows = [json.loads(row) for row in evidence.file.read_text().splitlines()]
    last = rows[-1]["errors"][0]
    assert last["path"] == ["finding"] and last["constraint"] == "string_too_long"
    assert last["expected"]["max_length"] == 450 and last["observed"]["type"] == "str"
    assert last["observed"]["length"] == len(value["finding"])
    assert "SENSITIVE" not in evidence.file.read_text()
    evidence.close()


def test_private_evidence_is_bounded_and_cannot_reuse_a_symlink(tmp_path):
    identifier = str(uuid4())
    evidence = Evidence(tmp_path, identifier, BugPlanningResponse)
    os.write(evidence.fd, b"x" * (MAX_EVIDENCE - evidence.file.stat().st_size))
    with pytest.raises(OSError):
        evidence.received(b"new response")
    assert evidence.file.stat().st_size == MAX_EVIDENCE
    evidence.close()
    link = tmp_path / "linked"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        Evidence(link, str(uuid4()), BugPlanningResponse)
    with pytest.raises(FileExistsError):
        Evidence(tmp_path, identifier, BugPlanningResponse)


def test_wire_schema_enforces_load_and_call_shapes_without_changing_local_authority():
    variants = wire_schema(BugPlanningResponse)["$defs"]["Step"]["anyOf"]
    by_op = {v["properties"]["op"]["const"]: v for v in variants}
    assert by_op["load"]["required"] == ["op", "source"]
    assert by_op["load"]["properties"]["source"]["type"] == "string"
    assert by_op["call"]["properties"]["source"]["const"] is None
    assert by_op["table"]["properties"]["args"]["maxItems"] == 0
    bad = response()
    bad["plan"]["reproducer"]["steps"][1]["source"] = "!!!TwichUI/other.lua"
    with pytest.raises(ValidationError):
        BugPlanningResponse.model_validate(bad)


@pytest.mark.parametrize(
    "raw,path",
    [
        ('{"plan":{"finding":1,"finding":2}}', ["plan", "finding"]),
        ('{"finding":NaN}', ["finding"]),
        ('{"finding":"\\ud800"}', ["finding"]),
        ('{"finding":1e999}', ["finding"]),
    ],
)
def test_structural_rejections_keep_the_exact_field_path(tmp_path, raw, path):
    evidence = Evidence(tmp_path, str(uuid4()), BugPlanningResponse)
    with pytest.raises(ResponseValidationError) as error:
        strict_json(raw)
    evidence.rejected(error.value, phase="payload")
    row = json.loads(evidence.file.read_text().splitlines()[-1])
    assert row["errors"][0]["path"] == path
    assert row["errors"][0]["expected"]
    evidence.close()


def test_excessive_json_depth_is_a_local_rejection_not_a_worker_crash():
    with pytest.raises(ResponseValidationError, match="nesting limit"):
        strict_json("[" * 2000 + "0" + "]" * 2000)


@pytest.mark.parametrize("mode", ["complete", "oversized", "cancel-after-body"])
def test_real_http_receipt_precedes_limit_and_cancellation_checks(tmp_path, mode):
    from workbench.planning_transport import LocalHTTP

    body = b"{}" if mode != "oversized" else b"x" * 32

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    evidence = Evidence(tmp_path, str(uuid4()), BugPlanningResponse)
    stop = threading.Event()

    def observed(raw, complete):
        evidence.received(raw, complete=complete)
        if mode == "cancel-after-body":
            stop.set()

    try:
        with LocalHTTP(stop) as transport:
            transport.response_observer = observed
            request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/")
            if mode == "complete":
                assert transport.read(request, 16) == body
            else:
                with pytest.raises(PlanningUnavailable) as error:
                    transport.read(request, 16)
                assert error.value.reason == ("output_limit" if mode == "oversized" else "canceled")
        rows = [json.loads(row) for row in evidence.file.read_text().splitlines()]
        assert rows[-1]["phase"] == "wire-received"
        assert rows[-1]["bytes"] == (17 if mode == "oversized" else len(body))
        assert rows[-1]["bounded_prefix"] == (mode == "oversized")
    finally:
        evidence.close()
        server.shutdown()
        thread.join(2)
        server.server_close()
