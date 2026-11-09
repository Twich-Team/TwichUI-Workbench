"""The real scope-save -> review -> separate-run transition, without live calls."""
import copy
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from workbench import bug_intent, intake, regression_preparation, scope_binding, worker
from workbench.db import (intakes, intake_scopes, planning_jobs, proposals, model_attempts,
                          proposal_decisions, real_execution_grants, write_transaction)
from workbench.planning_provider import LocalProvider
from test_bug_intent_workflow import scope
from test_bug_workflow import enable_bugs, report, get_run_offer
from test_everyday import installed, write_policy  # noqa: F401

pytestmark = pytest.mark.usefixtures('installed')


def unapproved(client, headers, store, monkeypatch):
    enable_bugs(monkeypatch)
    config = Path(os.environ['WORKBENCH_PREPARATION_CONFIG'])
    settings = json.loads(config.read_text())
    settings['bug_plan_review'] = 'source-grounded-intent-v1'
    write_policy(config, settings)
    calls = []

    def generate(self, packet, schema, expected_model):
        calls.append(copy.deepcopy(packet))
        assert schema is bug_intent.Response
        self.diagnostics = dict(actual_model=expected_model, generation_state='finished',
            accounting_state='complete', finish_reason='stop', request_completion_limit=6144,
            usage=dict(units='tokens', values=dict(prompt_tokens=40,completion_tokens=10,total_tokens=50)))
        return dict(finding='The inspected function omits normalization.', plan=scope()), expected_model, 'local'

    monkeypatch.setattr(LocalProvider, 'generate', generate)
    item = client.post('/api/intake', headers=headers, json={**report(), 'visual_requirement':'unclassified'}).json()
    assert worker.tick(store)
    item = client.get('/api/intake/'+item['id']).json()
    assert item['status'] == 'completed'
    return item, calls


def scope_command(item):
    p = item['proposals'][0]
    return dict(command_id=str(uuid4()), proposal_id=p['id'], binding=p['binding'],
        visual_requirement='nonvisual', stage='implementation',
        note='Correct the reported behavior while preserving the existing layout and unrelated data.')


def get(client, item):
    return client.get('/api/intake/'+item['id']).json()


def approve(client, headers, item):
    p = item['proposals'][0]
    command = dict(command_id=str(uuid4()), proposal_id=p['id'], binding=p['binding'],
        scope_revision=p['scope_revision'], action='approve', note='Independent controlled review.',
        expectation_review=p['task_contract']['bug_intent']['review_digest'])
    r = client.post('/api/intake/'+item['id']+'/decisions', headers=headers, json=command)
    assert r.status_code == 200, r.text
    return command


