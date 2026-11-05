"""Deterministic workflow and boundary tests; never invoke a model service."""
import base64
from concurrent.futures import ThreadPoolExecutor
import json
import struct
from uuid import uuid4
import zlib

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select, func

from workbench.api import create_app
from workbench.db import (intakes, planning_jobs, proposal_artifacts, proposal_decisions,
                          backlog_items, tasks, write_transaction, engine_for)
from workbench import intake, planning_worker
from workbench.planning_provider import DemoProvider, LocalProvider, PlanningUnavailable
from workbench.schemas import IntakeInput, ProposalDecisionInput, DecompositionOutput


def submit(client, headers, kind='feature', description='Keep the requested behavior bounded and preserve existing addon behavior.', **overrides):
    body = dict(request_id=str(uuid4()), kind=kind, title='Guide navigation', description=description, lua_error='', visual_requirement='unclassified')
    body.update(overrides)
    response = client.post('/api/intake', headers=headers, json=body)
    assert response.status_code == 200, response.text
    return response.json(), body


def get(client, identity):
    return client.get('/api/intake/'+identity).json()


def planned(client, headers, store):
    data, _ = submit(client, headers)
    planning_worker.process_one(store, DemoProvider())
    return get(client, data['id'])


def research(client, headers, data):
    p = data['proposals'][0]
    response = client.post('/api/intake/'+data['id']+'/research-revision', headers=headers,
                           json={'binding':p['binding'], 'explanation':'Inspect addon feasibility before proposing implementation.'})
    assert response.status_code == 200, response.text
    return response.json()


def decision(data, action='approve', note=''):
    p = data['proposals'][0]
    return dict(command_id=str(uuid4()), proposal_id=p['id'], binding=p['binding'], scope_revision=p['scope_revision'], action=action, note=note)


def decide(client, headers, data, body=None):
    response = client.post('/api/intake/'+data['id']+'/decisions', json=body or decision(data), headers=headers)
    return response


