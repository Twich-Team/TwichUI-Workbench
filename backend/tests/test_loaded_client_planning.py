"""Loaded-addon source selection at the real planner/HTTP boundary."""

from contextlib import nullcontext
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from test_grounded_workflow import controlled_source, current, policy_file, save_local_policy
from test_verification_profiles import git
from workbench import grounded_scenarios, intake, planning_provider, worker
from workbench.db import model_attempts, planning_jobs, write_transaction


HOLD = json.loads((Path(__file__).with_name("fixtures") / "loaded_client_planning_hold.json").read_text())
TEST = "tests/test_map_viewmath_step.lua"
SOURCE = "!!!TwichUI/map/ViewMath.lua"
TARGET = ["TwichUI", "MapViewMath", "Fade"]
MODEL = "controlled-addon-model"


def old_scenario(**fields):
    return dict(id="same-threshold", acceptance_index=0, test_path=TEST, fixture="loaded-client",
                source_paths=[SOURCE], target=TARGET, method=False, args=[2, 2, 2],
                observe={"expected": 1}, incorrect=0) | fields


@pytest.mark.parametrize("fields", [
    {"addons": ["TwichUI"]},
    {"source_paths": ["TwichUI_Settings/map/ViewMath.lua"]},
])
def test_reproduces_retained_diagnostic_without_assuming_missing_response_fields(fields):
    with pytest.raises(ValidationError) as error:
        grounded_scenarios.GroundedDraft.model_validate({"scenarios": [old_scenario(**fields)]})
    issue, = error.value.errors(include_input=False)
    location = ".".join(map(str, ("draft", "GroundedDraft", *issue["loc"])))
    assert f"at {location}: {issue['msg']}" in HOLD["message"]
    assert HOLD["diagnostics"]["category"] == "schema_validation"


