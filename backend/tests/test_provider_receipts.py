"""Content is irrelevant to independently retained provider terminal state."""
import json
from uuid import uuid4

import pytest
from workbench.provider_receipts import completed_turns


def write(path,value,lines=False):
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    path.write_text('\n'.join(json.dumps(v) for v in value)+'\n' if lines else json.dumps(value))
    path.chmod(0o600)


def receipts(tmp_path):
    identity,receipt=str(uuid4()),str(uuid4())
    values=dict(prompt_tokens=30,completion_tokens=4,total_tokens=34)
    write(tmp_path/'release/artifacts/runs'/('planning-evidence-'+identity)/'events.jsonl',[
        dict(phase='provider-result',provider='codex',generation_state='finished',accounting_state='complete',
             provider_receipt=receipt,usage=dict(values=values))],True)
    terminal=tmp_path/'artifacts/codex-evidence'/(receipt+'.jsonl')
    events=[dict(phase='turn-started',thread_id='thread',turn_id='turn'),
        dict(phase='lifecycle-terminal',thread_id='thread',turn_id='turn',status='completed'),
        dict(phase='turn-finished',status='completed',usage=values),dict(phase='connection-closed')]
    write(terminal,events,True)
    return identity,terminal,events,dict(status='complete',units='tokens',requests_observed=1,values=values)


def test_malformed_or_missing_model_text_cannot_destroy_completed_lifecycle(tmp_path):
    identity,_,_,usage=receipts(tmp_path)
    # No parseable content file exists. The terminal receipt remains usable for
    # accounting/recovery, never for approval or repair execution.
    result=completed_turns(identity,usage,[tmp_path])
    assert result['provider_outcome']=='completed' and result['usage']['total_tokens']==34
    assert result['output_accepted'] is False and result['execution_authorized'] is False


@pytest.mark.parametrize('missing_native_usage',[False,True])
def test_completed_codex_execution_does_not_depend_on_available_ledger_tokens(tmp_path,missing_native_usage):
    identity,path,events,usage=receipts(tmp_path)
    journal=tmp_path/'release/artifacts/runs'/('planning-evidence-'+identity)/'events.jsonl'
    if missing_native_usage:
        rows=[json.loads(line) for line in journal.read_text().splitlines()]
        rows[0].update(accounting_state='unavailable',usage=None)
        events[2]['usage']=None
        write(journal,rows,True)
        write(path,events,True)
    before={p:p.read_bytes() for p in [journal,path]}
    with pytest.raises(ValueError):
        completed_turns(identity,None,[tmp_path])  # Frozen strict callers retain their contract.
    proof=completed_turns(identity,None,[tmp_path],allow_unavailable=True)
    assert proof['provider_outcome']=='completed' and proof['requests']==1
    assert proof['historical_usage_unavailable']
    assert proof['unknown_usage_requests']==int(missing_native_usage)
    assert proof['known_usage']==({} if missing_native_usage else usage['values'])
    assert all(p.read_bytes()==raw for p,raw in before.items())
    events.pop()  # A disconnected transport alone still cannot close execution.
    write(path,events,True)
    with pytest.raises(ValueError):
        completed_turns(identity,None,[tmp_path],allow_unavailable=True)


@pytest.mark.parametrize('missing_usage',[False,True])
@pytest.mark.parametrize('fault',[None,'no_close','no_isolation','changed_code','unsafe','foreign_turn'])
def test_native_completed_malformed_output_settles_before_the_parsed_result_exists(tmp_path,missing_usage,fault):
    from workbench.provider_receipts import request_state,safe_continuation
    from workbench.investigation_recovery import capabilities,ROOT
    identity,path,events,usage=receipts(tmp_path)
    caps=capabilities()
    for name in caps['code']:
        target=tmp_path/'release'/name
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes((ROOT/name).read_bytes())
        target.chmod(0o644)
    if fault=='unsafe':
        caps['source_writes']=True
    events.insert(1,dict(phase='isolation-verified',capabilities=caps))
    if missing_usage:
        events[3]=dict(phase='turn-held',status='terminal',terminal_status='completed',usage=None)
        usage=None
    if fault=='no_close':
        events.pop()
    if fault=='no_isolation':
        events.pop(1)
    if fault=='foreign_turn':
        events[2]['turn_id']='foreign'
    if fault=='changed_code':
        (tmp_path/'release/backend/workbench/codex_provider.py').write_text('changed')
    write(path,events,True)
    journal=tmp_path/'release/artifacts/runs'/('planning-evidence-'+identity)/'events.jsonl'
    write(journal,[dict(phase='request-stopped',provider_receipt=path.stem,
        provider_terminal_confirmed=True,terminal_status='completed')],True)
    before={p:p.read_bytes() for p in [journal,path]}
    def read():
        return request_state(identity,'codex','selected',None,'incomplete_output',usage,
            dict(generation_state='finished'),caps,[tmp_path],
            local_status='failed',local_finished='finished',finished='finished')
    if fault and not missing_usage:
        with pytest.raises(ValueError):
            read()
    else:
        result=read()
        assert safe_continuation(result)==(fault is None)
        if fault is None:
            assert result['provider_state']=='completed' and result['actual_model']=='selected'
            assert result['content_complete'] is None and result['output_accepted'] is False
            assert result['terminal_proof']['unknown_usage_requests']==int(missing_usage)
    assert all(p.read_bytes()==raw for p,raw in before.items())


