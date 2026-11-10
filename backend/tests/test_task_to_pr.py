"""NON-MODEL controlled task/budget/worker/PR delivery. No real publication."""

import copy
from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

import pytest
from sqlalchemy import select

from workbench import (
    task_budget,
    planning_worker,
    real_execution as execution,
    real_publication as delivery,
    worker,
)
from workbench.db import (
    backlog_items,
    proposal_decisions,
    real_execution_grants as grants,
    real_execution_requests as requests,
    model_attempts,
    real_publications,
    write_transaction,
)
from workbench.real_workspace import durable, sha
from test_real_handoff import HandoffRuntime
from test_real_transport import http_tape as http_tape


ORACLE = b"-- NON-MODEL controlled checker; never executed\n"


def template_data():
    from workbench.admission import FIXTURE

    proposal = copy.deepcopy(json.loads(FIXTURE.read_text())["planning"]["proposal"])
    proposal.update(
        summary="NON-MODEL supported task",
        value="Controlled canonical task-to-PR path only.",
        scope=["Exercise the bounded controlled recipe."],
        visual="nonvisual",
        delivery="implementation",
        acceptance=["Controlled checks and delivery complete exactly once."],
    )
    contract = copy.deepcopy(execution.contract())
    contract.update(task_id="non-model-supported-control", title="NON-MODEL supported task")
    recipe = dict(
        contract=contract,
        read_paths=list(execution.PATHS),
        focused_test=execution.PATHS[2],
        oracle_sha256=sha(ORACLE),
        instruction="NON-MODEL scripted test only; no generation.",
        initial_reads=[dict(path=execution.PATHS[i % 3], start=1, end=2) for i in range(4)],
        controls={"original": "fail", "known-good-oracle-control": "pass", "incomplete": "fail"},
        base_branch="main",
        draft=True,
        category="Feature",
        identity=dict(name="NON-MODEL", email="control@example.invalid"),
    )
    return dict(proposal=proposal, recipe=recipe)


@pytest.fixture(autouse=True)
def delivery_clock(monkeypatch):
    """Advance the recorded delivery backoff deterministically, never sleep."""

    class Clock(datetime):
        elapsed = 0

        @classmethod
        def now(cls, tz=None):
            cls.elapsed += 3
            return datetime.now(tz) + timedelta(seconds=cls.elapsed)

    monkeypatch.setattr(delivery, "datetime", Clock)


@pytest.fixture
def supported(client, headers, store, tmp_path, monkeypatch, disposable_legacy_addon):
    template = template_data()
    folder = tmp_path / "templates"
    folder.mkdir()
    raw = json.dumps(template, sort_keys=True).encode()
    identity = sha(raw)
    (folder / (identity + ".json")).write_bytes(raw)
    monkeypatch.setenv("WORKBENCH_TASK_TEMPLATES_ROOT", str(folder))
    intake_id = str(uuid4())
    body = dict(
        request_id=intake_id,
        kind="feature",
        title="NON-MODEL supported task",
        description="Controlled test; no model or real PR.",
        visual_requirement="nonvisual",
        supported_task=identity,
    )
    response = client.post("/api/intake", headers=headers, json=body)
    assert response.status_code == 200, response.text
    assert planning_worker.process_one(store)
    data = client.get("/api/intake/" + intake_id).json()
    proposal = data["proposals"][0]
    offer = client.get(f"/api/intake/{intake_id}/proposals/{proposal['id']}/run-offer").json()
    assert offer["available"]
    decision = dict(
        command_id=str(uuid4()),
        proposal_id=proposal["id"],
        binding=proposal["binding"],
        scope_revision=proposal["scope_revision"],
        action="approve",
        note="DISPOSABLE CONTROL",
        run_budget=offer["offer"]["budget"],
    )
    return dict(
        intake_id=intake_id, proposal=proposal, decision=decision, template=template, body=body
    )


