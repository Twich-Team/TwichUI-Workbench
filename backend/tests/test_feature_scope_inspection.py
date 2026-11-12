"""App-owned committed-source navigation before feature scope generation."""

import copy
import json
import pytest

from sqlalchemy import select

from workbench import feature_preparation
from workbench import feature_step_source
from workbench import feature_steps
from workbench.db import planning_jobs, real_execution_grants, model_attempts
from test_feature_preparation import context, ScopePlanner
from test_feature_steps import scope
from test_everyday import installed as installed


def read_request():
    return dict(inspection=[dict(path=scope()["read_paths"][0], start=1, end=120)], limitation="")


@pytest.mark.parametrize("has_test", [False, True])
def test_local_request_really_transmits_inspected_scope_and_same_native_schema(tmp_path, has_test):
    from uuid import uuid4
    from workbench.planning_provider import LocalProvider
    from workbench.planning_response import Evidence

    model = "pilot-qwen38-27b"
    files = [
        dict(path="!!!TwichUI/View.lua", excerpts=[dict(line=1, text="local state = {}")]),
        dict(
            path="tests/test_actual.lua",
            excerpts=[dict(line=1, text="local test = {}")] if has_test else [],
        ),
        dict(path="tests/test_metadata_only.lua", excerpts=[]),
    ]
    coverage = dict(focused_test_candidates=["tests/test_actual.lua"])
    packet = dict(original=dict(title="A feature"), files=files, coverage=coverage)
    recorded = Evidence(
        tmp_path, str(uuid4()), feature_steps.StepScope, coverage=coverage, files=files
    )
    calls = []

    class Transport:
        seconds = 120

        def models(self, _):
            return model

        def read(self, request, limit):
            body = json.loads(request.data)
            calls.append(body)
            outgoing = body["response_format"]["json_schema"]["schema"]
            assert outgoing == recorded.schema
            assert json.loads(body["messages"][1]["content"]) == packet
            complete = [branch for branch in outgoing["anyOf"] if "role" in branch["required"]]
            if has_test:
                assert complete[0]["properties"]["focused_test"]["enum"] == [
                    "tests/test_actual.lua"
                ]
                assert complete[0]["properties"]["read_paths"]["items"]["enum"] == [
                    "!!!TwichUI/View.lua",
                    "tests/test_actual.lua",
                ]
            else:
                assert not complete
                read = next(
                    branch for branch in outgoing["anyOf"] if branch["required"] == ["inspection"]
                )
                assert read["properties"]["inspection"]["items"]["properties"]["path"]["enum"] == [
                    "tests/test_actual.lua"
                ]
            raw = dict(inspection=[dict(path="tests/test_actual.lua", start=1, end=80)])
            return json.dumps(
                dict(
                    id="controlled-local-scope-wire",
                    model=model,
                    usage=dict(prompt_tokens=10, completion_tokens=5, total_tokens=15),
                    choices=[dict(finish_reason="stop", message=dict(content=json.dumps(raw)))],
                )
            ).encode()

    provider = LocalProvider()
    provider.response_evidence = recorded
    try:
        result, actual, basis = provider._infer(
            Transport(), "inert", feature_steps.StepScope, json.dumps(packet), expected_model=model
        )
        assert (
            result["inspection"][0]["path"] == "tests/test_actual.lua"
            and actual == model
            and basis == "local"
        )
        assert len(calls) == 1
        assert provider.diagnostics["usage"]["values"]["total_tokens"] == 15
    finally:
        recorded.close()


def test_scope_wire_requires_an_outcome_and_only_inspected_existing_test_choices():
    from workbench.planning_response import wire_schema

    files = [
        dict(path="!!!TwichUI/View.lua", excerpts=[dict(line=1, text="local state = {}")]),
        dict(path="tests/test_view.lua", excerpts=[dict(line=1, text="local H = {}")]),
        dict(path="tests/test_metadata_only.lua", excerpts=[]),
    ]
    schema = wire_schema(feature_steps.StepScope, files=files)
    assert all(branch["required"] for branch in schema["anyOf"])
    complete = schema["anyOf"][0]
    assert set(complete["required"]) == {
        "role",
        "read_paths",
        "writable_paths",
        "focused_test",
        "checks",
    }
    assert complete["properties"]["focused_test"]["enum"] == ["tests/test_view.lua"]
    assert complete["properties"]["read_paths"]["items"]["enum"] == [
        "!!!TwichUI/View.lua",
        "tests/test_view.lua",
    ]
    inspection = next(b for b in schema["anyOf"] if b["required"] == ["inspection"])
    assert inspection["properties"]["inspection"]["type"] == "array"
    without_test = wire_schema(feature_steps.StepScope, files=files[:1])
    assert all("role" not in branch["required"] for branch in without_test["anyOf"])
    guided = wire_schema(
        feature_steps.StepScope,
        files=files[:1],
        coverage=dict(focused_test_candidates=["tests/test_actual.lua"]),
    )
    branch = next(b for b in guided["anyOf"] if b["required"] == ["inspection"])
    item = branch["properties"]["inspection"]["items"]
    assert item["properties"]["path"]["enum"] == ["tests/test_actual.lua"]
    assert item["properties"]["start"]["const"] == 1
    assert item["properties"]["end"]["const"] == 80
    # Once an existing test has actually been read, ordinary source navigation
    # and the complete-scope branch return. Index names alone cannot do this.
    restored = wire_schema(
        feature_steps.StepScope,
        files=files,
        coverage=dict(focused_test_candidates=["tests/test_actual.lua"]),
    )
    assert restored["anyOf"][0]["properties"]["focused_test"]["enum"] == ["tests/test_view.lua"]


