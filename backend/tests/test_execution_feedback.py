"""NON-MODEL diagnostics, confinement and repeated-read regression evidence."""

import json
from types import SimpleNamespace
import pytest
from workbench.real_feedback import CheckFeedback, LOG_PAGE_BYTES
from workbench.real_workspace import Tools
from workbench.real_tool_contract import ToolDenied, ToolBudgetExceeded, invoke
from workbench.real_execution import PATHS


def test_failure_at_end_is_actionable_and_full_log_is_paged(tmp_path):
    f = CheckFeedback(tmp_path, ["tests/test_guild_locations.lua"])
    raw = (
        "routine chatter\n" * 120
        + "lua5.1: test_guild_locations.lua:920: FAILED: expected valid level1, actual nil\nstack traceback:\n\ttest_guild_locations.lua:920: in main chunk\n"
    )
    result = f.record(dict(status="fail", rc=1, out=raw), "fixed-000")
    assert "expected valid level1, actual nil" in result["diagnostic"]
    assert result["context_hint"] == dict(path="tests/test_guild_locations.lua", start=916, end=924)
    page = json.loads(f.read(dict(check_id="fixed-000", start=121, end=123)))
    assert "FAILED" in page["lines"][0]["text"]
    charged=f.read_chars
    replay=json.loads(f.read(dict(check_id="fixed-000", start=121, end=123)))
    assert replay['lines']==page['lines'] and replay['previously_shown']
    assert f.read_chars==charged
    assert len(json.dumps(result, ensure_ascii=True).encode()) < 8192
    for identity in ["../private", "/etc/passwd", "working-01-00"]:
        with pytest.raises(ToolDenied):
            f.read(dict(check_id=identity))


def test_untrusted_unicode_controls_and_large_diagnostics_stay_bounded(tmp_path):
    f = CheckFeedback(tmp_path, [])
    result = f.record(dict(status="fail", rc=1, out="FAILED:" + ("\x00é☃" * 1000)), "fixed-000")
    # Tool serialization must remain a valid bounded JSON object, never clipped JSON.
    assert len(json.dumps(result, ensure_ascii=True).encode()) < 8192
    with pytest.raises(ValueError):
        f.read(dict(check_id="fixed-000", start=True))


def distinct(tmp_path):
    p = tmp_path / PATHS[0]
    p.parent.mkdir(parents=True)
    p.write_text("alpha\nbeta\ngamma\n")
    rt = SimpleNamespace(workspace=tmp_path, recipe={"read_budget_policy": "distinct-lines-v1"})
    return Tools(rt, lambda: True), p


def read(tools, start=1, end=3):
    return json.loads(tools.handle("read_file", dict(path=PATHS[0], start=start, end=end)))


def test_distinct_reads_omit_unchanged_overlap_and_charge_changed_lines(tmp_path):
    tools, p = distinct(tmp_path)
    assert [v["text"] for v in read(tools, 1, 2)["spans"]] == ["alpha\nbeta\n"]
    assert tools.read == 11
    assert [v["text"] for v in read(tools, 2, 3)["spans"]] == ["gamma\n"]
    assert tools.read == 17 and read(tools)["already_shown_or_empty"]
    p.write_text("alpha\nfixed\ngamma\n")
    assert [v["text"] for v in read(tools)["spans"]] == ["fixed\n"]
    assert tools.read == 23
    p.unlink()
    p.symlink_to("/etc/passwd")
    with pytest.raises(ToolDenied):
        read(tools)


def test_fully_repeated_focused_read_returns_usable_current_text_without_new_charge(tmp_path):
    tools, p = distinct(tmp_path)
    p.write_text(''.join(f'local value_{i} = {i}\n' for i in range(1, 181)))
    read(tools, 1, 180)
    used = tools.read
    # Retained real incident: the agent narrowed the same already-read window
    # one start line at a time because the application returned no source text.
    for start in range(100, 111):
        value = read(tools, start, 152)
        assert value['already_shown_or_empty'] and value['spans'] == []
        assert value['replayed_spans'][0]['start'] == start
        assert value['replayed_spans'][0]['end'] == 152
        assert f'local value_{start} = {start}\n' in value['replayed_spans'][0]['text']
        assert value['next_start'] is None and tools.read == used
        assert len(json.dumps(value, ensure_ascii=True).encode()) < 8192
    value = read(tools, 200, 210)
    assert value['total_lines'] == 180 and not value['replayed_spans']


