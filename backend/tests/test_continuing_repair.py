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
from test_owner_task import owner as owner  # noqa: F401


def approval():
    continuation=dict(scope_approval_id='original',scope_binding='a'*64,
        owner_provenance=dict(actor='owner',purpose=continuing.PURPOSE,
            authorization_sha256='b'*64,authorization_path='/private/controlled-owner.txt'))
    budget=dict(turns=None,fallback=False,publication='hold',activate_once=True)
    return dict(payload=dict(execution_continuation=continuation,run_budget=budget),
        binding=digest(dict(execution_continuation=continuation,run_budget=budget)))


def test_reasoned_controls_are_distinct_bound_engineering_settings_not_an_upgrade_to_old_decisions(tmp_path):
    from workbench.schemas import ExecutionContinuationView,TaskRunApprovalInput
    from pydantic import ValidationError
    value=approval()
    runtime=SimpleNamespace(workflow_task=dict(approval=value))
    assert continuing.operational_controls(runtime) is None
    value['payload']['execution_continuation']['operational_controls']=dict(continuing.CONTROLS)
    assert not continuing.permitted_budget(value)  # Controls must be inside the fresh binding.
    value['binding']=digest(value['payload'])
    controls=continuing.operational_controls(runtime)
    assert controls['request_seconds']==300 and controls['decoding']['reasoning_budget_tokens']==768
    assert 'reasoning_effort' not in controls['decoding']
    changed=json.loads(json.dumps(value))
    changed['payload']['execution_continuation']['operational_controls']['request_seconds']=301
    changed['binding']=digest(changed['payload'])
    assert not continuing.permitted_budget(changed)
    # Public approval input has no channel for asserting trusted owner recovery.
    with pytest.raises(ValidationError):
        TaskRunApprovalInput.model_validate(dict(operational_controls=continuing.CONTROLS))
    with pytest.raises(ValidationError):
        ExecutionContinuationView.model_validate(dict(value['payload']['execution_continuation'],unexpected='no'))


def test_bound_reasoning_is_used_and_its_actual_total_usage_is_still_accounted(tmp_path):
    value=approval()
    value['payload']['execution_continuation']['operational_controls']=dict(continuing.CONTROLS)
    value['binding']=digest(value['payload'])
    runtime=SimpleNamespace(root=tmp_path,enforce_storage=lambda:None,workflow_task=dict(approval=value),recipe={})
    class NoTools:
        def __init__(self):self.runtime=runtime
    observation=[]
    reply=answer(80,60)
    reply['choices'][0]['message']['reasoning_content']='CONTROLLED private reasoning; no live model.'
    Transport.responses=[reply]
    Transport.bodies=[]
    result=implement(NoTools(),'Controlled task','','controlled',lambda:True,transport=Transport,on_request=observation.append)
    body=Transport.bodies[0]
    assert body['reasoning_budget_tokens']==768 and body['chat_template_kwargs']==dict(enable_thinking=True)
    assert body['reasoning_format']=='deepseek' and 'reasoning_effort' not in body
    assert body['max_tokens']==2496 and observation[0]['request_seconds']==300
    assert result['completion_tokens']==reply['usage']['completion_tokens']
    assert result['prompt_tokens']==reply['usage']['prompt_tokens']


