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
    fresh = copy.deepcopy(program_answer(False))
    fresh["finding"] += " A complete fresh source-bound proposal."
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
