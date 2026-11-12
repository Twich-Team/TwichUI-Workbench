"""One actual combined local result after controlled model-authored children.

Lua execution and frozen handoffs are real; confinement/provider telemetry is
controlled. No service, real model, external publication or game is activated.
"""

import pytest
from sqlalchemy import select

from workbench import feature_verification, worker, real_execution
from workbench.db import (
    planning_jobs,
    real_execution_requests,
    real_execution_grants,
    model_attempts,
)
from test_feature_check_preparation import at_independent_preparation
from test_everyday import installed as installed


@pytest.mark.parametrize('pending', [False, True])
def test_dead_local_preparation_owner_never_dispatches_pending_or_partial_checks(
    client, headers, store, installed, monkeypatch, pending,
):
    from workbench import real_confine, work_controls
    monkeypatch.setattr(real_confine, 'root', lambda: '/sys/fs/cgroup/workbench-real-CONTROLLED-local-feature')
    identity, _, _, tick = at_independent_preparation(client, headers, store, monkeypatch)
    assert tick() and tick()

    def crash(*args, **kwargs):
        raise SystemExit('CONTROLLED worker exit before local dispatch')

    if pending:
        dispatch = real_execution.process_one
        def before_local_dispatch(engine, runtime=None, **kwargs):
            if getattr(runtime, 'operation_kind', None) == feature_verification.KIND:
                crash()
            return dispatch(engine, runtime=runtime, **kwargs)
        monkeypatch.setattr(real_execution, 'process_one', before_local_dispatch)
    else:
        def partial(self):
            self.oracle_source.unlink()
            crash()
        monkeypatch.setattr(feature_verification.Runtime, 'prepare', partial)
    with pytest.raises(SystemExit):
        tick()
    monkeypatch.setattr(real_execution, 'alive', lambda identity: False)
    work_controls.save(store, work_controls.WorkControlInput(revision=work_controls.read(store).revision, paused=True))
    # No parsing of partial runtime files, replay, provider or local check call.
    monkeypatch.setattr(feature_verification, 'Runtime', crash)
    assert worker.tick(store)
    with store.connect() as c:
        job = c.execute(select(planning_jobs).where(planning_jobs.c.kind == 'feature_execution')).mappings().one()
        assert job['status'] == 'failed' and job['payload']['phase'] == 'held'
        assert len(job['payload']['completed_steps']) == 2
        rows = list(c.execute(select(real_execution_requests)).mappings())
        assert len(rows) == 2 + int(pending)
        if pending:
            local = next(r for r in rows if r['status'] == 'blocked')
            assert not local['fence'] and not local['started_at'] and local['result'] is None
            grant = c.execute(select(real_execution_grants).where(real_execution_grants.c.id == local['grant_id'])).mappings().one()
            assert grant['revoked_at']
    assert not worker.tick(store)
    assert client.get('/api/features/' + identity).json()['state'] != 'completed'


def test_failed_local_preparation_settles_exact_claim_without_dispatch_or_replay(
    client, headers, store, installed, monkeypatch,
):
    from workbench.db import task_sessions
    identity, decision, _, tick = at_independent_preparation(client, headers, store, monkeypatch)
    assert tick() and tick()
    calls = []

    def unavailable(self):
        calls.append(str(self.root))
        raise ValueError('CONTROLLED unavailable original local-check workspace')

    monkeypatch.setattr(feature_verification.Runtime, 'prepare', unavailable)
    assert tick()
    with store.connect() as c:
        job = c.execute(select(planning_jobs).where(planning_jobs.c.kind == 'feature_execution')).mappings().one()
        parent = c.execute(select(task_sessions).where(task_sessions.c.id == decision)).mappings().one()
        assert job['status'] == 'failed' and job['payload']['phase'] == 'held'
        assert job['payload']['feature_verification_claim']['root'] == calls[0]
        assert len(job['payload']['completed_steps']) == 2
        assert parent['status'] == 'held'
        assert len(c.execute(select(real_execution_requests)).all()) == 2
    assert not tick() and len(calls) == 1
    assert client.get('/api/features/' + identity).json()['state'] != 'completed'


