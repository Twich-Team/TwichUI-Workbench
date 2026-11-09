"""Scoped repair attempts and post-edit evidence have different responsibilities.

The existing grant, provider, tools and workspace own execution. This module
supplies no grant and no model-written implementation. Readiness proves only a
clean base, passing existing suite and actual confinement. Correctness additionally
requires new regression assertions to fail against unchanged original production
and pass with the frozen repair, alongside the complete candidate suite.
"""
import difflib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


def enabled(spec):
    return isinstance(spec,dict) and spec.get('repair_cycle')=='scoped-repair-v1'


def oracle(focused_test):
    if not re.fullmatch(r'tests/test_[A-Za-z0-9_-]+\.lua',focused_test):
        raise ValueError('An existing focused Lua test is required')
    # A real generic assertion runner, not a prewritten addon reproducer/solution.
    # It gains task-specific assertions only when the coding agent adds them.
    return ("local ok, failure = xpcall(function() dofile("+json.dumps(Path(focused_test).name)+") end, debug.traceback)\n"
            "if not ok then io.write(tostring(failure), '\\n') end\n"
            "io.write('WB_BEHAVIOR bug-reproducer ', ok and 'PASS' or 'FAIL', '\\n')\n"
            "if not ok then os.exit(1) end\n")


def readiness(runtime):
    """Pure revalidation of scope/sandbox readiness; never bug certification."""
    from workbench.real_workspace import sha,suite_inventory,require_safe_check
    path=runtime.root/'controls-passed.json'
    value=json.loads(path.read_text())
    baseline=json.loads((runtime.root/'fresh-baseline-suite.json').read_text())
    boundary=json.loads((runtime.root/'boundary-proof.json').read_text())
    require_safe_check(baseline)
    for check in boundary.get('checks',{}).values():
        require_safe_check(check)
    expected=dict(kind='scoped-repair-readiness-v1',verification='not_run',
        recipe_sha256=sha((runtime.root/'recipe.json').read_bytes()),
        base_manifest_sha256=sha(runtime.initial.read_bytes()),
        baseline_sha256=sha((runtime.root/'fresh-baseline-suite.json').read_bytes()),
        boundary_sha256=sha((runtime.root/'boundary-proof.json').read_bytes()))
    if (value!=expected or not enabled(runtime.recipe)
            or runtime.oracle_source.read_text()!=oracle(runtime.recipe['focused_test'])
            or not suite_inventory(baseline,runtime.expected_suite())
            or not boundary.get('checks')
            or any(c.get('status')!='pass' or c.get('rc')!=0 or c.get('usage',{}).get('cgroup_removed') is not True
                   or c.get('usage',{}).get('leaked_after_kill') for c in boundary['checks'].values())):
        raise ValueError('Scoped repair readiness changed; this is not a correctness certificate')
    return expected


def record_readiness(runtime,boundary):
    from workbench.real_workspace import durable,sha
    durable(runtime.root/'boundary-proof.json',Path(boundary).read_bytes())
    value=dict(kind='scoped-repair-readiness-v1',verification='not_run',
        recipe_sha256=sha((runtime.root/'recipe.json').read_bytes()),
        base_manifest_sha256=sha(runtime.initial.read_bytes()),
        baseline_sha256=sha((runtime.root/'fresh-baseline-suite.json').read_bytes()),
        boundary_sha256=sha((runtime.root/'boundary-proof.json').read_bytes()))
    durable(runtime.root/'controls-passed.json',json.dumps(value,sort_keys=True).encode())
    return readiness(runtime)


def new_assertion_failure(result,path,original,candidate):
    """Syntax, missing fixtures and unrelated crashes are not original-bug proof."""
    from workbench.verification_profiles import behavioral_outcome
    if behavioral_outcome(result,'bug-reproducer')!='fail':
        return False
    added=set()
    for tag,_,_,start,end in difflib.SequenceMatcher(None,original.splitlines(),candidate.splitlines(),autojunk=False).get_opcodes():
        if tag=='insert':
            added.update(range(start+1,end+1))
        elif tag!='equal':
            return False  # Existing tests remain intact.
    output=result.get('out','')
    if re.search(r'cannot open|No such file|module .+not found|syntax error|attempt to (?:call|index)|bad argument',output,re.I):
        return False
    frames=re.findall(r'(?<![\w/])'+re.escape(Path(path).name)+r':([0-9]+):',output)
    return bool(added and re.search(r"\bin function ['\"]assert['\"]",output)
                and any(int(line) in added for line in frames))


