"""NON-MODEL scripted adapter tests. HTTP mocked; no server/lease or real addon."""

import io
import json
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from workbench import real_execution as job
from workbench.db import model_attempts, write_transaction
from workbench.real_provider import implement
from workbench.real_stream import NamedStream
from workbench.real_tool_contract import ToolBudgetExceeded, ToolDenied
from workbench.real_usage import summarize
from workbench.real_workspace import Tools, durable


@pytest.fixture
def tape(tmp_path, monkeypatch):
    import workbench.real_stream as stream

    state = SimpleNamespace(
        root=tmp_path,
        bodies=[],
        steps=[],
        events=[],
        turns=[],
        requests=[],
        error=None,
        result=None,
        identity="request",
        fence="fence",
    )
    file = tmp_path / "workspace" / job.PATHS[0]
    file.parent.mkdir(parents=True)
    file.write_text("PRIVATE-FIXTURE-CONTENT\nline2\nline3\n")

    class Runtime:
        workspace = file.parents[2]

        def enforce_storage(self):
            pass

        def scope(self):
            pass

    # Edit transactions now preview the real pinned candidate, rather than
    # trusting a fake scope callback. Keep transport mocked; use a tiny Git base.
    from test_verification_profiles import git
    runtime=Runtime()
    runtime.root=tmp_path/'evidence'
    runtime.root.mkdir()
    runtime.paths=job.PATHS
    for name in job.PATHS:
        other=runtime.workspace/name
        other.parent.mkdir(parents=True,exist_ok=True)
        if not other.exists():
            other.write_text('-- unchanged allowed fixture\n')
    git(runtime.workspace,'init','-q')
    git(runtime.workspace,'add','.')
    git(runtime.workspace,'-c','user.name=Fixture','-c','user.email=fixture@invalid','commit','-qm','Controlled base')
    runtime.base=git(runtime.workspace,'rev-parse','HEAD').strip()
    state.tools = Tools(runtime, lambda: True)
    state.file = file

    class Response(io.BytesIO):
        def readline(self, n=-1):
            time.sleep(0.05)  # Permit the actual observer thread to bind its scripted task.
            return super().readline(n)

    class Client:
        def __init__(self, *args):
            self.ends_at = time.monotonic() + 120
            self.opener = self

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, *args, **kwargs):
            if args[0].full_url.endswith("/input_tokens"):
                return b'{"input_tokens":10}'
            return json.dumps(
                [dict(id=0, id_task=42, is_processing=True, next_token=[{"n_decoded": 3}])]
            ).encode()

        def open(self, req, **kwargs):
            body = json.loads(req.data)
            state.bodies.append(body)
            step = state.steps[len(state.bodies) - 1]
            if isinstance(step, Exception):
                raise step
            if step.get("inspect"):
                step["inspect"](body)
            calls = step.get("calls", [])
            delta = {"role": "assistant"}
            if calls:
                delta["tool_calls"] = [
                    dict(
                        index=i,
                        id=f"fixture-{i}",
                        function=dict(name=name, arguments=json.dumps(args)),
                    )
                    for i, (name, args) in enumerate(calls)
                ]
            else:
                delta["content"] = "NON-MODEL scripted completion"
            chunks = [
                dict(
                    model="scripted",
                    choices=[dict(delta=delta, finish_reason="tool_calls" if calls else "stop")],
                )
            ]
            if step.get("usage", True):
                chunks.append(
                    dict(
                        choices=[],
                        usage=step["usage"]
                        if isinstance(step.get("usage"), dict)
                        else dict(prompt_tokens=10, completion_tokens=20),
                    )
                )
            return Response(
                b"".join(b"data: " + json.dumps(c).encode() + b"\n\n" for c in chunks)
                + b"data: [DONE]\n\n"
            )

    class Cancel:
        def __init__(self, secret, nonce, *args, **kwargs):
            self.nonce = nonce

        def delete(self, nonce):
            assert nonce == self.nonce

        def observe(self, nonce):
            return dict(
                nonce=nonce, task_id=42, is_processing=False, producer_done=True, n_decoded=0
            )

    monkeypatch.setattr(stream, "LocalHTTP", Client)
    monkeypatch.setattr(stream, "NativeCancellation", Cancel)
    state.adapter = NamedStream(
        "NON-CREDENTIAL", tmp_path / "session", lambda: [1, 2], lambda: True, lambda e: None
    )

    def record(kind, event):
        event = dict(event, request_id=state.identity, fence=state.fence)
        name = (
            f"{kind}-{event['turn']:02}.json"
            if kind != "tool"
            else f"tool-{event['turn']:02}-{event['call_index']:02}.json"
        )
        durable(tmp_path / "attempt" / name, json.dumps(event, ensure_ascii=True).encode())
        getattr(state, {"request": "requests", "turn": "turns", "tool": "events"}[kind]).append(
            event
        )

    def run(steps, identity="request", fence="fence"):
        state.steps, state.identity, state.fence = steps, identity, fence
        try:
            state.result = implement(
                state.tools,
                "NON-MODEL correction test",
                "",
                "scripted",
                lambda: True,
                ownership=state.adapter,
                on_request=lambda e: record("request", e),
                on_turn=lambda e: record("turn", e),
                on_tool=lambda e: record("tool", e),
            )
        except Exception as e:
            state.error = e
        return state

    state.run = run
    return state


