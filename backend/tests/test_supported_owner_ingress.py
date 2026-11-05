"""NON-MODEL trusted owner ingress and failed-preparation evidence regression."""

from datetime import datetime, timezone, timedelta
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from workbench import owner_task, intake, supported_tasks, task_session, planning_worker
from workbench.db import (
    planning_jobs,
    proposal_decisions,
    real_execution_grants,
    model_attempts,
    real_publications,
)
from workbench.schemas import IntakeInput, ProposalDecisionInput
from test_task_to_pr import template_data


@pytest.fixture
def owned_template(tmp_path, monkeypatch):
    folder = tmp_path / "templates"
    folder.mkdir()
    value = template_data()
    raw = json.dumps(value, sort_keys=True).encode()
    identity = hashlib.sha256(raw).hexdigest()
    (folder / (identity + ".json")).write_bytes(raw)
    monkeypatch.setenv("WORKBENCH_TASK_TEMPLATES_ROOT", str(folder))
    auth = tmp_path / "owner.txt"
    auth.write_text("NON-MODEL explicit owner task/scope/budget/draft-PR instruction")
    return auth, hashlib.sha256(auth.read_bytes()).hexdigest(), identity, value


def test_supported_owner_uses_existing_jobs_and_one_explicit_budget_without_execution(
    store, owned_template
):
    auth, h, identity, value = owned_template
    args = (store, auth, h, identity, value["recipe"]["contract"])
    ref = owner_task.record_supported(*args)
    assert owner_task.record_supported(*args) == ref
    with store.connect() as c:
        jobs = c.execute(select(planning_jobs)).mappings().all()
        assert len(jobs) == 2 and all(
            j["status"] == "completed" and j["attempts"] == 0 for j in jobs
        )
        assert all(
            j["payload"]["owner_provenance"]["purpose"] == "supported-task-to-draft-pr-v1"
            for j in jobs
        )
        approval = c.execute(select(proposal_decisions)).mappings().one()
        assert (
            approval["status"] == "applied"
            and approval["payload"]["run_budget"]["publication"] == "draft-pr"
        )
        for table in [real_execution_grants, model_attempts, real_publications]:
            assert not c.execute(select(table)).first()
    assert "not a browser action" in approval["payload"]["note"]


def test_selected_owner_ingress_does_not_apply_an_unrelated_pending_decision(store, owned_template):
    auth, h, identity, value = owned_template
    other = str(uuid4())
    intake.submit(
        store,
        IntakeInput(
            request_id=other,
            kind="feature",
            title="Unrelated scope",
            description="NON-MODEL other task",
            visual_requirement="nonvisual",
            supported_task=identity,
        ),
    )
    assert supported_tasks.process_one(store, intake_id=other)
    p = intake.view(store, other)["proposals"][0]
    command = str(uuid4())
    intake.decide(
        store,
        other,
        ProposalDecisionInput(
            command_id=command,
            proposal_id=p["id"],
            binding=p["binding"],
            scope_revision=1,
            action="approve",
        ),
    )
    owner_task.record_supported(store, auth, h, identity, value["recipe"]["contract"])
    with store.connect() as c:
        assert (
            c.execute(
                select(proposal_decisions.c.status).where(proposal_decisions.c.id == command)
            ).scalar()
            == "pending"
        )


def test_supported_owner_rejects_different_contract_before_recording(store, owned_template):
    auth, h, identity, value = owned_template
    with pytest.raises(ValueError, match="exact owner-approved"):
        owner_task.record_supported(
            store, auth, h, identity, dict(value["recipe"]["contract"], base="a" * 40)
        )
    with store.connect() as c:
        assert not c.execute(select(planning_jobs)).first()


def test_failed_baseline_evidence_is_durable_before_readiness_rejection(tmp_path):
    result = dict(
        status="fail",
        rc=1,
        out="NON-MODEL failed baseline",
        command=["bash", "tests/run.sh"],
        network_mode="private-loopback",
    )
    runtime = SimpleNamespace(
        root=tmp_path,
        workspace=tmp_path,
        prepare=lambda: None,
        check=lambda *a, **kw: result,
        expected_suite=lambda: ["test_fixture.lua"],
    )
    with pytest.raises(ValueError, match="baseline failed"):
        task_session.provision(runtime, {})
    assert json.loads((tmp_path / "fresh-baseline-suite.json").read_text()) == result


def test_three_hour_config_still_requires_private_owned_instruction_and_one_cycle(
    tmp_path, monkeypatch
):
    root = tmp_path / "artifacts/runs/task-lease-owned"
    root.mkdir(parents=True)
    monkeypatch.setattr(task_session, "ROOT", tmp_path)
    auth = tmp_path / "owner.txt"
    auth.write_text("NON-MODEL approved three-hour session")
    now = datetime.now(timezone.utc)
    data = dict(
        started_at=now.isoformat(),
        deadline=(now + timedelta(hours=3)).isoformat(),
        max_attempts=1,
        max_probes=1,
        max_leases=1,
        owner_instruction=str(auth),
        owner_instruction_sha256=hashlib.sha256(auth.read_bytes()).hexdigest(),
        run_root=str(root),
    )
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps(data))
    cfg.chmod(0o600)
    assert task_session.read_config(cfg)["max_attempts"] == 1
    data["max_attempts"] = 2
    cfg.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="bounded session"):
        task_session.read_config(cfg)