@pytest.mark.parametrize("fault, explanation", [
    (None, None),
    ("other-operation", None),
    ("old-shape", "complete proposal"),
    ("other-test", "complete proposal"),
    ("other-source", "complete proposal"),
    ("traversal", "complete proposal"),
    ("invented", "outside the verified source bindings"),
    ("ambiguous", "select one callable"),
    ("same-example", "more than one callable for the same example"),
    ("missing-args", "complete proposal"),
    ("missing-outcome", "complete proposal"),
    ("wrong-arity", "explicit arguments"),
    ("different-outcome", "match the user's explicit example"),
    ("duplicate-key", "could not read"),
    ("incomplete", "complete plan"),
    ("abstain", "could not match the supplied example"),
    ("stale", "bindings are stale"),
])
def test_loaded_client_transport_to_review(fault, explanation, store, client, headers, tmp_path, monkeypatch):
    real = os.environ.get("WB_TEST_REAL_ADDON")
    if real and fault != "ambiguous":
        root = Path(real)
        assert git(root, "rev-parse", "HEAD").strip() == "1caf530212bbc6eaf37e41d668902a391aa936c1"
    else:
        root = controlled_source(tmp_path)
        (root / SOURCE).parent.mkdir()
        (root / TEST).write_text(
            'dofile(TESTS .. "harness.lua")\n'
            'local c = MakeClient("Owner", {"!!!TwichUI"})\n'
            'local run = assert(loadfile(ROOT .. "map/ViewMath.lua")); setfenv(run, c); run("!!!TwichUI", {})\n'
        )
        module = ('local addon = TwichUI\nlocal math = {}\naddon.MapViewMath = math\n'
                  'function math.Fade(v, a, b) return 1 end\n'
                  'function math.Step(scale, notches, step, lo, hi) return scale end\n'
                  'function math.Lerp(a, b, t) return a + (b - a) * t end\n')
        if fault == "ambiguous":
            module += 'math.Fade = function(x, y, z) return 1 end\n'
        (root / SOURCE).write_text(module)
        git(root, "add", ".")
        git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@invalid", "commit", "-qm", "Add client chunk")
    config, output = policy_file(store, tmp_path)
    key = tmp_path / "inert"
    key.write_text("inert")
    monkeypatch.setenv("WORKBENCH_RESEARCH_ROOT", str(root))
    monkeypatch.setenv("WORKBENCH_PREPARATION_CONFIG", str(config))
    monkeypatch.setenv("WORKBENCH_LOCAL_KEY", str(key))
    monkeypatch.setenv("WORKBENCH_PLANNER", "local")
    save_local_policy(store, MODEL)
    calls = []

    class Transport:
        seconds = 120

        def models(self, secret):
            assert secret == "inert"
            return MODEL

        def read(self, request, limit):
            calls.append(request.data)
            wire = json.loads(request.data)
            assert len(request.data) < 24000 and limit == 24000
            assert wire["tools"] == [] and wire["reasoning_budget_tokens"] == 0
            assert wire["max_tokens"] == 2600
            assert "Select one binding_id from binding_options" in wire["messages"][0]["content"]
            defs = wire["response_format"]["json_schema"]["schema"]["$defs"]
            assert set(defs["BindingSelection"]["properties"]) == {
                "id", "binding_id", "acceptance_index", "args", "expected", "incorrect"
            }
            packet = json.loads(wire["messages"][1]["content"])
            assert "fixture_bindings" not in packet
            assert all("text" not in item for item in packet["evidence"])
            assert all(o["source_path"] == SOURCE and o["test_path"] == TEST for o in packet["binding_options"])
            operation = {"other-operation": "Lerp", "wrong-arity": "Step"}.get(fault, "Fade")
            selected = next(o for o in packet["binding_options"] if o["target"][-1] == operation)
            example, = packet["examples"]
            scenario = {k: example[k] for k in ("acceptance_index", "args", "expected", "incorrect")}
            scenario.update(id="threshold-return", binding_id=selected["binding_id"])
            if fault == "old-shape":
                scenario = old_scenario(addons=["TwichUI"])
            if fault == "other-test":
                scenario["test_path"] = "tests/test_other.lua"
            if fault in {"other-source", "traversal"}:
                scenario["source_paths"] = ["TwichUI_Settings/view.lua" if fault == "other-source"
                                            else "!!!TwichUI/../private.lua"]
            if fault == "invented":
                scenario["binding_id"] = 999
            if fault == "missing-args":
                del scenario["args"]
            if fault == "missing-outcome":
                del scenario["incorrect"]
            if fault == "different-outcome":
                scenario["expected"] = 9
            draft = {"scenarios": [scenario]}
            if fault == "same-example":
                draft["scenarios"].append(scenario | {"id": "second-choice"})
            if fault == "abstain":
                draft = {"unable_to_select": True}
            value = {k: packet[k] for k in ("input_sha256", "evidence_sha256", "policy_sha256")}
            if fault == "stale":
                value["evidence_sha256"] = "0" * 64
            content = json.dumps(dict(value, draft=draft, model=MODEL))
            if fault == "duplicate-key":
                content = content.replace('"binding_id":', '"binding_id": 999, "binding_id":')
            return json.dumps(dict(model=MODEL, usage=dict(prompt_tokens=200, completion_tokens=80, total_tokens=280),
                                   choices=[dict(finish_reason="length" if fault == "incomplete" else "stop",
                                                 message=dict(content=content))])).encode()

    monkeypatch.setattr(planning_provider, "local_slot", nullcontext)
    monkeypatch.setattr(planning_provider, "LocalHTTP", lambda _: nullcontext(Transport()))
    original = HOLD["original"]
    if fault == "other-operation":
        original = dict(original, title="Cover linear interpolation halfway",
                        description='Test the halfway point of MapViewMath.Lerp.\n'
                                    'Example 1: {"args":[2,6,0.5],"expected":4,"incorrect":2}',
                        regression_tests=dict(test_paths=[TEST], acceptance=["Halfway from 2 to 6 returns 4."]))
    submit = client.post("/api/intake", headers=headers, json=dict(original, request_id=str(uuid4())))
    assert submit.status_code == 200, submit.text
    item = submit.json()
    assert worker.tick(store) and not worker.tick(store)
    view = current(client, item)
    with store.connect() as c:
        attempt = c.execute(select(model_attempts)).mappings().one()
    assert len(calls) == 1 and attempt["usage"]["values"]["total_tokens"] == 280
    assert not view["decisions"] and not view["tasks"] and not list(output.iterdir())
    if explanation:
        assert attempt["outcome"] == view["status"] == "failed"
        job, = view["jobs"]
        assert explanation in job["message"] and "Your original request and any example are saved." in job["message"]
        assert job["diagnostics"]["retained_failure"] == 1
        assert not view["proposals"] and view["result"] is None
        denied = client.post(f"/api/intake/{view['id']}/retry", headers=headers, json={"job_id": job["id"]})
        assert denied.status_code == 409
        assert not worker.tick(store) and len(calls) == 1
    else:
        assert attempt["outcome"] == view["status"] == "completed"
        proposal, = view["proposals"]
        assert not proposal["approval_blockers"]
        scenario, = proposal["task_contract"]["behavioral_evidence"]["scenarios"]
        assert scenario["source_paths"] == [SOURCE] and scenario["test_path"] == TEST
        assert scenario["addons"] == ["!!!TwichUI"] and scenario["setup"] == []
        assert scenario["target"] == (TARGET[:-1] + ["Lerp"] if fault == "other-operation" else TARGET)
        args, expected, incorrect = ([2, 6, 0.5], 4, 2) if fault == "other-operation" else ([2, 2, 2], 1, 0)
        assert scenario["observe"] == {"path": [], "expected": expected} and scenario["incorrect"] == incorrect
        assert scenario["args"] == args


def test_retained_failure_projection_keeps_original_diagnostic_and_denies_retry(store, client, headers):
    submit = client.post("/api/intake", headers=headers, json=dict(HOLD["original"], request_id=str(uuid4())))
    assert submit.status_code == 200
    item = submit.json()
    job, = item["jobs"]
    with write_transaction(store) as c:
        c.execute(planning_jobs.update().where(planning_jobs.c.id == job["id"]).values(
            status="failed", attempts=1, message=HOLD["message"], payload={"diagnostics": HOLD["diagnostics"]}))
    view = current(client, item)
    assert view["jobs"][0]["message"].startswith("The model's proposal did not bind")
    assert view["jobs"][0]["diagnostics"]["retained_failure"] == 1
    assert "Example 1:" in view["original"]["description"] and not view["proposals"]
    with store.connect() as c:
        assert intake.require(c, planning_jobs, job["id"])["message"] == HOLD["message"]
    denied = client.post(f"/api/intake/{view['id']}/retry", headers=headers, json={"job_id": job["id"]})
    assert denied.status_code == 409
