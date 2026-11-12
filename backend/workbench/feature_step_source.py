"""Bounded committed-source reads for feature scopes, including checked inputs.

The app owns fixed Git reads. Model data cannot choose a command, external URL,
working tree, revision or source outside eligible first-party Lua/test files.
"""

import copy
import json

from workbench import feature_steps, regression_preparation
from workbench.bug_evidence import InvestigationRead, InvestigationSearch
from workbench.visual_scope import digest


def index(root, base):
    entries = []
    for entry in regression_preparation.git(
        root,
        "ls-tree",
        "-rz",
        "-l",
        base,
        "--",
        "!!!TwichUI",
        "TwichUI_Settings",
        "tests",
        max_bytes=1 << 20,
    ).split(b"\0"):
        if not entry:
            continue
        meta, name = entry.split(b"\t", 1)
        mode, kind, oid, size = meta.decode().split()
        path = name.decode()
        if mode not in {"100644", "100755"} or kind != "blob" or int(size) > 131072:
            continue
        if not feature_steps.eligible_path(path):
            continue
        entries.append(dict(path=path, bytes=int(size)))
    return entries


def _append(packet, root, base, path, start, end, allowance):
    metadata, raw = feature_steps._file(root, base, path)
    if len(raw) > 131072:
        raise ValueError("A requested feature source file exceeds its read boundary.")
    lines = raw.decode().splitlines()
    if not 1 <= start <= end or start > len(lines):
        raise ValueError("Requested source range is outside the committed file.")
    selected = []
    end = min(end, start + 79, len(lines))
    used = 0
    for line in lines[start - 1 : end]:
        if used + len(line) + 1 > allowance:
            break
        selected.append(line)
        used += len(line) + 1
    if not selected:
        raise ValueError(
            "A source line exceeds the bounded inspection response. Search another location."
        )
    text = "\n".join(selected)
    last = start + len(selected) - 1
    window = dict(
        path=path,
        start=start,
        end=last,
        text=text,
        sha256=metadata["sha256"],
        git_blob=metadata["git_blob"],
        total_lines=len(lines),
        next_line=last + 1 if last < len(lines) else None,
    )
    identity = digest({k: window[k] for k in ("path", "start", "end", "sha256")})
    if any(
        digest({k: w[k] for k in ("path", "start", "end", "sha256")}) == identity
        for w in packet.get("source_windows", [])
    ):
        raise ValueError(
            "This exact source window was already inspected. Read its next lines or choose another search."
        )
    file = next((f for f in packet["files"] if f["path"] == path), None)
    if file is None:
        if len(packet["files"]) >= 12:
            raise ValueError("Feature scope inspection already has twelve source files.")
        file = dict(path=path, sha256=metadata["sha256"], excerpts=[])
        packet["files"].append(file)
    if file["sha256"] != metadata["sha256"]:
        raise ValueError("A checked predecessor source file changed during inspection.")
    file["excerpts"].append(dict(line=start, text=text))
    packet.setdefault("source_windows", []).append(window)
    return window, used


def inspect(packet, root, base, requests):
    if not 1 <= len(requests) <= 3:
        raise ValueError("Use one to three bounded committed-source reads/searches.")
    value = copy.deepcopy(packet)
    out = []
    left = 6500
    inventory = index(root, base)
    for position, raw in enumerate(requests):
        if "query" not in raw:
            request = InvestigationRead.model_validate(raw)
            window, used = _append(
                value,
                root,
                base,
                request.path,
                request.start,
                request.end,
                max(1, left // (len(requests) - position)),
            )
            out.append(dict(kind="read", **window))
            left -= used
        else:
            request = InvestigationSearch.model_validate(raw)
            matches = []
            scanned = 0
            for item in inventory:
                path = item["path"]
                if (
                    request.path
                    and path != request.path
                    and not path.startswith(request.path + "/")
                ):
                    continue
                metadata, data = feature_steps._file(root, base, path)
                scanned += len(data)
                if scanned > 16 << 20:
                    raise ValueError("The bounded first-party source search scan is full.")
                for number, line in enumerate(data.decode().splitlines(), 1):
                    if request.query.casefold() in line.casefold():
                        matches.append(
                            dict(path=path, line=number, text=line[:240], sha256=metadata["sha256"])
                        )
                        if len(matches) >= 8:
                            break
                if len(matches) >= 8:
                    break
            prior = value.get("source_searches", [])
            if any(p["query"].casefold() == request.query.casefold() and p["path"] == request.path for p in prior):
                raise ValueError(
                    "This source search was already returned. Change the query or inspect an actual matching path."
                )
            record = dict(kind="search", query=request.query, path=request.path, matches=matches)
            value.setdefault("source_searches", []).append(record)
            out.append(record)
    value["source_inspection_results"] = out
    if len(json.dumps(value, ensure_ascii=False).encode()) > 131072:
        raise ValueError("Feature source inspection reached its packet boundary.")
    return value, out


def restore(initial, root, base, records):
    value = copy.deepcopy(initial)
    for record in records:
        value, observations = inspect(value, root, base, record["requests"])
        if digest(observations) != record["result_binding"]:
            raise ValueError(
                "Previously inspected committed source changed. No model request started."
            )
    return value
