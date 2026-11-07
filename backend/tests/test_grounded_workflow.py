"""Ordinary HTTP intake -> existing adapter -> review -> worker -> actual Lua.

Controlled model responses and confinement telemetry only; no model/service calls.
The application, not a per-task command or registration, dispatches preparation.
"""

import copy
from contextlib import nullcontext
import hashlib
import json
from subprocess import CalledProcessError
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from workbench import intake, model_policy, planning_provider, worker, trusted_preparation
from workbench.db import (
    planning_jobs,
    model_attempts,
    proposal_decisions,
    task_run_offers,
    write_transaction,
    engine_for,
    fake_publications,
    pr_preparations,
)
from workbench.regression_evidence import ScenarioResponse, input_binding
from test_regression_evidence import ControlledPreparer, approve
from test_reviewed_offers import source, authority_counts
from test_verification_profiles import PRODUCTION, TEST, git
from workbench import verification_profiles as profiles
from workbench import grounded_scenarios

CLIENT_TEST = "tests/test_client.lua"
FLOOR_TEST = "tests/test_floor.lua"
FLOOR_SOURCE = "!!!TwichUI/floor.lua"


def test_committed_client_chunk_requires_literal_load_and_matching_environment():
    test = """-- A real harness loads the client first.
dofile(TESTS .. "harness.lua")
local client = MakeClient("Rich", {"!!!TwichUI"})
local run = assert(loadfile(ROOT .. "map/ViewMath.lua")); setfenv(run, client); run("!!!TwichUI", {})
"""
    assert grounded_scenarios.client_chunk(test) == "!!!TwichUI/map/ViewMath.lua"
    assert grounded_scenarios.client_chunk("--[=[\n" + test + "]=]") is None
    assert grounded_scenarios.client_chunk("local text = [=[\n" + test + "]=]") is None
    assert grounded_scenarios.client_chunk(test.replace('ROOT .. "map/ViewMath.lua"', "ROOT .. file")) is None
    assert grounded_scenarios.client_chunk(test.replace("setfenv(run, client)", "setfenv(run, other)")) is None


def test_client_chunk_exports_only_the_published_namespace():
    source = """local addon = TwichUI
local math = {}
addon.MapViewMath = math
function math.Fade(value, low, high) end
function unrelated.Other() end
"""
    assert grounded_scenarios.chunk_operations("!!!TwichUI/map/ViewMath.lua", source) == [
        dict(defined_in="!!!TwichUI/map/ViewMath.lua", target=["TwichUI", "MapViewMath", "Fade"],
             method=False, parameters=["value", "low", "high"], variadic=False),
    ]
    assert grounded_scenarios.chunk_operations("!!!TwichUI/map/ViewMath.lua",
                                              source.replace("addon.MapViewMath = math", "")) == []


def controlled_source(tmp_path):
    root = tmp_path / "source"
    if (root / CLIENT_TEST).exists():
        return root
    if not root.exists():
        root = source(tmp_path, profiles.REGRESSION)
    harness = """local normalize = dofile('../!!!TwichUI/normalize.lua')
function MakeClient(name, addons)
 local c = {API={}}
 c.API.Normalize = function(value) c.last=normalize(value); return c.last end
 return c
end
"""
    (root / "tests/harness.lua").write_text(harness)
    (root / CLIENT_TEST).write_text(
        "dofile(TESTS .. 'harness.lua')\nlocal c=MakeClient('Base', {'!!!TwichUI'})\nassert(c.API.Normalize('alice') == 'alice')\n"
    )
    runner = root / "tests/run.sh"
    runner.write_text(
        runner.read_text()
        .replace('require("bit");', 'require("bit");TESTS="./";')
        .replace("4 passed", "5 passed")
    )
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@invalid",
        "commit",
        "-qm",
        "Two supported fixture mappings",
    )
    return root


def add_floor_fixture(root):
    (root / FLOOR_SOURCE).write_text(
        "local M = {}\nfunction M.AtLeast(value, floor) return math.max(value, floor) end\nreturn M\n"
    )
    (root / FLOOR_TEST).write_text(
        "local bounds = dofile('../!!!TwichUI/floor.lua')\nassert(bounds.AtLeast(9, 3) == 9)\n"
    )
    runner = root / "tests/run.sh"
    runner.write_text(runner.read_text().replace("5 passed", "6 passed"))
    git(root, "add", ".")
    git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@invalid", "commit", "-qm",
        "Add a distinct numeric module fixture")


def policy_file(store, tmp_path):
    output = tmp_path / "preparations"
    output.mkdir(mode=0o700, exist_ok=True)
    db = Path(store.url.database)
    config = dict(
        version=1,
        database=str(db),
        database_identity=[db.stat().st_dev, db.stat().st_ino],
        repository="Twich-Team/TwichUI",
        allowed_classes=["lua-regression-tests"],
        output_root=str(output),
        identity=dict(name="Controlled author", email="fixture@invalid"),
        scenario_generation=True,
    )
    file = tmp_path / "preparation.json"
    file.write_text(json.dumps(config))
    file.chmod(0o600)
    return file, output


def configure(store, tmp_path, monkeypatch):
    root = controlled_source(tmp_path)
    file, output = policy_file(store, tmp_path)
    monkeypatch.setenv("WORKBENCH_RESEARCH_ROOT", str(root))
    monkeypatch.setenv("WORKBENCH_PREPARATION_CONFIG", str(file))
    monkeypatch.setattr(trusted_preparation, "Runtime", ControlledPreparer)
    return root, file, output


