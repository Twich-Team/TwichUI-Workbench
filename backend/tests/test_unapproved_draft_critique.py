"""Settled complete drafts are critique data; fresh plan and review remain mandatory."""

import copy
import json
import os
from pathlib import Path

import pytest
from sqlalchemy import update
from workbench.db import model_attempts
from workbench.investigation_memory import draft_for_critique
from test_bug_workflow import enable_bugs, report
from test_everyday import installed, write_policy  # noqa: F401
from test_bug_source_planning import scripted
from test_bug_plan_review import review, run
from test_bug_process_rehearsal import program_answer
from test_verification_profiles import PRODUCTION


def enable(monkeypatch):
    enable_bugs(monkeypatch)
    p = Path(os.environ["WORKBENCH_PREPARATION_CONFIG"])
    value = json.loads(p.read_text())
    value["bug_plan_review"] = "source-grounded-lua-v1"
    write_policy(p, value)


def old_failed(client, headers, store, monkeypatch):
    enable(monkeypatch)
    from test_bug_investigation_stage import diagnosis

    scripted(
        monkeypatch,
        [
            dict(
                finding="Read committed source first.",
                draft=dict(inspection=[dict(path=PRODUCTION, start=1, end=1)]),
            ),
            diagnosis(),
            program_answer(False),
            review(False),
            program_answer(False),
        ],
    )
    item, attempt = run(client, headers, store)
    assert item["status"] == "failed" and attempt["outcome"] == "failed"
    return attempt


@pytest.mark.parametrize("ready", [True, False])
@pytest.mark.usefixtures("installed")
def test_historical_review_cannot_return_old_plan_even_when_ready(
    client, headers, store, monkeypatch, ready
):
    old_failed(client, headers, store, monkeypatch)
    from workbench import investigation_memory

    monkeypatch.setattr(investigation_memory, "review_lessons", lambda *a: None)
    fresh = copy.deepcopy(program_answer(False))
    fresh["finding"] += " A complete fresh source-bound proposal."
    fresh["plan"]["reproducer"]["phases"][0]["code"] = "return subject(string.upper('alice'))"
    calls = scripted(monkeypatch, [review(ready), fresh, review()])
    item, attempt = run(client, headers, store)
    assert item["status"] == "completed" and len(item["proposals"]) == 1
    assert len(calls) == 3 and calls[0]["historical_draft_notice"]
    assert calls[1]["fresh_proposal_required"]
    assert (
        calls[1]["self_review"]["approved"] is False
        and calls[1]["self_review"]["executed"] is False
    )
    assert calls[0]["review_binding"] != calls[2]["review_binding"]
    assert attempt["usage"]["requests_observed"] == 3
    assert not item["decisions"] and not item["tasks"]


@pytest.mark.parametrize(
    "fault", ["hash", "mode", "symlink", "partial", "unknown", "success", "missing"]
)
@pytest.mark.usefixtures("installed")
def test_ineligible_historical_draft_is_never_reused(client, headers, store, monkeypatch, fault):
    from workbench import bug_investigation as bug
    from workbench.trusted_preparation import configuration
    from workbench.investigation_memory import seed

    old = old_failed(client, headers, store, monkeypatch)
    packet = bug.context(report(), "", configuration(store))
    packet, windows, prior = seed(packet, report(), "00000000-0000-0000-0000-000000000001", store)
    assert prior == old["id"] and windows
    from uuid import UUID, uuid5
    from workbench import everyday

    root = Path(everyday.configuration(store).evidence_roots[0])
    path = (
        root
        / "artifacts/runs"
        / ("planning-evidence-" + str(uuid5(UUID(prior), "committed-source-turn-2")))
        / "proposed-plan.json"
    )
    assert path.exists()
    if fault == "hash":
        path.write_text(path.read_text() + " ")
    if fault == "mode":
        path.chmod(0o644)
    if fault == "missing":
        path.unlink()
    if fault == "symlink":
        target = path.with_name("moved.json")
        path.rename(target)
        path.symlink_to(target)
    if fault in {"partial", "unknown", "success"}:
        with store.begin() as c:
            c.execute(
                update(model_attempts)
                .where(model_attempts.c.id == prior)
                .values(
                    outcome={
                        "partial": "incomplete_output",
                        "unknown": "interrupted_unknown",
                        "success": "completed",
                    }[fault]
                )
            )
    assert draft_for_critique(prior, store, packet, report(), "") is None


