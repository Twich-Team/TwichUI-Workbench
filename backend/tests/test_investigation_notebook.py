"""Completed search facts, including negatives, survive a later line read."""

import copy
import pytest

from workbench import investigation_notebook as notebook, worker
from test_bug_source_planning import scripted, inspection
from test_bug_workflow import enable_bugs, report, response
from test_everyday import installed  # noqa: F401


@pytest.mark.usefixtures("installed")
def test_normal_workflow_keeps_negative_search_after_a_source_read(
    client, headers, store, monkeypatch
):
    enable_bugs(monkeypatch)
    query = "NeverPresentEventAABBCC"

    def final(packet, schema):
        facts = packet["completed_searches"]
        assert facts["read_only"] and len(facts["searches"]) == 1
        assert facts["searches"][0]["query"] == query and facts["searches"][0]["match_count"] == 0
        assert facts["searches"][0]["complete_scan"] is True
        assert packet["inspection_result"]["visible_requested_windows"][0]["provided"]
        return response()

    calls = scripted(
        monkeypatch,
        [
            dict(
                finding="Search for the relevant existing event.",
                plan=None,
                question=None,
                limitation=None,
                inspection=[dict(query=query)],
            ),
            inspection(),
            final,
        ],
    )
    identity = client.post("/api/intake", headers=headers, json=report()).json()["id"]
    assert worker.tick(store)
    assert client.get("/api/intake/" + identity).json()["status"] == "completed"
    assert len(calls) == 3


def test_search_scope_and_negatives_are_not_global_absence_or_authority():
    packet = dict(base="fixed")
    notebook.record(packet, "MISSING", [], {"!!!TwichUI/a.lua"})
    notebook.record(packet, "missing", [], {"!!!TwichUI/a.lua"})
    value = notebook.wire(packet)
    assert len(value["searches"]) == 1 and value["searches"][0]["match_count"] == 0
    assert next(iter(value["scopes"].values()))["paths"] == ["!!!TwichUI/a.lua"]
    assert value["read_only"] and "never infer project-wide absence" in value["note"]
    notebook.verify(dict(base="fixed"), packet, lambda base, query, paths: [])


@pytest.mark.parametrize(
    "fault", ["changed_result", "changed_complete", "wrong_scope", "duplicate"]
)
def test_search_evidence_is_recomputed_before_a_plan_can_use_it(fault):
    packet = dict(base="fixed")
    notebook.record(packet, "needle", [], ["!!!TwichUI/a.lua"])
    entry = packet["inspection_searches"][0]
    if fault == "changed_result":
        entry["matches"] = [dict(path="!!!TwichUI/a.lua", line=1, text="invented")]
    elif fault == "changed_complete":
        entry["complete_scan"] = False
    elif fault == "wrong_scope":
        entry["paths"] = []
    else:
        packet["inspection_searches"].append(copy.deepcopy(entry))
    with pytest.raises(ValueError):
        notebook.verify(dict(base="fixed"), packet, lambda *args: [])


def test_limited_search_does_not_claim_exhaustive_absence_or_resend_source_text():
    packet = dict(base="fixed")
    notebook.record(
        packet,
        "needle",
        [dict(path="!!!TwichUI/a.lua", line=i, text="PRIVATE MATCH") for i in range(1, 13)],
        None,
    )
    facts = notebook.wire(packet)["searches"][0]
    assert facts["complete_scan"] is False and facts["match_count"] == 12
    assert all("text" not in match for match in facts["matches"])


@pytest.mark.usefixtures("installed")
def test_repeating_only_completed_visible_searches_stops_without_an_unchanged_loop(
    client, headers, store, monkeypatch
):
    enable_bugs(monkeypatch)
    reply = dict(
        finding="Check a literal event name.",
        plan=None,
        question=None,
        limitation=None,
        inspection=[dict(query="NeverPresentEventAABBCC")],
    )
    calls = scripted(monkeypatch, [reply, copy.deepcopy(reply)])
    identity = client.post("/api/intake", headers=headers, json=report()).json()["id"]
    assert worker.tick(store)
    item = client.get("/api/intake/" + identity).json()
    assert item["status"] == "failed" and len(calls) == 2 and not item["proposals"]
    assert item["jobs"][0]["diagnostics"]["category"] == "inspection_unchanged"