class ControlledPlanner:
    def __init__(self, mutate=None):
        self.calls = []
        self.mutate = mutate
        self.diagnostics = {
            "actual_model": "controlled-scenario-adapter",
            "configured_model": "controlled-scenario-adapter",
        }

    def generate(self, packet, schema):
        self.calls.append(copy.deepcopy(packet))
        client = packet["writable_paths"] == [CLIENT_TEST]
        value = dict(
            input_sha256=packet["input_sha256"],
            evidence_sha256=packet["evidence_sha256"],
            policy_sha256=packet["policy_sha256"],
            model="untrusted-generated-model-label",
            draft=dict(
                scenarios=[
                    dict(
                        id="trimmed-client-state" if client else "capital-name",
                        acceptance_index=0,
                        test_path=packet["writable_paths"][0],
                        fixture="client" if client else "module",
                        source_paths=[PRODUCTION],
                        target=["API", "Normalize"] if client else [],
                        args=["  bob  "] if client else ["  ALICE "],
                        observe=dict(
                            path=["last"] if client else [], expected="bob" if client else "alice"
                        ),
                        incorrect="  bob  " if client else "ALICE",
                    )
                ]
            ),
        )
        if self.mutate:
            self.mutate(value, packet)
        return value, "controlled-scenario-adapter", "demo"


def submit(client, headers, path=TEST, *, example=True, example_line=None):
    value = dict(
        request_id=str(uuid4()),
        kind="feature",
        title="Cover retained client state" if path == CLIENT_TEST else "Cover case normalization",
        description="In "
        + path
        + (
            " cover trimming spaces before storing the last name."
            if path == CLIENT_TEST
            else " cover trimming and lowercase normalization."
        ),
        visual_requirement="nonvisual",
        regression_tests=dict(
            test_paths=[path],
            acceptance=["The last name stores bob after a spaced input."]
            if path == CLIENT_TEST
            else ["Spaces and capital letters normalize to alice."],
        ),
    )
    if example:
        value["description"] += "\n" + (example_line or (
            'Example 1: {"args": ["  bob  "], "expected": "bob", "incorrect": "  bob  "}'
            if path == CLIENT_TEST else
            'Example 1: {"args": ["  ALICE "], "expected": "alice", "incorrect": "ALICE"}'
        ))
    r = client.post("/api/intake", headers=headers, json=value)
    assert r.status_code == 200, r.text
    return r.json()


def current(client, item):
    return client.get("/api/intake/" + item["id"]).json()


def approve_and_decompose(store, client, headers, item):
    r, body = approve(store, client, headers, current(client, item))
    assert r.status_code == 200, r.text
    assert worker.tick(store)  # Apply the review.
    assert worker.tick(store)  # Existing deterministic decomposition + durable preparation intent.
    return body


def preparation_job(store):
    with store.connect() as c:
        return dict(
            c.execute(select(planning_jobs).where(planning_jobs.c.kind == "behavioral_preparation"))
            .mappings()
            .one()
        )


def test_two_distinct_requests_reach_review_and_application_dispatch_without_task_registration(
    store,
    client,
    headers,
    tmp_path,
    monkeypatch,
):
    root, _, output = configure(store, tmp_path, monkeypatch)
    adapter = ControlledPlanner()
    for path in [TEST, CLIENT_TEST]:
        item = submit(client, headers, path)
        assert worker.tick(store, planning_adapter=adapter)
        view = current(client, item)
        prepared = view["proposals"][0]["task_contract"]["behavioral_evidence"]
        assert prepared["status"] == "review_required", prepared
        assert prepared["origin"] == "controlled-scenario-adapter"
        assert prepared["execution_authorized"] is False
        assert adapter.calls[-1]["input_sha256"] == input_binding(
            view["original"], "", git(root, "rev-parse", "HEAD").strip(), [path]
        )
        assert all(e["sha256"] and e["git_blob"] for e in adapter.calls[-1]["evidence"])
        assert adapter.calls[-1]["preparation_policy"]["permissions"] == []
        denied, _ = approve(store, client, headers, view, confirm=False)
        assert denied.status_code == 422
        approve_and_decompose(store, client, headers, item)
        before = len(list(output.iterdir()))
        assert worker.tick(store)  # Actual existing preparer, selected by durable job.
        assert len(list(output.iterdir())) == before + 1
        refreshed = current(client, item)
        task = next(t for t in refreshed["tasks"] if t["kind"] == "task")
        offer = client.get(f"/api/intake/{item['id']}/tasks/{task['id']}/run-approval").json()
        assert offer["available"], offer
        assert not offer["offer"]["budget"].get("activate_once", False)
        assert (
            next(j for j in refreshed["jobs"] if j["kind"] == "behavioral_preparation")["status"]
            == "completed"
        )
    assert len(adapter.calls) == 2
    assert not worker.tick(store, planning_adapter=adapter)
    with store.connect() as c:
        assert len(c.execute(select(task_run_offers)).all()) == 2
        attempts = c.execute(select(model_attempts)).mappings().all()
        assert len(attempts) == 2 and all(
            a["source"] == "demo" and a["outcome"] == "completed" for a in attempts
        )
        assert (
            len(
                c.execute(
                    select(planning_jobs).where(planning_jobs.c.kind == "behavioral_preparation")
                ).all()
            )
            == 2
        )
    counts = authority_counts(store)
    assert (
        counts["real_execution_grants"]
        == counts["real_execution_requests"]
        == counts["real_publications"]
        == counts["task_sessions"]
        == 0
    )
    assert git(root, "status", "--porcelain") == ""


