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
from test_verification_profiles import BUGGY, CORRECT, PRODUCTION, TEST, GOOD_TEST, git

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



def program_answer(client_case,session=False):
    from test_bug_workflow import CLIENT_TEST
    value=copy.deepcopy(response(client_case))
    plan=value['plan']
    plan['other_reads']=[]
    plan.pop('read_paths')
    if client_case:
        phases=[dict(description='Observe the actual notification before another event.',
            code="state.actual=nil; client.Notify=function(_,name)state.actual=name end;client.Deliver('Sam');return state.actual",
            expected='Sam',incorrect='Unknown')]
        phases += [dict(description='Observe the next event-driven notification.',
            code="state.actual=nil;client.FireEvent('WORKBENCH_FIXTURE_NAME','"+name+"');return state.actual",
            expected=name,incorrect='Unknown') for name in ('Lee','Pat')]
        if session:
            phases.append(dict(description='Recreate a fresh client without carrying the old runtime.',code="local old=client;restart_session({'FixtureDB'});return client~=old",expected=True,incorrect=False))
        setup=dict(kind='client-bootstrap',test_path=CLIENT_TEST,client='c',through_line=5)
    else:
        phases=[dict(description='Observe the actual exported name normalization.',
            code="return subject('"+text+"')",expected=expected,incorrect=incorrect)
            for text,expected,incorrect in [('  ALICE ','alice','ALICE'),(' BOB ','bob','BOB'),(' Lee ','lee','Lee')]]
        setup=dict(kind='module',source=PRODUCTION)
    plan['reproducer']=dict(description='The existing operation must produce the requested name.',setup=setup,phases=phases)
    return value


def regression_edit(client_case):
    """Controlled coding output, never an acceptance addon implementation."""
    if client_case:
        from test_bug_workflow import CLIENT_TEST
        old='assert(type("message")=="string")'
        return dict(path=CLIENT_TEST,old=old,new=old+'''\nlocal observed
c.Notify=function(_,name) observed=name end
c.Deliver('Sam'); assert(observed=='Sam')
c.FireEvent('WORKBENCH_FIXTURE_NAME','Lee'); assert(observed=='Lee')
c.FireEvent('WORKBENCH_FIXTURE_NAME','Pat'); assert(observed=='Pat')
''')
    old='assert(normalize("alice") == "alice")'
    return dict(path=TEST,old=old,new=old+'\n'+GOOD_TEST)

