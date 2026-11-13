"""Settled correction through actual API/job/ledger ownership; no real provider."""
import copy
import json
from uuid import uuid4

import pytest
from sqlalchemy import select

from workbench import worker, everyday
from workbench.db import planning_jobs, model_attempts, intakes, write_transaction
from workbench.schemas import PlanningOutput
from test_feature_planning import ControlledFeaturePlanner, submit
from test_everyday import installed as installed


def failed(client, headers, store):
    def incomplete(value, schema):
        if schema is not PlanningOutput:
            value['tasks'] = value['tasks'][:1]
    planner = ControlledFeaturePlanner(incomplete)
    planner.diagnostics.update(accounting_state='complete', usage={'status':'complete', 'total_tokens':200})
    generate = planner.generate
    def received(packet, schema, expected_model=None):
        result = generate(packet, schema, expected_model)
        usage = dict(source='controlled provider', units='tokens', status='complete', requests_observed=1,
            values=dict(prompt_tokens=100, completion_tokens=100, total_tokens=200))
        planner.diagnostics.update(usage=usage, actual_model=expected_model)
        planner.response_evidence.received(b'{}')
        planner.response_evidence.envelope(dict(id='chatcmpl-'+str(uuid4())),
            dict(generation_state='finished', finish_reason='stop',model_matches=1,
                 accounting_state='complete', usage=usage))
        return result
    planner.generate = received
    data, body = submit(client, headers)
    assert worker.tick(store, planning_adapter=planner)
    assert worker.tick(store, planning_adapter=planner)
    view = client.get('/api/features/'+data['id']).json()
    assert view['state'] == 'failed' and view['total_steps'] == 0
    assert view['correction']
    return view, body, planner


def command(view):
    return dict(command_id=str(uuid4()), binding=view['correction']['binding'])


def test_finished_feature_response_with_unavailable_ledger_usage_is_recoverable(
    client, headers, store, installed,
):
    view, _, _ = failed(client, headers, store)
    with write_transaction(store) as c:
        job = c.execute(select(planning_jobs).where(planning_jobs.c.id == view['correction']['job_id'])).mappings().one()
        attempt = c.execute(select(model_attempts).where(model_attempts.c.job_id == job['id'])).mappings().one()
        c.execute(model_attempts.update().where(model_attempts.c.id == attempt['id']).values(usage=None))
    current = client.get('/api/features/'+view['id']).json()
    assert current['correction']
    assert client.post('/api/features/'+view['id']+'/correct-steps', headers=headers, json=command(current)).status_code == 200
    with store.connect() as c:
        assert c.execute(select(model_attempts.c.usage).where(model_attempts.c.id == attempt['id'])).scalar_one() is None


def test_same_plan_correction_retains_rejected_response_and_claims(
    client, headers, store, installed,
):
    view, body, planner = failed(client, headers, store)
    original_plan = copy.deepcopy(view['intake']['proposals'])
    with store.connect() as c:
        job = c.execute(select(planning_jobs).where(planning_jobs.c.id == view['correction']['job_id'])).mappings().one()
        evidence = everyday.ROOT/'artifacts/runs'/('planning-evidence-'+job['payload']['response_evidence_id'])
        assert (evidence/'proposed-plan.json').is_file()
        assert len(json.loads((evidence/'proposed-plan.json').read_text())['tasks']) == 1
        original_receipt = c.execute(select(planning_jobs.c.payload).where(
            planning_jobs.c.intake_id == view['id'], planning_jobs.c.kind == 'planning')).scalar_one()['local_planning_receipt']
        old_ledger = [dict(v) for v in c.execute(select(model_attempts)).mappings()]
    saved = command(view)
    url = '/api/features/'+view['id']+'/correct-steps'
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(client.post, url, headers=headers, json=saved) for _ in range(2)]
        assert [f.result().status_code for f in futures] == [200, 200]
    assert client.post(url, headers=headers, json={**saved, 'binding':'f'*64}).status_code == 409
    assert client.post(url, headers=headers, json=command(view)).status_code == 409
    planner.mutate = None
    assert worker.tick(store, planning_adapter=planner)
    assert not worker.tick(store, planning_adapter=planner)
    assert len(planner.calls) == 3
    assert 'Missing actionable coverage' in planner.calls[-1]['correction']['previous_validation_failure']
    assert [x['index'] for x in planner.calls[-1]['indexed_scope']] == [0, 1]
    current = client.get('/api/features/'+view['id']).json()
    assert current['total_steps'] == 2 and current['completed_steps'] == 0
    assert current['intake']['proposals'] == original_plan
    assert current['intake']['original']['request_id'] == body['request_id']
    assert client.post(url, headers=headers, json=saved).status_code == 200
    with store.connect() as c:
        jobs = list(c.execute(select(planning_jobs).where(planning_jobs.c.intake_id == view['id'])).mappings())
        assert len(jobs) == 2
        job = next(j for j in jobs if j['kind'] == 'decomposition')
        assert job['attempts'] == 2 and job['status'] == 'completed'
        assert len(job['payload']['correction_history']) == 1
        assert len(job['payload']['correction_commands']) == 1
        assert next(j for j in jobs if j['kind']=='planning')['payload']['local_planning_receipt'] == original_receipt
        assert all(dict(c.execute(select(model_attempts).where(model_attempts.c.id == v['id'])).mappings().one()) == v for v in old_ledger)
    assert (evidence/'proposed-plan.json').is_file()


