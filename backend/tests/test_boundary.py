from uuid import uuid4
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select
from workbench.api import create_app
from workbench.db import commands

URL = "/api/tasks/cozy-step-review/decisions"


def body(client):
    task = client.get("/api/tasks/cozy-step-review").json()
    return dict(command_id=str(uuid4()), version=task["version"], notes="",
                choices={claim["id"]: "confirm" for claim in task["evidence"]["claims"]})


@pytest.mark.parametrize("override", [
    {"origin": "http://evil.test"}, {"origin": "null"}, {"x-csrf-token": "wrong"},
    {"sec-fetch-site": "cross-site"}, {"origin": "http://localhost:8787"},
])
def test_mutations_require_exact_origin_and_csrf(client, headers, override, store):
    response = client.post(URL, json=body(client), headers={**headers, **override})
    assert response.status_code == 403
    with store.connect() as connection:
        assert not connection.execute(select(commands)).first()


def test_missing_origin_refused(client, headers):
    headers.pop("origin")
    assert client.post(URL, headers=headers, json=body(client)).status_code == 403


def test_forwarded_headers_never_authorize_host(client):
    response = client.get("/api/session", headers={"host": "evil.test", "x-forwarded-host": "testserver",
                                                 "x-forwarded-for": "127.0.0.1"})
    assert response.status_code == 400
    assert client.get("/api/session", headers={"host": "127.0.0.1:9999"}).status_code == 400
    assert client.get("/api/session", headers=[("host", "testserver"), ("host", "evil.test")]).status_code == 400


def test_body_bounds_and_private_paths(client, headers):
    assert client.post(URL, headers=headers, json={"extra": "x" * 20000}).status_code == 413
    assert client.post(URL, headers={**headers, "content-type": "text/plain"}, content="{}").status_code == 415
    assert client.post(URL, headers={**headers, "content-type": "application/json"}, content="{").status_code == 422
    for path in ("/api/state", "/api/.local/workbench.sqlite3", "/api/tasks/no-such-task"):
        assert client.get(path).status_code == 404


@pytest.mark.parametrize("change", [
    {"choices": {}}, {"choices": {"follow": "confirm"}}, {"choices": {"follow": "approve"}},
    {"notes": "x" * 1601}, {"command_id": "invalid"}, {"version": "invalid"},
    {"unexpected": True},
])
def test_invalid_decisions_are_not_saved(client, headers, change, store):
    assert client.post(URL, headers=headers, json={**body(client), **change}).status_code == 422
    with store.connect() as connection:
        assert not connection.execute(select(commands)).first()

def test_home_lan_peers_share_operator_access_with_origin_and_duplicate_checks(store, monkeypatch):
    monkeypatch.setenv('WORKBENCH_PRIVATE_LAN_IP', '10.0.0.9')
    monkeypatch.setenv('WORKBENCH_PRIVATE_LAN_SUBNET', '10.0.0.0/24')
    app = create_app(store)
    origin = 'https://10.0.0.9:9487'
    with (
        TestClient(app, base_url=origin, client=('10.0.0.25', 1234)) as first,
        TestClient(app, base_url=origin, client=('10.0.0.210', 1234)) as second,
        TestClient(app, base_url=origin, client=('10.0.1.25', 1234)) as outside,
    ):
        payload = body(first)
        token = first.get('/api/session').json()['csrf_token']
        assert second.get('/api/session').json()['csrf_token'] == token
        headers = {'origin': origin, 'x-csrf-token': token}
        assert first.post(URL, json=payload, headers={'origin': origin}).status_code == 403
        assert second.post(URL, json=payload, headers={**headers, 'origin': 'https://other.invalid'}).status_code == 403
        assert outside.get('/api/session', headers={'x-forwarded-for': '10.0.0.25'}).status_code == 403
        assert outside.post(URL, json=payload, headers=headers).status_code == 403
        accepted = first.post(URL, json=payload, headers=headers)
        assert accepted.is_success
        assert second.post(URL, json=payload, headers=headers).json() == accepted.json()
        assert first.post('/v1/chat/completions', json={}, headers=headers).status_code in {404, 405}
    with store.connect() as connection:
        assert len(connection.execute(select(commands)).all()) == 1


