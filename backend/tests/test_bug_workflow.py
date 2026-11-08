"""Ordinary bug intake, reviewed reproduction and worker dispatch with controlled providers."""
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from workbench import bug_evidence, intake, real_execution, worker
from workbench.db import engine_for, model_attempts, real_execution_grants, real_publications
from test_everyday import installed, save_codex_policy, write_policy  # noqa: F401 - pytest fixture
from test_verification_profiles import BUGGY, CORRECT, PRODUCTION, TEST, git

CLIENT_SOURCE = '!!!TwichUI/alerts.lua'
CLIENT_TEST = 'tests/test_alerts.lua'
BAD_ALERT = 'function Deliver(name) Notify("online", "Unknown") end\n'
GOOD_ALERT = 'function Deliver(name) Notify("online", name) end\n'


def enable_bugs(monkeypatch):
    path = Path(os.environ['WORKBENCH_PREPARATION_CONFIG'])
    policy = json.loads(path.read_text())
    policy['allowed_classes'].append('lua-offline-bugfix')
    write_policy(path, policy)
    root = Path(os.environ['WORKBENCH_RESEARCH_ROOT'])
    (root / PRODUCTION).write_text(BUGGY)
    (root / CLIENT_SOURCE).write_text(BAD_ALERT)
    (root / 'tests/harness.lua').write_text('''
function MakeClient()
  local client = setmetatable({}, {__index=_G})
  local chunk = assert(loadfile(ROOT .. 'normalize.lua'))
  setfenv(chunk, client)
  client.Normalize = chunk()
  return client
end
''')
    (root / 'tests/test_client.lua').write_text('assert(1+1==2)\n')
    (root / CLIENT_TEST).write_text('''dofile(TESTS .. "harness.lua")
local c = MakeClient()
local chunk = assert(loadfile(ROOT .. "alerts.lua"))
setfenv(chunk, c)
chunk()

assert(type("message")=="string")
''')
    runner = root / 'tests/run.sh'
    runner.write_text(runner.read_text().replace('5 passed', '6 passed')
                      .replace('TESTS="./";', 'TESTS="./";ROOT="../!!!TwichUI/";'))
    git(root, 'add', '.')
    git(root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@invalid',
        'commit', '-qm', 'Disposable controlled behavior cases')
    return root


def report(client_case=False):
    title = 'Wrong name in the online alert' if client_case else 'Name normalization leaves uppercase letters'
    description = ('Delivering an online alert for Sam shows Unknown instead of Sam.'
                   if client_case else 'Entering ALICE with surrounding spaces returns ALICE. It should return alice.')
    return dict(request_id=str(uuid4()), title=title, description=description,
                kind='issue', visual_requirement='nonvisual', local_planning='qwen-once')


def response(client_case=False, *, already_works=False, bootstrap=False):
    path, focused = (CLIENT_SOURCE, CLIENT_TEST) if client_case else (PRODUCTION, TEST)
    setup = [dict(op='capture', path=['Notify'])] if client_case else []
    call = dict(op='call', path=['Deliver'], args=['Sam']) if client_case else dict(op='call', args=['  ALICE '])
    steps = [*setup, dict(op='load', source=path), call]
    if client_case:
        steps += [*setup, dict(op='call', path=['Deliver'], args=['Lee'])]
    if bootstrap:
        steps = [step for step in steps if step['op'] != 'load']
    expected, incorrect = ('Sam', 'Unknown') if client_case else ('alice', 'ALICE')
    return dict(finding='The relevant committed Lua code and tests can be checked with an offline fixture.',
        question=None, limitation=None,
        plan=dict(scope='Correct the value produced for the reported operation.',
            read_paths=[path, focused], writable_paths=[path, focused], focused_test=focused,
            reproducer=dict(description='The operation should produce the requested name.',
                fixture='client' if client_case else 'module',
                steps=steps, bootstrap=dict(client='c', through_line=5) if bootstrap else None,
                observations=[dict(after_step=2 if bootstrap or not client_case else 3,
                    source='captured' if client_case else 'return', path=[2] if client_case else [1],
                    expected=incorrect if already_works else expected,
                    incorrect=expected if already_works else incorrect),
                    *([dict(after_step=4 if bootstrap else 5, source='captured', path=[2], expected='Lee',
                            incorrect='Unknown')] if client_case else [])])))


class BugPlanner:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []
        self.diagnostics = {}

    def generate(self, packet, schema):
        assert schema is bug_evidence.BugPlanningResponse
        assert not any(f['path'] in {'tests/harness.lua', 'tests/run.sh'} for f in packet['files'])
        self.calls.append(packet)
        return self.answer, 'controlled-bug-planner', 'demo'


