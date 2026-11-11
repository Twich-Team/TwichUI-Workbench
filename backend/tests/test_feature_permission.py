"""Parent command bindings in disposable canonical records; no model execution."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select,func

from workbench import feature_permission,worker,planning_worker,task_sessions,everyday
from workbench.db import (proposal_decisions,planning_jobs,real_execution_grants,
                          real_execution_requests,model_attempts,write_transaction)
from test_feature_planning import submit,ControlledFeaturePlanner
from test_everyday import installed as installed


def ready(client,headers,store, *, planner=None, **changes):
    data,_ = submit(client,headers,ai_settings=dict(
        primary=dict(provider='local',model=everyday.MODEL),fallback=None), **changes)
    planner = planner or ControlledFeaturePlanner()
    assert worker.tick(store,planning_adapter=planner)
    assert worker.tick(store,planning_adapter=planner)
    plan = client.get('/api/intake/'+data['id']).json()['proposals'][0]
    command = dict(command_id=str(uuid4()),proposal_id=plan['id'],binding=plan['binding'],
        scope_revision=plan['scope_revision'],action='approve',note='NON-MODEL parent scope fixture')
    assert client.post('/api/intake/'+data['id']+'/decisions',headers=headers,json=command).status_code == 200
    assert planning_worker.apply_one(store)
    return data['id'],command


def enable():
    path = task_sessions.ROOT/'.local/task-runtime.json'
    value = json.loads(path.read_text())
    value['feature_execution'] = 'sequential-feature-v1'
    path.write_text(json.dumps(value))
    return path


def request(offer):
    return feature_permission.FeatureRunInput(command_id=uuid4(),binding=offer['binding'])


def test_parent_command_is_explicit_idempotent_and_never_forges_step_approvals(
    client,headers,store,installed,
):
    identity,scope = ready(client,headers,store)
    with pytest.raises(ValueError,match='not installed'):
        feature_permission.offer(store,identity)
    enable()
    offered = feature_permission.offer(store,identity)
    assert offered['step_count'] == 2 and offered['scope']['provider'] == 'local'
    assert offered['scope']['route']['model'] == everyday.MODEL
    assert offered['budget']['implementation_fallback'] is False
    body = request(offered)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(feature_permission.request,store,identity,body) for _ in range(2)]
        assert all(f.result()['id'] == str(body.command_id) for f in futures)
    assert feature_permission.request(store,identity,body)['id'] == str(body.command_id)
    with pytest.raises(HTTPException) as error:
        feature_permission.request(store,identity,request(offered))
    assert error.value.status_code == 409
    with store.connect() as c:
        decisions = list(c.execute(select(proposal_decisions).where(proposal_decisions.c.intake_id == identity)).mappings())
        assert len(decisions) == 2
        original = next(d for d in decisions if d['id'] == scope['command_id'])
        parent = next(d for d in decisions if d['id'] == str(body.command_id))
        assert original['status']=='applied' and 'feature_run' not in original['payload']
        assert parent['status']=='pending' and parent['snapshot']==original['snapshot']
        assert parent['payload']['execution_continuation']['scope_approval_id'] == original['id']
        assert parent['payload']['execution_continuation']['owner_provenance']['actor'] == 'owner'
        assert 'run_budget' not in parent['payload']
        assert feature_permission.validate(c,parent,pending=True)['scope'] == offered['scope']
        jobs = list(c.execute(select(planning_jobs).where(planning_jobs.c.intake_id == identity,
            planning_jobs.c.kind=='feature_execution')).mappings())
        assert len(jobs)==1 and jobs[0]['status']=='queued'
        assert jobs[0]['payload']['feature_decision_id']==parent['id']
        assert jobs[0]['payload']['parent_permission_pending'] is True
        assert c.execute(select(func.count()).select_from(model_attempts)).scalar_one()==2
        assert not c.execute(select(real_execution_grants)).first()
        assert not c.execute(select(real_execution_requests)).first()


def test_identical_command_committed_between_initial_read_and_offer_returns_its_receipt(
    client, headers, store, installed, monkeypatch,
):
    identity, _ = ready(client, headers, store)
    enable()
    original = feature_permission.offer
    body = request(original(store, identity))
    receipts = []
    def overlap(engine, identity):
        monkeypatch.setattr(feature_permission, 'offer', original)
        receipts.append(feature_permission.request(engine, identity, body))
        return original(engine, identity)  # The competing-parent guard now rejects.
    monkeypatch.setattr(feature_permission, 'offer', overlap)
    assert feature_permission.request(store, identity, body) == receipts[0]
    with store.connect() as c:
        assert len(c.execute(select(proposal_decisions)).all()) == 2
        assert len(c.execute(select(planning_jobs).where(planning_jobs.c.kind == 'feature_execution')).all()) == 1
        assert not c.execute(select(real_execution_grants)).first()


@pytest.mark.parametrize('fault',['tokens','fallback','expiry','graph','owner','snapshot'])
def test_changed_parent_permission_cannot_be_admitted(client,headers,store,installed,fault):
    identity,_ = ready(client,headers,store)
    enable()
    selected = feature_permission.offer(store,identity)
    body = request(selected)
    feature_permission.request(store,identity,body)
    with store.connect() as c:
        row = dict(c.execute(select(proposal_decisions).where(proposal_decisions.c.id==str(body.command_id))).mappings().one())
    row = copy.deepcopy(row)
    permission = row['payload']['feature_run']
    if fault=='tokens':
        permission['budget']['per_attempt_tokens'] = 999999
    elif fault=='fallback':
        permission['budget']['implementation_fallback'] = True
    elif fault=='expiry':
        permission['expires_at'] = '2000-01-01T00:00:00+00:00'
    elif fault=='graph':
        permission['scope']['graph'][0]['binding']='a'*64
    elif fault=='owner':
        row['payload']['execution_continuation']['owner_provenance']['actor']='model'
    else:
        row['snapshot']['content']['summary']='Changed after approval'
    with store.connect() as c,pytest.raises(ValueError):
        feature_permission.validate(c,row,pending=True)


def test_capability_withdrawal_holds_dispatch_but_keeps_original_command_receipt(
    client,headers,store,installed,
):
    identity,_ = ready(client,headers,store)
    path = enable()
    body = request(feature_permission.offer(store,identity))
    saved = feature_permission.request(store,identity,body)
    value = json.loads(path.read_text())
    value.pop('feature_execution')
    path.write_text(json.dumps(value))
    assert feature_permission.request(store,identity,body)==saved
    with store.connect() as c:
        row=c.execute(select(proposal_decisions).where(proposal_decisions.c.id==str(body.command_id))).mappings().one()
        with pytest.raises(ValueError,match='not installed'):
            feature_permission.validate(c,row,pending=True)


def test_source_change_before_parent_command_does_not_create_a_budget(
    client,headers,store,installed,monkeypatch,
):
    identity,_ = ready(client,headers,store)
    enable()
    body=request(feature_permission.offer(store,identity))
    def moved(*args):
        raise ValueError('source changed')
    monkeypatch.setattr('workbench.project_source.require_current',moved)
    with pytest.raises(HTTPException) as error:
        feature_permission.request(store,identity,body)
    assert error.value.status_code == 409
    with store.connect() as c:
        assert len(c.execute(select(proposal_decisions)).all())==1
        assert not c.execute(select(planning_jobs).where(planning_jobs.c.kind=='feature_execution')).first()


def test_graph_change_during_parent_admission_does_not_approve_a_different_task(
    client,headers,store,installed,monkeypatch,
):
    from workbench.db import backlog_items
    identity,_ = ready(client,headers,store)
    enable()
    selected=feature_permission.offer(store,identity)
    original=feature_permission.offer
    def changed(*args):
        result=original(*args)
        with write_transaction(store) as c:
            c.execute(backlog_items.update().where(backlog_items.c.id==selected['scope']['graph'][0]['task_id']).values(title='Changed after review'))
        return result
    monkeypatch.setattr(feature_permission,'offer',changed)
    with pytest.raises(HTTPException) as error:
        feature_permission.request(store,identity,request(selected))
    assert error.value.status_code==409
    with store.connect() as c:
        assert len(c.execute(select(proposal_decisions)).all())==1


def test_existing_worker_applies_one_parent_without_replanning_or_dispatching_steps(
    client,headers,store,installed,
):
    identity,_ = ready(client,headers,store)
    enable()
    selected = feature_permission.offer(store,identity)
    body = request(selected)
    feature_permission.request(store,identity,body)
    assert worker.tick(store)
    from workbench import task_execution
    with store.connect() as c:
        parent = c.execute(select(proposal_decisions).where(proposal_decisions.c.id==str(body.command_id))).mappings().one()
        assert parent['status']=='applied'
        feature_permission.validate(c,parent)
        for task in selected['scope']['graph']:
            linked = task_execution.snapshot(c,identity,task['task_id'],parent['id'])
            assert linked['approval']['id']==parent['id']
            assert linked['decomposition']['payload']['approval_id']==selected['scope']['scope_approval_id']
        assert len(c.execute(select(proposal_decisions)).all())==2
        assert len(c.execute(select(planning_jobs).where(planning_jobs.c.kind=='decomposition')).all())==1
        assert c.execute(select(func.count()).select_from(model_attempts)).scalar_one()==2
        assert not c.execute(select(real_execution_grants)).first()
        assert not c.execute(select(real_execution_requests)).first()


def test_withdrawal_keeps_parent_pending_and_history_survives_budget_expiry(
    client,headers,store,installed,
):
    identity,_ = ready(client,headers,store)
    path = enable()
    selected = feature_permission.offer(store,identity)
    body = request(selected)
    feature_permission.request(store,identity,body)
    value = json.loads(path.read_text())
    value.pop('feature_execution')
    path.write_text(json.dumps(value))
    assert not worker.tick(store)
    with store.connect() as c:
        row=c.execute(select(proposal_decisions).where(proposal_decisions.c.id==str(body.command_id))).mappings().one()
        assert row['status']=='pending'
    enable()
    assert worker.tick(store)
    with store.connect() as c:
        row=copy.deepcopy(dict(c.execute(select(proposal_decisions).where(proposal_decisions.c.id==str(body.command_id))).mappings().one()))
    # A controlled clock jump holds new execution while preserving the actual
    # applied scope provenance used to display earlier checked child results.
    from datetime import datetime,timedelta
    end=datetime.fromisoformat(row['payload']['feature_run']['expires_at'])+timedelta(seconds=1)
    from unittest.mock import patch
    with patch('workbench.feature_permission.datetime') as clock:
        clock.now.return_value=end
        clock.fromisoformat.side_effect=datetime.fromisoformat
        with store.connect() as c:
            with pytest.raises(ValueError):
                feature_permission.validate(c,row)
            assert feature_permission.validate(c,row,active=False)['scope']==selected['scope']


@pytest.mark.parametrize('fault',['malformed_permission','other_journal_owner','unexpected_active_journal'])
def test_bad_parent_admission_does_not_overwrite_an_unowned_or_active_journal(
    client,headers,store,installed,fault,
):
    identity,_ = ready(client,headers,store)
    enable()
    body = request(feature_permission.offer(store,identity))
    feature_permission.request(store,identity,body)
    with write_transaction(store) as c:
        parent=c.execute(select(proposal_decisions).where(proposal_decisions.c.id==str(body.command_id))).mappings().one()
        job=dict(c.execute(select(planning_jobs).where(planning_jobs.c.kind=='feature_execution')).mappings().one())
        if fault=='malformed_permission':
            payload=copy.deepcopy(parent['payload'])
            payload['feature_run']=False
            c.execute(proposal_decisions.update().where(proposal_decisions.c.id==parent['id']).values(payload=payload))
        elif fault=='other_journal_owner':
            job['payload']={**job['payload'],'feature_decision_id':str(uuid4())}
            c.execute(planning_jobs.update().where(planning_jobs.c.id==job['id']).values(payload=job['payload']))
        else:
            job['status']='running'
            c.execute(planning_jobs.update().where(planning_jobs.c.id==job['id']).values(status='running'))
    assert worker.tick(store)
    with store.connect() as c:
        saved=c.execute(select(proposal_decisions).where(proposal_decisions.c.id==str(body.command_id))).mappings().one()
        assert saved['status']=='failed'
        actual=dict(c.execute(select(planning_jobs).where(planning_jobs.c.id==job['id'])).mappings().one())
        if fault=='malformed_permission':
            assert actual['status']=='failed'
        else:
            assert actual==job
        assert c.execute(select(func.count()).select_from(model_attempts)).scalar_one()==2
        assert not c.execute(select(real_execution_grants)).first()