@pytest.mark.parametrize(
    "fault",
    [
        "symbol",
        "operation",
        "permission",
        "mapping",
        "source",
        "stale-evidence",
        "stale-policy",
        "stale-input",
        "ambiguous",
    ],
)
def test_invalid_generated_content_holds_before_approval_and_preparation(
    store, client, headers, tmp_path, monkeypatch, fault
):
    configure(store, tmp_path, monkeypatch)

    def mutate(value, packet):
        s = value["draft"]["scenarios"][0]
        if fault == "symbol":
            s["target"] = ["InventedOperation"]
        if fault == "operation":
            s["setup"] = [dict(op="execute", path=["API"], value="os.exit()")]
        if fault == "permission":
            value["permissions"] = ["production-write"]
        if fault == "mapping":
            s["fixture"] = "client"
            s["target"] = ["API", "Normalize"]
        if fault == "source":
            s["source_paths"] = ["!!!TwichUI/invented.lua"]
        if fault == "stale-evidence":
            value["evidence_sha256"] = "0" * 64
        if fault == "stale-policy":
            value["policy_sha256"] = "0" * 64
        if fault == "stale-input":
            value["input_sha256"] = "0" * 64
        if fault == "ambiguous":
            value["draft"] = dict(
                scenarios=[],
                unresolved=[
                    dict(
                        acceptance_index=0,
                        question="Should an empty name be rejected or normalized to an empty value?",
                    )
                ],
            )

    item = submit(client, headers)
    adapter = ControlledPlanner(mutate)
    assert worker.tick(store, planning_adapter=adapter)
    view = current(client, item)
    if fault == "ambiguous":
        assert view["status"] == "waiting_for_information"
        assert view["result"]["clarification"]
        denied, _ = approve(store, client, headers, view)
        assert denied.status_code in {409, 422}
    else:
        assert view["status"] == "failed" and not view["proposals"]
        assert view["jobs"][0]["status"] == "failed" and view["result"] is None
        assert client.post("/api/intake/" + item["id"] + "/clarification", headers=headers,
            json=dict(command_id=str(uuid4()), revision=view["revision"], text="Repeat the example.")).status_code == 409
    assert not worker.tick(store, planning_adapter=adapter)
    assert len(adapter.calls) == 1
    assert not view["tasks"] and not view["decisions"]
    with store.connect() as c:
        assert not c.execute(select(task_run_offers)).first()
        assert not c.execute(
            select(planning_jobs).where(planning_jobs.c.kind == "behavioral_preparation")
        ).first()


def test_denied_policy_never_calls_adapter(store, client, headers, tmp_path, monkeypatch):
    _, file, _ = configure(store, tmp_path, monkeypatch)
    config = json.loads(file.read_text())
    config["allowed_classes"] = []
    file.write_text(json.dumps(config))
    item = submit(client, headers)
    adapter = ControlledPlanner()
    assert worker.tick(store, planning_adapter=adapter)
    assert not adapter.calls
    assert "denies this task class" in str(current(client, item))


@pytest.mark.parametrize(
    "failure", ["stale-base", "denied-policy", "preparer-failure", "subprocess-failure"]
)
def test_dispatch_failures_are_durable_specific_holds_without_replay(
    store, client, headers, tmp_path, monkeypatch, failure
):
    root, file, output = configure(store, tmp_path, monkeypatch)
    item = submit(client, headers)
    assert worker.tick(store, planning_adapter=ControlledPlanner())
    approve_and_decompose(store, client, headers, item)
    calls = []
    original = trusted_preparation.prepare_regression

    def prepare(*args, **kwargs):
        calls.append(args[1])
        if failure == "subprocess-failure":
            raise CalledProcessError(1, ["git", "clone"])
        if failure == "preparer-failure":
            raise ValueError("Controlled preparation failure: Lua expectation replay failed")
        return original(*args, **kwargs)

    monkeypatch.setattr(trusted_preparation, "prepare_regression", prepare)
    if failure == "stale-base":
        (root / PRODUCTION).write_text("return function(s) return s end\n")
        git(root, "add", ".")
        git(
            root,
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@invalid",
            "commit",
            "-qm",
            "Changed source",
        )
    if failure == "denied-policy":
        config = json.loads(file.read_text())
        config["allowed_classes"] = []
        file.write_text(json.dumps(config))
    assert worker.tick(store)
    job = preparation_job(store)
    assert job["status"] == "failed" and job["payload"]["preparation_result"]["status"] == "hold"
    assert len(calls) == (0 if failure == "denied-policy" else 1)
    restarted = engine_for(store.url.database)
    assert not worker.tick(restarted)
    with pytest.raises(Exception) as denied:
        intake.retry(restarted, item["id"], job["id"])
    assert denied.value.status_code == 409
    assert not worker.tick(restarted)
    assert len(list(output.iterdir())) == (0 if failure == "denied-policy" else 1)
    with store.connect() as c:
        assert not c.execute(select(task_run_offers)).first()


def test_duplicate_dispatch_and_interrupted_completion_reconcile_without_repreparing(
    store, client, headers, tmp_path, monkeypatch
):
    _, _, output = configure(store, tmp_path, monkeypatch)
    item = submit(client, headers)
    assert worker.tick(store, planning_adapter=ControlledPlanner())
    approve_and_decompose(store, client, headers, item)
    calls = []
    original = trusted_preparation.prepare_regression

    def prepare(*args, **kwargs):
        calls.append(args[1])
        assert (
            trusted_preparation.process_one(store) is False
        )  # Another worker sees durable live claim.
        return original(*args, **kwargs)

    monkeypatch.setattr(trusted_preparation, "prepare_regression", prepare)
    assert worker.tick(store)
    completed = preparation_job(store)
    # Simulate DB receipt loss after successful durable artifacts/offer, then a dead owner.
    payload = copy.deepcopy(completed["payload"])
    payload.pop("preparation_result")
    payload["preparation_claim"]["owner"]["start"] = "dead-owner"
    with write_transaction(store) as c:
        c.execute(
            planning_jobs.update()
            .where(planning_jobs.c.id == completed["id"])
            .values(status="running", payload=payload, finished_at=None)
        )
    restarted = engine_for(store.url.database)
    assert worker.tick(restarted)
    assert preparation_job(store)["status"] == "completed"
    assert not worker.tick(restarted)
    assert len(calls) == 1 and len(list(output.iterdir())) == 1
    with store.connect() as c:
        assert len(c.execute(select(task_run_offers)).all()) == 1


