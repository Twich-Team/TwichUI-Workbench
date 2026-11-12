"""Failure diagnostics preserve suite rejection and never grant new tools."""
import json
from types import SimpleNamespace

import pytest

from workbench.suite_failure_detail import observe, failed_tests
from workbench.real_tool_contract import ToolDenied
from workbench.real_feedback import CheckFeedback


def fixture(tmp_path, outcome='fail'):
    workspace=tmp_path/'workspace'
    (workspace/'tests').mkdir(parents=True)
    (workspace/'tests/test_scope.lua').write_text('local actual = 1\nassert(actual == 2, "expected two")\n')
    calls=[]
    def check(argv,cwd,**kwargs):
        calls.append((argv,cwd,kwargs))
        return dict(status=outcome,rc=int(outcome!='pass'),out=(
            'test_scope.lua:2: expected two\nstack traceback:\n'
            'WB_LOCAL_CONTEXT test_scope.lua:2 actual=1\n'),usage=dict(cgroup_removed=True))
    runtime=SimpleNamespace(workspace=workspace,base='a'*40,check=check,
        expected_suite=lambda:['test_scope.lua'])
    return runtime,calls


def test_fixed_failure_observation_preserves_assertion_values_source_and_revision(tmp_path):
    runtime,calls=fixture(tmp_path)
    result=dict(out='FAIL test_scope.lua\n\t[C]: ?\n177 passed, 1 failed')
    details=observe(runtime,result,tmp_path/'evidence',lambda:True)
    item=details[0]
    assert item['line']==2 and 'assert(actual == 2' in item['source_excerpt']
    assert item['base']==runtime.base and len(item['source_sha256'])==64
    summary=CheckFeedback(tmp_path,[]).record(item['result'],'fixed-000')
    assert 'expected two' in summary['diagnostic'] and 'actual=1' in summary['diagnostic']
    assert calls[0][0][:2]==['lua5.1','-e']
    assert 'xpcall' in calls[0][0][-1]
    saved=json.loads((tmp_path/'evidence/failure-detail-test_scope.lua.json').read_text())
    assert saved==item['result']


def test_failed_names_are_only_from_committed_suite_inventory(tmp_path):
    runtime,calls=fixture(tmp_path)
    result=dict(out='FAIL ../../outside.lua\nFAIL test_other.lua\nFAIL test_scope.lua\nFAIL test_scope.lua\n')
    assert failed_tests(runtime,result)==['test_scope.lua']
    with pytest.raises(ToolDenied,match='Permission changed'):
        observe(runtime,result,tmp_path/'evidence',lambda:False)
    assert not calls


def test_diagnostic_pass_does_not_rewrite_the_original_suite_failure(tmp_path):
    runtime,_=fixture(tmp_path,'pass')
    failed=dict(status='fail',rc=1,out='FAIL test_scope.lua\n0 passed, 1 failed')
    assert observe(runtime,failed,tmp_path/'evidence',lambda:True)[0]['result']['status']=='pass'
    assert failed['status']=='fail' and failed['rc']==1