def test_settled_truncated_output_is_saved_but_never_dispatched_and_can_request_a_smaller_complete_operation(tmp_path):
    value=approval()
    runtime=SimpleNamespace(root=tmp_path,workflow_task=dict(approval=value),recipe={},enforce_storage=lambda:None)
    class NoTools:
        def __init__(self):self.runtime=runtime
        def handle(self,*args):pytest.fail('A truncated tool batch must never dispatch')
    partial=answer(2496)
    partial['choices'][0].update(finish_reason='length',message=dict(content='CONTROLLED incomplete output',
        reasoning_content='CONTROLLED private thinking',tool_calls=[dict(id='complete-but-rejected',
            function=dict(name='read_file',arguments=json.dumps(dict(path='ignored.lua')))),dict(id='partial',
            function=dict(name='replace_text',arguments='{"path":'))]))
    Transport.responses=[partial,answer(30)]
    Transport.bodies=[]
    observations=[]
    result=implement(NoTools(),'Controlled ongoing work','NEVER-ACTUAL-SECRET','controlled',lambda:True,
        transport=Transport,on_turn=observations.append)
    saved=json.loads((tmp_path/'attempt/response-00.json').read_text())
    assert saved['transport_completed'] and saved['response']==partial
    assert 'NEVER-ACTUAL-SECRET' not in (tmp_path/'attempt/response-00.json').read_text()
    assert (tmp_path/'attempt/response-00.json').stat().st_mode & 0o077 == 0
    assert result['completion_tokens']==2526
    assert observations[0]['response_completed'] and observations[0]['finish_reason']=='length'
    assert 'No tool from it ran' in Transport.bodies[1]['messages'][-1]['content']
    assert all('CONTROLLED incomplete output' not in str(m) for m in Transport.bodies[1]['messages'])


def test_repeated_cutoff_without_a_workspace_change_stops_for_diagnosis_without_fabricating_completion(tmp_path):
    value=approval()
    runtime=SimpleNamespace(root=tmp_path,workflow_task=dict(approval=value),recipe={},enforce_storage=lambda:None)
    tools=SimpleNamespace(runtime=runtime)
    partial=answer(2496)
    partial['choices'][0]['finish_reason']='length'
    Transport.responses=[partial,partial]
    Transport.bodies=[]
    observed=[]
    with pytest.raises(BudgetError,match='Repeated truncated output'):
        implement(tools,'Controlled ongoing work','','controlled',lambda:True,transport=Transport,on_turn=observed.append)
    assert len(observed)==2 and all(x['usage_validated'] for x in observed)
    assert (tmp_path/'attempt/response-01.json').exists()
    assert len(Transport.bodies)==2


def test_uncertain_partial_response_is_retained_without_a_final_usage_claim_or_replay(tmp_path):
    runtime=SimpleNamespace(root=tmp_path,workflow_task=dict(approval=approval()),recipe={},enforce_storage=lambda:None)
    class Broken:
        def __init__(self,*args):self.ends_at=float('inf')
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,*args,**kwargs):
            error=OSError('CONTROLLED disconnect')
            error.stream_partial_response=dict(choices=[dict(message=dict(content='partial'))])
            raise error
    observed=[]
    with pytest.raises(OSError,match='disconnect'):
        implement(SimpleNamespace(runtime=runtime),'Controlled ongoing work','','controlled',lambda:True,
            transport=Broken,on_turn=observed.append)
    saved=json.loads((tmp_path/'attempt/response-00.json').read_text())
    assert saved['transport_completed'] is False and 'usage' not in saved['response']
    assert observed==[]  # No usage receipt is invented; the execution owner records unknown.


@pytest.mark.parametrize('later',['none','edit','failed-check'])
def test_reasoned_scoped_cycle_keeps_all_tools_and_finishes_only_after_last_unchanged_independent_pass(tmp_path,later):
    value=approval()
    value['payload']['execution_continuation']['operational_controls']=dict(continuing.CONTROLS)
    value['binding']=digest(value['payload'])
    workspace=tmp_path/'workspace'
    workspace.mkdir()
    file=workspace/'source.lua'
    file.write_text('return 1\n')
    runtime=SimpleNamespace(root=tmp_path,workflow_task=dict(approval=value),workspace=workspace,
        paths=['source.lua'],read_paths=['source.lua'],enforce_storage=lambda:None,
        recipe=dict(repair_cycle='scoped-repair-v1',verification_profile='CONTROLLED'),
        tool_commands=lambda:{'focused':['CONTROLLED']})
    events=[]
    class WorkingTools:
        def __init__(self):self.runtime=runtime
        def handle(self,name,args):
            events.append((name,args))
            if name=='read_file':
                return json.dumps(dict(path='source.lua',total_lines=1,spans=[dict(start=1,end=1,text=file.read_text())]))
            if name=='replace_text':
                file.write_text('return 2\n')
                return 'CONTROLLED edit'
            assert name=='run_check'
            return json.dumps(dict(status='pass' if args['name']=='candidate' else 'fail',
                diagnostic='CONTROLLED observation',rc=0 if args['name']=='candidate' else 1))
    first=response('run_check',dict(name='candidate'),0)
    if later!='none':
        extra=(response('replace_text',dict(path='source.lua',old='return 1',new='return 2'),1) if later=='edit'
            else response('run_check',dict(name='focused'),1))
        first['choices'][0]['message']['tool_calls']+=extra['choices'][0]['message']['tool_calls']
    Transport.responses=[first]+([response('run_check',dict(name='candidate'),2)] if later!='none' else [])
    Transport.bodies=[]
    result=implement(WorkingTools(),'Controlled scoped repair','','controlled',lambda:True,
        transport=Transport,on_finish=lambda:pytest.fail('No extra generation/final callback after independently checked tools'))
    assert result['completion_reason']=='independent-candidate-checks-passed'
    assert len(Transport.bodies)==(1 if later=='none' else 2)
    assert all(body['tool_choice']=='required' for body in Transport.bodies)
    assert {t['function']['name'] for t in Transport.bodies[0]['tools']} >= {'read_file','find_text','replace_text','run_check'}
    assert sum(n=='run_check' and a['name']=='candidate' for n,a in events)==(1 if later=='none' else 2)


