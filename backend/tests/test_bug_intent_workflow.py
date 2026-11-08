"""Controlled model + real Lua feedback, through normal API scope and run gates."""
import copy
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from workbench import bug_check_preparation,bug_intent,worker
from workbench.db import model_attempts,real_execution_grants
from workbench.planning_provider import LocalProvider
from workbench.visual_scope import digest
from test_bug_workflow import enable_bugs,report,response,get_run_offer
from test_everyday import installed,write_policy  # noqa: F401
from test_verification_profiles import PRODUCTION,TEST
from test_grounded_workflow import ControlledPreparer


def scope():
    return dict(scope='Correct normalization for the reported operation.',
        read_paths=[PRODUCTION,TEST],writable_paths=[PRODUCTION,TEST],focused_test=TEST,
        criteria=[dict(id='normalization',description='The actual function trims spaces and normalizes uppercase names.')],risks=[])


@pytest.mark.usefixtures('installed')
@pytest.mark.parametrize('recover_construction',[False,True])
def test_readable_scope_then_check_construction_with_real_original_feedback_and_separate_run_approval(
    client,headers,store,monkeypatch,recover_construction,
):
    enable_bugs(monkeypatch)
    config=Path(os.environ['WORKBENCH_PREPARATION_CONFIG'])
    settings=json.loads(config.read_text())
    settings['bug_plan_review']='source-grounded-intent-v1'
    write_policy(config,settings)
    # This is explicitly controlled telemetry, not sandbox or real-provider evidence.
    monkeypatch.setattr(bug_check_preparation,'Runtime',ControlledPreparer)
    calls=[]
    fail_construction=recover_construction
    def generate(self,packet,schema,expected_model):
        calls.append(copy.deepcopy(packet))
        self.diagnostics=dict(actual_model=expected_model,generation_state='finished',accounting_state='complete',finish_reason='stop',
            request_completion_limit=6144,usage=dict(units='tokens',values=dict(prompt_tokens=40,completion_tokens=10,total_tokens=50)))
        if packet.get('approved_intent') and fail_construction:
            raw=dict(finding='The controlled fixture requires an updated safe runtime helper.',draft=dict(limitation='A settled controlled runtime-support gap.'))
        elif schema is bug_intent.Response:
            raw=dict(finding='Normalization omits the requested conversion.',plan=scope())
        elif schema.__name__=='InvestigationResponse':
            raw=dict(finding='The committed function trims without converting case.',
                draft=dict(diagnosis=dict(cause='The conversion is absent.',evidence_paths=[PRODUCTION])))
        elif schema.__name__=='ReviewResponse':
            raw=dict(review_binding=packet['review_binding'],ready=True,issues=[])
        else:
            raw=response()
            raw['plan'].update({k:scope()[k] for k in ('scope','read_paths','writable_paths','focused_test')})
            if not any('sandbox_feedback' in p for p in calls):
                # An actual missing function fails Lua rather than reproducing a bug.
                raw['plan']['reproducer'].update(phases=[dict(description='Invoke the inspected function.',
                    code="return subject.Missing('ALICE')")],steps=[dict(op='load',source=PRODUCTION)],
                    observations=[dict(after_step=1,source='return',path=[1],expected='alice',incorrect='ALICE')])
        return raw,expected_model,'local'
    monkeypatch.setattr(LocalProvider,'generate',generate)
    item=client.post('/api/intake',headers=headers,json=report()).json()
    assert worker.tick(store)
    item=client.get('/api/intake/'+item['id']).json()
    assert item['status']=='completed',item['jobs']
    proposal=item['proposals'][0]
    assert proposal['task_contract']['bug_intent']['checks_prepared'] is False
    assert not proposal['task_contract'].get('bug_evidence')
    assert len(calls)==2
    command=dict(command_id=str(uuid4()),proposal_id=proposal['id'],binding=proposal['binding'],
        scope_revision=proposal['scope_revision'],action='approve',note='Controlled scope review',
        expectation_review=proposal['task_contract']['bug_intent']['review_digest'])
    saved=client.post('/api/intake/'+item['id']+'/decisions',headers=headers,json=command)
    assert saved.status_code==200,saved.text
    if recover_construction:
        from workbench.db import planning_jobs
        assert worker.tick(store)  # Applied scope, without a run grant.
        assert worker.tick(store)  # Decompose approved scope.
        assert worker.tick(store)  # Settled construction limitation.
        with store.connect() as c:
            job=c.execute(select(planning_jobs).where(planning_jobs.c.intake_id==item['id'],planning_jobs.c.kind=='behavioral_preparation')).mappings().one()
            assert job['status']=='failed'
        retry=dict(job_id=job['id'],command_id=str(uuid4()),review_binding=proposal['binding'],renew_check_construction=True)
        before=len(calls)
        wrong=dict(retry,review_binding='0'*64)
        assert client.post('/api/intake/'+item['id']+'/retry',headers=headers,json=wrong).status_code==409
        missing=dict(retry)
        missing.pop('command_id')
        assert client.post('/api/intake/'+item['id']+'/retry',headers=headers,json=missing).status_code==409
        with store.begin() as c:
            old_attempt=c.execute(select(model_attempts).where(model_attempts.c.job_id==job['id'])).mappings().one()
            c.execute(model_attempts.update().where(model_attempts.c.id==old_attempt['id']).values(usage=None))
        assert client.post('/api/intake/'+item['id']+'/retry',headers=headers,json=retry).status_code==409
        with store.begin() as c:
            c.execute(model_attempts.update().where(model_attempts.c.id==old_attempt['id']).values(usage=old_attempt['usage']))
        saved=client.post('/api/intake/'+item['id']+'/retry',headers=headers,json=retry)
        assert saved.status_code==200,saved.text
        assert client.post('/api/intake/'+item['id']+'/retry',headers=headers,json=retry).status_code==200
        assert len(calls)==before
        conflicting=dict(retry,command_id=str(uuid4()))
        assert client.post('/api/intake/'+item['id']+'/retry',headers=headers,json=conflicting).status_code==409
        fail_construction=False
        assert worker.tick(store)
        with store.connect() as c:
            renewed=c.execute(select(planning_jobs).where(planning_jobs.c.id==job['id'])).mappings().one()
            assert renewed['attempts']==2 and renewed['status']=='completed',renewed['message']
            assert len(renewed['payload']['check_preparation_history'])==1
            assert renewed['payload']['check_preparation_history'][0]['output_reused'] is False
    else:
        for _ in range(3):
            assert worker.tick(store)
    endpoint,offer=get_run_offer(client,item)
    assert offer['available'],offer
    frozen=offer['offer']['prepared_behavior']
    assert frozen['intent_digest']==proposal['task_contract']['bug_intent']['review_digest']
    assert frozen['reproducer']['observations']
    assert any(p.get('sandbox_feedback',{}).get('outcome')=='hold' for p in calls)
    assert all(p.get('approved_intent') for p in calls[2:])
    assert client.get('/api/intake/'+item['id']).json()['proposals'][0]==proposal
    with store.connect() as c:
        assert not c.execute(select(real_execution_grants)).first()
        attempts=c.execute(select(model_attempts).where(model_attempts.c.task_id==item['id'])).mappings().all()
        assert sorted(a['purpose'] for a in attempts)==['check_preparation']*(2 if recover_construction else 1)+['planning']
        assert all(a['outcome'] in {'completed','failed'} and a['usage']['status']=='complete' for a in attempts)
        assert sum(a['outcome']=='failed' for a in attempts)==int(recover_construction)
    # Tampering a prepared scope cannot be hidden by recomputing its model-owned digest.
    root=Path(settings['output_root'])
    evidence_file=next(root.rglob('prepared-bug-checks.json'))
    prepared=json.loads(evidence_file.read_text())
    assert prepared['intent_digest']==proposal['task_contract']['bug_intent']['review_digest']
    changed=copy.deepcopy(prepared['evidence'])
    changed['plan']['writable_paths']=[PRODUCTION]
    changed['review_digest']=digest(changed)
    with pytest.raises(ValueError):
        bug_check_preparation.validate_evidence(proposal['task_contract']['bug_intent'],changed)


