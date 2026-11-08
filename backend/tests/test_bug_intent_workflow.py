"""Controlled model + real Lua feedback, through normal API scope and run gates."""
import copy
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from workbench import bug_intent,worker
from workbench.db import model_attempts,real_execution_grants,task_run_offers
from workbench.planning_provider import LocalProvider
from test_bug_workflow import enable_bugs,report,get_run_offer
from test_everyday import installed,write_policy  # noqa: F401
from test_verification_profiles import PRODUCTION,TEST,CORRECT,BUGGY,GOOD_TEST
from test_grounded_workflow import ControlledPreparer


def scope():
    return dict(scope='Correct normalization for the reported operation.',
        read_paths=[PRODUCTION,TEST],writable_paths=[PRODUCTION,TEST],focused_test=TEST,
        criteria=[dict(id='normalization',description='The actual function trims spaces and normalizes uppercase names.')],risks=[])


@pytest.mark.usefixtures('installed')
def test_scope_and_sandbox_offer_allow_repair_before_new_tests_exist_but_do_not_grant_it(
    client,headers,store,monkeypatch,
):
    enable_bugs(monkeypatch)
    config=Path(os.environ['WORKBENCH_PREPARATION_CONFIG'])
    settings=json.loads(config.read_text())
    settings['bug_plan_review']='source-grounded-intent-v1'
    write_policy(config,settings)
    calls=[]
    def generate(self,packet,schema,expected_model):
        calls.append(copy.deepcopy(packet))
        self.diagnostics=dict(actual_model=expected_model,generation_state='finished',accounting_state='complete',finish_reason='stop',
            request_completion_limit=6144,usage=dict(units='tokens',values=dict(prompt_tokens=40,completion_tokens=10,total_tokens=50)))
        if schema is bug_intent.Response:
            raw=dict(finding='Normalization omits the requested conversion.',plan=scope())
        elif schema.__name__=='InvestigationResponse':
            raw=dict(finding='The committed function trims without converting case.',
                draft=dict(diagnosis=dict(cause='The conversion is absent.',evidence_paths=[PRODUCTION])))
        else:
            raise AssertionError('No model-generated checking artifact is required before permission to attempt a repair')
        return raw,expected_model,'local'
    monkeypatch.setattr(LocalProvider,'generate',generate)
    item=client.post('/api/intake',headers=headers,json=report()).json()
    assert worker.tick(store)
    item=client.get('/api/intake/'+item['id']).json()
    proposal=item['proposals'][0]
    assert proposal['task_contract']['bug_intent']['checks_prepared'] is False
    command=dict(command_id=str(uuid4()),proposal_id=proposal['id'],binding=proposal['binding'],
        scope_revision=proposal['scope_revision'],action='approve',note='Controlled independent scope review',
        expectation_review=proposal['task_contract']['bug_intent']['review_digest'])
    saved=client.post('/api/intake/'+item['id']+'/decisions',headers=headers,json=command)
    assert saved.status_code==200,saved.text
    for _ in range(3):
        assert worker.tick(store)
    endpoint,offer=get_run_offer(client,item)
    assert offer['available'],offer
    assert len(calls)==2
    assert 'prepared_behavior' not in offer['offer']
    assert offer['offer']['repair_cycle']['verification']=='not_run'
    assert offer['offer']['repair_cycle']['criteria']==scope()['criteria']
    assert client.get('/api/intake/'+item['id']).json()['proposals'][0]==proposal
    with store.connect() as c:
        assert not c.execute(select(real_execution_grants)).first()
        attempts=c.execute(select(model_attempts).where(model_attempts.c.task_id==item['id'])).mappings().all()
        assert [a['purpose'] for a in attempts]==['planning']
    # A separate current run decision is still required; scope approval alone
    # has created zero implementation authority or model generation.
    rejected=client.post(endpoint,headers=headers,json=dict(command_id=str(uuid4()),
        scope_approval_id=command['command_id'],binding='0'*64,run_budget=offer['offer']['budget'],note='Stale controlled run'))
    assert rejected.status_code==409
    # Controlled component exercise: edits can be attempted before a new test
    # exists, but a green old suite cannot qualify as a checked bug repair.
    # This is actual Lua with simulated confinement, never real-provider proof.
    from workbench import task_session
    from workbench.real_workspace import durable
    with store.connect() as c:
        saved_offer=c.execute(select(task_run_offers).where(task_run_offers.c.id==offer['offer']['id'])).mappings().one()
    prepared=Path(saved_offer['snapshot']['ready_preparation']['root'])
    run=prepared.parent/'controlled-repair-cycle'
    run.mkdir(mode=0o700)
    for name in ('recipe.json','recipe-oracle.lua'):
        durable(run/name,(prepared/name).read_bytes())
    runtime=ControlledPreparer(run)
    task_session.provision(runtime,dict(control_patches={}))
    runtime._request_id,runtime._fence,runtime._context_sha=str(uuid4()),str(uuid4()),None
    (runtime.workspace/PRODUCTION).write_text(CORRECT)
    without_test=runtime.candidate_feedback(lambda:True)
    assert without_test['status']=='fail' and without_test['original_test_differential']['status']=='fail'
    target=runtime.workspace/TEST
    target.write_text(target.read_text()+GOOD_TEST)
    checked=runtime.candidate_feedback(lambda:True)
    assert checked['status']=='pass' and checked['original_test_differential']['status']=='pass'
    result=runtime.freeze_candidate(runtime._request_id,runtime._fence,lambda:True,
        dict(correction_count=1,read_input_errors=0),dict(usage=dict(status='complete',units='tokens',values=dict(prompt_tokens=1,completion_tokens=1,total_tokens=2))))
    assert result['verification']=='passed' and runtime.verify_frozen(result)
    original_check=run/result['independent_check_evidence']/'original-with-new-tests.json'
    original_check.write_text('{}')
    assert not runtime.verify_frozen(result)
    assert (Path(os.environ['WORKBENCH_RESEARCH_ROOT'])/PRODUCTION).read_text()==BUGGY


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