def test_terminal_session_closes_only_its_grant_and_preserves_one_outcome(store, tmp_path):
    from workbench import real_execution as execution
    from workbench.db import write_transaction

    decision, other = str(uuid4()), str(uuid4())
    root = tmp_path / "owned"
    root.mkdir()
    config = dict(
        run_root=str(root),
        task=dict(intake_id=str(uuid4()), task_id="controlled-task", approval_id=decision),
    )
    with write_transaction(store) as c:
        for selected in [decision, other]:
            snapshot = dict(task_budget=dict(decision_id=selected))
            c.execute(
                execution.grants.insert().values(
                    id=str(uuid4()),
                    snapshot=snapshot,
                    binding=execution.digest(snapshot),
                    issued_at=execution.now(),
                    expires_at=(datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
                )
            )
    assert not task_session.finish(store, config, "held", "NON-MODEL controlled end")
    before = (root / "session-finished.json").read_bytes()
    assert not task_session.finish(store, config, "held", "NON-MODEL controlled end")
    assert (root / "session-finished.json").read_bytes() == before
    with store.connect() as c:
        rows = c.execute(select(execution.grants)).mappings().all()
        assert all(
            bool(r["revoked_at"]) == (r["snapshot"]["task_budget"]["decision_id"] == decision)
            for r in rows
        )
    with pytest.raises(ValueError, match="Conflicting session"):
        task_session.finish(
            store, dict(config, task=dict(config["task"], approval_id=other)), "held", "changed"
        )


def test_normal_worker_can_win_both_ingress_races_without_losing_provenance(
    store, owned_template, monkeypatch
):
    auth, h, identity, value = owned_template
    submit, decide = intake.submit, intake.decide

    def completed_before_annotation(engine, body, **kwargs):
        result = submit(engine, body, **kwargs)
        assert kwargs["owner_provenance"]["authorization_sha256"] == h
        assert supported_tasks.process_one(engine, intake_id=str(body.request_id))
        return result

    def applied_before_annotation(engine, selected, body):
        result = decide(engine, selected, body)
        assert planning_worker.apply_one(engine, command_id=str(body.command_id))
        assert supported_tasks.process_one(engine, intake_id=selected)
        return result

    monkeypatch.setattr(intake, "submit", completed_before_annotation)
    monkeypatch.setattr(intake, "decide", applied_before_annotation)
    ref = owner_task.record_supported(store, auth, h, identity, value["recipe"]["contract"])
    with store.connect() as c:
        jobs = (
            c.execute(select(planning_jobs).where(planning_jobs.c.intake_id == ref["intake_id"]))
            .mappings()
            .all()
        )
        assert len(jobs) == 2 and all(
            j["status"] == "completed"
            and j["payload"]["owner_provenance"]["authorization_sha256"] == h
            for j in jobs
        )


@pytest.mark.parametrize("read_limit", [45000, 98304])
def test_explicit_continuation_preserves_task_and_prior_scope_receipt(
    store, owned_template, tmp_path, client, headers, read_limit
):
    from workbench import task_execution

    auth, h, identity, value = owned_template
    old = owner_task.record_supported(store, auth, h, identity, value["recipe"]["contract"])
    with store.connect() as c:
        previous = task_execution.snapshot(c, **old)
    amendment = tmp_path / "new-owner-instruction"
    amendment.write_text("NON-MODEL explicit new continuation budget, same scope")
    newhash = hashlib.sha256(amendment.read_bytes()).hexdigest()
    recipe = {
        **value["recipe"],
        "read_budget_policy": "distinct-lines-v1",
        "instruction": "NON-MODEL improved task diagnostics",
    }
    recipe["contract"] = {
        **recipe["contract"],
        "limits": {**recipe["contract"]["limits"], "read_chars": read_limit},
    }
    continued = owner_task.continue_supported(store, old, amendment, newhash, recipe)
    assert continued["task_id"] == old["task_id"] and continued["intake_id"] == old["intake_id"]
    assert continued["approval_id"] != old["approval_id"]
    with store.connect() as c:
        assert task_execution.snapshot(c, **old) == previous
        new = task_execution.snapshot(c, **continued)
        assert new["decomposition"] == previous["decomposition"]
        assert new["task"] == previous["task"]
    assert owner_task.continue_supported(store, old, amendment, newhash, recipe) == continued
    response = client.get("/api/intake/" + old["intake_id"])
    assert response.status_code == 200
    receipt = next(d for d in response.json()["decisions"] if d["id"] == continued["approval_id"])
    assert receipt["payload"]["execution_continuation"]["scope_approval_id"] == old["approval_id"]
    assert receipt["scope_review_required"] is False
    from workbench.schemas import ProposalDecisionInput
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ProposalDecisionInput.model_validate(receipt["payload"])
    with pytest.raises(ValueError, match="scope, base, oracle"):
        owner_task.continue_supported(
            store, old, amendment, newhash, {**recipe, "oracle_sha256": "0" * 64}
        )
