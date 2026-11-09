"""Reusable offline Lua verification requirements, never execution authority.

Trusted preparation supplies reviewed behavior/oracle bytes and control patches.
The existing Runtime owns scope, confined commands, snapshots and durable files.
Selection is descriptive; certification independently evaluates recorded checks.
"""

import json
import re
import subprocess
from pathlib import Path

from workbench.visual_scope import digest

REGRESSION = 'lua-regression-tests-v1'
BUGFIX = 'lua-offline-bugfix-v1'
PROFILES = {
    REGRESSION: {
        'controls': {'original': 'fail', 'known-good-oracle-control': 'pass',
                     'vacuous': 'fail', 'wrong-behavior': 'fail'},
        'requirements': [
            'Production code and the test harness remain unchanged; existing tests only gain assertions.',
            'The independent oracle targets the approved behavior; original, vacuous and wrong-behavior controls must fail for that behavior, and a known-good test addition must pass.',
            'Syntax, focused existing checks and the exact complete suite pass for every control; unrelated failures cannot establish behavioral adequacy.',
        ],
    },
    BUGFIX: {
        'controls': {'original': 'fail'},
        'requirements': [
            'An independent approved-behavior reproducer fails on the original base and passes on the frozen candidate.',
            'Scope, syntax, focused existing regressions, exact complete suite and artifact integrity pass.',
            'A hand-written known-good implementation is not required; missing or ambiguous reproducer evidence holds verification.',
        ],
    },
}


def select(category):
    identity = {'lua-regression-tests': REGRESSION, 'lua-offline-bugfix': BUGFIX}.get(category)
    if identity is None:
        raise ValueError('Unsupported offline Lua verification class')
    return dict(id=identity, requirements=list(PROFILES[identity]['requirements']),
                status='not_run', execution_authorized=False)


def requirements(identity):
    if not isinstance(identity, str) or identity not in PROFILES:
        raise ValueError('Unknown verification profile')
    return json.loads(json.dumps(PROFILES[identity]))


def validate_recipe(spec):
    """Profile selection validates data, never certifies behavior or permission."""
    profile = requirements(spec['verification_profile'])
    behavior = spec.get('behavior')
    if (not isinstance(behavior, dict) or set(behavior) != {'id', 'review_binding'}
            or not isinstance(behavior.get('id'), str)
            or not re.fullmatch(r'[a-z][a-z0-9-]{0,63}', behavior['id'])
            or not isinstance(behavior.get('review_binding'), str)
            or not re.fullmatch(r'[a-f0-9]{64}', behavior['review_binding'])):
        raise ValueError('Exact reviewed behavior identity required')
    if spec.get('controls') != profile['controls']:
        raise ValueError('Profile control requirements differ')
    paths = spec['contract']['paths']
    if spec['verification_profile'] == REGRESSION and any(
            not re.fullmatch(r'tests/test_[A-Za-z0-9_-]+\.lua', p) for p in paths):
        raise ValueError('Regression profile permits existing test additions only')


def binding(runtime):
    """Changing any verification input invalidates the old certification."""
    from workbench.real_workspace import sha, git
    from workbench.real_recipes import load

    if load(runtime.root) != runtime.recipe:
        raise ValueError('Prepared recipe changed')

    return dict(profile=runtime.recipe['verification_profile'],
                requirements=requirements(runtime.recipe['verification_profile']),
                recipe=digest(runtime.recipe), base=runtime.base,
                base_manifest=sha(runtime.initial.read_bytes()),
                oracle=sha(runtime.oracle_source.read_bytes()),
                frozen_oracle=sha((runtime.root / 'oracle.lua').read_bytes()),
                suite=sorted(runtime.expected_suite()),
                source_head=git(Path(runtime.source), 'rev-parse', 'HEAD').strip(),
                source_status=git(Path(runtime.source), 'status', '--porcelain=v1', '--ignored'),
                helpers={name: sha(Path(__file__).with_name(name).read_bytes()) for name in (
                    'verification_profiles.py', 'real_recipes.py', 'real_workspace.py',
                    'real_confine.py', 'real_sandbox.py', 'verification_process.py')})