@pytest.mark.usefixtures("installed")
def test_retained_complete_critique_starts_fresh_construction_without_rejected_code(
    client, headers, store, monkeypatch
):
    old_failed(client, headers, store, monkeypatch)
    fresh = copy.deepcopy(program_answer(False))
    fresh["plan"]["reproducer"]["phases"][0]["code"] = "return subject(string.upper('alice'))"
    calls = scripted(monkeypatch, [fresh, review()])
    item, attempt = run(client, headers, store)
    assert item["status"] == "completed" and len(calls) == 2
    assert calls[0]["investigation_progress"]["phase"] == "plan_construction"
    assert calls[0]["fresh_proposal_required"]
    assert "previous_candidate" not in calls[0]["self_review"]
    assert "candidate_plan" not in calls[0]
    assert not item["decisions"] and not item["tasks"]
    assert attempt["usage"]["requests_observed"] == 2


@pytest.mark.usefixtures("installed")
def test_read_list_or_wording_changes_do_not_repeat_rejected_behavioral_review(
    client, headers, store, monkeypatch
):
    enable(monkeypatch)
    from test_bug_investigation_stage import diagnosis

    changed = copy.deepcopy(program_answer(False))
    changed["finding"] += " More surrounding words."
    changed["plan"]["scope"] += " A reworded description."
    changed["plan"]["reproducer"]["description"] += " Changed prose."
    changed["plan"]["reproducer"]["phases"][0]["description"] += " Changed prose."
    changed["plan"]["other_reads"] = [PRODUCTION]
    calls = scripted(monkeypatch, [diagnosis(), program_answer(False), review(False), changed])
    item, attempt = run(client, headers, store)
    assert item["status"] == "failed" and len(calls) == 4 and not item["proposals"]
    assert attempt["usage"]["requests_observed"] == 4
    assert "previous_candidate" not in calls[3]["self_review"]


def test_behavior_binding_keeps_program_setup_and_expectations_but_ignores_prose_reads():
    from workbench.bug_plan_review import behavior_binding, feedback, ReviewResponse
    from workbench.bug_program import compile as compile_program
    from workbench.visual_scope import digest
    from test_bug_program import wire, SOURCE, TEST

    value, _ = compile_program(
        wire(), [dict(path=SOURCE, top_level_returns_value=True), dict(path=TEST)], {}
    )
    key = behavior_binding(value)
    changed = copy.deepcopy(value)
    changed["plan"]["scope"] = "A different summary."
    changed["plan"]["read_paths"].append("!!!TwichUI/unrelated.lua")
    changed["plan"]["reproducer"]["description"] = "More prose."
    assert behavior_binding(changed) == key
    changed["plan"]["reproducer"]["phases"][0]["code"] = "return subject('other')"
    assert behavior_binding(changed) != key
    changed = copy.deepcopy(value)
    changed["plan"]["reproducer"]["observations"][0]["expected"] = "other"
    assert behavior_binding(changed) != key
    review = ReviewResponse(
        review_binding=digest(value),
        ready=False,
        issues=[
            dict(
                category="observation",
                explanation="The observation does not use the actual result.",
            )
        ],
    )
    result = feedback(dict(coverage=dict(reproducer_format="lua-phases-v1")), value, review)
    assert (
        "previous_candidate" not in result["self_review"]
        and result["self_review"]["rejected_behavior_sha256"] == key
    )
    assert (
        value
        == compile_program(
            wire(), [dict(path=SOURCE, top_level_returns_value=True), dict(path=TEST)], {}
        )[0]
    )


@pytest.mark.usefixtures("installed")
def test_verified_previous_rejection_blocks_same_behavior_before_another_review(
    client, headers, store, monkeypatch
):
    old = old_failed(client, headers, store, monkeypatch)
    from workbench.investigation_memory import review_lessons

    lesson = review_lessons(old["id"], store)
    assert lesson["rejected_behavior_sha256"]
    unchanged = copy.deepcopy(program_answer(False))
    unchanged["plan"]["other_reads"] = [PRODUCTION]
    unchanged["finding"] += " New prose without a behavioral correction."
    calls = scripted(monkeypatch, [unchanged])
    item, attempt = run(client, headers, store)
    assert item["status"] == "failed" and not item["proposals"] and len(calls) == 1
    assert attempt["usage"]["requests_observed"] == 1
    assert "previous_candidate" not in calls[0]["self_review"]