def png():
    def chunk(kind, value):
        return struct.pack('>I',len(value))+kind+value+struct.pack('>I',zlib.crc32(kind+value)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',2,2,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b'\0'+b'\x80\x40\x20'*2+b'\0'+b'\x80\x40\x20'*2))+chunk(b'IEND',b'')


def artifact(data, role='mockup', raw=None, media='image/png'):
    p = data['proposals'][0]
    return dict(artifact_id=str(uuid4()), proposal_id=p['id'], binding=p['binding'], role=role,
                label='Synthetic test artifact; not game evidence', media_type=media,
                data_base64=base64.b64encode(png() if raw is None else raw).decode())


def test_intake_persists_exact_original_and_idempotency_across_restart(client, headers, store):
    original = '  <script>danger()</script>\nplayer-123 /home/private/name secret=private'
    data, body = submit(client, headers, kind='issue', lua_error=original)
    assert data['status']=='queued' and data['original']['lua_error']==original and not data['result']
    assert client.post('/api/intake',json=body,headers=headers).json()==data
    assert client.post('/api/intake',json={**body,'title':'Changed'},headers=headers).status_code==409
    restarted = engine_for(store.url.database)
    with TestClient(create_app(restarted)) as second:
        assert get(second,data['id'])['original']==body
    restarted.dispose()
    assert planning_worker.process_one(store,DemoProvider())
    data=get(client,data['id'])
    assert data['status']=='completed' and len(data['tasks'])==1
    assert 'unverified' in data['tasks'][0]['purpose']
    assert not data['proposals'] and not data['decisions']
    assert original not in json.dumps(client.get('/api/backlog').json())


@pytest.mark.parametrize('change',[{'title':' '},{'description':''},{'title':'x'*161},{'lua_error':'x'*6001}, {'kind':'invalid'},{'request_id':'bad'},{'unexpected':True}])
def test_intake_validation(client,headers,change):
    body=dict(request_id=str(uuid4()),kind='issue',title='Title',description='Description',lua_error='')
    assert client.post('/api/intake',headers=headers,json={**body,**change}).status_code==422
    assert client.get('/api/intake').json()['total']==0


def test_clarification_is_durable_and_keeps_original(client,headers,store):
    data,body=submit(client,headers,kind='issue',description='It breaks.')
    planning_worker.process_one(store,DemoProvider())
    data=get(client,data['id'])
    assert data['status']=='waiting_for_information' and data['result']['clarification'] and not data['tasks']
    reply=dict(command_id=str(uuid4()),revision=data['revision'],text='Open a guide, advance a step, reload; it loses the selected step.')
    url='/api/intake/'+data['id']+'/clarification'
    assert client.post(url,headers=headers,json=reply).status_code==200
    assert client.post(url,headers=headers,json=reply).status_code==200
    planning_worker.process_one(store,DemoProvider())
    data=get(client,data['id'])
    assert data['original']==body and data['status']=='completed' and len(data['tasks'])==1
    assert data['clarifications'][0]['text']==reply['text']


def test_changes_revision_stale_decision_and_applied_scope_safety(client,headers,store):
    with store.connect() as c:
        before=list(c.execute(select(tasks)).mappings())
    data=planned(client,headers,store)
    old=decision(data)
    assert decide(client,headers,data,decision(data,'changes')).status_code==422
    changes=decision(data,'changes','Keep existing keyboard navigation; inspect it first.')
    saved=decide(client,headers,data,changes)
    assert saved.status_code==200 and saved.json()['status']=='pending'
    assert decide(client,headers,data,changes).json()['id']==saved.json()['id']
    assert get(client,data['id'])['status']=='completed'
    assert planning_worker.apply_one(store)
    assert get(client,data['id'])['decisions'][0]['status']=='applied'
    planning_worker.process_one(store,DemoProvider())
    data=get(client,data['id'])
    assert data['revision']==2 and len(data['proposals'])==2
    assert 'keyboard navigation' in json.dumps(data['proposals'][0]['content'])
    assert decide(client,headers,data,old).status_code==409
    data=research(client,headers,data)
    assert data['revision']==3 and data['proposals'][0]['content']['delivery']=='research'
    approval=decision(data)
    assert decide(client,headers,data,approval).json()['status']=='pending'
    assert not data['tasks']
    with pytest.raises(RuntimeError):
        planning_worker.apply_one(store,fail_before_commit=True)
    assert get(client,data['id'])['decisions'][0]['status']=='pending'
    assert planning_worker.apply_one(store)
    assert get(client,data['id'])['status']=='approved'
    assert planning_worker.process_one(store,DemoProvider())
    complete=get(client,data['id'])
    assert complete['status']=='tasks_ready' and len(complete['tasks'])==2
    assert not planning_worker.process_one(store,DemoProvider())
    assert decide(client,headers,data,approval).json()['id']==approval['command_id']
    assert complete['priority']['execution'].startswith('Not authorized')
    with store.connect() as c:
        assert list(c.execute(select(tasks)).mappings())==before
        assert c.scalar(select(func.count()).select_from(backlog_items))==2
    root=[t for t in complete['tasks'] if not t['parent']][0]
    backlog=client.get('/api/backlog',params={'q':'Guide navigation'}).json()
    assert any(r['id']==root['id'] for r in backlog['items'])
    detail=client.get('/api/records/fixture/'+root['id']).json()
    assert detail['record']['intake_id']==data['id'] and detail['children_total']==1


def test_concurrent_decisions_one_saved_and_worker_stale(client,headers,store):
    data=research(client,headers,planned(client,headers,store))
    first=ProposalDecisionInput(**decision(data))
    second=ProposalDecisionInput(**decision(data,'decline'))
    def save(body):
        try:
            return intake.decide(store,data['id'],body)['status']
        except Exception as error:
            return getattr(error,'status_code',type(error).__name__)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(save,[first,second]))
    assert sorted(results,key=str)==sorted(['pending',409],key=str)
    with write_transaction(store) as c:
        c.execute(intakes.update().where(intakes.c.id==data['id']).values(revision=99))
    planning_worker.apply_one(store)
    data=get(client,data['id'])
    assert data['decisions'][0]['status']=='stale' and not data['tasks']


