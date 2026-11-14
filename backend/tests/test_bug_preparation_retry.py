"""Settled original-only construction uses the existing job and policy journal.

Provider IO and terminal reader are controlled
the live native receipt reader is
also exercised read-only against retained acceptance evidence outside this suite.
"""
import copy
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from workbench import ai_strategy as ai,bug_authority,smart_provider,worker
from workbench.db import model_attempts,planning_jobs,proposal_decisions,task_run_offers,write_transaction
from workbench.bug_preparation_retry import settled_strategy_program
from test_bug_authority import enable
from test_bug_intent_workflow import scope
from test_bug_workflow import enable_bugs,report
from test_everyday import installed,write_policy  # noqa: F401
from test_task_ai_settings import catalog  # noqa: F401
from test_smart_worker import setup
from test_repair_profiles_v2 import ProfileRuntime,controlled_trial_boundary
from test_verification_profiles import git,PRODUCTION,TEST,CORRECT


@pytest.mark.usefixtures('installed','catalog')
@pytest.mark.parametrize('failure',['runtime','citation','semantic'])
def test_settled_feedback_is_journaled_before_real_evidence_closes(client,headers,store,monkeypatch,failure,contained_failure=False):
    from workbench import everyday
    controlled_trial_boundary(monkeypatch)
    source=enable_bugs(monkeypatch)
    for path in (source/'tests').glob('test_*.py'):
        path.write_text('import unittest\nclass Existing(unittest.TestCase):\n def test_environment(self): self.assertEqual(1+1,2)\n')
    (source/PRODUCTION).write_text(CORRECT)
    (source/TEST).write_text('local normalize=dofile("../!!!TwichUI/normalize.lua")\nassert(normalize(" ALICE ")=="ALICE")\n')
    git(source,'add','.')
    git(source,'-c','user.name=Fixture','-c','user.email=fixture@invalid','commit','-qm','Incorrect original assertion')
    config=Path(os.environ['WORKBENCH_PREPARATION_CONFIG'])
    settings=json.loads(config.read_text())
    settings['bug_plan_review']='source-grounded-intent-v1'
    write_policy(config,settings)
    strategy=setup(store)
    enable(store)
    monkeypatch.setattr('workbench.trusted_preparation.Runtime',ProfileRuntime)
    monkeypatch.setattr('workbench.task_sessions.process_one',lambda *a,**k:False)
    proofs=[]
    def settled(*args,**kwargs):
        # Only the native terminal reader is controlled. Actual Evidence files,
        # Smart request accounting and feedback recording run unchanged.
        assert kwargs['local_finished'] and kwargs['finished']
        assert args[-2]==dict(source_writes=False,execution_dispatch=False,external_action_tools=False)
        proofs.append(args[0])
        return dict(provider_state='completed')
    monkeypatch.setattr('workbench.provider_receipts.request_state',settled)
    original_observe=smart_provider.Planner.observe_settled_failure
    if contained_failure:
        def old_close_order(self,reason):
            self.response_evidence.close()  # Reproduce the installed pre-fix fault.
            return original_observe(self,reason)
        monkeypatch.setattr(smart_provider.Planner,'observe_settled_failure',old_close_order)
    designs=[]
    reviews=[]
    def exchange(request,stop,seconds):
        stage=strategy.stages[0]
        decision=ai.Decision(state='selected',stage_id=stage.id,provider=stage.provider,model=stage.model,
            effort=stage.effort,role=stage.role,reason='Controlled verified allowance')
        if request['operation']=='smart-choose':
            return dict(state='choice',decision=decision.model_dump())
        packet=json.loads(request['prompt'].split('Untrusted task data:\n')[1])
        if packet.get('purpose')=='original-only independent behavior checks':
            designs.append(packet)
            output=dict(rationale='Original requirements specify lowercase normalized output.',criteria=['normalization'],
                program='local normalize=dofile("../!!!TwichUI/normalize.lua")\nassert(normalize(" ALICE ")=="alice")',
                evidence=[dict(criterion='normalization',kind='report',path=None,quote='Return trimmed lowercase output for uppercase input.')],
                negative_control=[dict(path=PRODUCTION,old=':lower()',new='')])
            if len(designs)==1:
                if failure=='runtime':
                    output['program']=output['program'].replace('=="alice"','=="ALICE"')
                if failure=='citation':
                    output['evidence'][0]['quote']='An unsupported invented requirement.'
            elif not contained_failure:
                assert packet['prior_design_reviews'][0]['accepted'] is False
        elif packet.get('purpose')=='review original-only independent behavior checks':
            reviews.append(packet)
            output=dict(ready=not(failure=='semantic' and len(reviews)==1),covered_criteria=['normalization'],
                reason='The independent assertions must cover the original normalization requirement.')
        else:
            output=dict(finding='The original implementation follows the requirement and its test disagrees.',action='plan',reads=[],searches=[],checks=None,question=None,limitation=None,
                plan={**scope(),'writable_paths':[TEST],'repair_profile':dict(version=2,kind='test-correction')})
        return dict(state='completed',result=dict(actual_model=stage.model,output=output,provider_receipt=str(uuid4()),
            usage=dict(prompt_tokens=40,completion_tokens=10,total_tokens=50)),admission=dict(stage_id=stage.id,overrun=[]))
    monkeypatch.setattr(smart_provider,'exchange',exchange)
    stage=strategy.stages[0]
    body={**report(), 'description':'Return trimmed lowercase output for uppercase input.', 'local_planning':'smart-strategy',
        'ai_settings':dict(primary=dict(provider=stage.provider,model=stage.model,effort=stage.effort),fallback=None,strategy=strategy.model_dump())}
    response=client.post('/api/intake',headers=headers,json=body)
    assert response.status_code==200,response.text
    identity=response.json()['id']
    for _ in range(18):
        worker.tick(store)
    with store.connect() as c:
        job=c.execute(select(planning_jobs).where(planning_jobs.c.intake_id==identity,planning_jobs.c.kind=='behavioral_preparation')).mappings().one()
    if contained_failure:
        assert job['status']=='failed' and job['payload']['worker_operation_failure']=='TypeError'
        assert not job['payload'].get('preparation_result') and len(designs)==1
        original_payload=copy.deepcopy(job['payload'])
        command=dict(job_id=job['id'],command_id=str(uuid4()),review_binding=job['payload']['preparation_claim']['review_binding'],renew_check_construction=True)
        # A live original owner cannot use this contained-failure recovery.
        denied=client.post('/api/intake/'+identity+'/retry',headers=headers,json=command)
        assert denied.status_code==409
        from workbench import real_execution
        alive=real_execution.alive
        monkeypatch.setattr(real_execution,'alive',lambda *a:False)
        def terminal(identity,usage,roots,**options):
            entry=usage['strategy_requests'][0]
            return dict(actual_model=entry['model'],requests=1,turns=[dict(provider_receipt=entry['native_receipt'])])
        monkeypatch.setattr('workbench.bug_preparation_retry.completed_turns',terminal)
        for fault in ('other-failure','accepted-program','unknown-usage'):
            with write_transaction(store) as c:
                bad=copy.deepcopy(original_payload)
                parent=dict(c.execute(select(model_attempts).where(model_attempts.c.id==bad['independent_program_claim']['id'])).mappings().one())
                if fault=='other-failure':
                    bad['worker_operation_failure']='RuntimeError'
                if fault=='accepted-program':
                    bad['independent_profile_program']=dict(path='already-accepted')
                c.execute(planning_jobs.update().where(planning_jobs.c.id==job['id']).values(payload=bad))
                if fault=='unknown-usage':
                    c.execute(model_attempts.update().where(model_attempts.c.id==parent['id']).values(usage={**parent['usage'],'status':'partial'}))
            assert client.post('/api/intake/'+identity+'/retry',headers=headers,json=command).status_code==409
            with write_transaction(store) as c:
                c.execute(planning_jobs.update().where(planning_jobs.c.id==job['id']).values(payload=original_payload))
                c.execute(model_attempts.update().where(model_attempts.c.id==parent['id']).values(usage=parent['usage']))
        summary=next(Path(original_payload['preparation_claim']['root']).glob('original-program-trial-*/summary.json'))
        raw=summary.read_bytes()
        summary.write_text('{}')
        assert client.post('/api/intake/'+identity+'/retry',headers=headers,json=command).status_code==409
        summary.write_bytes(raw)
        resumed=client.post('/api/intake/'+identity+'/retry',headers=headers,json=command)
        assert resumed.status_code==200,resumed.text
        assert client.post('/api/intake/'+identity+'/retry',headers=headers,json=command).status_code==200
        with store.connect() as c:
            renewed=c.execute(select(planning_jobs).where(planning_jobs.c.id==job['id'])).mappings().one()
            history=renewed['payload']['check_preparation_history']
            assert len(history)==1 and history[0]['worker_operation_failure']=='TypeError' and history[0]['result'] is None
            assert history[0]['read_only_feedback_failure']['owner_stopped']
            assert 'worker_operation_failure' not in renewed['payload'] and 'phase' not in renewed['payload']
            assert dict(c.execute(select(model_attempts).where(model_attempts.c.id==parent['id'])).mappings().one())==parent
        monkeypatch.setattr(real_execution,'alive',alive)
        monkeypatch.setattr(smart_provider.Planner,'observe_settled_failure',original_observe)
        for _ in range(12):
            worker.tick(store)
        with store.connect() as c:
            assert c.execute(select(task_run_offers)).mappings().one()['snapshot']['reviewed_task']['ref']==original_payload['ref']
            assert c.execute(select(planning_jobs.c.status).where(planning_jobs.c.id==job['id'])).scalar_one()=='completed'
        return
    with store.connect() as c:
        offer=c.execute(select(task_run_offers)).mappings().one()
    assert job['status']=='completed' and len(designs)==2 and len(proofs)==1
    root=Path(offer['snapshot']['ready_preparation']['root'])
    record=json.loads((root/'independent-program.json').read_text())
    rejected=record['design_reviews'][0]
    assert not rejected['accepted'] and record['design_reviews'][-1]['accepted']
    evidence=everyday.ROOT/'artifacts/runs'/('planning-evidence-'+rejected['evidence_id'])
    events=[json.loads(line) for line in (evidence/'events.jsonl').read_text().splitlines()]
    observed=[event for event in events if event['phase']=='strategy-settled-failure']
    assert len(observed)==1 and observed[0]['settled'] and observed[0]['reason']=='invalid-response'


