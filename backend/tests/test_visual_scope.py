"""WB-16: deterministic authority, stale decisions and conservative legacy migration."""
import json
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import select

from workbench import intake, planning_worker
from workbench.db import (engine_for, intakes, proposal_artifacts, proposal_decisions,
                          planning_jobs, backlog_items, intake_scopes, write_transaction)
from workbench.planning_provider import DemoProvider
from test_intake import submit, get, decision, decide, artifact, research, png
from workbench.schemas import IntakeInput


class Classified(DemoProvider):
    def __init__(self, visual):
        self.visual = visual

    def generate(self, packet, schema):
        result, model, basis = super().generate(packet, schema)
        result['proposal']['visual'] = self.visual
        return result, model, basis


class NeverCalled:
    def generate(self, *_):
        pytest.fail('Invalid approval must be rejected before invoking a planner')


def proposal(client, headers, store, human='visual', model='nonvisual'):
    data, _ = submit(client, headers, visual_requirement=human)
    planning_worker.process_one(store, Classified(model))
    return get(client, data['id'])


def scope_change(client, headers, data, visual='visual', stage='implementation'):
    p = data['proposals'][0]
    body = dict(command_id=str(uuid4()), proposal_id=p['id'], binding=p['binding'],
                visual_requirement=visual, stage=stage, note='Human review confirms the scope of this revision.')
    response = client.post('/api/intake/'+data['id']+'/scope', headers=headers, json=body)
    assert response.status_code == 200, response.text
    return response.json(), body


def attach_both(client, headers, data):
    for role in ['mockup', 'game_context']:
        response = client.post('/api/intake/'+data['id']+'/artifacts', headers=headers, json=artifact(data, role))
        assert response.status_code == 200, response.text
        data = response.json()
    return data


@pytest.mark.parametrize('human,model,conflict', [
    ('visual','nonvisual',True), ('unclassified','nonvisual',False),
    ('nonvisual','visual',True), ('nonvisual','unknown',True), ('visual','unknown',False),
])
def test_combined_authority_and_direct_api_blockers(client, headers, store, human, model, conflict):
    data = proposal(client, headers, store, human, model)
    p = data['proposals'][0]
    assert p['required_artifacts'] == ['mockup','game_context']
    assert len(p['approval_blockers']) == 2
    assert bool(p['scope_conflicts']) == conflict
    assert decide(client, headers, data).status_code == 422
    data = attach_both(client, headers, data)
    assert decide(client, headers, data).status_code == 200
    planning_worker.apply_one(store)
    assert get(client, data['id'])['decisions'][0]['status'] == 'applied'


def test_default_not_sure_does_not_guess_natural_language_scope(client, headers, store):
    data, _ = submit(client, headers, description='Do not draw anything; definitely nonvisual; no interface.')
    planning_worker.process_one(store, Classified('nonvisual'))
    data = get(client, data['id'])
    assert data['human_scope']['visual_requirement'] == 'unclassified'
    assert decide(client, headers, data).status_code == 422


def test_human_visual_survives_model_revisions(client, headers, store):
    data = proposal(client, headers, store)
    original = data['original']
    assert decide(client, headers, data, decision(data, 'changes', 'Refine without removing the visual requirement.')).status_code == 200
    planning_worker.apply_one(store)
    planning_worker.process_one(store, Classified('nonvisual'))
    data = get(client, data['id'])
    assert data['revision'] == 2 and data['human_scope']['revision'] == 1
    assert data['original'] == original
    assert all(p['scope_revision'] == 1 for p in data['proposals'])
    assert decide(client, headers, data).status_code == 422


def test_scope_change_invalidates_api_and_queued_worker_decision_and_is_idempotent(client, headers, store):
    data = proposal(client, headers, store, 'nonvisual','nonvisual')
    body = decision(data)
    assert decide(client, headers, data, body).status_code == 200
    updated, change = scope_change(client, headers, data)
    assert updated['revision'] == 2 and updated['human_scope']['revision'] == 2
    url = '/api/intake/'+data['id']+'/scope'
    assert client.post(url, headers=headers, json=change).json() == updated
    assert client.post(url, headers=headers, json={**change,'note':'different'}).status_code == 409
    assert decide(client, headers, data, {**body,'command_id':str(uuid4())}).status_code == 409
    assert decide(client, headers, data, body).json()['status'] == 'pending'  # saved retry, no new authority
    with pytest.raises(RuntimeError):
        planning_worker.apply_one(store, fail_before_commit=True)
    assert get(client, data['id'])['decisions'][0]['status'] == 'pending'
    assert planning_worker.apply_one(store)
    updated = get(client, data['id'])
    assert updated['decisions'][0]['status'] == 'stale'
    assert updated['decisions'][0]['payload'] == body
    assert updated['decisions'][0]['scope_review_required']
    assert not updated['tasks'] and len(updated['scope_history']) == 2
    assert not planning_worker.apply_one(store)
    assert not planning_worker.process_one(store, NeverCalled())


