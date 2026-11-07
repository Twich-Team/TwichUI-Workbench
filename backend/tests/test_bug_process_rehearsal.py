"""Two ordinary bug journeys across API/worker process restarts, with scripted models."""
import copy
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import traceback

import pytest
from sqlalchemy import select

from workbench import bug_investigation, codex_provider, trusted_preparation, worker
from workbench.bug_evidence import BugPlan
from workbench.api import create_app
from workbench.db import intakes, model_attempts, planning_jobs, proposal_decisions, task_sessions as sessions
from workbench.planning_provider import LocalProvider
from workbench.real_execution import LIMITS
from workbench.schemas import TaskBudget
from workbench.visual_scope import digest
from test_acceptance_browser import serve
from test_bug_workflow import (
    BAD_ALERT, GOOD_ALERT, CLIENT_SOURCE, enable_bugs, report, response,
)
from test_everyday import installed  # noqa: F401
from test_bug_wire import wire_from_data
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
    if client_case:
        # Exercise native argument-reference interpretation across processes.
        # These are ordinary fixture inputs, never an acceptance addon answer.
        steps, mapping = [], {}
        for index, step in enumerate(fixture['steps'], 1):
            if step['op']=='call' and step.get('path')==['Deliver']:
                steps.append(dict(op='set',path=['FixtureName'],value=step['args'][0]))
                step=dict(step,path=['FireEvent'],args=['WORKBENCH_FIXTURE_NAME',dict(ref=['FixtureName'])])
            steps.append(step)
            mapping[index]=len(steps)
        fixture['steps']=steps
        for observation in fixture['observations']:
            observation['after_step']=mapping[observation['after_step']]
    return value


