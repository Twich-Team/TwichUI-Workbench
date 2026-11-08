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