@pytest.mark.parametrize('fault',['missing_lifecycle','failed','conflicting_terminal','no_close','wrong_usage','wrong_turn','aggregate','symlink'])
def test_unknown_or_conflicting_execution_is_never_completed_by_content(tmp_path,fault):
    identity,path,events,usage=receipts(tmp_path)
    if fault=='missing_lifecycle':
        events=[e for e in events if e['phase']!='lifecycle-terminal']
    if fault=='failed':
        events[1]['status']='failed'
    if fault=='conflicting_terminal':
        events.append(dict(phase='lifecycle-terminal',thread_id='thread',turn_id='turn',status='failed'))
    if fault=='no_close':
        events.pop()
    if fault=='wrong_usage':
        events[2]['usage']={**events[2]['usage'],'total_tokens':35}
    if fault=='wrong_turn':
        events[1]['turn_id']='different'
    if fault=='aggregate':
        usage={**usage,'values':{**usage['values'],'total_tokens':35}}
    write(path,events,True)
    if fault=='symlink':
        target=path.with_name('other.jsonl')
        path.rename(target)
        path.symlink_to(target)
    with pytest.raises(ValueError):
        completed_turns(identity,usage,[tmp_path])


def interrupted_receipts(tmp_path):
    identity,_,_,usage=receipts(tmp_path)
    receipt=str(uuid4())
    values=dict(prompt_tokens=48000,completion_tokens=858,total_tokens=48858)
    caps=dict(kind='tool-free-codex-investigation-v1',source_writes=False,
              execution_dispatch=False,external_action_tools=False,shell_disabled=True,
              empty_work_directory=True,owner_config_mounted=False,sandbox='readOnly',approval_policy='never')
    events=[dict(phase='turn-started',thread_id='thread2',turn_id='turn2'),
            dict(phase='isolation-verified',capabilities=caps),
            dict(phase='lifecycle-terminal',thread_id='thread2',turn_id='turn2',status='interrupted',usage=values),
            dict(phase='interrupt-result',acknowledged=True,status='interrupted'),
            dict(phase='turn-held',terminal_status='interrupted',usage=values),
            dict(phase='connection-closed')]
    path=tmp_path/'artifacts/codex-evidence'/(receipt+'.jsonl')
    write(path,events,True)
    aggregate=dict(usage,requests_observed=2,values={k:usage['values'][k]+v for k,v in values.items()})
    diag=dict(provider_receipt=receipt,provider_terminal_confirmed=True,
              terminal_status='interrupted',investigation_requests=2)
    return identity,path,events,aggregate,diag,caps


@pytest.mark.parametrize('fault',[None,'missing_terminal','unknown','missing_close','wrong_identity',
                                  'missing_ack','wrong_usage','wrong_prefix','write_capability','changed_isolation'])
def test_confirmed_interruption_preserves_usage_and_never_authorizes_partial_output(tmp_path,fault):
    from workbench.provider_receipts import interrupted_turns
    identity,path,events,usage,diag,caps=interrupted_receipts(tmp_path)
    if fault=='missing_terminal':
        events.pop(2)
    if fault=='unknown':
        diag['provider_terminal_confirmed']=False
    if fault=='missing_close':
        events.pop()
    if fault=='wrong_identity':
        events[2]['turn_id']='another'
    if fault=='missing_ack':
        events.pop(3)
    if fault=='wrong_usage':
        usage['values']['total_tokens']+=1
    if fault=='wrong_prefix':
        usage['values']['prompt_tokens']+=1
    if fault=='write_capability':
        caps['source_writes']=True
    if fault=='changed_isolation':
        events[1]['capabilities']={**caps,'shell_disabled':False}
    write(path,events,True)
    if fault:
        with pytest.raises((ValueError,KeyError)):
            interrupted_turns(identity,usage,[tmp_path],diag,caps)
    else:
        proof=interrupted_turns(identity,usage,[tmp_path],diag,caps)
        assert proof['provider_outcome']=='interrupted'
        assert proof['usage']==usage['values']
        assert proof['completed_prefix']['provider_outcome']=='completed'
        assert not proof['output_accepted'] and not proof['execution_authorized']


