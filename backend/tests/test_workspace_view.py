"""Everyday read model: truthful states, decisions, filtering and no authority changes."""
from uuid import uuid4

import pytest
from sqlalchemy import select

from workbench import planning_worker
from workbench.db import intakes, planning_jobs, proposal_decisions, write_transaction
from workbench.planning_provider import DemoProvider
from test_intake import submit, planned, decision, decide


def index(client, **params):
    response = client.get('/api/workspace', params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_counts_search_pagination_and_read_only_state(client, headers, store):
    ids = [submit(client, headers, kind='issue', title=title)[0]['id']
           for title in ['A queued bug', 'A failed bug', 'Another queued bug']]
    with write_transaction(store) as c:
        c.execute(intakes.update().where(intakes.c.id == ids[1]).values(status='failed'))
    with store.connect() as c:
        before = [dict(r) for r in c.execute(select(planning_jobs)).mappings()]
    page = index(client, search='bug', page_size=1)
    assert page['total'] == page['all_total'] == page['counts']['all'] == 3
    assert len(page['items']) == 1 and page['counts']['queued'] == 2
    assert page['counts']['active'] == page['counts']['needs_you'] == 0
    assert index(client, search='ANOTHER')['items'][0]['id'] == ids[2]
    failed = index(client, state='failed')
    assert failed['total'] == 1 and failed['items'][0]['id'] == ids[1]
    assert failed['counts'] == page['counts']  # Tabs count the same search scope.
    assert len({index(client, page=n, page_size=1)['items'][0]['id'] for n in [1, 2, 3]}) == 3
    assert client.get('/api/workspace?state=invented').status_code == 422
    assert client.get('/api/workspace?page=0').status_code == 422
    with store.connect() as c:
        assert [dict(r) for r in c.execute(select(planning_jobs)).mappings()] == before


def test_pending_decision_is_recorded_not_unanswered_or_running(client, headers, store):
    data = planned(client, headers, store)
    assert index(client)['items'][0]['state'] == 'needs_approval'
    assert decide(client, headers, data, decision(data, 'changes', 'Preserve keyboard use.')).status_code == 200
    row = index(client)['items'][0]
    assert row['state'] == 'decision_pending' and not row['needs_you'] and not row['active']
    assert 'saved' in row['message']
    assert not index(client)['highlights']['needs_you']
    with store.connect() as c:
        assert c.execute(select(proposal_decisions.c.status)).scalar() == 'pending'


def test_finished_investigation_is_findings_not_a_patch(client, headers, store):
    submit(client, headers, kind='issue')
    planning_worker.process_one(store, DemoProvider())
    row = index(client, state='results')['items'][0]
    assert row['state'] == 'completed' and row['result_kind'] == 'findings' and row['result_id'] is None
    assert not row['active']


def test_investigating_without_live_claim_never_looks_active(client, headers, store):
    data, _ = submit(client, headers, kind='issue')
    with write_transaction(store) as c:
        c.execute(intakes.update().where(intakes.c.id == data['id']).values(status='investigating'))
    assert index(client)['items'][0]['state'] == 'queued'
    with write_transaction(store) as c:
        c.execute(planning_jobs.update().values(status='running', lease='old', lease_until='2000-01-01T00:00:00+00:00'))
    assert index(client)['items'][0]['state'] == 'interrupted'
    assert not index(client)['items'][0]['active']


@pytest.mark.parametrize('kind', ['feature', 'inspection', 'simulation', 'data', 'other'])
def test_future_work_saves_durably_without_model_or_execution_authority(client, headers, store, kind):
    data, body = submit(client, headers, requested_work=kind)
    assert data['status'] == 'unavailable' and not data['jobs'] and not data['decisions']
    assert client.post('/api/intake', headers=headers, json=body).json() == data
    row = index(client)['items'][0]
    assert row['kind'] == kind and row['state'] == 'unavailable'
    assert not row['active'] and not row['needs_you']
    assert not planning_worker.process_one(store, DemoProvider())
    assert client.post('/api/intake', headers=headers, json={**body, 'requested_work':'other' if kind != 'other' else 'feature'}).status_code == 409


@pytest.mark.parametrize('extra', [dict(kind='issue'), dict(local_planning='qwen-once'),
    dict(regression_tests={'acceptance':['Preserve behavior'], 'test_paths':[]})])
def test_future_work_cannot_smuggle_execution_or_provider_permission(client, headers, extra):
    body = dict(request_id=str(uuid4()), kind='feature', title='Future task', description='Keep this idea.',
                requested_work='inspection', visual_requirement='nonvisual')
    body.update(extra)
    assert client.post('/api/intake', headers=headers, json=body).status_code == 422
    assert index(client)['total'] == 0

@pytest.mark.parametrize('stage,verification,candidate,expected,result', [
    ('running', None, None, 'working', False),
    ('preparing', None, None, 'preparing', False),
    ('checking', None, 'partial-candidate', 'checking', False),
    ('checked', 'passed', 'checked-candidate', 'completed', True),
    ('checked', 'failed', 'failed-candidate', 'failed', True),
    ('recovering', None, None, 'interrupted', False),
])
def test_execution_projection_does_not_promote_partial_or_unchecked_output(client, headers, store, monkeypatch,
        stage, verification, candidate, expected, result):
    from test_intake import research
    from workbench.db import backlog_items
    from workbench import workspace_view
    data = research(client, headers, planned(client, headers, store))
    assert decide(client, headers, data).status_code == 200
    planning_worker.apply_one(store)
    planning_worker.process_one(store, DemoProvider())
    with write_transaction(store) as c:
        original = dict(c.execute(select(intakes)).mappings().one())
        c.execute(intakes.update().values(payload={**original['payload'], 'regression_tests':{'acceptance':['Keep behavior.']}}))
        # A single synthetic implementation task solely for read-model state checks.
        tasks = list(c.execute(select(backlog_items).where(backlog_items.c.kind=='task')).mappings())
        for task in tasks[1:]:
            c.execute(backlog_items.update().where(backlog_items.c.id==task['id']).values(kind='finding'))
    monkeypatch.setattr(workspace_view.task_execution, 'context', lambda *a:dict(
        stage=stage, run_budget=None, current_approval=True, session=None,
        runs=[dict(id=str(uuid4()),current_task=True,status=stage,verification=verification,candidate_id=candidate)]))
    monkeypatch.setattr(workspace_view, 'later_review', lambda *a:None)
    row=index(client)['items'][0]
    assert row['state']==expected
    assert bool(row['result_id'])==result

def test_expired_execution_permission_is_visible_as_attention_without_reviving_it(client, headers, store, monkeypatch):
    from test_intake import research
    from workbench.db import backlog_items
    from workbench import workspace_view
    data=research(client, headers, planned(client, headers, store))
    decide(client, headers, data)
    planning_worker.apply_one(store)
    planning_worker.process_one(store, DemoProvider())
    with write_transaction(store) as c:
        original=dict(c.execute(select(intakes)).mappings().one())
        c.execute(intakes.update().values(payload={**original['payload'],'regression_tests':{'acceptance':['Keep behavior.']}}))
        tasks=list(c.execute(select(backlog_items).where(backlog_items.c.kind=='task')).mappings())
        for task in tasks[1:]:
            c.execute(backlog_items.update().where(backlog_items.c.id==task['id']).values(kind='finding'))
    monkeypatch.setattr(workspace_view.task_execution,'context',lambda *a:dict(
        stage='held',run_budget={'current':False},current_approval=True,session=None,runs=[]))
    row=index(client,state='needs_you')['items'][0]
    assert row['state']=='held' and row['needs_you'] and not row['active']
    assert 'expired' in row['message'] and index(client)['counts']['needs_you']==1