class ControlledRuntime(HandoffRuntime):
    """Real create-only seal/file path; stub checks and no model implementation."""

    calls = 0

    def identity(self):
        return {
            **super().identity(),
            "model": "pilot-qwen38-27b",
            "evidence_role": "NON-MODEL control",
        }

    def execute(self, *args):
        self.calls += 1
        return super().execute(*args)


class ControlledRemote:
    real = False

    def permitted(self):
        return True

    def __init__(self):
        self.pushes = 0
        self.creates = 0
        self.prepares = 0
        self.remote = None
        self.pr = None
        self.lose = None
        self.base = execution.BASE

    def base_head(self, snapshot):
        return self.base

    def prepare(self, snapshot, result):
        self.prepares += 1
        return "b" * 40

    def branch_head(self, snapshot):
        return self.remote

    def push(self, snapshot, commit):
        assert self.permitted()
        self.pushes += 1
        self.remote = commit
        if self.lose == "push":
            raise delivery.UncertainWrite("push")

    def find_pr(self, snapshot, commit):
        return self.pr

    def create_pr(self, snapshot, commit):
        assert self.permitted()
        self.creates += 1
        self.pr = dict(reference="NON-MODEL simulated draft", commit=commit)
        if self.lose == "pr":
            raise delivery.UncertainWrite("pr")

    def valid_pr(self, snapshot, commit, value):
        return value["commit"] == commit


def approve(client, headers, store, supported, tmp_path):
    result = client.post(
        "/api/intake/" + supported["intake_id"] + "/decisions",
        headers=headers,
        json=supported["decision"],
    )
    assert result.status_code == 200, result.text
    assert planning_worker.apply_one(store)
    assert planning_worker.process_one(store)
    with store.connect() as c:
        task = (
            c.execute(
                select(backlog_items).where(
                    backlog_items.c.intake_id == supported["intake_id"],
                    backlog_items.c.kind == "task",
                )
            )
            .mappings()
            .one()
        )
    ref = dict(
        intake_id=supported["intake_id"],
        task_id=task["id"],
        approval_id=supported["decision"]["command_id"],
    )
    root = tmp_path / "NON_MODEL_RUNTIME"
    root.mkdir()
    durable(root / "recipe.json", json.dumps(supported["template"]["recipe"]).encode())
    durable(root / "recipe-oracle.lua", ORACLE)
    durable(root / "oracle.lua", ORACLE)
    durable(root / "base-manifest.json", b"{}")
    runtime = ControlledRuntime(root)
    runtime.task_ref = ref
    return ref, runtime


def drain(store, runtime, remote, maximum=24):
    for _ in range(maximum):
        worked = worker.tick(store, runtime=runtime, publisher=remote)
        if not worked:
            with store.connect() as c:
                active = c.execute(
                    select(real_publications.c.id).where(
                        real_publications.c.status.in_(delivery.ACTIVE)
                    )
                ).first()
            if not active:
                break


def test_single_scope_budget_to_worker_seal_and_simulated_draft(
    client, headers, store, supported, tmp_path
):
    ref, runtime = approve(client, headers, store, supported, tmp_path)
    remote = ControlledRemote()
    drain(store, runtime, remote)
    with store.connect() as c:
        row = c.execute(select(requests)).mappings().one()
        assert c.execute(select(model_attempts)).mappings().one()["outcome"] == "checked"
        assert len(c.execute(select(grants)).all()) == 1
        assert c.execute(select(real_publications)).mappings().one()["task_id"] == ref["task_id"]
    view = delivery.view(store, row["id"])
    assert view["status"] == "published" and not view["published"] and view["url"] is None
    assert "No real PR" in view["message"] and view["preview"]["category"] == "Feature"
    assert runtime.calls == remote.prepares == remote.pushes == remote.creates == 1
    drain(store, runtime, remote)
    assert runtime.calls == remote.pushes == remote.creates == 1
    assert (
        client.get("/api/real-executions/" + row["id"] + "/delivery").json()["source"]
        == "controlled"
    )


