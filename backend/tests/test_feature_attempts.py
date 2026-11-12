"""Actual failed-owner exit and Lua evidence with controlled coding/confinement.

No live inference, model service, external publication or sandbox-readiness claim.
The subprocess really exits; its checked/failure artifacts and DB are canonical.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy import select

from workbench import feature_execution, feature_attempts, task_sessions, task_budget
from workbench.db import (planning_jobs, task_run_offers, task_sessions as sessions,
    real_execution_requests, proposal_decisions, write_transaction)
from workbench.real_workspace import Runtime
from test_everyday import installed as installed
from test_feature_execution import (missing_names_parent, controlled_check, controlled_readiness, controlled_slot)
from test_feature_preparation import ScopePlanner

ROOT = Path(__file__).resolve().parents[2]
CHILD = r'''
import json,sys
from pathlib import Path
from sqlalchemy import select
from workbench import task_session,task_sessions,everyday,task_budget,task_execution,real_execution
from workbench.db import engine_for,proposal_decisions,real_execution_requests
from workbench.real_workspace import Runtime,durable
from test_feature_execution import controlled_check,controlled_readiness,PRODUCTION,TEST
Runtime.check=controlled_check
config_path=Path(sys.argv[1]);config=json.loads(config_path.read_text());ref=config['task']
for module in (task_sessions,task_session,everyday):module.ROOT=config_path.parents[3]
engine=engine_for(sys.argv[2])
with engine.connect() as c:
 approval=c.execute(select(proposal_decisions).where(proposal_decisions.c.id==ref['approval_id'])).mappings().one()
 budget=task_budget.permission(c,approval,task_id=ref['task_id'],offer_id=config['feature_offer_id'])
 linked=task_execution.snapshot(c,**ref)
 template=task_session.preparation_template(c,linked,budget,engine)
root=Path(config['run_root'])/'real-run';root.mkdir()
durable(root/'recipe.json',json.dumps(template['recipe']).encode())
durable(root/'recipe-oracle.lua',template['oracle'].encode())
class Failed(Runtime):
 def preflight(self):return self.static_preflight()
 def execute(self,identity,fence,permitted):
  self._request_id,self._fence,self._context_sha=identity,fence,self.context_sha()
  durable(self.root/('ownership-'+identity+'.json'),json.dumps(dict(owner=real_execution.owner_identity(),
   cgroup='/sys/fs/cgroup/workbench-real-CONTROLLED-'+identity)).encode())
  p=self.workspace/PRODUCTION
  p.write_text(p.read_text().replace('function(s) ','function(s) if s == nil then return false end; '))
  p=self.workspace/TEST;p.write_text(p.read_text()+'local ok, observed = pcall(normalize, nil)\nassert(ok and observed == nil)\n')
  assert self.candidate_feedback(permitted)['status']=='fail'
  raise ValueError('CONTROLLED actual native assertion did not pass')
 def failure_evidence(self,*args):
  return dict(usage=dict(status='complete',source='controlled_fixture',units='tokens',
   values=dict(prompt_tokens=10,completion_tokens=10,total_tokens=20)))
runtime=Failed(root);controlled_readiness(runtime,template)
runtime.task_ref=ref;runtime.feature_offer_id=config['feature_offer_id']
assert task_budget.authorize_one(engine,runtime)
assert real_execution.process_one(engine,runtime=runtime)
with engine.connect() as c:
 result=c.execute(select(real_execution_requests)).mappings().one()
 assert result['status']=='failed',result['result']
durable(root.parent/'lease-controller-result.json',json.dumps(dict(
 restoration=dict(restored=True),evidence_role='controlled_fixture_no_service_activation')).encode())
task_session.finish(engine,config,'failed','Controlled native assertion failed; owner will now really exit.')
engine.dispose()
'''


def failed_parent(client, headers, store, monkeypatch):
    monkeypatch.setattr(Runtime, 'check', controlled_check)
    _, decision, first, _ = missing_names_parent(client, headers, store)
    assert feature_execution.advance(store, adapter=ScopePlanner())
    assert feature_execution.advance(store, provision=controlled_readiness)
    assert feature_execution.advance(store)
    def failed_slot(engine, config_path, stop_event=None):
        env = dict(os.environ)
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        env['PYTHONPATH'] = os.pathsep.join(str(ROOT/p) for p in ('backend','backend/tests','scripts'))
        run = subprocess.run([sys.executable,'-c',CHILD,str(config_path),str(engine.url.database)],
            env=env,capture_output=True,text=True,timeout=40)
        assert run.returncode == 0, run.stdout+run.stderr
    assert task_sessions.process_one(store, advance=failed_slot)
    assert feature_execution.advance(store)  # Holds the failed current slot; no replay.
    with store.connect() as c:
        parent = c.execute(select(sessions).where(sessions.c.id==decision)).mappings().one()
        assert parent['status']=='failed'
        job = c.execute(select(planning_jobs).where(planning_jobs.c.kind=='feature_execution')).mappings().one()
        offered = c.execute(select(task_run_offers).where(task_run_offers.c.id==job['payload']['current_offer_id'])).mappings().one()
        proof = feature_attempts.settled(c,dict(offered))
        assert proof['failed_changes_reused'] is False and proof['next_attempt']==2
    return decision, first, dict(offered), proof


def test_fresh_attempt_keeps_real_parent_and_failed_usage_but_uses_original_checked_input(
    client, headers, store, installed, monkeypatch,
):
    decision, first, offered, proof = failed_parent(client,headers,store,monkeypatch)
    assert feature_execution.advance(store)  # Verified fresh slot; no new human approval.
    assert feature_execution.advance(store,provision=controlled_readiness)
    assert feature_execution.advance(store)
    with store.connect() as c:
        parent=c.execute(select(sessions)).mappings().one()
        assert parent['id']==decision and len(parent['state']['feature_history'])==1
        assert parent['state']['feature_slot']['snapshot']['feature_offer_id']!=offered['id']
        policy=task_budget.permission(c,c.execute(select(proposal_decisions).where(proposal_decisions.c.id==decision)).mappings().one(),task_id=first,
            offer_id=parent['state']['feature_slot']['snapshot']['feature_offer_id'])
        assert policy['feature_attempt']==2
        assert policy['offer']['snapshot']['feature_step']['recovery']==proof
        assert policy['offer']['snapshot']['specification']['approved_feature_scope']['feature_input'] is None
        assert len(c.execute(select(proposal_decisions)).all())==2
    assert task_sessions.process_one(store,advance=controlled_slot)
    assert feature_execution.advance(store)
    with store.connect() as c:
        rows=list(c.execute(select(real_execution_requests).order_by(real_execution_requests.c.created_at)).mappings())
        assert [r['status'] for r in rows]==['failed','checked']
        assert rows[0]['result']['usage']['values']['completion_tokens']==10
        job=c.execute(select(planning_jobs).where(planning_jobs.c.kind=='feature_execution')).mappings().one()
        assert len(job['payload']['completed_steps'])==1
        assert job['payload']['completed_steps'][0]['task_id']==first
        assert len(job['payload']['recovery_history'])==1


@pytest.mark.parametrize('fault',['restore','owner','usage','revocation','receipt','owner_stop','capability'])
def test_uncertain_or_changed_prior_execution_cannot_open_a_fresh_slot(
    client,headers,store,installed,monkeypatch,fault,
):
    decision, _, offered, proof=failed_parent(client,headers,store,monkeypatch)
    if fault=='restore':
        Path(proof['controller_report']).write_text(json.dumps(dict(restoration=dict(restored=False))))
    elif fault=='owner':
        monkeypatch.setattr(feature_attempts.execution,'alive',lambda _:True)
    elif fault=='receipt':
        path=Path(proof['recovery_root'])/('outcome-'+proof['execution_id']+'.json')
        value=json.loads(path.read_text())
        value['result_digest']='a'*64
        path.write_text(json.dumps(value))
    elif fault=='owner_stop':
        from uuid import uuid4
        identity = offered['snapshot']['feature_step']['scope']['decision_id']
        with store.connect() as c:
            intake_id = c.execute(select(proposal_decisions.c.intake_id).where(proposal_decisions.c.id==identity)).scalar_one()
        response = client.post('/api/features/'+intake_id+'/runs/'+identity+'/stop',
            headers=headers,json=dict(command_id=str(uuid4())))
        assert response.status_code == 200, response.text
        view = client.get('/api/features/'+intake_id).json()
        assert view['run']['cancel_requested'] and not view['run']['can_stop']
    elif fault=='capability':
        path = task_sessions.ROOT/'.local/task-runtime.json'
        value = json.loads(path.read_text())
        value.pop('feature_execution')
        path.write_text(json.dumps(value))
    else:
        from workbench.db import real_execution_grants,model_attempts
        with write_transaction(store) as c:
            if fault=='revocation':
                c.execute(real_execution_grants.update().values(revoked_at=None))
            else:
                c.execute(model_attempts.update().where(model_attempts.c.id==proof['fence']).values(usage=None))
    assert not feature_execution.resume_failed_step(store)
    with store.connect() as c:
        parent=c.execute(select(sessions).where(sessions.c.id==decision)).mappings().one()
        assert parent['status']==('cancelled' if fault=='owner_stop' else 'failed')
        assert len(c.execute(select(task_run_offers)).all())==1
        assert len(c.execute(select(real_execution_requests)).all())==1



def test_issued_grants_enforce_original_budget_while_ungranted_preparations_do_not_count(
    client,headers,store,installed,monkeypatch,
):
    import copy
    from uuid import uuid4
    from workbench.db import real_execution_grants
    from workbench.intake import now
    from workbench.visual_scope import digest
    decision,first,offered,proof=failed_parent(client,headers,store,monkeypatch)
    assert proof['coding_budget']['policy']=='issued-coding-grants-v1'
    assert len(proof['coding_budget']['consumed'])==1 and proof['coding_budget']['next_index']==2
    assert feature_execution.advance(store)
    assert feature_execution.advance(store,provision=controlled_readiness)
    assert feature_execution.advance(store)
    with store.connect() as c:
        scope=offered['snapshot']['feature_step']['scope']
        assert len(feature_attempts.coding_grants(c,scope))==1
        parent=c.execute(select(sessions).where(sessions.c.id==decision)).mappings().one()
        selected=parent['state']['feature_slot']['snapshot']['feature_offer_id']
        original=dict(c.execute(select(real_execution_grants)).mappings().one())
    # Every issued capacity reservation counts, including an already revoked
    # grant with unavailable request/usage. No missing usage becomes zero.
    with write_transaction(store) as c:
        for index in [2,3]:
            snapshot=copy.deepcopy(original['snapshot'])
            snapshot['runtime']['controlled_reservation']=index
            snapshot['task_budget']['offer']['id']=str(uuid4())
            c.execute(real_execution_grants.insert().values(id=str(uuid4()),snapshot=snapshot,
                binding=digest(snapshot),issued_at=now(),expires_at=original['expires_at'],revoked_at=now()))
    with store.connect() as c:
        assert len(feature_attempts.coding_grants(c,scope))==3
        approval=c.execute(select(proposal_decisions).where(proposal_decisions.c.id==decision)).mappings().one()
        assert approval['payload']['feature_run']['budget']['implementation_attempts_per_step']==3
        with pytest.raises(ValueError,match='coding-grant budget'):
            task_budget.permission(c,approval,task_id=first,offer_id=selected)
        with pytest.raises(ValueError,match='attempt budget is exhausted'):
            feature_attempts.settled(c,offered)