def test_ambiguous_expectation_is_clarified_in_place_with_fresh_bound_feedback(
    store, client, headers, tmp_path, monkeypatch
):
    configure(store, tmp_path, monkeypatch)

    def unresolved(value, packet):
        if not packet["feedback"]:
            value["draft"] = dict(
                scenarios=[],
                unresolved=[
                    dict(
                        acceptance_index=0,
                        question="Should a capitalized name be lowercased or kept as entered?",
                    )
                ],
            )

    adapter = ControlledPlanner(unresolved)
    item = submit(client, headers)
    assert worker.tick(store, planning_adapter=adapter)
    held = current(client, item)
    assert held["status"] == "waiting_for_information"
    old_binding = held["proposals"][0]["binding"]
    denied = client.post(
        "/api/intake/" + item["id"] + "/decisions",
        headers=headers,
        json=dict(
            command_id=str(uuid4()),
            proposal_id=held["proposals"][0]["id"],
            binding=old_binding,
            scope_revision=held["proposals"][0]["scope_revision"],
            action="approve",
            note="",
        ),
    )
    assert denied.status_code == 409
    answer = "Lowercase the name and trim spaces; the uppercase result must be rejected."
    saved = client.post(
        "/api/intake/" + item["id"] + "/clarification",
        headers=headers,
        json=dict(command_id=str(uuid4()), revision=held["revision"], text=answer),
    )
    assert saved.status_code == 200
    assert worker.tick(store, planning_adapter=adapter)
    fresh = current(client, item)
    assert fresh["id"] == item["id"] and fresh["original"] == held["original"]
    assert fresh["proposals"][0]["binding"] != old_binding
    assert (
        fresh["proposals"][0]["task_contract"]["behavioral_evidence"]["status"] == "review_required"
    )
    assert adapter.calls[-1]["feedback"] == answer
    assert adapter.calls[-1]["input_sha256"] != adapter.calls[0]["input_sha256"]


@pytest.mark.parametrize("example_line, reason", [
    (None, "what exact arguments"),
    ('Example 1: {"args":["  ALICE "],"expected":"alice"}', "what exact arguments"),
    ('Example 1: {"args":[],"expected":"alice","incorrect":"ALICE"}', "no committed callable matches"),
    ('Example 1: {"args":["  ALICE "],"args":[" Alice "],"expected":"alice","incorrect":"ALICE"}', "what exact arguments"),
])
def test_missing_user_example_holds_without_a_planning_attempt(
    store, client, headers, tmp_path, monkeypatch, example_line, reason
):
    _, _, output = configure(store, tmp_path, monkeypatch)
    adapter = ControlledPlanner()
    item = submit(client, headers, example=example_line is not None, example_line=example_line)
    assert worker.tick(store, planning_adapter=adapter)
    held = current(client, item)
    assert held["status"] == "waiting_for_information"
    assert reason in held["result"]["clarification"]
    assert "Example 1" in held["result"]["clarification"]
    assert "No reviewed behavioral example" not in held["result"]["clarification"]
    assert held["proposals"][0]["task_contract"]["behavioral_evidence"]["scenarios"] == []
    assert not adapter.calls and not list(output.iterdir()) and not worker.tick(store)
    denied, _ = approve(store, client, headers, held)
    assert denied.status_code == 409
    assert not held["tasks"] and not held["decisions"]
    assert all(value == 0 for value in authority_counts(store).values())
    with store.connect() as c:
        assert not c.execute(select(task_run_offers)).first()
        assert not c.execute(select(planning_jobs).where(
            planning_jobs.c.kind == "behavioral_preparation"
        )).first()

    answer = 'Example 1: {"args":["  ALICE "],"expected":"alice","incorrect":"ALICE"}'
    saved = client.post(
        f"/api/intake/{item['id']}/clarification", headers=headers,
        json=dict(command_id=str(uuid4()), revision=held["revision"], text=answer),
    )
    assert saved.status_code == 200
    assert worker.tick(store, planning_adapter=adapter)
    fresh = current(client, item)
    assert fresh["status"] == "completed" and len(adapter.calls) == 1
    assert fresh["original"] == held["original"]
    assert fresh["proposals"][0]["binding"] != held["proposals"][0]["binding"]
    assert adapter.calls[0]["feedback"] == answer
    assert adapter.calls[0]["examples"] == [
        dict(acceptance_index=0, args=["  ALICE "], expected="alice", incorrect="ALICE", setup=[])
    ]
    assert not fresh["tasks"] and not fresh["decisions"] and not list(output.iterdir())


def test_source_drift_during_generation_discards_response(
    store, client, headers, tmp_path, monkeypatch
):
    root, _, _ = configure(store, tmp_path, monkeypatch)

    def drift(value, packet):
        (root / PRODUCTION).write_text("return function(s) return s end\n")
        git(root, "add", ".")
        git(
            root,
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@invalid",
            "commit",
            "-qm",
            "Drift while planning",
        )

    item = submit(client, headers)
    assert worker.tick(store, planning_adapter=ControlledPlanner(drift))
    view = current(client, item)
    assert view["status"] == "failed"
    assert not view["proposals"] and "changed during generation" in view["jobs"][0]["message"]
    assert not view["tasks"]