@pytest.mark.parametrize("lost", ["push", "pr"])
def test_lost_external_response_is_reconciled_without_another_write(
    client, headers, store, supported, tmp_path, lost
):
    _, runtime = approve(client, headers, store, supported, tmp_path)
    remote = ControlledRemote()
    remote.lose = lost
    drain(store, runtime, remote)
    assert runtime.calls == remote.pushes == remote.creates == 1
    with store.connect() as c:
        assert c.execute(select(real_publications.c.status)).scalar() == "published"


def test_plan_only_does_not_gain_execution_or_publication(
    client, headers, store, supported, tmp_path
):
    supported["decision"].pop("run_budget")
    _, runtime = approve(client, headers, store, supported, tmp_path)
    drain(store, runtime, ControlledRemote())
    with store.connect() as c:
        assert not c.execute(select(grants)).first()
        assert not c.execute(select(model_attempts)).first()
        assert not c.execute(select(real_publications)).first()


def test_exact_retry_and_conflicting_budget_preserve_original(client, headers, store, supported):
    url = "/api/intake/" + supported["intake_id"] + "/decisions"
    body = supported["decision"]
    first = client.post(url, headers=headers, json=body)
    assert first.status_code == 200
    assert client.post(url, headers=headers, json=body).json() == first.json()
    changed = copy.deepcopy(body)
    changed["run_budget"]["publication"] = "hold"
    assert client.post(url, headers=headers, json=changed).status_code == 409
    changed["run_budget"]["generated_tokens"] = 48000
    assert client.post(url, headers=headers, json=changed).status_code == 422


def test_owner_budget_expiry_never_starts_an_attempt(client, headers, store, supported, tmp_path):
    _, runtime = approve(client, headers, store, supported, tmp_path)
    with write_transaction(store) as c:
        c.execute(
            proposal_decisions.update().values(
                created_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
            )
        )
    assert not task_budget.authorize_one(store, runtime)
    assert runtime.calls == 0


def test_verified_candidate_waits_without_operational_publication_activation(
    client, headers, store, supported, tmp_path, monkeypatch
):
    _, runtime = approve(client, headers, store, supported, tmp_path)
    monkeypatch.delenv("WORKBENCH_PUBLICATION_ROOT", raising=False)
    assert execution.process_one(store, runtime)
    assert delivery.enqueue_one(store)
    assert not delivery.process_one(store)
    assert runtime.calls == 1
    with store.connect() as c:
        assert c.execute(select(real_publications.c.status)).scalar() == "pending"


def test_base_movement_holds_delivery_without_rerunning_implementation(
    client, headers, store, supported, tmp_path
):
    _, runtime = approve(client, headers, store, supported, tmp_path)
    remote = ControlledRemote()
    remote.base = "a" * 40
    drain(store, runtime, remote)
    with store.connect() as c:
        assert c.execute(select(real_publications.c.status)).scalar() == "held"
    assert runtime.calls == 1 and remote.pushes == remote.creates == 0


def test_changed_offer_is_rejected_before_saving_approval(client, headers, store, supported):
    changed = copy.deepcopy(supported["decision"])
    changed["run_budget"]["offer_fingerprint"] = "0" * 64
    result = client.post(
        "/api/intake/" + supported["intake_id"] + "/decisions", headers=headers, json=changed
    )
    assert result.status_code == 409
    with store.connect() as c:
        assert not c.execute(select(proposal_decisions)).first()