def test_usage_receipt_survives_private_response_storage_failure(tmp_path,monkeypatch):
    import workbench.real_provider as provider
    runtime=SimpleNamespace(root=tmp_path,workflow_task=dict(approval=approval()),recipe={},enforce_storage=lambda:None)
    Transport.responses=[answer(17)]
    Transport.bodies=[]
    monkeypatch.setattr(provider,'retain_response',lambda *args:(_ for _ in ()).throw(OSError('CONTROLLED evidence disk failure')))
    observed=[]
    with pytest.raises(OSError,match='disk failure'):
        implement(SimpleNamespace(runtime=runtime),'Controlled work','','controlled',lambda:True,
            transport=Transport,on_turn=observed.append)
    assert len(observed)==1 and observed[0]['usage_validated'] and observed[0]['usage']['completion_tokens']==17


def test_private_response_evidence_cannot_follow_a_symlink_or_overwrite_another_response(tmp_path):
    from workbench.real_provider import retain_response
    runtime=SimpleNamespace(root=tmp_path,enforce_storage=lambda:None)
    folder=tmp_path/'attempt'
    folder.mkdir()
    other=tmp_path/'unrelated'
    other.write_text('preserve')
    target=folder/'response-00.json'
    target.symlink_to(other)
    with pytest.raises(BudgetError,match='path changed'):
        retain_response(runtime,0,answer(),True)
    assert other.read_text()=='preserve'
    target.unlink()
    retain_response(runtime,0,answer(),True)
    with pytest.raises(ValueError,match='immutable'):
        retain_response(runtime,0,answer(29),True)


def test_new_grant_binds_recorded_operational_controls_without_changing_baseline_or_old_decision(store,monkeypatch,owner):
    from test_real_execution import ControlledRuntime
    from workbench import real_execution,task_execution,task_budget
    value=approval()
    value['payload']['execution_continuation']['operational_controls']=dict(continuing.CONTROLS)
    value['binding']=digest(value['payload'])
    scope=dict(approval=value)
    monkeypatch.setattr(task_execution,'snapshot',lambda *args:scope)
    monkeypatch.setattr(task_budget,'permission',lambda *args:dict(decision_id='CONTROLLED',
        offer=dict(snapshot=dict(specification=dict(contract=real_execution.contract())))))
    ref,_,_=owner
    grant=real_execution.issue(store,'CONTROLLED owner scope, no live authorization','controlled test',
        ControlledRuntime(),task=ref)
    assert grant['snapshot']['contract']['decoding']==real_execution.DECODING
    assert grant['snapshot']['operational_controls']==continuing.controls_for(value)
    assert grant['binding']==digest(grant['snapshot'])
    changed=json.loads(json.dumps(grant['snapshot']))
    changed['operational_controls']['request_seconds']=301
    assert digest(changed)!=grant['binding']


