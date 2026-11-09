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


@pytest.mark.parametrize('new_path',['!!!TwichUI/discovered.lua','TwichUI_Settings/pages/discovered.lua'])
@pytest.mark.usefixtures('installed')
def test_followup_discovery_can_propose_new_existing_source_without_registration(client, headers, store, monkeypatch,new_path):
    from test_bug_source_planning import scripted
    from test_verification_profiles import git
    root = enable_bugs(monkeypatch)
    (root/new_path).parent.mkdir(parents=True,exist_ok=True)
    (root/new_path).write_text('local otherwise = 1\nreturn otherwise\n')
    git(root, 'add', '.')
    git(root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@invalid', 'commit', '-qm', 'Discovery fixture')
    config=Path(os.environ['WORKBENCH_PREPARATION_CONFIG'])
    settings=json.loads(config.read_text())
    settings['bug_plan_review']='source-grounded-intent-v1'
    write_policy(config,settings)
    from workbench import bug_investigation
    original_context=bug_investigation.context
    def initial_without_dependency(*args, **kwargs):
        packet=original_context(*args, **kwargs)
        packet['files']=[f for f in packet['files'] if f['path']!=new_path]
        return packet
    monkeypatch.setattr(bug_investigation,'context',initial_without_dependency)
    proposed=scope()
    proposed['read_paths'][0]=new_path
    proposed['writable_paths'][0]=new_path
    calls=scripted(monkeypatch,[
        dict(finding='Inspect a relevant committed dependency before proposing it.',draft=dict(inspection=[dict(path=new_path,start=1,end=2)])),
        dict(finding='A fresh scope for genuine review, no editing permission.',draft=dict(plan=proposed))])
    item=client.post('/api/intake',headers=headers,json=report()).json()
    assert worker.tick(store)
    item=client.get('/api/intake/'+item['id']).json()
    assert new_path not in {f['path'] for f in calls[0]['files']}
    assert len(calls)==2
    discovered=next(f for f in calls[1]['files'] if f['path']==new_path)
    assert discovered['support_only'] is True and discovered['repair_scope_candidate'] is True
    wire_schema=bug_intent.schema(calls[1]['files'])
    assert new_path in wire_schema['$defs']['Plan']['properties']['writable_paths']['items']['enum']
    assert len(item['proposals'])==1 and not item['decisions'] and not item['tasks']
    contract=item['proposals'][0]['task_contract']
    assert new_path in contract['writable_paths'] and not contract['execution_authorized']
    with store.connect() as c:
        assert not c.execute(select(real_execution_grants)).first()


def prepared_scope(client,headers,store,monkeypatch):
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
    return item,command,proposal,calls,endpoint,offer


@pytest.mark.usefixtures('installed')
def test_scope_and_sandbox_offer_allow_repair_before_new_tests_exist_but_do_not_grant_it(
    client,headers,store,monkeypatch,
):
    item,command,proposal,calls,endpoint,offer=prepared_scope(client,headers,store,monkeypatch)
    assert offer['available'],offer
    assert len(calls)==1
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
    # A verified baseline can be reused for the same unchanged source/recipe,
    # while each new run still executes its own confinement boundary probe.
    import shutil
    from workbench.repair_cycle import reuse_baseline
    cached=run.parent/'cache-stage/release/artifacts/runs/preparations/ready'
    cached.mkdir(mode=0o700,parents=True)
    for name in ('recipe.json','recipe-oracle.lua','workspace-location.json','base-manifest.json',
                 'fresh-baseline-suite.json','boundary-proof.json','controls-passed.json'):
        shutil.copy2(run/name,cached/name)
    release=cached.parents[3]
    framework=Path(__file__).resolve().parents[1]/'workbench'
    for name in ('real_workspace.py','real_confine.py','real_sandbox.py','verification_process.py'):
        target=release/'backend/workbench'/name
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(framework/name,target)
    assert reuse_baseline(runtime,str(cached))['status']=='pass'
    (release/'backend/workbench/real_confine.py').write_text('changed checker')
    assert reuse_baseline(runtime,str(cached)) is None
    runtime._request_id,runtime._fence,runtime._context_sha=str(uuid4()),str(uuid4()),None
    (runtime.workspace/PRODUCTION).write_text(CORRECT)
    assert reuse_baseline(runtime,str(cached)) is None
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
            raw=dict(finding='A user fact is needed.',question='Which displayed name should be normalized?')
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


@pytest.mark.usefixtures('installed')
def test_invalid_four_tool_reply_is_rejected_then_fresh_scoped_plan_keeps_unknowns_and_all_usage(
    client,headers,store,monkeypatch,
):
    from test_bug_source_planning import scripted
    enable_bugs(monkeypatch)
    config=Path(os.environ['WORKBENCH_PREPARATION_CONFIG'])
    settings=json.loads(config.read_text())
    settings['bug_plan_review']='source-grounded-intent-v1'
    write_policy(config,settings)
    invalid=dict(finding='Need source, not permission.',action='inspect',
        reads=[dict(path=p,start=1,end=2) for p in (PRODUCTION,TEST)],
        searches=[dict(query='return'),dict(query='local')],plan=None,question=None,limitation=None)
    def valid_read(packet,schema):
        assert schema is bug_intent.Response
        assert packet['validation_feedback']['rejected']
        assert not packet['validation_feedback']['approved']
        return dict(finding='Observed source is available; the exact repair remains a hypothesis.',
            inspection=[dict(path=PRODUCTION,start=1,end=2)])
    def proposed(packet,schema):
        assert schema is bug_intent.Response and 'investigation_diagnosis' not in packet
        assert packet['investigation_working_notes']['notes']
        assert 'investigation_tool_progress' in packet
        candidate=scope()
        candidate['risks']=['The suspected conversion defect must be checked against the original and revised behavior in the isolated workspace.']
        return dict(finding='The inspected function establishes the normalization boundary; conversion is a leading explanation, not a proven runtime diagnosis.',plan=candidate)
    calls=scripted(monkeypatch,[invalid,valid_read,proposed])
    item=client.post('/api/intake',headers=headers,json=report()).json()
    assert worker.tick(store)
    result=client.get('/api/intake/'+item['id']).json()
    assert len(calls)==3 and result['status']=='completed' and len(result['proposals'])==1
    assert not result['decisions'] and not result['tasks']
    contract=result['proposals'][0]['task_contract']
    assert not contract['execution_authorized'] and not contract['publication_authorized']
    assert contract['bug_intent']['plan']['risks']
    with store.connect() as connection:
        attempt=connection.execute(select(model_attempts).where(model_attempts.c.task_id==item['id'])).mappings().one()
        assert attempt['usage']['requests_observed']==3
        assert attempt['usage']['values']['total_tokens']==150
        assert not connection.execute(select(real_execution_grants)).first()


@pytest.mark.usefixtures('installed')
def test_repeated_cached_reads_prompt_scoped_search_instead_of_a_diagnosis_renewal_gate(
    client,headers,store,monkeypatch,
):
    from test_bug_source_planning import scripted
    enable_bugs(monkeypatch)
    config=Path(os.environ['WORKBENCH_PREPARATION_CONFIG'])
    settings=json.loads(config.read_text())
    settings['bug_plan_review']='source-grounded-intent-v1'
    write_policy(config,settings)
    read=dict(finding='The same exact source is being reconsidered; cause is still uncertain.',
        inspection=[dict(path=PRODUCTION,start=1,end=1)])
    def change_strategy(packet,schema):
        progress=packet['investigation_tool_progress']
        assert progress['new_source_characters']==0 and progress['no_new_information_streak']==2
        assert 'Do not repeat them unchanged' in progress['next_strategy']
        return dict(finding='Search the existing tests in a bounded scope rather than reread the same line.',
            inspection=[dict(query='return',path='tests')])
    def proposed(packet,schema):
        assert packet['investigation_tool_progress']['new_searches']==1
        assert any(v['paths']==['tests'] for v in packet['completed_searches']['scopes'].values())
        return dict(finding='The observed source and existing tests support a bounded repair attempt with explicit uncertainty.',plan=scope())
    calls=scripted(monkeypatch,[read,read,change_strategy,proposed])
    item=client.post('/api/intake',headers=headers,json=report()).json()
    assert worker.tick(store)
    result=client.get('/api/intake/'+item['id']).json()
    assert len(calls)==4 and len(result['proposals'])==1 and not result['decisions'] and not result['tasks']


@pytest.mark.usefixtures("installed")
def test_unapproved_plan_can_be_refined_read_only_without_an_owner_approval_or_usage_reset(
    client, headers, store, monkeypatch
):
    from test_bug_source_planning import scripted
    from workbench.db import planning_jobs

    enable_bugs(monkeypatch)
    config = Path(os.environ["WORKBENCH_PREPARATION_CONFIG"])
    settings = json.loads(config.read_text())
    settings["bug_plan_review"] = "source-grounded-intent-v1"
    write_policy(config, settings)

    def corrected(packet, schema):
        assert packet["validation_feedback"]["errors"][0]["path"] == ["plan", "writable_paths"]
        assert "THREE writable files TOTAL" in packet["validation_feedback"]["errors"][0]["rule"]
        return dict(finding="A complete source-grounded scope for review.", plan=scope())

    invalid = scope()
    invalid["writable_paths"] = invalid["writable_paths"][:1]
    calls = scripted(
        monkeypatch,
        [
            dict(finding="Initial unapproved scope.", plan=scope()),
            dict(finding="An incomplete test boundary.", plan=invalid),
            corrected,
        ],
    )
    item = client.post("/api/intake", headers=headers, json=report()).json()
    assert worker.tick(store)
    url = "/api/intake/" + item["id"]
    before = client.get(url).json()
    command = dict(
        command_id=str(uuid4()),
        revision=before["revision"],
        text="Make the scope and test boundaries clear before review.",
        continue_investigation=True,
        renew_operation=True,
    )
    assert client.post(url + "/clarification", headers=headers, json=command).status_code == 200
    assert client.post(url + "/clarification", headers=headers, json=command).status_code == 200
    assert worker.tick(store)
    after = client.get(url).json()
    assert after["status"] == "completed" and len(after["proposals"]) == 2
    assert (
        after["proposals"][1] == before["proposals"][0] and after["original"] == before["original"]
    )
    assert not after["decisions"] and not after["tasks"] and len(calls) == 3
    with store.connect() as c:
        jobs = c.execute(select(planning_jobs).order_by(planning_jobs.c.revision)).mappings().all()
        assert jobs[-1]["payload"]["bug_followup"]["known_prior_usage"]["total_tokens"] == 50
        assert jobs[-1]["payload"]["bug_followup"]["budget_version"] == 5
        assert "retained_source_packet" not in jobs[-1]["payload"]["bug_followup"]
        assert not c.execute(select(real_execution_grants)).first()


@pytest.mark.usefixtures("installed")
def test_same_missing_test_relation_stops_after_two_completed_rejections_despite_wording_changes(
    client, headers, store, monkeypatch
):
    from test_bug_source_planning import scripted

    enable_bugs(monkeypatch)
    config = Path(os.environ["WORKBENCH_PREPARATION_CONFIG"])
    settings = json.loads(config.read_text())
    settings["bug_plan_review"] = "source-grounded-intent-v1"
    write_policy(config, settings)
    invalid = scope()
    invalid["writable_paths"] = invalid["writable_paths"][:1]
    second = copy.deepcopy(invalid)
    second["scope"] = "A differently worded scope that has not fixed its test boundary."
    calls = scripted(
        monkeypatch,
        [
            dict(finding="First description.", plan=invalid),
            dict(finding="Another description.", plan=second),
        ],
    )
    item = client.post("/api/intake", headers=headers, json=report()).json()
    assert worker.tick(store)
    after = client.get("/api/intake/" + item["id"]).json()
    assert (
        after["status"] == "failed"
        and not after["proposals"]
        and not after["decisions"]
        and len(calls) == 2
    )
    with store.connect() as c:
        attempt = (
            c.execute(select(model_attempts).where(model_attempts.c.task_id == item["id"]))
            .mappings()
            .one()
        )
        assert attempt["finished_at"] and attempt["usage"]["requests_observed"] == 2