def test_new_recipe_real_parser_read_edit_failed_check_repair_and_delivery(
    client, headers, store, supported, tmp_path, monkeypatch, http_tape
):
    """NON-MODEL HTTP tape and stub check outcomes through the production path."""
    import shutil
    from workbench.real_workspace import Runtime
    from test_real_transport import tool_response, final_response

    ref, _ = approve(client, headers, store, supported, tmp_path)
    root = tmp_path / "SCRIPTED_NATIVE_ADAPTER"
    root.mkdir()
    durable(root / "recipe.json", json.dumps(supported["template"]["recipe"]).encode())
    durable(root / "recipe-oracle.lua", ORACLE)
    runtime = Runtime(root)
    runtime.prepare()
    runtime.task_ref = ref
    try:
        runtime.primary = http_tape.adapter
        monkeypatch.setattr(
            runtime,
            "preflight",
            lambda: [dict(name="NON-MODEL scripted activation gate", status="passed")],
        )
        monkeypatch.setattr(
            "workbench.real_workspace.confine.root", lambda: str(tmp_path / "NON_MODEL_CGROUP")
        )
        path = runtime.workspace / execution.PATHS[0]
        first = path.read_text().splitlines(True)[0]
        incomplete = "-- NON_MODEL_INCOMPLETE, discarded readiness data\n"
        repaired = "-- NON_MODEL_REPAIRED, discarded readiness data\n"
        checks = []

        def check(argv, cwd, ro=(), timeout=120, private_loopback=False):
            checks.append(argv)
            fails = str(root / "oracle.lua") in argv and incomplete in path.read_text()
            return dict(
                status="fail" if fails else "pass",
                rc=1 if fails else 0,
                command=argv,
                out="NON-MODEL required behavior missing"
                if fails
                else "\n".join("ok   " + n for n in runtime.expected_suite())
                + f"\n{len(runtime.expected_suite())} passed, 0 failed",
                network_mode="private-loopback" if private_loopback else "denied",
            )

        monkeypatch.setattr(runtime, "check", check)

        def correction(body):
            assert "required behavior missing" in body["messages"][-1]["content"]
            return tool_response(
                [
                    ("read_file", dict(path=execution.PATHS[0], start=1, end=1)),
                    ("replace_text", dict(path=execution.PATHS[0], old=incomplete, new=repaired)),
                ]
            )

        http_tape.chunked = True
        http_tape.steps = [
            tool_response([("read_file", dict(path=execution.PATHS[0], start=1, end=1))] * 4),
            tool_response(
                [("replace_text", dict(path=execution.PATHS[0], old=first, new=incomplete + first))]
            ),
            tool_response([("run_check", dict(name="candidate"))]),
            correction,
            tool_response([("run_check", dict(name="candidate"))]),
            final_response(),
        ]
        remote = ControlledRemote()
        drain(store, runtime, remote)
        with store.connect() as c:
            request = c.execute(select(requests)).mappings().one()
            assert request["status"] == "checked"
            assert len(c.execute(select(model_attempts)).all()) == 1
        assert request["result"]["usage"]["values"]["completion_tokens"] == 300
        assert request["result"]["model_check_rounds"][0]["status"] == "fail"
        assert request["result"]["model_check_rounds"][1]["status"] == "pass"
        assert delivery.view(store, request["id"])["source"] == "controlled"
        assert len(http_tape.bodies) == 6 and remote.pushes == remote.creates == 1
        assert len(checks) == 11 and runtime.verify_frozen(request["result"])
    finally:
        shutil.rmtree(runtime.workspace.parent)


def test_delivery_restart_after_push_and_missing_receipt_never_pushes_twice(
    client, headers, store, supported, tmp_path
):
    _, runtime = approve(client, headers, store, supported, tmp_path)
    remote = ControlledRemote()
    assert execution.process_one(store, runtime)
    assert delivery.enqueue_one(store)
    assert delivery.process_one(store, remote)  # prepare
    assert delivery.process_one(store, remote)  # durable intent, external effect
    with write_transaction(store) as c:
        row = c.execute(select(real_publications)).mappings().one()
        state = dict(row["state"], owner={"pid": -1, "start": "gone", "boot": "gone"})
        c.execute(real_publications.update().values(state=state))
    drain(store, runtime, remote)
    assert runtime.calls == remote.pushes == remote.creates == 1


