"""Feature intake/source/plans/dependencies in the real API and worker; no real model."""
import copy
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from workbench import admission, feature_planning, planning_worker, worker
from workbench.api import create_app
from workbench.db import (engine_for, intakes, planning_jobs, model_attempts, backlog_items,
                          real_execution_grants, real_execution_requests, write_transaction)
from workbench.schemas import PlanningOutput
from test_everyday import installed as installed


class ControlledFeaturePlanner:
    def __init__(self, mutate=None, *, terminal_receipts=False):
        self.calls = []
        self.mutate = mutate
        self.terminal_receipts = terminal_receipts
        self.diagnostics = {'generation_state':'finished', 'usage':None}

    def generate(self, packet, schema, expected_model=None):
        self.calls.append(copy.deepcopy(packet))
        ids = [item['id'] for item in packet['evidence']]
        if schema is PlanningOutput:
            result = json.loads(admission.FIXTURE.read_text())['planning']
            result['summary'] = 'Retain the requested normalization preference.'
            result['evidence_ids'] = ids[:2]
            result['proposal'].update(summary=packet['original']['title'],
                evidence_ids=ids[:2], scope=['Save the chosen preference.', 'Restore the saved preference.'],
                visual='nonvisual', delivery='implementation')
        else:
            result = dict(tasks=[dict(key=key, kind='task', parent=None,
                title=title, purpose=title, acceptance=['The reviewed behavior passes an independent check.'],
                scope_indices=[index], depends_on=['save'] if index else [], source_ids=ids[:2])
                for index, (key, title) in enumerate([('save', 'Save the preference'), ('restore', 'Restore the preference')])])
        if self.mutate:
            self.mutate(result, schema)
        if self.terminal_receipts:
            usage = dict(source='controlled provider', units='tokens', status='complete', requests_observed=1,
                values=dict(prompt_tokens=100, completion_tokens=100, total_tokens=200))
            self.diagnostics.update(accounting_state='complete',usage=usage, actual_model=expected_model)
            self.response_evidence.received(b'{}')
            self.response_evidence.envelope(dict(id='chatcmpl-'+str(uuid4())),
                dict(generation_state='finished', finish_reason='stop',model_matches=1,
                     accounting_state='complete', usage=usage))
        return result, expected_model, 'demo'


def test_feature_wire_contract_requires_actionable_flat_steps_and_rejects_empty_containers():
    from workbench.planning_response import wire_schema
    schema = wire_schema(feature_planning.FeatureDecomposition)
    task = schema['$defs']['FeatureTask']['properties']
    assert task['kind']['const'] == 'task'
    assert task['parent']['type'] == 'null'
    assert schema['properties']['tasks']['maxItems'] == 6
    # The real provider repeatedly used an empty milestone as a checking step.
    # Constrain generation to supported steps; never silently convert that output
    # or bypass the canonical hierarchy/coverage validator.
    value = dict(tasks=[dict(key='verify', kind='milestone', parent=None,
        title='Verify the requested behavior', purpose='Check the combined result.',
        acceptance=['The requested behavior passes independent checks.'],
        depends_on=[], scope_indices=[0], source_ids=['source-1'])])
    with pytest.raises(ValueError):
        feature_planning.FeatureDecomposition.model_validate(value)
    value['tasks'][0]['kind'] = 'task'
    parsed = feature_planning.validate_response(value,feature_planning.FeatureDecomposition,
        dict(evidence=[dict(id='source-1')],proposal=dict(scope=['Verify the requested behavior.'])))
    assert parsed['decomposition']['tasks'][0]['kind'] == 'task'


def submit(client, headers, **changes):
    body = dict(request_id=str(uuid4()), kind='feature', requested_work='feature', feature_planning=True,
                title='Remember the normalization preference', description='Save my chosen normalization setting and restore it on the next session.',
                visual_requirement='nonvisual', local_planning='qwen-once')
    body.update(changes)
    result = client.post('/api/intake', headers=headers, json=body)
    assert result.status_code == 200, result.text
    return result.json(), body