def test_replayed_unicode_source_remains_paged_and_does_not_hide_changed_lines(tmp_path):
    tools, p = distinct(tmp_path)
    p.write_text(('☃' * 20 + '\n') * 250)
    for _ in range(10):
        read(tools, 1, 250)
    used = tools.read
    value = read(tools, 1, 250)
    assert value['replayed_spans'] and value['next_start']
    assert len(json.dumps(value, ensure_ascii=True).encode()) < 8192
    assert tools.read == used
    p.write_text('fixed\n' + ('☃' * 20 + '\n') * 249)
    assert read(tools, 1, 3)['spans'][0]['text'] == 'fixed\n'
    assert tools.read == used + len('fixed\n')


def test_source_budget_and_response_bound_remain_enforced(tmp_path):
    tools, p = distinct(tmp_path)
    p.write_text(("☃" * 20 + "\n") * 250)
    result = read(tools, 1, 250)
    assert len(json.dumps(result, ensure_ascii=True).encode()) < 8192
    assert result["spans"][-1]["end"] < 250  # Unreturned lines are not charged.
    assert tools.read == sum(len(x["text"]) for x in result["spans"])
    tools.read = 45000
    with pytest.raises(ToolBudgetExceeded):
        read(tools, 250, 250)


def test_log_dispatch_path_cannot_select_arbitrary_files(tmp_path):
    tools, _ = distinct(tmp_path)
    tools.runtime.feedback = CheckFeedback(tmp_path, PATHS)
    tools.runtime.feedback.record(dict(status="fail", rc=1, out="FAILED: retained"), "fixed-000")
    event = []
    result = invoke(
        tools,
        dict(
            id="scripted",
            function=dict(name="read_check_log", arguments=json.dumps(dict(check_id="fixed-000"))),
        ),
        0,
        0,
        event.append,
    )
    assert "retained" in json.loads(result)["lines"][0]["text"]
    assert event[0]["outcome"] == "completed"
    with pytest.raises(ToolDenied):
        invoke(
            tools,
            dict(
                function=dict(
                    name="read_check_log", arguments=json.dumps(dict(check_id="../SECRET"))
                )
            ),
            0,
            1,
            event.append,
        )
    assert "SECRET" not in json.dumps(event)


def test_unicode_multiline_summary_and_long_log_line_are_retrievable(tmp_path):
    f = CheckFeedback(tmp_path, [])
    raw = "".join("FAILED: " + ("☃" * 1000) + "\n" for _ in range(10))
    result = f.record(dict(status="fail", rc=1, out=raw), "fixed-000")
    assert len(json.dumps(result, ensure_ascii=True).encode()) < 8192
    output = []
    position = {"start": 1}
    loops = 0
    while position:
        value = json.loads(f.read(dict(check_id="fixed-000", **position)))
        assert len(json.dumps(value, ensure_ascii=True).encode()) <= LOG_PAGE_BYTES
        output.extend(x["text"] for x in value["lines"])
        position = value["next_page"]
        loops += 1
        assert loops < 100
    assert "".join(output) == raw


