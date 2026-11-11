"""API and private egress delivery on disposable stores with controlled GitHub."""

import json
import socket
import sqlite3

import pytest
from sqlalchemy import select

from workbench import draft_delivery, real_publication as publication
from workbench.db import engine_for, real_publications
from test_task_to_pr import (
    checked_draft as checked_draft,
    supported as supported,
    delivery_clock as delivery_clock,
    ControlledRemote,
)


@pytest.fixture
def installed_draft(store, checked_draft, tmp_path, monkeypatch, client):
    row, _, _, _ = checked_draft
    release = tmp_path / "release"
    release.mkdir()
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(mode=0o700)
    database = artifacts / "workbench.sqlite3"
    with sqlite3.connect(store.url.database) as source, sqlite3.connect(database) as dest:
        source.backup(dest)
    database.chmod(0o600)
    policy = dict(
        repository="Twich-Team/TwichUI", base_branch="main",
        database_identity=[database.stat().st_dev, database.stat().st_ino],
        owner_instruction_sha256="e" * 64, automatic_since="2000-01-01T00:00:00+00:00",
    )
    authority = artifacts / "draft-authority.json"
    authority.write_text(json.dumps(policy))
    authority.chmod(0o600)
    monkeypatch.setattr(draft_delivery, "ROOT", release)
    engine = engine_for(database)
    monkeypatch.setattr(client.app.state, "engine", engine)
    yield engine, row, authority
    engine.dispose()


@pytest.mark.parametrize("lost", ["push", "pr"])
def test_browser_request_and_egress_worker_reconcile_a_lost_response(
    client, headers, installed_draft, monkeypatch, lost
):
    engine, row, _ = installed_draft
    identity = row["id"]
    prefix = "/api/real-executions/" + identity
    review = client.get(prefix + "/draft-review").json()
    assert review["available"] is True
    body = dict(binding=review["binding"])
    assert client.post(prefix + "/draft-delivery", headers=headers, json=body).status_code == 200
    assert client.post(prefix + "/draft-delivery", headers=headers, json=body).status_code == 200
    remote = ControlledRemote()
    remote.lose = lost
    monkeypatch.setattr("workbench.publication_adapter.PilotGitHub", lambda *_args, **_kwargs: remote)
    left, right = socket.socketpair()
    try:
        for _ in range(12):
            draft_delivery.handle(right, identity)
    finally:
        left.close()
        right.close()
    state = client.get(prefix + "/delivery").json()
    assert state["status"] == "published" and state["source"] == "controlled"
    assert not state["published"] and state["url"] is None
    assert remote.prepares == remote.pushes == remote.creates == 1
    with engine.connect() as c:
        assert len(c.execute(select(real_publications)).all()) == 1


def test_standing_policy_withdrawal_prevents_remote_action(client, headers, installed_draft, monkeypatch):
    _, row, authority = installed_draft
    prefix = "/api/real-executions/" + row["id"]
    review = client.get(prefix + "/draft-review").json()
    assert client.post(prefix + "/draft-delivery", headers=headers,
                       json={"binding": review["binding"]}).status_code == 200
    authority.unlink()
    monkeypatch.setattr("workbench.publication_adapter.PilotGitHub",
                        lambda *_args, **_kwargs: pytest.fail("Publication was attempted after withdrawal"))
    left, right = socket.socketpair()
    try:
        assert not draft_delivery.handle(right, row["id"])
    finally:
        left.close()
        right.close()
    assert not client.get(prefix + "/draft-review").json()["available"]


def test_automatic_delivery_queues_only_new_current_checked_results(installed_draft):
    engine, row, authority = installed_draft
    policy = json.loads(authority.read_text())
    authority.write_text(json.dumps({**policy, "automatic_since": "2099-01-01T00:00:00+00:00"}))
    assert not draft_delivery.queue_automatic(engine)
    authority.write_text(json.dumps(policy))
    assert draft_delivery.queue_automatic(engine)
    assert not draft_delivery.queue_automatic(engine)
    assert publication.view(engine, row["id"])["status"] == "pending"


def test_private_connector_delivers_only_for_the_existing_system_worker(monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace
    from uuid import uuid4
    import codex_connector as connector

    identity = str(uuid4())
    calls = []
    monkeypatch.setattr(draft_delivery, "handle", lambda connection, execution: calls.append(execution) or True)
    monkeypatch.setattr(connector, "resolve_backend", lambda *_: pytest.fail("A model request was started"))
    actual_path = Path

    def paths(value):
        if str(value).startswith("/proc/") and str(value).endswith("/cgroup"):
            return SimpleNamespace(read_text=lambda: "0::/system.slice/workbench-everyday-worker.service/main")
        return actual_path(value)

    monkeypatch.setattr(connector, "Path", paths)
    left, right = socket.socketpair()
    try:
        left.sendall(json.dumps({"operation": "draft-delivery", "execution_id": identity}).encode() + b"\n")
        connector.handle(right, None, None, None)
        assert json.loads(left.recv(4096)) == {"worked": True}
    finally:
        left.close()
        right.close()
    assert calls == [identity]