def test_unobservable_write_is_held_without_retry_or_second_model(
    client, headers, store, supported, tmp_path
):
    _, runtime = approve(client, headers, store, supported, tmp_path)

    class Unknown(ControlledRemote):
        def push(self, snapshot, commit):
            self.pushes += 1
            raise delivery.UncertainWrite("push")

    remote = Unknown()
    drain(store, runtime, remote)
    with store.connect() as c:
        assert c.execute(select(real_publications.c.status)).scalar() == "held"
    assert remote.pushes == runtime.calls == 1 and remote.creates == 0


def test_stop_and_same_process_concurrency_do_not_publish(
    client, headers, store, supported, tmp_path
):
    import threading

    _, runtime = approve(client, headers, store, supported, tmp_path)
    assert execution.process_one(store, runtime)
    assert delivery.enqueue_one(store)
    remote = ControlledRemote()
    stop = threading.Event()
    stop.set()
    assert not delivery.process_one(store, remote, stop_event=stop)
    assert delivery._delivery_lock.acquire(blocking=False)
    try:
        assert not delivery.process_one(store, remote)
    finally:
        delivery._delivery_lock.release()
    assert remote.prepares == remote.pushes == remote.creates == 0


def test_publisher_rejects_control_output_and_changed_commit_before_any_push(tmp_path):
    from workbench.publication_adapter import PilotGitHub

    snapshot = dict(
        repository="Twich-Team/TwichUI",
        base_branch="main",
        branch="maintenance/workbench-" + "a" * 20,
        execution_id=str(uuid4()),
    )
    adapter = PilotGitHub(tmp_path, snapshot)
    with pytest.raises(delivery.DeliveryHeld, match="real model"):
        adapter.prepare(snapshot, dict(inference_ran=False))
    calls = []

    def read_only(argv, cwd=None, raw=False):
        calls.append(argv)
        return "a" * 40

    adapter._call = read_only
    with pytest.raises(delivery.DeliveryHeld, match="recorded commit"):
        adapter.push(snapshot, "b" * 40)
    assert len(calls) == 1 and calls[0][:2] == ["git", "rev-parse"]


def test_task_session_rejects_unbounded_or_public_configuration(tmp_path):
    from workbench.task_session import read_config

    config = tmp_path / "session.json"
    config.write_text("{}")
    config.chmod(0o644)
    with pytest.raises(ValueError, match="Private owned"):
        read_config(config)
    config.chmod(0o600)
    now = datetime.now(timezone.utc)
    config.write_text(
        json.dumps(
            dict(
                started_at=now.isoformat(),
                deadline=(now + timedelta(hours=4)).isoformat(),
                max_attempts=1,
                max_probes=1,
                max_leases=1,
            )
        )
    )
    with pytest.raises(ValueError, match="bounded session"):
        read_config(config)


def test_store_failure_after_effect_recovers_same_candidate_and_delivery(
    client, headers, store, supported, tmp_path, monkeypatch
):
    from sqlalchemy.exc import OperationalError

    _, runtime = approve(client, headers, store, supported, tmp_path)
    remote = ControlledRemote()
    assert execution.process_one(store, runtime)
    assert delivery.enqueue_one(store)
    assert delivery.process_one(store, remote)
    original = delivery._save

    def unavailable(engine, identity, fence, state, status, message):
        if state.get("uncertain") == "push":
            raise OperationalError("controlled busy store", {}, Exception("locked"))
        return original(engine, identity, fence, state, status, message)

    monkeypatch.setattr(delivery, "_save", unavailable)
    with pytest.raises(OperationalError):
        delivery.process_one(store, remote)
    monkeypatch.setattr(delivery, "_save", original)
    drain(store, runtime, remote)
    assert runtime.calls == remote.pushes == remote.creates == 1
    with store.connect() as c:
        assert c.execute(select(real_publications.c.status)).scalar() == "published"


