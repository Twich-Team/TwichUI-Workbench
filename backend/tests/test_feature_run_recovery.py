"""Real API/journal/package transitions; coding and confinement are controlled."""

import copy
import json
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from workbench import (
    feature_execution,
    feature_permission,
    feature_steps,
    feature_run_recovery,
    task_sessions,
    task_session,
    real_package,
)
from workbench.db import (
    planning_jobs,
    proposal_decisions,
    task_sessions as sessions,
    real_execution_grants,
    model_attempts,
    write_transaction,
)
from workbench.real_workspace import Runtime
from test_everyday import installed as installed
from test_feature_execution import (
    missing_names_parent,
    controlled_readiness,
    controlled_check,
    controlled_slot,
)
from test_feature_preparation import ScopePlanner


def never_started(client, headers, store, monkeypatch):
    monkeypatch.setattr(Runtime, "check", controlled_check)
    identity, decision, first, _ = missing_names_parent(client, headers, store)
    policy_path = Path(os.environ["WORKBENCH_PREPARATION_CONFIG"])
    policy = json.loads(policy_path.read_text())
    native_output = task_sessions.ROOT / "artifacts/runs/preparations"
    native_output.mkdir(mode=0o700, parents=True)
    policy["output_root"] = str(native_output)
    policy_path.write_text(json.dumps(policy))

    class ControlledLocalScope(ScopePlanner):
        def generate(self, *args, **kwargs):
            raw, model, _ = super().generate(*args, **kwargs)
            return raw, model, "local"

    assert feature_execution.advance(store, adapter=ControlledLocalScope())
    instant = datetime.now(timezone.utc) + timedelta(hours=5, minutes=55)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant if tz is not None else instant.replace(tzinfo=None)

    for module in (feature_permission, feature_steps, feature_run_recovery, task_session):
        monkeypatch.setattr(module, "datetime", Clock)
    monkeypatch.setattr("workbench.intake.now", lambda: instant.isoformat())
    monkeypatch.setattr(real_package.time, "time", lambda: instant.timestamp())
    assert feature_execution.advance(store, provision=controlled_readiness)
    assert feature_execution.advance(store)
    # Actual package allocator refuses the shortened remaining reservation.
    assert task_sessions.process_one(store)
    assert feature_execution.advance(store)
    with store.connect() as c:
        job = feature_run_recovery.journal(c, decision)
        assert job["status"] == "failed"
        parent = c.execute(select(sessions).where(sessions.c.id == decision)).mappings().one()
        assert (
            parent["status"] == "held" and parent["state"]["feature_slot_result"]["outcome"] is None
        )
        offered = feature_run_recovery.offer(c, identity)
        assert offered
        frozen = dict(
            c.execute(select(proposal_decisions).where(proposal_decisions.c.id == decision))
            .mappings()
            .one()
        )
    return identity, decision, first, offered, frozen