def read(**args):
    return ("read_file", {"path": job.PATHS[0], **args})


def assert_feedback(body):
    error = json.loads(body["messages"][-1]["content"])["error"]
    assert error["continuation_enabled"] and error["correctable"]
    assert error["contract"]["max_lines"] == 250 and error["contract"]["start_default"] == 1
    assert error["contract"]["end_default"] == "min(start+249,2147483647)"
    assert "PRIVATE" not in json.dumps(error)


def test_successful_correction_same_adapter_parser_validator(tape):
    t = tape.run(
        [
            {"calls": [read(start=0)]},
            {"calls": [read(start=2, end=2)], "inspect": assert_feedback},
            {},
        ]
    )
    assert t.error is None and len(t.bodies) == 3
    assert t.result["correction_count"] == 1 and t.result["completion_tokens"] == 60
    assert t.events[0]["outcome"] == "corrective_input" and t.events[1]["outcome"] == "completed"
    assert t.tools.read == 6 and t.bodies[2]["messages"][-1]["content"] == "line2\n"


def test_two_corrections_followed_by_read_and_scoped_edit(tape):
    t = tape.run(
        [
            {"calls": [read(end=251)]},
            {"calls": [read(end=None)], "inspect": assert_feedback},
            {"calls": [read(start=2, end=2)], "inspect": assert_feedback},
            {
                "calls": [
                    ("replace_text", {"path": job.PATHS[0], "old": "line2\n", "new": "fixed\n"})
                ]
            },
            {},
        ]
    )
    assert t.error is None and len(t.bodies) == 5 and t.result["correction_count"] == 2
    assert t.result["completion_tokens"] == 100 and t.tools.read == 6
    assert t.file.read_text() == "PRIVATE-FIXTURE-CONTENT\nfixed\nline3\n"
    assert [e["correction_count"] for e in t.events] == [1, 2, 2, 2]


def test_exact_replacement_miss_can_be_corrected_without_content_leak(tape):
    def feedback(body):
        error = json.loads(body["messages"][-1]["content"])["error"]
        assert error["code"] == "replace_match" and error["continuation_enabled"]
        assert error["contract"]["matching"] == "old must occur exactly once"
        assert "PRIVATE" not in json.dumps(error)

    def edit(old, new):
        return ("replace_text", dict(path=job.PATHS[0], old=old, new=new))

    t = tape.run(
        [
            {"calls": [edit("absent", "new")]},
            {"calls": [read(start=2, end=2)], "inspect": feedback},
            {"calls": [edit("line2\n", "fixed\n")]},
            {},
        ]
    )
    assert t.error is None and t.result["correction_count"] == 1
    assert t.result["read_input_errors"] == 0 and t.result["tool_input_errors"] == 1
    assert t.file.read_text() == "PRIVATE-FIXTURE-CONTENT\nfixed\nline3\n"