def test_readonly_refinement_then_scope_save_then_review_then_separate_run(client,headers,store,monkeypatch):
    item, calls = unapproved(client,headers,store,monkeypatch)
    url = '/api/intake/'+item['id']
    refined = client.post(url+'/clarification', headers=headers, json=dict(command_id=str(uuid4()),
        revision=item['revision'], text='Clarify the bounded repair scope without approving changes.',
        continue_investigation=True, renew_operation=True))
    assert refined.status_code == 200, refined.text
    assert worker.tick(store)
    item = get(client,item)
    assert item['revision'] == 2 and len(calls) == 2
    old_proposal = item['proposals'][0]
    command = scope_command(item)
    r = client.post(url+'/scope', headers=headers, json=command)
    assert r.status_code == 200, r.text
    current = r.json()
    p = current['proposals'][0]
    assert current['status'] == 'completed' and current['revision'] == 3
    assert not p['approval_blockers'] and p['content'] == old_proposal['content']
    assert p['task_contract']['bug_intent']['plan'] == old_proposal['task_contract']['bug_intent']['plan']
    assert p['task_contract']['writable_paths'] == old_proposal['task_contract']['writable_paths']
    assert p['binding'] != old_proposal['binding']
    assert command['note'] in p['task_contract']['review_feedback']
    assert current['jobs'][-1]['operation_kind'] == 'scope_binding'
    assert not current['jobs'][-1]['model'] and current['jobs'][-1]['attempts'] == 0
    assert not current['decisions'] and not current['tasks'] and len(calls) == 2
    assert client.post(url+'/scope', headers=headers, json=command).json() == current
    assert client.post(url+'/scope', headers=headers, json={**command,'note':'Changed command'}).status_code == 409
    store.dispose()  # Reload from the committed journal, with no hidden in-memory state.
    assert get(client,item) == current
    from workbench.delivery_phase import observe, renewable
    with store.connect() as c:
        ledger = observe(c,item['id'],[Path(os.environ['WORKBENCH_EVERYDAY_CONFIG']).parent.parent])
        assert renewable(ledger) and ledger['next_sequence'] == 4
        assert len(ledger['phases']) == 2 and len(ledger['local_operations']) == 1
        assert ledger['known_usage']['total_tokens'] == 100
    approval = approve(client, headers, current)
    for _ in range(3):
        assert worker.tick(store)
    endpoint, offered = get_run_offer(client, current)
    assert offered['available'], offered
    assert offered['offer']['repair_cycle']['verification'] == 'not_run'
    assert len(calls) == 2  # Scope, approval and offer preparation do not regenerate a plan.
    with store.connect() as c:
        assert not c.execute(select(real_execution_grants)).first()
        assert c.execute(select(proposal_decisions.c.id)).scalars().all() == [approval['command_id']]
        current_p = dict(c.execute(select(proposals).where(proposals.c.id == p['id'])).mappings().one())
        assert regression_preparation.contract_for(c,current_p) == p['task_contract']
    # Old scope binding cannot authorize a run, even with a current offer budget.
    rejected = client.post(endpoint,headers=headers,json=dict(command_id=str(uuid4()),
        scope_approval_id=approval['command_id'],binding=old_proposal['binding'],
        run_budget=offered['offer']['budget'],note='Stale controlled request'))
    assert rejected.status_code == 409


def legacy_scope(client,headers,store,monkeypatch):
    item, calls = unapproved(client,headers,store,monkeypatch)
    command = scope_command(item)
    parent = item['proposals'][0]
    with write_transaction(store) as c:
        c.execute(intake_scopes.insert().values(intake_id=item['id'], revision=2,
            command_id=command['command_id'], visual_requirement='nonvisual', stage='implementation',
            origin='human', note=command['note'], change_payload=command, created_at=intake.now()))
        c.execute(proposals.insert().values(id=str(uuid4()), intake_id=item['id'],revision=2,
            scope_revision=2,content=parent['content'],model=parent['model'],basis='preparation',created_at=intake.now()))
        c.execute(intakes.update().where(intakes.c.id == item['id']).values(revision=2,status='completed'))
        job = intake.enqueue(c,item['id'],'planning',3,dict(feedback=command['note']))
        c.execute(intakes.update().where(intakes.c.id == item['id']).values(status='queued'))
    # Reproduce the exact old input through the real preflight path. The mock
    # provider is installed, but must never be invoked for this failed operation.
    assert worker.tick(store)
    assert len(calls) == 1
    with write_transaction(store) as c:
        row = c.execute(select(planning_jobs).where(planning_jobs.c.id == job)).mappings().one()
        assert row['message'] == scope_binding.EARLY_REJECTION
        assert row['payload']['preflight_rejection']['provider_dispatch'] == 'not_started'
        assert row['attempts'] == 0 and row['lease'] is None
        assert not c.execute(select(model_attempts).where(model_attempts.c.job_id == job)).first()
        # Exact historical format, before preflight markers were implemented.
        c.execute(planning_jobs.update().where(planning_jobs.c.id == job).values(payload={
            k:v for k,v in row['payload'].items() if k != 'preflight_rejection'}))
    return get(client,item), calls


