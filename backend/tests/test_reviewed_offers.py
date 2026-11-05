"""Actual reviewed records + actual Lua, controlled telemetry; no model/activation.

The bug contract/oracles/controls are trusted task DATA, deliberately prepared by
hand. No task template or source registration is used. Real confinement and PR
publication remain separately gated and are not claimed by these tests.
"""

import copy
import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from workbench import (
    intake,
    owner_task,
    planning_worker,
    real_recipes,
    task_budget,
    task_execution,
    task_session,
)
from workbench import regression_preparation as preparation, verification_profiles as profiles
from workbench.db import (
    backlog_items,
    intakes,
    planning_jobs,
    proposals,
    proposal_decisions,
    real_execution_grants,
    real_execution_requests,
    real_publications,
    model_attempts,
    task_sessions,
    task_run_offers,
    write_transaction,
)
from workbench.real_workspace import Runtime, durable, sha, PRELUDE
from test_verification_profiles import (
    ControlledLuaRuntime,
    git,
    controls,
    boundary,
    PRODUCTION,
    TEST,
    CORRECT,
    BUGGY,
    BASE_TEST,
    REGRESSION_ORACLE,
    BUG_ORACLE,
    GOOD_TEST,
)


class PreparedLuaRuntime(ControlledLuaRuntime):
    expected_suite = Runtime.expected_suite


def source(tmp_path, profile):
    root = tmp_path / "source"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "remote", "add", "origin", "https://github.com/Twich-Team/TwichUI.git")
    (root / "!!!TwichUI").mkdir()
    (root / "tests").mkdir()
    (root / PRODUCTION).write_text(CORRECT if profile == profiles.REGRESSION else BUGGY)
    (root / TEST).write_text(BASE_TEST)
    (root / "tests/harness.lua").write_text("-- Unchanged controlled framework\n")
    (root / "tests/run.sh").write_text("""#!/bin/bash
set -e
cd tests
for t in test_*.lua; do
 lua5.1 -e 'require("bit");normalize=dofile("../!!!TwichUI/normalize.lua")' "$t"
 echo "ok   $t"
done
for t in test_sv_inspect.py test_release_tools.py test_wizard_art.py; do
 python3 "$t"
 echo "ok   $t"
done
# PASSED / ERROR:
echo '4 passed, 0 failed'
""")
    for name in ("test_sv_inspect.py", "test_release_tools.py", "test_wizard_art.py"):
        (root / "tests" / name).write_text("assert 1 + 1 == 2\n")
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@invalid",
        "commit",
        "-qm",
        "Controlled base",
    )
    return root


