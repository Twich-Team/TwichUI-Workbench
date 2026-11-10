"""Normal plan approval -> settled storage failure -> automatic offer, no run grant."""

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from workbench import preparation_resources, trusted_preparation, worker
from workbench.db import planning_jobs, proposal_decisions, real_execution_grants, model_attempts
from workbench.storage_resources import StorageHold
from test_bug_intent_workflow import prepared_scope
from test_everyday import installed  # noqa: F401
from test_regression_evidence import ControlledPreparer


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.usefixtures("installed")
def test_approved_task_recovers_storage_once_and_keeps_separate_run_permission(
    client, headers, store, monkeypatch, legacy
):
    once = []

    class InterruptedCopy(ControlledPreparer):
        def prepare(self):
            super().prepare()
            self.workspace.chmod(0o700)
            if not once:
                once.append(True)
                if legacy:
                    raise ValueError(preparation_resources.LEGACY)
                raise StorageHold(
                    "low_disk", "Controlled low disk before checks", dict(free_bytes=1)
                )

    monkeypatch.setattr(trusted_preparation, "Runtime", InterruptedCopy)
    item, command, proposal, calls, endpoint, offer = prepared_scope(
        client, headers, store, monkeypatch
    )
    assert not offer["available"] and offer["preparation_resource"]["automatic"]
    with store.connect() as c:
        job = dict(
            c.execute(select(planning_jobs).where(planning_jobs.c.kind == "behavioral_preparation"))
            .mappings()
            .one()
        )
    assert job["status"] == "failed" and job["attempts"] == 1
    retained = Path(job["payload"]["preparation_claim"]["root"])
    old_recipe = (retained / "recipe.json").read_bytes()
    assert preparation_resources.process_one(store)
    assert not preparation_resources.process_one(store)
    assert worker.tick(store)  # Same job, fresh recorded round, no model call.
    assert not worker.tick(store)
    store.dispose()
    current = client.get(endpoint).json()
    assert current["available"] and current["offer"]["repair_cycle"]["verification"] == "not_run"
    assert current["receipt"] is None and len(calls) == 1
    assert (
        client.post(
            "/api/intake/" + item["id"] + "/decisions", headers=headers, json=command
        ).json()["status"]
        == "applied"
    )
    assert not worker.tick(store) and len(calls) == 1
    with store.connect() as c:
        saved = dict(
            c.execute(select(planning_jobs).where(planning_jobs.c.id == job["id"])).mappings().one()
        )
        assert saved["status"] == "completed" and saved["attempts"] == 2
        history = saved["payload"]["storage_recovery_history"]
        assert len(history) == 1 and history[0]["claim"] == job["payload"]["preparation_claim"]
        assert history[0]["result"] == job["payload"]["preparation_result"]
        assert len(c.execute(select(proposal_decisions)).all()) == 1
        assert len(c.execute(select(model_attempts)).all()) == 1
        assert not c.execute(select(real_execution_grants)).first()
    assert (retained / "recipe.json").read_bytes() == old_recipe


@pytest.mark.parametrize("fault", ["changed_copy", "uncertain_check"])
@pytest.mark.usefixtures("installed")
def test_preparation_recovery_preserves_changes_and_holds_unsettled_checks(
    client, headers, store, monkeypatch, fault
):
    class Stopped(ControlledPreparer):
        def prepare(self):
            super().prepare()
            self.workspace.chmod(0o700)
            raise StorageHold("low_disk", "Controlled storage failure", {})

    monkeypatch.setattr(trusted_preparation, "Runtime", Stopped)
    item, _, _, calls, endpoint, _ = prepared_scope(client, headers, store, monkeypatch)
    with store.connect() as c:
        job = dict(
            c.execute(select(planning_jobs).where(planning_jobs.c.kind == "behavioral_preparation"))
            .mappings()
            .one()
        )
    root = Path(job["payload"]["preparation_claim"]["root"])
    if fault == "uncertain_check":
        (root / "usage.jsonl").write_text(
            json.dumps(dict(cgroup_removed=False, leaked_after_kill=True)) + "\n"
        )
    else:
        workspace = Path(json.loads((root / "workspace-location.json").read_text())["path"])
        (workspace / "tests/retained.txt").write_text("preserve this unreviewed change")
    assert not preparation_resources.process_one(store)
    held = client.get(endpoint).json()
    assert not held["available"] and not held["preparation_resource"]["automatic"]
    assert not worker.tick(store) and len(calls) == 1
    with store.connect() as c:
        saved = c.execute(
            select(planning_jobs.c.payload).where(planning_jobs.c.id == job["id"])
        ).scalar_one()
        assert saved["preparation_claim"] == job["payload"]["preparation_claim"]
        assert "storage_recovery_history" not in saved
        assert not c.execute(select(real_execution_grants)).first()


@pytest.mark.usefixtures("installed")
def test_low_resource_admission_does_not_allocate_or_consume_preparation_claim(
    client, headers, store, monkeypatch
):
    from workbench import everyday
    from test_scope_binding import unapproved, scope_command, approve, get

    item, calls = unapproved(client, headers, store, monkeypatch)
    item = client.post(
        "/api/intake/" + item["id"] + "/scope", headers=headers, json=scope_command(item)
    ).json()
    approve(client, headers, item)
    assert worker.tick(store) and worker.tick(store)
    monkeypatch.setattr(everyday, "measure", lambda policy: (16 << 20, (4 << 30) + (16 << 20), 0))
    assert not worker.tick(store)
    with store.connect() as c:
        job = (
            c.execute(select(planning_jobs).where(planning_jobs.c.kind == "behavioral_preparation"))
            .mappings()
            .one()
        )
        assert (
            job["status"] == "queued"
            and job["attempts"] == 0
            and "preparation_claim" not in job["payload"]
        )
        assert job["payload"]["storage_recovery"]["automatic"]
    state = get(client, item)
    task = next(t for t in state["tasks"] if t["kind"] == "task")
    context = client.get(
        "/api/intake/" + item["id"] + "/tasks/" + task["id"] + "/run-approval"
    ).json()
    assert not context["available"] and context["preparation_resource"]["automatic"]
    assert len(calls) == 1