@pytest.mark.parametrize('case_provider',['local','codex'])
@pytest.mark.parametrize('reproduction_mode',['source-grounded-v2','source-grounded-lua-v1','source-grounded-intent-v1'])
def test_two_bugs_with_two_browsers_and_separate_process_restarts(
    store, installed, tmp_path, monkeypatch, reproduction_mode, case_provider,  # noqa: F811
):
    import local_browser_fixture
    from project_env import environment

    source = enable_bugs(monkeypatch)
    # Exercise the installed semantic mode, not only legacy one-pass planning.
    from test_everyday import write_policy
    preparation=Path(os.environ['WORKBENCH_PREPARATION_CONFIG'])
    settings=json.loads(preparation.read_text())
    settings['bug_plan_review']=reproduction_mode
    write_policy(preparation,settings)
    from test_bug_plan_review import large_reference
    from workbench import game_api_reference
    monkeypatch.setattr(game_api_reference,'collect',large_reference)
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
    if reproduction_mode=='source-grounded-intent-v1':
        # Normal test code uses the real fixture client's event registration;
        # do not overwrite that API with an unrelated second frame registry.
        alert_source=BAD_ALERT+event_source[event_source.index('local frame=CreateFrame()'):]+'-- bounded source paging fixture\n'*120
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
    intent_mode=reproduction_mode=='source-grounded-intent-v1'
    program_mode=reproduction_mode in {'source-grounded-lua-v1','source-grounded-intent-v1'}
    if intent_mode and not native:
        from workbench import bug_check_preparation
        from test_regression_evidence import ControlledPreparer
        monkeypatch.setattr(bug_check_preparation,'Runtime',ControlledPreparer)
    cases = []
    for index, client_case in enumerate([False, True]):
        request = report(client_case)
        if case_provider=='codex':
            request['local_planning']='codex-once'
        if client_case:
            request['description'] += ' It should refresh each time.'
        request.pop("request_id")
        request["lua_error"] = ""
        policy = json.loads(Path(os.environ["WORKBENCH_PREPARATION_CONFIG"]).read_text())
        packet = bug_investigation.context(request, "", policy)
        answer = program_answer(client_case,session=intent_mode) if program_mode else three_observations(client_case)
        if program_mode:
            from workbench.bug_program import compile as compile_program
            answer,_=compile_program(answer,packet['files'],bug_investigation.report_coverage(request))
        intent_plan={k:answer['plan'][k] for k in ('scope','read_paths','writable_paths','focused_test')}
        intent_plan['criteria']=[dict(id='observation-'+str(i+1),description='The actual operation produces '+str(o['expected'])+' for measured input '+str(i+1)+'.')
                                 for i,o in enumerate(answer['plan']['reproducer']['observations'])]
        intent_plan['risks']=[]
        cases.append(dict(choice="fixture-bug-" + str(index), request=request,
                          provider=case_provider, reason=None, model="pilot-qwen38-27b" if case_provider=="local" else "gpt-6.1-sol",
                          evidenceSha=digest(packet["files"]), writablePaths=answer["plan"]["writable_paths"],
                          reviewed=BugPlan.model_validate(answer["plan"]).model_dump(mode="json"),
                          client_case=client_case,intent=intent_plan,criteria=[c['description'] for c in intent_plan['criteria']],clarification=case_provider=="codex" and program_mode and not intent_mode and not client_case))
    policy = dict(base=git(source, "rev-parse", "HEAD").strip(), limits=LIMITS, cases=cases, reproduction_mode=reproduction_mode,
                  models=dict(local="pilot-qwen38-27b", codex="gpt-6.1-sol"),
                  budget=TaskBudget(activate_once=True, offer_id="00000000-0000-0000-0000-000000000000",
                                    offer_fingerprint="0"*64).model_dump(mode="json"),
                  note="Controlled process rehearsal, no owner live authority")
    path = tmp_path / "bug-browser-policy.json"
    path.write_text(json.dumps(policy))
    ctx = multiprocessing.get_context("fork")
    calls, errors = ctx.Queue(), ctx.Queue()

    def answer_for(packet,schema):
        from workbench.bug_plan_review import ReviewResponse
        from workbench.bug_investigation_stage import InvestigationResponse
        from workbench.bug_intent import Response as IntentResponse
        scoping=schema is IntentResponse
        preparing=bool(packet.get('approved_intent'))
        reviewing=schema is ReviewResponse
        investigating=schema is InvestigationResponse
        assert scoping or reviewing or investigating or schema is bug_investigation.BugPlanningResponse
        assert 24000 < len(json.dumps(packet).encode()) <= 196608
        conflict = packet['report']['title'].endswith(' · controlled conflicting outcomes')
        fault = conflict or packet['report']['title'].endswith(' · controlled malformed response')
        case = cases[0] if fault else next(c for c in cases if c["request"]["title"] == packet["report"]["title"])
        answer = program_answer(case['client_case'],session=intent_mode) if program_mode else three_observations(case['client_case'])
        if investigating:
            finding='Controlled committed source diagnosis, no approval or execution.'
            if conflict:
                answer=dict(finding=finding, draft=dict(question='A question.',limitation='Conflicting outcome.'))
            elif fault:
                answer=dict(finding='PRIVATE_REJECTED_CONTENT'*30,draft=dict(question='A specific question.'))
            elif case.get('clarification') and 'Technical context:' not in packet.get('owner_reply',''):
                answer=dict(finding=finding,draft=dict(question='Should normalization happen each time?'))
            elif ((case['client_case'] and len([w for w in packet.get('source_memory', {}).get('read_windows', [])
                    if w['path'] == CLIENT_SOURCE]) < 2) or (not case['client_case'] and
                    not packet.get('inspection_result', {}).get('search_matches'))):
                read = (dict(path=CLIENT_SOURCE,start=1,end=9999) if case['client_case']
                        else dict(query='return'))
                answer=dict(finding=finding,draft=dict(inspection=[read]))
            else:
                answer=dict(finding=finding,draft=dict(diagnosis=dict(
                    cause='The inspected implementation produces the wrong name for the reported operation.',
                    evidence_paths=[CLIENT_SOURCE if case['client_case'] else PRODUCTION])))
        elif scoping:
            answer=dict(finding='The reported behavior needs a bounded source repair.',plan=case['intent'])
        elif preparing and not reviewing:
            if intent_mode and case_provider=='local' and case['client_case']:
                with store.connect() as connection:
                    producer=connection.execute(select(planning_jobs.c.payload).where(planning_jobs.c.kind=='behavioral_preparation',planning_jobs.c.status=='running')).scalar_one()
                if not producer.get('preparation_round_id'):
                    return dict(finding='The controlled checker needs a renewed supported construction operation.',draft=dict(limitation='Controlled terminal check construction limitation.'))
            if not packet.get('sandbox_feedback') and not packet.get('self_review'):
                answer['plan']['reproducer']['phases'][0]['code']='return state.nonexistent()'
        elif reviewing:
            rejected = program_mode and 'BOGUS_INPUT' in packet['candidate_plan']['plan']['reproducer']['phases'][0]['code']
            answer=dict(review_binding=packet['review_binding'],ready=not rejected,
                issues=[dict(category='observation',explanation='The input was changed but the expected actual name was not; construct the correct observation from the reported operation.')] if rejected else [])
        elif conflict:
            answer['question'] = 'A controlled extra question conflicts with the plan.'
        elif fault:
            answer['finding'] = 'PRIVATE_REJECTED_CONTENT' * 30
        elif 'inspection_result' not in packet:
            answer = dict(finding='Inspect the exact committed implementation before proposing its check.',
                plan=None, question=None, limitation=None,
                inspection=[dict(path=CLIENT_SOURCE, start=1, end=9999) if case['client_case']
                            else dict(query='return')])
        elif case['client_case'] and len(packet.get('source_memory', {}).get('read_windows', [])) == 1:
            answer = dict(finding='Read the remaining committed source in the same bounded range.',
                plan=None,question=None,limitation=None,
                inspection=[dict(path=CLIENT_SOURCE,start=1,end=9999)])
        elif not packet.get('validation_feedback') and not packet.get('self_review'):
            # Exercise the actual cross-process correction path: a completed
            # single-plan candidate omitted its load and cannot be approved.
            if program_mode:
                answer['plan']['reproducer']['phases'][0]['code']='function incomplete('
            else:
                for step in answer['plan']['reproducer']['steps']:
                    if step['op'] == 'load':
                        step.clear()
                        step.update(op='table', path=['unused'])
        elif not fault and not program_mode:
            answer = wire_from_data(answer)
        if program_mode and not scoping and not preparing and not reviewing and not investigating and not fault:
            if packet.get('self_review'):
                assert 'previous_candidate' not in packet['self_review']
            elif packet.get('validation_feedback'):
                phase=answer['plan']['reproducer']['phases'][0]
                phase['code']=phase['code'].replace("'Sam'", "'BOGUS_INPUT'").replace('ALICE', 'BOGUS_INPUT')
        outcome = answer if reviewing or investigating else dict(finding=answer['finding'], draft={key:answer[key]
            for key in ('plan', 'question', 'limitation', 'inspection') if answer.get(key) is not None})
        calls.put(('fixture-conflict' if conflict else 'fixture-fault' if fault else case["choice"], "self-review" if reviewing else "diagnosis" if investigating else "planning"))
        return outcome

    def scripted(self, schema, encoded, key_path, expected_model):
        packet=json.loads(encoded)
        answer=answer_for(packet,schema)
        class Transport:
            seconds = 120
            def models(self, _): return expected_model
            def context_budget(self, _, body):
                return dict(prompt_tokens=8000, context_tokens=32768)
            def read(self, request, limit):
                program=packet.get('coverage',{}).get('reproducer_format')=='lua-phases-v1'
                assert len(request.data)<=(131072 if program else 65536) and limit==(65536 if program else 24000)
                return json.dumps(dict(id='controlled-response', model=expected_model,
                    usage=dict(prompt_tokens=40, completion_tokens=10, total_tokens=50),
                    choices=[dict(finish_reason='stop', message=dict(content=json.dumps(answer),
                        reasoning_content='CONTROLLED_NATIVE_REASONING_NOT_A_MODEL'))])).encode()
        return self._infer(Transport(), 'fixture-no-credential', schema, json.dumps(packet), expected_model=expected_model)

    def controlled_codex(model,effort,prompt,schema,stop,**kwargs):
        assert model=='gpt-6.1-sol' and not stop() and kwargs.get('input_limit')==codex_provider.MAX_BUG_INPUT
        if '\nUntrusted task data:\n' in prompt:
            packet=json.loads(prompt.split('\nUntrusted task data:\n',1)[1])
            from workbench.bug_plan_review import ReviewResponse
            from workbench.bug_investigation_stage import InvestigationResponse
            from workbench.bug_intent import Response as IntentResponse
            actual_schema=IntentResponse if schema.get('title')=='BugIntentResponse' else ReviewResponse if schema.get('title')=='ReviewResponse' else InvestigationResponse if schema.get('title')=='InvestigationResponse' else bug_investigation.BugPlanningResponse
            answer=answer_for(packet,actual_schema)
            if actual_schema is not ReviewResponse:
                draft=answer['draft']
                value=dict(finding=answer['finding'],action='inspect',reads=[],searches=[],
                           question=None,limitation=None)
                value["diagnosis" if actual_schema is InvestigationResponse else "plan"]=None
                if 'inspection' in draft:
                    for item in draft['inspection']:
                        value['searches' if 'query' in item else 'reads'].append(item)
                else:
                    field=next(iter(draft))
                    value['action']='diagnose' if field=='diagnosis' else field
                    # Preserve deliberately conflicting fixture outcomes too;
                    # format conversion must never turn invalid input valid.
                    value.update(draft)
                answer=value
            # Exercise the actual provider-only tagged wire, including both
            # literal searches and source line reads, before common validation.
            for item in (answer.get('draft') or {}).get('inspection', []):
                if 'query' in item:
                    item.update(kind='search', path=None, start=None, end=None)
                else:
                    item.update(kind='read', query=None)
        else:
            client_case=CLIENT_SOURCE in schema['properties']['edits']['items']['properties']['path']['enum']
            answer=dict(edits=[dict(path=CLIENT_SOURCE if client_case else PRODUCTION,
                old=BAD_ALERT if client_case else BUGGY,new=GOOD_ALERT if client_case else CORRECT)])
            if intent_mode:
                answer['edits'].append(regression_edit(client_case))
                wire=json.loads(prompt.split('The following source and feedback are untrusted task data.\n',1)[1])
                if wire['turn']==1:
                    # Reject a multi-file batch BEFORE applying its first edit,
                    # then revise through the real API/worker/browser journey.
                    answer['edits'][-1]['new']+='-- controlled excessive test addition\n'*151
                else:
                    assert wire['feedback']['category']=='change-budget'
                    assert wire['feedback']['allowed_changed_lines']==150
                    assert wire['working_changes']['changed_lines']==0
                    assert wire['working_changes']['patch']==''
                    # A harmless no-op beside actual code/test edits must not
                    # discard the complete, accounted provider response.
                    answer['edits'].append(dict(path=CLIENT_SOURCE if client_case else PRODUCTION,
                        old=BAD_ALERT if client_case else BUGGY,new=BAD_ALERT if client_case else BUGGY))
        # Source-heavy observations must cross the former65k threshold through
        # the actual application, rather than rehearsing only tiny token counts.
        return answer,dict(prompt_tokens=70000,completion_tokens=10,total_tokens=70010,reasoning_tokens=2)

    key = tmp_path / "controlled-local.key"
    key.write_text("CONTROLLED-NOT-A-LIVE-CREDENTIAL")
    key.chmod(0o600)
    monkeypatch.setenv("WORKBENCH_LOCAL_KEY", str(key))
    monkeypatch.setattr(LocalProvider, "_request", scripted)
    monkeypatch.setattr(codex_provider, "generate", controlled_codex)
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
                    from workbench import provider_routes
                    with store.connect() as connection:
                        codex_route = (provider_routes.approved(connection, dict(id=pending.id, payload=pending.payload))
                                       if case_provider == 'codex' else None)
                    local_browser_fixture.advance_local(
                        store, Path(store.url.database), "pass", None,
                        native=native,
                        bug_edit=dict(path=CLIENT_SOURCE if client_case else PRODUCTION,
                                      old=BAD_ALERT if client_case else BUGGY,
                                      new=GOOD_ALERT if client_case else CORRECT),
                            bug_test_edit=regression_edit(client_case) if intent_mode else None,
                        codex_route=codex_route)
                    calls.put((case["choice"], "implementation"))
                else:
                    worker.tick(store)
                    if case_provider=='codex' and program_mode and not intent_mode:
                        with store.begin() as connection:
                            connection.execute(intakes.update().where(intakes.c.revision==2,
                                intakes.c.status=='waiting_for_information').values(status='failed'))
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
    if intent_mode:
        from queue import Empty
        observed=[]
        while True:
            try:
                observed.append(calls.get(timeout=.2))
            except Empty:
                break
        assert observed[:2]==[('fixture-fault','diagnosis'),('fixture-conflict','diagnosis')]
        for case in cases:
            events=[r[1] for r in observed if r[0]==case['choice']]
            assert events.count('implementation')==1
            assert events.count('self-review')==0 and events.count('planning')==1
            assert events[-1]=='implementation'
        with store.connect() as c:
            assert not c.execute(select(model_attempts.c.id).where(model_attempts.c.purpose=='check_preparation')).first()
    else:
        expected = [('fixture-fault', 'diagnosis'), ('fixture-conflict', 'diagnosis')]
        for case in cases:
            expected += [(case['choice'], 'diagnosis')]*((3 if case['client_case'] else 2)+2*int(case.get('clarification',False)))
            expected += [(case['choice'], 'planning')]*2
            expected.append((case['choice'], 'self-review'))
            if program_mode:
                expected += [(case['choice'], 'planning'),(case['choice'], 'self-review')]
            expected.append((case['choice'], 'implementation'))
        assert [calls.get(timeout=2) for _ in expected] == expected
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
    prefix=['schema','request-schema']+(['request-limits'] if program_mode and not intent_mode else [])+['wire-received','envelope'] if case_provider=='local' else ['schema','request-schema','codex-wire-schema','codex-request-budget','private-proposed-data','provider-result']
    assert [row['phase'] for row in rows][:len(prefix)] == prefix
    assert len(rows[1]['schema_sha256']) == 64 and rows[1]['corrective'] is False
    if case_provider=='codex':
        assert rows[2]['codec']=='flat-investigation-v2' and len(rows[2]['schema_sha256'])==64
    assert rows[-1]['phase'] == 'validation-rejected'
    issue = rows[-1]['errors'][0]
    assert issue['path'] == ['finding'] and issue['expected']['max_length'] == 450
    assert issue['observed']['type'] == 'str'
    assert 'PRIVATE_REJECTED_CONTENT' not in evidence.read_text()
    assert 'CONTROLLED_NATIVE_REASONING_NOT_A_MODEL' not in evidence.read_text()
    assert (any(row['phase']=='reasoning-channel' and row['present'] and not row['retained'] for row in rows) if case_provider=='local' else any(row['phase']=='provider-result' and row['provider']=='codex' for row in rows))
    with store.connect() as c:
        payload = c.execute(select(planning_jobs.c.payload).join(intakes).where(
            intakes.c.payload['title'].as_string() == cases[1]['request']['title'],
            planning_jobs.c.kind == 'planning')).scalar_one()
    [page, next_page] = payload['bug_inspection_packet']['inspection_windows']
    if case_provider=='codex':
        # The larger Codex page contains this complete committed fixture.
        assert page['through_line']==len(alert_source.splitlines()) and page['next_line'] is None
        assert next_page['line']==1 and next_page['cached_prefix_through_line'] is None
    else:
        assert page['through_line']==80 and page['next_line']==81
        assert next_page['line']==81 and next_page['cached_prefix_through_line']==80
    assert page['requested_through_line'] == 9999 and page['coverage'] == 'partial requested committed lines'
    assert next_page['requested_start_line'] == 1 and next_page['next_line'] is None
    assert not git(source, "status", "--porcelain").strip()
    assert (source / PRODUCTION).read_text() == BUGGY
    assert (source / CLIENT_SOURCE).read_text() == alert_source