def prepare_review(store, client, headers, tmp_path, monkeypatch, profile=profiles.REGRESSION):
    root = source(tmp_path, profile)
    monkeypatch.setenv("WORKBENCH_RESEARCH_ROOT", str(root))
    is_test = profile == profiles.REGRESSION
    value = dict(
        request_id=str(uuid4()),
        kind="feature" if is_test else "issue",
        title="Cover case normalization" if is_test else "Repair case normalization",
        description="Trim surrounding spaces and lowercase names.",
        visual_requirement="nonvisual",
    )
    # Regression uses ordinary deterministic intake. Bug fixes have no automatic
    # preparer yet: supply a controlled trusted typed contract through that same
    # existing planning-job/proposal owner before human review.
    if is_test:
        value["regression_tests"] = dict(
            test_paths=[TEST], acceptance=["Names with spaces and capitals normalize to lowercase."]
        )
    response = client.post("/api/intake", headers=headers, json=value)
    assert response.status_code == 200, response.text
    identity = response.json()["id"]
    original = {
        **value,
        "regression_tests": dict(
            test_paths=[TEST], acceptance=["Names with spaces and capitals normalize to lowercase."]
        ),
    }
    if is_test:
        assert planning_worker.process_one(store)
    contract = preparation.prepare(original)
    if not is_test:
        contract.update(
            category="lua-offline-bugfix",
            verification_profile=profiles.select("lua-offline-bugfix"),
            change_policy="bounded-existing-lua-files",
            writable_paths=[PRODUCTION],
        )
        contract["verification_commands"] = [
            dict(argv=["luac5.1", "-p", PRODUCTION], cwd=".", purpose="Syntax", status="not_run"),
            dict(
                argv=["lua5.1", "-e", PRELUDE, Path(TEST).name],
                cwd="tests",
                purpose="Focused",
                status="not_run",
            ),
            dict(argv=["bash", "tests/run.sh"], cwd=".", purpose="Full suite", status="not_run"),
        ]
    oid = git(root, "rev-parse", "HEAD:" + PRODUCTION).strip()
    contract["evidence"].append(
        dict(path=PRODUCTION, git_blob=oid, sha256=sha((root / PRODUCTION).read_bytes()))
    )
    with write_transaction(store) as c:
        job = (
            c.execute(select(planning_jobs).where(planning_jobs.c.intake_id == identity))
            .mappings()
            .one()
        )
        p = c.execute(select(proposals).where(proposals.c.intake_id == identity)).mappings().first()
        proposal_id = p["id"] if p else str(uuid4())
        content = preparation.proposal_content(original, contract)
        if not is_test:
            content.update(
                summary=value["title"],
                value="Correct name normalization.",
                scope=["Correct normalization in " + PRODUCTION + "."],
                exclusions=[
                    "Shared framework changes, unrelated behavior, activation and publication."
                ],
            )
        if p:
            c.execute(
                proposals.update().where(proposals.c.id == proposal_id).values(content=content)
            )
        else:
            c.execute(
                proposals.insert().values(
                    id=proposal_id,
                    intake_id=identity,
                    revision=1,
                    scope_revision=1,
                    content=content,
                    model="controlled-trusted-preparation",
                    basis="preparation",
                    created_at=intake.now(),
                )
            )
        c.execute(
            planning_jobs.update()
            .where(planning_jobs.c.id == job["id"])
            .values(
                status="completed",
                finished_at=intake.now(),
                payload={
                    **job["payload"],
                    "prepared_proposal_id": proposal_id,
                    "task_contract": contract,
                },
            )
        )
        c.execute(
            intakes.update().where(intakes.c.id == identity).values(status="completed", revision=1)
        )
    p = client.get("/api/intake/" + identity).json()["proposals"][0]
    decision_id = str(uuid4())
    decision = dict(
        command_id=decision_id,
        proposal_id=p["id"],
        binding=p["binding"],
        scope_revision=p["scope_revision"],
        action="approve",
        note="CONTROLLED scope-only review; no run permission.",
    )
    assert (
        client.post(
            "/api/intake/" + identity + "/decisions", headers=headers, json=decision
        ).status_code
        == 200
    )
    assert planning_worker.apply_one(store, command_id=decision_id)
    # Both use the existing single-task materializer, not a new task registration.
    with write_transaction(store) as c:
        job = (
            c.execute(
                select(planning_jobs).where(
                    planning_jobs.c.intake_id == identity, planning_jobs.c.kind == "decomposition"
                )
            )
            .mappings()
            .one()
        )
        data = planning_worker.materialize_single_task(
            c, intake.require(c, intakes, identity), job, "controlled-trusted-preparation"
        )
        c.execute(
            planning_jobs.update()
            .where(planning_jobs.c.id == job["id"])
            .values(
                status="completed",
                finished_at=intake.now(),
                model="controlled-trusted-preparation",
                payload={**job["payload"], "decomposition": data},
            )
        )
        task = (
            c.execute(
                select(backlog_items).where(
                    backlog_items.c.intake_id == identity, backlog_items.c.kind == "task"
                )
            )
            .mappings()
            .one()
        )
    return dict(intake_id=identity, task_id=task["id"], approval_id=decision_id), root


def prepared(
    store, client, headers, tmp_path, monkeypatch, profile=profiles.REGRESSION, certify=True
):
    ref, source_root = prepare_review(store, client, headers, tmp_path, monkeypatch, profile)
    oracle = REGRESSION_ORACLE if profile == profiles.REGRESSION else BUG_ORACLE
    with store.connect() as c:
        spec = real_recipes.from_review(
            c,
            ref,
            oracle=oracle,
            behavior_id="normalize",
            read_paths=[PRODUCTION, TEST],
            focused_test=TEST,
            identity=dict(name="Controlled author", email="fixture@invalid"),
        )
    root = tmp_path / "certified"
    root.mkdir()
    durable(root / "recipe.json", json.dumps(spec, sort_keys=True).encode())
    durable(root / "recipe-oracle.lua", oracle.encode())
    runtime = PreparedLuaRuntime(root)
    runtime.prepare()
    if certify:
        profiles.prepare(runtime, controls(runtime) if profile == profiles.REGRESSION else {})
        runtime.record_controls(boundary(runtime))
    return ref, runtime, source_root


def authority_counts(store):
    with store.connect() as c:
        return {
            t.name: c.scalar(select(func.count()).select_from(t))
            for t in (
                real_execution_grants,
                real_execution_requests,
                model_attempts,
                real_publications,
                task_sessions,
            )
        }