def test_read_and_edit_corrections_share_two_error_allowance(tape):
    edit = ("replace_text", dict(path=job.PATHS[0], old="absent", new="new"))
    t = tape.run([{"calls": [read(start=0)]}, {"calls": [edit]}, {"calls": [edit]}, {}])
    assert isinstance(t.error, ToolBudgetExceeded) and len(t.bodies) == 3
    evidence = summarize(t.root, t.identity, t.fence, "scripted")
    assert evidence["correction_count"] == 2 and evidence["read_input_errors"] == 1
    assert evidence["tool_input_errors"] == 3
    assert t.events[-1]["error_code"] == "tool_correction_limit"
    assert not t.events[-1]["continuation_enabled"]


def test_replace_security_denial_is_never_corrective(tape):
    t = tape.run([{"calls": [("replace_text", dict(path="../PRIVATE", old="old", new="new"))]}, {}])
    assert isinstance(t.error, ToolDenied) and len(t.bodies) == 1
    assert not t.events[0]["continuation_enabled"] and t.events[0]["correction_count"] == 0
    assert "PRIVATE" not in json.dumps(t.events)


def test_context_phase_requires_edit_and_correction_reopens_one_read_turn(tape):
    def editing(body):
        names = {t["function"]["name"] for t in body["tools"]}
        assert names == {"replace_text"} and body["tool_choice"] == "required"

    def refreshed(body):
        names = {t["function"]["name"] for t in body["tools"]}
        assert names == {"read_file", "replace_text"} and body["tool_choice"] == "required"

    wrong = ("replace_text", dict(path=job.PATHS[0], old="absent", new="new"))
    right = ("replace_text", dict(path=job.PATHS[0], old="line2\n", new="fixed\n"))
    t = tape.run(
        [
            {"calls": [read(start=2, end=2)] * 4},
            {"calls": [wrong], "inspect": editing},
            {"calls": [read(start=2, end=2)], "inspect": refreshed},
            {"calls": [right], "inspect": editing},
            {},
        ]
    )
    assert t.error is None and t.result["correction_count"] == 1
    assert t.file.read_text() == "PRIVATE-FIXTURE-CONTENT\nfixed\nline3\n"


@pytest.mark.parametrize("start", [0, 3])
def test_unadvertised_phase_read_never_executes_or_becomes_corrective(tape, start):
    t = tape.run([{"calls": [read(start=2, end=2)] * 4}, {"calls": [read(start=start, end=3)]}, {}])
    assert isinstance(t.error, ToolDenied) and len(t.bodies) == 2 and t.tools.read == 24
    assert t.events[-1]["error_code"] == "tool_phase_denied"
    assert not t.events[-1]["continuation_enabled"]


def test_failed_fixed_check_reopens_context_without_extra_attempt(tape):
    def check(*args, **kwargs):
        return dict(status="fail", rc=1, out="NON-MODEL check feedback")

    tape.tools.runtime.check = check

    def refreshed(body):
        assert "read_file" in {t["function"]["name"] for t in body["tools"]}

    t = tape.run(
        [
            {"calls": [read(start=2, end=2)] * 4},
            {"calls": [("replace_text", dict(path=job.PATHS[0], old="line2\n", new="fixed\n"))]},
            {"calls": [("run_check", dict(name="syntax-data"))]},
            {"calls": [read(start=3, end=3)], "inspect": refreshed},
            {},
        ]
    )
    assert t.error is None and len(t.bodies) == 5 and t.result["correction_count"] == 0


def test_fixed_check_schema_and_safe_name_correction(tape):
    from workbench.real_provider import TOOLS
    from workbench.real_tool_contract import CHECK_NAMES

    spec = next(t["function"] for t in TOOLS if t["function"]["name"] == "run_check")
    assert spec["parameters"]["properties"]["name"]["enum"] == list(CHECK_NAMES)
    calls = []

    def check(argv, *args, **kwargs):
        calls.append(argv)
        return dict(status="pass", rc=0, out="NON-MODEL fixed check")

    tape.tools.runtime.check = check

    def feedback(body):
        e = json.loads(body["messages"][-1]["content"])["error"]
        assert e["code"] == "check_name" and e["contract"]["allowed_names"] == list(CHECK_NAMES)
        assert "PRIVATE" not in json.dumps(e)

    t = tape.run(
        [
            {"calls": [("run_check", dict(name="syntax"))]},
            {"calls": [("run_check", dict(name="syntax-data"))], "inspect": feedback},
            {},
        ]
    )
    assert t.error is None and len(calls) == 1 and t.result["correction_count"] == 1
    assert t.result["read_input_errors"] == 0 and t.result["tool_input_errors"] == 1


