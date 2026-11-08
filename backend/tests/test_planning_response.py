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


def test_long_investigation_journal_keeps_terminal_diagnostics_with_same_byte_limit(tmp_path):
    evidence = Evidence(tmp_path,str(uuid4()),BugPlanningResponse)
    try:
        for index in range(48):
            evidence.record('source-followup',turn=index+1,evidence_id=str(uuid4()))
        evidence.record('validated',generation_state='finished')
        assert evidence.file.stat().st_size < MAX_EVIDENCE == 65536
        rows=list(map(json.loads,evidence.file.read_text().splitlines()))
        assert rows[-1]['phase']=='validated'
        os.write(evidence.fd,b'x'*(MAX_EVIDENCE-evidence.file.stat().st_size))
        with pytest.raises(OSError,match='limit'):
            evidence.record('validated',generation_state='finished')
    finally:
        evidence.close()


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
    if fault in {'good', 'payload', 'duplicate', 'truncated', 'unknown-finish', 'tools', 'usage', 'negative', 'identity'}:
        retained=json.loads((evidence.directory/'provider-content.json').read_text())
        assert retained['text']==envelope['choices'][0]['message']['content']
        assert retained['truncated'] is False
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
    schema = wire_schema(BugPlanningResponse)
    variants = [value for value in schema['$defs'].values()
                if value.get('properties', {}).get('op', {}).get('const')]
    by_op = {v["properties"]["op"]["const"]: v for v in variants}
    assert "load" not in by_op  # Explicit setup selects source loads.
    setup = schema["$defs"]["Reproducer"]["properties"]["setup"]["anyOf"]
    assert any(choice["properties"]["kind"]["const"] == "module" for choice in setup)
    assert "source" not in by_op["call"]["properties"]
    assert "args" not in by_op["table"]["properties"]
    bad = response()
    bad["plan"]["reproducer"]["steps"][1]["source"] = "!!!TwichUI/other.lua"
    with pytest.raises(ValidationError):
        BugPlanningResponse.model_validate(bad)


def test_wire_outcomes_are_mutually_exclusive_and_unwrap_without_choosing():
    from workbench.planning_response import normalize_bug

    schema = wire_schema(BugPlanningResponse)
    assert set(schema["properties"]) == {"finding", "draft"}
    for selected, variant in zip(
        ("plan", "question", "limitation", "inspection"), schema["$defs"]["BugOutcome"]["anyOf"], strict=True
    ):
        assert variant["required"] == [selected]
        assert set(variant["properties"]) == {selected}
        assert variant["properties"][selected].get("type") != "null"
        assert all(
            v.get("const") is None and v.get("type") == "null"
            for key, v in variant["properties"].items()
            if key != selected
        )
    value = response()
    wrapped = dict(
        finding=value["finding"],
        draft={key: value[key] for key in ("plan", "question", "limitation")},
    )
    decoded, changes = normalize_bug(wrapped)
    assert decoded == value and changes == ["unwrap-outcome-draft"]
    wrapped["draft"]["question"] = "A genuine question is not discarded."
    decoded, _ = normalize_bug(wrapped)
    with pytest.raises(ValidationError, match="exactly one"):
        BugPlanningResponse.model_validate(decoded)


def test_repeat_coverage_is_generic_and_does_not_invent_observations():
    from workbench.bug_investigation import report_coverage

    basic = dict(
        title="Name conversion", description="Entering ALICE returns uppercase. Expect lowercase."
    )
    repeat = dict(
        title="Refresh cached settings",
        description="Relearn the settings each time the panel opens.",
    )
    assert report_coverage(basic)["minimum_observations"] == 1
    coverage = report_coverage(repeat)
    assert coverage["minimum_observations"] == 3
    schema = wire_schema(BugPlanningResponse, coverage)
    assert schema["$defs"]["Reproducer"]["properties"]["checkpoints"]["minItems"] == 3
    assert (
        BugPlanningResponse.model_json_schema()["$defs"]["Reproducer"]["properties"][
            "observations"
        ]["minItems"]
        == 1
    )
    ordinary = response()
    assert len(BugPlanningResponse.model_validate(ordinary).plan.reproducer.observations) == 1
    from workbench.bug_investigation import resolve

    with pytest.raises(ResponseValidationError) as error:
        resolve(ordinary, dict(files=[]), repeat, "")
    assert error.value.path == ["plan", "reproducer", "observations"]
    assert len(ordinary["plan"]["reproducer"]["observations"]) == 1