def test_api_must_bind_scope_revision_even_with_current_hash(client, headers, store):
    data = proposal(client, headers, store, 'nonvisual','nonvisual')
    body = decision(data)
    for value in [None, 0, 200]:
        assert decide(client, headers, data, {**body,'scope_revision':value}).status_code == 409


def test_artifact_changes_invalidate_api_and_worker_binding(client, headers, store):
    data = attach_both(client, headers, proposal(client, headers, store))
    old = decision(data)
    # A third evidence attachment changes even a complete artifact set.
    data = client.post('/api/intake/'+data['id']+'/artifacts', headers=headers,
                       json=artifact(data, 'evidence', b'Bounded extra context', 'text/plain')).json()
    assert decide(client, headers, data, old).status_code == 409
    fresh = decision(data)
    assert decide(client, headers, data, fresh).status_code == 200
    with write_transaction(store) as c:
        # Simulate out-of-band relevant artifact changes after enqueue. Worker must revalidate.
        c.execute(proposal_artifacts.update().where(proposal_artifacts.c.proposal_id == fresh['proposal_id']).values(sha256='0'*64))
    planning_worker.apply_one(store)
    assert get(client, data['id'])['decisions'][0]['status'] == 'stale'
    assert not planning_worker.process_one(store, NeverCalled())


def test_decomposition_rechecks_scope_before_adapter_and_retains_applied_audit(client, headers, store):
    data = proposal(client, headers, store, 'nonvisual','nonvisual')
    approval = decision(data)
    decide(client, headers, data, approval)
    planning_worker.apply_one(store)
    applied = get(client, data['id'])['decisions'][0]
    data, _ = scope_change(client, headers, get(client, data['id']))
    assert not planning_worker.process_one(store, NeverCalled())
    data = get(client, data['id'])
    assert data['decisions'][0]['status'] == applied['status'] == 'applied'
    assert data['decisions'][0]['payload'] == applied['payload']
    assert data['decisions'][0]['scope_review_required'] and not data['tasks']
    failed = next(j for j in data['jobs'] if j['kind'] == 'decomposition')
    assert failed['status'] == 'failed'
    assert client.post('/api/intake/'+data['id']+'/retry', headers=headers,json={'job_id':failed['id']}).status_code == 409


def test_scope_change_during_decomposition_discards_result(client, headers, store):
    data = research(client, headers, proposal(client, headers, store))
    decide(client, headers, data)
    planning_worker.apply_one(store)

    class ChangeDuringWork(DemoProvider):
        def generate(self, packet, schema):
            result = super().generate(packet, schema)
            scope_change(client, headers, get(client, data['id']), stage='research')
            return result
    planning_worker.process_one(store, ChangeDuringWork())
    data = get(client, data['id'])
    assert data['status'] == 'completed' and not data['tasks']
    assert data['jobs'][-1]['status'] == 'failed'


def test_scope_change_during_planning_cannot_overwrite_current_review(client, headers, store):
    data = proposal(client, headers, store)
    decide(client, headers, data, decision(data, 'changes', 'Refine the proposal.'))
    planning_worker.apply_one(store)

    class ChangeDuringWork(Classified):
        def generate(self, packet, schema):
            result = super().generate(packet, schema)
            scope_change(client, headers, get(client, data['id']))
            return result
    planning_worker.process_one(store, ChangeDuringWork('nonvisual'))
    data = get(client, data['id'])
    assert data['status'] == 'completed' and data['revision'] == 2
    assert data['proposals'][0]['scope_revision'] == 2
    assert data['jobs'][-1]['status'] == 'failed'