def test_arbitrary_check_command_never_enters_correction(tape):
    t = tape.run([{"calls": [("run_check", dict(name="sh -c PRIVATE_COMMAND"))]}, {}])
    assert isinstance(t.error, ToolDenied) and len(t.bodies) == 1
    assert not t.events[0]["continuation_enabled"] and t.events[0]["correction_count"] == 0
    assert "PRIVATE" not in json.dumps(t.events)


def test_check_name_corrections_still_share_two_error_limit(tape):
    t = tape.run([{"calls": [("run_check", dict(name="syntax"))]}] * 4)
    assert isinstance(t.error, ToolBudgetExceeded) and len(t.bodies) == 3
    assert (
        t.events[-1]["error_code"] == "tool_correction_limit"
        and not t.events[-1]["continuation_enabled"]
    )


def test_third_error_terminates_without_fourth_request_or_read(tape):
    t = tape.run([{"calls": [read(end=251)]}] * 4)
    assert isinstance(t.error, ToolBudgetExceeded) and len(t.bodies) == 3 and t.tools.read == 0
    assert t.events[-1]["error_code"] == "read_correction_limit"
    evidence = summarize(t.root, t.identity, t.fence, "scripted")
    assert evidence["correction_count"] == 2 and evidence["read_input_errors"] == 3
    assert evidence["usage"]["values"]["completion_tokens"] == 60
    assert not t.events[-1]["continuation_enabled"]


@pytest.mark.parametrize(
    "failure", ["path", "symlink", "read_budget", "payload", "infrastructure", "other_tool"]
)
def test_terminal_failures_cannot_enter_corrective_path(tape, failure):
    if failure == "path":
        step = {"calls": [("read_file", {"path": "../../PRIVATE-SECRET"})]}
    elif failure == "symlink":
        outside = tape.root / "private"
        outside.write_text("PRIVATE-SECRET")
        tape.file.unlink()
        tape.file.symlink_to(outside)
        step = {"calls": [read()]}
    elif failure == "read_budget":
        tape.tools.read = 45000
        step = {"calls": [read()]}
    elif failure == "payload":
        step = {"calls": [read(start="PRIVATE-SECRET" * 300)]}
    elif failure == "other_tool":
        step = {"calls": [("edit_file", [])]}
    else:
        step = OSError("scripted infrastructure failure")
    t = tape.run([step, {}])
    assert t.error is not None and len(t.bodies) == 1
    assert all(not e["continuation_enabled"] and e["correction_count"] == 0 for e in t.events)
    assert "PRIVATE" not in json.dumps(t.events)
    if failure in ("path", "symlink"):
        assert isinstance(t.error, ToolDenied)
    elif failure in ("read_budget", "payload"):
        assert isinstance(t.error, ToolBudgetExceeded)


def test_corrections_still_use_original_turn_budget(tape, monkeypatch):
    monkeypatch.setitem(job.LIMITS, "turns", 2)
    t = tape.run([{"calls": [read(end=251)]}] * 3)
    assert len(t.bodies) == 2 and t.result is None and "Twenty-turn" in str(t.error)
    assert len(t.requests) == 2 and all(b["max_tokens"] == 2496 for b in t.bodies)


