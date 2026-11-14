"""Real scoped Lua edit/check path with controlled native IO and one durable pause."""
import json
from uuid import uuid4

import pytest
from sqlalchemy import select

from workbench import ai_strategy as ai,codex_runtime,smart_provider,smart_coding_wait,real_execution
from workbench.db import model_attempts,write_transaction
from test_codex_repair_feedback import prepared,Runtime,BUGGY,CORRECT,PRODUCTION,Canonical


def test_between_turn_restart_keeps_edits_oracle_counts_and_exact_provider_attribution(store,tmp_path,monkeypatch):
    runtime=prepared(tmp_path)
    strategy=ai.Strategy(stages=[ai.Stage(id='first',provider='codex',model='controlled-codex',allocation=50),
        ai.Stage(id='next',provider='claude',model='claude-sonnet-5-5',effort='high',allocation=50)])
    selection=dict(provider='smart',model='controlled-codex',effort=None,policy_revision=1,strategy=strategy.model_dump())
    runtime.codex_selection=selection
    runtime.strategy_engine=store
    identity,fence=str(uuid4()),str(uuid4())
    with write_transaction(store) as c:
        c.execute(model_attempts.insert().values(id=fence,task_id='disposable-coding',source='strategy-control',
            backend='smart',purpose='implementation',requested_model='controlled-codex',requested_effort=None,
            policy_revision=1,started_at=real_execution.now(),routing_reason='Controlled native coding boundary',outcome='running'))
    monkeypatch.setattr(codex_runtime.confine,'root',lambda:'/controlled-test-boundary')
    calls=[]
    paused={'value':False}
    incomplete=BUGGY+'-- Harmless incomplete change.\n'
    def choose(selection,stop,**options):
        assert not stop()
        if not calls:
            stage=strategy.stages[0]
        elif not paused['value']:
            paused['value']=True
            raise smart_provider.Waiting(ai.Decision(state='waiting',provider='codex',stage_id='first',
                reason='Waiting for a temporary usage window.',condition='provider_limit',recheck_at=1).model_dump())
        else:
            stage=strategy.stages[1]
        return ai.Decision(state='selected',provider=stage.provider,stage_id=stage.id,model=stage.model,effort=stage.effort,
            role=stage.role,reason='Controlled actual native stage')
    class Output(dict):
        pass
    def generate(selection,decision,prompt,schema,request_id,stop,**kwargs):
        assert not stop()
        calls.append((decision.provider,decision.model,request_id))
        old,new=(BUGGY,incomplete) if len(calls)==1 else (incomplete,CORRECT)
        result=Output(edits=[dict(path=PRODUCTION,old=old,new=new)])
        result.provider_receipt=str(uuid4())
        return result,dict(prompt_tokens=100,completion_tokens=20,total_tokens=120)
    monkeypatch.setattr(smart_provider,'choose',choose)
    monkeypatch.setattr(smart_provider,'remote_generate',generate)
    oracle=runtime.oracle_source.read_bytes()
    with pytest.raises(smart_provider.Waiting):
        runtime.execute(identity,fence,lambda:True)
    assert len(calls)==1 and (runtime.workspace/PRODUCTION).read_text()==incomplete
    assert runtime.ai_wait_usage['requests_observed']==1 and runtime.ai_wait_usage['values']['total_tokens']==120
    assert not (runtime.root/('codex-usage-'+identity+'.json')).exists()
    # The suspended prefix is immutable, and only a dead old owner may resume it.
    row=dict(id=identity,fence=fence,result=dict(ai_checkpoint=runtime.ai_checkpoint))
    grant=dict(snapshot=dict(runtime=runtime.identity()))
    with pytest.raises(ValueError,match='Frozen work'):
        smart_coding_wait.load(runtime,row,grant)
    monkeypatch.setattr(real_execution,'alive',lambda _:False)
    for path in runtime.root.glob('codex-turn-*.json'):
        path.chmod(0o600)
    restored=Runtime(runtime.root,selection)
    restored.strategy_engine=store
    restored.ai_resume=smart_coding_wait.load(restored,row,grant)
    restored.ai_wait_since=restored.ai_resume['waiting_since']
    result=restored.execute(identity,fence,lambda:True)
    assert [c[:2] for c in calls]==[('codex','controlled-codex'),('claude','claude-sonnet-5-5')]
    assert len({c[2] for c in calls})==2
    assert result['verification']=='passed' and result['usage']['requests_observed']==2
    assert result['usage']['values']['total_tokens']==240
    assert restored.oracle_source.read_bytes()==oracle
    assert (tmp_path/'source'/PRODUCTION).read_text()==BUGGY
    assert Canonical(restored.root).verify_frozen(result)
    with store.connect() as c:
        rows=c.execute(select(model_attempts).where(model_attempts.c.source=='workbench')
            .order_by(model_attempts.c.started_at)).mappings().all()
    assert [(r['backend'],r['actual_model'],r['usage']['values']['total_tokens']) for r in rows]==[
        ('codex','controlled-codex',120),('claude','claude-sonnet-5-5',120)]
    assert all(r['actual_effort'] is None for r in rows)
    changed=json.loads(json.dumps(grant))
    changed['snapshot']['runtime']['model']='unexpected'
    with pytest.raises(ValueError,match='Frozen work'):
        smart_coding_wait.load(restored,row,changed)


