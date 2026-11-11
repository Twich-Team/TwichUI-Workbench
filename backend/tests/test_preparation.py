"""Existing candidate -> derived comparison -> durable fake draft preparation, disposable only."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, select

from test_implementation import sample, real_worker
from workbench import candidate_review as review, implementation as impl, pr_preparation as pr
from workbench.db import metadata, write_transaction, proposal_decisions, engine_for
from workbench.api import create_app

ROOT = Path(__file__).resolve().parents[2]


def ready(client, headers, store, checked=True):
    intake, task, path, _, result, vpath, body = sample(client, headers, store)
    if checked:
        assert client.post(vpath, headers=headers, json=body).status_code==200
        assert real_worker(store).returncode==0
    candidate = result['candidate']['id']
    root = path.removesuffix('implementation')+'candidates/'+candidate
    result = client.get(root+'/review')
    assert result.status_code==200, result.text
    context = result.json()
    return intake, task, root, dict(command_id=str(uuid4()), mode='fixture',
        review_fingerprint=context['review']['fingerprint'], settings=context['settings'])


def get(client, root):
    response = client.get(root+'/review')
    assert response.status_code==200, response.text
    return response.json()


def save(client, headers, root, body):
    response=client.post(root+'/sample-pr',headers=headers,json=body)
    assert response.status_code==200,response.text
    return response.json()


def count(store):
    with store.connect() as c:
        return len(c.execute(select(pr.effects)).all())


def historical(store):
    with store.connect() as c:
        return {t.name:[dict(r) for r in c.execute(select(t)).mappings()] for t in metadata.sorted_tables if t not in {pr.records,pr.effects}}


def test_existing_worker_end_to_end_exact_preview_and_no_historical_mutation(client,headers,store):
    _,_,root,body=ready(client,headers,store)
    baseline=historical(store)
    context=get(client,root)
    assert context['review']['eligible']
    assert context['review']['files'][0]['additions']==4
    assert context['review']['files'][0]['deletions']==4
    assert context['review']['base_identity'] and context['review']['git_base_revision'] is None
    pending=save(client,headers,root,body)
    assert pending['status']=='pending' and pending['started_at'] is None and pending['result'] is None
    assert pending['preview']==context['preview']
    assert pending['settings']['draft'] and pending['preview']['target_repository'] is None
    assert pending['publication_allowed'] is False and pending['execution_allowed'] is False
    assert real_worker(store).returncode==0
    result=get(client,root)['history'][0]
    assert result['status']=='prepared' and result['current_eligible']
    assert result['started_at']<=result['finished_at']
    assert result['result']['url'] is None and not result['result']['real_pr_created']
    assert result['result']['source']=='deterministic-fake-publisher'
    assert not pr.process_one(store) and count(store)==1 and historical(store)==baseline
    assert save(client,headers,root,body)==result


@pytest.mark.parametrize('change',['missing','failed','pending','empty','snapshot','payload','labels','time','malformed-check'])
def test_invalid_verification_is_not_eligibility(client,headers,store,change):
    _,_,root,body=ready(client,headers,store)
    with write_transaction(store) as c:
        row=dict(c.execute(select(impl.verifications)).mappings().one())
        if change=='missing':
            c.execute(impl.verifications.delete())
        else:
            values={'failed':dict(verification='failed'),'pending':dict(status='pending'),
                'empty':dict(checks=[]),'snapshot':dict(snapshot={}),
                'payload':dict(payload={**row['payload'],'binding':'0'*64}),
                'labels':dict(checks=[dict(label='Not the required checks',passed=True)]),
                'time':dict(finished_at=None),'malformed-check':dict(checks=[dict(passed=True)])}[change]
            c.execute(impl.verifications.update().values(**values))
    assert not get(client,root)['review']['eligible']
    assert client.post(root+'/sample-pr',headers=headers,json=body).status_code==409
    assert count(store)==0


@pytest.mark.parametrize('after_save',[False,True])
@pytest.mark.parametrize('change',['approval','task','base','artifact','provenance','check'])
def test_changed_inputs_never_reuse_eligibility(client,headers,store,after_save,change):
    _,_,root,body=ready(client,headers,store)
    if after_save:
        save(client,headers,root,body)
    with write_transaction(store) as c:
        if change=='approval':
            c.execute(proposal_decisions.update().values(scope_review_required=1))
        elif change=='task':
            c.execute(impl.admission.backlog_items.update().values(purpose='Changed task'))
        elif change=='artifact':
            c.execute(impl.candidates.update().values(artifact=b'broken'))
        elif change=='provenance':
            cand=dict(c.execute(select(impl.candidates)).mappings().one())
            c.execute(impl.candidates.update().values(provenance={**cand['provenance'],'artifact_sha256':'0'*64}))
        elif change=='base':
            request=dict(c.execute(select(impl.records)).mappings().one())
            pinned=json.loads(json.dumps(request['snapshot']))
            pinned['fixture']['cases'][0]['input']='Different base'
            c.execute(impl.records.update().values(snapshot=pinned))
        else:
            row=dict(c.execute(select(impl.verifications)).mappings().one())
            c.execute(impl.verifications.update().values(message=row['message']+' Changed evidence.'))
    if after_save:
        assert pr.process_one(store)
        row=get(client,root)['history'][0]
        assert row['status'] in {'blocked','stale'} and row['started_at'] is None
        assert not row['current_eligible']
    else:
        assert client.post(root+'/sample-pr',headers=headers,json=body).status_code==409
    assert count(store)==0


def test_exact_retry_conflict_settings_and_historical_success(client,headers,store):
    _,_,root,body=ready(client,headers,store)
    first=save(client,headers,root,body)
    assert save(client,headers,root,body)==first
    settings={**body['settings'],'title':'Another sample preview','category':'Reliability'}
    conflict={**body,'settings':settings}
    assert client.post(root+'/sample-pr',headers=headers,json=conflict).status_code==409
    assert client.post(root+'/sample-pr',headers=headers,json={**body,'command_id':str(uuid4())}).status_code==409
    assert pr.process_one(store)
    other=save(client,headers,root,{**conflict,'command_id':str(uuid4())})
    assert other['preview']['title']==settings['title'] and '\nCategory: Reliability\n' in other['preview']['body']
    assert pr.process_one(store) and count(store)==2
    before=get(client,root)['history']
    with write_transaction(store) as c:
        c.execute(proposal_decisions.update().values(scope_review_required=1))
    after=get(client,root)['history']
    assert all(r['status']=='prepared' and not r['current_eligible'] for r in after)
    assert [r['result'] for r in before]==[r['result'] for r in after]
    assert save(client,headers,root,body)['result']==before[1]['result']
    assert count(store)==2


def test_lost_response_reconciles_one_logical_effect_after_withdrawal(client,headers,store):
    _,_,root,body=ready(client,headers,store)
    body['settings']['fake_outcome']='lost-response'
    saved=save(client,headers,root,body)
    assert pr.process_one(store)
    row=get(client,root)['history'][0]
    assert row['status']=='reconciling' and row['result'] is None and row['finished_at'] is None
    assert count(store)==1
    with write_transaction(store) as c:
        c.execute(proposal_decisions.update().values(scope_review_required=1))
    assert save(client,headers,root,body)['id']==saved['id']
    assert real_worker(store).returncode==0
    row=get(client,root)['history'][0]
    assert row['status']=='prepared' and not row['current_eligible']
    assert row['result']['reconciled'] and count(store)==1
    assert not pr.process_one(store)


@pytest.mark.parametrize('point,has_effect', [('after-start',False),('before-effect-commit',False),('after-effect',True),('before-receipt-commit',True),('after-commit',True)])
def test_subprocess_crash_boundaries_and_exact_recovery(client,headers,store,point,has_effect):
    _,_,root,body=ready(client,headers,store)
    save(client,headers,root,body)
    run=subprocess.run([sys.executable,'-c',
        'from workbench.pr_preparation import process_one;from workbench.db import engine_for;process_one(engine_for(),crash='+repr(point)+')'],
        env={**os.environ,'PYTHONPATH':str(ROOT/'backend'),'WORKBENCH_DB':str(store.url.database)},timeout=10)
    assert run.returncode in {91,92}
    assert count(store)==int(has_effect)
    row=get(client,root)['history'][0]
    if point=='after-commit':
        assert row['status']=='prepared' and not pr.process_one(store)
    else:
        assert row['status']=='preparing'
        assert pr.process_one(store)
    row=get(client,root)['history'][0]
    assert row['status']=='prepared' and count(store)==1
    assert save(client,headers,root,body)==row


def test_rejection_and_corrupt_remote_evidence(client,headers,store):
    _,_,root,body=ready(client,headers,store)
    body['settings']['fake_outcome']='reject'
    save(client,headers,root,body)
    assert pr.process_one(store)
    assert get(client,root)['history'][0]['status']=='rejected' and count(store)==1
    next_body={**body,'command_id':str(uuid4()),'settings':{**body['settings'],'fake_outcome':'lost-response'}}
    save(client,headers,root,next_body)
    assert pr.process_one(store)
    with write_transaction(store) as c:
        c.execute(pr.effects.update().values(evidence_sha256='0'*64))
    assert pr.process_one(store)
    assert get(client,root)['history'][0]['status']=='failed' and count(store)==2
    assert not pr.process_one(store)


@pytest.mark.parametrize('change',['payload','snapshot','candidate','binding'])
def test_corrupt_saved_command_fails_closed(client,headers,store,change):
    _,_,root,body=ready(client,headers,store)
    save(client,headers,root,body)
    with write_transaction(store) as c:
        row=dict(c.execute(select(pr.records)).mappings().one())
        if change=='candidate':
            # Cross-reference mutation with the original immutable snapshot remains invalid.
            values=dict(snapshot={**row['snapshot'],'candidate_id':'0'*64})
        elif change=='snapshot':
            values=dict(snapshot={**row['snapshot'],'contract':'different-contract'})
        elif change=='binding':
            values=dict(binding='0'*64)
        else:
            values=dict(payload={**row['payload'],'mode':'real'})
        c.execute(pr.records.update().values(**values))
    assert pr.process_one(store)
    assert get(client,root)['history'][0]['status']=='stale' and count(store)==0


def test_concurrent_saves_workers_and_pending_settings_guard(client,headers,store):
    intake,task,root,body=ready(client,headers,store)
    parsed=pr.PreparationRequest.model_validate(body)
    candidate=root.split('/')[-1]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:pr.submit(store,intake,task['id'],candidate,parsed),range(2)))
    assert results[0]==results[1]
    assert client.post(root+'/sample-pr',headers=headers,json={**body,'command_id':str(uuid4()),
        'settings':{**body['settings'],'title':'Changed while pending'}}).status_code==409
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _:pr.process_one(store),range(2)))
    assert count(store)==1 and get(client,root)['history'][0]['status']=='prepared'


@pytest.mark.parametrize('path,before,after,options,kind,adds,deletes',[
    ('safe.txt',b'a\nb\n',b'a\nc\n',{},'modified',1,1),
    ('prefix.txt',b'--old\n',b'++new\n',{},'modified',1,1),
    ('new.txt',None,b'new\n',{},'added',1,0),
    ('gone.txt',b'gone\n',None,{},'deleted',0,1),
    ('new.txt',b'same\n',b'same\n',{'old_path':'old.txt'},'renamed',0,0),
    ('binary.dat',b'\0a',b'\0b',{},'binary',None,None),
    ('invalid.txt',b'\xff',b'a',{},'binary',None,None),
    ('missing.txt',None,b'new',{'base_available':False},'unavailable',None,None),
    ('../private',b'a',b'b',{},'unavailable',None,None),
    ('/etc/passwd',b'a',b'b',{},'unavailable',None,None),
    ('a\\b',b'a',b'b',{},'unavailable',None,None),
    ('new.txt',b'a',b'b',{'old_path':'../../private'},'unavailable',None,None),
    ('huge.txt',b'a'*32769,b'b',{},'unavailable',None,None),
])
def test_bounded_diff_cases(path,before,after,options,kind,adds,deletes):
    value=review.FileComparison.model_validate(review.compare(path,before,after,**options))
    assert value.kind==kind and value.additions==adds and value.deletions==deletes
    if kind in {'binary','unavailable'}:
        assert value.diff is None and value.reason


def test_truncation_and_missing_newline_are_explicit():
    value=review.compare('large.txt',b'a\n'*8000,b'b\n'*8000)
    assert value['truncated'] and len(value['diff'])==review.LIMIT
    assert value['additions']==value['deletions']==8000
    assert 'No newline at end of file' in review.compare('no-newline',b'a',b'b')['diff']


def test_security_boundary_and_no_external_or_provider_hooks(client,headers,store,monkeypatch):
    from workbench import planning_provider, model_policy
    import socket
    _,_,root,body=ready(client,headers,store)
    before=historical(store)
    assert client.post(root+'/sample-pr',json=body).status_code==403
    assert client.post(root+'/sample-pr',headers={**headers,'origin':'http://evil'},json=body).status_code==403
    assert client.get(root+'/review',headers={'host':'evil'}).status_code==400
    for field,value in [('draft',False),('publication_allowed',True),('target_repository','Twich-Team/TwichUI'),('base_branch','main')]:
        assert client.post(root+'/sample-pr',headers=headers,json={**body,'settings':{**body['settings'],field:value}}).status_code==422
    def forbidden(*_,**__):
        pytest.fail('Forbidden external/provider/filesystem hook')
    monkeypatch.setattr(planning_provider.DemoProvider,'generate',forbidden)
    monkeypatch.setattr(planning_provider.LocalProvider,'generate',forbidden)
    monkeypatch.setattr(model_policy,'evaluate',forbidden)
    monkeypatch.setattr(subprocess,'Popen',forbidden)
    monkeypatch.setattr(socket,'create_connection',forbidden)
    monkeypatch.setattr(Path,'write_bytes',forbidden)
    monkeypatch.setattr(Path,'write_text',forbidden)
    writes=[]
    def observe(_c,_cursor,statement,_p,_ctx,_many):
        if statement.lstrip().upper().startswith(('INSERT ','UPDATE ','DELETE ')):
            writes.append(statement)
    event.listen(store,'before_cursor_execute',observe)
    try:
        save(client,headers,root,body)
        assert pr.process_one(store)
        assert get(client,root)['history'][0]['status']=='prepared'
    finally:
        event.remove(store,'before_cursor_execute',observe)
    assert all('pr_preparations' in sql or 'fake_publications' in sql for sql in writes)
    assert historical(store)==before


def test_submission_rollback_and_populated_migration_restore(client,headers,store,monkeypatch,tmp_path):
    _,_,root,body=ready(client,headers,store)
    original=pr.view
    def broken(*_):
        raise RuntimeError('Before acknowledgment')
    monkeypatch.setattr(pr,'view',broken)
    with pytest.raises(RuntimeError):
        client.post(root+'/sample-pr',headers=headers,json=body)
    monkeypatch.setattr(pr,'view',original)
    assert get(client,root)['history']==[]
    save(client,headers,root,body)
    pr.process_one(store)
    expected=get(client,root)
    backup=tmp_path/'restored.sqlite3'
    with sqlite3.connect(store.url.database) as source,sqlite3.connect(backup) as dest:
        source.backup(dest)
    restored=engine_for(backup)
    config=Config(str(ROOT/'alembic.ini'))
    with restored.connect() as c:
        config.attributes['connection']=c
        command.upgrade(config,'head')
        c.commit()
        command.check(config)
        assert c.exec_driver_sql('PRAGMA foreign_key_check').all()==[]
    assert not pr.process_one(restored)
    with TestClient(create_app(restored)) as other:
        assert get(other,root)==expected
    restored.dispose()


def test_populated_0008_upgrade_preserves_every_original_row(tmp_path):
    from workbench.seed import seed
    engine=engine_for(tmp_path/'prior.sqlite3')
    config=Config(str(ROOT/'alembic.ini'))
    with engine.connect() as c:
        config.attributes['connection']=c
        command.upgrade(config,'0008')
        c.commit()
    seed(engine)
    with TestClient(create_app(engine)) as client:
        headers={'origin':'http://testserver','x-csrf-token':client.get('/api/session').json()['csrf_token']}
        identity,task,_,_,result,vpath,body=sample(client,headers,engine)
        client.post(vpath,headers=headers,json=body)
        assert real_worker(engine).returncode==0
    with engine.connect() as c:
        before={name:c.exec_driver_sql('SELECT * FROM "'+name+'" ORDER BY rowid').all()
            for name, in c.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table' AND name!='alembic_version'")}
        config.attributes['connection']=c
        command.upgrade(config,'head')
        c.commit()
        command.upgrade(config,'head')
        c.commit()
        command.check(config)
        assert c.exec_driver_sql('SELECT version_num FROM alembic_version').scalar_one()=='0014'
        assert c.exec_driver_sql('PRAGMA foreign_key_check').all()==[]
        for name,rows in before.items():
            actual=c.exec_driver_sql('SELECT * FROM "'+name+'" ORDER BY rowid').all()
            if name=='model_attempts':
                assert all(row[-1] is None for row in actual)
                actual=[tuple(row[:-1]) for row in actual]
            assert actual==rows
    with TestClient(create_app(engine)) as client:
        headers={'origin':'http://testserver','x-csrf-token':client.get('/api/session').json()['csrf_token']}
        root=f'/api/intake/{identity}/tasks/{task["id"]}/candidates/{result["candidate"]["id"]}'
        data=get(client,root)
        save(client,headers,root,dict(command_id=str(uuid4()),mode='fixture',review_fingerprint=data['review']['fingerprint'],settings=data['settings']))
        assert real_worker(engine).returncode==0
        assert get(client,root)['history'][0]['status']=='prepared'
    engine.dispose()


def test_before_effect_recovery_rechecks_approval(client,headers,store):
    _,_,root,body=ready(client,headers,store)
    save(client,headers,root,body)
    # Genuine worker-process exit after persisted preparation, before simulated effect.
    run=subprocess.run([sys.executable,'-c',
        "from workbench.pr_preparation import process_one;from workbench.db import engine_for;process_one(engine_for(),crash='after-start')"],
        env={**os.environ,'PYTHONPATH':str(ROOT/'backend'),'WORKBENCH_DB':str(store.url.database)},timeout=10)
    assert run.returncode==91
    assert get(client,root)['history'][0]['status']=='preparing' and count(store)==0
    with write_transaction(store) as c:
        c.execute(proposal_decisions.update().values(scope_review_required=1))
    assert real_worker(store).returncode==0
    assert get(client,root)['history'][0]['status']=='blocked' and count(store)==0


@pytest.mark.parametrize('status',['pending','failed','changes_requested'])
def test_nonapplied_approval_blocks_preparation(client,headers,store,status):
    _,_,root,body=ready(client,headers,store)
    with write_transaction(store) as c:
        c.execute(proposal_decisions.update().values(status=status))
    assert not get(client,root)['review']['eligible']
    assert client.post(root+'/sample-pr',headers=headers,json=body).status_code==409
    assert count(store)==0


def test_route_mismatch_and_meaningless_title_are_rejected(client,headers,store):
    _,task,root,body=ready(client,headers,store)
    assert client.post(root.replace(task['id'],'wrong-task')+'/sample-pr',headers=headers,json=body).status_code==404
    assert client.post(root+'/sample-pr',headers=headers,json={**body,'settings':{**body['settings'],'title':'   '}}).status_code==422
    assert client.get(root.rsplit('/',1)[0]+'/not-a-candidate/review').status_code==404
    assert count(store)==0



def test_changed_scope_uses_plain_language_hold(client,headers,store):
    _,_,root,_=ready(client,headers,store)
    with write_transaction(store) as c:
        row=dict(c.execute(select(impl.admission.proposals)).mappings().one())
        c.execute(impl.admission.proposals.update().values(content={**row['content'],'summary':'A changed proposal'}))
    result=get(client,root)['review']
    assert not result['eligible'] and 'binding' not in result['message']
    assert 'supporting evidence' in result['message']


def test_missing_approval_preserves_comparison_but_holds_new_preparation(client,headers,store):
    _,_,root,body=ready(client,headers,store)
    # Fault injection only: simulate missing imported evidence in this disposable DB.
    with sqlite3.connect(store.url.database) as c:
        c.execute('DELETE FROM proposal_decisions')
    data=get(client,root)
    assert not data['review']['eligible'] and data['review']['files'][0]['diff']
    assert client.post(root+'/sample-pr',headers=headers,json=body).status_code==409
    assert count(store)==0