def behavioral_outcome(result, behavior_id):
    """A crash, syntax error, or unrelated assertion is never reproduction proof.

    The independently reviewed oracle emits exactly one final structured verdict
    AFTER evaluating its assertions. Missing/ambiguous verdicts explicitly hold.
    Candidate test output by itself is never used for this classification.
    """
    from workbench.real_workspace import require_safe_check

    try:
        require_safe_check(result)
    except ValueError:
        return 'hold'
    markers = re.findall(r'^WB_BEHAVIOR ([a-z][a-z0-9-]{0,63}) (PASS|FAIL)$',
                         result.get('out', ''), re.MULTILINE)
    if len(markers) != 1:
        return 'hold'
    identity, outcome = markers[0]
    if identity != behavior_id:
        return 'rejected'
    expected = ('pass', 0) if outcome == 'PASS' else ('fail', 1)
    if (result.get('status'), result.get('rc')) != expected:
        return 'rejected'
    if result.get('out', '').strip().splitlines()[-1] != f'WB_BEHAVIOR {identity} {outcome}':
        return 'hold'
    return outcome.lower()


def evaluate_snapshot(runtime, record, expected):
    """Evaluate checks on a frozen tree, including the unchanged existing suite."""
    from workbench.real_workspace import require_safe_check, suite_inventory

    try:
        checks = record['checks']
        commands = runtime.expected_checks()
        if len(checks) != len(commands) or [c.get('command') for c in checks] != commands:
            return 'hold', 'Missing or changed fixed checks'
        for result in checks:
            require_safe_check(result)
        if any(c.get('status') != 'pass' or c.get('rc') != 0 for c in checks[:-2]):
            return 'rejected', 'Syntax or existing focused checks failed'
        if not suite_inventory(checks[-1], runtime.expected_suite()):
            return 'rejected', 'Existing suite failed or its inventory changed'
        outcome = behavioral_outcome(checks[-2], runtime.recipe['behavior']['id'])
        if outcome == 'hold':
            missing = re.findall(r'^WB_EXPECTATION ([a-z][a-z0-9-]{0,47}) MISMATCH$', checks[-2].get('out', ''), re.MULTILINE)
            if missing:
                return 'hold', f'Example {missing[0]}: the expected observation or fixture setup does not match the committed behavior. Clarify that expectation; generated examples are not correctness evidence.'
            if runtime.recipe['behavior']['id'] == 'bug-reproducer':
                return 'hold', 'The offline fixture did not produce either reviewed result. Workbench could not verify this report with the available evidence.'
            return 'hold', 'Missing or ambiguous behavioral verdict'
        if outcome != expected:
            if runtime.recipe['behavior']['id'] == 'bug-reproducer' and expected == 'fail':
                return 'hold', 'The original code already passed the reviewed behavior check. Workbench could not reproduce the bug and will not offer a repair.'
            if runtime.recipe['behavior']['id'] == 'regression-scenarios':
                if expected == 'fail' and outcome == 'pass':
                    return 'rejected', 'Existing or negative-control tests already distinguish the supplied example. Identify an uncovered expected behavior before proposing new coverage.'
                details = re.findall(r'^WB_SCENARIO ([a-z][a-z0-9-]{0,47}) ([A-Z_]+)$', checks[-2].get('out', ''), re.MULTILINE)
                if details:
                    identity, why = details[0]
                    message = {'CORRECT_RUN_FAILED': 'the existing test does not run with the proposed fixture setup',
                        'TARGET_NOT_OBSERVED': 'the proposed operation/input was not observed through the supported fixture adapter',
                        'OBSERVATION_NOT_ASSERTED': 'the generated positive addition does not distinguish the expected observation',
                        'NON_ASSERTION_FAILURE': 'the fault caused an unrelated error rather than a checked expectation'}.get(why)
                    if message:
                        return 'hold', f'Example {identity}: {message}. Clarify the fixture, operation and expected result.'
            return 'rejected', 'Approved-behavior outcome differs'
        return 'certified', 'Fixed checks and approved behavior match'
    except (KeyError, TypeError, ValueError, IndexError):
        return 'hold', 'Incomplete or unsafe check evidence'


