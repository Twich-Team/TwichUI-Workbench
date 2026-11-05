"""NON-MODEL one-off UI ingress/recovery; disposable stores, no lease/provider/GitHub."""

import json
from uuid import uuid4
from pathlib import Path
import pytest
from sqlalchemy import select
from workbench import (
    task_sessions as sessions,
    supported_tasks,
    intake,
    planning_worker,
    task_budget,
    task_session,
)
from workbench.db import task_sessions as rows, proposal_decisions
from workbench.schemas import IntakeInput, ProposalDecisionInput
from test_supported_owner_ingress import owned_template as owned_template


@pytest.fixture
def installed(store, tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "ROOT", tmp_path)
    monkeypatch.setattr(task_session, "ROOT", tmp_path)
    local = tmp_path / ".local"
    local.mkdir()
    proof = local / "proof.json"
    proof.write_text("{}")
    db = Path(store.url.database)
    value = dict(
        mode="explicit-one-off-only",
        database=str(db),
        database_identity=[db.stat().st_dev, db.stat().st_ino],
        templates_root=str(local / "supported-task-templates"),
        repository="Twich-Team/TwichUI",
        base_branch="main",
        model="pilot-qwen38-27b",
        automatic_fallback=False,
        total_limit_proof=str(proof),
        total_limit_proof_sha256=sessions.sha(proof.read_bytes()),
    )
    path = local / "task-runtime.json"
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    assert sessions.capability(store) == value
    return tmp_path


def approved(store, owned_template, activate=True):
    _, _, identity, _ = owned_template
    iid = str(uuid4())
    intake.submit(
        store,
        IntakeInput(
            request_id=iid,
            kind="feature",
            title="NON-MODEL supported task",
            description="Offline workflow check",
            visual_requirement="nonvisual",
            supported_task=identity,
        ),
    )
    assert supported_tasks.process_one(store, intake_id=iid)
    proposal = intake.view(store, iid)["proposals"][0]
    offer = task_budget.context(store, iid, proposal["id"])["offer"]
    body = ProposalDecisionInput(
        command_id=uuid4(),
        proposal_id=proposal["id"],
        binding=proposal["binding"],
        scope_revision=1,
        action="approve",
        run_budget={**offer["budget"], "activate_once": activate},
    )
    intake.decide(store, iid, body)
    assert planning_worker.apply_one(store, command_id=str(body.command_id))
    assert supported_tasks.process_one(store, intake_id=iid)
    return str(body.command_id)


def test_queue_requires_fresh_explicit_activation_and_deduplicates(
    store, owned_template, installed
):
    old = approved(store, owned_template, False)
    assert not sessions.queue_one(store)
    key = approved(store, owned_template)
    assert sessions.queue_one(store) and not sessions.queue_one(store)
    with store.connect() as c:
        row = c.execute(select(rows)).mappings().one()
        assert row["id"] == key and row["id"] != old and row["status"] == "pending"
        view = sessions.public_view(c, row["task_id"])
        assert view["can_stop"] and "snapshot" not in view and "state" not in view


def test_capability_is_store_bound_private_and_cannot_enable_recurring(store, installed):
    p = installed / ".local/task-runtime.json"
    original = json.loads(p.read_text())
    for change in [
        dict(database_identity=[0, 0]),
        dict(automatic_fallback=True),
        dict(mode="recurring"),
        dict(repository="elsewhere/private"),
    ]:
        p.write_text(json.dumps({**original, **change}))
        assert sessions.capability(store) is None
    p.write_text(json.dumps(original))
    p.chmod(0o644)
    assert sessions.capability(store) is None


def test_preparation_and_restart_use_same_saved_session_no_replay(store, owned_template, installed):
    key = approved(store, owned_template)
    sessions.queue_one(store)
    calls = []

    def scripted(engine, cfg, stop_event):
        value = json.loads(cfg.read_text())
        calls.append(str(cfg))
        root = Path(value["run_root"])
        root.mkdir(exist_ok=True)
        if len(calls) == 2:
            (root / "session-finished.json").write_text(
                json.dumps(
                    dict(task=value["task"], status="published", message="NON-MODEL draft result")
                )
            )

    assert sessions.process_one(store, advance=scripted)
    with store.connect() as c:
        assert c.execute(select(rows.c.status)).scalar() == "waiting_recovery"
    assert sessions.process_one(store, advance=scripted)
    assert calls[0] == calls[1]
    assert not sessions.process_one(store, advance=scripted)
    with store.connect() as c:
        row = c.execute(select(rows)).mappings().one()
        assert row["status"] == "completed" and row["id"] == key