def test_adaptive_log_pages_charge_only_delivered_characters_in_overlapping_rereads(tmp_path):
    f = CheckFeedback(tmp_path, [])
    raw = 'prefix\n' + ('☃' * 700) + '\n'
    f.record(dict(status='fail', rc=1, out=raw), 'fixed-000')
    first = json.loads(f.read(dict(check_id='fixed-000', start=1)))
    charged = sum(len(x['text']) for x in first['lines'])
    assert f.read_chars == charged and first['next_page']['start'] == 2
    # Same start offset, different available page space: the longer second
    # fragment must charge its new tail. A cursor-only seen-set missed this.
    overlap = json.loads(f.read(dict(check_id='fixed-000', start=2)))
    assert len(overlap['lines'][0]['text']) > len(first['lines'][1]['text'])
    assert not overlap['previously_shown']
    assert f.read_chars == len('prefix\n') + len(overlap['lines'][0]['text'])
    used = f.read_chars
    replay = json.loads(f.read(dict(check_id='fixed-000', start=2)))
    assert replay['previously_shown'] and f.read_chars == used
    cursor = overlap['next_page']
    while cursor:
        value = json.loads(f.read(dict(check_id='fixed-000', **cursor)))
        assert value['lines'] and value['next_page'] != cursor
        cursor = value['next_page']
    assert f.read_chars == len(raw)
    f.read_chars = 96000
    f.record(dict(status='fail', rc=1, out='new log'), 'fixed-001')
    with pytest.raises(ToolBudgetExceeded):
        f.read(dict(check_id='fixed-001'))


def test_saved_log_tamper_is_denied(tmp_path):
    f = CheckFeedback(tmp_path, [])
    f.record(dict(status="fail", rc=1, out="FAILED: original"), "fixed-000")
    (tmp_path / "check-logs/fixed-000.json").write_text("{}")
    with pytest.raises(ToolDenied):
        f.read(dict(check_id="fixed-000"))


def test_candidate_feedback_does_not_duplicate_escaped_diagnostic_past_wire_bound(
    tmp_path, monkeypatch
):
    from workbench.real_workspace import Runtime
    import workbench.real_workspace as module

    rt = object.__new__(Runtime)
    rt.root = tmp_path
    rt.workspace = tmp_path / "workspace"
    rt.workspace.mkdir()
    rt.initial = tmp_path / "base.json"
    rt.initial.write_text("{}")
    rt.oracle_source = tmp_path / "oracle.lua"
    rt.oracle_source.write_text("-- NON-MODEL")
    rt.base = "a" * 40
    rt.enforce_storage = lambda: None
    fixture_file = rt.workspace / "tests/test_fixture.lua"
    fixture_file.parent.mkdir()
    fixture_file.write_text("-- NON-MODEL fixture\n")
    rt.paths = ("tests/test_fixture.lua",)
    rt.recipe = {"controlled": True}
    rt._request_id = "NON-MODEL"
    rt._fence = "NON-MODEL"
    rt._context_sha = None
    rt.context_sha = lambda: None
    rt.scope = lambda: (list(rt.paths), "NON-MODEL diff")
    rt.expected_checks = lambda: [["syntax"], ["focused"], ["oracle"], ["suite"]]
    rt.expected_suite = lambda: []
    rt.check = lambda *a, **k: dict(status="fail", rc=1, out=("FAILED: " + "☃" * 600 + "\n") * 5)
    rt.feedback = CheckFeedback(tmp_path, rt.paths)
    monkeypatch.setattr(module, "tree", lambda _: {"changed": "fixture"})
    reply = Tools(rt, lambda: True).handle("run_check", {"name": "candidate"})
    assert len(reply.encode()) < 8192
    assert json.loads(reply)["check_id"] == "working-00-01"