def test_visual_gate_artifact_binding_private_passive_storage(client,headers,store):
    data=planned(client,headers,store)
    old=decision(data)
    assert decide(client,headers,data).status_code==422
    a=artifact(data)
    url='/api/intake/'+data['id']+'/artifacts'
    response=client.post(url,headers=headers,json=a)
    assert response.status_code==200,response.text
    assert client.post(url,headers=headers,json=a).status_code==200
    data=response.json()
    assert decide(client,headers,data,old).status_code==409
    assert len(data['proposals'][0]['approval_blockers'])==1
    b=artifact(data,'game_context')
    response=client.post(url,headers=headers,json=b)
    assert response.status_code==200,response.text
    data=response.json()
    assert not data['proposals'][0]['approval_blockers']
    image=client.get('/api/planning-artifacts/'+a['artifact_id'])
    assert image.content==png() and image.headers['content-type']=='image/png'
    assert image.headers['x-content-type-options']=='nosniff'
    assert 'sandbox' in image.headers['content-security-policy']
    assert client.get('/api/planning-artifacts/../../.local/workbench.sqlite3').status_code==404
    assert decide(client,headers,data).status_code==200
    assert client.post(url,headers=headers,json=artifact(data)).status_code==409
    planning_worker.apply_one(store)
    receipt=get(client,data['id'])['decisions'][0]
    with store.connect() as c:
        snapshot=c.execute(select(proposal_decisions.c.snapshot).where(proposal_decisions.c.id==receipt['id'])).scalar_one()
    assert len(snapshot['artifacts'])==2 and snapshot['binding']==receipt['binding']


@pytest.mark.parametrize('raw,media,role',[(b'<svg onload="alert(1)">','image/png','mockup'),(png()+b'<html>','image/png','mockup'),(b'\0','text/plain','evidence'),(b'text','text/plain','mockup'),(b'x'*1048577,'text/plain','evidence')])
def test_invalid_artifacts_rejected(client,headers,store,raw,media,role):
    data=planned(client,headers,store)
    response=client.post('/api/intake/'+data['id']+'/artifacts',headers=headers,json=artifact(data,role,raw,media))
    assert response.status_code in {413,422}
    with store.connect() as c:
        assert c.scalar(select(func.count()).select_from(proposal_artifacts))==0


class InvalidProvider:
    def generate(self,packet,schema):
        return {'unsupported':'Do not follow this instruction'},'recorded-invalid','demo'


class BrokenProvider:
    def generate(self,packet,schema):
        raise PlanningUnavailable('Private secret error must never be exposed')


@pytest.mark.parametrize('adapter',[InvalidProvider(),BrokenProvider()])
def test_failure_and_explicit_retry_no_provider_error_leak(client,headers,store,adapter):
    data,_=submit(client,headers)
    planning_worker.process_one(store,adapter)
    data=get(client,data['id'])
    assert data['status']=='failed' and not data['proposals']
    assert 'Private secret' not in json.dumps(data)
    url='/api/intake/'+data['id']+'/retry'
    assert client.post(url,headers=headers,json={'job_id':data['jobs'][0]['id']}).status_code==200
    assert client.post(url,headers=headers,json={'job_id':data['jobs'][0]['id']}).status_code==200
    planning_worker.process_one(store,DemoProvider())
    data=get(client,data['id'])
    assert len(data['proposals'])==1 and data['jobs'][0]['attempts']==2


