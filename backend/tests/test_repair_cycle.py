"""Actual Lua assertion classification; no live provider or sandbox claim."""
import subprocess

import pytest

from workbench.repair_cycle import oracle,new_assertion_failure


@pytest.mark.parametrize('fault',[None,'already_passes','syntax','missing_fixture','non_assertion','old_assertion','replaced_test'])
def test_original_evidence_must_be_a_new_behavior_assertion_not_a_fixture_crash(tmp_path,fault):
    original='local observed="unfixed"\nassert(type(observed)=="string")\n'
    candidate=original+'assert(observed=="fixed")\n'
    if fault=='already_passes':
        candidate=original+'assert(observed=="unfixed")\n'
    if fault=='syntax':
        candidate=original+'assert(\n'
    if fault=='missing_fixture':
        candidate=original+'assert(loadfile("missing.lua"))\n'
    if fault=='non_assertion':
        candidate=original+'MissingFixture()\n'
    if fault=='old_assertion':
        original='assert(false)\n'
        candidate=original+'assert(true)\n'
    if fault=='replaced_test':
        candidate='local observed="changed"\nassert(type(observed)=="string")\nassert(observed=="fixed")\n'
    (tmp_path/'test_example.lua').write_text(candidate)
    (tmp_path/'oracle.lua').write_text(oracle('tests/test_example.lua'))
    run=subprocess.run(['lua5.1','oracle.lua'],cwd=tmp_path,capture_output=True,timeout=5)
    result=dict(status='pass' if run.returncode==0 else 'fail',rc=run.returncode,
        out=(run.stdout+run.stderr).decode(),usage=dict(cgroup_removed=True))
    assert new_assertion_failure(result,'tests/test_example.lua',original,candidate) is (fault is None)


@pytest.mark.parametrize('path',['../test.lua','tests/../test.lua','/tmp/test.lua','tests/run.sh','tests/test_x.lua;os.execute("x")'])
def test_fixed_assertion_runner_cannot_take_a_path_or_command_outside_the_reviewed_test(path):
    with pytest.raises(ValueError):
        oracle(path)
