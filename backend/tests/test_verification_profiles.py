"""Executable, NON-MODEL Lua fixtures; controlled runner, no confinement claim.

Production uses Runtime.check's existing confined runner. These tests use actual
Lua outputs with explicitly controlled resource metadata to exercise certification,
not to establish sandbox/client/unattended readiness.
"""

import copy
import json
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from workbench import verification_profiles as profiles
from workbench.real_workspace import Runtime, durable, sha, tree
from test_task_to_pr import template_data


PRODUCTION = '!!!TwichUI/normalize.lua'
TEST = 'tests/test_normalize.lua'
GOOD_TEST = 'assert(normalize("  ALICE ") == "alice")\n'
BASE_TEST = 'normalize=normalize or dofile("../!!!TwichUI/normalize.lua")\nassert(normalize("alice") == "alice")\n'
REGRESSION_ORACLE = '''local approved = dofile('../!!!TwichUI/normalize.lua')
local function run(fn)
  normalize = fn
  return pcall(dofile, 'test_normalize.lua')
end
local good = run(approved)
-- Inject only the approved behavior fault: case normalization.
local rejects_fault = not run(function(s) return s:match('^%s*(.-)%s*$') end)
if good and rejects_fault then print('WB_BEHAVIOR normalize PASS')
else print('WB_BEHAVIOR normalize FAIL'); os.exit(1) end
'''
BUG_ORACLE = '''local normalize = dofile('../!!!TwichUI/normalize.lua')
if normalize('  ALICE ') == 'alice' then print('WB_BEHAVIOR normalize PASS')
else print('WB_BEHAVIOR normalize FAIL'); os.exit(1) end
'''
CORRECT = "return function(s) return s:match('^%s*(.-)%s*$'):lower() end\n"
BUGGY = "return function(s) return s:match('^%s*(.-)%s*$') end\n"
RUNNER = '''#!/bin/bash
set -e
cd tests
for t in test_*.lua; do
  lua5.1 -e 'normalize=dofile("../!!!TwichUI/normalize.lua")' "$t"
  echo "ok   $t"
done
echo '1 passed, 0 failed'
'''


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.DEVNULL).decode()


class ControlledLuaRuntime(Runtime):
    """Real Lua, controlled boundary telemetry. No service/model/network activity."""

    def check(self, argv, cwd, ro=(), timeout=120, private_loopback=False):
        run = subprocess.run(argv, cwd=cwd, capture_output=True, timeout=timeout)
        return dict(command=argv, status='pass' if run.returncode == 0 else 'fail', rc=run.returncode,
                    out=(run.stdout+run.stderr).decode(), network_mode='private-loopback' if private_loopback else 'denied',
                    usage={'controlled_fixture': True, 'cgroup_removed': True})

    def expected_suite(self):
        return ['test_normalize.lua']