def test_queued_stop_is_idempotent_and_starts_nothing(store, owned_template, installed):
    key = approved(store, owned_template)
    sessions.queue_one(store)
    cmd = str(uuid4())
    assert sessions.cancel(store, key, cmd)["status"] == "cancelled"
    assert sessions.cancel(store, key, cmd)["status"] == "cancelled"
    assert not sessions.process_one(store, advance=lambda *a, **kw: pytest.fail("stopped work ran"))
    assert not (installed / "artifacts").exists()


def test_expired_approval_is_held_without_activation(store, owned_template, installed):
    key = approved(store, owned_template)
    with store.begin() as c:
        c.execute(
            proposal_decisions.update()
            .where(proposal_decisions.c.id == key)
            .values(created_at="2000-01-01T00:00:00+00:00")
        )
    assert sessions.queue_one(store)
    with store.connect() as c:
        assert c.execute(select(rows.c.status)).scalar() == "held"
    assert not sessions.process_one(store, advance=lambda *a, **kw: pytest.fail("expired work ran"))


def test_session_stop_api_enforces_boundary_and_never_creates_approval(
    store, owned_template, installed, client, headers
):
    key = approved(store, owned_template)
    assert sessions.queue_one(store)
    with store.connect() as c:
        before = c.execute(select(proposal_decisions)).all()
    assert (
        client.post(
            "/api/task-sessions/" + key + "/stop", json={"command_id": str(uuid4())}
        ).status_code
        == 403
    )
    response = client.post(
        "/api/task-sessions/" + key + "/stop", json={"command_id": str(uuid4())}, headers=headers
    )
    assert response.status_code == 200 and response.json()["status"] == "cancelled"
    with store.connect() as c:
        assert c.execute(select(proposal_decisions)).all() == before


def test_live_original_controller_is_waited_for_not_replayed(
    store, owned_template, installed, monkeypatch
):
    approved(store, owned_template)
    sessions.queue_one(store)
    with store.connect() as c:
        row = dict(c.execute(select(rows)).mappings().one())
    cfg = sessions._config(store, row)
    root = Path(json.loads(cfg.read_text())["run_root"])
    foreign = dict(pid=1234, start="1", boot="NON-MODEL")
    (root / "controller-owner.json").write_text(json.dumps(foreign))
    monkeypatch.setattr(sessions.execution, "alive", lambda owner: owner == foreign)
    assert sessions.process_one(
        store, advance=lambda *a, **kw: pytest.fail("live controller replay")
    )
    with store.connect() as c:
        assert c.execute(select(rows.c.status)).scalar() == "waiting_recovery"


def test_dead_controller_needs_verified_restoration_before_recovery(installed):
    root = installed / "artifacts/runs/task-lease-controlled"
    root.mkdir(parents=True)
    (root / "session-claimed.json").write_text("{}")
    state = installed / ".local/chronicle-primary-lease-task-lease-controlled"
    state.mkdir()
    evidence = state / "state.json"
    evidence.write_text(json.dumps(dict(restored=True)))
    sessions.reconcile_controller(root)
    assert not (root / "lease-controller-result.json").exists()
    value = dict(
        restored=True,
        original_config_verified=True,
        original_access_verified=True,
        temporary_access_rejected=True,
    )
    evidence.write_text(json.dumps(value))
    sessions.reconcile_controller(root)
    report = json.loads((root / "lease-controller-result.json").read_text())
    assert report["recovered_after_controller_interruption"] and report["provenance"][
        "state_sha256"
    ] == sessions.sha(evidence.read_bytes())
    assert "implementation" not in report


def test_unclaimed_readiness_failure_is_terminal_but_claimed_uncertainty_waits(
    store, owned_template, installed
):
    approved(store, owned_template)
    sessions.queue_one(store)

    def interrupted(engine, cfg, stop_event):
        root = Path(json.loads(cfg.read_text())["run_root"])
        (root / "session-claimed.json").write_text("{}")
        raise OSError("NON-MODEL interrupted delivery")

    assert sessions.process_one(store, advance=interrupted)
    with store.connect() as c:
        assert c.execute(select(rows.c.status)).scalar() == "waiting_recovery"


def test_site_catalog_cannot_select_session_fallback(store, owned_template):
    _, _, identity, value = owned_template
    folder = Path(__import__("os").environ["WORKBENCH_TASK_TEMPLATES_ROOT"])
    value["recipe"]["contract"].update(
        provider="current-session",
        model="codex-current-session",
        authoring="explicit-current-session",
    )
    raw = json.dumps(value, sort_keys=True).encode()
    key = sessions.sha(raw)
    (folder / (key + ".json")).write_bytes(raw)
    with pytest.raises(ValueError, match="local model only|Unsupported bounded Lua contract"):
        supported_tasks.load(key, store)
    assert all(item["id"] != key for item in supported_tasks.catalog(store)["items"])


