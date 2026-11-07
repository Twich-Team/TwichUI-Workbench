"""Two ordinary bug journeys across API/worker process restarts, with scripted models."""
import copy
import json
import multiprocessing
import os
from pathlib import Path
import subprocess

import pytest
from sqlalchemy import select

from workbench import bug_investigation, codex_provider, worker
from workbench.bug_evidence import BugPlan
from workbench.api import create_app
from workbench.db import intakes, model_attempts, proposal_decisions, task_sessions as sessions
from workbench.planning_provider import LocalProvider
from workbench.real_execution import LIMITS
from workbench.schemas import TaskBudget
from workbench.visual_scope import digest
from test_acceptance_browser import serve
from test_bug_workflow import (
    BAD_ALERT, GOOD_ALERT, CLIENT_SOURCE, enable_bugs, report, response,
)
from test_everyday import installed  # noqa: F401
from test_verification_profiles import BUGGY, CORRECT, PRODUCTION, git

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(os.environ.get("WB_PROCESS_REHEARSAL") != "1",
                                reason="Explicit multi-process browser rehearsal")


def three_observations(client_case):
    value = copy.deepcopy(response(client_case))
    fixture = value["plan"]["reproducer"]
    if client_case:
        fixture["steps"] += [dict(op="capture", path=["Notify"]),
                             dict(op="call", path=["Deliver"], args=["Pat"])]
        fixture["observations"].append(dict(after_step=7, source="captured", path=[2],
                                             expected="Pat", incorrect="Unknown"))
    else:
        for text, expected, incorrect in [(" BOB ", "bob", "BOB"), (" Lee ", "lee", "Lee")]:
            fixture["steps"].append(dict(op="call", args=[text]))
            fixture["observations"].append(dict(after_step=len(fixture["steps"]),
                                                source="return", path=[1],
                                                expected=expected, incorrect=incorrect))
    return value


def test_two_bugs_with_two_browsers_and_separate_process_restarts(
    store, installed, tmp_path, monkeypatch,  # noqa: F811
):
    import local_browser_fixture
    from project_env import environment

    source = enable_bugs(monkeypatch)
    monkeypatch.setenv("WORKBENCH_STATIC_ROOT", str(ROOT / "frontend/dist"))
    monkeypatch.setattr(local_browser_fixture, "install_offline_guard", lambda: None)
    cases = []
    for index, client_case in enumerate([False, True]):
        request = report(client_case)
        request.pop("request_id")
        request["lua_error"] = ""
        policy = json.loads(Path(os.environ["WORKBENCH_PREPARATION_CONFIG"]).read_text())
        packet = bug_investigation.context(request, "", policy)
        answer = three_observations(client_case)
        cases.append(dict(choice="fixture-bug-" + str(index), request=request,
                          provider="local", reason=None, model="pilot-qwen38-27b",
                          evidenceSha=digest(packet["files"]), writablePaths=answer["plan"]["writable_paths"],
                          reviewed=BugPlan.model_validate(answer["plan"]).model_dump(mode="json"),
                          client_case=client_case))
    policy = dict(base=git(source, "rev-parse", "HEAD").strip(), limits=LIMITS, cases=cases,
                  models=dict(local="pilot-qwen38-27b", codex="gpt-6.1-sol"),
                  budget=TaskBudget(activate_once=True, offer_id="00000000-0000-0000-0000-000000000000",
                                    offer_fingerprint="0"*64).model_dump(mode="json"),
                  note="Controlled process rehearsal, no owner live authority")
    path = tmp_path / "bug-browser-policy.json"
    path.write_text(json.dumps(policy))
    ctx = multiprocessing.get_context("fork")
    calls, errors = ctx.Queue(), ctx.Queue()

    def scripted(self, packet, schema, expected_model):
        assert schema is bug_investigation.BugPlanningResponse
        case = next(c for c in cases if c["request"]["title"] == packet["report"]["title"])
        calls.put((case["choice"], "planning"))
        self.diagnostics = dict(actual_model=expected_model, generation_state="finished",
                                usage=codex_provider.usage_record(dict(prompt_tokens=40,
                                    completion_tokens=10, reasoning_tokens=0, total_tokens=50)))
        return three_observations(case["client_case"]), expected_model, "local"

    monkeypatch.setattr(LocalProvider, "generate", scripted)
    # No real connector is used. The real browser gate observes the explicitly
    # controlled availability that this fixture declared.
    monkeypatch.setattr(codex_provider, "connector_path", lambda: tmp_path / "controlled.sock")
    monkeypatch.setenv("WORKBENCH_CODEX_SOCKET", str(tmp_path / "controlled.sock"))

    def advance(stopped):
        try:
            while not stopped.wait(.04):
                with store.connect() as connection:
                    pending = connection.execute(select(intakes.c.id, intakes.c.payload).join_from(
                        sessions, proposal_decisions, sessions.c.id == proposal_decisions.c.id,
                    ).join(intakes).where(sessions.c.status == "pending")).first()
                if pending:
                    case = next(c for c in cases if c["request"]["title"] == pending.payload["title"])
                    client_case = case["client_case"]
                    local_browser_fixture.advance_local(
                        store, Path(store.url.database), "pass", None,
                        bug_edit=dict(path=CLIENT_SOURCE if client_case else PRODUCTION,
                                      old=BAD_ALERT if client_case else BUGGY,
                                      new=GOOD_ALERT if client_case else CORRECT))
                    calls.put((case["choice"], "implementation"))
                else:
                    worker.tick(store)
        except Exception as error:
            errors.put(repr(error))

    actors = []
    for index in range(len(cases)):
        stopped = ctx.Event()
        with serve(create_app(store)) as origin:
            store.dispose()
            child = ctx.Process(target=advance, args=(stopped,), daemon=True)
            child.start()
            actors.append(child.pid)
            try:
                run = subprocess.run(["node", "e2e/bug_process_rehearsal.mjs", origin, str(path), str(index)],
                                     cwd=ROOT, env=environment(), capture_output=True, text=True, timeout=150)
                assert run.returncode == 0, run.stdout + run.stderr
            finally:
                stopped.set()
                child.join(10)
                if child.is_alive():
                    child.terminate()
                    child.join(5)
                assert errors.empty(), errors.get_nowait() if not errors.empty() else ""
                assert child.exitcode == 0
        with store.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "delete"
            assert len(connection.execute(select(model_attempts).where(
                model_attempts.c.purpose == "implementation")).all()) == index+1
    assert actors[0] != actors[1] != os.getpid()
    assert [calls.get(timeout=2) for _ in range(4)] == [
        (case["choice"], phase) for case in cases for phase in ("planning", "implementation")]
    assert not git(source, "status", "--porcelain").strip()
    assert (source / PRODUCTION).read_text() == BUGGY
    assert (source / CLIENT_SOURCE).read_text() == BAD_ALERT
