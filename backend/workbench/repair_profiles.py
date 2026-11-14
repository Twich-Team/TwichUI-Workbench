"""Versioned repair evidence adapters within the existing Runtime and journals."""
import json
from pathlib import Path
import re
from typing import Literal

from pydantic import Field
from workbench.bug_evidence import Data

PROFILE = 'offline-repair-v2'
CLASSES = ('existing-failure', 'missing-regression', 'test-correction', 'environment')
REQUIREMENTS = [
    'Original and candidate identities, frozen scope and independently designed original-source checks remain separate.',
    'The reported original failure is retained; all committed Lua and Python regressions must pass on the exact candidate.',
    'Existing failing tests remain unchanged for production repair. Test corrections require independent original behavior and negative-control evidence.',
    'Candidate-authored tests alone do not establish independent behavioral verification. No empty or environment-only patch can qualify.',
]


class Profile(Data):
    version: Literal[2] = 2
    kind: Literal['existing-failure', 'missing-regression', 'test-correction', 'environment']


class RequirementEvidence(Data):
    criterion: str = Field(min_length=1,max_length=48)
    kind: Literal['report','source-contract']
    path: str | None = None
    quote: str = Field(min_length=10,max_length=800)


class ControlReplacement(Data):
    path: str = Field(min_length=1,max_length=240)
    old: str = Field(min_length=1,max_length=2000)
    new: str = Field(max_length=2000)


class IndependentProgram(Data):
    evidence: list[RequirementEvidence] = Field(default_factory=list,max_length=12)
    rationale: str = Field(min_length=20, max_length=1200)
    criteria: list[str] = Field(min_length=1, max_length=12)
    program: str = Field(min_length=20, max_length=16000)
    negative_control: list[ControlReplacement] = Field(default_factory=list, max_length=2)


def selected(spec):
    return isinstance(spec, dict) and spec.get('repair_cycle') == 'scoped-repair-v2'


def selection():
    return dict(id=PROFILE, requirements=REQUIREMENTS, status='not_run', execution_authorized=False)


def language(focused):
    return 'python' if focused.endswith('.py') else 'lua'


def focused_command(path):
    from workbench.real_workspace import PRELUDE
    return (['python3', '-B', '-m', 'unittest', '-v', Path(path).stem] if language(path)=='python'
        else ['lua5.1', '-e', PRELUDE, Path(path).name])


def syntax_command(path):
    if path.endswith('.py'):
        return ['python3', '-B', '-c', 'import ast,sys;ast.parse(open(sys.argv[1],encoding="utf-8").read(),filename=sys.argv[1])', path]
    return ['luac5.1', '-p', path]


def oracle(path, program=None):
    if program is not None:
        # Program executes as an assertion body; only the application emits a
        # verdict after the body actually finishes. Models cannot emit verdicts.
        if 'WB_BEHAVIOR' in program or len(program.encode())>16000:
            raise ValueError('Independent programs cannot supply application verdict markers')
        if language(path)=='python':
            return ('import traceback,sys\nsys.stderr=sys.stdout\nok=False\ntry:\n'+''.join('    '+line+'\n' for line in program.splitlines())+
                '    ok=True\nexcept Exception:\n    traceback.print_exc()\nprint("WB_BEHAVIOR bug-reproducer "+("PASS" if ok else "FAIL"))\nraise SystemExit(0 if ok else 1)\n')
        return ('local ok,err=xpcall(function()\n'+program+'\nend,debug.traceback)\nif not ok then print(err) end\n'
            "print('WB_BEHAVIOR bug-reproducer '..(ok and 'PASS' or 'FAIL'))\nos.exit(ok and 0 or 1)\n")
    if language(path)=='python':
        return ('import unittest,sys,os\nsys.stderr=sys.stdout\nsys.path.insert(0,os.getcwd())\nsuite=unittest.defaultTestLoader.loadTestsFromName('+json.dumps(Path(path).stem)+')\n'
            'count=suite.countTestCases()\nresult=unittest.TextTestRunner(stream=sys.stdout,verbosity=2).run(suite)\n'
            'ok=count>len(result.skipped) and result.wasSuccessful()\nprint("WB_BEHAVIOR bug-reproducer "+("PASS" if ok else "FAIL"))\nraise SystemExit(0 if ok else 1)\n')
    from workbench.repair_cycle import oracle as lua_oracle
    return lua_oracle(path)