@pytest.mark.usefixtures('installed')
def test_invalid_bug_plan_retains_safe_schema_diagnostics_without_replay(
    client, headers, store, monkeypatch,
):
    enable_bugs(monkeypatch)
    item = client.post('/api/intake', headers=headers, json=report()).json()
    private_value = 'PRIVATE_PROVIDER_CONTENT'
    private_key = 'PRIVATE_PROVIDER_KEY'
    answer = {**response(), 'finding': private_value * 40, private_key: private_value}
    planner = BugPlanner(answer)
    planner.diagnostics = dict(generation_state='finished', output_bytes=3300)
    assert worker.tick(store, planning_adapter=planner)
    result = client.get('/api/intake/' + item['id']).json()
    assert result['status'] == 'failed'
    assert result['proposals'] == [] and result['tasks'] == []
    diagnostics = result['jobs'][0]['diagnostics']
    assert diagnostics['generation_state'] == 'finished'
    assert diagnostics['category'] == 'schema_validation'
    assert diagnostics['validation_errors'] == 2
    assert diagnostics['validation_locations'] == (
        'finding:string_too_long; unrecognized:extra_forbidden')
    assert len(diagnostics['validation_schema_sha256']) == 64
    assert private_value not in json.dumps(result) and private_key not in json.dumps(result)
    assert not worker.tick(store, planning_adapter=planner)
    assert len(planner.calls) == 1


def test_bug_schema_diagnostics_bound_unknown_locations_and_ignore_error_messages():
    from pydantic import ValidationError

    errors = [dict(type='value_error', loc=('PRIVATE_KEY', i, 'PRIVATE_NESTED'),
                   input='PRIVATE_INPUT', ctx={'error': ValueError('PRIVATE_MESSAGE')})
              for i in range(10)]
    error = ValidationError.from_exception_data('PRIVATE_TITLE', errors)
    diagnostics = bug_evidence.validation_diagnostics(error)
    assert diagnostics['validation_errors'] == 10
    assert len(diagnostics['validation_locations'].split('; ')) == 4
    assert len(diagnostics['validation_locations']) <= 600
    assert 'PRIVATE' not in json.dumps(diagnostics)


def approve(client, headers, item):
    plan = item['proposals'][0]
    endpoint = f"/api/intake/{item['id']}/decisions"
    command = dict(command_id=str(uuid4()), binding=plan['binding'], action='approve',
                   proposal_id=plan['id'], scope_revision=plan['scope_revision'])
    assert client.post(endpoint, headers=headers, json=command).status_code == 422
    command['expectation_review'] = plan['task_contract']['bug_evidence']['review_digest']
    decision = client.post(endpoint, headers=headers, json=command)
    assert decision.status_code == 200, decision.text
    return decision.json()


def get_run_offer(client, item):
    path = '/api/intake/' + item['id']
    task, = (t for t in client.get(path).json()['tasks'] if t['kind'] == 'task')
    endpoint = f"{path}/tasks/{task['id']}/run-approval"
    return endpoint, client.get(endpoint).json()