def test_connected_feature_source_plan_decomposition_restart_and_review(
    client, headers, store, installed,
):
    planner = ControlledFeaturePlanner()
    item, body = submit(client, headers)
    assert client.post('/api/intake', headers=headers, json=body).json()['id'] == item['id']
    assert worker.tick(store, planning_adapter=planner)
    planned = client.get('/api/features/'+item['id']).json()
    assert planned['state'] == 'needs_owner_action' and planned['total_steps'] == 0
    assert worker.tick(store, planning_adapter=planner)
    data = client.get('/api/intake/'+item['id']).json()
    steps = [t for t in data['tasks'] if t['kind']=='task']
    assert len(steps) == 2 and steps[1]['dependencies'] == [steps[0]['id']]
    assert len(planner.calls) == 2 and all(call['files'] and call['base'] for call in planner.calls)
    assert all(e['kind']=='addon' and e['source_revision']==planner.calls[0]['base'] for e in data['evidence'])
    assert not worker.tick(store, planning_adapter=planner)
    restarted = engine_for(store.url.database)
    with TestClient(create_app(restarted)) as other:
        restored = other.get('/api/features/'+item['id']).json()
        assert restored['total_steps'] == 2 and restored['completed_steps'] == 0
        assert restored['intake']['original'] == data['original']
    restarted.dispose()
    proposal = data['proposals'][0]
    decision = dict(command_id=str(uuid4()), proposal_id=proposal['id'], binding=proposal['binding'],
                    scope_revision=proposal['scope_revision'], action='approve', note='Controlled scope review only.')
    assert client.post('/api/intake/'+item['id']+'/decisions', headers=headers, json=decision).status_code == 200
    assert planning_worker.apply_one(store)
    assert not worker.tick(store, planning_adapter=planner)
    assert len(planner.calls) == 2
    assert client.get('/api/intake/'+item['id']).json()['status'] == 'tasks_ready'
    with store.connect() as c:
        assert not c.execute(select(real_execution_grants)).first()
        assert not c.execute(select(real_execution_requests)).first()


@pytest.mark.parametrize('fault', ['cycle', 'missing', 'duplicate', 'uncovered', 'source'])
def test_invalid_decomposition_never_partially_creates_tasks(client, headers, store, installed, fault):
    def corrupt(result, schema):
        if schema is PlanningOutput:
            return
        first, second = result['tasks']
        if fault == 'cycle':
            first['depends_on'] = ['restore']
        elif fault == 'missing':
            second['depends_on'] = ['absent']
        elif fault == 'duplicate':
            second['key'] = 'save'
        elif fault == 'uncovered':
            second['scope_indices'] = [0]
        else:
            first['source_ids'] = ['invented-source']
    planner = ControlledFeaturePlanner(corrupt)
    item, _ = submit(client, headers)
    assert worker.tick(store, planning_adapter=planner)
    assert worker.tick(store, planning_adapter=planner)
    data = client.get('/api/intake/'+item['id']).json()
    assert data['status']=='failed' and not data['tasks']
    assert data['proposals'] and len(planner.calls)==2
    assert not worker.tick(store, planning_adapter=planner)


def test_clarification_and_changed_requirements_preserve_original_and_fence_late_output(
    client, headers, store, installed,
):
    def question(result, schema):
        result.update(proposal=None, clarification='Should this setting apply to one character or every character?')
    item, original = submit(client, headers)
    assert worker.tick(store, planning_adapter=ControlledFeaturePlanner(question))
    data = client.get('/api/features/'+item['id']).json()
    assert data['state']=='needs_clarification' and data['needs_owner']
    assert data['intake']['original']['description']==original['description']
    assert not data['steps']
    assert not worker.tick(store, planning_adapter=ControlledFeaturePlanner())
    answer = dict(command_id=str(uuid4()), revision=data['intake']['revision'], text='Use a setting for each character.')
    assert client.post('/api/intake/'+item['id']+'/clarification', headers=headers, json=answer).status_code==200
    assert client.post('/api/intake/'+item['id']+'/clarification', headers=headers, json=answer).status_code==200
    followup = ControlledFeaturePlanner()
    assert worker.tick(store, planning_adapter=followup)
    assert worker.tick(store, planning_adapter=followup)
    revised = client.get('/api/features/'+item['id']).json()
    assert revised['total_steps']==2 and revised['intake']['revision']==2
    assert followup.calls[0]['feedback']==answer['text']
    assert revised['intake']['original']['description']==original['description']


def test_interrupted_provider_claim_is_retained_without_automatic_replay(
    client, headers, store, installed, monkeypatch,
):
    item, _ = submit(client, headers)
    with pytest.raises(RuntimeError, match='completion crash'):
        feature_planning.process_one(store, adapter=ControlledFeaturePlanner(), fail_before_commit=True)
    monkeypatch.setattr('workbench.real_execution.alive', lambda owner:False)
    planner = ControlledFeaturePlanner()
    assert worker.tick(store, planning_adapter=planner)
    assert not worker.tick(store, planning_adapter=planner) and not planner.calls
    with store.connect() as c:
        attempt = c.execute(select(model_attempts).where(model_attempts.c.task_id==item['id'])).mappings().one()
        assert attempt['outcome']=='interrupted_unknown'
        assert not c.execute(select(backlog_items).where(backlog_items.c.intake_id==item['id'])).first()
        job_id = c.execute(select(planning_jobs.c.id).where(planning_jobs.c.intake_id==item['id'])).scalar()
    assert client.post('/api/intake/'+item['id']+'/retry', headers=headers, json=dict(job_id=job_id)).status_code==409