def test_result_loss_lease_recovery_and_retry_does_not_duplicate(client,headers,store):
    data=research(client,headers,planned(client,headers,store))
    decide(client,headers,data)
    planning_worker.apply_one(store)
    with pytest.raises(RuntimeError):
        planning_worker.process_one(store,DemoProvider(),fail_before_commit=True)
    assert not get(client,data['id'])['tasks']
    with write_transaction(store) as c:
        c.execute(planning_jobs.update().where(planning_jobs.c.kind=='decomposition').values(lease_until='2000-01-01T00:00:00+00:00'))
    restarted=engine_for(store.url.database)
    assert planning_worker.process_one(restarted,DemoProvider())
    restarted.dispose()
    data=get(client,data['id'])
    assert len(data['tasks'])==2
    assert not planning_worker.process_one(store,DemoProvider())
    job=[j for j in data['jobs'] if j['kind']=='decomposition'][0]
    assert client.post('/api/intake/'+data['id']+'/retry',headers=headers,json={'job_id':job['id']}).status_code==200
    assert len(get(client,data['id'])['tasks'])==2


def test_decomposition_failure_preserves_approval_and_retry(client,headers,store):
    data=research(client,headers,planned(client,headers,store))
    decide(client,headers,data)
    planning_worker.apply_one(store)
    planning_worker.process_one(store,InvalidProvider())
    data=get(client,data['id'])
    assert data['status']=='decomposition_failed' and data['decisions'][0]['status']=='applied' and not data['tasks']
    job=[j for j in data['jobs'] if j['kind']=='decomposition'][0]
    client.post('/api/intake/'+data['id']+'/retry',headers=headers,json={'job_id':job['id']})
    planning_worker.process_one(store,DemoProvider())
    data=get(client,data['id'])
    assert data['status']=='tasks_ready' and len(data['decisions'])==1


@pytest.mark.parametrize('change',[{'depends_on':['missing']},{'depends_on':['scope-0']},{'scope_indices':[99]},{'parent':'missing'}])
def test_decomposition_rejects_invalid_scope_dependencies(change):
    content={'scope':['One thing'],'acceptance':['Works'],'delivery':'research'}
    raw,_,_=DemoProvider().generate({'proposal':content},DecompositionOutput)
    raw['tasks'][0].update(change)
    with pytest.raises(ValueError):
        planning_worker.validate_decomposition(raw,content)


def test_dependency_cycles_and_parent_links_are_separate():
    content={'scope':['One','Two'],'acceptance':['Works'],'delivery':'research'}
    raw,_,_=DemoProvider().generate({'proposal':content},DecompositionOutput)
    raw['tasks'][0]['depends_on']=['scope-1']
    raw['tasks'][1]['depends_on']=['scope-0']
    with pytest.raises(ValueError):
        planning_worker.validate_decomposition(raw,content)
    raw['tasks'][1]['depends_on']=[]
    assert planning_worker.validate_decomposition(raw,content).tasks[0].parent is None
    raw['tasks'][0].update(kind='milestone',parent='scope-1')
    raw['tasks'][1].update(kind='milestone',parent='scope-0')
    with pytest.raises(ValueError):
        planning_worker.validate_decomposition(raw,content)


def test_unavailable_default_and_manual_priority_persists(client,headers,store,monkeypatch):
    monkeypatch.delenv('WORKBENCH_PLANNER',raising=False)
    data,_=submit(client,headers,kind='issue')
    assert data['priority']['priority']==2 and data['priority']['severity']=='Unconfirmed report'
    url='/api/intake/'+data['id']+'/priority'
    client.put(url,headers=headers,json={'priority':5})
    planning_worker.process_one(store)
    data=get(client,data['id'])
    assert data['status']=='failed' and data['priority']['priority']==5 and data['priority']['manual']
    assert client.get('/api/intake').json()['planner']=='unavailable'
    assert client.put(url,headers=headers,json={'priority':None}).json()['priority']['priority']==2


def test_local_adapter_refuses_unconfigured_credentials_without_network(monkeypatch):
    monkeypatch.delenv('WORKBENCH_LOCAL_KEY',raising=False)
    with pytest.raises(PlanningUnavailable):
        LocalProvider().generate({},DecompositionOutput)
    with pytest.raises(PlanningUnavailable):
        LocalProvider().generate({'oversize':'x'*24001},DecompositionOutput)