def certify(runtime, bundle):
    """Pure certification: no execution, grants, offers or evidence mutation."""
    result = dict(status='hold', reason='Missing prepared evidence', execution_authorized=False)
    try:
        if bundle['binding'] != binding(runtime):
            return dict(result, reason='Verification inputs changed; prepare fresh evidence')
        if (bundle['binding']['source_head'] != runtime.base or bundle['binding']['source_status']
                or bundle['binding']['oracle'] != bundle['binding']['frozen_oracle']):
            return dict(result, reason='Original source or frozen oracle differs')
        if set(bundle['controls']) != set(requirements(runtime.recipe['verification_profile'])['controls']):
            return dict(result, reason='Required behavioral controls are missing')
        for name, expected in requirements(runtime.recipe['verification_profile'])['controls'].items():
            record = bundle['controls'][name]
            # Recompute the entire snapshot, including the patch and frozen files.
            folder = runtime.root / 'profile-evidence' / name
            if json.loads((folder / 'snapshot.json').read_text()) != record:
                return dict(result, reason='Frozen control snapshot changed')
            for i, check in enumerate(record['checks']):
                if json.loads((folder / f'check-{i:02}.json').read_text()) != check:
                    return dict(result, reason='Frozen check evidence changed')
            initial = json.loads(runtime.initial.read_text())
            changed = {p for p in set(initial) | set(record['tree']) if initial.get(p) != record['tree'].get(p)}
            if (not changed.issubset(runtime.paths) or (name == 'original' and changed)
                    or (name != 'original' and not changed)):
                return dict(result, status='rejected', reason='Control scope differs')
            from workbench.real_workspace import sha
            if sha((folder / 'changes.patch').read_bytes()) != record['patch_sha256']:
                return dict(result, reason='Frozen control patch changed')
            if name != 'original' and sha((folder / 'input.patch').read_bytes()) != record['input_patch_sha256']:
                return dict(result, reason='Task-specific control input changed')
            if name == 'original' and json.loads((runtime.root / 'fresh-baseline-suite.json').read_text()) != record['checks'][-1]:
                return dict(result, reason='Baseline suite evidence changed')
            for path in changed:
                if sha((folder / 'files' / path).read_bytes()) != record['tree'][path]:
                    return dict(result, reason='Frozen control file changed')
            status, reason = evaluate_snapshot(runtime, record, expected)
            if status != 'certified':
                return dict(result, status=status, reason=f'{name}: {reason}')
        return dict(result, status='certified', reason='Independent profile controls certified',
                    binding_sha256=digest(bundle['binding']), evidence_sha256=digest(bundle))
    except (OSError, KeyError, TypeError, ValueError, AttributeError, subprocess.SubprocessError):
        return result


def control_certificate(runtime):
    """Resolve immutable control observations without rewriting their commands.

    Reuse concerns pre-model controls only. Candidate checks always use the new
    workspace and commands, and every run must prove its own sandbox boundary.
    """
    from workbench.real_workspace import Runtime, sha, tree
    from workbench.trusted_preparation import owned

    reference = runtime.root/'profile-reference.json'
    if not reference.exists():
        return certify(runtime,json.loads((runtime.root/'profile-evidence.json').read_text()))
    owned(reference)
    if reference.stat().st_mode&0o077 or reference.stat().st_size>4096:
        raise ValueError('Private bounded profile reference required')
    value=json.loads(reference.read_text())
    source=owned(value['source'])
    if source==runtime.root or (source/'profile-reference.json').exists():
        raise ValueError('Nested or recursive profile references are not permitted')
    previous=Runtime(source)
    if (value.get('version')!=1 or previous.recipe!=runtime.recipe
            or binding(previous)!=binding(runtime)
            or tree(previous.workspace)!=json.loads(previous.initial.read_text())
            or value['evidence_sha256']!=sha((source/'profile-evidence.json').read_bytes())
            or value['controls_sha256']!=sha((source/'controls-passed.json').read_bytes())):
        raise ValueError('Prepared profile inputs or source evidence changed')
    baseline=source/'fresh-baseline-suite.json'
    observed=baseline.stat().st_mtime_ns
    for name in ('bash','lua5.1','python3'):
        info=(Path('/usr/bin')/name).stat()
        if info.st_uid!=0 or info.st_mode&0o022 or max(info.st_mtime_ns,info.st_ctime_ns)>observed:
            raise ValueError('Interpreter changed since prepared control observations')
    if not previous.controls_valid():
        raise ValueError('Source profile or its original sandbox evidence is invalid')
    certificate=certify(previous,json.loads((source/'profile-evidence.json').read_text()))
    if certificate['status']!='certified':
        raise ValueError('Source profile controls are no longer certified')
    return certificate


