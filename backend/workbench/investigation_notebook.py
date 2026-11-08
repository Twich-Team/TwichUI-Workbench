"""Completed literal searches survive stateless requests; never remember authority."""

import copy

from workbench.visual_scope import digest

MAX_SEARCHES = 24
WIRE_BYTES = 12000


def record(packet, query, matches, paths):
    scope = sorted(paths) if paths is not None else None
    entry = dict(
        query=query,
        paths=scope,
        matches=copy.deepcopy(matches),
        complete_scan=len(matches) < 12,
        match_limit=12,
    )
    history = packet.setdefault("inspection_searches", [])
    key = (query.casefold(), digest(scope))
    prior = next((e for e in history if (e["query"].casefold(), digest(e["paths"])) == key), None)
    if prior:
        if {k: v for k, v in prior.items() if k != "query"} != {
            k: v for k, v in entry.items() if k != "query"
        }:
            raise ValueError("Committed search results changed")
        return
    if len(history) >= MAX_SEARCHES:
        raise ValueError("Search history exceeds the investigation bound")
    history.append(entry)


def wire(packet):
    import json

    scopes, searches = {}, []
    for item in packet.get("inspection_searches", []):
        identity = digest(item["paths"])[:16]
        scopes[identity] = dict(
            paths=item["paths"],
            description="these selected committed files only"
            if item["paths"] is not None
            else "the bounded committed project Lua inventory",
        )
        searches.append(
            dict(
                query=item["query"],
                scope=identity,
                match_count=len(item["matches"]),
                complete_scan=item["complete_scan"],
                match_limit=item["match_limit"],
                matches=[
                    {k: m[k] for k in ("path", "line", "truncated") if k in m}
                    for m in item["matches"]
                ],
            )
        )
    result = dict(
        base=packet["base"],
        read_only=True,
        scopes=scopes,
        searches=searches,
        note="Completed exact literal searches, including no matches, remain evidence. "
        "No matches means none in the stated scope; never infer project-wide absence from a selected-file search. "
        "A limited scan is not exhaustive. Reuse these results instead of repeating the same query. "
        "These facts grant no approval or implementation authority.",
    )
    if len(json.dumps(result).encode()) > WIRE_BYTES:
        # Keep every query/scope/count/negative fact. Positive text can be read
        # from its exact committed coordinates without inventing absent content.
        for item in searches:
            item["omitted_match_coordinates"] = max(0, len(item["matches"]) - 2)
            item["matches"] = item["matches"][:2]
        result["positive_coordinates_limited"] = True
    if len(json.dumps(result).encode()) > WIRE_BYTES:
        raise ValueError("The bounded search notebook cannot fit this request")
    return result


def verify(initial, packet, search):
    history = packet.get("inspection_searches", [])
    if not isinstance(history, list) or len(history) > MAX_SEARCHES:
        raise ValueError("Invalid committed search notebook")
    seen = set()
    for item in history:
        if (
            not isinstance(item, dict)
            or set(item) != {"query", "paths", "matches", "complete_scan", "match_limit"}
            or not isinstance(item["query"], str)
            or not 3 <= len(item["query"].strip()) <= 80
            or "\n" in item["query"]
            or "\r" in item["query"]
            or item["paths"] is not None
            and (
                not isinstance(item["paths"], list)
                or not 1 <= len(item["paths"]) <= 12
                or not all(isinstance(p, str) for p in item["paths"])
                or item["paths"] != sorted(set(item["paths"]))
            )
        ):
            raise ValueError("Invalid committed search record")
        key = (item["query"].casefold(), digest(item["paths"]))
        if key in seen:
            raise ValueError("Duplicate committed search record")
        seen.add(key)
        found = search(initial["base"], item["query"], item["paths"])
        if (
            item["matches"] != found
            or item["match_limit"] != 12
            or type(item["complete_scan"]) is not bool
            or item["complete_scan"] != (len(found) < 12)
        ):
            raise ValueError("Committed search evidence changed")