def test_two_bugs_with_two_browsers_and_separate_process_restarts(
    store, installed, tmp_path, monkeypatch,  # noqa: F811
):
    import local_browser_fixture
    from project_env import environment

    source = enable_bugs(monkeypatch)
    harness=source/'tests/harness.lua'
    harness.write_text(harness.read_text().replace('  return client', """
  local frames={}
  client.CreateFrame=function()
    local f={events={},scripts={}}
    function f:RegisterEvent(e) self.events[e]=true end
    function f:SetScript(n,fn) self.scripts[n]=fn end
    frames[#frames+1]=f
    return f
  end
  client.FireEvent=function(e,...)
    for _,f in ipairs(frames) do
      if f.events[e] and f.scripts.OnEvent then f.scripts.OnEvent(f,e,...) end
    end
  end
  return client"""))
    event_source = """
CreateFrame=function()
 local f={scripts={}}
 function f:RegisterEvent() end
 function f:UnregisterEvent() end
 function f:UnregisterAllEvents() end
 function f:SetScript(n,fn) self.scripts[n]=fn end
 function f:HookScript() end
 return f
end
local frame=CreateFrame()
frame:RegisterEvent('WORKBENCH_FIXTURE_NAME')
frame:SetScript('OnEvent',function(_,_,name) Deliver(name) end)
"""
    alert_source = BAD_ALERT + event_source + '-- bounded source paging fixture\n'*120
    (source / CLIENT_SOURCE).write_text(alert_source)
    git(source, 'add', '.')
    git(source, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@invalid',
        'commit', '-qm', 'Controlled long source read')
    native = os.environ.get("WB_PROCESS_NATIVE") == "1"
    if native:
        from workbench.real_workspace import Runtime
        monkeypatch.setattr(trusted_preparation, "Runtime", Runtime)
    monkeypatch.setenv("WORKBENCH_STATIC_ROOT", str(ROOT / "frontend/dist"))
    monkeypatch.setattr(local_browser_fixture, "install_offline_guard", lambda: None)
    cases = []
    for index, client_case in enumerate([False, True]):
        request = report(client_case)
        if client_case:
            request['description'] += ' It should refresh each time.'
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
        conflict = packet['report']['title'].endswith(' · controlled conflicting outcomes')
        fault = conflict or packet['report']['title'].endswith(' · controlled malformed response')
        case = cases[0] if fault else next(c for c in cases if c["request"]["title"] == packet["report"]["title"])
        answer = three_observations(case['client_case'])
        if conflict:
            answer['question'] = 'A controlled extra question conflicts with the plan.'
        elif fault:
            answer['finding'] = 'PRIVATE_REJECTED_CONTENT' * 30
        elif 'inspection_result' not in packet:
            answer = dict(finding='Inspect the exact committed implementation before proposing its check.',
                plan=None, question=None, limitation=None,
                inspection=[dict(path=CLIENT_SOURCE, start=1, end=9999) if case['client_case']
                            else dict(query='return')])
        elif not packet.get('validation_feedback'):
            # Exercise the actual cross-process correction path: a completed
            # single-plan candidate omitted its load and cannot be approved.
            for step in answer['plan']['reproducer']['steps']:
                if step['op'] == 'load':
                    step.clear()
                    step.update(op='table', path=['unused'])
        elif not fault:
            answer = wire_from_data(answer)
        class Transport:
            seconds = 120
            def models(self, _): return expected_model
            def read(self, request, limit):
                assert len(request.data) < 24000 and limit == 24000
                outcome = dict(finding=answer['finding'], draft={key:answer[key]
                    for key in ('plan', 'question', 'limitation', 'inspection') if answer.get(key) is not None})
                return json.dumps(dict(id='controlled-response', model=expected_model,
                    usage=dict(prompt_tokens=40, completion_tokens=10, total_tokens=50),
                    choices=[dict(finish_reason='stop', message=dict(content=json.dumps(outcome),
                        reasoning_content='CONTROLLED_NATIVE_REASONING_NOT_A_MODEL'))])).encode()
        value = self._infer(Transport(), 'fixture-no-credential', schema, json.dumps(packet), expected_model=expected_model)
        calls.put(('fixture-conflict' if conflict else 'fixture-fault' if fault else case["choice"], "planning"))
        return value

    monkeypatch.setattr(LocalProvider, "generate", scripted)
    # No real connector is used. The real browser gate observes the explicitly
    # controlled availability that this fixture declared.
    monkeypatch.setattr(codex_provider, "connector_path", lambda: tmp_path / "controlled.sock")
    monkeypatch.setenv("WORKBENCH_CODEX_SOCKET", str(tmp_path / "controlled.sock"))

    def advance(stopped, index):
        try:
            if native:
                group = Path(os.environ["WORKBENCH_CG_ROOT"]) / f"workbench-real-rehearsal-worker-{index}"
                group.mkdir()
                for name, value in {"memory.max":str(6 << 30), "memory.swap.max":"0",
                                    "pids.max":"128", "cpu.max":"200000 100000"}.items():
                    (group / name).write_text(value)
                (group / "main").mkdir()
                (group / "main/cgroup.procs").write_text(str(os.getpid()))
                (group / "cgroup.subtree_control").write_text("+cpu +memory +pids")
                os.environ["WORKBENCH_CG_ROOT"] = str(group)
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
                        native=native,
                        bug_edit=dict(path=CLIENT_SOURCE if client_case else PRODUCTION,
                                      old=BAD_ALERT if client_case else BUGGY,
                                      new=GOOD_ALERT if client_case else CORRECT))
                    calls.put((case["choice"], "implementation"))
                else:
                    worker.tick(store)
        except Exception:
            errors.put(traceback.format_exc(limit=12))

    actors = []
    for index in range(len(cases)):
        stopped = ctx.Event()
        with serve(create_app(store)) as origin:
            store.dispose()
            child = ctx.Process(target=advance, args=(stopped, index), daemon=True)
            child.start()
            actors.append(child.pid)
            try:
                if index == 0:
                    for failure in ('fault', 'conflict'):
                        fault = subprocess.run(["node", "e2e/bug_process_rehearsal.mjs", origin, str(path), failure],
                                               cwd=ROOT, env=environment(), capture_output=True, text=True, timeout=40)
                        assert fault.returncode == 0, fault.stdout + fault.stderr
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
                if native:
                    group = Path(os.environ["WORKBENCH_CG_ROOT"]) / f"workbench-real-rehearsal-worker-{index}"
                    assert not (group / "main/cgroup.procs").read_text().strip()
                    (group / "main").rmdir()
                    group.rmdir()
        with store.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "delete"
            assert len(connection.execute(select(model_attempts).where(
                model_attempts.c.purpose == "implementation")).all()) == index+1
    assert actors[0] != actors[1] != os.getpid()
    assert [calls.get(timeout=2) for _ in range(10)] == [('fixture-fault', 'planning'), ('fixture-conflict', 'planning'), *[
        (case["choice"], phase) for case in cases for phase in ("planning", "planning", "planning", "implementation")]]
    with store.connect() as c:
        failures = c.execute(select(intakes.c.id, intakes.c.payload).where(intakes.c.status == 'failed')).all()
        assert len(failures) == 2
        failed = next(row.id for row in failures if row.payload['title'].endswith(' · controlled malformed response'))
        attempt = c.execute(select(model_attempts).where(model_attempts.c.task_id == failed)).mappings().one()
        assert attempt['outcome'] == 'failed' and attempt['finished_at']
        assert attempt['usage']['values']['completion_tokens'] == 10
        job = c.execute(select(planning_jobs.c.payload).where(planning_jobs.c.intake_id == failed)).scalar_one()
    evidence = tmp_path/'artifacts/runs'/('planning-evidence-'+job['diagnostics']['evidence_id'])/'events.jsonl'
    rows = [json.loads(row) for row in evidence.read_text().splitlines()]
    assert [row['phase'] for row in rows][:4] == ['schema', 'request-schema', 'wire-received', 'envelope']
    assert len(rows[1]['schema_sha256']) == 64 and rows[1]['corrective'] is False
    assert rows[-1]['phase'] == 'validation-rejected'
    issue = rows[-1]['errors'][0]
    assert issue['path'] == ['finding'] and issue['expected']['max_length'] == 450
    assert issue['observed']['type'] == 'str'
    assert 'PRIVATE_REJECTED_CONTENT' not in evidence.read_text()
    assert 'CONTROLLED_NATIVE_REASONING_NOT_A_MODEL' not in evidence.read_text()
    assert any(row['phase']=='reasoning-channel' and row['present'] and not row['retained'] for row in rows)
    with store.connect() as c:
        payload = c.execute(select(planning_jobs.c.payload).join(intakes).where(
            intakes.c.payload['title'].as_string() == cases[1]['request']['title'],
            planning_jobs.c.kind == 'planning')).scalar_one()
    [page] = payload['bug_inspection_packet']['inspection_windows']
    assert page['through_line'] == 80 and page['next_line'] == 81
    assert page['requested_through_line'] == 9999 and page['coverage'] == 'partial requested committed lines'
    assert not git(source, "status", "--porcelain").strip()
    assert (source / PRODUCTION).read_text() == BUGGY
    assert (source / CLIENT_SOURCE).read_text() == alert_source
