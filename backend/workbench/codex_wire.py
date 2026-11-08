"""Explicit read/search tags on the provider wire; unchanged local semantic schemas."""

import copy
from workbench.planning_response import ResponseValidationError

NOTE = (
    "Return complete compact JSON, closing every array and object; do not pad with whitespace. "
    "Every inspection item uses explicit kind read or search, and ALL five fields kind/path/start/end/query. "
    "For read, query is null; for search, path/start/end are null. "
    "These tags change formatting only, never source permission, task scope or required content."
)


def schema(value):
    result = copy.deepcopy(value)
    definitions = result.get("$defs", {})
    if not {"InvestigationRead", "InvestigationSearch"} <= set(definitions):
        return result, False
    read, search = (
        definitions["InvestigationRead"]["properties"],
        definitions["InvestigationSearch"]["properties"],
    )
    changed = False

    def replace(node):
        nonlocal changed
        if isinstance(node, list):
            return [replace(item) for item in node]
        if not isinstance(node, dict):
            return node
        if set(node) == {"anyOf"} and node["anyOf"] == [
            {"$ref": "#/$defs/InvestigationRead"},
            {"$ref": "#/$defs/InvestigationSearch"},
        ]:
            changed = True
            return {"$ref": "#/$defs/CodexInspection"}
        return {key: replace(item) for key, item in node.items()}

    result = replace(result)
    if changed:
        props = dict(kind=dict(type="string", enum=["read", "search"]))
        for key, shape in [
            ("path", read["path"]),
            ("start", read["start"]),
            ("end", read["end"]),
            ("query", search["query"]),
        ]:
            props[key] = dict(anyOf=[copy.deepcopy(shape), dict(type="null")])
        result["$defs"]["CodexInspection"] = dict(
            type="object", properties=props, required=list(props), additionalProperties=False
        )
    return result, changed


def normalize(value):
    if not isinstance(value, dict):
        return value, []
    target = value.get("draft", value)
    if not isinstance(target, dict) or not isinstance(target.get("inspection"), list):
        return value, []
    result = copy.deepcopy(value)
    target = result.get("draft", result)
    changes = []
    for index, item in enumerate(target["inspection"]):
        if not isinstance(item, dict) or "kind" not in item:
            continue  # Existing canonical shape still receives the original strict validation.
        valid = set(item) == {"kind", "path", "start", "end", "query"}
        if valid and item["kind"] == "read" and item["query"] is None:
            converted = {key: item[key] for key in ("path", "start", "end")}
        elif (
            valid
            and item["kind"] == "search"
            and all(item[k] is None for k in ("path", "start", "end"))
        ):
            converted = dict(query=item["query"])
        else:
            raise ResponseValidationError(
                "Ambiguous inspection wire fields.",
                ["inspection", index],
                "one explicit read or search, with unused fields null",
                item,
            )
        target["inspection"][index] = converted
        changes.append("explicit-inspection-" + item["kind"])
    return result, changes