def test_interrupted_preparer_with_partial_output_holds_without_relaunch(
    store, client, headers, tmp_path, monkeypatch
):
    _, _, output = configure(store, tmp_path, monkeypatch)
    item = submit(client, headers)
    assert worker.tick(store, planning_adapter=ControlledPlanner())
    approve_and_decompose(store, client, headers, item)
    calls = []

    def interrupted(*args, **kwargs):
        calls.append(args[1])
        (args[2] / "partial-evidence.txt").write_text("Retained before worker interruption")
        raise KeyboardInterrupt("Controlled worker interruption")

    monkeypatch.setattr(trusted_preparation, "prepare_regression", interrupted)
    with pytest.raises(KeyboardInterrupt):
        worker.tick(store)
    job = preparation_job(store)
    assert job["status"] == "running"
    payload = copy.deepcopy(job["payload"])
    payload["preparation_claim"]["owner"]["start"] = "dead-owner"
    with write_transaction(store) as c:
        c.execute(
            planning_jobs.update().where(planning_jobs.c.id == job["id"]).values(payload=payload)
        )
    assert worker.tick(engine_for(store.url.database))
    held = preparation_job(store)
    assert held["status"] == "failed" and "automatic replay is denied" in held["message"]
    assert len(calls) == 1 and len(list(output.iterdir())) == 1
    assert (
        next(output.iterdir()) / "partial-evidence.txt"
    ).read_text() == "Retained before worker interruption"


def test_interrupted_generation_records_unknown_without_an_automatic_model_retry(
    store, client, headers, tmp_path, monkeypatch
):
    _, policy, _ = configure(store, tmp_path, monkeypatch)
    item = submit(client, headers)

    def interrupted(value, packet):
        # A second worker cannot duplicate the already claimed adapter request.
        assert worker.tick(store, planning_adapter=ControlledPlanner()) is False
        raise KeyboardInterrupt("Controlled generation interruption")

    adapter = ControlledPlanner(interrupted)
    with pytest.raises(KeyboardInterrupt):
        worker.tick(store, planning_adapter=adapter)
    with write_transaction(store) as c:
        job = (
            c.execute(select(planning_jobs).where(planning_jobs.c.kind == "planning"))
            .mappings()
            .one()
        )
        payload = copy.deepcopy(job["payload"])
        payload["scenario_generation_claim"]["start"] = "dead-owner"
        c.execute(
            planning_jobs.update().where(planning_jobs.c.id == job["id"]).values(payload=payload)
        )
    assert worker.tick(engine_for(store.url.database), planning_adapter=adapter)
    assert len(adapter.calls) == 1
    assert not worker.tick(store, planning_adapter=adapter)
    view = current(client, item)
    assert view["status"] == "failed" and "completion is unknown" in view["jobs"][0]["message"]
    with store.connect() as c:
        assert c.execute(select(model_attempts.c.outcome)).scalar_one() == "interrupted_unknown"

    # A denied explicit retry must not rewrite the previous unknown attempt.
    config = json.loads(policy.read_text())
    config["allowed_classes"] = []
    policy.write_text(json.dumps(config))
    intake.retry(store, item["id"], job["id"])
    assert worker.tick(store, planning_adapter=adapter)
    assert len(adapter.calls) == 1
    with store.connect() as c:
        assert c.execute(select(model_attempts.c.outcome)).scalar_one() == "interrupted_unknown"


def test_reviewed_policy_change_holds_dispatch_and_invalidates_existing_offer(
    store, client, headers, tmp_path, monkeypatch
):
    _, file, output = configure(store, tmp_path, monkeypatch)
    item = submit(client, headers)
    assert worker.tick(store, planning_adapter=ControlledPlanner())
    approve_and_decompose(store, client, headers, item)
    assert worker.tick(store)
    view = current(client, item)
    task = next(t for t in view["tasks"] if t["kind"] == "task")
    url = f"/api/intake/{item['id']}/tasks/{task['id']}/run-approval"
    assert client.get(url).json()["available"]
    config = json.loads(file.read_text())
    config["identity"]["name"] = "A different configured identity"
    file.write_text(json.dumps(config))
    assert not client.get(url).json()["available"]
    assert len(list(output.iterdir())) == 1
    assert not worker.tick(store)


def test_literal_and_comment_decoys_do_not_ground_exports_or_source_loaders():
    from workbench.grounded_scenarios import loaders, exported, code, returned

    text = """-- dofile('../!!!TwichUI/comment.lua')
local decoy = "dofile('../!!!TwichUI/string.lua')"
local block = [=[dofile('../!!!TwichUI/long-string.lua')]=]
--[=[ dofile('../!!!TwichUI/long-comment.lua') ]=]
local real = dofile('../!!!TwichUI/real.lua')
local M = {}
-- M.Invented = function() end
local words = 'M.Invented = function() end'
M.Actual = function() return 1 end
return M
"""
    assert loaders(text) == {"!!!TwichUI/real.lua"}
    assert returned(text) == "M"
    assert exported(code(text), ["Actual"], returned(text))
    assert not exported(code(text), ["Invented"], returned(text))


def test_grounded_signatures_distinguish_zero_argument_calls_and_variadic_methods():
    from workbench.grounded_scenarios import operations, validate_call

    source = """local M = {API={}}
M.Empty = function() return 0 end
function M.API:Count(first, ...) return 1 end
other.M.Decoy = function(value) return value end
return M
"""
    exports = operations(PRODUCTION, source, "M")
    assert exports == [
        dict(defined_in=PRODUCTION, target=["Empty"], method=False, parameters=[], variadic=False),
        dict(defined_in=PRODUCTION, target=["API", "Count"], method=True, parameters=["first"], variadic=True),
    ]
    binding = {"operations": exports}
    validate_call("empty", binding, {PRODUCTION}, ["Empty"], False, [])
    validate_call("count", binding, {PRODUCTION}, ["API", "Count"], True, [None, 1, 2])
    with pytest.raises(ValueError, match="explicit arguments"):
        validate_call("count", binding, {PRODUCTION}, ["API", "Count"], True, [])
    with pytest.raises(ValueError, match="calling convention"):
        validate_call("count", binding, {PRODUCTION}, ["API", "Count"], False, [1])