def test_reused_first_window_does_not_discard_fresh_test_in_same_batch(
    client,
    headers,
    store,
    installed,
):
    decision, task, _, job_id = context(client, headers, store)
    assert feature_preparation.prepare_scope(
        store, job_id, task, adapter=ScopePlanner(response=read_request())
    )
    batch = dict(
        inspection=[
            *read_request()["inspection"],
            dict(path=scope()["focused_test"], start=1, end=80),
        ]
    )
    assert feature_preparation.prepare_scope(
        store, job_id, task, adapter=ScopePlanner(response=batch)
    )
    with store.connect() as c:
        job = c.execute(select(planning_jobs).where(planning_jobs.c.id == job_id)).mappings().one()
        history = job["payload"]["scope_history"]
        assert history[-1]["failure"] is None and history[-1]["inspection"]
        packet = feature_preparation.packet(
            c,
            decision,
            task,
            None,
            inspections=[item["inspection"] for item in history if item.get("inspection")],
        )
        assert len(packet["source_windows"]) == 2
        assert packet["source_inspection_results"][0]["reused"] is True
        assert "reused" not in packet["source_inspection_results"][1]
        assert any(f["path"] == scope()["focused_test"] and f["excerpts"] for f in packet["files"])
        assert not c.execute(select(real_execution_grants)).first()


def test_inspect_actual_definitions_then_prepare_scope_in_same_parent_and_original_limits(
    client,
    headers,
    store,
    installed,
):
    _, task, _, job_id = context(client, headers, store)
    first = ScopePlanner(response=read_request())
    assert feature_preparation.prepare_scope(store, job_id, task, adapter=first)
    with store.connect() as c:
        job = c.execute(select(planning_jobs).where(planning_jobs.c.id == job_id)).mappings().one()
        assert job["status"] == "queued" and job["payload"]["phase"] == "prepare_scopes"
        assert (
            job["payload"]["scope_history"][0]["inspection"]["requests"]
            == read_request()["inspection"]
        )
        assert not job["payload"]["current_step_scope"]
        assert not c.execute(select(real_execution_grants)).first()
        assert (
            c.execute(
                select(model_attempts.c.outcome).where(
                    model_attempts.c.purpose == "feature_step_scope"
                )
            ).scalar_one()
            == "completed"
        )
    second = ScopePlanner()
    assert feature_preparation.prepare_scope(store, job_id, task, adapter=second)
    packet = second.calls[0]
    assert packet["source_index"] and packet["source_windows"]
    assert packet["source_windows"][0]["path"] == scope()["read_paths"][0]
    assert packet["source_inspection_results"][0]["git_blob"]
    with store.connect() as c:
        job = c.execute(select(planning_jobs).where(planning_jobs.c.id == job_id)).mappings().one()
        assert (
            job["payload"]["phase"] == "scope_ready" and len(job["payload"]["scope_history"]) == 2
        )
        assert not c.execute(select(real_execution_grants)).first()


def test_repeated_source_window_cannot_masquerade_as_new_information_or_replay_forever(
    client,
    headers,
    store,
    installed,
):
    _, task, _, job_id = context(client, headers, store)
    planner = ScopePlanner(response=read_request())
    for _ in range(3):
        assert feature_preparation.prepare_scope(store, job_id, task, adapter=planner)
    assert not feature_preparation.prepare_scope(store, job_id, task, adapter=planner)
    assert len(planner.calls) == 3
    with store.connect() as c:
        job = c.execute(select(planning_jobs).where(planning_jobs.c.id == job_id)).mappings().one()
        assert job["status"] == "failed" and job["payload"]["phase"] == "held"
        assert "already inspected" in job["message"]
        assert len(job["payload"]["scope_history"]) == 3
        assert not c.execute(select(real_execution_grants)).first()


