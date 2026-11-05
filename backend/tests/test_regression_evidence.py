"""Controlled model drafts + real Lua. No inference, confinement or real-run claim."""
import copy
import json
from uuid import uuid4

import pytest
from sqlalchemy import select

from workbench import planning_worker, regression_evidence as evidence
from workbench import task_budget, task_session, trusted_preparation
from workbench import verification_profiles as profiles
from workbench.db import backlog_items, planning_jobs, proposal_decisions, write_transaction
from workbench.real_workspace import Runtime, durable
from test_reviewed_offers import source, PreparedLuaRuntime, authority_counts
from test_verification_profiles import PRODUCTION, TEST, git


def scenario(**changes):
    return dict(id='capital-name', acceptance_index=0, test_path=TEST, fixture='module',
        source_paths=[PRODUCTION], target=[], args=['  ALICE '], observe=dict(expected='alice'), incorrect='ALICE', **changes)


def response(original, scenarios=None, unresolved=None, feedback='', base=None):
    return dict(input_sha256=evidence.input_binding(original, feedback, base, [TEST]),
        draft=dict(scenarios=scenarios if scenarios is not None else [scenario()], unresolved=unresolved or []),
        model='controlled-model-response-no-inference')


def request_and_plan(store, client, headers, tmp_path, monkeypatch, scenarios=None, modify=None):
    root = source(tmp_path, profiles.REGRESSION)
    if modify:
        modify(root)
        git(root, 'add', '.')
        git(root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@invalid', 'commit', '-qm', 'Controlled harness')
    monkeypatch.setenv('WORKBENCH_RESEARCH_ROOT', str(root))
    original = dict(request_id=str(uuid4()), kind='feature', title='Cover name normalization',
        description='Trim spaces and lowercase names, adding tests only.', visual_requirement='nonvisual',
        regression_tests=dict(test_paths=[TEST], acceptance=['A name with spaces and capital letters becomes lowercase.']))
    saved = client.post('/api/intake', headers=headers, json=original)
    assert saved.status_code == 200, saved.text
    identity = saved.json()['id']
    original = saved.json()['original']
    assert planning_worker.process_one(store, scenario_response=response(original, scenarios, base=git(root, 'rev-parse', 'HEAD').strip()))
    current = client.get('/api/intake/' + identity).json()
    return root, current, original


def approve(store, client, headers, current, *, confirm=True):
    proposal = current['proposals'][0]
    prepared = proposal['task_contract']['behavioral_evidence']
    payload = dict(command_id=str(uuid4()), proposal_id=proposal['id'], binding=proposal['binding'],
        scope_revision=proposal['scope_revision'], action='approve', note='Independent expectation review, controlled fixture.')
    if confirm:
        payload['expectation_review'] = prepared['review_digest']
    saved = client.post('/api/intake/' + current['id'] + '/decisions', headers=headers, json=payload)
    return saved, payload


def reviewed(store, client, headers, current):
    saved, payload = approve(store, client, headers, current)
    assert saved.status_code == 200, saved.text
    assert planning_worker.apply_one(store, command_id=payload['command_id'])
    assert planning_worker.process_one(store)
    with store.connect() as c:
        task = c.execute(select(backlog_items).where(backlog_items.c.intake_id == current['id'],
            backlog_items.c.kind == 'task')).mappings().one()
    return dict(intake_id=current['id'], task_id=task['id'], approval_id=payload['command_id'])


class ControlledPreparer(PreparedLuaRuntime):
    def check(self, argv, *args, **kwargs):
        if argv[:2] == ['python3', '-c']:
            # Deliberately controlled telemetry, never a confinement certificate.
            return dict(command=argv, status='pass', rc=0, out='CONTROLLED boundary only',
                        usage=dict(cgroup_removed=True, controlled_fixture=True))
        return super().check(argv, *args, **kwargs)


def test_ordinary_request_generated_package_reaches_reviewable_offer_without_custom_lua(
    store, client, headers, tmp_path, monkeypatch,
):
    root, current, _ = request_and_plan(store, client, headers, tmp_path, monkeypatch)
    prepared = current['proposals'][0]['task_contract']['behavioral_evidence']
    assert prepared['status'] == 'review_required' and not prepared['holds']
    assert prepared['origin'] == 'controlled-model-response-no-inference'
    assert prepared['execution_authorized'] is False
    denied, _ = approve(store, client, headers, current, confirm=False)
    assert denied.status_code == 422 and 'Independently confirm' in denied.text
    ref = reviewed(store, client, headers, current)
    before = authority_counts(store)
    output = tmp_path / 'generated'
    output.mkdir(mode=0o700)
    monkeypatch.setattr(trusted_preparation, 'Runtime', ControlledPreparer)
    selected = trusted_preparation.prepare_regression(store, ref, output,
        dict(name='Controlled author', email='fixture@invalid'))
    assert authority_counts(store) == before
    assert selected['snapshot']['specification']['verification_profile'] == profiles.REGRESSION
    assert trusted_preparation.register(store, ref, output) == selected
    runtime = Runtime(output)
    bundle = json.loads((output / 'profile-evidence.json').read_text())
    assert profiles.certify(runtime, bundle)['status'] == 'certified'
    assert {k: profiles.behavioral_outcome(v['checks'][-2], 'regression-scenarios')
            for k,v in bundle['controls'].items()} == profiles.requirements(profiles.REGRESSION)['controls']
    assert runtime.controls_valid()
    url = f'/api/intake/{ref["intake_id"]}/tasks/{ref["task_id"]}/run-approval'
    offer = client.get(url).json()
    assert offer['available'] and offer['offer']['checks']
    # Existing UI budget path is executable-capable subject to separately configured
    # runtime gates; this controlled test saves a nonactivating first budget only.
    budget = offer['offer']['budget']
    assert not budget.get('activate_once', False)
    saved = client.post(url, headers=headers, json=dict(command_id=str(uuid4()),
        scope_approval_id=ref['approval_id'], binding=offer['binding'], run_budget=budget))
    assert saved.status_code == 200, saved.text
    assert planning_worker.apply_one(store)
    assert authority_counts(store) == before
    assert git(root, 'status', '--porcelain') == ''
    # Same canonical provision owner consumes generated frozen controls again.
    with store.connect() as c:
        template = task_budget.preparation_inputs(c, selected)
    fresh = tmp_path / 'fresh'
    fresh.mkdir()
    durable(fresh / 'recipe.json', json.dumps(template['recipe']).encode())
    durable(fresh / 'recipe-oracle.lua', template['oracle'].encode())
    again = ControlledPreparer(fresh)
    task_session.provision(again, template)
    assert again.controls_valid()


@pytest.mark.parametrize('kind', ['client-return', 'client-state'])
def test_generated_harness_scenarios_check_returns_and_state_without_handwritten_controls(
    store, client, headers, tmp_path, monkeypatch, kind,
):
    def fixture(root):
        runner = root / 'tests/run.sh'
        runner.write_text(runner.read_text().replace('require("bit");', 'require("bit");TESTS="./";'))
        (root / 'tests/harness.lua').write_text('''function MakeClient(name, addons)
 local normalize = dofile('../!!!TwichUI/normalize.lua')
 local c = {API={}}
 c.API.Normalize = function(value) c.last=normalize(value); return c.last end
 return c
end
''')
        (root / TEST).write_text('''dofile(TESTS .. 'harness.lua')
local c=MakeClient('Base', {'!!!TwichUI'})
assert(c.API.Normalize('alice') == 'alice')
''')
    s = scenario()
    s.update(fixture='client', target=['API', 'Normalize'],
        setup=[dict(op='set', path=['last'], value='before')],
        observe=dict(path=['last'] if kind == 'client-state' else [], expected='alice'))
    _, current, _ = request_and_plan(store, client, headers, tmp_path, monkeypatch, [s], modify=fixture)
    ref = reviewed(store, client, headers, current)
    output = tmp_path / 'generated'
    output.mkdir(mode=0o700)
    monkeypatch.setattr(trusted_preparation, 'Runtime', ControlledPreparer)
    offer = trusted_preparation.prepare_regression(store, ref, output,
        dict(name='Controlled author', email='fixture@invalid'))
    assert offer['snapshot']['certified_preparation']['certificate']['status'] == 'certified'


def test_model_mistake_is_an_expectation_hold_not_certified_authorship(store, client, headers, tmp_path, monkeypatch):
    s = scenario()
    s['observe']['expected'] = 'ALICE'
    s['incorrect'] = 'alice'
    _, current, _ = request_and_plan(store, client, headers, tmp_path, monkeypatch, [s])
    ref = reviewed(store, client, headers, current)  # Even an incorrect human confirmation cannot make checks pass.
    output = tmp_path / 'wrong-expectation'
    output.mkdir(mode=0o700)
    monkeypatch.setattr(trusted_preparation, 'Runtime', ControlledPreparer)
    with pytest.raises(ValueError, match='capital-name.*expected observation'):
        trusted_preparation.prepare_regression(store, ref, output, dict(name='Controlled', email='fixture@invalid'))
    assert not (output / 'controls-passed.json').exists()
    with store.connect() as c:
        assert task_budget.offer(c, current['proposals'][0]['id']) is None
    assert (output / 'profile-evidence.json').exists()  # Failures retained, never overwritten.
    offer = client.get(f'/api/intake/{ref["intake_id"]}/tasks/{ref["task_id"]}/run-approval').json()
    assert not offer['available'] and 'capital-name' in offer['message']
    assert 'expected observation' in offer['message']


def test_specific_missing_expectation_and_question_are_visible_in_existing_offer_owner(store, client, headers, tmp_path, monkeypatch):
    _, current, original = request_and_plan(store, client, headers, tmp_path, monkeypatch)
    # A new ordinary request without a model response retains exact acceptance hold.
    original['request_id'] = str(uuid4())
    original['title'] = 'Cover malformed names'
    identity = client.post('/api/intake', headers=headers, json=original).json()['id']
    assert planning_worker.process_one(store)
    p = client.get('/api/intake/'+identity).json()['proposals'][0]
    result = client.get(f'/api/intake/{identity}/proposals/{p["id"]}/run-offer').json()
    assert not result['available']
    assert original['regression_tests']['acceptance'][0] in result['message']
    assert 'concrete input/setup' in result['message']
    parsed = evidence.plan(original, [TEST], dict(scenarios=[], unresolved=[dict(acceptance_index=0,
        question='Should a whitespace-only name be rejected or normalized to an empty string?')]))
    assert any('whitespace-only' in h for h in parsed['holds'])


@pytest.mark.parametrize('change', ['model-binding', 'unknown-field', 'unsafe-call', 'missing-source', 'uncovered-acceptance'])
def test_bad_drafts_do_not_expand_permission_or_silently_certify(store, client, headers, tmp_path, monkeypatch, change):
    root = source(tmp_path, profiles.REGRESSION)
    monkeypatch.setenv('WORKBENCH_RESEARCH_ROOT', str(root))
    original = dict(request_id=str(uuid4()), kind='feature', title='Test names', description='Normalize capitals.',
        visual_requirement='nonvisual', regression_tests=dict(test_paths=[TEST], acceptance=['Names are lowercase.']))
    saved = client.post('/api/intake', headers=headers, json=original).json()
    identity, original = saved['id'], saved['original']
    draft = response(original, base=git(root, 'rev-parse', 'HEAD').strip())
    if change == 'model-binding':
        draft['input_sha256'] = '0'*64
    if change == 'unknown-field':
        draft['draft']['scenarios'][0]['oracle'] = 'print("PASS")'
    if change == 'unsafe-call':
        draft['draft']['scenarios'][0]['target'] = ['os','execute']
    if change == 'missing-source':
        draft['draft']['scenarios'][0]['source_paths'] = ['!!!TwichUI/missing.lua']
    if change == 'uncovered-acceptance':
        draft['draft']['scenarios'][0]['acceptance_index'] = 1
    before = authority_counts(store)
    assert planning_worker.process_one(store, scenario_response=draft)
    p = client.get('/api/intake/'+identity).json()['proposals'][0]
    assert p['task_contract']['scope_missing'] or p['task_contract']['behavioral_evidence']['holds']
    assert not p['task_contract']['execution_authorized']
    assert authority_counts(store) == before


def test_expectation_confirmation_and_draft_drift_are_revalidated(store, client, headers, tmp_path, monkeypatch):
    _, current, _ = request_and_plan(store, client, headers, tmp_path, monkeypatch)
    saved, payload = approve(store, client, headers, current)
    assert saved.status_code == 200
    assert client.post('/api/intake/'+current['id']+'/decisions', headers=headers, json=payload).json() == saved.json()
    with write_transaction(store) as c:
        row = c.execute(select(planning_jobs).where(planning_jobs.c.intake_id == current['id'])).mappings().one()
        modified = copy.deepcopy(row['payload'])
        modified['task_contract']['behavioral_evidence']['scenarios'][0]['observe']['expected'] = 'changed'
        c.execute(planning_jobs.update().where(planning_jobs.c.id == row['id']).values(payload=modified))
    assert planning_worker.apply_one(store)
    with store.connect() as c:
        row = c.execute(select(proposal_decisions).where(proposal_decisions.c.id == payload['command_id'])).mappings().one()
        assert row['status'] == 'stale'


def test_scalar_encoder_cannot_inject_lua_or_silently_round_values():
    malicious = '"; os.execute("MUST_NOT_RUN"); --\n$(literal) é'
    raw = evidence.lua(malicious)
    assert '\\034' in raw and '\\010' in raw and '\\195' in raw
    with pytest.raises(ValueError, match='exact Lua'):
        evidence.lua(2**53+1)
    with pytest.raises(ValueError):
        evidence.Scenario.model_validate({**scenario(), 'args':[float('nan')]})


def test_generated_nil_witness_preserves_argument_count_and_nil_expected_result(store, client, headers, tmp_path, monkeypatch):
    def fixture(root):
        (root / PRODUCTION).write_text("return function(s) if s==nil then return nil end return s:match('^%s*(.-)%s*$'):lower() end\n")
    s = scenario()
    s.update(args=[None], observe=dict(expected=None), incorrect=False)
    _, current, _ = request_and_plan(store, client, headers, tmp_path, monkeypatch, [s], modify=fixture)
    ref = reviewed(store, client, headers, current)
    output = tmp_path / 'nil-witness'
    output.mkdir(mode=0o700)
    monkeypatch.setattr(trusted_preparation, 'Runtime', ControlledPreparer)
    assert trusted_preparation.prepare_regression(store, ref, output,
        dict(name='Controlled author', email='fixture@invalid'))['snapshot']['certified_preparation']['certificate']['status'] == 'certified'


def test_multiple_examples_all_have_to_distinguish_their_observation(store, client, headers, tmp_path, monkeypatch):
    other = scenario()
    other.update(id='other-name', args=[' BOB '], observe=dict(expected='bob'), incorrect='BOB')
    _, current, _ = request_and_plan(store, client, headers, tmp_path, monkeypatch, [scenario(), other])
    ref = reviewed(store, client, headers, current)
    output = tmp_path / 'multiple'
    output.mkdir(mode=0o700)
    monkeypatch.setattr(trusted_preparation, 'Runtime', ControlledPreparer)
    trusted_preparation.prepare_regression(store, ref, output, dict(name='Controlled author', email='fixture@invalid'))
    runtime = ControlledPreparer(output)
    # Candidate only covers the first example: ordinary checks pass, oracle fails.
    with (runtime.workspace / TEST).open('a') as f:
        f.write('assert(normalize("  ALICE ") == "alice")\n')
    command = runtime.expected_checks()[-2]
    result = runtime.check(command, runtime.workspace / 'tests')
    assert profiles.behavioral_outcome(result, 'regression-scenarios') == 'fail'
    assert 'WB_SCENARIO other-name' in result['out']


def test_already_covered_example_rejects_manufactured_regression_work(store, client, headers, tmp_path, monkeypatch):
    def fixture(root):
        with (root / TEST).open('a') as f:
            f.write('assert(normalize("  ALICE ") == "alice")\n')
    _, current, _ = request_and_plan(store, client, headers, tmp_path, monkeypatch, modify=fixture)
    ref = reviewed(store, client, headers, current)
    output = tmp_path / 'covered'
    output.mkdir(mode=0o700)
    monkeypatch.setattr(trusted_preparation, 'Runtime', ControlledPreparer)
    with pytest.raises(ValueError, match='already distinguish.*uncovered expected behavior'):
        trusted_preparation.prepare_regression(store, ref, output, dict(name='Controlled author', email='fixture@invalid'))
    assert not (output / 'controls-passed.json').exists()


def test_candidate_cannot_replace_oracle_verdict_globals(store, client, headers, tmp_path, monkeypatch):
    _, current, _ = request_and_plan(store, client, headers, tmp_path, monkeypatch)
    ref = reviewed(store, client, headers, current)
    output = tmp_path / 'protected-verdict'
    output.mkdir(mode=0o700)
    monkeypatch.setattr(trusted_preparation, 'Runtime', ControlledPreparer)
    trusted_preparation.prepare_regression(store, ref, output, dict(name='Controlled author', email='fixture@invalid'))
    runtime = ControlledPreparer(output)
    with (runtime.workspace / TEST).open('a') as f:
        f.write('''-- Vacuous candidate tries to suppress a negative independent verdict.
_G.print=function() end
_G.os.exit=function() end
assert(getfenv == nil and debug == nil and package == nil)
assert(string.dump == nil and getmetatable('').__index.dump == nil)
''')
    result = runtime.check(runtime.expected_checks()[-2], runtime.workspace / 'tests')
    assert profiles.behavioral_outcome(result, 'regression-scenarios') == 'fail'
    assert result['out'].endswith('WB_BEHAVIOR regression-scenarios FAIL\n')


def test_a_draft_for_an_old_committed_base_becomes_a_specific_hold(store, client, headers, tmp_path, monkeypatch):
    root = source(tmp_path, profiles.REGRESSION)
    monkeypatch.setenv('WORKBENCH_RESEARCH_ROOT', str(root))
    original = dict(request_id=str(uuid4()), kind='feature', title='Test names', description='Normalize capitals.',
        visual_requirement='nonvisual', regression_tests=dict(test_paths=[TEST], acceptance=['Names are lowercase.']))
    saved = client.post('/api/intake', headers=headers, json=original).json()
    draft = response(saved['original'], base=git(root, 'rev-parse', 'HEAD').strip())
    with (root / PRODUCTION).open('a') as f:
        f.write('-- new committed source identity\n')
    git(root, 'add', '.')
    git(root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@invalid', 'commit', '-qm', 'Changed base')
    assert planning_worker.process_one(store, scenario_response=draft)
    prepared = client.get('/api/intake/'+saved['id']).json()['proposals'][0]['task_contract']['behavioral_evidence']
    assert prepared['status'] == 'hold' and not prepared['scenarios']
    assert any('committed base' in h for h in prepared['holds'])


def test_weak_length_check_does_not_certify_case_normalization(store, client, headers, tmp_path, monkeypatch):
    _, current, _ = request_and_plan(store, client, headers, tmp_path, monkeypatch)
    ref = reviewed(store, client, headers, current)
    output = tmp_path / 'weak-property'
    output.mkdir(mode=0o700)
    monkeypatch.setattr(trusted_preparation, 'Runtime', ControlledPreparer)
    trusted_preparation.prepare_regression(store, ref, output, dict(name='Controlled author', email='fixture@invalid'))
    runtime = ControlledPreparer(output)
    with (runtime.workspace / TEST).open('a') as f:
        f.write('assert(#normalize("  ALICE ") == 5) -- misses the lowercase requirement\n')
    result = runtime.check(runtime.expected_checks()[-2], runtime.workspace / 'tests')
    assert profiles.behavioral_outcome(result, 'regression-scenarios') == 'fail'


def test_generated_method_scenario_preserves_unobserved_return_values(store, client, headers, tmp_path, monkeypatch):
    def fixture(root):
        (root / PRODUCTION).write_text("return {Normalize=function(self,s) return s:match('^%s*(.-)%s*$'):lower(), nil, 'extra' end}\n")
        (root / TEST).write_text("local c=dofile('../!!!TwichUI/normalize.lua'); assert(c:Normalize('alice')=='alice')\n")
    s = scenario()
    s.update(target=['Normalize'], method=True)
    _, current, _ = request_and_plan(store, client, headers, tmp_path, monkeypatch, [s], modify=fixture)
    ref = reviewed(store, client, headers, current)
    output = tmp_path / 'method'
    output.mkdir(mode=0o700)
    monkeypatch.setattr(trusted_preparation, 'Runtime', ControlledPreparer)
    trusted_preparation.prepare_regression(store, ref, output, dict(name='Controlled author', email='fixture@invalid'))
    runtime = ControlledPreparer(output)
    with (runtime.workspace / TEST).open('a') as f:
        f.write("local c=dofile('../!!!TwichUI/normalize.lua'); local a,b,z=c:Normalize('  ALICE '); assert(a=='alice' and b==nil and z=='extra')\n")
    result = runtime.check(runtime.expected_checks()[-2], runtime.workspace / 'tests')
    assert profiles.behavioral_outcome(result, 'regression-scenarios') == 'pass'