def reuse_controls(runtime, source, patches):
    """Reuse an exact approved preparation; never reuse candidate correctness."""
    import os
    from workbench.real_workspace import Runtime, durable, sha, tree
    from workbench.trusted_preparation import owned

    if source is None:
        return False
    folder=owned(source)
    previous=Runtime(folder)
    if (previous.recipe!=runtime.recipe or binding(previous)!=binding(runtime)
            or tree(runtime.workspace)!=json.loads(runtime.initial.read_text())):
        raise ValueError('Control reuse requires an identical recipe and a fresh base copy')
    if (folder/'profile-reference.json').exists() or not previous.controls_valid():
        raise ValueError('Only the original fully certified preparation can supply controls')
    expected=set(requirements(runtime.recipe['verification_profile'])['controls'])-{'original'}
    if set(patches)!=expected or any((folder/'profile-evidence'/name/'input.patch').read_text()!=patches[name]
                                    for name in expected):
        raise ValueError('Task-specific control patches changed')
    reference=dict(version=1,source=str(folder),
        evidence_sha256=sha((folder/'profile-evidence.json').read_bytes()),
        controls_sha256=sha((folder/'controls-passed.json').read_bytes()))
    path=runtime.root/'profile-reference.json'
    # Privacy must not depend on the invoking shell's umask. Exclusive creation
    # also prevents replacing an old reference or following a supplied symlink.
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream:
        stream.write(json.dumps(reference,sort_keys=True).encode())
        stream.flush()
        os.fsync(stream.fileno())
    directory=os.open(runtime.root,os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    control_certificate(runtime)  # Recheck everything before accepting the reference.
    durable(runtime.root/'fresh-baseline-suite.json',(folder/'fresh-baseline-suite.json').read_bytes())
    return True


def prepare(runtime, control_patches):
    """Trusted offline preparation using existing Runtime checks/scope/freezing.

    No model or preflight/service/probe call. Call only on a fresh isolated Runtime
    with independently reviewed recipe/oracle and task-specific control patches.
    Every output is create-only. Failure retains evidence and never certifies it.
    """
    from workbench.real_workspace import durable, git, sha, tree

    validate_recipe(runtime.recipe)
    expected = requirements(runtime.recipe['verification_profile'])['controls']
    if set(control_patches) != set(expected) - {'original'}:
        raise ValueError('Exact task-specific controls required')
    initial = json.loads(runtime.initial.read_text())
    if tree(runtime.workspace) != initial or git(runtime.workspace, 'status', '--porcelain=v1', '--ignored'):
        raise ValueError('Fresh clean isolated base required')
    bundle = dict(binding=binding(runtime), controls={})
    for name in expected:
        patch = control_patches.get(name, '')
        folder = runtime.root / 'profile-evidence' / name
        if name != 'original':
            if not isinstance(patch, str) or not patch or len(patch.encode()) > 65536:
                raise ValueError('Bounded task-specific control patch required')
            durable(folder / 'input.patch', patch.encode())
            subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-C', str(runtime.workspace),
                            'apply', '--whitespace=nowarn', '-'], input=patch.encode(),
                           check=True, timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            _, patch = runtime.scope()  # Existing path/additive/line/mode/binary guards.
        current = tree(runtime.workspace)
        durable(folder / 'changes.patch', patch.encode())
        changed = [p for p in current if initial.get(p) != current[p]]
        for path in changed:
            durable(folder / 'files' / path, (runtime.workspace / path).read_bytes())
        checks = []
        commands = runtime.expected_checks()
        for i, argv in enumerate(commands):
            oracle = i == len(commands) - 2
            result = runtime.check(argv, runtime.workspace / 'tests' if i in (len(commands)-3, len(commands)-2)
                                   else runtime.workspace, [runtime.root / 'oracle.lua'] if oracle else (),
                                   timeout=600 if i == len(commands)-1 else 120,
                                   private_loopback=i == len(commands)-1)
            checks.append(result)
            durable(folder / f'check-{i:02}.json', json.dumps(result, sort_keys=True).encode())
            if result['status'] == 'canceled':
                raise ValueError('Worker stopped during independent preparation. Partial checks were retained.')
        if tree(runtime.workspace) != current or binding(runtime) != bundle['binding']:
            raise ValueError('Control inputs changed during checks')
        record = dict(tree=current, patch_sha256=sha(patch.encode()), checks=checks)
        if name != 'original':
            record['input_patch_sha256'] = sha(control_patches[name].encode())
        else:
            durable(runtime.root / 'fresh-baseline-suite.json', json.dumps(checks[-1], sort_keys=True).encode())
        durable(folder / 'snapshot.json', json.dumps(record, sort_keys=True).encode())
        bundle['controls'][name] = record
        # Restore only files changed by this owned control, after preserving bytes.
        for path in changed:
            raw = subprocess.check_output(['git', '-C', str(runtime.workspace), 'show', runtime.base + ':' + path])
            (runtime.workspace / path).write_bytes(raw)
        if tree(runtime.workspace) != initial:
            raise ValueError('Control cleanup differs from base; evidence retained')
    durable(runtime.root / 'profile-evidence.json', json.dumps(bundle, sort_keys=True).encode())
    return bundle