@pytest.mark.parametrize("profile", [profiles.REGRESSION, profiles.BUGFIX])
def test_review_to_certified_offer_to_existing_preparation(
    store, client, headers, tmp_path, monkeypatch, profile
):
    ref, runtime, _ = prepared(store, client, headers, tmp_path, monkeypatch, profile)
    before = authority_counts(store)
    selected = task_budget.register_reviewed_offer(store, ref, runtime)
    assert task_budget.register_reviewed_offer(store, ref, Runtime(runtime.root)) == selected
    # Reloaded DB owner/runtime reconstructs exactly the same current offer.
    with store.connect() as c:
        assert task_budget.offer(c, selected["proposal_id"]) == selected
        linked = task_execution.snapshot(c, **ref)
        template = task_session.preparation_template(c, linked, {"offer": selected}, store)
        assert "supported_task" not in linked["original"]
        assert (
            task_budget.permission(c, intake.require(c, proposal_decisions, ref["approval_id"]))
            is None
        )
    assert authority_counts(store) == before
    # Consume the actual offer's oracle/control data through the Runtime + profile
    # provisioner branch. Boundary itself is controlled telemetry in this test.
    target = tmp_path / "execution-preparation"
    target.mkdir()
    durable(target / "recipe.json", json.dumps(template["recipe"], sort_keys=True).encode())
    durable(target / "recipe-oracle.lua", template["oracle"].encode())

    class ExecutionPreparation(PreparedLuaRuntime):
        def check(self, argv, *args, **kwargs):
            if argv[0] == "python3" and argv[1] == "-c":
                return dict(
                    status="pass", rc=0, usage={"cgroup_removed": True, "controlled_fixture": True}
                )
            return super().check(argv, *args, **kwargs)

    next_runtime = ExecutionPreparation(target)
    task_session.provision(next_runtime, template)
    assert next_runtime.controls_valid()
    assert json.loads((target / "preparation-completed.json").read_text())["no_generation"]
    assert authority_counts(store) == before
    # Separate EXPRESS controlled owner instruction uses the existing budget
    # continuation owner. No grant/model/session is created by recording a budget.
    instruction = tmp_path / "controlled-owner-instruction.txt"
    instruction.write_text(
        "CONTROLLED TEST DATA: approve one bounded local run and draft PR; this demonstration must invoke neither."
    )
    budget_ref = owner_task.continue_supported(
        store, ref, instruction, sha(instruction.read_bytes()), runtime.recipe
    )
    assert (
        owner_task.continue_supported(
            store, ref, instruction, sha(instruction.read_bytes()), runtime.recipe
        )
        == budget_ref
    )
    with store.connect() as c:
        approval = intake.require(c, proposal_decisions, budget_ref["approval_id"])
        policy = task_budget.permission(c, approval)
        assert policy["offer"] == selected
        linked = task_execution.snapshot(c, **budget_ref)
        assert task_session.preparation_template(c, linked, policy, store) == template
    assert authority_counts(store) == before
    # Correct frozen candidate still uses the established independent checker.
    next_runtime._request_id, next_runtime._fence, next_runtime._context_sha = str(uuid4()), 1, None
    path, text = (
        (TEST, BASE_TEST + GOOD_TEST) if profile == profiles.REGRESSION else (PRODUCTION, CORRECT)
    )
    (next_runtime.workspace / path).write_text(text)
    assert next_runtime.candidate_feedback(lambda: True)["status"] == "pass"


