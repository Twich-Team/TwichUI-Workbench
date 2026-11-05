from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3

from fastapi.testclient import TestClient
import pytest
from workbench.api import create_app
from workbench import catalog


def projection(tmp_path, *, age=0, count=3):
    path = tmp_path/'pilot.sqlite3'
    with sqlite3.connect(path) as db:
        db.executescript('''PRAGMA user_version=1;
            CREATE TABLE items(id TEXT PRIMARY KEY,kind TEXT,parent TEXT,owner_title TEXT,
                owner_objective TEXT,owner_updated_at TEXT,source_version TEXT,projection TEXT);
            CREATE TABLE observations(item TEXT PRIMARY KEY,observed_at TEXT);''')
        for index in range(count):
            id = f'item-{index:03}'
            at = (datetime.now(timezone.utc)-timedelta(seconds=age)).isoformat()
            body = dict(title=f'Sanitized task {index}',category='Needs you',in_progress=True,
                        outcome='accepted',verification='passed',publication='not_published',
                        private_prompt='DO_NOT_EXPOSE', artifact='/home/rich/private-secret',
                        attempts=[dict(outcome='failed',model='local',secret='DO_NOT_EXPOSE')],
                        updated_at='2026-11-01T00:00:00+00:00')
            db.execute('INSERT INTO items VALUES(?,?,?,?,?,?,?,?)',
                       (id,'request' if index==0 else 'task',None if index==0 else 'item-000',
                        None,None,None,'source-v1',json.dumps(body)))
            db.execute('INSERT INTO observations VALUES(?,?)',(id,at))
    return path


def test_fixture_hierarchy_details_and_literal_search(client):
    page = client.get('/api/backlog').json()
    assert page['total'] == 1
    assert client.get('/api/backlog?view=all').json()['total'] == 2
    assert page['freshness']['state'] == 'fixture'
    children = client.get('/api/backlog?parent=guide-review').json()
    assert children['total'] == 2
    detail = client.get('/api/records/fixture/cozy-step-review').json()
    assert [r['id'] for r in detail['ancestors']] == ['guide-request','guide-review']
    assert detail['record']['review_task_id'] == 'cozy-step-review'
    search = client.get('/api/backlog?q=Keep%20your%20place').json()
    assert search['filtered'] and [r['id'] for r in search['items'] if not r['context']] == ['cozy-step-review']
    assert client.get('/api/backlog?q=%25').json()['total'] == 0
    assert client.get('/api/backlog?page=0').status_code == 422
    assert client.get('/api/backlog?source=unknown').status_code == 422


def test_saved_pending_count_history_and_application_remain_distinct(client, headers, store):
    from uuid import uuid4
    from workbench.worker import process_one
    task = client.get('/api/tasks/cozy-step-review').json()
    assert client.get('/api/work').json()['needs_you'] == 1
    body = dict(command_id=str(uuid4()),version=task['version'],notes='',
                choices={c['id']:'confirm' for c in task['evidence']['claims']})
    client.post('/api/tasks/cozy-step-review/decisions',headers=headers,json=body)
    work = client.get('/api/work').json()
    assert work['needs_you'] == 0 and work['waiting_application'] == 1
    history = client.get('/api/history').json()
    assert history['items'][0]['title'] == 'Review decision saved'
    process_one(store)
    history = client.get('/api/history').json()
    assert any(e['title']=='Review applied' for e in history['items'])
    assert any(e['title']=='Review decision saved' for e in history['items'])
    assert client.get('/api/records/fixture/cozy-step-review').json()['record']['publication'] == 'not_published'


def test_live_projection_read_only_sanitized_and_paginated(store, tmp_path):
    path = projection(tmp_path, count=30)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    with TestClient(create_app(store,pilot_path=path)) as client:
        overview = client.get('/api/work?source=pilot').json()
        assert overview['freshness']['state'] == 'fresh'
        assert overview['needs_you'] == 30
        page = client.get('/api/backlog?source=pilot&parent=item-000&page=2').json()
        assert page['total']==29 and len(page['items'])==12
        detail = client.get('/api/records/pilot/item-001').json()
        assert detail['record']['id']=='item-001' and detail['record']['review_task_id'] is None
        assert detail['record']['attempts']==[dict(outcome='failed',model='local')]
        assert 'DO_NOT_EXPOSE' not in json.dumps(detail)
        assert 'private-secret' not in json.dumps(detail)
        assert client.post('/api/records/pilot/item-001',json={}).status_code in {403,405}
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_stale_projection_never_claims_current_work_or_decisions(store,tmp_path):
    path = projection(tmp_path,age=1000)
    with TestClient(create_app(store,pilot_path=path)) as client:
        work = client.get('/api/work?source=pilot').json()
        assert work['freshness']['state']=='stale'
        assert work['current']==[] and work['needs_you']==0
        assert client.get('/api/backlog?source=pilot').json()['total']==1


@pytest.mark.parametrize('mode',['disabled','missing','symlink','schema','malformed'])
def test_unavailable_is_explicit_and_never_creates_or_modifies_source(store,tmp_path,mode):
    path = tmp_path/'missing.sqlite3'
    if mode=='symlink':
        path.symlink_to(projection(tmp_path))
    if mode in {'schema','malformed'}:
        path=projection(tmp_path)
        with sqlite3.connect(path) as db:
            db.execute('PRAGMA user_version=999' if mode=='schema' else "UPDATE items SET projection='bad'")
    with TestClient(create_app(store,pilot_path=path if mode!='disabled' else '')) as client:
        work=client.get('/api/work?source=pilot').json()
        assert work['freshness']['state']=='unavailable'
        assert work['needs_you']==0 and work['current']==[]
        assert client.get('/api/tasks').status_code==200
    if mode in {'missing','disabled'}:
        assert not path.exists()