def test_legacy_saved_scope_is_recovered_idempotently_with_no_approval_or_model_replay(client,headers,store,monkeypatch):
    item, calls = legacy_scope(client,headers,store,monkeypatch)
    url = '/api/intake/'+item['id']
    assert item['status'] == 'failed' and item['scope_recovery']
    before = copy.deepcopy(item)
    blocked = client.post(url+'/scope',headers=headers,json=scope_command(item))
    assert blocked.status_code == 409 and get(client,item) == item
    body = dict(command_id=str(uuid4()),binding=item['scope_recovery']['binding'])
    r = client.post(url+'/scope-recovery',headers=headers,json=body)
    assert r.status_code == 200,r.text
    recovered = r.json()
    assert recovered['revision'] == 4 and recovered['status'] == 'completed'
    assert not recovered.get('scope_recovery') and not recovered['proposals'][0]['approval_blockers']
    assert recovered['human_scope'] == before['human_scope']
    assert recovered['scope_history'] == before['scope_history']
    assert recovered['original'] == before['original']
    assert recovered['proposals'][0]['content'] == before['proposals'][-1]['content']
    assert recovered['jobs'][:-1] == before['jobs']
    assert not recovered['decisions'] and not recovered['tasks'] and len(calls) == 1
    store.dispose()
    assert client.post(url+'/scope-recovery',headers=headers,json=body).json() == recovered
    assert client.post(url+'/scope-recovery',headers=headers,json={**body,'binding':'0'*64}).status_code == 409
    # Future ordinary plan refinement must count only actual provider generations,
    # while accounting for every human/local operation in revision allocation.
    r = client.post(url+'/clarification',headers=headers,json=dict(command_id=str(uuid4()),
        revision=4,text='Review the existing uncertainty before I decide.',continue_investigation=True,renew_operation=True))
    assert r.status_code == 200,r.text
    assert r.json()['jobs'][-1]['revision'] == 5
    assert worker.tick(store)
    assert get(client,item)['status'] == 'completed' and len(calls) == 2


@pytest.mark.parametrize('fault',['active','unknown','unrecorded_attempt','changed_error','changed_input','changed_scope'])
def test_recovery_never_promotes_absence_of_process_to_terminal_evidence(client,headers,store,monkeypatch,fault):
    item, calls = legacy_scope(client,headers,store,monkeypatch)
    offered = item['scope_recovery']
    with write_transaction(store) as c:
        job = dict(c.execute(select(planning_jobs).where(planning_jobs.c.id == item['jobs'][-1]['id'])).mappings().one())
        if fault in {'active','unknown'}:
            attempt = c.execute(select(model_attempts).where(model_attempts.c.task_id == item['id'])).mappings().one()
            c.execute(model_attempts.update().where(model_attempts.c.id == attempt['id']).values(
                finished_at=None if fault == 'active' else intake.now(),
                outcome='running' if fault == 'active' else 'interrupted_unknown',usage=None))
        elif fault == 'unrecorded_attempt':
            c.execute(planning_jobs.update().where(planning_jobs.c.id == job['id']).values(attempts=1))
        elif fault == 'changed_error':
            c.execute(planning_jobs.update().where(planning_jobs.c.id == job['id']).values(message='Transport disconnected'))
        elif fault == 'changed_input':
            c.execute(planning_jobs.update().where(planning_jobs.c.id == job['id']).values(payload={**job['payload'],'scenario_generation_claim':{'uncertain':True}}))
        else:
            c.execute(intake_scopes.update().where(intake_scopes.c.intake_id == item['id'],intake_scopes.c.revision == 2).values(note='Changed saved constraint'))
    assert not get(client,item).get('scope_recovery')
    r = client.post('/api/intake/'+item['id']+'/scope-recovery',headers=headers,
        json=dict(command_id=str(uuid4()),binding=offered['binding']))
    assert r.status_code == 409 and len(calls) == 1


