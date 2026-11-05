import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from fastapi.testclient import TestClient

from workbench.api import create_app
from workbench.monitoring import MAX_ROWS, RETENTION, resources, policy

NOW = 1800000000
SAMPLE = dict(cpu=25, gpu=None, ram_used=12*2**30, ram_total=64*2**30,
              vram_used=2*2**30, vram_total=16*2**30, gpu_device='card0 · AMD')


def history(path, rows):
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE samples(at INTEGER PRIMARY KEY, body TEXT)')
        db.executemany('INSERT INTO samples VALUES(?,?)', [(at,json.dumps(value)) for at,value in rows])
    return path


def test_units_freshness_missing_and_read_only(tmp_path):
    path = history(tmp_path/'history.sqlite3', [(NOW-15,SAMPLE)])
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    result = resources(path, now=NOW)
    assert result['latest']['ram_used'] == 12*2**30
    assert result['latest']['gpu'] is None
    assert result['latest']['gpu_device'] == 'card0 · AMD'
    assert result['age_s'] == 15 and result['freshness'] == 'fresh'
    assert resources(path,now=NOW+60)['freshness'] == 'stale'
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_gaps_invalid_metrics_and_no_invented_zeroes(tmp_path):
    path = history(tmp_path/'samples.sqlite3',[(NOW-120,SAMPLE),(NOW,{**SAMPLE,'cpu':101,'ram_used':-1,'gpu':float('nan')})])
    data = resources(path,now=NOW)
    assert len(data['points']) == 3
    assert data['points'][1]['cpu'] is None
    assert data['latest']['cpu'] is None and data['latest']['gpu'] is None
    assert data['latest']['ram_used'] is None


def test_retention_query_output_and_bucket_gaps_are_bounded(tmp_path):
    path = history(tmp_path/'bounded.sqlite3',[(NOW-RETENTION-15,SAMPLE)]+[(at,SAMPLE) for at in range(NOW-RETENTION,NOW+1,60)])
    data = resources(path,168,now=NOW)
    assert len(data['points']) <= 1102
    assert all(p['at'] >= NOW-RETENTION for p in data['points'])
    assert data['first_at'] == NOW-RETENTION
    assert any(p['cpu'] is None for p in data['points'])
    assert data['latest']['cpu'] == 25


def test_malformed_future_empty_and_unavailable_sources(tmp_path):
    assert resources(None,now=NOW)['freshness'] == 'unavailable'
    empty = history(tmp_path/'empty.sqlite3',[])
    assert resources(empty,now=NOW)['latest'] is None
    future = history(tmp_path/'future.sqlite3',[(NOW+15,SAMPLE)])
    assert resources(future,now=NOW)['latest'] is None
    assert resources(future,now=NOW)['freshness'] == 'unavailable'
    with sqlite3.connect(empty) as db:
        db.execute('INSERT INTO samples VALUES(?,?)',(NOW,'not json'))
    assert resources(empty,now=NOW)['freshness'] == 'unavailable'


def test_source_separation_and_demo_determinism(tmp_path):
    observed = history(tmp_path/'observed.sqlite3',[(NOW,SAMPLE)])
    assert resources(observed,source='observed',now=NOW)['latest']['gpu'] is None
    a = resources(observed,source='demo',now=NOW)
    assert a == resources(None,source='demo',now=NOW)
    assert a['source'] == 'demo' and a['freshness'] == 'demo'
    assert a['latest']['gpu'] is not None


def test_read_row_and_body_limits(tmp_path):
    path = history(tmp_path/'rowlimit.sqlite3',[(NOW-i,SAMPLE) for i in range(MAX_ROWS+1)])
    assert resources(path,168,now=NOW)['freshness'] == 'unavailable'
    with sqlite3.connect(path) as db:
        db.execute('DELETE FROM samples')
        db.execute('INSERT INTO samples VALUES(?,?)',(NOW,' '*4097))
    assert resources(path,now=NOW)['freshness'] == 'unavailable'


def test_old_acknowledgment_never_turns_matching_revision_pending(tmp_path):
    path = tmp_path/'policy.sqlite3'
    with sqlite3.connect(path) as db:
        db.executescript('CREATE TABLE settings(id,revision,body);CREATE TABLE observed(id,revision,at,pid,body);')
        body = json.dumps(dict(paused=False,invocations_per_window=4,secret='not exposed'))
        db.execute('INSERT INTO settings VALUES(1,4,?)',(body,))
        db.execute('INSERT INTO observed VALUES(1,4,?,123,?)',(NOW-10000,body))
    result=policy(path,NOW)
    assert result['acknowledgment'] == 'Last acknowledged'
    assert result['freshness'] == 'stale'
    assert 'secret' not in result['values']
    with sqlite3.connect(path) as db:
        db.execute('UPDATE settings SET revision=5')
    assert policy(path,NOW)['acknowledgment'] == 'Saved · awaiting acknowledgment'
    assert policy(None,NOW)['available'] is False


def test_preference_validation_security_conflicts_and_restart(store, tmp_path):
    app = create_app(store)
    with TestClient(app) as client:
        initial = client.get('/api/display-settings').json()
        body = dict(revision=0,resource_hours=24,refresh_seconds=15)
        headers = {'origin':'http://testserver','x-csrf-token':client.get('/api/session').json()['csrf_token']}
        assert client.put('/api/display-settings',json=body).status_code == 403
        assert client.put('/api/display-settings',json=body,headers={**headers,'origin':'http://evil'}).status_code == 403
        assert client.put('/api/display-settings',json=body,headers={**headers,'host':'evil'}).status_code == 400
        for key,value in [('resource_hours',2),('resource_hours',True),('refresh_seconds',1),('refresh_seconds','15'),('revision',True),('paused',False)]:
            assert client.put('/api/display-settings',json={**body,key:value},headers=headers).status_code == 422
        assert client.get('/api/display-settings').json() == initial
        saved = client.put('/api/display-settings',json=body,headers=headers).json()
        assert saved == {**body,'revision':1}
        assert client.put('/api/display-settings',json=body,headers=headers).json() == saved
        assert client.put('/api/display-settings',json={**body,'resource_hours':168},headers=headers).status_code == 409
        assert client.put('/api/operating-policy',json=body,headers=headers).status_code in {404,405}
        for query in ['hours=2','hours=999999','source=pilot','source=demo&hours=-1']:
            assert client.get('/api/resources?'+query).status_code == 422
    # A newly created app/session over the same durable store reads committed values.
    with TestClient(create_app(store)) as restarted:
        assert restarted.get('/api/display-settings').json() == saved
        assert restarted.put('/api/display-settings',json={**saved,'resource_hours':168},headers=headers).status_code == 403
        assert restarted.get('/api/resources').json()['source'] == 'observed'
        assert restarted.get('/api/resources').json()['freshness'] == 'unavailable'
    result = subprocess.run([sys.executable, '-c',
        'import json; from workbench.db import engine_for; from workbench.preferences import read; print(json.dumps(read(engine_for())))'],
        env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1]),
             'WORKBENCH_DB': store.url.database}, capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == saved


def test_activity_uses_explicit_states_and_omits_undated_events(client):
    data = client.get('/api/activity?source=fixture&demo=mixed').json()
    assert data['source'] == 'fixture'
    assert all(key.lower().startswith(('waiting','blocked')) for key in data['counts'])
    assert len(data['omissions']) == 2
    unavailable = client.get('/api/activity?source=pilot').json()
    assert unavailable['freshness']['state'] == 'unavailable'
    assert unavailable['counts'] == {}