@pytest.mark.usefixtures('installed')
@pytest.mark.parametrize(('client_case', 'via_codex'), [(False, False), (True, False), (True, True)])
def test_ordinary_report_reproduces_and_returns_checked_patch(
    client, headers, store, monkeypatch, client_case, via_codex,
):
    root = enable_bugs(monkeypatch)
    assert client.get('/api/local-work').json()['bug_investigation']
    original = report(client_case)
    planner = BugPlanner(response(client_case))
    provider_calls = []
    if via_codex:
        from workbench import codex_provider

        save_codex_policy(store)
        original['local_planning'] = 'codex-once'
        monkeypatch.setattr(codex_provider, 'connector_path', lambda: Path('/controlled-connector'))

        def generate(model, effort, prompt, schema, stop, **kwargs):
            assert model == 'simulated-codex' and effort is None and not stop()
            provider_calls.append(prompt)
            usage = dict(total_tokens=120, prompt_tokens=100, completion_tokens=20, reasoning_tokens=0)
            if 'Untrusted task data:' in prompt:
                return response(client_case, bootstrap=True), usage
            assert schema['properties']['edits']['items']['properties']['path']['enum'] == [CLIENT_SOURCE, CLIENT_TEST]
            assert CLIENT_SOURCE in prompt and CLIENT_TEST in prompt
            return dict(edits=[dict(path=CLIENT_SOURCE, old=BAD_ALERT, new=GOOD_ALERT),
                dict(path=CLIENT_TEST, old='assert(type("message")=="string")',
                     new='assert(type("message")=="string")\nassert("existing regression" ~= "")')]), usage

        monkeypatch.setattr(codex_provider, 'generate', generate)
    item = client.post('/api/intake', headers=headers, json=original).json()
    assert client.post('/api/intake', headers=headers, json=original).json()['id'] == item['id']
    assert worker.tick(store, planning_adapter=None if via_codex else planner)
    item = client.get('/api/intake/' + item['id']).json()
    assert item['status'] == 'completed', item['jobs']
    plan = item['proposals'][0]['task_contract']
    assert plan['category'] == 'lua-offline-bugfix'
    production, focused = plan['writable_paths']
    if via_codex:
        assert production in provider_calls[0] and focused in provider_calls[0]
        assert plan['bug_evidence']['bootstrap_source'].endswith('return c\n')
    else:
        assert {production, focused} <= {f['path'] for f in planner.calls[0]['files']}
    assert item['evidence'][0]['source_revision'] == plan['resolved_base']
    approve(client, headers, item)
    assert worker.tick(store)
    assert worker.tick(store)
    assert worker.tick(store)
    endpoint, offer = get_run_offer(client, item)
    assert offer['available'], offer
    assert offer['offer']['paths'] == [production, focused]
    with store.connect() as c:
        assert not c.execute(select(real_execution_grants)).first()
    command = dict(command_id=str(uuid4()), binding=offer['binding'],
                   scope_approval_id=offer['scope_approval_id'], run_budget=offer['offer']['budget'])
    saved = client.post(endpoint, headers=headers, json=command)
    assert saved.status_code == 200, saved.text
    assert client.post(endpoint, headers=headers, json=command).json() == saved.json()
    assert worker.tick(store)
    assert worker.tick(store)
    import local_browser_fixture

    monkeypatch.setattr(local_browser_fixture, 'install_offline_guard', lambda: None)
    old, new = (BAD_ALERT, GOOD_ALERT) if client_case else (BUGGY, CORRECT)
    codex_route = None
    if via_codex:
        from workbench.provider_routes import approved
        from workbench.db import intakes

        with store.connect() as c:
            codex_route = approved(c, intake.require(c, intakes, item['id']))
    local_browser_fixture.advance_local(store, Path(store.url.database), 'pass', planner,
                                       bug_edit=dict(path=production, old=old, new=new), codex_route=codex_route)
    if via_codex:
        assert len(provider_calls) == 2
    assert (root / production).read_text() == old
    assert not git(root, 'status', '--porcelain').strip()
    run, = client.get('/api/real-executions').json()['items']
    restarted = engine_for(store.url.database)
    try:
        review = real_execution.review(restarted, run['id'])
        assert review['behavior_checked'] == plan['acceptance']
        assert {f['path'] for f in review['result']['files']} == (
            {production, focused} if via_codex else {production})
        assert review['result']['verification'] == 'passed'
        assert not worker.tick(restarted)
        with restarted.connect() as c:
            assert not c.execute(select(real_publications)).first()
            assert len(c.execute(select(model_attempts).where(model_attempts.c.purpose == 'planning')).all()) == 1
    finally:
        restarted.dispose()


@pytest.mark.usefixtures('installed')
def test_original_pass_holds_without_an_execution_offer(client, headers, store, monkeypatch):
    enable_bugs(monkeypatch)
    item = client.post('/api/intake', headers=headers, json=report()).json()
    assert worker.tick(store, planning_adapter=BugPlanner(response(already_works=True)))
    item = client.get('/api/intake/' + item['id']).json()
    approve(client, headers, item)
    for _ in range(3):
        assert worker.tick(store)
    _, offer = get_run_offer(client, item)
    assert not offer['available'] and 'could not reproduce' in offer['message']


@pytest.mark.usefixtures('installed')
def test_followup_requires_explicit_bound_consent_and_one_settled_route(
    client, headers, store, monkeypatch,
):
    enable_bugs(monkeypatch)
    item = client.post('/api/intake', headers=headers, json=report()).json()
    planner = BugPlanner(dict(finding='I found relevant code.', plan=None,
                              question='Does this happen when you save the name?', limitation=None))
    from workbench.planning_provider import LocalProvider

    def generate(self, packet, schema, expected_model):
        result, _, _ = planner.generate(packet, schema)
        self.diagnostics = dict(generation_state='finished', accounting_state='complete',
            usage=dict(units='tokens', values=dict(prompt_tokens=40, completion_tokens=10, total_tokens=50)))
        return result, expected_model, 'local'

    monkeypatch.setattr(LocalProvider, 'generate', generate)
    assert worker.tick(store)
    item = client.get('/api/intake/' + item['id']).json()
    assert item['status'] == 'waiting_for_information', item['jobs']
    url = f"/api/intake/{item['id']}/clarification"
    reply = dict(command_id=str(uuid4()), revision=item['revision'], text='Yes, right after saving.')
    assert client.post(url, headers=headers, json=reply).status_code == 422
    reply['continue_investigation'] = True
    assert client.post(url, headers=headers, json=reply).status_code == 200
    assert client.post(url, headers=headers, json=reply).status_code == 200
    assert worker.tick(store)
    assert len(planner.calls) == 2
    assert not worker.tick(store)
    result = intake.view(store, item['id'])
    assert result['status'] == 'failed'
    assert client.post(url, headers=headers, json={**reply, 'command_id': str(uuid4())}).status_code == 409