def test_saved_decision_requires_same_origin(client,store):
    body=IntakeInput(request_id=uuid4(),kind='issue',title='Test',description='Test')
    assert client.post('/api/intake',json=body.model_dump(mode='json')).status_code==403
    with store.connect() as c:
        assert c.scalar(select(func.count()).select_from(intakes))==0


def test_local_http_adapter_bounds_identity_schema_and_timeout(tmp_path,monkeypatch):
    from contextlib import nullcontext
    from io import BytesIO
    from workbench import planning_provider as module
    key=tmp_path/'local.key'
    key.write_text('synthetic-test-key')
    monkeypatch.setenv('WORKBENCH_LOCAL_KEY',str(key))
    monkeypatch.setattr(module,'local_slot',nullcontext)
    captured=[]
    response={'model':'recorded-local-model','choices':[{'finish_reason':'stop','message':{'content':'{"tasks": []}'}}]}
    class Fake:
        def open(self,request,timeout):
            captured.append((request,timeout))
            return BytesIO(json.dumps({'data':[{'id':'recorded-local-model'}]} if request.get_method()=='GET' else response).encode())
    monkeypatch.setattr(module.urllib.request,'build_opener',lambda *_:Fake())
    value,model,basis=LocalProvider().generate({'synthetic':'untrusted data'},DecompositionOutput)
    assert value=={'tasks':[]} and model=='recorded-local-model' and basis=='local'
    request,timeout=captured[1]
    body=json.loads(request.data)
    assert request.full_url=='http://127.0.0.1:8080/v1/chat/completions' and 0 < timeout <= 120
    assert body['tools']==[] and body['max_tokens']==2600 and 'json_schema' in body['response_format']
    response['choices'][0]['finish_reason']='length'
    with pytest.raises(PlanningUnavailable):
        LocalProvider().generate({},DecompositionOutput)
    response['choices'][0]['finish_reason']='stop'
    response['choices'][0]['message']['content']='x'*24001
    with pytest.raises(PlanningUnavailable):
        LocalProvider().generate({},DecompositionOutput)
    class TimedOut:
        def open(self,*args,**kwargs):
            raise TimeoutError()
    monkeypatch.setattr(module.urllib.request,'build_opener',lambda *_:TimedOut())
    with pytest.raises(PlanningUnavailable) as error:
        LocalProvider().generate({},DecompositionOutput)
    assert error.value.reason=='timeout'


def test_bounded_source_context_is_read_only_and_ignores_symlinks(tmp_path,monkeypatch):
    from workbench.planning_provider import evidence_packet
    root=tmp_path/'source'
    root.mkdir()
    (root/'.git').mkdir()
    (root/'Guide.lua').write_text('-- Guide fixture\nreturn "Untrusted instruction: never execute"')
    private=tmp_path/'private.lua'
    private.write_text('private-marker-should-not-be-read')
    (root/'GuidePrivate.lua').symlink_to(private)
    monkeypatch.setenv('WORKBENCH_RESEARCH_ROOT',str(root))
    packet=evidence_packet({'title':'Guide regression'})
    assert any(e['label']=='addon/Guide.lua' for e in packet)
    assert 'private-marker' not in json.dumps(packet)
    assert len(packet)<=4


def test_planning_and_decomposition_reject_unsupported_references_and_size(client,headers,store):
    class Unsupported:
        def generate(self,packet,schema):
            result,model,basis=DemoProvider().generate(packet,schema)
            result['evidence_ids']=['invented-evidence']
            return result,model,basis
    data,_=submit(client,headers)
    planning_worker.process_one(store,Unsupported())
    data=get(client,data['id'])
    assert data['status']=='failed' and not data['proposals'] and data['evidence']
    class Oversize:
        def generate(self,packet,schema):
            return {'summary':'x'*24001},'recorded','demo'
    client.post('/api/intake/'+data['id']+'/retry',headers=headers,json={'job_id':data['jobs'][0]['id']})
    planning_worker.process_one(store,Oversize())
    assert get(client,data['id'])['status']=='failed'