def commands(runtime):
    from workbench.real_workspace import PRELUDE
    focused = runtime.recipe['focused_test']
    behavior = (['python3', '-B', str(runtime.root/'oracle.lua')] if language(focused)=='python'
        else ['lua5.1', '-e', PRELUDE, str(runtime.root/'oracle.lua')])
    return [syntax_command(p) for p in runtime.paths]+[focused_command(focused), behavior,
        ['python3', '-B', str(Path(__file__).with_name('repair_suite.py')), str(runtime.root/'complete-test-inventory.json')]]


def complete_inventory(runtime):
    base = json.loads(runtime.initial.read_text())
    return sorted(Path(p).name for p in base if re.fullmatch(r'tests/test_[A-Za-z0-9_-]+\.(?:lua|py)', p))


def suite_observation(result, expected):
    from workbench.real_workspace import require_safe_check
    require_safe_check(result)
    output = result.get('out', '')
    passed = re.findall(r'^ok   (\S+)$', output, re.M)
    failed = re.findall(r'^FAIL (\S+)$', output, re.M)
    return (sorted(passed+failed)==sorted(expected) and len(passed+failed)==len(set(passed+failed))
        and output.strip().endswith(f'{len(passed)} passed, {len(failed)} failed')
        and result.get('rc') == int(bool(failed)))


def validate_spec(spec):
    from workbench.real_workspace import sha
    profile=Profile.model_validate(spec['repair_profile'])
    intent=spec['approved_intent']
    plan=intent['plan']
    if (spec['verification_profile']!=PROFILE or plan.get('repair_profile')!=profile.model_dump()
            or profile.kind=='environment' or spec['contract'].get('runtime_profile')!=PROFILE
            or spec['read_paths']!=plan['read_paths'] or spec['contract']['paths']!=plan['writable_paths']
            or spec['focused_test']!=plan['focused_test'] or spec['contract']['base']!=intent['base']):
        raise ValueError('Repair profile differs from the frozen source/intent')
    independent=spec.get('independent_program')
    if profile.kind in {'missing-regression','test-correction'} and not independent:
        raise ValueError('This repair profile requires an original-source independent behavior program')
    if independent:
        program=IndependentProgram.model_validate(independent)
        if set(program.criteria)!={c['id'] for c in plan['criteria']}:
            raise ValueError('Independent program must cover all frozen behavior criteria')
        if profile.kind=='test-correction' and {e.criterion for e in program.evidence}!={c['id'] for c in plan['criteria']}:
            raise ValueError('Test correction requires independent original requirement evidence for every criterion')
        if profile.kind=='test-correction' and not program.negative_control:
            raise ValueError('Test correction requires meaningful negative production controls')
    expected=oracle(spec['focused_test'], independent['program'] if independent else None)
    if spec['oracle_sha256']!=sha(expected.encode()):
        raise ValueError('Independent checking bytes changed')