@pytest.mark.parametrize('fault',[None,'missing_close','unknown_terminal','write_capability',
    'changed_code','accepted_content','prepared_output','candidate','wrong_prefix','ambiguous','wrong_receipt'])
def test_readonly_construction_recovery_preserves_unknown_usage_and_fences_output(tmp_path,fault):
    from datetime import datetime,timedelta,timezone
    from pathlib import Path
    import shutil
    from uuid import UUID,uuid5
    from workbench.investigation_recovery import capabilities
    from workbench.provider_receipts import interrupted_check_construction
    identity,path,events,aggregate,diag,_=interrupted_receipts(tmp_path)
    source=Path(__file__).resolve().parents[2]
    caps=capabilities()
    for name in [*caps['code'],'docs/OWNER_READONLY_RECOVERY_20261108.md']:
        target=tmp_path/'release'/name
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source/name,target)
    events[1]['capabilities']=caps
    events[2]['usage']=events[4]['usage']=None
    now=datetime.now(timezone.utc)
    begin=(now-timedelta(seconds=2)).isoformat()
    finish=(now+timedelta(seconds=2)).isoformat()
    events.insert(0,dict(phase='request',time=now.isoformat()))
    write(path,events,True)
    phase=str(uuid5(UUID(identity),'committed-source-turn-1'))
    stopped=tmp_path/'release/artifacts/runs'/('planning-evidence-'+phase)/'events.jsonl'
    write(stopped,[dict(phase='request-schema',time=begin),dict(phase='request-stopped',time=finish)],True)
    root=tmp_path/'release/artifacts/runs/preparations'/str(uuid4())
    claim=dict(id=identity,capabilities=dict(source_writes=False,execution_dispatch=False,external_action_tools=False))
    result=dict(id=identity,content_accepted=False,candidate_count=0,usage=None,
        known_usage=json.dumps(dict(prompt_tokens=30,completion_tokens=4,total_tokens=34)))
    if fault=='missing_close':
        events.pop()
    if fault=='unknown_terminal':
        events[3]['status']='unknown'
    if fault=='write_capability':
        events[2]['capabilities']={**caps,'source_writes':True}
    if fault=='changed_code':
        (tmp_path/'release/backend/workbench/codex_provider.py').write_text('changed')
    if fault=='accepted_content':
        result['content_accepted']=True
    if fault=='prepared_output':
        write(root/'prepared-bug-checks.json',{})
    if fault=='candidate':
        result['candidate_count']=1
    if fault=='wrong_prefix':
        result['known_usage']=json.dumps(dict(prompt_tokens=31,completion_tokens=4,total_tokens=35))
    if fault=='ambiguous':
        write(path.with_name(str(uuid4())+'.jsonl'),events,True)
    if fault=='wrong_receipt':
        result['diagnostics']=dict(provider_receipt=str(uuid4()))
    write(root/'check-construction-claim.json',claim)
    write(root/'check-construction-result.json',result)
    write(path,events,True)
    if fault:
        with pytest.raises(ValueError):
            interrupted_check_construction(identity,root,[tmp_path])
    else:
        proof=interrupted_check_construction(identity,root,[tmp_path])
        assert proof['final_outcome']=='interrupted' and proof['final_usage'] is None
        assert proof['unknown_usage_requests']==1 and not proof['output_reused']
        assert proof['requires_fresh_claim'] and not proof['execution_authorized']
        assert proof['completed_prefix']['usage']['total_tokens']==34
        import sqlite3
        from workbench.provider_receipts import recorded_check_quarantine
        c=sqlite3.connect(':memory:')
        c.execute('CREATE TABLE model_attempts(id,job_id,backend,purpose,outcome,usage,finished_at,execution_id)')
        c.execute('CREATE TABLE planning_jobs(id,payload)')
        c.execute('INSERT INTO model_attempts VALUES(?,?,?,?,?,?,?,?)',
            (identity,'job','codex','check_preparation','interrupted_unknown',None,finish,None))
        retained=dict(claim=dict(root=str(root)),construction=dict(id=identity),
            output_reused=False,settlement=[dict(attempt=identity,read_only_quarantine=proof)])
        payload=dict(check_preparation_history=[retained],preparation_claim=dict(root=str(root)+'-fresh'))
        c.execute('INSERT INTO planning_jobs VALUES(?,?)',('job',json.dumps(payload)))
        assert recorded_check_quarantine(c,identity,[tmp_path])['requests_started']==2
        # A late/current claim cannot acquire authority through the old receipt.
        payload['preparation_claim']['root']=str(root)
        c.execute('UPDATE planning_jobs SET payload=?',(json.dumps(payload),))
        with pytest.raises(ValueError):
            recorded_check_quarantine(c,identity,[tmp_path])
        assert c.execute('SELECT outcome,usage FROM model_attempts').fetchone()==('interrupted_unknown',None)