def original_with_new_tests(runtime,folder,permitted):
    """Independent original production + frozen candidate test additions only.

    No implementation edit or model tool can reach this sibling clone. It is
    removed only after exact source/test/check evidence has been saved.
    """
    from workbench.real_workspace import durable,git,sha,tree
    changed,_=runtime.scope()
    tests=[p for p in changed if p.startswith('tests/test_')]
    focused=runtime.recipe['focused_test']
    if focused not in tests:
        return dict(status='fail',reason='Add regression assertions to the approved focused test while implementing the repair.')
    if not any(not p.startswith('tests/') for p in changed):
        return dict(status='fail',reason='A test-only change is not an addon repair.')
    parent=Path(tempfile.mkdtemp(prefix='workbench-verify-',dir=runtime.workspace.parent.parent))
    original_workspace=runtime.workspace
    clone=parent/'workspace'
    try:
        subprocess.run(['git','-c','core.hooksPath=/dev/null','-c','protocol.allow=never',
            '-c','protocol.file.allow=always','clone','--local','--no-hardlinks',
            '--no-recurse-submodules','--quiet',runtime.source,str(clone)],check=True,timeout=60)
        git(clone,'remote','remove','origin')
        git(clone,'config','core.hooksPath','/dev/null')
        if git(clone,'rev-parse','HEAD').strip()!=runtime.base:
            raise ValueError('Independent original source changed')
        initial=tree(clone)
        for path in tests:
            data=(original_workspace/path).read_bytes()
            durable(folder/'original-test-inputs'/path,data)
            (clone/path).write_bytes(data)
        current=tree(clone)
        if any(current.get(p)!=v for p,v in initial.items() if p not in tests):
            raise ValueError('Original production was modified during independent checking')
        if not permitted():
            raise ValueError('Run permission changed before original checking')
        runtime.workspace=clone
        result=runtime.check(runtime.expected_checks()[-2],clone/'tests',[runtime.root/'oracle.lua'],timeout=120)
        durable(folder/'original-with-new-tests.json',json.dumps(result,sort_keys=True).encode())
        old=git(clone,'show',runtime.base+':'+focused)
        fresh=(clone/focused).read_text()
        reproduced=new_assertion_failure(result,focused,old,fresh)
        proof=dict(status='pass' if reproduced else 'fail',
            reason='New regression assertions expose the original behavior.' if reproduced else
                'The new test must expose an assertion failure in unchanged original production; syntax, fixture errors and already-passing tests do not qualify.',
            original_production_unchanged=True,base=runtime.base,
            test_sha256={p:sha((clone/p).read_bytes()) for p in tests},
            result_sha256=sha(json.dumps(result,sort_keys=True).encode()),
            diagnostic=result.get('out','')[-5000:])
        if tree(clone)!=current or not permitted():
            raise ValueError('Independent check inputs or permission changed')
        durable(folder/'original-test-differential.json',json.dumps(proof,sort_keys=True).encode())
        return proof
    finally:
        runtime.workspace=original_workspace
        shutil.rmtree(parent)


def verify_result(runtime,result):
    """Re-evaluate the saved original assertion evidence against frozen test bytes."""
    from workbench.real_workspace import git,sha
    relative=result.get('independent_check_evidence','')
    if not re.fullmatch(r'working-checks/[0-9]{2}',relative):
        return False
    folder=runtime.root/relative
    proof=json.loads((folder/'original-test-differential.json').read_text())
    original=json.loads((folder/'original-with-new-tests.json').read_text())
    focused=runtime.recipe['focused_test']
    frozen=runtime.root/'candidates'/result['candidate_id']/'files'
    tests=proof.get('test_sha256',{})
    if (proof.get('status')!='pass' or proof.get('base')!=runtime.base or not proof.get('original_production_unchanged')
            or focused not in tests or proof.get('result_sha256')!=sha(json.dumps(original,sort_keys=True).encode())
            or any(path not in runtime.paths or not path.startswith('tests/test_')
                   or sha((frozen/path).read_bytes())!=value or result['manifest']['tree'].get(path)!=value
                   for path,value in tests.items())):
        return False
    return new_assertion_failure(original,focused,git(Path(runtime.source),'show',runtime.base+':'+focused),
        (frozen/focused).read_text())


def reuse_baseline(runtime,source):
    """Reuse a verified unchanged-base observation, never candidate correctness.

    Source comes only from the canonical approved offer. A different recipe,
    source tree, checker or changed interpreter falls back to a fresh baseline.
    The new execution boundary is always probed afresh; post-edit checks rerun.
    """
    if not source:
        return None
    from workbench.real_workspace import Runtime,durable,sha,tree
    from workbench.trusted_preparation import owned
    root=owned(source)
    previous=Runtime(root)
    readiness(previous)
    if (previous.recipe!=runtime.recipe or sha(previous.initial.read_bytes())!=sha(runtime.initial.read_bytes())
            or tree(runtime.workspace)!=json.loads(runtime.initial.read_text())):
        return None
    release=root.parents[3]
    current=Path(__file__).resolve().parents[2]
    for name in ('real_workspace.py','real_confine.py','real_sandbox.py','verification_process.py'):
        old=release/'backend/workbench'/name
        if not old.is_file() or old.is_symlink() or old.read_bytes()!=(current/'backend/workbench'/name).read_bytes():
            return None
    baseline=root/'fresh-baseline-suite.json'
    observed=baseline.stat().st_mtime_ns
    for name in ('bash','lua5.1','python3'):
        path=Path('/usr/bin')/name
        info=path.stat()
        if info.st_uid!=0 or info.st_mode&0o022 or max(info.st_mtime_ns,info.st_ctime_ns)>observed:
            return None
    value=json.loads(baseline.read_text())
    durable(runtime.root/'baseline-reuse.json',json.dumps(dict(
        source=str(root),baseline_sha256=sha(baseline.read_bytes()),
        exact_recipe_and_base=True,checker_unchanged=True,interpreters_unchanged_since_observation=True,
        candidate_verification_reused=False,new_boundary_probe_required=True),sort_keys=True).encode())
    return value