def test_combined_original_and_candidate_checks_have_no_fabricated_model_usage(
    client,
    headers,
    store,
    installed,
    monkeypatch,
    tmp_path,
):
    from workbench import real_confine

    monkeypatch.setattr(
        real_confine, "root", lambda: "/sys/fs/cgroup/workbench-real-CONTROLLED-local-feature"
    )
    identity, decision, planner, tick = at_independent_preparation(
        client, headers, store, monkeypatch
    )
    assert tick() and tick()  # Blind program and separate controlled self-review.
    assert tick()  # The real existing dispatcher owns local checks; no model.
    with store.connect() as c:
        job = (
            c.execute(select(planning_jobs).where(planning_jobs.c.kind == "feature_execution"))
            .mappings()
            .one()
        )
        assert job["payload"]["phase"] == "prepare_scopes", job["message"]
        assert len(job["payload"]["completed_steps"]) == 2
        assert len(job["payload"]["completed_checks"]) == 1
        combined_id = job["payload"]["final_verification_execution"]
        row = (
            c.execute(
                select(real_execution_requests).where(real_execution_requests.c.id == combined_id)
            )
            .mappings()
            .one()
        )
        grant = (
            c.execute(
                select(real_execution_grants).where(real_execution_grants.c.id == row["grant_id"])
            )
            .mappings()
            .one()
        )
        assert row["status"] == "checked", row["result"]
        result = feature_verification.checked(c, row, grant)
        assert result["inference_ran"] is False and result["usage"] is None
        assert len(result["contributions"]) == 2
        assert not c.execute(
            select(model_attempts).where(model_attempts.c.execution_id == combined_id)
        ).first()
        assert len(c.execute(select(real_execution_requests)).all()) == 3
        assert grant["snapshot"]["contract"]["provider"] == "none"
        assert (
            grant["snapshot"]["contract"]["base"]
            == grant["snapshot"]["feature_verification"]["original_base"]
        )
        assert result["manifest"]["checked_input"]["commit"] != result["manifest"]["base"]
        assert result["original_checks"][1]["status"] == "fail"
        assert all(check["status"] == "pass" for check in result["checks"])
        assert all(
            part["usage"]["values"]["completion_tokens"] == 10 for part in result["contributions"]
        )
    before = client.get("/api/features/" + identity).json()
    assert before["completed_steps"] == 3 and before["total_steps"] == 4
    assert before["state"] != "completed"
    assert not real_execution.process_one(
        store, runtime=feature_verification.Runtime(grant["snapshot"]["recovery"]["root"])
    )
    # No child draft and no fabricated final feature completion.
    assert worker.tick(store, planning_adapter=planner)
    assert worker.tick(store, planning_adapter=planner)  # Exact final delivery scope admitted.
    assert worker.tick(store, planning_adapter=planner)  # Actual local Git draft bundle.
    from workbench import real_publication
    from workbench.real_publication import DeliveryHeld
    from workbench.db import real_publications
    from pathlib import Path
    import json
    import pytest

    with store.connect() as c:
        job = (
            c.execute(select(planning_jobs).where(planning_jobs.c.kind == "feature_execution"))
            .mappings()
            .one()
        )
        assert job["status"] == "completed" and job["payload"]["phase"] == "delivery_ready", job[
            "message"
        ]
        assert not c.execute(select(real_publications)).first()
        bundle = json.loads(Path(job["payload"]["feature_delivery"]["bundle"]).read_text())
        assert (
            bundle["local_commit"] and bundle["patch_sha256"] == result["manifest"]["patch_sha256"]
        )
        assert len(c.execute(select(real_execution_requests)).all()) == 3
        for child in job["payload"]["completed_steps"]:
            child_row, child_grant = real_publication._load(c, child["execution_id"])
            with pytest.raises(DeliveryHeld, match="never as separate drafts"):
                real_publication._saved_policy(c, child_row, child_grant)
    review = real_publication.review_checked(store, combined_id)
    assert review["preview"]["title"] == "Handle absent and blank names safely"
    assert 'Parent feature: ' + identity in review['preview']['body']
    assert review["base"] == result["manifest"]["base"]
    assert review["candidate_id"] == result["candidate_id"]
    with store.connect() as c:
        _, _, _, snapshot = real_publication._saved_inputs(store, combined_id)
        assert "feature_artifact" in snapshot and "task_budget" not in snapshot
    shown = client.get("/api/features/" + identity).json()
    assert (
        shown["state"] == "ready_to_review" and shown["integrated_verification"] == "passed_offline"
    )
    assert (
        shown["feature_result"]["execution_id"] == combined_id
        and shown["feature_result"]["draft_url"] is None
    )
    assert shown["completed_steps"] == 3 and shown["total_steps"] == 4
    # Controlled publication owner only: one actual artifact, no external write
    # and no fake GitHub URL in the feature view.
    root = tmp_path / "CONTROLLED-checked-publication"
    root.mkdir(mode=0o700)
    approval = dict(
        execution_id=combined_id, binding=review["binding"], owner_instruction_sha256="c" * 64
    )
    target = root / "checked-approval.json"
    target.write_text(json.dumps(approval))
    target.chmod(0o600)
    assert real_publication.authorize_checked(store, combined_id, root)
    assert not real_publication.authorize_checked(store, combined_id, root)
    from test_task_to_pr import ControlledRemote as Publisher

    class ControlledPublisher(Publisher):
        def prepare(self, snapshot, result):
            self.prepares += 1
            assert snapshot["feature_artifact"]["candidate_id"] == result["candidate_id"]
            return bundle["local_commit"]

    publisher = ControlledPublisher()
    publisher.base = review["base"]
    for _ in range(6):
        real_publication.process_one(
            store, publisher=publisher, execution_id=combined_id, checked_root=root
        )
    assert publisher.pushes == publisher.creates == 1
    with store.connect() as c:
        publication = c.execute(select(real_publications)).mappings().one()
        assert (
            publication["status"] == "published" and publication["state"]["source"] == "controlled"
        )
        assert publication["state"]["url"] is None
        assert not c.execute(
            select(model_attempts).where(model_attempts.c.execution_id == combined_id)
        ).first()
    final = client.get("/api/features/" + identity).json()
    assert not final["feature_result"]["published"] and final["feature_result"]["draft_url"] is None