@pytest.mark.parametrize('unknown',[False,True])
def test_two_settled_bad_turns_can_change_provider_but_unknown_cannot(store,tmp_path,monkeypatch,unknown):
    runtime=prepared(tmp_path)
    strategy=ai.Strategy(stages=[ai.Stage(id='first',provider='codex',model='controlled-codex',allocation=50),
        ai.Stage(id='next',provider='claude',model='claude-sonnet-5-5',effort='high',allocation=50,
            role='fallback',triggers=['unsuitable'])])
    runtime.codex_selection=dict(provider='smart',model='controlled-codex',effort=None,policy_revision=1,strategy=strategy.model_dump())
    runtime.strategy_engine=store
    identity,fence=str(uuid4()),str(uuid4())
    with write_transaction(store) as c:
        c.execute(model_attempts.insert().values(id=fence,task_id='disposable-coding',source='strategy-control',backend='smart',
            purpose='implementation',requested_model='controlled-codex',policy_revision=1,started_at=real_execution.now(),routing_reason='Controlled boundary',outcome='running'))
    monkeypatch.setattr(codex_runtime.confine,'root',lambda:'/controlled-test-boundary')
    calls=[]
    def choose(selection,stop,**options):
        stage=strategy.stages[1] if options.get('failures',{}).get('first')=='unsuitable' else strategy.stages[0]
        return ai.Decision(state='selected',provider=stage.provider,stage_id=stage.id,model=stage.model,effort=stage.effort,role=stage.role,reason='Authorized controlled boundary')
    class Output(dict):
        pass
    def generate(selection,decision,prompt,schema,request_id,stop,**kwargs):
        calls.append(decision.provider)
        if unknown:
            from workbench.codex_provider import Hold
            raise Hold('timeout',started=True,terminal=False)
        output=Output(edits=[dict(path=PRODUCTION,old=BUGGY,new=CORRECT)] if decision.provider=='claude' else [dict(path=PRODUCTION,old=BUGGY,new=BUGGY)])
        output.provider_receipt=str(uuid4())
        return output,dict(prompt_tokens=100,completion_tokens=20,total_tokens=120)
    monkeypatch.setattr(smart_provider,'choose',choose)
    monkeypatch.setattr(smart_provider,'remote_generate',generate)
    if unknown:
        from workbench.codex_provider import Hold
        with pytest.raises(Hold):
            runtime.execute(identity,fence,lambda:True)
        assert calls==['codex']
        assert runtime.ai_failures=={}
    else:
        result=runtime.execute(identity,fence,lambda:True)
        assert calls==['codex','codex','claude']
        assert result['verification']=='passed' and result['usage']['values']['total_tokens']==360
        assert Canonical(runtime.root).verify_frozen(result)