def test_existing_local_adapter_uses_scenario_schema_and_instructions_with_controlled_transport():
    from workbench.grounded_scenarios import Response
    from workbench.planning_provider import LocalProvider

    response = dict(
        input_sha256="0" * 64,
        evidence_sha256="1" * 64,
        policy_sha256="2" * 64,
        model="advisory",
        draft=dict(
            scenarios=[],
            unresolved=[dict(acceptance_index=0, question="What should an empty name do?")],
        ),
    )

    class ControlledTransport:
        seconds = 30

        def models(self, secret):
            return "controlled-transport-model"

        def read(self, request, limit):
            payload = json.loads(request.data)
            assert payload["tools"] == []
            assert (
                payload["response_format"]["json_schema"]["schema"] == Response.model_json_schema()
            )
            defs = payload["response_format"]["json_schema"]["schema"]["$defs"]
            draft = payload["response_format"]["json_schema"]["schema"]["properties"]["draft"]["anyOf"]
            assert {v["$ref"] for v in draft} == {"#/$defs/GroundedDraft", "#/$defs/ClarificationDraft"}
            assert defs["GroundedDraft"]["properties"]["unresolved"]["maxItems"] == 0
            assert defs["ClarificationDraft"]["properties"]["scenarios"]["maxItems"] == 0
            assert defs["ClarificationDraft"]["properties"]["unresolved"]["minItems"] == 1
            variants = defs["GroundedDraft"]["properties"]["scenarios"]["items"]["oneOf"]
            assert {v["$ref"] for v in variants} == {
                "#/$defs/ModuleScenario", "#/$defs/ClientScenario", "#/$defs/LoadedClientScenario"
            }
            module = defs["ModuleScenario"]["properties"]
            assert module["fixture"]["const"] == "module"
            assert module["setup"]["maxItems"] == 0
            assert module["source_paths"]["minItems"] == module["source_paths"]["maxItems"] == 1
            assert defs["ModuleObservation"]["properties"]["path"]["maxItems"] == 0
            client = defs["ClientScenario"]["properties"]
            assert client["fixture"]["const"] == "client"
            assert client["target"]["minItems"] == 1 and "target" in defs["ClientScenario"]["required"]
            assert all("args" in defs[name]["required"] for name in ("ModuleScenario", "ClientScenario"))
            assert client["setup"]["maxItems"] > 0
            loaded = defs["LoadedClientScenario"]["properties"]
            assert loaded["fixture"]["const"] == "loaded-client"
            assert loaded["setup"]["maxItems"] == 0 and loaded["target"]["minItems"] == 1
            assert loaded["source_paths"]["minItems"] == loaded["source_paths"]["maxItems"] == 1
            assert defs["Observation"]["properties"]["path"]["maxItems"] > 0
            instructions = payload["messages"][0]["content"]
            assert (
                "untrusted DATA" in instructions and "bounded regression scenarios" in instructions
            )
            assert 'fixture="module"' in instructions and "only the first return" in instructions
            assert "Omit setup and observe.path or leave them empty" in instructions
            assert "return specific unresolved questions only, with no scenarios" in instructions
            assert "Features: proposal required" not in instructions
            return json.dumps(
                dict(
                    model="controlled-transport-model",
                    choices=[
                        dict(finish_reason="stop", message=dict(content=json.dumps(response)))
                    ],
                )
            ).encode()

    value, model, basis = LocalProvider()._infer(ControlledTransport(), "inert", Response, "{}")
    assert value == response and model == "controlled-transport-model" and basis == "local"


def save_local_policy(store, model="selected-local-model", *, enabled=True, preferred="local"):
    policy = model_policy.read(store)
    policy.providers["local"].model = model
    policy.providers["local"].enabled = enabled
    policy.preferred = preferred
    policy.fallback = ["local"] if preferred == "codex" else ["codex"]
    policy.substitution = "configured_fallback_only"
    return model_policy.save(store, policy, model_policy.CapabilityReport(executors=[]))


