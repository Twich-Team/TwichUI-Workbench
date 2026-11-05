"""Explicit opt-in NON-MODEL readiness, actual confined commands + scripted HTTP.

Never a model candidate or normal test side effect. Frozen historical fixture bytes
are used ONLY as negative/positive controls in an owned disposable workspace.
"""

import json
import os
from pathlib import Path
import shutil
import pytest
from workbench.real_workspace import Runtime, Tools, durable
from workbench.real_provider import implement
from test_real_transport import http_tape as http_tape, tool_response, final_response


def test_actual_failure_log_retrieval_and_repair(http_tape):
    raw = os.environ.get("WORKBENCH_ACTUAL_REPAIR_READINESS")
    if not raw:
        pytest.skip("Explicit owned disposable readiness invocation only; no live inference")
    root = Path(raw)
    root.mkdir()
    previous = Path(
        "/home/rich/TwichUI-Workbench/artifacts/runs/guild-roster-pr-1tpdchx0/preparation-v4"
    )
    template = json.loads((previous / "template.json").read_text())
    recipe = {
        **template["recipe"],
        "read_budget_policy": "distinct-lines-v1",
        "contract": {
            **template["recipe"]["contract"],
            "limits": {**template["recipe"]["contract"]["limits"], "read_chars": 98304},
        },
    }
    durable(root / "recipe.json", json.dumps(recipe, sort_keys=True).encode())
    durable(root / "recipe-oracle.lua", (previous / "oracle.lua").read_bytes())
    runtime = Runtime(root)
    runtime.prepare()
    runtime._request_id = "NON-MODEL-READINESS"
    runtime._fence = "NON-MODEL"
    runtime._context_sha = None
    tools = Tools(runtime, lambda: True)
    path = recipe["contract"]["paths"][0]
    failed = (
        Path(
            "/home/rich/TwichUI-Workbench/artifacts/runs/task-lease-guild-roster-complete-1tbeau4m-01/real-run/working-checks/00/files"
        )
        / path
    )
    original = (runtime.workspace / path).read_text()
    new = failed.read_text()
    begin = original.rfind("print((")
    needle = original[begin:]
    incomplete = new[len(original[:begin]) : -len(needle)]
    assert original[:begin] + incomplete + needle == new
    positive = (previous / "positive-control.fragment.lua").read_text()

    def after_failed(body):
        feedback = json.loads(body["messages"][-1]["content"])
        assert (
            feedback["status"] == "fail"
            and "FAILED: level 1 and MAGE kept" in feedback["diagnostic"]
        )
        assert feedback["context_hint"]["start"] == 916
        return tool_response(
            [("read_check_log", dict(check_id=feedback["check_id"], start=70, end=149))]
        )

    def repair(body):
        log = json.loads(body["messages"][-1]["content"])
        assert any("FAILED:" in x["text"] for x in log["lines"])
        return tool_response(
            [("replace_text", dict(path=path, old=incomplete, new=positive + "\n"))]
        )

    http_tape.steps = [
        tool_response(
            [("read_file", dict(path=path, start=i, end=i)) for i in (1, 2, 3, 4)]
            + [("find_text", dict(path=recipe["read_paths"][-1], query="FullName"))]
        ),
        tool_response([("replace_text", dict(path=path, old=needle, new=incomplete + needle))]),
        tool_response([("run_check", dict(name="candidate"))]),
        after_failed,
        repair,
        tool_response([("run_check", dict(name="candidate"))]),
        final_response(),
    ]
    try:
        usage = implement(
            tools,
            "NON-MODEL deterministic diagnostics/failure/repair readiness",
            "",
            "pilot-qwen38-27b",
            lambda: True,
            ownership=http_tape.adapter,
            on_finish=lambda: runtime.candidate_feedback(lambda: True),
        )
        assert runtime.candidate_feedback(lambda: True)["status"] == "pass"
        assert len(http_tape.bodies) == 7
        durable(
            root / "READINESS_RESULT.json",
            json.dumps(
                dict(
                    passed=True,
                    role="NON-MODEL scripted HTTP; actual confined tools/checks; not implementation candidate",
                    usage_is_scripted=True,
                    scripted_responses=7,
                    first_failure_preserved=True,
                    oracle_unchanged=True,
                    usage=usage,
                ),
                sort_keys=True,
            ).encode(),
        )
    finally:
        shutil.rmtree(runtime.workspace.parent)
