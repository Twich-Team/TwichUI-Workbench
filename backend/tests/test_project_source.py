"""Fresh source epochs retain old task/result bindings and never grant edits."""

import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from workbench import worker, regression_preparation as source
from workbench.db import planning_jobs, model_attempts
from workbench.planning_provider import LocalProvider
from test_bug_workflow import enable_bugs, report
from test_bug_intent_workflow import scope
from test_everyday import installed, write_policy  # noqa:F401
from test_verification_profiles import git


def new_snapshot(root):
    registry = root / ".git/workbench-source"
    (registry / "snapshots").mkdir(mode=0o700, parents=True)
    pending = registry / "snapshots/new"
    git(root, "clone", "--quiet", "--no-hardlinks", str(root), str(pending))
    git(pending, "remote", "set-url", "origin", "https://github.com/Twich-Team/TwichUI.git")
    (pending / "!!!TwichUI/new_feature.lua").write_text("return {version = 2}\n")
    git(pending, "add", ".")
    git(
        pending,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@invalid",
        "commit",
        "-qm",
        "Later project feature",
    )
    base = git(pending, "rev-parse", "HEAD").strip()
    pending.rename(registry / "snapshots" / base)
    path = registry / "current.json"
    path.write_text(json.dumps(dict(version=1, repository="Twich-Team/TwichUI", base=base)))
    path.chmod(0o600)
    return base


@pytest.mark.usefixtures("installed")
def test_original_report_refreshes_to_new_committed_source_without_reusing_a_plan_or_resetting_usage(
    client, headers, store, monkeypatch
):
    from workbench import project_source

    root = enable_bugs(monkeypatch)
    config = Path(os.environ["WORKBENCH_PREPARATION_CONFIG"])
    settings = json.loads(config.read_text())
    settings["bug_plan_review"] = "source-grounded-intent-v1"
    write_policy(config, settings)
    calls = []

    def generate(self, packet, schema, expected_model):
        calls.append(packet)
        self.diagnostics = dict(
            actual_model=expected_model,
            generation_state="finished",
            accounting_state="complete",
            finish_reason="stop",
            usage=dict(
                units="tokens", values=dict(prompt_tokens=40, completion_tokens=10, total_tokens=50)
            ),
        )
        return (
            dict(
                finding="The inspected source supports a bounded repair hypothesis.", plan=scope()
            ),
            expected_model,
            "local",
        )

    monkeypatch.setattr(LocalProvider, "generate", generate)
    original = report()
    item = client.post("/api/intake", headers=headers, json=original).json()
    identity = item["id"]
    assert worker.tick(store)
    before = client.get("/api/intake/" + identity).json()
    old_base = before["proposals"][0]["task_contract"]["resolved_base"]
    # Reproduce the older display overflow without rewriting the stored result.
    with store.begin() as c:
        row = (
            c.execute(select(planning_jobs).where(planning_jobs.c.intake_id == identity))
            .mappings()
            .one()
        )
        payload = dict(row["payload"])
        payload["result"] = {
            **payload["result"],
            "evidence_ids": [f"bug-source-{n}" for n in range(1, 20)],
        }
        c.execute(
            planning_jobs.update().where(planning_jobs.c.id == row["id"]).values(payload=payload)
        )
    assert client.get("/api/intake/" + identity).status_code == 200
    with store.connect() as c:
        assert (
            len(
                c.execute(
                    select(planning_jobs.c.payload).where(planning_jobs.c.id == row["id"])
                ).scalar_one()["result"]["evidence_ids"]
            )
            == 19
        )
    base = new_snapshot(root)
    assert base != old_base and source.repository()[1] == base
    assert source.repository(retained_base=old_base)[1] == old_base
    monkeypatch.setattr(project_source, "ensure_current", lambda: project_source.current())
    current = client.get("/api/intake/" + identity).json()
    offered = current["project_source"]
    assert offered["stale"] and offered["current_base"] == base
    proposal = current["proposals"][0]
    decision = dict(
        command_id=str(uuid4()),
        proposal_id=proposal["id"],
        binding=proposal["binding"],
        scope_revision=proposal["scope_revision"],
        action="approve",
        note="Cannot approve stale source",
        expectation_review=proposal["task_contract"]["bug_intent"]["review_digest"],
    )
    assert (
        client.post(
            "/api/intake/" + identity + "/decisions", headers=headers, json=decision
        ).status_code
        == 409
    )
    command = dict(
        command_id=str(uuid4()),
        revision=current["revision"],
        text="Use current project source for the original report; no edits approved.",
        renew_operation=True,
        continue_investigation=True,
        refresh_source=True,
        source_refresh_binding=offered["refresh_binding"],
    )
    endpoint = "/api/intake/" + identity + "/clarification"
    saved = client.post(endpoint, headers=headers, json=command)
    assert saved.status_code == 200, saved.text
    assert client.post(endpoint, headers=headers, json=command).status_code == 200
    with store.connect() as c:
        newest = (
            c.execute(
                select(planning_jobs)
                .where(planning_jobs.c.intake_id == identity)
                .order_by(planning_jobs.c.revision.desc())
            )
            .mappings()
            .first()
        )
        renewal = newest["payload"]["bug_followup"]
        assert renewal["budget_version"] == 5 and renewal["consumed_requests"] == 1
        assert (
            renewal["known_prior_usage"]["total_tokens"] == 50
            and "retained_source_packet" not in renewal
        )
    assert worker.tick(store)
    after = client.get("/api/intake/" + identity).json()
    assert after["original"] == before["original"] and len(after["proposals"]) == 2
    assert after["proposals"][0]["task_contract"]["resolved_base"] == base
    assert after["proposals"][1] == before["proposals"][0]
    assert not after["project_source"]["stale"] and not after["decisions"] and not after["tasks"]
    assert len(calls) == 2 and calls[0]["base"] == old_base and calls[1]["base"] == base
    with store.connect() as c:
        assert (
            len(c.execute(select(model_attempts).where(model_attempts.c.task_id == identity)).all())
            == 2
        )


