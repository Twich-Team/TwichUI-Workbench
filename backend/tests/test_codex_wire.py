"""Tagged inspection changes wire formatting, not source or semantic authority."""

import pytest
from workbench.codex_wire import schema, normalize
from workbench.bug_investigation_stage import InvestigationResponse
from workbench.planning_response import ResponseValidationError


def test_flat_investigation_has_no_draft_union_and_keeps_strict_local_semantics():
    original = InvestigationResponse.model_json_schema()
    wire, changed = schema(original)
    assert changed and original == InvestigationResponse.model_json_schema()
    assert "draft" not in wire["properties"] and "action" in wire["required"]
    assert set(wire["required"]) == set(wire["properties"]) and not wire["additionalProperties"]
    value = dict(
        finding="Read the exact committed source before diagnosis.",
        action="inspect",
        reads=[dict(path="!!!TwichUI/example.lua", start=30, end=48)],
        searches=[dict(query="needle")],
        diagnosis=None,
        question=None,
        limitation=None,
    )
    mapped, changes = normalize(value)
    parsed = InvestigationResponse.model_validate(mapped)
    assert len(parsed.draft.inspection) == 2 and parsed.draft.inspection[0].end == 48
    assert parsed.draft.inspection[1].query == "needle" and changes == [
        "flat-investigation-inspect"
    ]
    assert value["action"] == "inspect" and value["diagnosis"] is None


@pytest.mark.parametrize("action", ["diagnose", "question", "limitation"])
def test_flat_single_outcome_preserves_supplied_meaning(action):
    value = dict(
        finding="This is a controlled source diagnosis.",
        action=action,
        reads=[],
        searches=[],
        diagnosis=None,
        question=None,
        limitation=None,
    )
    field = "diagnosis" if action == "diagnose" else action
    value[field] = (
        dict(
            cause="The inspected function omits the requested operation.",
            evidence_paths=["!!!TwichUI/example.lua"],
        )
        if action == "diagnose"
        else "A specific controlled fact or boundary."
    )
    mapped, _ = normalize(value)
    assert mapped["draft"] == {field: value[field]}
    InvestigationResponse.model_validate(mapped)


@pytest.mark.parametrize("fault", ["missing", "extra", "contrary", "unknown", "bad_action_type"])
def test_flat_transport_never_fills_missing_or_ambiguous_content(fault):
    value = dict(
        finding="Inspect source safely.",
        action="inspect",
        reads=[],
        searches=[dict(query="needle")],
        diagnosis=None,
        question=None,
        limitation=None,
    )
    if fault == "missing":
        value.pop("reads")
    elif fault == "extra":
        value["approved"] = True
    elif fault == "contrary":
        value["question"] = "A conflicting second outcome."
    elif fault == "unknown":
        value["action"] = "execute"
    else:
        value["action"] = {}
    with pytest.raises(ResponseValidationError):
        normalize(value)


@pytest.mark.parametrize(
    "fault", ["unused_query", "unused_path", "missing_field", "unknown_kind", "extra_field"]
)
def test_tagged_wire_cannot_hide_conflicting_or_invent_missing_content(fault):
    item = dict(kind="read", path="!!!TwichUI/sample.lua", start=30, end=48, query=None)
    if fault == "unused_query":
        item["query"] = "conflicting request"
    if fault == "unused_path":
        item.update(kind="search", query="needle")
    if fault == "missing_field":
        item.pop("end")
    if fault == "unknown_kind":
        item["kind"] = "execute"
    if fault == "extra_field":
        item["approved"] = True
    with pytest.raises(ResponseValidationError):
        normalize(dict(inspection=[item]))


def test_original_canonical_inspection_remains_semantically_identical():
    value = dict(finding="Read committed code.", draft=dict(inspection=[dict(query="literal")]))
    result, changes = normalize(value)
    assert result == value and not changes
    InvestigationResponse.model_validate(result)