@pytest.mark.parametrize('continued',[False,True])
def test_actual_tool_loop_can_continue_after_twenty_only_under_distinct_permission(tmp_path,continued,monkeypatch):
    # This isolates authority/accounting. Eager context and repetition detection
    # have their own tests; this script intentionally delivers new lines per turn.
    monkeypatch.setattr('workbench.implementation_context.CurrentSource.prefill',lambda *args:None)
    p=tmp_path/PATHS[0]
    p.parent.mkdir(parents=True)
    p.write_text(''.join('local controlled'+str(i)+' = 1\n' for i in range(26)))
    runtime=SimpleNamespace(root=tmp_path,enforce_storage=lambda:None,workspace=tmp_path,read_paths=[PATHS[0]],paths=[PATHS[0]],
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
    runtime=SimpleNamespace(root=tmp_path,enforce_storage=lambda:None,paths=[PATHS[0]],read_paths=[PATHS[0]],
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
    assert 'controlled actual mismatch' in summary['diagnostic']
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


def test_engineering_stop_needs_its_actual_command_proof_and_changed_code(monkeypatch):
    value=approval()
    old={name:'a'*64 for name in continuing.code_identity()}
    marker=dict(actor='engineering-recovery',session_id='session',command_id='stop',code_identity=old,
        owner_provenance=value['payload']['execution_continuation']['owner_provenance'])
    session=dict(id='session',state=dict(cancel_requested=True,stop_command='stop',engineering_yield=marker))
    monkeypatch.setattr(continuing,'code_identity',lambda:old)
    assert not continuing.engineering_stop(session,value)
    monkeypatch.setattr(continuing,'code_identity',lambda:{k:'b'*64 for k in old})
    assert continuing.engineering_stop(session,value)
    session['state']['stop_command']='an-owner-stop-wins'
    assert not continuing.engineering_stop(session,value)
    session['state']['stop_command']='stop'
    del marker['owner_provenance']
    assert not continuing.engineering_stop(session,value)


@pytest.mark.parametrize('focused_pass',[True,False])
def test_unchecked_model_edit_receives_actual_check_feedback_on_a_read_stall(tmp_path,focused_pass):
    path=tmp_path/PATHS[0]
    path.parent.mkdir(parents=True)
    path.write_text('local controlled=1\n')
    runtime=SimpleNamespace(root=tmp_path,enforce_storage=lambda:None,workspace=tmp_path,
        paths=[PATHS[0]],read_paths=[PATHS[0]],workflow_task=dict(approval=approval()),
        recipe=dict(source_context=[{}],read_budget_policy='distinct-lines-v1',
            verification_profile='lua-offline-bugfix-v1',repair_cycle='scoped-repair-v1'),
        tool_commands=lambda:{'focused':['controlled-only']})
    checks=[]
    real=Tools(runtime,lambda:True)
    class ControlledTools:
        def __init__(self):self.runtime=runtime
        def handle(self,name,args):
            if name=='run_check':
                checks.append(args['name'])
                return json.dumps(dict(status='pass' if focused_pass else 'fail',diagnostic='CONTROLLED actual check observation'))
            if name=='replace_text':
                path.write_text(path.read_text().replace(args['old'],args['new']))
                return 'Edit saved in isolated workspace.'
            return real.handle(name,args)
    Transport.responses=[response('replace_text',dict(path=PATHS[0],old='=1',new='=2'),0)]+[
        response('read_file',dict(path=PATHS[0],start=1,end=1),i) for i in range(1,3)]
    if not focused_pass:
        Transport.responses += [answer()]
    Transport.bodies=[]
    result=implement(ControlledTools(),'Controlled work','','controlled',lambda:True,
        transport=Transport,on_finish=lambda:dict(status='pass'))
    assert checks==(['focused','candidate'] if focused_pass else ['focused'])
    assert '=2' in path.read_text()
    if focused_pass:
        assert len(result['log'])==3 and result['completion_reason'].startswith('independent-checks-passed')
    else:
        assert any('CONTROLLED actual check observation' in m.get('content','')
            for m in Transport.bodies[-1]['messages'])
    assert (tmp_path/'attempt/progress-check-02-focused.json').is_file()