def setup(tmp_path, profile=profiles.REGRESSION, oracle=None):
    source = tmp_path / 'source'
    source.mkdir()
    git(source, 'init', '-q')
    (source / '!!!TwichUI').mkdir()
    (source / 'tests').mkdir()
    (source / PRODUCTION).write_text(CORRECT if profile == profiles.REGRESSION else BUGGY)
    (source / TEST).write_text(BASE_TEST)
    (source / 'tests/run.sh').write_text(RUNNER)
    (source / 'tests/harness.lua').write_text('-- Unchanged framework\n')
    git(source, 'add', '.')
    git(source, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@invalid', 'commit', '-qm', 'Controlled base')
    root = tmp_path / 'evidence'
    root.mkdir()
    spec = template_data()['recipe']
    spec['contract'].update(source=str(source), base=git(source, 'rev-parse', 'HEAD').strip(),
                            paths=[TEST] if profile == profiles.REGRESSION else [PRODUCTION])
    spec.update(verification_profile=profile, behavior=dict(id='normalize', review_binding=sha(b'Controlled reviewed behavior: trim and lowercase')),
                controls=copy.deepcopy(profiles.requirements(profile)['controls']), read_paths=[PRODUCTION, TEST], focused_test=TEST,
                initial_reads=[dict(path=TEST, start=1, end=2) for _ in range(4)])
    raw = (oracle if oracle is not None else REGRESSION_ORACLE if profile == profiles.REGRESSION else BUG_ORACLE).encode()
    spec['oracle_sha256'] = sha(raw)
    durable(root / 'recipe.json', json.dumps(spec).encode())
    durable(root / 'recipe-oracle.lua', raw)
    runtime = ControlledLuaRuntime(root)
    runtime.prepare()
    return runtime


def patch(runtime, path, text):
    before = (runtime.workspace / path).read_text()
    (runtime.workspace / path).write_text(text)
    value = git(runtime.workspace, 'diff', '--no-ext-diff', '--no-textconv', runtime.base)
    (runtime.workspace / path).write_text(before)
    return value


def controls(runtime, wrong=None):
    # Existing focused Runtime prelude does not load our tiny module: the fixture
    # test loads it explicitly in the base (see fixture below).
    original = (runtime.workspace / TEST).read_text()
    return {
        'known-good-oracle-control': patch(runtime, TEST, original+GOOD_TEST),
        'vacuous': patch(runtime, TEST, original+'assert(true)\n'),
        'wrong-behavior': patch(runtime, TEST, original+(wrong or 'assert(normalize("  alice ") == "alice")\n')),
    }


def boundary(runtime):
    p = runtime.root / 'controlled-boundary.json'
    durable(p, json.dumps(dict(checks={'controlled': {'usage': {'cgroup_removed': True, 'controlled_fixture': True}}})).encode())
    return p


def test_selection_is_descriptive_and_profiles_differ():
    for category in ('lua-regression-tests', 'lua-offline-bugfix'):
        selected = profiles.select(category)
        assert selected['status'] == 'not_run' and selected['execution_authorized'] is False
    assert 'known-good-oracle-control' not in profiles.requirements(profiles.BUGFIX)['controls']
    with pytest.raises(ValueError):
        profiles.select('anything')


def test_regression_controls_certify_and_do_not_grant_permission(tmp_path):
    runtime = setup(tmp_path)
    bundle = profiles.prepare(runtime, controls(runtime))
    assert profiles.certify(runtime, bundle)['status'] == 'certified'
    assert tree(runtime.workspace) == json.loads(runtime.initial.read_text())
    runtime.record_controls(boundary(runtime))
    assert runtime.controls_valid()
    assert profiles.certify(runtime, bundle)['execution_authorized'] is False
    assert not list(runtime.root.glob('ownership-*'))


@pytest.mark.parametrize('control,addition', [
    ('known-good-oracle-control', 'assert(true)\n'),
    ('known-good-oracle-control', 'assert(normalize(" bob ") == "bob")\n'),
    ('wrong-behavior', 'error("unrelated fixture failure")\n'),
])
def test_vacuous_wrong_behavior_and_unrelated_failures_rejected(tmp_path, control, addition):
    runtime = setup(tmp_path)
    patches = controls(runtime)
    patches[control] = patch(runtime, TEST, (runtime.workspace / TEST).read_text()+addition)
    bundle = profiles.prepare(runtime, patches)
    assert profiles.certify(runtime, bundle)['status'] == 'rejected'
    with pytest.raises(ValueError):
        runtime.record_controls(boundary(runtime))
    assert not runtime.controls_valid()


@pytest.mark.parametrize('oracle', ["error('unrelated crash')\n", "print('no behavioral verdict')\n",
    "print('WB_BEHAVIOR normalize FAIL'); print('WB_BEHAVIOR normalize PASS'); os.exit(1)\n"])
def test_missing_ambiguous_behavior_holds(tmp_path, oracle):
    runtime = setup(tmp_path, profiles.BUGFIX, oracle)
    bundle = profiles.prepare(runtime, {})
    assert profiles.certify(runtime, bundle)['status'] == 'hold'


def test_bug_reproducer_needs_no_handwritten_good_control_and_candidate_must_pass(tmp_path):
    runtime = setup(tmp_path, profiles.BUGFIX)
    bundle = profiles.prepare(runtime, {})
    assert profiles.certify(runtime, bundle)['status'] == 'certified'
    runtime.record_controls(boundary(runtime))
    assert runtime.controls_valid()
    # Wrong behavior still fails the independently reviewed reproducer.
    commands = runtime.expected_checks()
    def candidate():
        checks = [runtime.check(argv, runtime.workspace/'tests' if i in (len(commands)-3, len(commands)-2) else runtime.workspace,
                    private_loopback=i == len(commands)-1) for i, argv in enumerate(commands)]
        return profiles.evaluate_snapshot(runtime, dict(checks=checks), 'pass')[0]
    assert candidate() == 'rejected'
    (runtime.workspace / PRODUCTION).write_text(CORRECT)
    runtime.scope()
    assert candidate() == 'certified'
    (runtime.workspace / PRODUCTION).write_text('error("unrelated failure")\n')
    assert candidate() == 'rejected'


@pytest.mark.parametrize('mutation', ['oracle', 'frozen-oracle', 'recipe', 'base-manifest', 'check',
                                    'patch', 'input-patch', 'frozen-file', 'source', 'baseline'])
def test_certification_cannot_be_reused_after_inputs_change(tmp_path, mutation):
    runtime = setup(tmp_path)
    bundle = profiles.prepare(runtime, controls(runtime))
    runtime.record_controls(boundary(runtime))
    assert runtime.controls_valid()
    targets = {'oracle': runtime.oracle_source, 'recipe': runtime.root/'recipe.json', 'base-manifest':runtime.initial,
               'frozen-oracle':runtime.root/'oracle.lua',
               'check':runtime.root/'profile-evidence/original/check-00.json',
               'patch':runtime.root/'profile-evidence/vacuous/changes.patch',
               'input-patch':runtime.root/'profile-evidence/vacuous/input.patch',
               'frozen-file':runtime.root/'profile-evidence/vacuous/files'/TEST,
               'source':Path(runtime.source)/'tests/harness.lua',
               'baseline':runtime.root/'fresh-baseline-suite.json'}
    p = targets[mutation]
    p.write_bytes(p.read_bytes()+b'\nchanged')
    assert profiles.certify(runtime, bundle)['status'] == 'hold'
    assert not runtime.controls_valid()


def test_missing_control_is_hold_and_profile_scope_is_enforced(tmp_path):
    runtime = setup(tmp_path)
    bundle = profiles.prepare(runtime, controls(runtime))
    del bundle['controls']['vacuous']
    assert profiles.certify(runtime, bundle)['status'] == 'hold'
    (runtime.workspace / PRODUCTION).write_text(BUGGY)
    with pytest.raises(ValueError):
        runtime.scope()


@pytest.mark.parametrize('profile', [profiles.REGRESSION, profiles.BUGFIX])
def test_existing_candidate_checker_and_freezer_consume_profile(tmp_path, profile):
    runtime = setup(tmp_path, profile)
    profiles.prepare(runtime, controls(runtime) if profile == profiles.REGRESSION else {})
    runtime.record_controls(boundary(runtime))
    runtime._request_id, runtime._fence, runtime._context_sha = str(uuid4()), 1, None
    if profile == profiles.REGRESSION:
        (runtime.workspace / TEST).write_text((runtime.workspace / TEST).read_text()+GOOD_TEST)
    else:
        (runtime.workspace / PRODUCTION).write_text(CORRECT)
    assert runtime.candidate_feedback(lambda: True)['status'] == 'pass'
    # This controlled author has no model usage and creates no grant or attempt.
    runtime.authoring_provenance = {'controlled_fixture': True}
    result = runtime.freeze_candidate(runtime._request_id, 1, lambda: True,
        dict(correction_count=0, read_input_errors=0), {'usage': {'status': 'unavailable'}})
    assert result['verification'] == 'passed' and result['inference_ran'] is False
    assert result['manifest']['profile_certificate_sha256'] == sha((runtime.root/'controls-passed.json').read_bytes())
    assert runtime.verify_frozen(result)
    # Even a cached passing check cannot bypass a changed preparation input.
    (runtime.root/'profile-evidence/original/check-00.json').write_text('{}')
    with pytest.raises(ValueError, match='profile certification'):
        runtime.candidate_feedback(lambda: True)
    with pytest.raises(ValueError, match='certification changed'):
        runtime.freeze_candidate(runtime._request_id, 1, lambda: True,
            dict(correction_count=0, read_input_errors=0), {'usage': {'status': 'unavailable'}})


def test_profile_selection_does_not_satisfy_candidate_gate(tmp_path):
    runtime = setup(tmp_path, profiles.BUGFIX)
    (runtime.workspace / PRODUCTION).write_text(CORRECT)
    runtime._request_id, runtime._fence, runtime._context_sha = str(uuid4()), 1, None
    with pytest.raises(ValueError, match='Missing or stale'):
        runtime.candidate_feedback(lambda: True)


@pytest.mark.parametrize('bad', ['tests/harness.lua', 'untracked.lua'])
def test_out_of_scope_or_untracked_changes_rejected(tmp_path, bad):
    runtime = setup(tmp_path)
    (runtime.workspace/TEST).write_text((runtime.workspace/TEST).read_text()+GOOD_TEST)
    (runtime.workspace/bad).write_text('assert(true)\n')
    with pytest.raises(ValueError):
        runtime.scope()


def test_recipe_profile_changes_do_not_weaken_legacy_requirements(tmp_path):
    from workbench.real_recipes import validate
    spec = template_data()['recipe']
    validate(spec)
    broken = copy.deepcopy(spec)
    del broken['controls']['known-good-oracle-control']
    with pytest.raises(ValueError):
        validate(broken)
    runtime = setup(tmp_path, profiles.BUGFIX)
    validate(runtime.recipe)  # A base reproducer is sufficient for this class.
    broken = copy.deepcopy(runtime.recipe)
    broken['controls'] = {'original': 'pass'}
    with pytest.raises(ValueError):
        validate(broken)
    broken = copy.deepcopy(runtime.recipe)
    broken['verification_profile'] = 'unknown'
    with pytest.raises(ValueError):
        validate(broken)


def test_legacy_proposed_contract_omits_new_profile_and_remains_identical(tmp_path):
    from workbench.schemas import RegressionTaskContract
    from workbench.regression_preparation import prepare
    contract = prepare(dict(title='Controlled', description='Controlled behavior',
        regression_tests=dict(test_paths=[], acceptance=['Controlled expected behavior'])))
    del contract['verification_profile']
    assert RegressionTaskContract.model_validate(contract).model_dump(mode='json') == contract


@pytest.mark.parametrize('kind', ['missing', 'ambiguous', 'unrelated'])
def test_candidate_behavioral_ambiguity_is_an_explicit_hold(tmp_path, kind):
    runtime = setup(tmp_path, profiles.BUGFIX)
    profiles.prepare(runtime, {})
    runtime.record_controls(boundary(runtime))
    (runtime.workspace/PRODUCTION).write_text(CORRECT)
    runtime._request_id, runtime._fence, runtime._context_sha = str(uuid4()), 1, None
    check = runtime.check
    def changed_check(argv, *args, **kwargs):
        result = check(argv, *args, **kwargs)
        if argv == runtime.expected_checks()[-2]:
            if kind == 'missing':
                result['out'] = ''
            elif kind == 'ambiguous':
                result['out'] += 'WB_BEHAVIOR normalize PASS\n'
            else:
                result['out'] = 'WB_BEHAVIOR different-behavior PASS\n'
        return result
    runtime.check = changed_check
    with pytest.raises(ValueError, match='behavioral evidence'):
        runtime.candidate_feedback(lambda: True)


def test_regression_profile_rejects_production_paths_and_test_deletions(tmp_path):
    from workbench.real_recipes import validate
    runtime = setup(tmp_path)
    spec = copy.deepcopy(runtime.recipe)
    spec['contract']['paths'] = [PRODUCTION, TEST]
    with pytest.raises(ValueError, match='existing test additions'):
        validate(spec)
    (runtime.workspace/TEST).write_text(GOOD_TEST)
    with pytest.raises(ValueError, match='only gain assertions'):
        runtime.scope()


def test_unconditional_oracle_cannot_certify_controls(tmp_path):
    runtime = setup(tmp_path, oracle="print('WB_BEHAVIOR normalize PASS')\n")
    bundle = profiles.prepare(runtime, controls(runtime))
    assert profiles.certify(runtime, bundle)['status'] == 'rejected'


def test_permission_is_rechecked_before_candidate_checks(tmp_path):
    runtime = setup(tmp_path, profiles.BUGFIX)
    profiles.prepare(runtime, {})
    runtime.record_controls(boundary(runtime))
    (runtime.workspace/PRODUCTION).write_text(CORRECT)
    runtime._request_id, runtime._fence, runtime._context_sha = str(uuid4()), 1, None
    with pytest.raises(ValueError, match='permission/context'):
        runtime.candidate_feedback(lambda: False)


def test_profile_preparation_satisfies_existing_static_verification_gates(tmp_path):
    runtime = setup(tmp_path, profiles.BUGFIX)
    profiles.prepare(runtime, {})
    runtime.record_controls(boundary(runtime))
    assert all(gate['status'] == 'passed' for gate in runtime.static_preflight())


@pytest.mark.parametrize('changed', ['requirements', 'suite', 'review', 'base'])
def test_semantic_verification_inputs_cannot_inherit_certification(tmp_path, monkeypatch, changed):
    runtime = setup(tmp_path, profiles.BUGFIX)
    bundle = profiles.prepare(runtime, {})
    runtime.record_controls(boundary(runtime))
    assert runtime.controls_valid()
    if changed == 'requirements':
        current = profiles.requirements(profiles.BUGFIX)
        current['requirements'].append('A new requirement')
        monkeypatch.setitem(profiles.PROFILES, profiles.BUGFIX, current)
    elif changed == 'suite':
        runtime.expected_suite = lambda: ['different_test.lua']
    elif changed == 'review':
        runtime.recipe['behavior']['review_binding'] = sha(b'New review revision')
        (runtime.root/'recipe.json').write_text(json.dumps(runtime.recipe))
    else:
        source = Path(runtime.source)
        (source/PRODUCTION).write_text(BUGGY+'-- new base\n')
        git(source, 'add', '.')
        git(source, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@invalid', 'commit', '-qm', 'Changed controlled base')
    assert profiles.certify(runtime, bundle)['status'] == 'hold'
    assert not runtime.controls_valid()
