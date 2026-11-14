"""Fresh policy authority, original history and ordinary worker progression."""
import copy
import json
import os
from pathlib import Path

import pytest
from sqlalchemy import select

from workbench import bug_authority as authority, model_policy, work_controls, worker
from workbench.db import planning_jobs, proposal_decisions, real_execution_grants
from workbench.planning_provider import LocalProvider
from test_bug_workflow import enable_bugs, report
from test_bug_intent_workflow import scope
from test_everyday import installed, write_policy  # noqa: F401


def enable(store):
    return authority.save(store, authority.ControlInput(revision=model_policy.read(store).revision, enabled=True))


def test_setting_boundary_revocation_and_reenable_do_not_resurrect_authority(client, headers, store):
    url='/api/routine-bug-policy'
    assert client.get(url).json()['enabled'] is False
    assert client.put(url,json=dict(revision=0,enabled=True)).status_code==403
    first=client.put(url,headers=headers,json=dict(revision=0,enabled=True))
    assert first.status_code==200,first.text
    assert client.put(url,headers=headers,json=dict(revision=0,enabled=True)).json()==first.json()
    policy=model_policy.read(store)
    identity=policy.routine_bugs.authorization_id
    # Generic model edits cannot change this owner permission.
    model_policy.save(store,policy.model_copy(update=dict(routine_bugs=None)),model_policy.capabilities())
    assert model_policy.read(store).routine_bugs.authorization_id==identity
    current=authority.read(store)
    authority.save(store,authority.ControlInput(revision=current.revision,enabled=False))
    enable(store)
    assert model_policy.read(store).routine_bugs.authorization_id!=identity
    with pytest.raises(Exception):
        authority.save(store,authority.ControlInput(revision=0,enabled=False))