def test_native_continuation_preserves_original_authority_and_cannot_revive_old_slot(
    client,
    headers,
    store,
    installed,
    monkeypatch,
):
    identity, decision, first, offer, original = never_started(client, headers, store, monkeypatch)
    expired = datetime.fromisoformat(original["payload"]["feature_run"]["expires_at"]) + timedelta(
        minutes=1
    )

    class ExpiredClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return expired if tz is not None else expired.replace(tzinfo=None)

    for module in (feature_permission, feature_steps, feature_run_recovery, task_session):
        monkeypatch.setattr(module, "datetime", ExpiredClock)
    monkeypatch.setattr("workbench.intake.now", lambda: expired.isoformat())
    monkeypatch.setattr(real_package.time, "time", lambda: expired.timestamp())
    with store.connect() as c, pytest.raises(ValueError, match="expired"):
        feature_permission.validate(c, original)
    proof = offer["approved_inputs"]["recovery"]
    with store.connect() as c:
        old = feature_steps.permission(
            c, original, first, offer_id=proof["previous_offer_id"], require_current=False
        )
        ledger = [dict(v) for v in c.execute(select(model_attempts)).mappings()]
    command = dict(command_id=str(uuid4()), binding=offer["binding"])
    url = "/api/features/" + identity + "/continue-unstarted-run"
    saved = client.post(url, headers=headers, json=command)
    assert saved.status_code == 200, saved.text
    assert client.post(url, headers=headers, json=command).status_code == 200
    assert (
        client.post(url, headers=headers, json={**command, "binding": "a" * 64}).status_code == 409
    )
    assert (
        client.post(url, headers=headers, json={**command, "command_id": str(uuid4())}).status_code
        == 409
    )
    view = saved.json()
    assert view["run"]["id"] == decision and view["completed_steps"] == 0
    assert view["run"]["expires_at"] == original["payload"]["feature_run"]["expires_at"]
    assert view["run"]["continuation_expires_at"] > view["run"]["expires_at"]
    with store.connect() as c:
        actual = dict(
            c.execute(select(proposal_decisions).where(proposal_decisions.c.id == decision))
            .mappings()
            .one()
        )
        assert actual == original
        assert feature_permission.validate(c, actual) == original["payload"]["feature_run"]
        assert (
            feature_steps.permission(
                c, actual, first, offer_id=proof["previous_offer_id"], require_current=False
            )
            == old
        )
        assert [dict(v) for v in c.execute(select(model_attempts)).mappings()] == ledger
        assert not c.execute(select(real_execution_grants)).first()
        job = feature_run_recovery.journal(c, decision)
        assert job["payload"]["current_attempt"] == 2
        assert len(job["payload"]["execution_windows"]) == 1
        parent = c.execute(select(sessions).where(sessions.c.id == decision)).mappings().one()
        assert (
            parent["state"]["feature_history"][0]["slot"]["snapshot"]["feature_offer_id"]
            == proof["previous_offer_id"]
        )
    assert feature_execution.advance(store, provision=controlled_readiness)
    assert feature_execution.advance(store)
    with store.connect() as c:
        parent = c.execute(select(sessions).where(sessions.c.id == decision)).mappings().one()
        policy = feature_steps.permission(
            c,
            original,
            first,
            offer_id=parent["state"]["feature_slot"]["snapshot"]["feature_offer_id"],
        )
        assert policy["feature_time_authority"] == command["command_id"]
    assert task_sessions.process_one(store, advance=controlled_slot)
    assert feature_execution.advance(store)
    with store.connect() as c:
        job = feature_run_recovery.journal(c, decision)
        assert len(job["payload"]["completed_steps"]) == 1
        assert job["payload"]["completed_steps"][0]["task_id"] == first
        assert len(c.execute(select(sessions)).all()) == 1