def test_research_approval_preserves_visual_obligation_and_no_execution_permission(client, headers, store):
    data = research(client, headers, proposal(client, headers, store))
    assert data['human_scope']['visual_requirement'] == 'visual'
    p = data['proposals'][0]
    assert p['research_only'] and not p['required_artifacts'] and not p['approval_blockers']
    assert 'All addon implementation, execution permission and publication.' in p['content']['exclusions']
    decide(client, headers, data)
    planning_worker.apply_one(store)
    planning_worker.process_one(store, DemoProvider())
    data = get(client, data['id'])
    assert data['tasks'] and not any(t['scope_review_required'] for t in data['tasks'])
    assert data['priority']['execution'].startswith('Not authorized')
    before = data['decisions'][0]
    future, _ = scope_change(client, headers, data, stage='implementation')
    assert future['human_scope']['visual_requirement'] == 'visual'
    assert future['proposals'][0]['required_artifacts'] == ['mockup','game_context']
    assert decide(client, headers, future).status_code == 422
    assert future['decisions'][0]['status'] == before['status'] == 'applied'
    assert all(t['scope_review_required'] for t in future['tasks'])
    root = next(t for t in future['tasks'] if not t['parent'])
    record = client.get('/api/records/fixture/'+root['id']).json()['record']
    assert record['scope_review_required'] and 'Execution held' in record['state']
    assert record['publication'] == 'Not published'
    # Changing the human review stage does not relabel a research proposal as implementation.
    assert future['proposals'][0]['content']['delivery'] == 'research'
    assert future['proposals'][0]['scope_conflicts']
    assert decide(client, headers, future, decision(future, 'changes', 'Return an implementation proposal for a separate review.')).status_code == 200
    planning_worker.apply_one(store)
    planning_worker.process_one(store, Classified('nonvisual'))
    implementation = get(client, data['id'])
    assert implementation['human_scope']['visual_requirement'] == 'visual'
    assert implementation['proposals'][0]['content']['delivery'] == 'implementation'
    assert implementation['proposals'][0]['required_artifacts'] == ['mockup','game_context']
    assert decide(client, headers, implementation).status_code == 422


def test_model_research_cannot_grant_its_own_artifact_exemption(client, headers, store):
    class Research(Classified):
        def generate(self, packet, schema):
            result, model, basis = super().generate(packet, schema)
            result['proposal'].update(delivery='research', exclusions=[intake.RESEARCH_EXCLUSION])
            return result, model, basis
    data, _ = submit(client, headers, visual_requirement='visual')
    planning_worker.process_one(store, Research('nonvisual'))
    data = get(client, data['id'])
    assert not data['proposals'][0]['research_only']
    assert decide(client, headers, data).status_code == 422


@pytest.mark.parametrize('status', ['pending', 'applied'])
def test_legacy_model_nonvisual_migration_preserves_audit_and_blocks_authority(tmp_path, status):
    engine = engine_for(tmp_path/'legacy.sqlite3')
    config = Config(str(Path(__file__).resolve().parents[2]/'alembic.ini'))
    identity, pid, did = (str(uuid4()) for _ in range(3))
    original = dict(request_id=identity,kind='feature',title='Legacy request',description='Keep original text.',lua_error='')
    old_payload = dict(command_id=did,proposal_id=pid,binding='f'*64,action='approve',note='Historical human decision; model called it nonvisual.')
    old_snapshot = {'content':{'visual':'nonvisual'},'binding':'f'*64}
    with engine.connect() as c:
        config.attributes['connection'] = c
        command.upgrade(config, '0003')
        c.exec_driver_sql("INSERT INTO intakes VALUES (?,?,?,1,?,NULL)", (identity,json.dumps(original),'tasks_ready' if status=='applied' else 'completed','2026-11-01T00:00:00+00:00'))
        result, _, _ = Classified('nonvisual').generate({'original':original,'related':[],'evidence':[]}, None)
        c.exec_driver_sql("INSERT INTO proposals VALUES (?,?,1,?,?,?,?)", (pid,identity,json.dumps(result['proposal']),'old-model','local','2026-11-01T00:00:00+00:00'))
        c.exec_driver_sql("INSERT INTO proposal_decisions VALUES (?,?,?,?,?,?,?,?,?,?)", (did,identity,pid,'f'*64,json.dumps(old_payload),json.dumps(old_snapshot),status,'Historical receipt','2026-11-01T00:00:00+00:00',None))
        c.exec_driver_sql("INSERT INTO backlog_items VALUES (?,?,?,NULL,'task',?,?,?, ?,?)", ('legacy-task',identity,pid,'Retained legacy task','Retain audit',json.dumps(['Review']),'old-model','2026-11-01T00:00:00+00:00'))
        c.exec_driver_sql("INSERT INTO proposal_artifacts VALUES (?,?, 'mockup', ?, ?, 'image/png', ?, ?)", (str(uuid4()),pid,'Retained original attachment','a'*64,png(),'2026-11-01T00:00:00+00:00'))
        c.exec_driver_sql("INSERT INTO planning_jobs VALUES (?,?, 'decomposition',1,'queued',0,NULL,NULL,?,?,NULL,?,NULL)", (str(uuid4()),identity,json.dumps({'proposal_id':pid,'approval_id':did,'binding':'f'*64}),'Legacy queue','2026-11-01T00:00:00+00:00'))
        c.commit()
        command.upgrade(config, 'head')
        c.commit()
    with engine.connect() as c:
        scope = c.execute(select(intake_scopes)).mappings().one()
        assert scope['visual_requirement'] == 'unclassified' and scope['origin'] == 'legacy'
        saved = c.execute(select(proposal_decisions)).mappings().one()
        assert saved['payload'] == old_payload and saved['snapshot'] == old_snapshot and saved['status'] == status
        assert saved['scope_revision'] is None and saved['scope_review_required']
        assert c.execute(select(intakes.c.payload)).scalar_one() == original
    data = intake.view(engine, identity)
    assert data['tasks'][0]['scope_review_required']
    assert data['proposals'][0]['required_artifacts'] == ['mockup','game_context']
    if status == 'pending':
        assert planning_worker.apply_one(engine)
        assert intake.view(engine,identity)['decisions'][0]['status'] == 'stale'
        assert not planning_worker.apply_one(engine)
    else:
        assert data['status'] == 'scope_review_required'
        assert not planning_worker.apply_one(engine)
    assert not planning_worker.process_one(engine, NeverCalled())
    assert intake.view(engine,identity)['jobs'][0]['status'] == 'failed'
    assert intake.submit(engine, IntakeInput(**original))['id'] == identity  # unchanged original replay
    # Upgrading again is safe and does not remove any historical record.
    with engine.connect() as c:
        config.attributes['connection'] = c
        command.upgrade(config, 'head')
        c.commit()
        assert len(c.execute(select(backlog_items)).all()) == 1
        assert len(c.execute(select(proposal_decisions)).all()) == 1
        assert len(c.execute(select(planning_jobs)).all()) == 1
        retained = c.execute(select(proposal_artifacts)).mappings().one()
        assert retained['data'] == png() and retained['sha256'] == 'a'*64
    engine.dispose()