def test_correct_scope_with_unknown_accounting_never_authorizes_implementation(
    client,
    headers,
    store,
    installed,
):
    _, task, _, job_id = context(client, headers, store)

    class UnknownUsage(ScopePlanner):
        def generate(self, *args, **kwargs):
            result = super().generate(*args, **kwargs)
            self.diagnostics = {**self.diagnostics, "accounting_state": "unknown", "usage": None}
            return result

    planner = UnknownUsage()
    assert feature_preparation.prepare_scope(store, job_id, task, adapter=planner)
    with store.connect() as c:
        job = c.execute(select(planning_jobs).where(planning_jobs.c.id == job_id)).mappings().one()
        assert job["status"] == "failed" and job["payload"]["phase"] == "held"
        assert not job["payload"]["current_step_scope"]
        assert job["payload"]["scope_history"][0]["confirmed_finished"] is False
        assert not c.execute(select(real_execution_grants)).first()
    assert not feature_preparation.prepare_scope(store, job_id, task, adapter=planner)


def test_actual_unsupported_scope_holds_after_one_finished_request_without_unchanged_retries(
    client,
    headers,
    store,
    installed,
):
    _, task, _, job_id = context(client, headers, store)
    response = copy.deepcopy(scope())
    response["limitation"] = "This needs an unavailable game capability."
    planner = ScopePlanner(response=response)
    assert feature_preparation.prepare_scope(store, job_id, task, adapter=planner)
    assert not feature_preparation.prepare_scope(store, job_id, task, adapter=planner)
    assert len(planner.calls) == 1
    with store.connect() as c:
        job = c.execute(select(planning_jobs).where(planning_jobs.c.id == job_id)).mappings().one()
        assert job["status"] == "failed" and len(job["payload"]["scope_history"]) == 1


def test_later_source_reads_use_the_actual_checked_predecessor_not_original_production(
    client,
    headers,
    store,
    installed,
    monkeypatch,
):
    from workbench import worker
    from workbench.real_workspace import Runtime
    from test_feature_execution import (
        missing_names_parent,
        controlled_check,
        controlled_readiness,
        controlled_slot,
    )

    monkeypatch.setattr(Runtime, "check", controlled_check)
    _, _, _, second = missing_names_parent(client, headers, store)

    def tick(planner):
        return worker.tick(
            store,
            planning_adapter=planner,
            feature_provision=controlled_readiness,
            session_adapter=controlled_slot,
        )

    for _ in range(5):
        assert tick(ScopePlanner())
    reader = ScopePlanner(response=read_request())
    assert tick(reader)
    writer = ScopePlanner()
    assert tick(writer)
    assert "text" not in writer.calls[0]["source_windows"][0]
    assert any(
        "if s == nil then return nil end" in excerpt["text"]
        for file in writer.calls[0]["files"]
        for excerpt in file["excerpts"]
    )
    with store.connect() as c:
        job = (
            c.execute(select(planning_jobs).where(planning_jobs.c.kind == "feature_execution"))
            .mappings()
            .one()
        )
        assert len(job["payload"]["completed_steps"]) == 1
        assert job["payload"]["current_step_scope"]["task_id"] == second
        assert job["payload"]["phase"] == "scope_ready"


def test_scope_packet_keeps_new_definitions_once_and_fits_the_existing_transport():
    latest = "NEW-DEFINITION-" + "x" * 6000
    old = "OLDER-HEURISTIC-" + "y" * 14000
    packet = dict(
        original={"description": "Keep the exact reviewed request."},
        approved_scope=["Preserve defaults."],
        source_note="Partial source.",
        files=[
            dict(
                path="!!!TwichUI/map/View.lua",
                sha256="a" * 64,
                excerpts=[dict(line=1, text=old), dict(line=350, text=latest)],
            )
        ],
        source_windows=[dict(path="!!!TwichUI/map/View.lua", start=350, end=430, sha256="a" * 64)],
        source_inspection_results=[
            dict(kind="read", path="!!!TwichUI/map/View.lua", start=350, end=430)
        ],
        source_index=[
            dict(path="!!!TwichUI/map/example" + str(i) + ".lua", bytes=100) for i in range(96)
        ],
    )
    packed = feature_step_source.compact(packet)
    raw = json.dumps(packed, ensure_ascii=True, separators=(",", ":"))
    assert len(raw.encode()) <= 22000 and raw.count(latest) == 1
    assert (
        packed["original"] == packet["original"]
        and packed["approved_scope"] == packet["approved_scope"]
    )
    assert packed["source_index_truncated"] and len(packed["source_index"]) == 32
    assert len(packet["source_index"]) == 96


def test_scope_packet_never_silently_truncates_the_reviewed_request_or_latest_definitions():
    packet = dict(
        original={"description": "x" * 24000},
        source_note="Partial source.",
        source_index=[],
        files=[],
        source_windows=[dict(path="!!!TwichUI/map/View.lua")],
        source_inspection_results=[],
    )
    with pytest.raises(ValueError, match="reviewed requirements"):
        feature_step_source.compact(packet)
