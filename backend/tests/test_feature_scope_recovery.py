"""A separate bounded read-only correction never rewrites the parent run."""

import copy
from uuid import uuid4

from sqlalchemy import select
import pytest

from workbench import feature_preparation, feature_scope_recovery, feature_permission
from workbench.db import (
    planning_jobs,
    model_attempts,
    proposal_decisions,
    task_sessions,
    write_transaction,
)
from test_feature_preparation import context, ScopePlanner
from test_feature_steps import scope
from test_everyday import installed as installed


class LocalProtocolPlanner(ScopePlanner):
    """Controlled response with the production local route accounting shape."""

    def generate(self, *args, **kwargs):
        raw, model, _ = super().generate(*args, **kwargs)
        return raw, model, "local"


def failed(client, headers, store):
    decision, task, _, job_id = context(client, headers, store)
    response = copy.deepcopy(scope())
    response["checks"] = []
    planner = LocalProtocolPlanner(response=response)
    for _ in range(3):
        assert feature_preparation.prepare_scope(store, job_id, task, adapter=planner)
    with store.connect() as c:
        job = c.execute(select(planning_jobs).where(planning_jobs.c.id == job_id)).mappings().one()
        row = (
            c.execute(select(proposal_decisions).where(proposal_decisions.c.id == decision))
            .mappings()
            .one()
        )
        assert job["status"] == "failed"
        offered = feature_scope_recovery.offer(c, job["intake_id"])
        assert offered is not None
    return (
        job["intake_id"],
        decision,
        task,
        job_id,
        dict(row),
        copy.deepcopy(job["payload"]),
        offered,
    )


def test_explicit_additional_source_requests_preserve_parent_scope_prior_usage_and_one_session(
    client,
    headers,
    store,
    installed,
):
    identity, decision, task, job_id, old, payload, offered = failed(client, headers, store)
    command = dict(command_id=str(uuid4()), binding=offered["binding"])
    endpoint = "/api/features/" + identity + "/prepare-step-again"
    result = client.post(endpoint, headers=headers, json=command)
    assert result.status_code == 200, result.text
    assert result.json()["run"]["id"] == decision and result.json()["scope_correction"] is None
    assert client.post(endpoint, headers=headers, json=command).status_code == 200
    assert (
        client.post(
            endpoint, headers=headers, json={**command, "command_id": str(uuid4())}
        ).status_code
        == 409
    )
    with store.connect() as c:
        job = c.execute(select(planning_jobs).where(planning_jobs.c.id == job_id)).mappings().one()
        row = (
            c.execute(select(proposal_decisions).where(proposal_decisions.c.id == decision))
            .mappings()
            .one()
        )
        assert dict(row) == old and job["payload"]["scope_history"] == payload["scope_history"]
        assert len(c.execute(select(task_sessions)).all()) == 1
        assert len(c.execute(select(proposal_decisions)).all()) == 2
        permission = row["payload"]["feature_run"]
        maximum, reference, _ = feature_scope_recovery.allowance(c, job, task, permission)
        assert maximum == 6 and reference == command["command_id"]
        receipt = job["payload"]["scope_recoveries"][0]
        assert receipt["generation_requests"] == 3 and receipt["implementation_dispatch"] is False
        assert receipt["expires_at"] <= permission["expires_at"]
    reader = LocalProtocolPlanner(
        response=dict(
            inspection=[dict(path=scope()["read_paths"][0], start=1, end=120)], limitation=""
        )
    )
    assert feature_preparation.prepare_scope(store, job_id, task, adapter=reader)
    assert feature_preparation.prepare_scope(store, job_id, task, adapter=LocalProtocolPlanner())
    with store.connect() as c:
        job = c.execute(select(planning_jobs).where(planning_jobs.c.id == job_id)).mappings().one()
        assert (
            job["payload"]["phase"] == "scope_ready" and len(job["payload"]["scope_history"]) == 5
        )
        assert job["payload"]["scope_history"][:3] == payload["scope_history"]
        assert (
            job["payload"]["scope_history"][-1]["scope_request_permission"] == command["command_id"]
        )
        assert (
            c.execute(
                select(proposal_decisions.c.payload).where(proposal_decisions.c.id == decision)
            ).scalar_one()["feature_run"]
            == permission
        )


@pytest.mark.parametrize(
    "fault", ["unknown-usage", "unknown-history", "stopped", "changed-history", "foreign-ledger"]
)
def test_changed_uncertain_or_stopped_work_never_receives_more_scope_requests(
    client,
    headers,
    store,
    installed,
    fault,
):
    identity, decision, task, job_id, old, payload, offered = failed(client, headers, store)
    with write_transaction(store) as c:
        if fault == "unknown-usage":
            c.execute(
                model_attempts.update()
                .where(model_attempts.c.id == payload["scope_history"][-1]["operation_id"])
                .values(usage=None)
            )
        elif fault == "foreign-ledger":
            c.execute(
                model_attempts.update()
                .where(model_attempts.c.id == payload["scope_history"][-1]["operation_id"])
                .values(purpose="other")
            )
        elif fault == "stopped":
            parent = (
                c.execute(select(task_sessions).where(task_sessions.c.id == decision))
                .mappings()
                .one()
            )
            c.execute(
                task_sessions.update()
                .where(task_sessions.c.id == decision)
                .values(state={**parent["state"], "cancel_requested": True})
            )
        else:
            changed = copy.deepcopy(payload)
            if fault == "unknown-history":
                changed["scope_history"][-1]["confirmed_finished"] = False
            else:
                changed["scope_history"][-1]["failure"] = (
                    "A different failure requiring a fresh review"
                )
            c.execute(
                planning_jobs.update().where(planning_jobs.c.id == job_id).values(payload=changed)
            )
    response = client.post(
        "/api/features/" + identity + "/prepare-step-again",
        headers=headers,
        json=dict(command_id=str(uuid4()), binding=offered["binding"]),
    )
    assert response.status_code == 409
    with store.connect() as c:
        job = c.execute(select(planning_jobs).where(planning_jobs.c.id == job_id)).mappings().one()
        assert not job["payload"].get("scope_recoveries") and job["status"] == "failed"
        assert (
            c.execute(
                select(proposal_decisions.c.payload).where(proposal_decisions.c.id == decision)
            ).scalar_one()["feature_run"]
            == old["payload"]["feature_run"]
        )


def test_stop_after_extra_preparation_permission_cannot_restart_or_replay_it(
    client,
    headers,
    store,
    installed,
):
    identity, decision, task, job_id, _, _, offered = failed(client, headers, store)
    command = feature_scope_recovery.FeatureScopeCorrectionInput(
        command_id=uuid4(), binding=offered["binding"]
    )
    receipt = feature_scope_recovery.request(store, identity, command)
    feature_permission.stop(store, identity, decision, str(uuid4()))
    with pytest.raises(ValueError, match="stopped|expired"):
        feature_preparation.prepare_scope(store, job_id, task, adapter=LocalProtocolPlanner())
    assert feature_scope_recovery.request(store, identity, command) == receipt
    with store.connect() as c:
        assert (
            len(
                c.execute(
                    select(model_attempts).where(model_attempts.c.purpose == "feature_step_scope")
                ).all()
            )
            == 3
        )
        assert feature_scope_recovery.offer(c, identity) is None