@pytest.mark.parametrize(
    "case",
    ["completed_then_tool_failure", "missing_usage", "later_unavailable", "postresponse_failure"],
)
def test_worker_failure_accounts_known_and_unavailable_usage(store, tape, case):
    class Runtime:
        def preflight(self):
            return [{"name": "NON-MODEL test double", "status": "passed"}]

        def identity(self):
            return {"model": "scripted"}

        def cleanup(self, row):
            return True

        def execute(self, identity, fence, permitted):
            assert permitted()
            steps = (
                [{"calls": [("read_file", {"path": "../denied"})]}]
                if case == "completed_then_tool_failure"
                else [{}]
                if case == "postresponse_failure"
                else [{"usage": False}]
                if case == "missing_usage"
                else [{"calls": [read(start=2, end=2)]}, OSError("scripted lost response")]
            )
            t = tape.run(steps, identity, fence)
            if case == "postresponse_failure":
                assert t.error is None
                raise ValueError("scripted post-response independent check failure")
            assert t.error is not None
            raise t.error

        def failure_evidence(self, identity, fence):
            return summarize(tape.root, identity, fence, "scripted")

    runtime = Runtime()
    grant = job.issue(store, "NON-MODEL UNIT TEST AUTHORIZATION", "pytest disposable", runtime)
    receipt = job.submit(store, grant["id"], str(uuid4()))
    assert job.process_one(store, runtime)
    review = job.review(store, receipt["id"])
    assert review["status"] == "failed"
    usage = review["result"]["usage"]
    with store.connect() as c:
        ledger = (
            c.execute(select(model_attempts).where(model_attempts.c.execution_id == receipt["id"]))
            .mappings()
            .one()
        )
    assert ledger["usage"] == usage
    if case in ("completed_then_tool_failure", "postresponse_failure"):
        assert usage["status"] == "complete" and usage["values"]["completion_tokens"] == 20
        assert review["result"]["inference_ran"] is True
    elif case == "missing_usage":
        assert usage["status"] == "unavailable" and usage["values"]["completion_tokens"] is None
        assert usage["known_values"]["completion_tokens"] == 0
    else:
        assert usage["status"] == "partial" and usage["values"]["completion_tokens"] is None
        assert usage["known_values"]["completion_tokens"] == 20 and usage["unavailable_turns"] == [
            1
        ]
    assert usage["provenance"]["records"]
    before = ledger["usage"]
    assert not job.process_one(store, runtime)  # No inference replay or terminal-history rewrite.
    with store.connect() as c:
        assert (
            c.execute(
                select(model_attempts.c.usage).where(model_attempts.c.id == ledger["id"])
            ).scalar_one()
            == before
        )


def test_usage_rejects_cross_attempt_binding(tape):
    t = tape.run([{}])
    assert t.error is None
    wrong = summarize(t.root, "different-request", "different-fence", "scripted")
    assert (
        wrong["usage"]["status"] == "unavailable"
        and wrong["usage"]["values"]["completion_tokens"] is None
    )
    assert wrong["usage"]["known_values"]["completion_tokens"] == 0


def test_recovery_preserves_current_usage_without_replaying(store, tape, monkeypatch):
    class Runtime:
        def preflight(self):
            return [{"name": "NON-MODEL test", "status": "passed"}]

        def identity(self):
            return {"model": "scripted"}

        def cleanup(self, row):
            return True

        def failure_evidence(self, identity, fence):
            # Bound scripted evidence, never the live historical store.
            return summarize(tape.root, identity, fence, "scripted")

    runtime = Runtime()
    grant = job.issue(store, "NON-MODEL RECOVERY TEST", "pytest disposable", runtime)
    receipt = job.submit(store, grant["id"], str(uuid4()))
    tape.run([{}], receipt["id"], "test-fence")
    with write_transaction(store) as c:
        c.execute(
            job.requests.update()
            .where(job.requests.c.id == receipt["id"])
            .values(status="running", fence="test-fence", result={"owner": job.owner_identity()})
        )
        c.execute(
            model_attempts.insert().values(
                id="test-fence",
                job_id=None,
                execution_id=receipt["id"],
                task_id=job.TASK,
                source="workbench",
                purpose="implementation",
                routing_reason="NON-MODEL test",
                started_at=job.now(),
                outcome="running",
            )
        )
    monkeypatch.setattr(job, "alive", lambda owner: False)
    job.recover(store, runtime)
    review = job.review(store, receipt["id"])
    assert (
        review["status"] == "interrupted"
        and review["result"]["usage"]["values"]["completion_tokens"] == 20
    )


def test_verified_zero_usage_is_known_not_unavailable(tape):
    t = tape.run([{"usage": {"prompt_tokens": 10, "completion_tokens": 0}}])
    assert t.error is None
    evidence = summarize(t.root, t.identity, t.fence, "scripted")["usage"]
    assert evidence["status"] == "complete" and evidence["values"]["completion_tokens"] == 0
    assert evidence["validated_responses"] == 1 and evidence["unavailable_turns"] == []