def test_default_application_factory_routes_use_its_owned_store(store, monkeypatch):
    from fastapi.testclient import TestClient
    from workbench import api

    monkeypatch.setattr(api, "engine_for", lambda: store)
    with TestClient(api.create_app()) as client:
        assert client.get("/api/real-executions/" + str(uuid4()) + "/delivery").json() is None
        assert (
            client.get(
                "/api/intake/" + str(uuid4()) + "/proposals/" + str(uuid4()) + "/run-offer"
            ).status_code
            == 404
        )


def test_new_recipe_control_names_are_safe_and_require_an_incomplete_repair_negative(tmp_path):
    from workbench.real_recipes import validate
    from workbench.real_workspace import Runtime

    spec = template_data()["recipe"]
    for invalid in [
        dict(spec["controls"], incomplete="pass"),
        {"original": "fail", "known-good-oracle-control": "pass", "../escape": "fail"},
    ]:
        with pytest.raises(ValueError):
            validate(dict(spec, controls=invalid))
    root = tmp_path / "NON_MODEL_CONTROL_LABELS"
    root.mkdir()
    durable(root / "recipe.json", json.dumps(spec).encode())
    durable(root / "recipe-oracle.lua", ORACLE)
    durable(root / "oracle.lua", ORACLE)
    runtime = Runtime(root)
    durable(
        root / "oracle-controls.json",
        json.dumps(
            dict(
                oracle_sha256=sha(ORACLE),
                results={
                    key: dict(status=status, rc=0 if status == "pass" else 1)
                    for key, status in spec["controls"].items()
                },
            )
        ).encode(),
    )
    boundary = root / "non-model-boundary.json"
    durable(
        boundary,
        json.dumps(
            dict(
                checks={
                    "controlled": dict(usage={"cgroup_removed": True, "leaked_after_kill": False})
                }
            )
        ).encode(),
    )
    runtime.record_controls(boundary)
    marker = json.loads((root / "controls-passed.json").read_text())
    assert set(marker["checks"]) == {
        "reproduction",
        "positive-control",
        "negative-controls",
        "boundary",
    }
    assert runtime.controls_valid()


def test_local_preparation_recovery_preserves_failure_clock_and_never_reexecutes(
    client, headers, store, supported, tmp_path
):
    _, runtime = approve(client, headers, store, supported, tmp_path)
    assert execution.process_one(store, runtime)
    assert delivery.enqueue_one(store)

    class FailOnce(ControlledRemote):
        first = True

        def prepare(self, *args):
            if self.first:
                self.first = False
                raise delivery.DeliveryHeld("Prepared patch changed unrelated paths.")
            return super().prepare(*args)

        def prepared_patch_matches(self, *args):
            return True

    remote = FailOnce()
    assert delivery.process_one(store, publisher=remote)
    with store.connect() as c:
        before = dict(c.execute(select(real_publications)).mappings().one())
    assert before["status"] == "held"
    command = str(uuid4())
    assert (
        delivery.resume_preparation(store, before["execution_id"], command, "a" * 64, remote)
        == "pending"
    )
    assert (
        delivery.resume_preparation(store, before["execution_id"], command, "a" * 64, remote)
        == "pending"
    )
    with pytest.raises(delivery.DeliveryHeld, match="Conflicting"):
        delivery.resume_preparation(store, before["execution_id"], str(uuid4()), "a" * 64, remote)
    with store.connect() as c:
        after = c.execute(select(real_publications)).mappings().one()
        assert (
            after["snapshot"] == before["snapshot"]
            and after["state"]["started_at"] == before["state"]["started_at"]
        )
        assert (
            after["state"]["preparation_recovery"]["previous_outcome"]["state"] == before["state"]
        )
    drain(store, runtime, remote)
    assert runtime.calls == remote.pushes == remote.creates == 1
