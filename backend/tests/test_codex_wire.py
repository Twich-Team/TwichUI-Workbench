"""Tagged inspection changes wire formatting, not source or semantic authority."""

import pytest
from workbench.codex_wire import schema, normalize
from workbench.bug_investigation_stage import InvestigationResponse
from workbench.planning_response import ResponseValidationError


def test_read_search_union_is_one_explicit_tagged_object_without_weakening_local_validation():
    original = InvestigationResponse.model_json_schema()
    wire, changed = schema(original)
    assert changed and original == InvestigationResponse.model_json_schema()
    fields = wire["$defs"]["CodexInspection"]
    assert (
        set(fields["required"]) == {"kind", "path", "start", "end", "query"}
        and not fields["additionalProperties"]
    )
    item = dict(kind="read", path="!!!TwichUI/sample.lua", start=30, end=48, query=None)
    raw = dict(finding="Inspect the existing committed operation.", draft=dict(inspection=[item]))
    mapped, changes = normalize(raw)
    parsed = InvestigationResponse.model_validate(mapped)
    assert parsed.draft.inspection[0].path == item["path"] and parsed.draft.inspection[0].end == 48
    assert raw["draft"]["inspection"][0] == item and changes == ["explicit-inspection-read"]
    search = dict(kind="search", path=None, start=None, end=None, query="literal needle")
    normalized, _ = normalize(dict(finding=raw["finding"], draft=dict(inspection=[search])))
    assert (
        InvestigationResponse.model_validate(normalized).draft.inspection[0].query
        == "literal needle"
    )


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