def readiness(runtime):
    from workbench.real_workspace import sha, require_safe_check
    baseline=json.loads((runtime.root/'fresh-baseline-suite.json').read_text())
    marker=json.loads((runtime.root/'controls-passed.json').read_text())
    boundary=json.loads((runtime.root/'boundary-proof.json').read_text())
    behavior=json.loads((runtime.root/'original-behavior.json').read_text())
    from workbench.verification_profiles import behavioral_outcome
    expected_behavior='pass' if runtime.recipe['repair_profile']['kind']=='test-correction' else 'fail'
    expected=dict(kind='scoped-repair-readiness-v2',verification='not_run',
        recipe_sha256=sha((runtime.root/'recipe.json').read_bytes()),
        base_manifest_sha256=sha(runtime.initial.read_bytes()),
        baseline_sha256=sha((runtime.root/'fresh-baseline-suite.json').read_bytes()),
        original_behavior_sha256=sha((runtime.root/'original-behavior.json').read_bytes()),
        boundary_sha256=sha((runtime.root/'boundary-proof.json').read_bytes()),
        inventory_sha256=sha((runtime.root/'complete-test-inventory.json').read_bytes()))
    if (marker!=expected or not suite_observation(baseline,runtime.expected_suite())
            or behavioral_outcome(behavior,'bug-reproducer')!=expected_behavior
            or not boundary.get('checks') or any(c.get('status')!='pass' for c in boundary['checks'].values())):
        raise ValueError('Repair readiness or observed original failure changed')
    for check in boundary['checks'].values():
        require_safe_check(check)
    validate_spec(runtime.recipe)
    if runtime.recipe.get('independent_program'):
        validate_program_reference(runtime.recipe,runtime.root)
    if json.loads((runtime.root/'complete-test-inventory.json').read_text())!=complete_inventory(runtime):
        raise ValueError('Complete committed test inventory changed')
    return expected


def record_readiness(runtime,boundary):
    from workbench.real_workspace import durable, sha
    durable(runtime.root/'boundary-proof.json',Path(boundary).read_bytes())
    value=dict(kind='scoped-repair-readiness-v2',verification='not_run',
        recipe_sha256=sha((runtime.root/'recipe.json').read_bytes()),
        base_manifest_sha256=sha(runtime.initial.read_bytes()),
        baseline_sha256=sha((runtime.root/'fresh-baseline-suite.json').read_bytes()),
        original_behavior_sha256=sha((runtime.root/'original-behavior.json').read_bytes()),
        boundary_sha256=sha((runtime.root/'boundary-proof.json').read_bytes()),
        inventory_sha256=sha((runtime.root/'complete-test-inventory.json').read_bytes()))
    durable(runtime.root/'controls-passed.json',json.dumps(value,sort_keys=True).encode())
    return readiness(runtime)


def prepare_original(runtime, baseline):
    """Capture real failures without requiring the reported baseline to pass."""
    from workbench.real_workspace import durable, tree, require_safe_check
    from workbench.verification_profiles import behavioral_outcome
    if not suite_observation(baseline, runtime.expected_suite()):
        raise ValueError('Complete baseline inventory is missing or infrastructure failed')
    before=tree(runtime.workspace)
    focus=runtime.check(runtime.expected_checks()[-3],runtime.workspace/'tests',timeout=120)
    require_safe_check(focus)
    durable(runtime.root/'original-focused.json',json.dumps(focus,sort_keys=True).encode())
    behavior=runtime.check(runtime.expected_checks()[-2],runtime.workspace/'tests',[runtime.root/'oracle.lua'],timeout=120)
    require_safe_check(behavior)
    durable(runtime.root/'original-behavior.json',json.dumps(behavior,sort_keys=True).encode())
    kind=runtime.recipe['repair_profile']['kind']
    expected='pass' if kind=='test-correction' else 'fail'
    if behavioral_outcome(behavior,'bug-reproducer')!=expected or tree(runtime.workspace)!=before:
        raise ValueError('Independent original behavior does not establish the selected repair class')
    from workbench.investigation_checks import classify
    if kind in {'existing-failure','test-correction'} and (focus['status']!='fail' or classify(focus)!='assertion-failure'):
        raise ValueError('This profile requires an actual original assertion failure; environment failures remain diagnosed, not repaired')
    # An independent program is frozen before any candidate exists. This record
    # never claims that candidate-written tests were independently designed.
    if kind in {'missing-regression','test-correction'}:
        if not (runtime.root/'independent-program-reference.json').exists():
            raise ValueError('Original-only independent program authorship is missing')
        validate_program_reference(runtime.recipe, runtime.root)