@pytest.mark.parametrize(
    "change",
    [
        "review",
        "base",
        "origin",
        "evidence",
        "missing-certification",
        "recipe",
        "decomposition",
        "scope",
        "limits",
        "destination",
        "checks",
        "profile",
        "request",
        "offer",
    ],
)
def test_changed_inputs_invalidate_existing_offer(
    store, client, headers, tmp_path, monkeypatch, change
):
    ref, runtime, root = prepared(store, client, headers, tmp_path, monkeypatch)
    selected = task_budget.register_reviewed_offer(store, ref, runtime)
    if change == "base":
        (root / PRODUCTION).write_text(CORRECT + "-- changed base\n")
        git(root, "add", ".")
        git(
            root,
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@invalid",
            "commit",
            "-qm",
            "Changed base",
        )
    elif change == "origin":
        git(root, "remote", "set-url", "origin", "https://example.invalid/other.git")
    elif change in ("evidence", "missing-certification", "recipe"):
        path = (
            runtime.root
            / {
                "evidence": "profile-evidence/original/check-00.json",
                "missing-certification": "controls-passed.json",
                "recipe": "recipe.json",
            }[change]
        )
        path.write_text("{}")
    else:
        with write_transaction(store) as c:
            if change == "request":
                original = intake.require(c, intakes, ref["intake_id"])
                payload = {**original["payload"], "description": "Changed original request"}
                c.execute(
                    intakes.update().where(intakes.c.id == original["id"]).values(payload=payload)
                )
            elif change == "offer":
                c.execute(
                    task_run_offers.update()
                    .where(task_run_offers.c.id == selected["id"])
                    .values(snapshot={})
                )
            elif change == "decomposition":
                c.execute(
                    backlog_items.update()
                    .where(backlog_items.c.id == ref["task_id"])
                    .values(title="Changed task")
                )
            elif change == "scope":
                p = intake.require(c, proposals, selected["proposal_id"])
                content = copy.deepcopy(p["content"])
                content["scope"] = ["Different behavior"]
                c.execute(
                    proposals.update().where(proposals.c.id == p["id"]).values(content=content)
                )
            else:
                job = (
                    c.execute(
                        select(planning_jobs).where(
                            planning_jobs.c.intake_id == ref["intake_id"],
                            planning_jobs.c.kind == "planning",
                        )
                    )
                    .mappings()
                    .one()
                )
                payload = copy.deepcopy(job["payload"])
                contract = payload["task_contract"]
                if change == "review":
                    contract["intended_behavior"] = "Changed approved behavior"
                elif change == "limits":
                    contract["resource_limits"]["changed_lines"] += 1
                elif change == "destination":
                    contract["pr_draft"] = False
                elif change == "checks":
                    contract["verification_commands"][0]["argv"].append("--changed")
                elif change == "profile":
                    contract["verification_profile"] = profiles.select("lua-offline-bugfix")
                c.execute(
                    planning_jobs.update()
                    .where(planning_jobs.c.id == job["id"])
                    .values(payload=payload)
                )
    with store.connect() as c:
        assert task_budget.offer(c, selected["proposal_id"]) is None
        with pytest.raises((ValueError, KeyError)):
            task_budget.preparation_inputs(c, selected)
    assert not any(authority_counts(store).values())


def test_missing_and_cross_request_certification_cannot_register(
    store, client, headers, tmp_path, monkeypatch
):
    first_dir = tmp_path / "first"
    first_dir.mkdir()
    ref, runtime, _ = prepared(store, client, headers, first_dir, monkeypatch, certify=False)
    with pytest.raises(ValueError):
        task_budget.register_reviewed_offer(store, ref, runtime)
    with pytest.raises(ValueError, match="current reviewed contract"):
        task_budget.register_offer(store, runtime.recipe["contract"]["task_id"], runtime.recipe)
    profiles.prepare(runtime, controls(runtime))
    runtime.record_controls(boundary(runtime))
    second_dir = tmp_path / "second"
    second_dir.mkdir()
    other_ref, _, _ = prepared(store, client, headers, second_dir, monkeypatch, profiles.BUGFIX)
    with pytest.raises(ValueError):
        task_budget.register_reviewed_offer(store, other_ref, runtime)
    with store.connect() as c:
        assert c.scalar(select(func.count()).select_from(task_run_offers)) == 0


def test_cannot_downgrade_reviewed_contract_to_legacy_registration(
    store, client, headers, tmp_path, monkeypatch
):
    ref, runtime, _ = prepared(store, client, headers, tmp_path, monkeypatch)
    legacy = copy.deepcopy(runtime.recipe)
    del legacy["verification_profile"]
    with store.connect() as c:
        proposal_id = task_execution.snapshot(c, **ref)["task"]["proposal_id"]
    with pytest.raises(ValueError, match="certified profile"):
        task_budget.register_offer(store, proposal_id, legacy)


def test_changed_latest_preparation_does_not_resurrect_earlier_offer(
    store, client, headers, tmp_path, monkeypatch
):
    ref, runtime, _ = prepared(store, client, headers, tmp_path, monkeypatch)
    first = task_budget.register_reviewed_offer(store, ref, runtime)
    root = tmp_path / "new-preparation"
    root.mkdir()
    durable(root / "recipe.json", (runtime.root / "recipe.json").read_bytes())
    durable(root / "recipe-oracle.lua", runtime.oracle_source.read_bytes())
    newer = PreparedLuaRuntime(root)
    newer.prepare()
    profiles.prepare(newer, controls(newer))
    newer.record_controls(boundary(newer))
    second = task_budget.register_reviewed_offer(store, ref, newer)
    assert first["fingerprint"] != second["fingerprint"]
    with store.connect() as c:
        assert task_budget.offer(c, first["proposal_id"]) == second
        with pytest.raises(ValueError):
            task_budget.preparation_inputs(c, first)
    (newer.root / "controls-passed.json").write_text("{}")
    assert runtime.controls_valid()  # Old evidence exists, but is superseded.
    with store.connect() as c:
        assert task_budget.offer(c, first["proposal_id"]) is None
