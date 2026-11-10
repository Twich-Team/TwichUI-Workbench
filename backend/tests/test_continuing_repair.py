"""No live provider: genuine ongoing permission, observations and bounded recovery."""
import json
import subprocess
from types import SimpleNamespace

import pytest

from workbench import continuing_repair as continuing
from workbench.real_execution import PATHS, digest
from workbench.real_provider import implement, BudgetError
from workbench.real_workspace import Tools
from workbench.real_feedback import RepairFeedback, CheckFeedback
from workbench.real_tool_contract import ToolBudgetExceeded
from workbench.lua_failure_context import command
from workbench.real_usage import summarize
from test_real_execution import Transport, answer
from test_implementation_context import response


def approval():
    continuation=dict(scope_approval_id='original',scope_binding='a'*64,
        owner_provenance=dict(actor='owner',purpose=continuing.PURPOSE,
            authorization_sha256='b'*64,authorization_path='/private/controlled-owner.txt'))
    budget=dict(turns=None,fallback=False,publication='hold',activate_once=True)
    return dict(payload=dict(execution_continuation=continuation,run_budget=budget),
        binding=digest(dict(execution_continuation=continuation,run_budget=budget)))


@pytest.mark.parametrize('continued',[False,True])
def test_actual_tool_loop_can_continue_after_twenty_only_under_distinct_permission(tmp_path,continued):
    p=tmp_path/PATHS[0]
    p.parent.mkdir(parents=True)
    p.write_text(''.join('local controlled'+str(i)+' = 1\n' for i in range(26)))
    runtime=SimpleNamespace(workspace=tmp_path,read_paths=[PATHS[0]],paths=[PATHS[0]],
        workflow_task=dict(approval=approval()) if continued else {},
        recipe=dict(source_context=[{}],read_budget_policy='distinct-lines-v1',repair_cycle='scoped-repair-v1'),
        tool_commands=lambda:{'focused':['controlled-only']})
    Transport.responses=[response('read_file',dict(path=PATHS[0],start=i,end=i),i) for i in range(1,26)]+[answer()]
    Transport.bodies=[]
    turns=[]
    def run():
        return implement(Tools(runtime,lambda:True),'Controlled scoped task','','controlled',lambda:True,
            transport=Transport,on_turn=turns.append)
    if continued:
        result=run()
        assert len(result['log'])==len(turns)==26
    else:
        with pytest.raises(BudgetError,match='Twenty-turn'):
            run()
        assert len(turns)==20


def test_continuing_budget_cannot_be_forged_by_dropping_other_boundaries():
    value=approval()
    assert continuing.permitted_budget(value)
    for field,changed in [('fallback',True),('publication','draft-pr'),('activate_once',False),('turns',20)]:
        other=json.loads(json.dumps(value))
        other['payload']['run_budget'][field]=changed
        other['binding']=digest(dict(execution_continuation=other['payload']['execution_continuation'],run_budget=other['payload']['run_budget']))
        assert not continuing.permitted_budget(other)
    value['payload']['execution_continuation']['browser_request']={}
    assert not continuing.permitted_budget(value)


def test_continuation_observes_retained_check_before_generation_and_fences_revocation(tmp_path):
    (tmp_path/'repair-resume.json').write_text('{}')
    runtime=SimpleNamespace(root=tmp_path,paths=[PATHS[0]],read_paths=[PATHS[0]],
        workflow_task=dict(approval=approval()),recipe=dict(repair_cycle='scoped-repair-v1'),
        tool_commands=lambda:{'focused':['controlled-only']})
    checked=[]
    class WorkingTools:
        def __init__(self):
            self.runtime=runtime
        def handle(self,name,args):
            checked.append((name,args))
            return json.dumps(dict(status='fail',diagnostic='WB_LOCAL_CONTEXT observed=17'))
    Transport.responses=[answer()]
    Transport.bodies=[]
    implement(WorkingTools(),'Controlled task','','controlled',lambda:True,transport=Transport)
    assert checked==[('run_check',dict(name='focused'))]
    assert 'WB_LOCAL_CONTEXT observed=17' in Transport.bodies[0]['messages'][-1]['content']
    assert (tmp_path/'attempt/resumed-focused.json').is_file()
    checked.clear()
    with pytest.raises(BudgetError,match='Current permission'):
        implement(WorkingTools(),'Controlled task','','controlled',lambda:False,transport=Transport)
    assert not checked


def test_all_real_usage_is_observed_beyond_twenty_and_missing_receipt_stays_unknown(tmp_path):
    folder=tmp_path/'attempt'
    folder.mkdir()
    for i in range(24):
        common=dict(request_id='controlled',fence='same-fence',turn=i,request_limit=2496)
        (folder/f'request-{i:02}.json').write_text(json.dumps(common))
        (folder/f'turn-{i:02}.json').write_text(json.dumps(dict(**common,model='controlled',usage_validated=True,
            response_completed=True,usage=dict(prompt_tokens=30,completion_tokens=10))))
    result=summarize(tmp_path,'controlled','same-fence','controlled')['usage']
    assert result['requests_observed']==result['completed_responses']==24
    assert result['values']==dict(prompt_tokens=720,completion_tokens=240,total_tokens=960)
    (folder/'turn-23.json').unlink()
    result=summarize(tmp_path,'controlled','same-fence','controlled')['usage']
    assert result['status']=='partial' and result['unavailable_turns']==[23]
    assert result['known_values']['completion_tokens']==230 and result['values']['completion_tokens'] is None


def test_failed_working_check_retains_real_locals_and_failure_without_changing_independent_checks(tmp_path):
    test=tmp_path/'test_controlled.lua'
    test.write_text('local values = {[2]=true,[5]=true}\nlocal observed = 17\nassert(observed == 19, "controlled actual mismatch")\n')
    argv=['lua5.1','-e','local prelude = true',test.name]
    observed=command(argv,'tests/'+test.name)
    result=subprocess.run(observed,cwd=tmp_path,capture_output=True,text=True,timeout=5)
    assert result.returncode==1 and "in function 'assert'" in result.stderr
    assert 'observed=17' in result.stderr and 'controlled actual mismatch' in result.stderr
    assert 'observed_entries=2' in result.stderr
    before=test.read_bytes()
    original=subprocess.run(argv,cwd=tmp_path,capture_output=True,text=True,timeout=5)
    assert original.returncode==1 and 'WB_LOCAL_CONTEXT' not in original.stderr and test.read_bytes()==before
    summary=CheckFeedback(tmp_path,['tests/'+test.name]).record(dict(status='fail',rc=1,out=result.stderr),'fixed-000')
    assert 'observed=17' in summary['diagnostic']
    with pytest.raises(ValueError):
        command(['bash','-c','anything'],'tests/'+test.name)


def test_reverted_workspace_loop_is_recognized_across_intervening_edits(tmp_path):
    file=tmp_path/'controlled.lua'
    file.write_text('return 1\n')
    runtime=SimpleNamespace(workspace=tmp_path,paths=['controlled.lua'])
    feedback=RepairFeedback(runtime)
    failure=dict(status='fail',diagnostic='Controlled wrong behavior')
    feedback.checked('focused',failure)
    file.write_text('return 2\n')
    feedback.edited()
    file.write_text('return 1\n')
    feedback.edited()
    assert feedback.checked('focused',failure)['previously_rejected_workspace']
    with pytest.raises(ToolBudgetExceeded,match='changed strategy'):
        feedback.checked('focused',failure)