@pytest.mark.parametrize(
    "fault",
    [
        "changed_package",
        "reservation",
        "stop",
        "unknown_usage",
        "active_call",
        "changed_scope",
        "tampered_receipt",
        "capability",
    ],
)
def test_uncertainty_or_changed_native_proof_cannot_authorize_continuation(
    client,
    headers,
    store,
    installed,
    monkeypatch,
    fault,
):
    identity, decision, _, offer, _ = never_started(client, headers, store, monkeypatch)
    proof = offer["approved_inputs"]["recovery"]
    if fault == "capability":
        config = task_sessions.ROOT / ".local/task-runtime.json"
        value = json.loads(config.read_text())
        value.pop("feature_execution")
        config.write_text(json.dumps(value))
    elif fault in {"changed_package", "reservation"}:
        from pathlib import Path

        root = Path(proof["package_root"])
        target = root / ("owner-command.json" if fault == "changed_package" else "cycles.jsonl")
        target.write_text("{}")
    else:
        with write_transaction(store) as c:
            if fault == "stop":
                row = c.execute(select(sessions).where(sessions.c.id == decision)).mappings().one()
                c.execute(
                    sessions.update()
                    .where(sessions.c.id == decision)
                    .values(state={**row["state"], "cancel_requested": True})
                )
            elif fault in {"unknown_usage", "active_call"}:
                row = (
                    c.execute(
                        select(model_attempts).where(
                            model_attempts.c.purpose == "feature_step_scope"
                        )
                    )
                    .mappings()
                    .one()
                )
                c.execute(
                    model_attempts.update()
                    .where(model_attempts.c.id == row["id"])
                    .values(
                        **({"usage": None} if fault == "unknown_usage" else {"finished_at": None})
                    )
                )
            elif fault == "changed_scope":
                row = (
                    c.execute(select(proposal_decisions).where(proposal_decisions.c.id == decision))
                    .mappings()
                    .one()
                )
                changed = copy.deepcopy(row["payload"])
                changed["feature_run"]["scope"]["route"]["model"] = "changed-model"
                c.execute(
                    proposal_decisions.update()
                    .where(proposal_decisions.c.id == decision)
                    .values(payload=changed)
                )
    url = "/api/features/" + identity + "/continue-unstarted-run"
    if fault == "tampered_receipt":
        command = dict(command_id=str(uuid4()), binding=offer["binding"])
        assert client.post(url, headers=headers, json=command).status_code == 200
        with write_transaction(store) as c:
            job = feature_run_recovery.journal(c, decision)
            payload = copy.deepcopy(job["payload"])
            payload["execution_windows"][0]["expires_at"] = "2099-01-01T00:00:00+00:00"
            c.execute(
                planning_jobs.update()
                .where(planning_jobs.c.id == job["id"])
                .values(payload=payload)
            )
        with store.connect() as c, pytest.raises(ValueError, match="continuation changed"):
            row = (
                c.execute(select(proposal_decisions).where(proposal_decisions.c.id == decision))
                .mappings()
                .one()
            )
            feature_permission.validate(c, row)
    else:
        response = client.post(
            url, headers=headers, json=dict(command_id=str(uuid4()), binding=offer["binding"])
        )
        assert response.status_code == 409, response.text
        with store.connect() as c:
            assert not c.execute(select(real_execution_grants)).first()


def test_unused_slot_reaches_native_prompt_construction_without_coding_feedback(
    client,
    headers,
    store,
    installed,
    monkeypatch,
):
    from workbench import real_provider, real_confine
    from workbench.db import task_run_offers
    from workbench.real_workspace import tree

    identity, decision, _, offer, _ = never_started(client, headers, store, monkeypatch)
    saved = client.post(
        "/api/features/" + identity + "/continue-unstarted-run",
        headers=headers,
        json=dict(command_id=str(uuid4()), binding=offer["binding"]),
    )
    assert saved.status_code == 200, saved.text
    assert feature_execution.advance(store, provision=controlled_readiness)
    with store.connect() as c:
        job = feature_run_recovery.journal(c, decision)
        offered = (
            c.execute(
                select(task_run_offers).where(
                    task_run_offers.c.id == job["payload"]["current_offer_id"]
                )
            )
            .mappings()
            .one()
        )
    runtime = Runtime(offered["snapshot"]["ready_preparation"]["root"])
    before = tree(runtime.workspace)
    observed = []

    def no_provider(tools, instruction, *args, **kwargs):
        observed.append(instruction)
        raise RuntimeError("CONTROLLED prompt captured; no provider contacted")

    monkeypatch.setattr(Runtime, "preflight", lambda self: self.static_preflight())
    monkeypatch.setattr(real_provider, "implement", no_provider)
    monkeypatch.setattr(
        real_confine, "root", lambda: "/sys/fs/cgroup/workbench-real-controlled-no-provider"
    )
    with pytest.raises(RuntimeError, match="CONTROLLED prompt captured"):
        runtime.execute(str(uuid4()), str(uuid4()), lambda: True)
    assert len(observed) == 1 and "earlier slot never dispatched a coding request" in observed[0]
    assert tree(runtime.workspace) == before