def test_portal_requires_its_actual_peer_exact_domain_https_origin_and_csrf(store, monkeypatch):
    monkeypatch.setenv('WORKBENCH_PRIVATE_LAN_IP', '10.0.0.9')
    monkeypatch.setenv('WORKBENCH_PRIVATE_LAN_SUBNET', '10.0.0.0/24')
    app = create_app(store)
    origin = 'https://workbench.twich.gg'
    with TestClient(app, base_url=origin, client=('10.0.4.3', 1234)) as proxy:
        token = proxy.get('/api/session').json()['csrf_token']
        headers = {'origin': origin, 'x-csrf-token': token}
        payload = body(proxy)
        for override in ({'origin': 'https://10.0.0.9:9487'}, {'origin': 'https://evil.invalid'},
                         {'origin': 'null'}, {'x-csrf-token': 'wrong'}, {'sec-fetch-site': 'cross-site'}):
            assert proxy.post(URL, json=payload, headers=headers | override).status_code == 403
        assert proxy.post(URL, json=payload, headers={'x-csrf-token': token}).status_code == 403
        assert proxy.get('/api/session', headers=[('host', 'workbench.twich.gg'),
                                                 ('host', '10.0.0.9:9487')]).status_code == 400
        assert proxy.post(URL, json=payload, headers=[*headers.items(), ('origin', origin)]).status_code == 403
        for host in ('10.0.0.9:9487', 'evil.invalid', 'workbench.twich.gg.evil.invalid', 'workbench.twich.gg:443'):
            assert proxy.get('/api/session', headers={'host': host}).status_code == 400
        with store.connect() as connection:
            assert not connection.execute(select(commands)).first()
        accepted = proxy.post(URL, json=payload, headers=headers)
        assert accepted.is_success
        assert proxy.post(URL, json=payload, headers=headers).json() == accepted.json()
        with store.connect() as connection:
            assert len(connection.execute(select(commands)).all()) == 1
    with TestClient(app, base_url='http://workbench.twich.gg', client=('10.0.4.3', 1234)) as plain:
        assert plain.get('/api/session', headers={'x-forwarded-proto': 'https'}).status_code == 403


@pytest.mark.parametrize('peer', ['10.0.4.2', '10.0.4.4', '10.0.0.25', '127.0.0.1', '::ffff:10.0.4.3'])
def test_forwarding_headers_cannot_impersonate_the_portal(store, monkeypatch, peer):
    monkeypatch.setenv('WORKBENCH_PRIVATE_LAN_IP', '10.0.0.9')
    monkeypatch.setenv('WORKBENCH_PRIVATE_LAN_SUBNET', '10.0.0.0/24')
    app = create_app(store)
    with TestClient(app, base_url='https://workbench.twich.gg', client=(peer, 1234)) as client:
        assert client.get('/api/session', headers={
            'x-forwarded-for': '10.0.4.3', 'x-forwarded-host': 'workbench.twich.gg',
            'x-forwarded-proto': 'https',
            'forwarded': 'for=10.0.4.3;host=workbench.twich.gg;proto=https',
        }).status_code == 400


@pytest.mark.parametrize('listener,subnet', [('10.0.1.9', '10.0.1.0/24'),
                                           ('10.0.0.9', '10.0.0.0/25')])
def test_other_private_listener_configurations_do_not_gain_portal_access(store, monkeypatch, listener, subnet):
    monkeypatch.setenv('WORKBENCH_PRIVATE_LAN_IP', listener)
    monkeypatch.setenv('WORKBENCH_PRIVATE_LAN_SUBNET', subnet)
    with TestClient(create_app(store), base_url='https://workbench.twich.gg', client=('10.0.4.3', 1234)) as client:
        assert client.get('/api/session').status_code == 400
