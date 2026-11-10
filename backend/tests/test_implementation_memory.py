"""Lossless private source retention, current rereads and carried real feedback."""

import json
from pathlib import Path
from types import SimpleNamespace

from workbench.implementation_context import compact_source, ReadProgress, prior_failures, retained_work
from workbench.real_workspace import Tools
from workbench.real_execution import PATHS
import hashlib
import pytest


def test_retained_diff_is_exact_model_work_and_changed_binding_is_refused(tmp_path):
    patch='controlled prior model diff, not a new solution'
    digest=hashlib.sha256(patch.encode()).hexdigest()
    saved={'root':str(tmp_path/'old'),'patch_sha256':digest,'execution_id':'controlled-prior'}
    (tmp_path/'repair-resume.json').write_text(json.dumps(saved))
    runtime=SimpleNamespace(root=tmp_path,scope=lambda **kwargs:([PATHS[0]],patch))
    value=retained_work(runtime)
    assert value['not_verified'] and value['patch']==patch and value['previous_execution']=='controlled-prior'
    runtime.scope=lambda **kwargs:([PATHS[0]],'changed source')
    with pytest.raises(ValueError,match='differs'):
        retained_work(runtime)


def test_compaction_keeps_tool_protocol_and_private_exact_source_without_resetting_reads(tmp_path):
    workspace = tmp_path / "workspace"
    source = workspace / PATHS[0]
    source.parent.mkdir(parents=True)
    source.write_text("".join(f"local value_{i} = {i}\n" for i in range(1, 80)))
    runtime = SimpleNamespace(
        root=tmp_path / "evidence",
        workspace=workspace,
        read_paths=[PATHS[0]],
        recipe={"read_budget_policy": "distinct-lines-v1"},
    )
    tools = Tools(runtime, lambda: True)
    raw = tools.handle("read_file", {"path": PATHS[0], "start": 1, "end": 70})
    used = tools.read
    progress = ReadProgress()
    progress.observe("read_file", raw)
    messages = [
        {"role": "system", "content": "fixed security instructions"},
        {"role": "user", "content": "current approved scope"},
        {"role": "assistant", "tool_calls": [{"id": "exact-source-call"}]},
        {"role": "tool", "tool_call_id": "exact-source-call", "content": raw},
        *[{"role": "user", "content": "latest failure feedback"} for _ in range(4)],
    ]
    assert compact_source(messages, runtime)
    summary = json.loads(messages[3]["content"])
    assert "value_1" not in messages[3]["content"] and summary["path"] == PATHS[0]
    assert (
        runtime.root / "attempt/context-sources" / (summary["retained_source_sha256"] + ".json")
    ).read_text() == raw
    assert messages[3]["tool_call_id"] == "exact-source-call"
    assert (
        messages[0]["content"] == "fixed security instructions"
        and messages[1]["content"] == "current approved scope"
    )
    progress.rebuild_visible(messages)
    # A required reread of archived source is useful again, but still consumes
    # zero NEW distinct-source characters and cannot reset any task limit.
    replay = tools.handle("read_file", {"path": PATHS[0], "start": 50, "end": 60})
    progress.observe("read_file", replay)
    assert progress.repeated == 0 and tools.read == used
    for start in range(51, 55):
        progress.observe(
            "read_file", tools.handle("read_file", {"path": PATHS[0], "start": start, "end": 60})
        )
    assert progress.repeated == 4
    assert messages[-1]["content"] == "latest failure feedback"


def test_only_old_exact_source_can_be_compacted(tmp_path):
    messages = [
        {"role": "system", "content": "safety"},
        {"role": "user", "content": "approval"},
        {
            "role": "tool",
            "content": json.dumps({"status": "fail", "diagnostic": "real checker failed"}),
        },
        *[{"role": "user", "content": "newest"} for _ in range(4)],
    ]
    assert not compact_source(messages, SimpleNamespace(root=tmp_path))
    assert "real checker failed" in messages[2]["content"]
    assert not list(tmp_path.rglob("*"))


def test_continuation_carries_actual_failed_check_observations_not_a_solution(tmp_path):
    previous = tmp_path / "old"
    (previous / "check-logs").mkdir(parents=True)
    for n, status in enumerate(["pass", "fail", "fail"]):
        data = dict(
            check_id=f"fixed-{n:03}",
            result=dict(
                status=status,
                rc=0 if status == "pass" else 1,
                out=f"lua5.1: {Path(PATHS[0]).name}:24: assertion failed!\nstack traceback:\n",
            ),
        )
        (previous / "check-logs" / f"fixed-{n:03}.json").write_text(json.dumps(data))
    root = tmp_path / "fresh"
    root.mkdir()
    (root / "repair-resume.json").write_text(json.dumps({"root": str(previous)}))
    runtime = SimpleNamespace(root=root, read_paths=[PATHS[0]])
    observed = prior_failures(runtime)
    assert len(observed) == 2 and all(x["status"] == "fail" for x in observed)
    assert observed[-1]["context_hint"] == dict(path=PATHS[0], start=20, end=28)
    assert "assertion failed" in observed[-1]["diagnostic"]
    assert not any("log" in row or "check_id" in row for row in observed)
    assert len(list((root / "prior-feedback/check-logs").glob("*.json"))) == 2


def test_production_provider_loop_compacts_before_dispatch_and_keeps_actual_usage(tmp_path):
    from workbench.real_provider import implement
    from test_implementation_context import response
    from test_real_execution import answer
    import copy

    workspace = tmp_path / "workspace"
    path = workspace / PATHS[0]
    path.parent.mkdir(parents=True)
    path.write_text("".join(f"local row_{i} = {i}\n" for i in range(1, 80)))
    runtime = SimpleNamespace(
        root=tmp_path / "evidence",
        workspace=workspace,
        read_paths=[PATHS[0]],
        paths=[PATHS[0]],
        recipe={
            "source_context": [{}],
            "repair_cycle": "scoped-repair-v1",
            "read_budget_policy": "distinct-lines-v1",
        },
        tool_commands=lambda: {"focused": ["controlled"], "syntax-1": ["controlled"]},
    )
    tools = Tools(runtime, lambda: True)
    real_handle = tools.handle
    tools.handle = lambda name, args: (
        json.dumps(dict(status="pass")) if name == "run_check" else real_handle(name, args)
    )
    replies = [
        response("read_file", dict(path=PATHS[0], start=1, end=70), 0),
        response("read_file", dict(path=PATHS[0], start=71, end=79), 1),
        response("run_check", dict(name="focused"), 2),
        response("run_check", dict(name="syntax-1"), 3),
        response("read_file", dict(path=PATHS[0], start=50, end=60), 4),
        answer(),
    ]
    bodies = []

    class Owned:
        def count_input_tokens(self, body, deadline):
            compacted = any(
                "retained_source_sha256" in m.get("content", "") for m in body["messages"]
            )
            return 31000 if len(body["messages"]) >= 10 and not compacted else 1000

        def request(self, body, allowed, deadline):
            assert allowed()
            bodies.append(copy.deepcopy(body))
            return replies.pop(0)

    turns = []
    result = implement(
        tools,
        "Controlled source task",
        "",
        "controlled",
        lambda: True,
        ownership=Owned(),
        on_turn=turns.append,
    )
    assert result["completion_tokens"] == 120 and len(bodies) == len(turns) == 6
    assert tools.read == len(path.read_text())
    assert any("retained_source_sha256" in m.get("content", "") for m in bodies[4]["messages"])
    assert json.loads(bodies[-1]["messages"][-1]["content"])["replayed_spans"]
    assert list((runtime.root / "attempt/context-sources").glob("*.json"))
