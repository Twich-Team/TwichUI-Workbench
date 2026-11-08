"""Controlled connector only; real shared source/diagnosis/plan/review/approval path."""

import json
import os
from pathlib import Path
import pytest
from sqlalchemy import select
from workbench import codex_provider, worker
from workbench.db import model_attempts, planning_jobs
from workbench.planning_provider import LocalProvider
from test_bug_workflow import enable_bugs, report
from test_bug_process_rehearsal import program_answer
from test_everyday import installed, save_codex_policy, write_policy  # noqa: F401
from test_verification_profiles import PRODUCTION


def enable(store, monkeypatch):
    enable_bugs(monkeypatch)
    path = Path(os.environ["WORKBENCH_PREPARATION_CONFIG"])
    policy = json.loads(path.read_text())
    policy["bug_plan_review"] = "source-grounded-lua-v1"
    write_policy(path, policy)
    save_codex_policy(store)
    monkeypatch.setattr(
        LocalProvider, "generate", lambda *a, **kw: pytest.fail("Codex-only task used Qwen")
    )


@pytest.mark.usefixtures("installed")
def test_codex_uses_same_investigation_plan_and_review_before_normal_approval(
    client, headers, store, monkeypatch
):
    enable(store, monkeypatch)
    calls = []

    def generate(model, effort, prompt, schema, stop, **kwargs):
        assert kwargs == {"input_limit": codex_provider.MAX_BUG_INPUT} and not stop()
        packet = json.loads(prompt.split("\nUntrusted task data:\n", 1)[1])
        calls.append(packet)
        phase = packet["investigation_progress"]["phase"]
        assert "prior_rejected_plan_feedback" not in packet
        if phase == "source_diagnosis":
            value = dict(
                finding="The existing exported operation keeps uppercase input.",
                draft=dict(
                    diagnosis=dict(
                        cause="The inspected operation trims without lowercasing.",
                        evidence_paths=[PRODUCTION],
                    )
                ),
            )
        elif phase == "plan_construction":
            answer = program_answer(False)
            value = dict(finding=answer["finding"], draft=dict(plan=answer["plan"]))
        else:
            assert phase == "plan_self_check" and schema["title"] == "ReviewResponse"
            value = dict(review_binding=packet["review_binding"], ready=True, issues=[])
        return value, dict(
            prompt_tokens=40, completion_tokens=10, total_tokens=50, reasoning_tokens=2
        )

    monkeypatch.setattr(codex_provider, "generate", generate)
    identity = client.post(
        "/api/intake", headers=headers, json={**report(), "local_planning": "codex-once"}
    ).json()["id"]
    assert worker.tick(store)
    item = client.get("/api/intake/" + identity).json()
    assert (
        item["status"] == "completed"
        and len(item["proposals"]) == 1
        and not item["tasks"]
        and not item["decisions"]
    )
    assert [p["investigation_progress"]["phase"] for p in calls] == [
        "source_diagnosis",
        "plan_construction",
        "plan_self_check",
    ]
    with store.connect() as c:
        attempt = (
            c.execute(select(model_attempts).where(model_attempts.c.task_id == identity))
            .mappings()
            .one()
        )
        job = (
            c.execute(select(planning_jobs).where(planning_jobs.c.intake_id == identity))
            .mappings()
            .one()
        )
    assert attempt["backend"] == "codex" and attempt["outcome"] == "completed"
    assert attempt["usage"]["requests_observed"] == 3 and attempt["usage"]["values"] == dict(
        prompt_tokens=120, completion_tokens=30, total_tokens=150, reasoning_tokens=6
    )
    assert (
        "Codex" in attempt["usage"]["source"]
        and job["payload"]["local_planning_receipt"]["max_total_model_requests"] == 8
    )


@pytest.mark.parametrize("change", ["new_path", "changed_text", "changed_hash", "new_search_match"])
@pytest.mark.usefixtures("installed")
def test_codex_never_transmits_source_outside_initial_task_binding(store, monkeypatch, change):
    enable(store, monkeypatch)
    from workbench import bug_investigation as bug
    from workbench.trusted_preparation import configuration
    from workbench.bug_evidence import BugPlanningResponse

    packet = bug.context(report(), "", configuration(store))
    adapter = codex_provider.Planner(dict(model="simulated-codex", effort=None), lambda: False)
    adapter.bind_source(packet)
    _, wire = bug.planning_request(packet, report(), "")
    if change == "new_path":
        wire["files"][0]["path"] = "!!!TwichUI/new-private.lua"
    if change == "changed_text":
        wire["files"][0]["excerpts"][0]["text"] = "PRIVATE_UNAUTHORIZED_SOURCE"
    if change == "changed_hash":
        wire["files"][0]["sha256"] = "0" * 64
    if change == "new_search_match":
        wire["inspection_result"] = dict(
            search_matches=[dict(path="!!!TwichUI/new-private.lua", text="PRIVATE")]
        )
    monkeypatch.setattr(
        codex_provider,
        "generate",
        lambda *a, **kw: pytest.fail("Unauthorized source reached connector"),
    )
    with pytest.raises(codex_provider.Hold) as held:
        adapter.generate(wire, BugPlanningResponse)
    assert held.value.reason == "disclosure" and not held.value.started