@pytest.mark.usefixtures('installed','catalog')
def test_contained_original_feedback_crash_renews_same_job_only_after_owner_and_receipts_settle(client,headers,store,monkeypatch):
    test_settled_feedback_is_journaled_before_real_evidence_closes(client,headers,store,monkeypatch,'runtime',True)


@pytest.mark.usefixtures('installed','catalog')
def test_settled_smart_review_can_renew_same_job_once_with_policy_attribution(client,headers,store,monkeypatch):
    controlled_trial_boundary(monkeypatch)
    source=enable_bugs(monkeypatch)
    (source/PRODUCTION).write_text(CORRECT)
    (source/TEST).write_text('local normalize=dofile("../!!!TwichUI/normalize.lua")\nassert(normalize(" ALICE ")=="ALICE")\n')
    git(source,'add','.')
    git(source,'-c','user.name=Fixture','-c','user.email=fixture@invalid','commit','-qm','Incorrect original assertion')
    config=Path(os.environ['WORKBENCH_PREPARATION_CONFIG'])
    settings=json.loads(config.read_text())
    settings['bug_plan_review']='source-grounded-intent-v1'
    write_policy(config,settings)
    strategy=setup(store)
    enable(store)
    monkeypatch.setattr('workbench.trusted_preparation.Runtime',ProfileRuntime)
    monkeypatch.setattr('workbench.task_sessions.process_one',lambda *a,**k:False)
    state=dict(accept=False,calls=0)
    def exchange(request,stop,seconds):
        stage=strategy.stages[0]
        decision=ai.Decision(state='selected',stage_id=stage.id,provider=stage.provider,model=stage.model,
            effort=stage.effort,role=stage.role,reason='Controlled allowance')
        if request['operation']=='smart-choose':
            return dict(state='choice',decision=decision.model_dump())
        state['calls']+=1
        packet=json.loads(request['prompt'].split('Untrusted task data:\n')[1])
        if packet.get('purpose')=='original-only independent behavior checks':
            output=dict(rationale='Original requirements specify lowercase normalized output.',criteria=['normalization'],
                program='local normalize=dofile("../!!!TwichUI/normalize.lua")\nassert(normalize(" ALICE ")=="alice")',
                evidence=[dict(criterion='normalization',kind='report',path=None,quote='Return trimmed lowercase output for uppercase input.')],
                negative_control=[dict(path=PRODUCTION,old=':lower()',new='')])
        elif packet.get('purpose')=='review original-only independent behavior checks':
            output=dict(ready=state['accept'],covered_criteria=['normalization'],reason='Controlled design review requires a clearer independent check.')
        else:
            output=dict(finding='The original implementation follows the requirement, and the assertion disagrees.',action='plan',reads=[],searches=[],checks=None,question=None,limitation=None,
                plan={**scope(),'writable_paths':[TEST],'repair_profile':dict(version=2,kind='test-correction')})
        return dict(state='completed',result=dict(actual_model=stage.model,output=output,provider_receipt=str(uuid4()),
            usage=dict(prompt_tokens=40,completion_tokens=10,total_tokens=50)),admission=dict(stage_id=stage.id,overrun=[]))
    monkeypatch.setattr(smart_provider,'exchange',exchange)
    body=report()
    stage=strategy.stages[0]
    body.update(description='Return trimmed lowercase output for uppercase input.',local_planning='smart-strategy',
        ai_settings=dict(primary=dict(provider=stage.provider,model=stage.model,effort=stage.effort),fallback=None,strategy=strategy.model_dump()))
    response=client.post('/api/intake',headers=headers,json=body)
    assert response.status_code==200,response.text
    identity=response.json()['id']
    for _ in range(18):
        worker.tick(store)
    with store.connect() as c:
        job=c.execute(select(planning_jobs).where(planning_jobs.c.intake_id==identity,planning_jobs.c.kind=='behavioral_preparation')).mappings().one()
        attempt=c.execute(select(model_attempts).where(model_attempts.c.job_id==job['id'],model_attempts.c.purpose=='check_preparation')).mappings().one()
        frozen=bug_authority.frozen(c,identity)
        children=list(c.execute(select(model_attempts).where(model_attempts.c.job_id==job['id'],model_attempts.c.purpose=='strategy-request')).mappings())
    assert job['status']=='failed' and len(children)==6
    # Current bounded failures remain held. Only the old one-shot protocol upgrades.
    assert not bug_authority.process_one(store)
    payload=copy.deepcopy(job['payload'])
    payload['independent_program_claim'].pop('review_protocol')
    payload['preparation_result']['message']='Independent check review did not establish all original behavior requirements'
    with write_transaction(store) as c:
        c.execute(planning_jobs.update().where(planning_jobs.c.id==job['id']).values(payload=payload))
        # Preserve the legacy missing parent actual model rather than invent one.
        c.execute(model_attempts.update().where(model_attempts.c.id==attempt['id']).values(actual_model=None))
        attempt=dict(c.execute(select(model_attempts).where(model_attempts.c.id==attempt['id'])).mappings().one())
    receipt_calls=[]
    def terminal(identity,usage,roots,**options):
        receipt_calls.append(identity)
        entry=usage['strategy_requests'][0]
        return dict(actual_model=entry['model'],requests=1,turns=[dict(provider_receipt=entry['native_receipt'])])
    monkeypatch.setattr('workbench.bug_preparation_retry.completed_turns',terminal)
    with store.connect() as c:
        proof=settled_strategy_program(c,attempt,frozen['route'],payload,[])
        assert len(proof['requests'])==6
    for fault in ('unknown','aggregate','model','duplicate','native'):
        with write_transaction(store) as c:
            bad=dict(attempt)
            if fault=='unknown':
                bad['outcome']='interrupted_unknown'
            if fault=='aggregate':
                bad['usage']={**attempt['usage'],'values':dict(prompt_tokens=0,completion_tokens=0,total_tokens=0)}
            child=dict(children[0])
            changes={}
            if fault=='model':
                changes['actual_model']='other-model'
            if fault=='duplicate':
                changed=copy.deepcopy(child['usage'])
                changed['strategy_requests'][0]['evidence_id']=children[1]['usage']['strategy_requests'][0]['evidence_id']
                changes['usage']=changed
            if changes:
                c.execute(model_attempts.update().where(model_attempts.c.id==child['id']).values(**changes))
            if fault=='native':
                monkeypatch.setattr('workbench.bug_preparation_retry.completed_turns',lambda *a,**k:dict(actual_model='other',requests=1,turns=[]))
            with pytest.raises((HTTPException,ValueError)):
                settled_strategy_program(c,bad,frozen['route'],payload,[])
            if changes:
                c.execute(model_attempts.update().where(model_attempts.c.id==child['id']).values(**{k:child[k] for k in changes}))
            monkeypatch.setattr('workbench.bug_preparation_retry.completed_turns',terminal)
    # Normal task Pause prevents automatic renewal
    # Resume keeps the same task.
    control=client.get('/api/intake/'+identity+'/work-control').json()
    assert client.put('/api/intake/'+identity+'/work-control',headers=headers,json=dict(revision=control['revision'],paused=True)).status_code==200
    assert not bug_authority.process_one(store)
    assert client.put('/api/intake/'+identity+'/work-control',headers=headers,json=dict(revision=control['revision']+1,paused=False)).status_code==200
    state['accept']=True
    assert bug_authority.process_one(store)
    assert not bug_authority.process_one(store)
    with store.connect() as c:
        renewed=c.execute(select(planning_jobs).where(planning_jobs.c.id==job['id'])).mappings().one()
        history=renewed['payload']['check_preparation_history']
        assert renewed['status']=='queued' and len(history)==1 and len(history[0]['settlement'])==1
        assert history[0]['independent_program_claim']==payload['independent_program_claim']
        operation=c.execute(select(proposal_decisions).where(proposal_decisions.c.payload['policy_operation']['kind'].as_string()=='renew-independent-review')).mappings().one()
        assert operation['status']=='applied' and operation['payload']['policy_authorization']['actor']=='owner-policy'
        original=c.execute(select(proposal_decisions).where(proposal_decisions.c.id==payload['ref']['approval_id'])).mappings().one()
        # The proposal/binding index can return the renewal before the scope.
        # Receipt kind, never row order, determines implementation ancestry.
        assert bug_authority.scope_approval([operation,original])==original
        assert bug_authority.scope_approval([original,operation])==original
        assert bug_authority.scope_approval([operation]) is None
        assert c.execute(select(model_attempts).where(model_attempts.c.id==attempt['id'])).mappings().one()==attempt
    response=client.get('/api/intake/'+identity)
    assert response.status_code==200,response.text
    # Older installed policy renewals omitted redundant payload metadata.
    # Rendering derives it from the bound row without rewriting saved history.
    legacy=copy.deepcopy(operation['payload'])
    legacy.pop('proposal_id')
    legacy.pop('binding')
    with write_transaction(store) as c:
        c.execute(proposal_decisions.update().where(proposal_decisions.c.id==operation['id']).values(payload=legacy))
    response=client.get('/api/intake/'+identity)
    assert response.status_code==200,response.text
    shown=next(d for d in response.json()['decisions'] if d['id']==operation['id'])
    assert shown['payload']['binding']==operation['binding']
    assert shown['payload']['proposal_id']==operation['proposal_id']
    with store.connect() as c:
        assert c.execute(select(proposal_decisions.c.payload).where(proposal_decisions.c.id==operation['id'])).scalar_one()==legacy
    for _ in range(12):
        worker.tick(store)
    with store.connect() as c:
        offer=c.execute(select(task_run_offers)).mappings().one()
        assert offer['snapshot']['reviewed_task']['ref']==payload['ref']
        assert c.execute(select(planning_jobs).where(planning_jobs.c.id==job['id'])).mappings().one()['status']=='completed'
