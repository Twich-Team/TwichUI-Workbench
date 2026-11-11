"""Global pause admission, durable decisions and safe continuation without a provider."""

import pytest
from sqlalchemy import select

from workbench import model_policy, work_controls, worker
from workbench.db import engine_for, execution_policies
from workbench.model_policy import ExecutionPolicy


def test_pause_resume_is_durable_idempotent_and_preserves_model_choices(client, headers, store):
    original = model_policy.read(store)
    url = "/api/work-controls"
    current = client.get(url).json()
    assert current["paused"] is False
    body = {"revision": current["revision"], "paused": True}
    first = client.put(url, headers=headers, json=body)
    assert first.status_code == 200
    assert first.json()["paused"] is True
    assert client.put(url, headers=headers, json=body).json() == first.json()
    reopened = engine_for(store.url.database)
    try:
        assert work_controls.read(reopened).paused is True
    finally:
        reopened.dispose()
    saved = model_policy.read(store)
    assert saved.model_dump(exclude={"revision", "pause_new_work"}) == original.model_dump(exclude={"revision", "pause_new_work"})
    model_policy.save(store, saved.model_copy(update={"pause_new_work": False}),
                      model_policy.capabilities())
    assert work_controls.read(store).paused is True
    assert client.put(url, headers=headers, json={
        "revision": body["revision"], "paused": False,
    }).status_code == 409
    state = client.get(url).json()
    resumed = client.put(url, headers=headers, json={
        "revision": state["revision"], "paused": False,
    }).json()
    assert resumed["paused"] is False
    with store.connect() as c:
        assert c.execute(select(execution_policies.c.actor)).scalars().all()[0] == "local-owner-work-control"


def test_work_controls_keep_the_existing_browser_boundary(client, store):
    assert client.put("/api/work-controls", json={"revision": 0, "paused": True}).status_code == 403
    assert work_controls.read(store).paused is False


def test_paused_worker_continues_only_an_already_claimed_session(store, monkeypatch):
    work_controls.save(store, work_controls.WorkControlInput(revision=0, paused=True))
    observed = []

    def resume(_engine, stop_event=None, *, resume_only=False):
        observed.append(resume_only)
        return True

    monkeypatch.setattr("workbench.investigation_recovery.recover_one", lambda _engine: False)
    monkeypatch.setattr(worker.task_sessions, "process_one", resume)
    monkeypatch.setattr(worker, "process_one", lambda *_: pytest.fail("A new decision was applied while paused"))
    monkeypatch.setattr(worker.planning_worker, "process_one", lambda *_args, **_kwargs: pytest.fail("A provider was started while paused"))
    assert worker.tick(store)
    assert observed == [True]


def test_pause_flag_is_compatible_with_historical_model_policy():
    old = ExecutionPolicy().model_dump()
    assert "pause_new_work" not in old
    assert not ExecutionPolicy.model_validate(old).pause_new_work


def test_paused_workspace_preserves_a_queued_request(client, headers, store):
    from test_intake import submit

    item, _ = submit(client, headers, kind="issue", title="Saved while work is paused")
    work_controls.save(store, work_controls.WorkControlInput(revision=0, paused=True))
    page = client.get("/api/workspace").json()
    assert page["work_paused"] is True
    assert page["items"][0]["state"] == "paused"
    assert page["items"][0]["id"] == item["id"]
    assert page["counts"]["paused"] == 1 and page["counts"]["active"] == 0
    assert not worker.tick(store)
    assert client.get("/api/intake/" + item["id"]).json()["jobs"][0]["status"] == "queued"
    work_controls.save(store, work_controls.WorkControlInput(revision=1, paused=False))
    assert client.get("/api/workspace").json()["items"][0]["state"] == "queued"
    from workbench.planning_provider import DemoProvider
    assert worker.tick(store, planning_adapter=DemoProvider())
    after = client.get("/api/intake/" + item["id"]).json()
    assert len(after["jobs"]) == 1 and after["jobs"][0]["status"] == "completed"

