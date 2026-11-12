"""App-owned committed-source navigation before feature scope generation."""

import copy

from sqlalchemy import select

from workbench import feature_preparation
from workbench.db import planning_jobs, real_execution_grants, model_attempts
from test_feature_preparation import context, ScopePlanner
from test_feature_steps import scope
from test_everyday import installed as installed


def read_request():
    return dict(inspection=[dict(path=scope()["read_paths"][0], start=1, end=120)], limitation="")


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
    assert "if s == nil then return nil end" in writer.calls[0]["source_windows"][0]["text"]
    with store.connect() as c:
        job = (
            c.execute(select(planning_jobs).where(planning_jobs.c.kind == "feature_execution"))
            .mappings()
            .one()
        )
        assert len(job["payload"]["completed_steps"]) == 1
        assert job["payload"]["current_step_scope"]["task_id"] == second
        assert job["payload"]["phase"] == "scope_ready"
