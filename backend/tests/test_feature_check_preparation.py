"""Blind check preparation after actual controlled dependent implementations."""

import copy
from uuid import uuid4

import pytest
from sqlalchemy import select

from workbench import worker, feature_checks, feature_execution, feature_preparation
from workbench.db import planning_jobs, model_attempts, real_execution_requests, proposal_decisions
from workbench.real_workspace import Runtime
from test_feature_execution import (
    missing_names_parent,
    controlled_check,
    controlled_readiness,
    controlled_slot,
)
from test_feature_preparation import ScopePlanner
from test_feature_steps import scope
from test_feature_checks import program
from test_everyday import installed as installed


class IndependentPlanner(ScopePlanner):
    def __init__(self, *, unknown=False, stop=None):
        super().__init__()
        self.unknown, self.stop = unknown, stop
        self.blind_packets = []

    def generate(self, packet, schema, expected_model):
        if schema not in (feature_checks.Response, feature_checks.Review):
            result, model, basis = super().generate(packet, schema, expected_model)
            if packet["task"]["title"] == "Check the complete feature":
                result = {**scope(), "role": "verification", "writable_paths": []}
            if packet["task"]["title"] == "Prepare the feature delivery":
                result = {**scope(), "role": "delivery", "writable_paths": []}
            return result, model, basis
        self.blind_packets.append(copy.deepcopy(packet))
        self.diagnostics = dict(
            generation_state="uncertain" if self.unknown else "finished",
            accounting_state="unknown" if self.unknown else "complete",
            usage=None
            if self.unknown
            else dict(
                source="controlled_fixture",
                units="tokens",
                values=dict(prompt_tokens=30, completion_tokens=10, total_tokens=40),
            ),
        )
        if self.stop:
            self.stop()
        if self.unknown:
            raise ValueError("CONTROLLED lost response; no replay allowed")
        if schema is feature_checks.Review:
            return (
                dict(program_binding=packet["program_binding"], ready=True, issues=[]),
                expected_model,
                "demo",
            )
        return (
            dict(
                finding="Observe actual original normalized values.",
                program=program(packet),
                inspection=None,
                limitation=None,
            ),
            expected_model,
            "demo",
        )


def at_independent_preparation(client, headers, store, monkeypatch):
    monkeypatch.setattr(Runtime, "check", controlled_check)
    identity, decision, _, _ = missing_names_parent(client, headers, store, verification=True)
    planner = IndependentPlanner()

    def tick(adapter=planner):
        return worker.tick(
            store,
            planning_adapter=adapter,
            feature_provision=controlled_readiness,
            session_adapter=controlled_slot,
        )

    for _ in range(10):
        assert tick()  # Two actual checked dependent implementations.
    assert tick()  # Exact verification scope.
    assert tick()  # Start blind preparation, without executing its observations.
    return identity, decision, planner, tick


def test_existing_worker_prepares_and_reviews_original_only_feature_observations(
    client,
    headers,
    store,
    installed,
    monkeypatch,
):
    identity, decision, planner, tick = at_independent_preparation(
        client, headers, store, monkeypatch
    )
    assert tick() and tick()
    assert len(planner.blind_packets) == 2
    for packet in planner.blind_packets:
        assert packet["base"] and "original" in packet and packet["approved_plan"]
        assert not {
            "feature_input",
            "completed_steps",
            "candidate",
            "result",
            "patch",
            "source",
        } & set(packet)
        assert all(
            "if s == nil then return nil end" not in e["text"]
            for f in packet["files"]
            for e in f["excerpts"]
        )
    with store.connect() as c:
        job = (
            c.execute(select(planning_jobs).where(planning_jobs.c.kind == "feature_execution"))
            .mappings()
            .one()
        )
        saved = job["payload"]["independent_check_preparation"]
        assert job["payload"]["phase"] == "independent_checks_ready"
        assert saved["review"]["ready"] and len(saved["history"]) == 2
        assert all(record["confirmed_finished"] for record in saved["history"])
        assert saved["proposed_program"]["independently_executed"] is False
        assert len(job["payload"]["completed_steps"]) == 2
        attempts = list(
            c.execute(
                select(model_attempts).where(
                    model_attempts.c.purpose == "feature_check_preparation"
                )
            ).mappings()
        )
        assert len(attempts) == 2 and all(
            a["usage"]["values"]["completion_tokens"] == 10 for a in attempts
        )
        assert len(c.execute(select(real_execution_requests)).all()) == 2
        assert len(c.execute(select(proposal_decisions)).all()) == 2
    view = client.get("/api/features/" + identity).json()
    assert view["completed_steps"] == 2 and view["total_steps"] == 4
    assert view["integrated_verification"] == "not_recorded"
    assert view["state"] != "completed" and view["run"]["id"] == decision



@pytest.mark.parametrize("fault", ["unknown", "stop", "crash"])
def test_uncertain_stopped_or_interrupted_check_preparation_cannot_advance_or_replay(
    client,
    headers,
    store,
    installed,
    monkeypatch,
    fault,
):
    identity, decision, _, tick = at_independent_preparation(client, headers, store, monkeypatch)

    def stop():
        response = client.post(
            "/api/features/" + identity + "/runs/" + decision + "/stop",
            headers=headers,
            json=dict(command_id=str(uuid4())),
        )
        assert response.status_code == 200, response.text

    planner = IndependentPlanner(unknown=fault == "unknown", stop=stop if fault == "stop" else None)
    if fault == "crash":

        def lost(*a, **k):
            raise RuntimeError("CONTROLLED worker exit before provider completion")

        planner.generate = lost
        assert tick(planner)  # The shared worker contains the failed task.
        monkeypatch.setattr(feature_preparation, "alive", lambda owner: False)
        assert not feature_execution.advance(store)  # Already held by the same recovery owner.
    else:
        assert tick(planner)
    later = IndependentPlanner()
    assert not tick(later) and not later.blind_packets
    with store.connect() as c:
        job = (
            c.execute(select(planning_jobs).where(planning_jobs.c.kind == "feature_execution"))
            .mappings()
            .one()
        )
        assert job["status"] == "failed" and job["payload"]["phase"] == "held"
        assert len(c.execute(select(real_execution_requests)).all()) == 2
        attempt = (
            c.execute(
                select(model_attempts).where(
                    model_attempts.c.purpose == "feature_check_preparation"
                )
            )
            .mappings()
            .one()
        )
        assert attempt["finished_at"]
        assert (
            attempt["usage"] is None
            if fault in {"unknown", "crash"}
            else attempt["usage"]["values"]["completion_tokens"] == 10
        )