def test_source_root_rejects_unowned_or_symlink_snapshot_selection(tmp_path, monkeypatch):
    from workbench import project_source

    root = tmp_path / "repo"
    (root / ".git/workbench-source/snapshots").mkdir(parents=True)
    monkeypatch.setenv("WORKBENCH_RESEARCH_ROOT", str(root))
    target = root / ".git/workbench-source/snapshots" / ("a" * 40)
    target.symlink_to(tmp_path, target_is_directory=True)
    meta = root / ".git/workbench-source/current.json"
    meta.write_text(json.dumps(dict(version=1, repository="Twich-Team/TwichUI", base="a" * 40)))
    meta.chmod(0o600)
    with pytest.raises(ValueError):
        project_source.active_root()


def test_a_moved_project_does_not_reuse_an_unexecuted_old_approval(
    store, client, headers, tmp_path, monkeypatch
):
    from test_reviewed_offers import prepared
    from workbench.task_budget import _reviewed_spec

    ref, runtime, root = prepared(store, client, headers, tmp_path, monkeypatch)
    new_snapshot(root)
    with store.connect() as connection:
        with pytest.raises(ValueError, match="differs from the reviewed contract"):
            _reviewed_spec(connection, ref, runtime)


@pytest.mark.usefixtures("installed")
def test_failed_source_check_waits_without_starting_a_model_or_spinning_retries(
    client, headers, store, monkeypatch
):
    from workbench import project_source

    enable_bugs(monkeypatch)
    attempts = []

    def unavailable():
        attempts.append(1)
        raise ValueError("Controlled unavailable upstream")

    monkeypatch.setattr(project_source, "ensure_current", unavailable)
    monkeypatch.setattr(
        LocalProvider,
        "generate",
        lambda *args, **kwargs: pytest.fail("A model started with unverified source"),
    )
    item = client.post("/api/intake", headers=headers, json=report()).json()
    worker.tick(store)
    state = client.get("/api/intake/" + item["id"]).json()
    assert (
        state["status"] == "queued"
        and "No model request has started" in state["jobs"][0]["message"]
    )
    worker.tick(store)
    assert attempts == [1]
    with store.connect() as connection:
        assert not connection.execute(
            select(model_attempts).where(model_attempts.c.task_id == item["id"])
        ).first()


def test_interrupted_source_download_preserves_current_pointer_and_removes_only_owned_temporary(
    tmp_path, monkeypatch
):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "controlled_source_sync", Path(__file__).parents[2] / "scripts/project_source_sync.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tmp_path / "source-state"
    root.mkdir()
    snapshots = root / "snapshots"
    snapshots.mkdir()
    old = snapshots / ("a" * 40)
    old.mkdir()
    (old / "retained").write_text("original")
    current = root / "current.json"
    current.write_text("UNCHANGED CURRENT POINTER\n")

    def fail(root, *args, **kwargs):
        if args[0] == "ls-remote":
            return (("b" * 40) + "\trefs/heads/main\n").encode()
        if args[0] == "clone":
            Path(args[-1]).mkdir()
            (Path(args[-1]) / "partial").write_text("partial download")
            raise ValueError("Controlled failed download")
        pytest.fail("Unexpected operation")

    monkeypatch.setattr(module, "git", fail)
    with pytest.raises(ValueError, match="Controlled failed"):
        module.sync(root)
    assert (
        current.read_text() == "UNCHANGED CURRENT POINTER\n"
        and (old / "retained").read_text() == "original"
    )
    assert list(snapshots.iterdir()) == [old]


def test_source_unavailability_does_not_hide_history_or_affect_unrelated_demo_approvals(
    tmp_path, monkeypatch
):
    from workbench import project_source

    monkeypatch.setenv("WORKBENCH_RESEARCH_ROOT", str(tmp_path / "unavailable"))
    row = dict(payload=dict(kind="issue", local_planning="qwen-once"))
    assert project_source.refresh_view(row, []) is None
    project_source.require_current(None)
    with pytest.raises(ValueError, match="approval is held"):
        project_source.require_current(dict(resolved_base="a" * 40))