@pytest.mark.parametrize("damaged", ["tool", "request"])
def test_damaged_record_does_not_discard_independent_known_turn_usage(tape, damaged):
    t = tape.run([{}])
    path = (
        t.root / "attempt" / f"{damaged}-00-00.json"
        if damaged == "tool"
        else t.root / "attempt/request-00.json"
    )
    path.write_text("{partial-json")
    evidence = summarize(t.root, t.identity, t.fence, "scripted")
    usage = evidence["usage"]
    assert usage["known_values"]["completion_tokens"] == 20
    assert usage["provenance"]["invalid_records"] == [str(path.relative_to(t.root))]
    if damaged == "tool":
        assert usage["status"] == "complete" and usage["values"]["completion_tokens"] == 20
        assert evidence["correction_count"] is None
    else:
        assert usage["status"] == "partial" and usage["values"]["completion_tokens"] is None


def test_simple_check_infrastructure_failure_is_terminal_with_usage(tape):
    tape.tools.runtime.check = lambda *a, **k: dict(status="timeout", rc=None, out="NON-MODEL")
    t = tape.run([{"calls": [("run_check", {"name": "focused"})]}, {}])
    assert isinstance(t.error, ToolDenied) and len(t.bodies) == 1
    assert t.turns[0]["usage"]["completion_tokens"] == 20
    assert not t.events[0]["continuation_enabled"]


@pytest.mark.parametrize(
    "output", ["\x00" * 4000, "💡" * 4000, '\x00\\"<script>NON-MODEL</script>✓💡' * 2000]
)
def test_large_untrusted_check_feedback_remains_bounded_valid_json(tape, output):
    tape.tools.runtime.check = lambda *a, **k: dict(status="fail", rc=1, out=output)

    def inspect(body):
        result = json.loads(body["messages"][-1]["content"])
        assert result["status"] == "fail" and result["truncated"]
        assert result["out"] == output[:512]

    t = tape.run([{"calls": [("run_check", {"name": "focused"})]}, {"inspect": inspect}])
    assert t.error is None and t.result["correction_count"] == 0


def test_supported_distinct_read_policy_requires_edit_after_complete_context(tape):
    tape.tools.runtime.recipe = {"read_budget_policy": "distinct-lines-v1"}
    tape.tools.runtime.paths = job.PATHS
    tape.tools.runtime.read_paths = job.PATHS
    tape.tools.runtime.tool_commands = lambda: {"focused": ["lua5.1", "focused.lua"]}

    def first_edit(body):
        assert {x["function"]["name"] for x in body["tools"]} == {"replace_text"}
        assert body["tool_choice"] == "required"

    t = tape.run(
        [
            {"calls": [read(start=2, end=2)] * 4},
            {
                "calls": [("replace_text", dict(path=job.PATHS[0], old="line2\n", new="fixed\n"))],
                "inspect": first_edit,
            },
            {},
        ]
    )
    assert t.error is None and t.tools.read == 6 and len(t.bodies) == 3


def test_safe_log_window_error_is_correctable_in_same_attempt(tape):
    from workbench.real_feedback import CheckFeedback

    tape.tools.runtime.recipe = {"controlled": True}
    tape.tools.runtime.paths = job.PATHS
    tape.tools.runtime.read_paths = job.PATHS
    tape.tools.runtime.tool_commands = lambda: {"focused": ["lua5.1", "focused"]}
    tape.tools.runtime.feedback = CheckFeedback(tape.root, job.PATHS)
    tape.tools.runtime.feedback.record(
        dict(status="fail", rc=1, out="retained log\n" * 98), "fixed-000"
    )

    def feedback(body):
        error = json.loads(body["messages"][-1]["content"])["error"]
        assert error["code"] == "check_log_input" and error["contract"]["max_lines"] == 80
        assert error["continuation_enabled"] and "retained log" not in json.dumps(error)

    bad = ("read_check_log", dict(check_id="fixed-000", start=1, end=98))
    good = ("read_check_log", dict(check_id="fixed-000", start=81, end=98))
    t = tape.run([{"calls": [bad]}, {"calls": [good], "inspect": feedback}, {}])
    assert t.error is None and t.result["correction_count"] == 1 and len(t.bodies) == 3
    assert t.events[0]["outcome"] == "corrective_input"