def test_sealed_local_completion_recovers_while_paused_without_repeating_a_check(
    client,
    headers,
    store,
    installed,
    monkeypatch,
):
    import pytest
    from workbench import real_confine, work_controls

    monkeypatch.setattr(
        real_confine, "root", lambda: "/sys/fs/cgroup/workbench-real-CONTROLLED-local-feature"
    )
    identity, _, _, tick = at_independent_preparation(client, headers, store, monkeypatch)
    assert tick() and tick()
    calls = []
    original = feature_verification.Runtime.execute

    def counted(self, *args):
        calls.append(1)
        return original(self, *args)

    monkeypatch.setattr(feature_verification.Runtime, "execute", counted)
    with monkeypatch.context() as crash:

        def interrupted(*args):
            raise SystemExit("CONTROLLED interruption after native completion seal")

        crash.setattr(real_execution, "cleanup_confirmed", interrupted)
        with pytest.raises(SystemExit):
            tick()
    state = work_controls.read(store)
    work_controls.save(store, work_controls.WorkControlInput(revision=state.revision, paused=True))
    assert worker.tick(store)  # Native seal/cleanup reconciliation only; global pause remains.
    assert calls == [1] and work_controls.read(store).paused
    with store.connect() as c:
        job = (
            c.execute(select(planning_jobs).where(planning_jobs.c.kind == "feature_execution"))
            .mappings()
            .one()
        )
        assert (
            job["payload"]["phase"] == "prepare_scopes"
            and len(job["payload"]["completed_checks"]) == 1
        )
        combined = job["payload"]["final_verification_execution"]
        row = (
            c.execute(
                select(real_execution_requests).where(real_execution_requests.c.id == combined)
            )
            .mappings()
            .one()
        )
        grant = (
            c.execute(
                select(real_execution_grants).where(real_execution_grants.c.id == row["grant_id"])
            )
            .mappings()
            .one()
        )
        assert row["status"] == "checked" and grant["revoked_at"]
        assert not c.execute(
            select(model_attempts).where(model_attempts.c.execution_id == combined)
        ).first()
    assert not worker.tick(store)
    assert client.get("/api/features/" + identity).json()["completed_steps"] == 3


def test_stopping_a_local_combined_check_preserves_children_and_cannot_publish(
    client,
    headers,
    store,
    installed,
    monkeypatch,
):
    from uuid import uuid4
    from workbench import real_confine
    from test_feature_execution import controlled_check

    monkeypatch.setattr(
        real_confine, "root", lambda: "/sys/fs/cgroup/workbench-real-CONTROLLED-local-feature"
    )
    identity, decision, _, tick = at_independent_preparation(client, headers, store, monkeypatch)
    assert tick() and tick()
    stopped = []

    def stop_during_check(self, argv, cwd, **kwargs):
        result = controlled_check(self, argv, cwd, **kwargs)
        if not stopped:
            stopped.append(1)
            saved = client.post(
                "/api/features/" + identity + "/runs/" + decision + "/stop",
                headers=headers,
                json=dict(command_id=str(uuid4())),
            )
            assert saved.status_code == 200, saved.text
        return result

    monkeypatch.setattr(feature_verification.Runtime, "check", stop_during_check)
    assert tick()
    with store.connect() as c:
        rows = list(
            c.execute(
                select(real_execution_requests).order_by(real_execution_requests.c.created_at)
            ).mappings()
        )
        assert [r["status"] for r in rows] == ["checked", "checked", "cancelled"]
        assert rows[-1]["result"]["usage"] is None and rows[-1]["result"]["inference_ran"] is False
        assert not c.execute(
            select(model_attempts).where(model_attempts.c.execution_id == rows[-1]["id"])
        ).first()
        job = (
            c.execute(select(planning_jobs).where(planning_jobs.c.kind == "feature_execution"))
            .mappings()
            .one()
        )
        assert job["status"] == "failed" and not job["payload"].get("completed_checks")
    view = client.get("/api/features/" + identity).json()
    assert view["completed_steps"] == 2 and view["feature_result"] is None
    assert view["run"]["cancel_requested"] and not view["run"]["can_stop"]
    assert not tick()


