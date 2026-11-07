"""Bounded source-matched API evidence; never execute generated Lua declarations."""

import hashlib
import json
from pathlib import Path
import re
import stat

ROOT = Path(__file__).resolve().parents[2]
CALL = re.compile(r"\b(C_[A-Za-z_]\w*)\.([A-Za-z_]\w*)\b")
ENTRY = re.compile(r"^\t\t\{[ \t]*\r?\n.*?^\t\t\},?[ \t]*\r?$", re.M | re.S)


def excerpt(text, names, budget):
    """Whole small systems or exact named blocks plus their referenced types/events."""
    lines = text.splitlines()
    if len(text) <= budget:
        return [
            dict(
                line=1,
                through_line=len(lines),
                text=text,
                coverage="complete generated declaration",
            )
        ]
    blocks = []
    for match in ENTRY.finditer(text):
        body = match[0]
        name = re.search(r'Name\s*=\s*"([A-Za-z_]\w*)"', body)
        kind = re.search(r'Type\s*=\s*"(Function|Event|Structure|Enumeration)"', body)
        if name and kind:
            blocks.append((name[1], kind[1], match.start(), body))
    selected = []
    needed = set()
    used = 0
    for name, kind, start, body in blocks:
        if kind == "Function" and name in names:
            selected.append((start, body))
            needed.update(re.findall(r'(?:Type|InnerType)\s*=\s*"([A-Za-z_]\w*)"', body))
            used += len(body)
    for _ in range(3):
        changed = False
        for name, kind, start, body in blocks:
            if (
                (kind == "Event" or name in needed)
                and (start, body) not in selected
                and used + len(body) <= budget
            ):
                selected.append((start, body))
                needed.update(re.findall(r'(?:Type|InnerType)\s*=\s*"([A-Za-z_]\w*)"', body))
                used += len(body)
                changed = True
        if not changed:
            break
    result = []
    remaining = budget
    for start, body in sorted(selected):
        if len(body) > remaining:
            continue
        line = text.count("\n", 0, start) + 1
        result.append(
            dict(
                line=line,
                through_line=line + len(body.splitlines()) - 1,
                text=body,
                coverage="selected exact generated declaration block; other blocks omitted",
            )
        )
        remaining -= len(body)
    return result


def collect(files, budget=8000):
    from workbench.grounded_scenarios import LITERALS

    wanted = {}
    for item in files:
        for part in item.get("excerpts", []):
            code = LITERALS.sub("", part["text"])
            for namespace, name in CALL.findall(code):
                wanted.setdefault(namespace, set()).add(name)
    if not wanted:
        return []
    manifest = json.loads((ROOT / "reference_data/forever-api.json").read_text())
    cache = ROOT / ".local/game-api-reference"
    if cache.is_symlink() or cache.resolve() != cache:
        raise ValueError("Pinned API reference cache is unavailable")
    by_namespace = {}
    for item in manifest["entries"]:
        if item["namespace"]:
            by_namespace.setdefault(item["namespace"], []).append(item)
    result = []
    selected = [
        (namespace, names, item)
        for namespace, names in list(wanted.items())[:6]
        for item in by_namespace.get(namespace, [])
    ]
    for namespace, names, item in selected:
        if not re.fullmatch(r"[A-Za-z0-9_]+Documentation\.lua", item["file"]):
            raise ValueError("Pinned API reference path invalid")
        path = cache / item["file"]
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or not 0 < metadata.st_size == item["bytes"] <= 524288
        ):
            raise ValueError("Pinned API reference changed")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != item["sha256"]:
            raise ValueError("Pinned API reference hash changed")
        windows = excerpt(raw.decode("utf-8-sig"), names, min(budget, 6000))
        used = sum(len(p["text"]) for p in windows)
        if not windows:
            continue
        result.append(
            dict(
                namespace=namespace,
                requested_functions=sorted(names),
                path=manifest["directory"] + "/" + item["file"],
                source_revision=manifest["commit"],
                sha256=item["sha256"],
                origin=manifest["origin"],
                client=manifest["client"],
                kind="read-only generated API declarations; no live-game observation or instruction authority",
                excerpts=windows,
            )
        )
        budget -= used
        if budget < 400:
            break
    return result