@pytest.mark.usefixtures('installed')
def test_fresh_bug_advances_to_separate_run_authority_without_human_approval_and_preserves_history(client,headers,store,monkeypatch):
    enable_bugs(monkeypatch)
    config=Path(os.environ['WORKBENCH_PREPARATION_CONFIG'])
    value=json.loads(config.read_text())
    value['bug_plan_review']='source-grounded-intent-v1'
    write_policy(config,value)
    from test_verification_profiles import git, TEST, GOOD_TEST
    source=Path(os.environ['WORKBENCH_RESEARCH_ROOT'])
    (source/TEST).write_text((source/TEST).read_text()+GOOD_TEST)
    git(source,'add','.')
    git(source,'-c','user.name=Fixture','-c','user.email=fixture@invalid','commit','-qm','Reported failure')
    # This report predates policy adoption and cannot inherit it.
    historical=client.post('/api/intake',headers=headers,json=report()).json()
    enable(store)
    calls=[]
    def generate(self,packet,schema,expected_model):
        calls.append(copy.deepcopy(packet))
        self.diagnostics=dict(actual_model=expected_model,generation_state='finished',accounting_state='complete',finish_reason='stop',
            request_completion_limit=6144,usage=dict(units='tokens',values=dict(prompt_tokens=40,completion_tokens=10,total_tokens=50)))
        return dict(finding='The inspected function omits normalization.',plan={**scope(),'repair_profile':dict(version=2,kind='existing-failure')}),expected_model,'local'
    monkeypatch.setattr(LocalProvider,'generate',generate)
    from test_repair_profiles_v2 import ProfileRuntime
    monkeypatch.setattr('workbench.trusted_preparation.Runtime',ProfileRuntime)
    submitted=report()
    submitted.pop('local_planning')
    response=client.post('/api/intake',headers=headers,json=submitted)
    assert response.status_code==200,response.text
    item=response.json()
    url='/api/intake/'+item['id']
    assert item['original']['local_planning']=='qwen-once'
    assert client.post('/api/intake',headers=headers,json=submitted).json()['id']==item['id']
    # Stop before the configured real controller; all stages through queued run
    # admission use the production worker and actual isolated preparation.
    monkeypatch.setattr('workbench.task_sessions.process_one',lambda *a,**kw:False)
    for _ in range(16):
        worker.tick(store)
    saved=client.get(url)
    assert saved.status_code==200,saved.text
    item=saved.json()
    with store.connect() as c:
        decisions=list(c.execute(select(proposal_decisions).where(proposal_decisions.c.intake_id==item['id'])).mappings())
        assert len(decisions)==2,item
        scope_decision,run=sorted(decisions,key=lambda d:bool(d['payload'].get('run_budget')))
        assert all(d['status']=='applied' for d in decisions),decisions
        proof=scope_decision['payload']['policy_authorization']
        frozen=authority.frozen(c,item['id'])
        assert proof==authority.attribution(item['id'],frozen)
        assert proof['actor']=='owner-policy'
        assert 'expectation_review' not in scope_decision['payload']
        for fault in ('base', 'scope'):
            invalid=copy.deepcopy(scope_decision['snapshot'])
            if fault=='base':
                invalid['task_contract']['resolved_base']='f'*40
            else:
                invalid['task_contract']['writable_paths']=['tools/auth/secrets.py']
            with pytest.raises(ValueError):
                authority.validate(c,item['id'],invalid,proof)
        assert run['payload']['execution_continuation']['owner_provenance']==proof
        assert run['payload']['run_budget']['publication']=='hold'
        assert 'browser_request' not in run['payload']['execution_continuation']
        from workbench.task_budget import permission
        assert permission(c,run)
        assert authority.frozen(c,historical['id']) is None
        assert not c.execute(select(proposal_decisions).where(proposal_decisions.c.intake_id==historical['id'])).first()
        assert not c.execute(select(real_execution_grants)).first()
        before=[dict(d) for d in decisions]
    # Exercise actual grant issuance and dispatch with controlled preflight only.
    # No provider transport runs, and this does not prove real autonomous coding.
    from workbench import task_budget, task_execution
    with store.connect() as c:
        permission_value=permission(c,run)
        selected=permission_value['offer']
    runtime=ProfileRuntime(Path(selected['snapshot']['ready_preparation']['root']))
    runtime.task_ref=dict(intake_id=item['id'], task_id=item['tasks'][-1]['id'], approval_id=run['id'])
    runtime.preflight=lambda:[dict(name='controlled-preflight',status='passed',evidence='No model or service is contacted')]
    assert task_budget.authorize_one(store,runtime)
    assert not task_budget.authorize_one(store,runtime)
    assert task_execution.dispatch_one(store)
    assert not task_execution.dispatch_one(store)
    with store.connect() as c:
        grant=c.execute(select(real_execution_grants)).mappings().one()
        assert grant['snapshot']['workflow_task']['approval']['payload']['policy_authorization']==proof
        assert grant['snapshot']['task_budget']['decision_id']==run['id']
    # Pause/revocation fence admission, but never rewrite historical receipts.
    revision=model_policy.read(store).revision
    work_controls.save(store,work_controls.WorkControlInput(revision=revision,paused=True))
    with store.connect() as c:
        with pytest.raises(ValueError):
            permission(c,run)
    work_controls.save(store,work_controls.WorkControlInput(revision=revision+1,paused=False))
    current=authority.read(store)
    authority.save(store,authority.ControlInput(revision=current.revision,enabled=False))
    assert client.post('/api/intake',headers=headers,json=submitted).json()['id']==item['id']
    with store.connect() as c:
        with pytest.raises(ValueError):
            permission(c,run)
        assert list(map(dict,c.execute(select(proposal_decisions).where(proposal_decisions.c.intake_id==item['id'])).mappings()))==before
        assert len(list(c.execute(select(planning_jobs).where(planning_jobs.c.intake_id==item['id'],planning_jobs.c.kind=='planning'))))==1


@pytest.mark.parametrize('path',['../outside.lua','!!!TwichUI/libs/secret.lua','tools/../secrets.py','tests/harness.lua','.github/workflows/run.yml','tools/auth/credential.py'])
def test_routine_policy_refuses_unsupported_or_privileged_paths(path):
    assert not authority.permitted_path(path)