def test_newer_scope_rejects_late_plan_without_replacing_current_state(client, headers, store, installed):
    item, _ = submit(client, headers)
    def change(result, schema):
        with write_transaction(store) as c:
            c.execute(intakes.update().where(intakes.c.id==item['id']).values(revision=4, status='queued'))
    assert worker.tick(store, planning_adapter=ControlledFeaturePlanner(change))
    data = client.get('/api/intake/'+item['id']).json()
    assert data['revision']==4 and data['status']=='queued' and not data['proposals']
    with store.connect() as c:
        assert c.execute(select(planning_jobs.c.status).where(planning_jobs.c.intake_id==item['id'])).scalar()=='failed'


def test_feature_model_choices_are_frozen_after_global_settings_change(client, headers, store, installed):
    from workbench import everyday
    from test_grounded_workflow import save_local_policy
    item, _ = submit(client, headers, ai_settings=dict(primary=dict(provider='local', model=everyday.MODEL)))
    save_local_policy(store, model='a-different-later-global-choice')
    planner = ControlledFeaturePlanner()
    assert worker.tick(store, planning_adapter=planner)
    assert worker.tick(store, planning_adapter=planner)
    assert client.get('/api/features/'+item['id']).json()['total_steps']==2
    with store.connect() as c:
        assert set(c.execute(select(model_attempts.c.requested_model).where(model_attempts.c.task_id==item['id'])).scalars())=={everyday.MODEL}


@pytest.mark.parametrize('started', [False, True])
def test_feature_fallback_requires_proven_no_generation(client, headers, store, installed, monkeypatch, started):
    from test_everyday import save_codex_policy
    from workbench.planning_provider import LocalBeforeGeneration, LocalProvider
    from workbench.planning_transport import PlanningUnavailable
    from workbench import codex_provider
    save_codex_policy(store, both=True)
    item, _ = submit(client, headers, local_planning='qwen-with-codex-fallback')
    def unavailable(self, *args, **kwargs):
        if started:
            raise PlanningUnavailable('A partial result cannot switch providers.', 'incomplete_output')
        raise LocalBeforeGeneration()
    calls = []
    class ControlledCodex:
        def __init__(self, selection, stop):
            self.selection=selection
            self.diagnostics={'generation_state':'finished'}
        def bind_source(self, packet):
            assert packet['files'] and packet['base']
        def generate(self, packet, schema, expected_model=None):
            calls.append(packet)
            raw, model, _ = ControlledFeaturePlanner().generate(packet, schema, expected_model)
            return raw, model, 'codex'
    monkeypatch.setattr(LocalProvider, 'generate', unavailable)
    monkeypatch.setattr(codex_provider, 'Planner', ControlledCodex)
    assert worker.tick(store)
    data=client.get('/api/intake/'+item['id']).json()
    assert bool(data['proposals']) is (not started)
    assert len(calls)==(0 if started else 1)
    with store.connect() as c:
        attempts=c.execute(select(model_attempts).where(model_attempts.c.task_id==item['id'])).mappings().all()
        assert len(attempts)==(1 if started else 2)
        if not started:
            assert any(a['outcome']=='not_started' and a['backend']=='local' for a in attempts)
            assert any(a['outcome']=='completed' and a['backend']=='codex' for a in attempts)


def test_feature_decomposition_keeps_existing_model_reservation_demand(client, headers, store, installed):
    from workbench.local_model_priority import planning_demand
    item, _=submit(client, headers)
    assert item['id'] in planning_demand(store, eligible=True)
    assert worker.tick(store, planning_adapter=ControlledFeaturePlanner())
    assert item['id'] in planning_demand(store, eligible=True)
    assert worker.tick(store, planning_adapter=ControlledFeaturePlanner())
    assert item['id'] not in planning_demand(store, eligible=True)


def test_feature_source_packet_passes_real_disclosure_binding_without_provider_call(client, headers, store, installed):
    from workbench.codex_provider import Planner, Hold
    item, _=submit(client, headers)
    packet=feature_planning.research(item['original'])
    planner=Planner(dict(model='NON-MODEL', effort=None), lambda:False)
    planner.bind_source(packet)
    planner.validate_source(packet)
    tampered=copy.deepcopy(packet)
    tampered['files'][0]['excerpts'][0]['text']='Invented source is not disclosed.'
    with pytest.raises(Hold):
        planner.validate_source(tampered)