@pytest.mark.usefixtures('installed')
def test_settled_local_rejection_renews_with_fresh_bindings_without_losing_a_phase_or_usage(
    client,headers,store,monkeypatch,
):
    from workbench import intake
    from workbench.db import planning_jobs
    enable_bugs(monkeypatch)
    config=Path(os.environ['WORKBENCH_PREPARATION_CONFIG'])
    settings=json.loads(config.read_text())
    settings['bug_plan_review']='source-grounded-intent-v1'
    write_policy(config,settings)
    calls=[]
    def generate(self,packet,schema,expected_model):
        calls.append(packet)
        self.diagnostics=dict(actual_model=expected_model,generation_state='finished',accounting_state='complete',finish_reason='stop',
            request_completion_limit=6144,usage=dict(units='tokens',values=dict(prompt_tokens=40,completion_tokens=10,total_tokens=50)))
        if len(calls)==1:
            raw=dict(finding='A user fact is needed.',draft=dict(question='Which displayed name should be normalized?'))
        elif schema.__name__=='InvestigationResponse':
            raw=dict(finding='The function omits conversion.',draft=dict(diagnosis=dict(cause='Case conversion is missing.',evidence_paths=[PRODUCTION])))
        else:
            # Completed generation, locally invalid scope. No implementation.
            raw=dict(finding='An invalid draft.',plan=dict(scope='Incomplete'))
        return raw,expected_model,'local'
    monkeypatch.setattr(LocalProvider,'generate',generate)
    item=client.post('/api/intake',headers=headers,json=report()).json()
    assert worker.tick(store)
    url='/api/intake/'+item['id']
    item=client.get(url).json()
    assert item['status']=='waiting_for_information' and item['revision']==1
    command=dict(command_id=str(uuid4()),revision=1,text='The supplied uppercase name should be lowercase.',continue_investigation=True)
    assert client.post(url+'/clarification',headers=headers,json=command).status_code==200
    assert worker.tick(store)
    item=client.get(url).json()
    assert item['status']=='failed' and item['revision']==1 and len(item['proposals'])==1
    count=len(calls)
    command=dict(command_id=str(uuid4()),revision=1,text='Continue with the retained source and corrected readable scope format.',continue_investigation=True,renew_operation=True)
    saved=client.post(url+'/clarification',headers=headers,json=command)
    assert saved.status_code==200,saved.text
    assert client.post(url+'/clarification',headers=headers,json=command).status_code==200
    assert len(calls)==count
    with store.connect() as c:
        jobs=c.execute(select(planning_jobs).where(planning_jobs.c.intake_id==item['id'],planning_jobs.c.kind=='planning').order_by(planning_jobs.c.revision)).mappings().all()
        assert [j['revision'] for j in jobs]==[1,2,3]
        followup=jobs[-1]['payload']['bug_followup']
        assert followup['budget_version']==4 and followup['next_sequence']==3
        assert followup['consumed_requests']==3
        assert followup['known_prior_usage']['total_tokens']==150
        assert len(followup['prior_phases'])==2
        assert followup['prior_phases'][-1]['database_outcome']=='failed'
        assert followup['prior_phases'][-1]['provider_state']=='completed'
    # A concurrent repeated command with a new identity cannot replay queued work.
    command['command_id']=str(uuid4())
    assert client.post(url+'/clarification',headers=headers,json=command).status_code==409
    assert not intake.view(store,item['id'])['decisions']
