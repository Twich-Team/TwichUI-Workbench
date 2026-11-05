"""Bounded Lua recipe data, registered by trusted preparation, never model input."""

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re


def validate(spec):
    from workbench.task_budget import valid_contract

    if not isinstance(spec, dict) or not valid_contract(spec.get("contract", {})):
        raise ValueError("Unsupported bounded Lua contract")
    if spec.get('read_budget_policy', 'repeated-v1') not in ('repeated-v1', 'distinct-lines-v1'):
        raise ValueError('Unknown bounded source read policy')
    paths = spec.get("read_paths", spec["contract"]["paths"])
    if (
        not isinstance(paths, list)
        or not 1 <= len(paths) <= 8
        or not set(spec["contract"]["paths"]).issubset(paths)
        or any(
            not isinstance(p, str)
            or not re.fullmatch(r"[A-Za-z0-9_!./-]{1,240}", p)
            or PurePosixPath(p).is_absolute()
            or ".." in PurePosixPath(p).parts
            or not p.endswith(".lua")
            or not p.startswith(("!!!TwichUI/", "TwichUI_Settings/", "tests/test_"))
            for p in paths
        )
        or spec.get("focused_test") not in paths
        or not spec["focused_test"].startswith("tests/test_")
        or not re.fullmatch("[0-9a-f]{64}", spec.get("oracle_sha256", ""))
        or not isinstance(spec.get("instruction"), str)
        or not 1 <= len(spec["instruction"]) <= 12000
        or spec.get("base_branch") != "main"
        or spec.get("draft") is not True
    ):
        raise ValueError("Invalid fixed recipe paths, oracle or instruction")
    reads = spec.get("initial_reads")
    if (
        not isinstance(reads, list)
        or len(reads) != 4
        or any(
            not isinstance(r, dict)
            or r.get("path") not in paths
            or type(r.get("start")) is not int
            or type(r.get("end")) is not int
            or not 1 <= r["start"] <= r["end"] <= r["start"] + 249
            for r in reads
        )
    ):
        raise ValueError("Exactly four bounded initial windows required")
    identity = spec.get("identity")
    if (
        not isinstance(identity, dict)
        or set(identity) != {"name", "email"}
        or any(
            not isinstance(v, str) or not 1 <= len(v) <= 160 or any(ord(c) < 32 for c in v)
            for v in identity.values()
        )
    ):
        raise ValueError("Bounded repository-local publication identity required")
    if spec.get("category", "Bug fix") not in (
        "Bug fix",
        "Compatibility",
        "Reliability",
        "Performance",
        "Usability / visual",
        "Feature",
        "Tests",
        "Maintenance",
    ):
        raise ValueError("Unknown task category")
    controls = spec.get("controls")
    if (
        not isinstance(controls, dict)
        or controls.get("original") != "fail"
        or controls.get("known-good-oracle-control") != "pass"
        or not 3 <= len(controls) <= 8
        or any(not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", key) for key in controls)
        or not any(
            value == "fail"
            for key, value in controls.items()
            if key not in ("original", "known-good-oracle-control")
        )
        or any(v not in ("pass", "fail") for v in controls.values())
    ):
        raise ValueError("Reproduction, positive and negative controls required")
    if len(json.dumps(spec, allow_nan=False).encode()) > 65536:
        raise ValueError("Recipe size limit")
    return spec


def load(root):
    path = Path(root) / "recipe.json"
    if not path.exists():
        return None
    if path.is_symlink() or path.stat().st_uid != os.getuid() or path.stat().st_size > 65536:
        raise ValueError("Owned bounded fixed recipe required")
    spec = validate(json.loads(path.read_text()))
    oracle = Path(root) / "recipe-oracle.lua"
    if (
        oracle.is_symlink()
        or not oracle.is_file()
        or oracle.stat().st_uid != os.getuid()
        or oracle.stat().st_size > 65536
        or hashlib.sha256(oracle.read_bytes()).hexdigest() != spec["oracle_sha256"]
    ):
        raise ValueError("Independent recipe oracle identity mismatch")
    return spec