@pytest.mark.parametrize("fault, field", [
    (None, None),
    ("distinct-task", None),
    ("clarification", "Which value should the test reject?"),
    ("retained-response", "scenarios.0.module.args"),
    ("missing-args", "scenarios.0.module.args"),
    ("empty-required-args", "explicit arguments for the callable parameters (s)"),
    ("unsupported-client", "committed test/harness does not establish a client fixture"),
    ("ungrounded-intent", "match the user's explicit example"),
    ("mixed-clarification", "GroundedDraft.unresolved"),
    ("module-setup", "scenarios.0.module.setup"),
    ("module-field", "scenarios.0.module.observe.path"),
    ("module-table-result", "scenarios.0.module.observe.expected"),
    ("module-extra-source", "scenarios.0.module.source_paths"),
    ("client-empty-target", "scenarios.0.client.target"),
])
def test_9187_scenario_shape_through_local_parser_and_preparation(
    fault, field, store, client, headers, tmp_path, monkeypatch
):
    root, _, output = configure(store, tmp_path, monkeypatch)
    distinct = fault == "distinct-task"
    if distinct:
        add_floor_fixture(root)
    test_path, source_path, target, parameters = (
        (FLOOR_TEST, FLOOR_SOURCE, ["AtLeast"], ["value", "floor"]) if distinct
        else (TEST, PRODUCTION, [], ["s"])
    )
    args, expected, incorrect = ([2, 5], 5, 2) if distinct else (["  ALICE "], "alice", "ALICE")
    selected = "pilot-qwen38-27b"
    save_local_policy(store, selected)
    key = tmp_path / "inert-key"
    key.write_text("inert")
    key.chmod(0o600)
    monkeypatch.setenv("WORKBENCH_PLANNER", "local")
    monkeypatch.setenv("WORKBENCH_LOCAL_KEY", str(key))
    monkeypatch.setattr(planning_provider, "local_slot", nullcontext)
    planner = ControlledPlanner()

    class Transport:
        seconds = 120

        def models(self, secret):
            return selected

        def read(self, request, limit):
            payload = json.loads(request.data)
            packet = json.loads(payload["messages"][1]["content"])
            assert len(request.data) <= planning_provider.MAX_INPUT
            assert packet["writable_paths"] == [test_path]
            assert packet["fixture_bindings"] == [
                dict(test_path=test_path, fixture="module", source_paths=[source_path],
                     observation="scalar first return", operations=[
                         dict(defined_in=source_path, target=target, method=False,
                              parameters=parameters, variadic=False),
                     ])
            ]
            assert packet["examples"] == [
                dict(acceptance_index=0, args=args, expected=expected, incorrect=incorrect, setup=[])
            ]
            assert [entry["path"] for entry in packet["evidence"]] == [
                "tests/run.sh", "tests/harness.lua", test_path, source_path
            ]
            assert "API.Normalize" in packet["evidence"][1]["text"]
            response, _, _ = planner.generate(packet, None)
            if fault == "retained-response":
                captured = (Path(__file__).with_name("fixtures") / "grounded_9187_response.json").read_bytes()
                assert hashlib.sha256(captured).hexdigest() == (
                    "0c2d54d0d4d2021489a91a196f89435bbd4a78c6df18de373fc654fe6a3958fa"
                )
                return captured  # Actual envelope and draft, including omitted args and mixed clarification.
            scenario = response["draft"]["scenarios"][0]
            del scenario["observe"]["path"]  # Omitted path and setup select the first return.
            if distinct:
                scenario.update(id="floor-below-minimum", source_paths=[source_path], target=target,
                                args=args, observe=dict(expected=expected), incorrect=incorrect)
            if fault == "missing-args":
                del scenario["args"]
            if fault == "empty-required-args":
                scenario["args"] = []
            if fault == "unsupported-client":
                scenario.update(fixture="client", target=["API", "Normalize"])
            if fault == "ungrounded-intent":
                scenario["args"] = [" Alice "]
                scenario["incorrect"] = "Alice"
            if fault in {"clarification", "mixed-clarification"}:
                response["draft"]["unresolved"] = [dict(acceptance_index=0, question=field if fault == "clarification" else
                    "Which value should the test reject?")]
                if fault == "clarification":
                    del response["draft"]["scenarios"]
            if fault == "module-setup":
                scenario["setup"] = [dict(op="set", path=["name"], value="  ALICE ")]
            if fault == "module-field":
                scenario["observe"]["path"] = ["name"]
            if fault == "module-table-result":
                scenario["observe"]["expected"] = {"name": "alice"}
            if fault == "module-extra-source":
                scenario["source_paths"].append(PRODUCTION)
            if fault == "client-empty-target":
                scenario["fixture"] = "client"
            if fault in {"module-setup", "module-field"}:
                # The retained 9187 attempt records this validator error, but no response
                # body. Each synthetic payload reproduces one possible trigger.
                with pytest.raises(ValidationError, match="Module scenarios support a scalar first return only"):
                    ScenarioResponse.model_validate(response)
            return json.dumps(dict(model=selected, usage=dict(prompt_tokens=40, completion_tokens=10, total_tokens=50), choices=[
                dict(finish_reason="stop", message=dict(content=json.dumps(response)))
            ])).encode()

    monkeypatch.setattr(planning_provider, "LocalHTTP", lambda _: nullcontext(Transport()))
    if distinct:
        response = client.post("/api/intake", headers=headers, json=dict(
            request_id=str(uuid4()), kind="feature", title="Cover the numeric lower bound",
            description='Add a regression for applying a numeric floor.\n'
                        'Example 1: {"args":[2,5],"expected":5,"incorrect":2}',
            visual_requirement="nonvisual",
            regression_tests=dict(test_paths=[test_path], acceptance=["A value below the floor is raised."]),
        ))
        assert response.status_code == 200, response.text
        item = response.json()
    else:
        item = submit(client, headers)
    assert worker.tick(store)
    view = current(client, item)
    with store.connect() as c:
        attempt = c.execute(select(model_attempts)).mappings().one()
    assert len(planner.calls) == 1 and attempt["actual_model"] == selected
    if field and fault != "clarification":
        assert attempt["outcome"] == "failed"
        assert view["status"] == "failed" and not view["proposals"] and view["result"] is None
        assert field in view["jobs"][0]["message"]
        assert not view["tasks"] and not list(output.iterdir())
    elif fault == "clarification":
        assert attempt["outcome"] == "completed"
        assert view["status"] == "waiting_for_information"
        assert field in view["result"]["clarification"]
        denied, _ = approve(store, client, headers, view)
        assert denied.status_code in {409, 422}
        assert not view["tasks"] and not list(output.iterdir())
    else:
        contract = view["proposals"][0]["task_contract"]
        prepared = contract["behavioral_evidence"]
        assert attempt["outcome"] == "completed" and prepared["status"] == "review_required"
        (scenario,) = prepared["scenarios"]
        assert scenario["args"] == args and scenario["incorrect"] == incorrect
        assert scenario["observe"] == {"path": [], "expected": expected} and scenario["setup"] == []
        assert not list(output.iterdir())
        denied, _ = approve(store, client, headers, view, confirm=False)
        assert denied.status_code == 422
        approve_and_decompose(store, client, headers, item)
        assert worker.tick(store)  # Lua original, positive, vacuous and wrong-behavior controls.
        job = preparation_job(store)
        assert job["status"] == "completed", job["message"]
    assert not worker.tick(store)
    with store.connect() as c:
        decisions = c.execute(select(proposal_decisions.c.payload)).scalars().all()
        offers = c.execute(select(task_run_offers)).mappings().all()
        if field:
            assert not decisions and not offers
            assert not c.execute(select(planning_jobs).where(
                planning_jobs.c.kind == "behavioral_preparation"
            )).first()
        else:
            assert len(decisions) == len(offers) == 1 and not decisions[0].get("run_budget")
            assert offers[0]["snapshot"]["certified_preparation"]["certificate"]["status"] == "certified"
        assert not c.execute(select(pr_preparations)).first()
        assert not c.execute(select(fake_publications)).first()
    counts = authority_counts(store)
    assert all(counts[k] == 0 for k in (
        "task_sessions", "real_execution_grants", "real_execution_requests", "real_publications"
    ))