def test_changed_source_holds_new_review_but_preserves_saved_decision_replay(client, headers, store, installed):
    from workbench import regression_preparation
    from test_verification_profiles import git
    from test_intake import decision
    item, _=submit(client, headers)
    assert worker.tick(store, planning_adapter=ControlledFeaturePlanner())
    assert worker.tick(store, planning_adapter=ControlledFeaturePlanner())
    data=client.get('/api/intake/'+item['id']).json()
    command=decision(data)
    saved=client.post('/api/intake/'+item['id']+'/decisions', headers=headers, json=command)
    assert saved.status_code==200
    root, _, _=regression_preparation.repository()
    path=root/'!!!TwichUI/normalize.lua'
    path.write_text(path.read_text()+'\n-- a later unrelated source revision\n')
    git(root,'add','!!!TwichUI/normalize.lua')
    git(root,'-c','user.name=NON-MODEL','-c','user.email=fixture@example.invalid','commit','-qm','Later source')
    assert client.post('/api/intake/'+item['id']+'/decisions', headers=headers, json=command).json()==saved.json()
    assert client.post('/api/intake/'+item['id']+'/decisions', headers=headers, json={**command,'command_id':str(uuid4())}).status_code==409


@pytest.mark.parametrize('during', [False, True])
def test_review_before_or_during_decomposition_keeps_the_exact_decision_binding(
    client, headers, store, installed, during,
):
    from test_intake import decision
    item,_=submit(client,headers)
    assert worker.tick(store,planning_adapter=ControlledFeaturePlanner())
    def approve():
        data=client.get('/api/intake/'+item['id']).json()
        command=decision(data)
        assert client.post('/api/intake/'+item['id']+'/decisions', headers=headers, json=command).status_code==200
        assert planning_worker.apply_one(store)
        return command['command_id']
    decisions=[]
    if not during:
        decisions.append(approve())
    def callback(result,schema):
        if during:
            decisions.append(approve())
    assert worker.tick(store,planning_adapter=ControlledFeaturePlanner(callback))
    data=client.get('/api/intake/'+item['id']).json()
    assert data['status']=='tasks_ready'
    assert len([t for t in data['tasks'] if t['kind']=='task'])==2
    with store.connect() as c:
        saved=c.execute(select(planning_jobs.c.payload).where(planning_jobs.c.intake_id==item['id'],
            planning_jobs.c.kind=='decomposition')).scalar()
        assert saved['approval_id']==decisions[0]


def test_declined_feature_does_not_dispatch_its_queued_decomposition(client, headers, store, installed):
    from test_intake import decision
    item,_=submit(client,headers)
    planner=ControlledFeaturePlanner()
    assert worker.tick(store,planning_adapter=planner)
    data=client.get('/api/intake/'+item['id']).json()
    assert client.post('/api/intake/'+item['id']+'/decisions',headers=headers,json=decision(data,action='decline')).status_code==200
    assert worker.tick(store,planning_adapter=planner)
    assert worker.tick(store,planning_adapter=planner)
    data=client.get('/api/intake/'+item['id']).json()
    assert data['status']=='declined' and not data['tasks'] and len(planner.calls)==1


def test_storage_wait_has_no_provider_claim_and_explains_the_hold(client, headers, store, installed, monkeypatch):
    from workbench import everyday
    item,_=submit(client,headers)
    monkeypatch.setattr(everyday,'measure',lambda policy:(1<<20,8<<30,0))
    planner=ControlledFeaturePlanner()
    assert not worker.tick(store,planning_adapter=planner)
    assert not planner.calls
    data=client.get('/api/features/'+item['id']).json()
    assert data['state']=='held' and not data['active'] and not data['needs_owner']
    assert data['message'].startswith('Waiting for verified source')
    with store.connect() as c:
        assert not c.execute(select(model_attempts).where(model_attempts.c.task_id==item['id'])).first()


def test_disabling_planning_never_dispatches_a_saved_feature(client, headers, store, installed, monkeypatch):
    from workbench.planning_provider import LocalProvider
    item,_=submit(client,headers)
    monkeypatch.setenv('WORKBENCH_PLANNER','unavailable')
    monkeypatch.setattr(LocalProvider,'generate',lambda *a,**k:pytest.fail('Disabled feature planning contacted a provider'))
    assert not worker.tick(store)
    with store.connect() as c:
        assert c.execute(select(planning_jobs.c.status).where(planning_jobs.c.intake_id==item['id'])).scalar()=='queued'
        assert not c.execute(select(model_attempts).where(model_attempts.c.task_id==item['id'])).first()