@pytest.mark.parametrize('fault', ['unknown', 'unfinished', 'wrong_scope', 'superseded', 'declined'])
def test_uncertain_changed_or_declined_feature_never_offers_or_replays_correction(
    client, headers, store, installed, fault,
):
    view, _, planner = failed(client, headers, store)
    with write_transaction(store) as c:
        job = c.execute(select(planning_jobs).where(planning_jobs.c.id == view['correction']['job_id'])).mappings().one()
        payload = copy.deepcopy(job['payload'])
        if fault == 'unknown':
            payload['diagnostics']['generation_state'] = 'started'
            evidence = everyday.ROOT/'artifacts/runs'/('planning-evidence-'+job['payload']['response_evidence_id'])/'events.jsonl'
            events = [json.loads(line) for line in evidence.read_text().splitlines()]
            evidence.write_text(''.join(json.dumps(e)+'\n' for e in events if e['phase'] != 'envelope'))
        if fault == 'wrong_scope':
            payload['scope_revision'] += 1
        c.execute(planning_jobs.update().where(planning_jobs.c.id == job['id']).values(payload=payload))
        if fault in {'unfinished','missing_usage'}:
            c.execute(model_attempts.update().where(model_attempts.c.job_id == job['id']).values(
                **({'finished_at':None} if fault=='unfinished' else {'usage':None})))
        if fault in {'superseded','declined'}:
            c.execute(intakes.update().where(intakes.c.id == view['id']).values(
                **({'revision':2} if fault=='superseded' else {'status':'declined'})))
    response = client.post('/api/features/'+view['id']+'/correct-steps', headers=headers, json=command(view))
    assert response.status_code == 409
    assert not client.get('/api/features/'+view['id']).json()['correction']
    assert len(planner.calls) == 2


def test_correction_expiry_never_changes_original_consent_or_dispatches(
    client, headers, store, installed,
):
    view, _, planner = failed(client, headers, store)
    assert client.post('/api/features/'+view['id']+'/correct-steps', headers=headers, json=command(view)).status_code == 200
    with write_transaction(store) as c:
        job = c.execute(select(planning_jobs).where(planning_jobs.c.id == view['correction']['job_id'])).mappings().one()
        payload = copy.deepcopy(job['payload'])
        payload['feature_recovery']['expires_at'] = '2000-01-01T00:00:00+00:00'
        c.execute(planning_jobs.update().where(planning_jobs.c.id == job['id']).values(payload=payload))
    assert worker.tick(store, planning_adapter=planner)
    assert len(planner.calls) == 2
    assert client.get('/api/features/'+view['id']).json()['state'] == 'failed'


def test_feature_correction_checks_source_before_renewing(
    client, headers, store, installed, monkeypatch,
):
    view, _, planner = failed(client, headers, store)
    def moved(*args):
        raise ValueError('source moved')
    monkeypatch.setattr('workbench.project_source.require_current', moved)
    response = client.post('/api/features/'+view['id']+'/correct-steps', headers=headers, json=command(view))
    assert response.status_code == 409
    assert len(planner.calls) == 2


def test_recorded_correction_replay_survives_later_source_change_without_dispatch(
    client, headers, store, installed, monkeypatch,
):
    view, _, planner = failed(client, headers, store)
    saved = command(view)
    url = '/api/features/'+view['id']+'/correct-steps'
    assert client.post(url, headers=headers, json=saved).status_code == 200
    def moved(*args):
        raise ValueError('source moved')
    monkeypatch.setattr('workbench.project_source.require_current', moved)
    assert client.post(url, headers=headers, json=saved).status_code == 200
    with store.connect() as c:
        job = c.execute(select(planning_jobs).where(planning_jobs.c.id == view['correction']['job_id'])).mappings().one()
        assert job['attempts'] == 1 and len(job['payload']['correction_commands']) == 1
    assert len(planner.calls) == 2


def test_archiving_failed_feature_withdraws_correction_permission(
    client, headers, store, installed,
):
    from workbench import task_cleanup
    view, _, planner = failed(client, headers, store)
    context = task_cleanup.context(store, view['id'])
    assert context['can_archive']
    task_cleanup.apply(store, task_cleanup.CleanupCommand.model_validate(dict(
        command_id=str(uuid4()), action='archive',
        targets=[dict(intake_id=view['id'], binding=context['binding'])])))
    assert not client.get('/api/features/'+view['id']).json()['correction']
    assert client.post('/api/features/'+view['id']+'/correct-steps', headers=headers, json=command(view)).status_code == 409
    assert len(planner.calls) == 2


def test_plan_change_during_source_check_cannot_queue_a_stale_correction(
    client, headers, store, installed, monkeypatch,
):
    view, _, planner = failed(client, headers, store)
    def changed(*args):
        with write_transaction(store) as c:
            c.execute(intakes.update().where(intakes.c.id == view['id']).values(revision=2))
    monkeypatch.setattr('workbench.project_source.require_current', changed)
    response = client.post('/api/features/'+view['id']+'/correct-steps', headers=headers, json=command(view))
    assert response.status_code == 409
    assert len(planner.calls) == 2