def test_saved_stop_denies_new_grant_even_before_execution_request_exists(
    store, owned_template, installed
):
    from workbench.db import real_execution_grants, real_execution_requests, model_attempts

    key = approved(store, owned_template)
    sessions.queue_one(store)
    with store.connect() as c:
        approval = (
            c.execute(select(proposal_decisions).where(proposal_decisions.c.id == key))
            .mappings()
            .one()
        )
        historical = task_budget.permission(c, approval, require_current=False)
    sessions.cancel(store, key, str(uuid4()))
    with store.connect() as c:
        with pytest.raises(ValueError, match="stopped or expired"):
            task_budget.permission(c, approval)
        assert task_budget.permission(c, approval, require_current=False) == historical
        for table in (real_execution_grants, real_execution_requests, model_attempts):
            assert not c.execute(select(table)).first()


def test_stop_racing_grant_preflight_is_rechecked_inside_grant_transaction(
    store, owned_template, installed
):
    from types import SimpleNamespace
    from workbench.db import real_execution_grants

    key = approved(store, owned_template)
    sessions.queue_one(store)
    with store.connect() as c:
        row = c.execute(select(rows)).mappings().one()
        approval = (
            c.execute(select(proposal_decisions).where(proposal_decisions.c.id == key))
            .mappings()
            .one()
        )
        recipe = task_budget.permission(c, approval)["offer"]["snapshot"]["specification"]

    def preflight():
        sessions.cancel(store, key, str(uuid4()))
        return [dict(name="NON-MODEL controlled gate", status="passed")]

    runtime = SimpleNamespace(
        task_ref=row["snapshot"]["task"],
        recipe=recipe,
        execution_contract=recipe["contract"],
        grant_deadline=None,
        preflight=preflight,
        identity=lambda: dict(model="NON-MODEL"),
    )
    assert task_budget.authorize_one(store, runtime) is False
    with store.connect() as c:
        assert not c.execute(select(real_execution_grants)).first()


def test_technical_hold_is_not_an_owner_budget_withdrawal(store, owned_template, installed):
    key = approved(store, owned_template)
    sessions.queue_one(store)
    with store.begin() as c:
        c.execute(
            rows.update()
            .where(rows.c.id == key)
            .values(status="held", message="NON-MODEL temporary preparation failure")
        )
    with store.connect() as c:
        approval = (
            c.execute(select(proposal_decisions).where(proposal_decisions.c.id == key))
            .mappings()
            .one()
        )
        assert task_budget.permission(c, approval)["decision_id"] == key
    # No grant/attempt is created by this read-only permission check.
    from workbench.db import real_execution_grants

    with store.connect() as c:
        assert not c.execute(select(real_execution_grants)).first()


def test_worker_restart_waits_for_existing_readiness_then_continues_without_replay(
    store, owned_template, installed, monkeypatch
):
    approved(store, owned_template)
    sessions.queue_one(store)
    with store.connect() as c:
        row = dict(c.execute(select(rows)).mappings().one())
    cfg = sessions._config(store, row)
    value = json.loads(cfg.read_text())
    root = Path(value["run_root"])
    real = root / "real-run"
    real.mkdir()
    (real / "recipe.json").write_text("NON-MODEL recipe")
    (root / "preparation-controller.log").write_text("NON-MODEL running readiness")
    owner = dict(pid=1234, start="1", boot="NON-MODEL")
    (root / "preparation-owner.json").write_text(
        json.dumps(
            dict(owner=owner, recipe_sha256=sessions.sha((real / "recipe.json").read_bytes()))
        )
    )
    monkeypatch.setattr(sessions.execution, "alive", lambda value: value == owner)
    assert sessions.process_one(store, advance=lambda *a, **kw: pytest.fail("readiness replay"))
    with store.connect() as c:
        assert c.execute(select(rows.c.status)).scalar() == "waiting_recovery"
    (real / "preparation-completed.json").write_text("{}")
    calls = []

    def finished(engine, cfg, stop_event):
        calls.append(str(cfg))
        (root / "session-finished.json").write_text(
            json.dumps(
                dict(
                    task=value["task"],
                    status="verified",
                    message="NON-MODEL existing handler result",
                )
            )
        )

    assert sessions.process_one(store, advance=finished) and len(calls) == 1
    assert not sessions.process_one(store, advance=finished)