def test_wire_format_excerpt_contains_neither_values_nor_untrusted_keys(tmp_path):
    evidence = Evidence(tmp_path, str(uuid4()), BugPlanningResponse)
    evidence.received(
        b'{"PRIVATE_SECRET_KEY":"SENSITIVE_TOKEN_VALUE", "finding":"unterminated secret'
    )
    row = json.loads(evidence.file.read_text().splitlines()[-1])
    assert "<redacted>" in row["format_excerpt"]
    assert "PRIVATE_SECRET_KEY" not in evidence.file.read_text()
    assert "SENSITIVE_TOKEN_VALUE" not in evidence.file.read_text()
    assert "unterminated secret" not in evidence.file.read_text()
    assert len(row["format_excerpt"]) <= 512
    evidence.close()


def test_declaration_context_is_literal_selected_bounded_and_not_an_export_claim():
    from workbench.bug_investigation import declaration_index, json_bytes

    selected = "!!!TwichUI/cache.lua"
    hidden = "!!!TwichUI/unselected.lua"
    lines = ['-- function Fake() end', 'local s="function Secret()"', '',
             'function Cache:Refresh(value)', 'end']
    lines += [f'function Cache:Other{index}() end' for index in range(100)]
    inspected = {selected: (None, None, lines), hidden: (None, None, ['function Hidden() end'])}
    result = declaration_index([dict(path=selected)], inspected, {'refresh'})
    assert result[0]['header'] == 'function Cache:Refresh(value)'
    assert result[0]['line'] == 4
    assert result[0]['kind'] == 'literal declaration; export unverified'
    assert len(result) <= 12 and len(json_bytes(result)) <= 1200
    assert all(item['path'] == selected for item in result)
    assert not any(word in json_bytes(result).decode() for word in ['Fake', 'Secret', 'Hidden'])


def test_wire_paths_cannot_offer_support_only_or_undiscovered_writes():
    files = [dict(path='!!!TwichUI/a.lua'), dict(path='!!!TwichUI/helper.lua', support_only=True),
             dict(path='tests/test_a.lua')]
    schema = wire_schema(BugPlanningResponse, files=files)
    properties = schema['$defs']['BugPlan']['properties']
    assert properties['writable_paths']['items']['enum'] == ['!!!TwichUI/a.lua', 'tests/test_a.lua']
    assert properties['other_reads']['items']['enum'] == [item['path'] for item in files]
    assert properties['focused_test']['enum'] == ['tests/test_a.lua']