@pytest.mark.parametrize("fault", [None, "loaded-model", "returned-model"])
def test_saved_model_pinned_before_transport_and_through_review(
    fault, store, client, headers, tmp_path, monkeypatch
):
    _, _, output = configure(store, tmp_path, monkeypatch)
    saved = save_local_policy(store)
    key = tmp_path / "inert-key"
    key.write_text("inert")
    key.chmod(0o600)
    monkeypatch.setenv("WORKBENCH_PLANNER", "local")
    monkeypatch.setenv("WORKBENCH_LOCAL_KEY", str(key))
    monkeypatch.setattr(planning_provider, "local_slot", nullcontext)
    planner = ControlledPlanner()
    selected = saved.providers["local"].model
    other = "different-local-model"

    class Transport:
        seconds = 120

        def __init__(self, stop_event):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def models(self, secret):
            with store.connect() as c:
                attempt = c.execute(select(model_attempts)).mappings().one()
            assert attempt["outcome"] == "running"
            assert attempt["policy_revision"] == saved.revision
            assert attempt["requested_model"] == selected
            return other if fault == "loaded-model" else selected

        def read(self, request, limit):
            payload = json.loads(request.data)
            assert payload["model"] == selected and payload["max_tokens"] == 2600
            assert payload["reasoning_budget_tokens"] == 0
            packet = json.loads(payload["messages"][-1]["content"])
            value, _, _ = planner.generate(packet, None)
            # Saving a new default cannot redirect this already claimed attempt.
            save_local_policy(store, other)
            return json.dumps(dict(
                model=other if fault == "returned-model" else selected,
                usage=dict(prompt_tokens=91, completion_tokens=29, total_tokens=120),
                choices=[dict(finish_reason="stop", message=dict(content=json.dumps(value)))],
            )).encode()

    monkeypatch.setattr(planning_provider, "LocalHTTP", Transport)
    item = submit(client, headers)
    assert worker.tick(store)
    view = current(client, item)
    with store.connect() as c:
        attempt = c.execute(select(model_attempts)).mappings().one()
    assert attempt["policy_revision"] == saved.revision and attempt["requested_model"] == selected
    assert attempt["actual_effort"] is None and attempt["requested_effort"] is None
    if fault == "loaded-model":
        assert not planner.calls and attempt["actual_model"] is None and attempt["usage"] is None
    else:
        assert len(planner.calls) == 1 and attempt["usage"]["values"]["total_tokens"] == 120
        assert model_policy.read(store).revision == saved.revision + 1
        assert attempt["actual_model"] == (other if fault else selected)
    if fault:
        assert attempt["outcome"] == "failed"
        assert view["status"] == "failed" and not view["proposals"]
        assert not worker.tick(store)
    else:
        contract = view["proposals"][0]["task_contract"]
        assert attempt["outcome"] == "completed"
        assert contract["scenario_grounding"]["model_selection"] == dict(
            policy_revision=saved.revision, model=selected
        )
        assert contract["behavioral_evidence"]["origin"] == selected
        denied, _ = approve(store, client, headers, view, confirm=False)
        assert denied.status_code == 422
        approve_and_decompose(store, client, headers, item)
        assert worker.tick(store) and len(list(output.iterdir())) == 1
        task = next(t for t in current(client, item)["tasks"] if t["kind"] == "task")
        url = f"/api/intake/{item['id']}/tasks/{task['id']}/run-approval"
        offered = client.get(url).json()
        assert offered["available"] and offered["receipt"] is None
        assert offered["scope"]["task_contract"]["scenario_grounding"] == contract["scenario_grounding"]
        budget = offered["offer"]["budget"]
        assert (budget["overall_seconds"], budget["generated_tokens"], budget["fallback"]) == (5400, 24000, False)
        assert not worker.tick(store)  # Certification alone leaves the worker idle.
        command = dict(command_id=str(uuid4()), scope_approval_id=offered["scope_approval_id"],
                       binding=offered["binding"], run_budget=budget)
        assert client.post(url, headers=headers, json={**command, "run_budget": {
            **budget, "generated_tokens": 24001,
        }}).status_code == 422
        receipt = client.post(url, headers=headers, json=command)
        assert receipt.status_code == 200, receipt.text
        assert receipt.json()["status"] == "pending"
        assert worker.tick(store) and not worker.tick(store)
        applied = client.get(url).json()["receipt"]
        assert applied["status"] == "applied" and applied["payload"]["run_budget"] == budget
    counts = authority_counts(store)
    assert all(counts[k] == 0 for k in (
        "task_sessions", "real_execution_grants", "real_execution_requests", "real_publications"
    ))


@pytest.mark.parametrize("state", ["unsaved", "disabled", "paid-preferred", "unselected"])
def test_ineligible_saved_policy_never_contacts_adapter_transport(
    state, store, client, headers, tmp_path, monkeypatch
):
    configure(store, tmp_path, monkeypatch)
    monkeypatch.setenv("WORKBENCH_PLANNER", "local")
    if state != "unsaved":
        save_local_policy(store, model="" if state == "unselected" else "selected-local-model",
                          enabled=state != "disabled", preferred="codex" if state == "paid-preferred" else "local")

    def unexpected(*_args, **_kwargs):
        pytest.fail("An ineligible saved model policy must hold before contacting the adapter")

    monkeypatch.setattr(planning_provider.LocalProvider, "generate", unexpected)
    item = submit(client, headers)
    assert worker.tick(store) and not worker.tick(store)
    assert "saved, enabled preferred local model" in str(current(client, item))
    with store.connect() as c:
        assert not c.execute(select(model_attempts)).first()