@pytest.mark.parametrize('during_work', [False, True])
def test_decomposition_rechecks_artifacts_before_and_after_work(client, headers, store, during_work):
    data = attach_both(client, headers, proposal(client, headers, store))
    decide(client, headers, data)
    planning_worker.apply_one(store)

    def alter():
        with write_transaction(store) as c:
            c.execute(proposal_artifacts.update().where(proposal_artifacts.c.proposal_id == data['proposals'][0]['id']).values(sha256='b'*64))

    class AlterDuringWork(DemoProvider):
        def generate(self, packet, schema):
            result = super().generate(packet, schema)
            alter()
            return result
    if not during_work:
        alter()
    planning_worker.process_one(store, AlterDuringWork() if during_work else NeverCalled())
    updated = get(client, data['id'])
    assert not updated['tasks']
    assert updated['decisions'][0]['status'] == 'applied'
    assert updated['jobs'][-1]['status'] == 'failed'


def test_generated_tasks_revalidate_later_artifact_changes_without_erasing_audit(client, headers, store):
    data = attach_both(client, headers, research(client, headers, proposal(client, headers, store)))
    decide(client, headers, data)
    planning_worker.apply_one(store)
    planning_worker.process_one(store, DemoProvider())
    data = get(client, data['id'])
    assert data['tasks'] and not any(t['scope_review_required'] for t in data['tasks'])
    with write_transaction(store) as c:
        snapshot = c.execute(select(proposal_decisions.c.snapshot)).scalar_one()
        c.execute(proposal_artifacts.update().values(label='Changed relevant artifact'))
    updated = get(client, data['id'])
    assert updated['decisions'][0]['status'] == 'applied' and updated['decisions'][0]['scope_review_required']
    assert all(t['scope_review_required'] for t in updated['tasks'])
    with store.connect() as c:
        assert c.execute(select(proposal_decisions.c.snapshot)).scalar_one() == snapshot
    root = next(t for t in updated['tasks'] if not t['parent'])
    record = client.get('/api/records/fixture/'+root['id']).json()['record']
    assert record['scope_review_required'] and record['publication'] == 'Not published'


def test_effective_required_artifact_set_is_enforced_in_api_and_worker(client, headers, store):
    data = attach_both(client, headers, proposal(client, headers, store))
    body = decision(data)
    assert decide(client, headers, data, {**body,'required_artifacts':[]}).status_code == 422
    assert decide(client, headers, data, body).status_code == 200
    with write_transaction(store) as c:
        stored = c.execute(select(proposal_decisions)).mappings().one()
        assert stored['scope_revision'] == data['human_scope']['revision']
        assert stored['required_artifacts'] == ['mockup','game_context']
        c.execute(proposal_decisions.update().values(required_artifacts=[]))
    planning_worker.apply_one(store)
    assert get(client, data['id'])['decisions'][0]['status'] == 'stale'
    assert not planning_worker.process_one(store, NeverCalled())