def validate_program_reference(spec, root):
    from workbench.real_workspace import sha
    from workbench.trusted_preparation import owned
    ref=json.loads(owned(Path(root)/'independent-program-reference.json').read_text())
    original=owned(ref['path'])
    value=json.loads(original.read_text())
    if (sha(original.read_bytes())!=ref['sha256'] or value['program']!=spec['independent_program']
            or value['intent_digest']!=spec['approved_intent']['review_digest']
            or value.get('source_basis')!='original-only' or not value.get('review',{}).get('ready')
            or not value.get('model_attempt_id')):
        raise ValueError('Independent checking program provenance changed')
    return ref


def _negative(runtime, folder, permitted):
    """Original-only mutant + candidate tests; no write to original/candidate."""
    import shutil
    import subprocess
    import tempfile
    from workbench.real_workspace import git, tree, durable, sha
    from workbench.verification_profiles import behavioral_outcome
    program=runtime.recipe['independent_program']
    mutations=program['negative_control']
    correction=runtime.recipe['repair_profile']['kind']=='test-correction'
    if correction and not mutations:
        raise ValueError('Test correction needs a meaningful negative control')
    if not correction and mutations:
        raise ValueError('Missing-regression controls must retain unchanged original production')
    parent=Path(tempfile.mkdtemp(prefix='workbench-verify-',dir=runtime.workspace.parent.parent))
    candidate=runtime.workspace
    clone=parent/'workspace'
    try:
        subprocess.run(['git','-c','core.hooksPath=/dev/null','-c','protocol.allow=never','-c','protocol.file.allow=always',
            'clone','--local','--no-hardlinks','--no-recurse-submodules','--quiet',runtime.source,str(clone)],check=True,timeout=60)
        git(clone,'remote','remove','origin')
        git(clone,'config','core.hooksPath','/dev/null')
        if git(clone,'rev-parse','HEAD').strip()!=runtime.base:
            raise ValueError('Negative-control source changed')
        for mutation in mutations:
            if (set(mutation)!={'path','old','new'} or mutation['path'] not in runtime.read_paths
                    or mutation['path'].startswith('tests/') or not mutation['old'] or mutation['old']==mutation['new']
                    or len(mutation['old'])+len(mutation['new'])>4000):
                raise ValueError('Negative controls require a bounded original production mutation')
            target=clone/mutation['path']
            if target.is_symlink() or not target.resolve().is_relative_to(clone.resolve()):
                raise ValueError('Negative control path escaped its copy')
            old=target.read_text()
            if old.count(mutation['old'])!=1:
                raise ValueError('Negative control no longer matches original source')
            target.write_text(old.replace(mutation['old'],mutation['new'],1))
        for path in runtime.paths:
            if path.startswith('tests/test_'):
                (clone/path).write_bytes((candidate/path).read_bytes())
            elif correction:
                raise ValueError('Test-correction profile cannot modify production')
        before=tree(clone)
        runtime.workspace=clone
        if not permitted():
            raise ValueError('Permission changed before negative-control dispatch')
        check=runtime.check(runtime.expected_checks()[-2],clone/'tests',[runtime.root/'oracle.lua'],timeout=120)
        focus=runtime.check(runtime.expected_checks()[-3],clone/'tests',timeout=120)
        from workbench.investigation_checks import classify
        passed=(behavioral_outcome(check,'bug-reproducer')=='fail' and classify(focus)=='assertion-failure'
            and focus['status']=='fail' and changed_assertion_failure(runtime,focus,candidate) and tree(clone)==before and permitted())
        for name,result in [('negative-behavior',check),('negative-corrected-test',focus)]:
            durable(folder/(name+'.json'),json.dumps(result,sort_keys=True).encode())
        return dict(passed=bool(passed),mutations=mutations,tree=before,
            behavior_sha256=sha((folder/'negative-behavior.json').read_bytes()),
            test_sha256=sha((folder/'negative-corrected-test.json').read_bytes()))
    finally:
        runtime.workspace=candidate
        shutil.rmtree(parent)