def test_oracle_reports_every_original_and_candidate_observation(tmp_path):
    import subprocess
    from test_bug_process_rehearsal import three_observations
    from test_verification_profiles import BUGGY, CORRECT, PRODUCTION
    from workbench.bug_evidence import compile_plan, review_digest

    plan = BugPlanningResponse.model_validate(three_observations(False)).plan.model_dump(mode='json')
    spec = dict(
        plan=plan,
        bootstrap_source="",
        windows=[dict(path=PRODUCTION, start=1, end=1)] * 4,
        review_digest=review_digest(plan, ""),
    )
    oracle = tmp_path / "oracle.lua"
    oracle.write_text(compile_plan(spec)["oracle"])
    test_dir = tmp_path / "tests"
    test_dir.mkdir()
    source = tmp_path / PRODUCTION
    source.parent.mkdir()
    for contents, expected_code, marker in [(BUGGY, 1, "incorrect"), (CORRECT, 0, "expected")]:
        source.write_text(contents)
        result = subprocess.run(
            [
                "lua5.1",
                "-e",
                "package.preload.bit=function()return{}end;ROOT='../!!!TwichUI/';TESTS='./';REPO='../'",
                str(oracle),
            ],
            cwd=test_dir,
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode == expected_code, result.stdout + result.stderr
        assert all(
            f"WB_OBSERVATION bug-reproducer {index} {marker}" in result.stdout
            for index in [1, 2, 3]
        )


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


def test_fixture_diagnostics_preserve_operation_and_null_pair_without_source_or_secrets(tmp_path):
    from test_bug_wire import wire_response
    value = wire_response(True)
    step = value['plan']['reproducer']['steps'][-1]
    step.update(path=['Visible','SENSITIVE_TOKEN_VALUE'], args=['SENSITIVE_TOKEN_VALUE'])
    observation = value['plan']['reproducer']['observations'][0]
    observation.update(expected=None, incorrect=None)
    files = [dict(path='!!!TwichUI/alerts.lua', excerpts=[dict(text='function Visible() end -- HIDDEN_COMMENT\nlocal x="HIDDEN_LITERAL"')])]
    evidence = Evidence(tmp_path, str(uuid4()), BugPlanningResponse, files=files)
    evidence.parsed(value, {})
    rows = [json.loads(line) for line in evidence.file.read_text().splitlines()]
    fact = next(r for r in rows if r['phase']=='fixture-diagnostics')
    assert fact['steps'][-1]['path'][0]=='Visible'
    assert fact['observations'][0]['expected']==dict(type='NoneType',value=None)
    assert fact['observations'][0]['incorrect']==dict(type='NoneType',value=None)
    assert fact['observations'][0]['expected_present']
    assert fact['executable'] is False and fact['approved'] is False
    text=evidence.file.read_text()
    assert not any(s in text for s in ['SENSITIVE_TOKEN_VALUE','HIDDEN_COMMENT','HIDDEN_LITERAL','function Visible'])
    assert len(text.encode())<=65536
    evidence.close()


@pytest.mark.parametrize('value', [None, [], {'plan':None}, {'plan':{'reproducer':[]}},
    {'plan':{'reproducer':{'setup':{'kind':[]},'steps':[{'op':[]}],
       'observations':[{'source':{},'after_step':[], 'path':[{}]}]}}}])
def test_fixture_diagnostics_do_not_discard_malformed_response(value):
    from workbench.planning_fixture_diagnostics import fixture_snapshot
    snapshot=fixture_snapshot(value,[],lambda v:dict(type=type(v).__name__))
    if snapshot:
        assert snapshot['executable'] is False


def test_fixture_diagnostics_bound_hostile_lists_and_keep_observation_presence():
    from workbench.planning_fixture_diagnostics import fixture_snapshot
    fixture=dict(steps=[dict(op='call',path=['secret']*8,args=['secret']*8)]*1000,
                 observations=[dict(after_step=2,source='return',path=[1],incorrect=None)])
    snapshot=fixture_snapshot(dict(plan=dict(reproducer=fixture)),[],lambda v:dict(type=type(v).__name__,sha256='x'*64))
    assert len(json.dumps(snapshot).encode())<=6000 and snapshot['truncated']
    assert snapshot['observations'][0]['expected_present'] is False


def test_checkpoint_diagnostics_associate_selected_calls_with_results(tmp_path):
    from test_bug_wire import wire_from_data
    wire=wire_from_data(response())
    evidence=Evidence(tmp_path,str(uuid4()),BugPlanningResponse)
    evidence.parsed(wire,{})
    rows=[json.loads(x) for x in evidence.file.read_text().splitlines()]
    snapshot=next(r for r in rows if r['phase']=='fixture-diagnostics')
    assert snapshot['wire_format']=='call-checkpoints'
    assert snapshot['legacy_conflict'] is False
    assert snapshot['checkpoint_calls'][0]['call_step']==1
    assert snapshot['steps'][0]['op']=='call' and len(snapshot['observations'])==1
    assert snapshot['executable'] is False
    evidence.close()


def test_inspection_schema_omits_unreachable_programs_and_all_references_resolve():
    schema=wire_schema(BugPlanningResponse,dict(require_inspection=True))
    assert 'BugPlan' not in schema['$defs'] and 'Reproducer' not in schema['$defs']
    assert len(json.dumps(schema).encode()) < 2500
    def walk(value):
        if isinstance(value,dict):
            if '$ref' in value:
                assert value['$ref'].split('/')[-1] in schema['$defs']
            for child in value.values():
                walk(child)
        elif isinstance(value,list):
            for child in value:
                walk(child)
    walk(schema)
    assert 'BugPlan' in wire_schema(BugPlanningResponse)['$defs']


@pytest.mark.parametrize('content_present',[True,False])
def test_bounded_bug_reasoning_is_separate_from_plan_and_counted_in_total(tmp_path,content_present):
    evidence=Evidence(tmp_path,str(uuid4()),BugPlanningResponse)
    provider=LocalProvider()
    provider.response_evidence=evidence
    class Transport:
        seconds=120
        def models(self,_):
            return MODEL
        def read(self,request,limit):
            body=json.loads(request.data)
            assert body['reasoning_budget_tokens']==1024
            assert body['max_tokens']==2600 and body['reasoning_format']=='deepseek'
            assert body['chat_template_kwargs']==dict(enable_thinking=True)
            message=dict(reasoning_content='PRIVATE_REASONING_DO_NOT_STORE',
                         content=json.dumps(response()) if content_present else None)
            return json.dumps(dict(id='controlled-reasoning',model=MODEL,
                usage=dict(prompt_tokens=40,completion_tokens=1100,total_tokens=1140),
                choices=[dict(finish_reason='stop',message=message)])).encode()
    if content_present:
        decoded,_,_=provider._infer(Transport(),'inert',BugPlanningResponse,'{}',expected_model=MODEL)
        assert decoded==response()
    else:
        with pytest.raises(PlanningUnavailable):
            provider._infer(Transport(),'inert',BugPlanningResponse,'{}',expected_model=MODEL)
    assert provider.diagnostics['usage']['values']['completion_tokens']==1100
    assert provider.diagnostics['reasoning_channel_present'] is True
    raw=evidence.file.read_text()
    assert 'PRIVATE_REASONING_DO_NOT_STORE' not in raw
    assert '"retained": false' in raw
    evidence.close()


def test_single_outcome_wire_compiles_only_explicit_selection_and_keeps_content_strict():
    original=response()
    wire=dict(finding=original['finding'],draft=dict(plan=original['plan']))
    decoded,changes=payload(json.dumps(wire),bug=True)
    assert decoded==dict(original,inspection=None) and changes==['unwrap-single-outcome']
    for draft in [dict(plan={}),dict(plan=None),dict(plan=original['plan'],question='conflict'),dict(unknown='private')]:
        decoded,_=payload(json.dumps(dict(finding=original['finding'],draft=draft)),bug=True)
        with pytest.raises(ValueError):
            BugPlanningResponse.model_validate(decoded)
    untouched=dict(finding=original['finding'],plan=original['plan'])
    decoded,_=payload(json.dumps(untouched),bug=True)
    with pytest.raises(ValueError):
        BugPlanningResponse.model_validate(decoded)


def test_private_proposal_and_review_snapshots_persist_without_public_values(tmp_path):
    with_evidence=Evidence(tmp_path,str(uuid4()),BugPlanningResponse)
    try:
        candidate=dict(finding='PRIVATE_PROPOSED_DATA',plan=None)
        with_evidence.snapshot('proposed-plan',candidate)
        path=with_evidence.directory/'proposed-plan.json'
        assert json.loads(path.read_text())==candidate
        assert path.stat().st_mode & 0o077==0 and path.stat().st_nlink==1
        assert 'PRIVATE_PROPOSED_DATA' not in with_evidence.file.read_text()
        with pytest.raises(OSError):
            with_evidence.snapshot('proposed-plan',dict(changed=True))
        assert json.loads(path.read_text())==candidate
        with pytest.raises(OSError):
            with_evidence.snapshot('../outside',dict(data=True))
        with pytest.raises(OSError):
            with_evidence.snapshot('model-plan-review',dict(data='x'*24000))
        outside=tmp_path/'outside'
        outside.write_text('KEEP')
        (with_evidence.directory/'model-plan-review.json').symlink_to(outside)
        with pytest.raises(OSError):
            with_evidence.snapshot('model-plan-review',dict(data=True))
        assert outside.read_text()=='KEEP'
    finally:
        with_evidence.close()


def test_private_snapshots_share_the_total_evidence_budget(tmp_path):
    journal=Evidence(tmp_path,str(uuid4()),BugPlanningResponse)
    try:
        journal.snapshot('proposed-plan',dict(data='x'*23000))
        journal.snapshot('model-plan-review',dict(data='x'*23000))
        with pytest.raises(OSError):
            journal.record('bounded-padding',value='x'*15000)
        assert sum(p.stat().st_size for p in journal.directory.iterdir())<=MAX_EVIDENCE
    finally:
        journal.close()


@pytest.mark.parametrize('bug_review',[False,True,None])
def test_bug_transport_timeout_is_explicit_and_shared_deadline_still_wins(monkeypatch,tmp_path,bug_review):
    import time
    from contextlib import nullcontext
    from workbench import planning_provider
    from workbench.bug_plan_review import ReviewResponse
    from workbench.regression_evidence import ScenarioResponse
    from workbench.planning_transport import LocalHTTP
    monkeypatch.setenv('WORKBENCH_PLANNING_DEADLINE_S','60')
    key=tmp_path/'fixture-key'
    key.write_text('CONTROLLED-NOT-A-CREDENTIAL')
    monkeypatch.setattr(planning_provider,'local_slot',lambda:nullcontext())
    class Probe(LocalHTTP):
        def __enter__(self):
            return self
        def __exit__(self,*args):
            pass
    monkeypatch.setattr(planning_provider,'LocalHTTP',Probe)
    seen=[]
    def infer(self,transport,*args,**kwargs):
        seen.append((transport.seconds,transport.ends_at))
        return 'CONTROLLED-NO-HTTP'
    monkeypatch.setattr(LocalProvider,'_infer',infer)
    schema=ScenarioResponse if bug_review is None else ReviewResponse if bug_review else BugPlanningResponse
    seconds=60 if bug_review is None else 300
    now=time.monotonic()
    assert LocalProvider()._request(schema,'{}',key,'fixture')=='CONTROLLED-NO-HTTP'
    assert seen[-1][0]==seconds and now+seconds<=seen[-1][1]<=time.monotonic()+seconds
    shared=time.monotonic()+30
    LocalProvider(ends_at=shared)._request(schema,'{}',key,'fixture')
    assert seen[-1][0]==seconds and seen[-1][1]==shared


@pytest.mark.parametrize('text', ['{"draft":', 'x'*30000, '\U0001f60a'*12000])
def test_preparse_private_content_is_bounded_and_retains_original_identity(tmp_path, text):
    import hashlib
    from uuid import uuid4
    from workbench.bug_investigation_stage import InvestigationResponse
    from workbench.planning_response import Evidence
    journal=Evidence(tmp_path,str(uuid4()),InvestigationResponse)
    try:
        journal.capture_content(text,'fixture-credential')
        raw=(journal.directory/'provider-content.json').read_bytes()
        value=json.loads(raw)
        assert len(raw)<=8193 and value['original_sha256']==hashlib.sha256(text.encode()).hexdigest()
        assert text.startswith(value['text']) and value['truncated']==(value['text']!=text)
        assert (journal.directory/'provider-content.json').stat().st_mode & 0o077 == 0
        assert '"text"' not in journal.file.read_text()
    finally:
        journal.close()


def test_preparse_content_redacts_known_provider_credential_without_changing_parser_input(tmp_path):
    from uuid import uuid4
    from workbench.bug_investigation_stage import InvestigationResponse
    from workbench.planning_response import Evidence
    secret='fixture-private-provider-token'
    text='{"draft":{"question":"'+secret+'"}}'
    journal=Evidence(tmp_path,str(uuid4()),InvestigationResponse)
    try:
        journal.capture_content(text,secret)
        snapshot=(journal.directory/'provider-content.json').read_text()
        assert secret not in snapshot and secret not in journal.file.read_text()
        assert json.loads(snapshot)['credential_redacted'] and secret in text
    finally:
        journal.close()



def test_serialized_input_fitting_preserves_complete_committed_startup(tmp_path):
    from workbench.bug_investigation_stage import InvestigationResponse
    prefix='-- complete committed startup line\n'*129
    packet=dict(purpose='read-only-investigation',report=dict(title='A report',description='A report'),
        coverage={},owner_reply='x'*22000,
        api_references=[dict(path='pinned-api',excerpts=[dict(line=1,text='a'*16000)])],
        committed_bootstrap_context=dict(test_path='tests/test_example.lua',through_line=129,complete=True),
        files=[dict(path='tests/test_example.lua',excerpts=[dict(line=1,through_line=129,text=prefix,current_read=True)])]
              +[dict(path='!!!TwichUI/part'+str(i)+'.lua',excerpts=[dict(line=1,text='local x = 1\n'*180,current_read=True)]) for i in range(8)])
    assert len(json.dumps(packet,separators=(',',':')).encode())<65536
    calls=[]
    class Transport:
        seconds=120
        def models(self,_):return MODEL
        def context_budget(self,_,body):return dict(prompt_tokens=12000,context_tokens=32768)
        def read(self,request,limit):
            calls.append(request.data)
            body=json.loads(request.data)
            context=json.loads(body['messages'][1]['content'])
            chunks=context['files'][0]['excerpts']
            assert chunks[0]['text']==prefix and chunks[0]['through_line']==129
            assert len(request.data)<=65536
            assert 'shortened' in context['context_note']
            return json.dumps(dict(id='controlled-fit',model=MODEL,usage=dict(prompt_tokens=10,completion_tokens=5,total_tokens=15),
                choices=[dict(finish_reason='stop',message=dict(content=json.dumps(dict(finding='A question.',draft=dict(question='A real-world detail.')))))])).encode()
    provider=LocalProvider()
    provider._infer(Transport(),'inert',InvestigationResponse,json.dumps(packet,separators=(',',':')),expected_model=MODEL)
    assert len(calls)==1


def test_private_corrective_context_is_fitted_before_http_bound(monkeypatch):
    from workbench.bug_investigation_stage import InvestigationResponse
    from workbench.planning_provider import BUG_INPUT, BUG_PREPARED_INPUT
    packet=dict(coverage={},report=dict(title='An ordinary report',description='A repeated action needs refreshing.'),
        prior_rejected_plan_feedback=dict(note='Untrusted rejection, no approval',issues=['reason '*1800]),
        files=[dict(path='!!!TwichUI/Example.lua',excerpts=[dict(line=1,text='local value = 1\n'*3500)])])
    prepared=len(json.dumps(packet,separators=(',',':')).encode())
    assert BUG_INPUT<prepared<=BUG_PREPARED_INPUT
    seen=[]
    class Transport:
        seconds=120
        def models(self,_):return MODEL
        def context_budget(self,_,body):return dict(prompt_tokens=12000,context_tokens=32768)
        def read(self,request,limit):
            body=json.loads(request.data)
            actual=json.loads(body['messages'][1]['content'])
            assert actual['report']==packet['report']
            assert actual['prior_rejected_plan_feedback']==packet['prior_rejected_plan_feedback']
            assert len(request.data)<=BUG_INPUT and limit==24000
            seen.append(len(request.data))
            return json.dumps(dict(id='controlled-fit-correction',model=MODEL,
                usage=dict(prompt_tokens=10,completion_tokens=5,total_tokens=15),
                choices=[dict(finish_reason='stop',message=dict(content=json.dumps(dict(
                    finding='A controlled stop.',draft=dict(question='A real-world detail.')))))])).encode()
    def request(self,schema,encoded,key,expected):
        return self._infer(Transport(),'inert',schema,encoded,expected_model=expected)
    monkeypatch.setenv('WORKBENCH_LOCAL_KEY','unused-control-key-no-file-read')
    monkeypatch.setattr(LocalProvider,'_request',request)
    provider=LocalProvider()
    provider.generate(packet,InvestigationResponse,expected_model=MODEL)
    assert len(seen)==1 and provider.diagnostics['prepared_input_bytes']==prepared
    packet['prior_rejected_plan_feedback']['issues']=['x'*100000]
    with pytest.raises(PlanningUnavailable) as failure:
        provider.generate(packet,InvestigationResponse,expected_model=MODEL)
    assert failure.value.reason=='input_budget' and len(seen)==1
    assert provider.diagnostics['generation_state']=='not_started'


@pytest.mark.parametrize('count,allowed',[(24543,True),(26000,True),(26001,False),(29000,False)])
def test_program_working_set_uses_exact_verified_context_capacity(count,allowed):
    from workbench.bug_investigation_stage import InvestigationResponse
    packet=dict(coverage=dict(reproducer_format='lua-phases-v1'),files=[dict(path='!!!TwichUI/Example.lua',
        excerpts=[dict(line=1,text='local a = 1\n'*3500)])])
    calls=[]
    class Transport:
        seconds=300
        def models(self,_):return MODEL
        def context_budget(self,_,body):
            calls.append('count')
            assert body['max_tokens']==6144 and body['reasoning_budget_tokens']==3072
            return dict(prompt_tokens=count,context_tokens=32768)
        def read(self,request,limit):
            calls.append('generate')
            assert len(request.data)<=131072 and limit==65536
            return json.dumps(dict(id='controlled-full-source',model=MODEL,
                usage=dict(prompt_tokens=10,completion_tokens=5,total_tokens=15),choices=[
                    dict(finish_reason='stop',message=dict(content=json.dumps(dict(
                        finding='A controlled stop.',draft=dict(question='A real-world detail.')))))])).encode()
    provider=LocalProvider()
    provider.diagnostics=dict(generation_state='not_started')
    if allowed:
        provider._infer(Transport(),'inert',InvestigationResponse,json.dumps(packet),expected_model=MODEL)
        assert calls==['count','generate']
    else:
        with pytest.raises(PlanningUnavailable) as failure:
            provider._infer(Transport(),'inert',InvestigationResponse,json.dumps(packet),expected_model=MODEL)
        assert failure.value.reason=='context_budget' and calls==['count']
        assert provider.diagnostics['generation_state']=='not_started'