def test_original_only_rejection_gets_bounded_blind_correction_without_replaying_children(
    client,
    headers,
    store,
    installed,
    monkeypatch,
):
    from workbench import real_confine, feature_checks
    from workbench.regression_evidence import lua
    from test_feature_check_preparation import IndependentPlanner
    from pathlib import Path
    import json

    monkeypatch.setattr(
        real_confine, "root", lambda: "/sys/fs/cgroup/workbench-real-CONTROLLED-local-feature"
    )
    identity, _, _, tick = at_independent_preparation(client, headers, store, monkeypatch)

    class BadThenCorrect(IndependentPlanner):
        bad = False

        def generate(self, packet, schema, expected_model):
            value, model, basis = super().generate(packet, schema, expected_model)
            if schema is feature_checks.Response and not self.bad:
                self.bad = True
                for phase in value["program"]["phases"]:
                    phase["code"] = "return " + lua(phase["expected"])
            return value, model, basis

    planner = BadThenCorrect()
    assert tick(planner) and tick(planner)
    assert tick()  # Constant observations pass original, so they cannot qualify.
    with store.connect() as c:
        job = (
            c.execute(select(planning_jobs).where(planning_jobs.c.kind == "feature_execution"))
            .mappings()
            .one()
        )
        assert job["payload"]["phase"] == "prepare_independent_checks", job["message"]
        assert job["payload"]["feature_verification_attempt"] == 2
        assert (
            len(job["payload"]["completed_steps"]) == 2
            and len(job["payload"]["verification_history"]) == 1
        )
        failure = job["payload"]["verification_history"][0]
        failed_row = (
            c.execute(
                select(real_execution_requests).where(
                    real_execution_requests.c.id == failure["execution_id"]
                )
            )
            .mappings()
            .one()
        )
        failed_grant = (
            c.execute(
                select(real_execution_grants).where(
                    real_execution_grants.c.id == failed_row["grant_id"]
                )
            )
            .mappings()
            .one()
        )
        root = Path(failed_grant["snapshot"]["recovery"]["root"])
        assert failed_row["status"] == "failed" and failed_row["result"]["usage"] is None
        assert not (root / "combined.patch").exists()
        location = json.loads((root / "workspace-location.json").read_text())["path"]
        from workbench.real_workspace import tree

        assert tree(Path(location)) == json.loads((root / "base-manifest.json").read_text())
        assert (
            job["payload"]["independent_check_preparation"]["feedback"]["kind"]
            == "original-only-observation-feedback"
        )
    assert tick(planner) and tick(planner)
    assert tick()
    with store.connect() as c:
        rows = list(
            c.execute(
                select(real_execution_requests).order_by(real_execution_requests.c.created_at)
            ).mappings()
        )
        assert [r["status"] for r in rows] == ["checked", "checked", "failed", "checked"]
        assert rows[-1]["result"]["usage"] is None
        job = (
            c.execute(select(planning_jobs).where(planning_jobs.c.kind == "feature_execution"))
            .mappings()
            .one()
        )
        assert (
            len(job["payload"]["completed_steps"]) == 2
            and len(job["payload"]["completed_checks"]) == 1
        )
        attempts = list(
            c.execute(
                select(model_attempts).where(
                    model_attempts.c.purpose == "feature_check_preparation"
                )
            ).mappings()
        )
        assert len(attempts) == 4 and all(
            a["finished_at"] and a["usage"]["values"]["completion_tokens"] == 10 for a in attempts
        )
        assert not c.execute(
            select(model_attempts).where(
                model_attempts.c.execution_id.in_([rows[-1]["id"], rows[-2]["id"]])
            )
        ).first()
    assert client.get("/api/features/" + identity).json()["completed_steps"] == 3