def original_comparison(runtime, folder, permitted):
    from workbench.real_workspace import durable, git, sha
    from workbench.verification_profiles import behavioral_outcome
    kind=runtime.recipe['repair_profile']['kind']
    changed,_=runtime.scope()
    focus=runtime.recipe['focused_test']
    original=json.loads((runtime.root/'original-behavior.json').read_text())
    passed=behavioral_outcome(original,'bug-reproducer')==('pass' if kind=='test-correction' else 'fail')
    if kind=='existing-failure':
        passed=passed and focus not in changed and any(not p.startswith('tests/') for p in changed)
        # Keep the trusted failing original assertions byte-for-byte.
        passed=passed and (runtime.workspace/focus).read_text()==git(Path(runtime.source),'show',runtime.base+':'+focus)
    elif kind=='missing-regression':
        validate_program_reference(runtime.recipe,runtime.root)
        passed=passed and focus in changed and any(not p.startswith('tests/') for p in changed)
        negative=_negative(runtime,folder,permitted)
        durable(folder/'negative-control.json',json.dumps(negative,sort_keys=True).encode())
        passed=passed and negative['passed']
    elif kind=='test-correction':
        validate_program_reference(runtime.recipe,runtime.root)
        passed=passed and all(p.startswith('tests/test_') for p in changed)
        negative=_negative(runtime,folder,permitted)
        durable(folder/'negative-control.json',json.dumps(negative,sort_keys=True).encode())
        passed=passed and negative['passed']
    else:
        passed=False
    durable(folder/'original-with-new-tests.json',json.dumps(original,sort_keys=True).encode())
    proof=dict(version=2,status='pass' if passed else 'fail',kind=kind,base=runtime.base,
        original_behavior_sha256=sha((runtime.root/'original-behavior.json').read_bytes()),
        original_focused_sha256=sha((runtime.root/'original-focused.json').read_bytes()),
        baseline_sha256=sha((runtime.root/'fresh-baseline-suite.json').read_bytes()),
        source_before_candidate=True,independent_design='unchanged-original-test' if kind=='existing-failure' else 'original-only-program',
        reason='Original/candidate behavioral evidence meets this repair profile.' if passed else 'This candidate does not meet the frozen repair class; original failures and independent controls remain visible.')
    durable(folder/'original-test-differential.json',json.dumps(proof,sort_keys=True).encode())
    return proof


def verify_result(runtime,result):
    from workbench.real_workspace import sha, git
    from workbench.verification_profiles import behavioral_outcome
    relative=result.get('independent_check_evidence','')
    if not re.fullmatch(r'working-checks/[0-9]{2}',relative):
        return False
    folder=runtime.root/relative
    proof=json.loads((folder/'original-test-differential.json').read_text())
    kind=runtime.recipe['repair_profile']['kind']
    if (proof.get('status')!='pass' or proof.get('kind')!=kind or proof.get('base')!=runtime.base
            or not proof.get('source_before_candidate')
            or any(proof.get(key)!=sha((runtime.root/name).read_bytes()) for key,name in (
                ('original_behavior_sha256','original-behavior.json'),('original_focused_sha256','original-focused.json'),
                ('baseline_sha256','fresh-baseline-suite.json')))):
        return False
    original=json.loads((runtime.root/'original-behavior.json').read_text())
    if (json.loads((folder/'original-with-new-tests.json').read_text())!=original
            or behavioral_outcome(original,'bug-reproducer')!=('pass' if kind=='test-correction' else 'fail')):
        return False
    focused=runtime.recipe['focused_test']
    base=json.loads(runtime.initial.read_text())
    changed={p for p in result['manifest']['tree'] if result['manifest']['tree'][p]!=base.get(p)}
    if kind=='existing-failure':
        return focused not in changed and result['manifest']['tree'][focused]==sha(git(Path(runtime.source),'show',runtime.base+':'+focused).encode()) and any(not p.startswith('tests/') for p in changed)
    validate_program_reference(runtime.recipe,runtime.root)
    if kind=='missing-regression' and not (focused in changed and any(not p.startswith('tests/') for p in changed)):
        return False
    negative=json.loads((folder/'negative-control.json').read_text())
    behavior=json.loads((folder/'negative-behavior.json').read_text())
    test=json.loads((folder/'negative-corrected-test.json').read_text())
    from workbench.investigation_checks import classify
    return (kind in {'test-correction','missing-regression'} and (kind!='test-correction' or all(p.startswith('tests/test_') for p in changed))
        and negative['passed'] and negative['mutations']==runtime.recipe['independent_program']['negative_control']
        and negative['behavior_sha256']==sha((folder/'negative-behavior.json').read_bytes())
        and negative['test_sha256']==sha((folder/'negative-corrected-test.json').read_bytes())
        and negative['tree'][focused]==result['manifest']['tree'][focused]
        and behavioral_outcome(behavior,'bug-reproducer')=='fail' and classify(test)=='assertion-failure'
        and changed_assertion_failure(runtime,test,runtime.root/'candidates'/result['candidate_id']/'files'))


