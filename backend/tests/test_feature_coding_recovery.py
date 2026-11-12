"""Separately reviewed capacity preserves actual failed outcomes and old budgets."""
import copy
from uuid import uuid4

import pytest
from sqlalchemy import select

from workbench import feature_attempts,feature_execution,feature_run_recovery,feature_coding_recovery,task_budget
from workbench.db import real_execution_grants,real_execution_requests,proposal_decisions,planning_jobs,task_sessions,write_transaction
from workbench.intake import now
from workbench.visual_scope import digest
from test_everyday import installed as installed
from test_feature_attempts import failed_parent
from test_feature_execution import controlled_readiness,controlled_slot


def exhausted(client,headers,store,monkeypatch):
    decision,first,offered,_=failed_parent(client,headers,store,monkeypatch)
    with write_transaction(store) as c:
        original=dict(c.execute(select(real_execution_grants)).mappings().one())
        for i in [2,3]:
            snapshot=copy.deepcopy(original['snapshot'])
            snapshot['runtime']['controlled_reservation']=i
            snapshot['task_budget']['offer']['id']=str(uuid4())
            c.execute(real_execution_grants.insert().values(id=str(uuid4()),snapshot=snapshot,
                binding=digest(snapshot),issued_at=now(),expires_at=original['expires_at'],revoked_at=now()))
        approval=dict(c.execute(select(proposal_decisions).where(proposal_decisions.c.id==decision)).mappings().one())
        identity=approval['intake_id']
        grants=list(c.execute(select(real_execution_grants)).mappings())
        requests=list(c.execute(select(real_execution_requests)).mappings())
    return identity,decision,first,offered,approval,grants,requests


def test_explicit_one_attempt_receipt_admits_exact_new_slot_and_preserves_history(client,headers,store,installed,monkeypatch):
    identity,decision,first,offered,approval,grants,requests=exhausted(client,headers,store,monkeypatch)
    assert not feature_execution.resume_failed_step(store)
    with store.connect() as c:
        selected=feature_run_recovery.offer(c,identity)
        assert selected['kind']=='coding_attempt' and selected['hours']==6
        proof=selected['approved_inputs']['recovery']
        with pytest.raises(ValueError,match='separate reviewed'):
            feature_attempts.validate(c,proof,offered['snapshot']['feature_step']['scope'])
    view=client.get('/api/features/'+identity).json()
    assert view['run_recovery']['kind']=='coding_attempt'
    body=dict(command_id=str(uuid4()),binding=selected['binding'])
    url='/api/features/'+identity+'/continue-unstarted-run'
    response=client.post(url,headers=headers,json=body)
    assert response.status_code==200,response.text
    again=client.post(url,headers=headers,json=body)
    assert again.status_code==200
    assert {k:v for k,v in again.json().items() if k!='observed_at'}=={k:v for k,v in response.json().items() if k!='observed_at'}
    with store.connect() as c:
        from workbench import feature_steps
        old_permission=approval['payload']['feature_run']
        assert feature_run_recovery.deadline(c,approval,old_permission,at=approval['created_at'])==old_permission['expires_at']
        _, current_permission=feature_steps.parent(c,decision)
        assert current_permission==old_permission
        assert dict(c.execute(select(proposal_decisions).where(proposal_decisions.c.id==decision)).mappings().one())==approval
        assert list(c.execute(select(real_execution_grants)).mappings())==grants
        assert list(c.execute(select(real_execution_requests)).mappings())==requests
        feature_attempts.validate(c,proof,offered['snapshot']['feature_step']['scope'])
        receipt=feature_coding_recovery.validate_authority(c,proof,offered['snapshot']['feature_step']['scope'])
        assert receipt['additional_attempts']==1
        assert receipt['previous_window_end']==old_permission['expires_at']
        assert receipt['failed_edits_reused'] is False
    assert feature_execution.advance(store,provision=controlled_readiness)
    assert feature_execution.advance(store)
    with store.connect() as c:
        parent=c.execute(select(task_sessions).where(task_sessions.c.id==decision)).mappings().one()
        slot=parent['state']['feature_slot']['snapshot']['feature_offer_id']
        budget=task_budget.permission(c,approval,task_id=first,offer_id=slot)
        assert budget['offer']['snapshot']['feature_step']['recovery']['reviewed_additional']
        assert budget['budget']['generated_tokens']==24000 and budget['budget']['turns']==20
    from workbench import task_sessions as owner
    assert owner.process_one(store,advance=controlled_slot)
    assert feature_execution.advance(store)
    with store.connect() as c:
        assert len(c.execute(select(real_execution_grants)).all())==4
        assert c.execute(select(real_execution_requests.c.status).where(real_execution_requests.c.id==requests[0]['id'])).scalar_one()=='failed'
        job=c.execute(select(planning_jobs).where(planning_jobs.c.kind=='feature_execution')).mappings().one()
        assert len(job['payload']['completed_steps'])==1
        assert len(job['payload']['coding_continuations'])==1


