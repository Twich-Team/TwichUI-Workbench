"""Explicit read/search tags on the provider wire; unchanged local semantic schemas."""

import copy
from workbench.planning_response import ResponseValidationError

NOTE = (
    "Return complete compact JSON, closing every array and object; do not pad with whitespace. "
    "Every inspection item uses explicit kind read or search, and ALL five fields kind/path/start/end/query. "
    "For read, query is null; for search, path/start/end are null. "
    "These tags change formatting only, never source permission, task scope or required content."
)
FLAT_NOTE = (
    "Return complete compact JSON using the flat transport fields, not draft. "
    "action is inspect, diagnose, question, or limitation. For inspect, supply reads and searches "
    "(at most three combined); diagnosis, question and limitation are null. "
    "For the other actions reads/searches are empty, and only the selected value is non-null. "
    "The application maps this complete reply into its original strict schema. "
    "Do not pad with whitespace or invent missing content."
)


def schema(value):
    result = copy.deepcopy(value)
    definitions = result.get("$defs", {})
    if result.get("title") == "InvestigationResponse":
        props = dict(
            finding=result["properties"]["finding"],
            action=dict(type="string", enum=["inspect", "diagnose", "question", "limitation"]),
            reads=dict(type="array", items={"$ref": "#/$defs/InvestigationRead"}, maxItems=3),
            searches=dict(type="array", items={"$ref": "#/$defs/InvestigationSearch"}, maxItems=3),
            diagnosis=dict(anyOf=[{"$ref": "#/$defs/Diagnosis"}, dict(type="null")]),
            question=dict(
                anyOf=[
                    copy.deepcopy(definitions["Question"]["properties"]["question"]),
                    dict(type="null"),
                ]
            ),
            limitation=dict(
                anyOf=[
                    copy.deepcopy(definitions["Limitation"]["properties"]["limitation"]),
                    dict(type="null"),
                ]
            ),
        )
        return dict(
            title="InvestigationResponse",
            type="object",
            properties=props,
            required=list(props),
            additionalProperties=False,
            **{
                "$defs": {
                    k: definitions[k]
                    for k in ("InvestigationRead", "InvestigationSearch", "Diagnosis")
                }
            },
        ), True
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
    if "action" in value:
        fields = {"finding", "action", "reads", "searches", "diagnosis", "question", "limitation"}
        # Seven fields, with one explicit outcome. Empty collections are supplied
        # by the provider, never invented to complete a partial response.
        valid = (
            set(value) == fields
            and isinstance(value["action"], str)
            and isinstance(value["reads"], list)
            and isinstance(value["searches"], list)
        )
        action = value["action"]
        if valid and action == "inspect":
            valid = all(value[k] is None for k in ("diagnosis", "question", "limitation"))
            draft = dict(inspection=[*value["reads"], *value["searches"]])
        elif valid and action in {"diagnose", "question", "limitation"}:
            field = "diagnosis" if action == "diagnose" else action
            valid = (
                not value["reads"]
                and not value["searches"]
                and value[field] is not None
                and all(value[k] is None for k in {"diagnosis", "question", "limitation"} - {field})
            )
            draft = {field: value[field]}
        else:
            valid = False
        if not valid:
            raise ResponseValidationError(
                "Ambiguous investigation wire fields.",
                ["action"],
                "one explicit outcome with all unused fields null or empty",
                value,
            )
        return dict(finding=value["finding"], draft=draft), ["flat-investigation-" + action]
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
