"""Working notes and navigation remain bounded data, never authority."""

import copy
from uuid import uuid4

import pytest

from workbench import investigation_strategy as strategy
from workbench.bug_evidence import InvestigationSearch
from workbench.bug_source_planning import search_scope, inspect
from workbench.planning_transport import PlanningUnavailable


def test_notes_are_attributed_deduplicated_bounded_and_survive_a_copy():
    packet = {}
    for n in range(6):
        strategy.remember(
            packet, f"Observed source fragment {n}; cause remains a hypothesis.", str(uuid4())
        )
    assert len(strategy.validate(packet)) == 4
    last = packet["investigation_notes"][-1]["finding"]
    strategy.remember(packet, last, str(uuid4()))
    assert len(strategy.validate(packet)) == 4
    assert strategy.validate(copy.deepcopy(packet)) == packet["investigation_notes"]
    packet["investigation_notes"][0]["approved"] = True
    with pytest.raises(ValueError):
        strategy.validate(packet)


def test_notes_cannot_follow_source_cache_into_a_different_report():
    first = dict(
        title="First report", description="First private observation", request_id=str(uuid4())
    )
    other = dict(title="Other report", description="Other observation", request_id=str(uuid4()))
    packet = dict(files=[], inspection_focus=[])
    strategy.remember(packet, "Prior unverified finding", str(uuid4()), first)
    directory = dict(directories={})
    assert strategy.context(packet, directory, first)["notes"]
    assert not strategy.context(packet, directory, other)["notes"]
    strategy.remember(packet, "Different unverified finding", str(uuid4()), other)
    assert len(strategy.validate(packet)) == 1
    assert not strategy.context(packet, directory, first)["notes"]


@pytest.mark.parametrize(
    "value",
    [
        ["raw unbounded note"],
        [dict(phase=str(uuid4()), finding="x" * 801)],
        [dict(phase="not-a-provider-identity", finding="unverified")],
    ],
)
def test_invalid_notes_cannot_enter_a_source_cache(value):
    with pytest.raises(ValueError):
        strategy.validate(dict(investigation_notes=value))


def test_generic_discovery_suggests_unread_related_ui_and_tests_without_reading_or_granting():
    packet = dict(
        files=[dict(path="!!!TwichUI/alerts/Store.lua")],
        inspection_focus=["!!!TwichUI/alerts/Store.lua"],
    )
    directory = dict(
        directories={
            "TwichUI_Settings/pages": ["Alerts.lua", "Fishing.lua"],
            "tests": ["test_alerts.lua"],
            "!!!TwichUI/alerts": ["Store.lua", "Filter.lua"],
        }
    )
    view = strategy.context(
        packet,
        directory,
        dict(title="Missing alert labels", description="An alert label is wrong."),
    )
    assert view["unread_related_paths"][0] == "TwichUI_Settings/pages/Alerts.lua"
    assert "tests/test_alerts.lua" in view["unread_related_paths"]
    assert "Fishing.lua" not in str(view) and "not established facts" in view["note"]
    assert packet["files"] == [dict(path="!!!TwichUI/alerts/Store.lua")]


@pytest.mark.parametrize(
    "path", ["../private", "/home/rich", "tests/../private.lua", "!!!TwichUI/libs/Other.lua"]
)
def test_scoped_search_cannot_escape_project_rules(path):
    with pytest.raises(ValueError):
        InvestigationSearch(query="label", path=path)


def test_scoped_search_preserves_external_disclosure_intersection():
    permitted = {"TwichUI_Settings/pages/Alerts.lua", "!!!TwichUI/alerts/Store.lua"}
    assert search_scope(InvestigationSearch(query="label", path="TwichUI_Settings"), permitted) == {
        "TwichUI_Settings/pages/Alerts.lua"
    }
    with pytest.raises(PlanningUnavailable) as held:
        search_scope(InvestigationSearch(query="label", path="tests"), permitted)
    assert held.value.reason == "disclosure"
    with pytest.raises(PlanningUnavailable):
        search_scope(InvestigationSearch(query="label"), set())


def test_empty_search_scope_never_becomes_a_whole_repository_scan(monkeypatch):
    from workbench import bug_source_planning as source

    monkeypatch.setattr(
        source.source, "repository", lambda: pytest.fail("Empty permission scanned the project")
    )
    assert source.search("pinned", "label", set()) == []


def test_scoped_search_records_its_precise_scope_not_a_false_project_wide_absence(monkeypatch):
    from workbench import bug_source_planning as source

    calls = []
    monkeypatch.setattr(source, "search", lambda base, query, paths=None: calls.append(paths) or [])
    packet = dict(base="pinned", files=[])
    inspect(packet, [InvestigationSearch(query="label", path="TwichUI_Settings/pages")])
    assert calls == [{"TwichUI_Settings/pages"}]
    assert packet["inspection_searches"][0]["paths"] == ["TwichUI_Settings/pages"]


def test_read_only_schema_feedback_does_not_select_ambiguous_outcomes_or_copy_values():
    from pydantic import ValidationError
    from workbench.bug_intent import Response

    with pytest.raises(ValidationError) as failure:
        Response.model_validate(
            dict(finding="PRIVATE_SOURCE_VALUE", question="first", limitation="second")
        )
    feedback = strategy.correction(failure.value)
    assert feedback["rejected"] and not feedback["approved"] and not feedback["executed"]
    assert "PRIVATE_SOURCE_VALUE" not in str(feedback)
    assert "THREE source operations TOTAL" in feedback["note"]