@pytest.mark.parametrize('original_pass,suite_pass',[(False,True),(True,False),(True,True)])
def test_original_discrimination_runs_after_edit_checks_before_complete_suite(tmp_path,monkeypatch,original_pass,suite_pass):
    from workbench.real_workspace import Runtime
    import workbench.real_workspace as module
    import workbench.repair_cycle as cycle
    rt=object.__new__(Runtime)
    rt.root=tmp_path
    rt.workspace=tmp_path/'workspace'
    rt.workspace.mkdir()
    rt.initial=tmp_path/'base.json'
    rt.initial.write_text('{}')
    rt.oracle_source=tmp_path/'oracle.lua'
    rt.oracle_source.write_text('-- controlled')
    rt.base='a'*40
    rt._request_id=rt._fence='controlled'
    rt._context_sha=None
    rt.context_sha=lambda:None
    rt.paths=['source.lua','tests/test_fixture.lua']
    for path in rt.paths:
        target=rt.workspace/path
        target.parent.mkdir(exist_ok=True)
        target.write_text('-- controlled changed text')
    rt.recipe=dict(repair_cycle='scoped-repair-v1',focused_test=rt.paths[-1])
    rt.scope=lambda:(rt.paths,'controlled diff')
    names=['syntax-1','syntax-2','focused','independent-oracle','complete-suite']
    rt.expected_checks=lambda:[[name] for name in names]
    rt.expected_suite=lambda:[]
    rt.feedback=CheckFeedback(tmp_path,rt.paths)
    order=[]
    def check(argv,*args,**kwargs):
        order.append(argv[0])
        ok=argv[0]!='complete-suite' or suite_pass
        return dict(status='pass' if ok else 'fail',rc=0 if ok else 1,out='CONTROLLED suite observation',
            usage=dict(cgroup_removed=True,leaked_after_kill=False))
    rt.check=check
    def original(*args):
        order.append('original-with-new-tests')
        return dict(status='pass' if original_pass else 'fail',reason='Controlled original differential')
    monkeypatch.setattr(cycle,'original_with_new_tests',original)
    monkeypatch.setattr(module,'tree',lambda _:dict(controlled='changed'))
    monkeypatch.setattr(module,'suite_inventory',lambda *args:True)
    result=rt.candidate_feedback(lambda:True)
    assert order==names[:-1]+['original-with-new-tests']+(['complete-suite'] if original_pass else [])
    assert result['status']==('pass' if original_pass and suite_pass else 'fail')
    if not original_pass:
        assert result['failed_check_name']=='original-with-new-tests'
    elif not suite_pass:
        assert result['failed_check_name']=='complete-suite'


def test_literal_symbol_search_is_bounded_scoped_and_deduplicated(tmp_path):
    tools, p = distinct(tmp_path)
    p.write_text("preamble\nfunction SH.FullName(name)\nreturn name\nend\n")
    result = json.loads(
        invoke(
            tools,
            dict(
                function=dict(
                    name="find_text", arguments=json.dumps(dict(path=PATHS[0], query="FullName"))
                )
            ),
            0,
            0,
        )
    )
    assert result["matches"] == [dict(line=2, preview="function SH.FullName(name)")]
    cost = tools.read
    replay=json.loads(tools.handle("find_text", dict(path=PATHS[0], query="FullName")))
    assert replay['already_shown'] and replay['matches']==result['matches']
    assert tools.read == cost
    with pytest.raises(ToolDenied):
        tools.handle("find_text", dict(path="../private", query="SECRET"))
    p.write_text(("☃" * 300 + "\n") * 100)
    result = tools.handle("find_text", dict(path=PATHS[0], query="☃"))
    assert len(result.encode()) < 8192 and len(json.loads(result)["matches"]) <= 12


def test_measured_supported_read_allowance_is_bounded_without_legacy_change(tmp_path):
    from workbench.task_budget import valid_contract
    from workbench.real_execution import contract

    value = contract()
    value["limits"] = {**value["limits"], "read_chars": 98304}
    assert valid_contract(value)
    assert not valid_contract({**value, "limits": {**value["limits"], "read_chars": 98305}})
    assert not valid_contract({**value, "limits": {**value["limits"], "memory_bytes": 7 << 30}})
    tools, p = distinct(tmp_path)
    tools.runtime.source_read_limit = 98304
    p.write_text(("x" * 200 + "\n") * 500)
    for _ in range(15):
        read(tools, 1, 250)
    assert tools.read == 50250 > 45000
    with pytest.raises(ToolBudgetExceeded):
        for _ in range(15):
            read(tools, 251, 500)
    assert tools.read <= 98304