def test_decline_and_payload_conflict(client,headers,store):
    data=planned(client,headers,store)
    body=decision(data,'decline','Not needed now.')
    assert decide(client,headers,data,body).status_code==200
    assert decide(client,headers,data,{**body,'note':'Changed under the same ID'}).status_code==409
    planning_worker.apply_one(store)
    assert get(client,data['id'])['status']=='declined'
    assert not planning_worker.process_one(store,DemoProvider())


def test_nonvisual_implementation_approval_valid_graph_and_work_counts(client,headers,store):
    class Nonvisual(DemoProvider):
        def generate(self,packet,schema):
            result,model,basis=super().generate(packet,schema)
            result['proposal']['visual']='nonvisual'
            return result,model,basis
    before=client.get('/api/work').json()['needs_you']
    data,_=submit(client,headers,visual_requirement='nonvisual')
    planning_worker.process_one(store,Nonvisual())
    data=get(client,data['id'])
    assert not data['proposals'][0]['approval_blockers']
    assert client.get('/api/work').json()['needs_you']==before+1
    decide(client,headers,data)
    work=client.get('/api/work').json()
    assert work['needs_you']==before and work['waiting_application']==1
    planning_worker.apply_one(store)
    class Graph:
        def generate(self,packet,schema):
            return {'tasks':[
                dict(key='delivery',kind='milestone',parent=None,title='Deliver the agreed behavior',purpose=packet['proposal']['scope'][0],acceptance=packet['proposal']['acceptance'],depends_on=[],scope_indices=[0]),
                dict(key='behavior',kind='task',parent='delivery',title='Implement the agreed behavior',purpose=packet['proposal']['scope'][0],acceptance=[packet['proposal']['acceptance'][0]],depends_on=[],scope_indices=[0]),
                dict(key='verify',kind='task',parent='delivery',title='Verify the behavior and regressions',purpose='Verify the agreed scope',acceptance=[packet['proposal']['acceptance'][1]],depends_on=['behavior'],scope_indices=[0])]},'recorded-graph-fixture','demo'
    planning_worker.process_one(store,Graph())
    data=get(client,data['id'])
    assert data['status']=='tasks_ready' and len(data['tasks'])==4
    task=next(t for t in data['tasks'] if t['id'].endswith('-verify'))
    assert task['dependencies'][0].endswith('-behavior')
    assert task['parent'].endswith('-delivery') and task['parent'] not in task['dependencies']


def test_priority_policy_verified_serious_bugs_age_dependencies_and_override():
    serious,_=intake.priority_recommendation('issue',verified=True,severity='data_loss')
    unconfirmed,_=intake.priority_recommendation('issue')
    ordinary,_=intake.priority_recommendation('feature')
    aged,reason=intake.priority_recommendation('feature',age_days=15,dependencies=2)
    assert serious < unconfirmed < ordinary and aged < ordinary
    assert '2 dependency prerequisite' in reason


def test_local_transport_refuses_redirects():
    from workbench.planning_provider import NoRedirect
    from urllib.request import Request
    assert NoRedirect().redirect_request(Request('http://127.0.0.1:8080/v1/chat/completions'), None,
        302, 'Redirect', {}, 'https://external.invalid/collect') is None


def test_backlog_focus_opens_hierarchy_without_hiding_children(client,headers,store):
    data=research(client,headers,planned(client,headers,store))
    decide(client,headers,data)
    planning_worker.apply_one(store)
    planning_worker.process_one(store,DemoProvider())
    root='intake-'+data['id']
    response=client.get('/api/backlog',params={'focus':root}).json()
    assert response['total']==1 and response['items'][0]['id']==root
    assert response['items'][0]['children']==1 and not response['filtered'] and response['local_records']
    children=client.get('/api/backlog',params={'parent':root}).json()
    assert children['total']==1 and children['items'][0]['parent']==root