def changed_assertion_failure(runtime, observed, candidate):
    """The corrected/new assertion must reject the control, not an old check."""
    import difflib
    from workbench.real_workspace import git
    focus=runtime.recipe['focused_test']
    original=git(Path(runtime.source),'show',runtime.base+':'+focus).splitlines()
    changed=(Path(candidate)/focus).read_text().splitlines()
    lines=set()
    for tag,_,_,start,end in difflib.SequenceMatcher(None,original,changed,autojunk=False).get_opcodes():
        if tag in {'insert','replace'}:
            lines.update(range(start+1,end+1))
    name=re.escape(Path(focus).name)
    frames=re.findall(name+r':([0-9]+):',observed.get('out',''))
    frames+=re.findall(name+r'", line ([0-9]+)',observed.get('out',''))
    return bool(lines and any(int(line) in lines for line in frames))


def requirement_comment(text,quote,path):
    """Recognize actual Python comments/docstrings, including quoted body text."""
    if quote not in text:
        return False
    if not path.endswith('.py'):
        return quote.lstrip().startswith(('--','#','"""',"\'\'\'"))
    import ast
    import io
    import tokenize
    try:
        comments=[token.string for token in tokenize.generate_tokens(io.StringIO(text).readline)
                  if token.type==tokenize.COMMENT]
        for node in ast.walk(ast.parse(text)):
            if isinstance(node,(ast.Module,ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)):
                first=node.body[0] if node.body else None
                if isinstance(first,ast.Expr) and isinstance(first.value,ast.Constant) and isinstance(first.value.value,str):
                    comments.append(ast.get_source_segment(text,first.value) or '')
        return any(quote in comment for comment in comments)
    except (SyntaxError,tokenize.TokenError):
        return False


def validate_requirement_evidence(program,intent,original,feedback=None):
    """Ground intended behavior independently of the assertion being corrected."""
    if intent['plan']['repair_profile']['kind']!='test-correction':
        return
    from workbench import regression_preparation as source
    root,base,_=source.repository()
    if base!=intent['base']:
        raise ValueError('Source changed before requirement evidence validation')
    if {e.criterion for e in program.evidence}!={c['id'] for c in intent['plan']['criteria']}:
        raise ValueError('Every corrected test criterion needs original requirement evidence')
    if feedback:
        from workbench.visual_scope import digest
        if intent.get('report_binding')!=digest(dict(original=original,feedback=feedback)):
            raise ValueError('Owner clarification differs from the frozen original requirement binding')
    for evidence in program.evidence:
        if evidence.kind=='report':
            valid=evidence.path is None and (evidence.quote in original['description'] or bool(feedback and evidence.quote in feedback))
        else:
            valid=(evidence.path in intent['plan']['read_paths'] and evidence.path!=intent['plan']['focused_test']
                and requirement_comment(source.git(root,'show',base+':'+evidence.path).decode(),evidence.quote,evidence.path))
        if not valid:
            raise ValueError('Test correction cites no valid independent original requirement evidence')
