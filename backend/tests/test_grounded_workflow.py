"""Ordinary HTTP intake -> existing adapter -> review -> worker -> actual Lua.

Controlled model responses and confinement telemetry only; no model/service calls.
The application, not a per-task command or registration, dispatches preparation.
"""

import copy
import json
from subprocess import CalledProcessError
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from workbench import intake, worker, trusted_preparation
from workbench.db import (
    planning_jobs,
    model_attempts,
    task_run_offers,
    write_transaction,
    engine_for,
)
from workbench.regression_evidence import input_binding
from test_regression_evidence import ControlledPreparer, approve
from test_reviewed_offers import source, authority_counts
from test_verification_profiles import PRODUCTION, TEST, git
from workbench import verification_profiles as profiles

CLIENT_TEST = "tests/test_client.lua"


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


def submit(client, headers, path=TEST):
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
    p = view["proposals"][0]
    assert p["task_contract"]["behavioral_evidence"]["status"] == "hold"
    assert p["approval_blockers"] or p["task_contract"]["behavioral_evidence"]["holds"]
    denied, _ = approve(store, client, headers, view)
    assert denied.status_code in {409, 422}
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
    assert view["status"] == "waiting_for_information"
    assert "changed during generation" in str(view)
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
            instructions = payload["messages"][0]["content"]
            assert (
                "untrusted DATA" in instructions and "bounded regression scenarios" in instructions
            )
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