def test_research_revision_cannot_retain_an_implementation_contract(client,headers,store,monkeypatch):
    item, calls = unapproved(client,headers,store,monkeypatch)
    r = client.post('/api/intake/'+item['id']+'/scope',headers=headers,
        json={**scope_command(item),'stage':'research'})
    assert r.status_code == 200,r.text
    p = r.json()['proposals'][0]
    assert p['research_only'] and not p.get('task_contract') and p['content']['delivery'] == 'research'
    assert p['approval_blockers'] and 'research-only' in p['approval_blockers'][0]
    assert len(calls) == 1 and not r.json()['tasks']


def test_request_changes_uses_the_same_bound_readonly_refinement_path(client,headers,store,monkeypatch):
    item, calls = unapproved(client,headers,store,monkeypatch)
    url = '/api/intake/'+item['id']
    p = item['proposals'][0]
    command = dict(command_id=str(uuid4()),proposal_id=p['id'],binding=p['binding'],
        scope_revision=p['scope_revision'],action='changes',note='Make the scope and remaining uncertainty clearer.')
    r = client.post(url+'/decisions',headers=headers,json=command)
    assert r.status_code == 200,r.text
    assert worker.tick(store)  # Apply the actual decision, with no nested transaction.
    assert worker.tick(store)  # Fresh same-provider read-only refinement.
    item = get(client,item)
    assert item['status'] == 'completed' and item['revision'] == 2 and len(calls) == 2
    assert item['decisions'][0]['status'] == 'applied' and not item['tasks']
    assert client.post(url+'/decisions',headers=headers,json=command).json()['status'] == 'applied'
    assert not worker.tick(store)
    changed = client.post(url+'/scope',headers=headers,json=scope_command(item))
    assert changed.status_code == 200,changed.text
    assert not changed.json()['proposals'][0]['approval_blockers']
    approve(client,headers,changed.json())
    for _ in range(3):
        assert worker.tick(store)
    _, offer = get_run_offer(client,item)
    assert offer['available'],offer
    assert len(calls) == 2


def test_interrupted_local_reconciliation_rolls_back_and_same_command_can_finish(client,headers,store,monkeypatch):
    item, calls = legacy_scope(client,headers,store,monkeypatch)
    url = '/api/intake/'+item['id']+'/scope-recovery'
    command = dict(command_id=str(uuid4()),binding=item['scope_recovery']['binding'])
    original = scope_binding.complete
    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('Controlled process failure before atomic publication')
    with monkeypatch.context() as fault:
        fault.setattr(scope_binding,'complete',interrupted)
        with pytest.raises(RuntimeError, match='Controlled process failure'):
            client.post(url,headers=headers,json=command)
    store.dispose()
    assert get(client,item) == item
    r = client.post(url,headers=headers,json=command)
    assert r.status_code == 200,r.text
    assert r.json()['status'] == 'completed' and len(calls) == 1
    assert not r.json()['decisions'] and not r.json()['tasks']


def test_scope_change_waits_for_live_refinement_without_losing_existing_review(client,headers,store,monkeypatch):
    item, calls = unapproved(client,headers,store,monkeypatch)
    url = '/api/intake/'+item['id']
    queued = client.post(url+'/clarification',headers=headers,json=dict(command_id=str(uuid4()),
        revision=item['revision'],text='Refine the plan.',continue_investigation=True,renew_operation=True))
    assert queued.status_code == 200,queued.text
    r = client.post(url+'/scope',headers=headers,json=scope_command(item))
    assert r.status_code == 409 and 'still refining' in r.json()['detail']
    waiting = get(client,item)
    assert waiting['human_scope'] == item['human_scope'] and len(calls) == 1
    assert worker.tick(store)
    settled = get(client,item)
    r = client.post(url+'/scope',headers=headers,json=scope_command(settled))
    assert r.status_code == 200 and not r.json()['proposals'][0]['approval_blockers']