@pytest.mark.parametrize('fault',['binding','stop','restoration','usage'])
def test_changed_or_unsettled_failed_work_cannot_be_approved(client,headers,store,installed,monkeypatch,fault):
    identity,decision,_,offered,_,grants,requests=exhausted(client,headers,store,monkeypatch)
    with store.connect() as c:
        selected=feature_run_recovery.offer(c,identity)
    if fault=='binding':
        binding='a'*64
    else:
        binding=selected['binding']
        if fault=='stop':
            assert client.post('/api/features/'+identity+'/runs/'+decision+'/stop',headers=headers,json=dict(command_id=str(uuid4()))).status_code==200
        elif fault=='usage':
            from workbench.db import model_attempts
            with write_transaction(store) as c:
                c.execute(model_attempts.update().where(model_attempts.c.id==requests[0]['fence']).values(usage=None))
        else:
            import json
            from pathlib import Path
            path=Path(selected['approved_inputs']['recovery']['controller_report'])
            path.write_text(json.dumps(dict(restoration=dict(restored=False))))
    response=client.post('/api/features/'+identity+'/continue-unstarted-run',headers=headers,
        json=dict(command_id=str(uuid4()),binding=binding))
    assert response.status_code==409,response.text
    with store.connect() as c:
        assert list(c.execute(select(real_execution_grants)).mappings())==grants
        assert list(c.execute(select(real_execution_requests)).mappings())==requests


def test_expired_settled_step_gets_new_window_without_reviving_old_permissions(client,headers,store,installed,monkeypatch):
    from datetime import datetime,timedelta,timezone
    from workbench import feature_permission,feature_steps,task_session
    identity,decision,_,offered,approval,grants,requests=exhausted(client,headers,store,monkeypatch)
    original=approval['payload']['feature_run']
    instant=datetime.fromisoformat(original['expires_at'])+timedelta(minutes=1)
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):
            return instant if tz else instant.replace(tzinfo=None)
    for module in (feature_permission,feature_steps,feature_run_recovery,task_session):
        monkeypatch.setattr(module,'datetime',Clock)
    monkeypatch.setattr('workbench.intake.now',lambda:instant.isoformat())
    with store.connect() as c:
        with pytest.raises(ValueError):
            feature_steps.parent(c,decision)
        offer=feature_coding_recovery.offer(c,identity)
        assert offer
    body=dict(command_id=str(uuid4()),binding=offer['binding'])
    response=client.post('/api/features/'+identity+'/continue-unstarted-run',headers=headers,json=body)
    assert response.status_code==200,response.text
    with store.connect() as c:
        assert feature_steps.parent(c,decision)[1]==original
        assert feature_run_recovery.deadline(c,approval,original,reference='original-parent-run')==original['expires_at']
        assert datetime.fromisoformat(feature_run_recovery.deadline(c,approval,original))==instant+timedelta(hours=6)
        assert list(c.execute(select(real_execution_grants)).mappings())==grants
        assert list(c.execute(select(real_execution_requests)).mappings())==requests
    assert instant>datetime.fromisoformat(grants[0]['expires_at']).astimezone(timezone.utc)