def test_cycle_dangling_parent_unknown_times_and_read_budget(store,tmp_path):
    path=projection(tmp_path,count=503)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE items SET parent='item-001' WHERE id='item-000'")
        db.execute("UPDATE items SET owner_updated_at='2026-11-02', projection=? WHERE id='item-002'",
                   (json.dumps(dict(title='Unknown date',updated_at='2026-11-02T10:00:00')),))
    meta,rows=catalog.snapshot(store,'pilot',path)
    assert meta['partial'] and len(rows)==500
    assert next(r for r in rows if r['id']=='item-000')['parent'] is None
    assert next(r for r in rows if r['id']=='item-002')['updated_at'] is None
    assert catalog.instant('2026-11-02') is None
    assert catalog.clean('/home/rich/private token=secret-value') == '[private path] [redacted]'


def test_busy_projection_is_bounded_and_does_not_block_fixture_reviews(store,tmp_path):
    path=projection(tmp_path)
    with sqlite3.connect(path) as lock:
        lock.execute('BEGIN EXCLUSIVE')
        with TestClient(create_app(store,pilot_path=path)) as client:
            assert client.get('/api/work?source=pilot').json()['freshness']['state']=='unavailable'
            assert client.get('/api/work').json()['needs_you']==1


def test_one_fresh_record_does_not_make_older_records_current(store,tmp_path):
    path=projection(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM observations WHERE item='item-001'")
    with TestClient(create_app(store,pilot_path=path)) as client:
        work=client.get('/api/work?source=pilot').json()
        assert work['freshness']['state']=='fresh' and work['needs_you']==2
        detail=client.get('/api/records/pilot/item-001').json()
        assert detail['freshness']['state']=='stale'
        assert not detail['record']['currently_observed']


def test_fresh_waiting_reasons_are_visible_without_invented_start_time(store,tmp_path):
    path=projection(tmp_path,count=1)
    with sqlite3.connect(path) as db:
        db.execute('UPDATE items SET projection=?',(json.dumps(dict(title='Waiting task',
                    category='Waiting automatically',reason='Recorded capacity gate is full.',in_progress=False)),))
    with TestClient(create_app(store,pilot_path=path)) as client:
        work=client.get('/api/work?source=pilot').json()
        assert work['current']==[]
        assert work['waiting'][0]['summary']=='Recorded capacity gate is full.'
        assert work['waiting'][0]['state']=='Waiting automatically'
        assert work['waiting'][0]['reason']=='Recorded capacity gate is full.'


def test_projection_connection_is_closed_after_read(store,tmp_path,monkeypatch):
    path=projection(tmp_path)
    real_connect=sqlite3.connect
    connections=[]
    def capture(*args,**kwargs):
        connection=real_connect(*args,**kwargs)
        connections.append(connection)
        return connection
    monkeypatch.setattr(catalog.sqlite3,'connect',capture)
    catalog.snapshot(store,'pilot',path)
    assert len(connections)==1
    with pytest.raises(sqlite3.ProgrammingError,match='closed'):
        connections[0].execute('SELECT 1')


def test_selectable_demo_real_api_isolation_and_pages(client):
    mixed = client.get('/api/backlog?demo=mixed&view=all').json()
    assert mixed['freshness']['state'] == 'fixture'
    assert mixed['total'] == 16
    assert len(mixed['items']) == 12
    assert len(client.get('/api/backlog?demo=mixed&view=all&page=2').json()['items']) == 4
    children = client.get('/api/backlog?demo=hierarchy&parent=demo-00&view=all').json()
    assert children['total'] == 14
    detail = client.get('/api/records/fixture/demo-child-17?demo=mixed').json()
    assert [r['id'] for r in detail['ancestors']] == ['demo-00','demo-child-00']
    assert detail['record']['verification'] == 'Not run'
    assert client.get('/api/backlog?demo=empty').json()['total'] == 0
    assert client.get('/api/backlog').json()['total'] == 1
    assert client.get('/api/backlog?demo=unsupported').status_code == 422
    assert client.get('/api/history?demo=mixed').json()['total'] == 15
    assert client.get('/api/work?source=pilot&demo=mixed').json()['freshness']['state'] == 'unavailable'


def test_decision_queue_matches_actionable_count(client):
    work = client.get('/api/work').json()
    queue = client.get('/api/backlog?view=needs_you').json()
    assert queue['total'] == work['needs_you'] == 1
    assert [r['review_task_id'] for r in queue['items'] if not r['context']] == ['cozy-step-review']


def test_combined_filters_ancestor_context_and_full_pagination(client):
    base='/api/backlog?demo=mixed&view=all'
    result=client.get(base+'&type=task&state=Waiting%20for%20clarification').json()
    matches=[r for r in result['items'] if not r['context']]
    assert matches and all(r['kind']=='task' and r['state']=='Waiting for clarification' for r in matches)
    assert any(r['context'] for r in result['items'])
    found=[]
    for page in range(1,5):
        data=client.get(base+f'&q=Guide&page={page}').json()
        found += [r['id'] for r in data['items'] if not r['context']]
    assert len(found)==len(set(found))==data['total']
    assert client.get(base+'&state=not-a-state').json()['total']==0
    unknown=client.get(base+'&state=Unknown').json()
    assert all(r['state']=='Unknown' for r in unknown['items'] if not r['context'])
    mixed=client.get(base+'&type=task&type=milestone&area=Guides').json()
    assert all(r['kind'] in {'task','milestone'} and r['subsystem']=='Guides' for r in mixed['items'] if not r['context'])
