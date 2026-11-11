"""Test harness only. Operates exclusively on the disposable E2E database."""

import argparse
from datetime import datetime, timezone
import sqlite3
import os
from pathlib import Path
import subprocess
import sys
import select as io_select
import tempfile
import time

from sqlalchemy import select
from workbench.db import (
    applications,
    commands,
    engine_for,
    tasks,
    write_transaction,
    model_attempts,
    execution_policies,
)
from workbench.seed import seed
from workbench import planning_worker, verification, admission, implementation, pr_preparation
from workbench.planning_provider import DemoProvider, PlanningUnavailable
from workbench.db import (
    intakes,
    planning_jobs,
    proposals,
    proposal_artifacts,
    proposal_decisions,
    backlog_items,
    backlog_dependencies,
    intake_scopes,
)

ROOT = Path(__file__).resolve().parents[1]


def fixture_path(value):
    """Only this invocation's owned E2E directory, including confined TMPDIR."""
    raw = Path(value)
    path = raw.resolve()
    if (
        raw.is_symlink()
        or raw.parent.is_symlink()
        or path.parent.parent != Path(tempfile.gettempdir()).resolve()
        or not path.parent.name.startswith("workbench-e2e-")
        or path.name != "test.sqlite3"
        or not path.is_file()
        or path.stat().st_uid != os.getuid()
        or path.parent.stat().st_uid != os.getuid()
    ):
        raise SystemExit("Refusing a database outside the owned disposable E2E directory")
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=[
            'feature-workshop-setup', 'feature-workshop-tick', 'feature-workshop-invalid-steps',
            'feature-workshop-build-setup', 'feature-workshop-build-tick', 'feature-workshop-run-tick',
            "regression-planning",
            "reviewed-offer-setup", "reviewed-offer-plan", "reviewed-offer-prepare",
            "reviewed-offer-controller", "reviewed-offer-stale",
            "reviewed-offer-scope", "reviewed-offer-certify",
            "reviewed-offer-automatic-setup", "reviewed-offer-automatic-worker", "reviewed-offer-automatic-denied-worker",
            "reviewed-offer-automatic-tick", "reviewed-offer-automatic-local-wait",
            "reviewed-offer-automatic-local-pass", "reviewed-offer-automatic-local-fail",
            "reviewed-offer-automatic-local-interrupt", "reviewed-offer-automatic-local-recover", "reviewed-offer-automatic-local-expire",
            "reviewed-offer-bug-setup", "reviewed-offer-bug-tick", "reviewed-offer-bug-run",
            "reviewed-offer-bug-scope-setup", "reviewed-offer-bug-scope-tick",
            "reviewed-offer-scenario-plan", "reviewed-offer-scenario-certify",
            "reviewed-offer-scenario-wrong-plan", "reviewed-offer-scenario-certify-hold",
            "task-pr-setup",
            "task-pr-step",
            "task-pr-finish",
            "task-pr-hold",
            "sample-check-change",
            "sample-pr-start-crash",
            "sample-artifact-missing",
            "admission-revoke",
            "admission-stale",
            "verify-fixture-fail",
            "verify-fixture",
            "reset",
            "empty",
            "advance",
            "long",
            "worker",
            "crash",
            "lock",
            "lock-until-release",
            "count",
            "missing",
            "pilot-fresh",
            "planning",
            "apply-planning",
            "fail-planning",
            "off-planning",
            "nonvisual-planning",
        ],
    )
    args = parser.parse_args()
    path = fixture_path((ROOT / ".local/e2e-db-path").read_text())
    if args.action == "pilot-fresh":
        pilot = path.parent / "pilot.sqlite3"
        if pilot.is_symlink() or not pilot.is_file():
            raise SystemExit("Refusing a missing or symlinked fixture projection")
        with sqlite3.connect(pilot) as db:
            db.execute(
                "UPDATE observations SET observed_at=?", (datetime.now(timezone.utc).isoformat(),)
            )
            db.execute(
                "UPDATE items SET projection=json_set(projection,'$.category','Waiting automatically','$.reason','Recorded fixture capacity gate is full.','$.in_progress',json('false')) WHERE id='pilot-01'"
            )
        return
    engine = engine_for(path)
    if args.action.startswith('feature-workshop-'):
        from reviewed_offer_fixture import everyday_config, setup, advance
        from test_grounded_workflow import save_local_policy
        from test_feature_planning import ControlledFeaturePlanner
        from workbench import worker, model_policy, everyday
        if os.environ.get('WORKBENCH_E2E_EVERYDAY') != '1':
            raise SystemExit('Feature planning needs the explicitly controlled everyday browser fixture.')
        everyday_config(path)
        if args.action in {'feature-workshop-setup','feature-workshop-build-setup'}:
            setup(path)
            advance(engine, path, 'automatic-setup')
            save_local_policy(engine, model=everyday.MODEL)
            policy = model_policy.read(engine)
            policy.test_fallback = 'off'
            model_policy.save(engine, policy, model_policy.CapabilityReport(executors=[]))
            if args.action == 'feature-workshop-build-setup':
                from test_feature_permission import enable
                from workbench import task_sessions, task_session
                task_sessions.ROOT = path.parent/'controlled-session-owner'
                task_session.ROOT = task_sessions.ROOT
                enable()
        else:
            os.environ['WORKBENCH_RESEARCH_ROOT'] = str(path.parent / 'reviewed-research/source')
            planner = ControlledFeaturePlanner()
            if args.action in {'feature-workshop-build-tick','feature-workshop-run-tick'}:
                from test_feature_execution import missing_names_plan, controlled_readiness, controlled_slot, controlled_check
                from test_feature_preparation import ScopePlanner
                from workbench import task_sessions, task_session
                from workbench.real_workspace import Runtime
                from unittest.mock import patch
                task_sessions.ROOT = path.parent/'controlled-session-owner'
                task_session.ROOT = task_sessions.ROOT
                os.environ['WORKBENCH_PREPARATION_CONFIG'] = str(path.parent/'automatic-workflow/preparation.json')
                if args.action == 'feature-workshop-run-tick':
                    with patch.object(Runtime,'check',controlled_check):
                        assert worker.tick(engine,planning_adapter=ScopePlanner(),
                            feature_provision=controlled_readiness,session_adapter=controlled_slot)
                    return
                planner.mutate = missing_names_plan
            if args.action == 'feature-workshop-invalid-steps':
                def incomplete(result, schema):
                    result['tasks'] = result['tasks'][:1]
                planner.mutate = incomplete
                planner.diagnostics.update(accounting_state='complete',
                    usage=dict(status='complete', total_tokens=200))
            assert worker.tick(engine, planning_adapter=planner)
        return
    if args.action.startswith('reviewed-offer-'):
        from reviewed_offer_fixture import setup, advance
        action = args.action.removeprefix('reviewed-offer-')
        if action == 'setup':
            setup(path)
        else:
            advance(engine, path, action)
        return
    if args.action == "regression-planning":
        # Committed, deliberately inert test source owned by this disposable E2E
        # directory. No recipe, provider, service, oracle or execution registration.
        research = path.parent / "regression-research"
        if not research.exists():
            (research / "tests").mkdir(parents=True)
            (research / "tests/run.sh").write_text(
                '#!/bin/bash\nfor t in test_*.lua; do\nlua5.1 -e \'require("bit")\' "$t"\n'
                '# PASSED / ERROR:\ndone\n'
            )
            (research / "tests/harness.lua").write_text('-- CONTROLLED E2E framework evidence only\n')
            for name in ("profiles", "share"):
                (research / ("tests/test_" + name + ".lua")).write_text('-- CONTROLLED E2E source; never executed\n')
            git_env = {'PATH': '/usr/bin:/bin', 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'}
            def git(*values):
                subprocess.run(['git', '-C', str(research), '-c', 'core.hooksPath=/dev/null',
                    '-c', 'commit.gpgsign=false', *values], env=git_env, check=True, capture_output=True)
            git('init', '-q')
            git('remote', 'add', 'origin', 'https://github.com/Twich-Team/TwichUI.git')
            git('add', '.')
            git('-c', 'user.name=Controlled fixture', '-c', 'user.email=fixture@invalid',
                'commit', '-qm', 'Disposable regression-preparation evidence')
        os.environ['WORKBENCH_RESEARCH_ROOT'] = str(research)
        from workbench.regression_preparation import process_one
        process_one(engine)
        return
    if args.action.startswith("task-pr-"):
        from task_pr_fixture import setup, advance

        os.environ["WORKBENCH_TASK_TEMPLATES_ROOT"] = str(path.parent / "task-templates")

        if args.action == "task-pr-setup":
            setup(path)
        else:
            advance(
                engine,
                path,
                steps=12 if args.action == "task-pr-finish" else 1,
                hold=args.action == "task-pr-hold",
            )
        engine.dispose()
        return
    if args.action in {"verify-fixture", "verify-fixture-fail"}:

        class FailedVerifier:
            def verify(self, _):
                raise OSError("Synthetic failure")

        verification.process_one(engine, FailedVerifier() if args.action.endswith("fail") else None)
        return
    if args.action in {
        "planning",
        "apply-planning",
        "fail-planning",
        "off-planning",
        "nonvisual-planning",
    }:
        if args.action == "apply-planning":
            planning_worker.apply_one(engine)
        else:

            class Failed:
                def generate(self, packet, schema):
                    if args.action == "off-planning":
                        raise PlanningUnavailable("Fixture unavailable")
                    return {"invalid": True}, "fixture-invalid", "demo"

            class Nonvisual(DemoProvider):
                def generate(self, packet, schema):
                    result, model, basis = super().generate(packet, schema)
                    result["proposal"]["visual"] = "nonvisual"
                    return result, model, basis

            adapter = (
                Nonvisual()
                if args.action == "nonvisual-planning"
                else DemoProvider()
                if args.action == "planning"
                else Failed()
            )
            planning_worker.process_one(engine, adapter)
        return
    if args.action == "sample-pr-start-crash":
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from workbench.pr_preparation import process_one;from workbench.db import engine_for;process_one(engine_for(),crash='after-start')",
            ],
            env={**os.environ, "WORKBENCH_DB": str(path)},
            check=False,
        )
        raise SystemExit(0 if result.returncode == 91 else 1)
    if args.action in {"worker", "crash"}:
        extra = ["--test-crash", "before-commit"] if args.action == "crash" else []
        result = subprocess.run(
            [sys.executable, "-m", "workbench.worker", "--once", *extra],
            env={**os.environ, "WORKBENCH_DB": str(path)},
            check=False,
        )
        raise SystemExit(0 if result.returncode in {0, 91} else result.returncode)
    with write_transaction(engine) as connection:
        if args.action in {"reset", "empty"}:
            # Test capability belongs only to this validated disposable store.
            (path.parent / 'controlled-session-owner/.local/task-runtime.json').unlink(missing_ok=True)
            from workbench.db import (
                real_publications,
                task_sessions,
                task_run_offers,
                real_execution_requests,
                real_execution_grants,
            )

            connection.execute(task_sessions.delete())
            connection.execute(real_publications.delete())
            connection.execute(model_attempts.delete())
            connection.execute(real_execution_requests.delete())
            connection.execute(real_execution_grants.delete())
            connection.execute(task_run_offers.delete())
            for table in (
                pr_preparation.effects,
                pr_preparation.records,
                implementation.verifications,
                implementation.candidates,
                implementation.attempts,
                implementation.records,
                admission.records,
                verification.records,
                model_attempts,
                execution_policies,
                backlog_dependencies,
                backlog_items,
                proposal_decisions,
                proposal_artifacts,
                proposals,
                planning_jobs,
                intake_scopes,
                intakes,
            ):
                connection.execute(table.delete())
            connection.execute(applications.delete())
            connection.execute(commands.delete())
            connection.execute(tasks.delete())
        elif args.action == "sample-check-change":
            row = connection.execute(select(implementation.verifications)).mappings().one()
            connection.execute(
                implementation.verifications.update().values(
                    message=row["message"] + " Updated saved check note."
                )
            )
        elif args.action == "admission-revoke":
            connection.execute(proposal_decisions.update().values(scope_review_required=1))
        elif args.action == "sample-artifact-missing":
            connection.execute(implementation.candidates.update().values(artifact=b""))
        elif args.action == "admission-stale":
            connection.execute(
                backlog_items.update()
                .where(backlog_items.c.kind == "task")
                .values(title="Changed synthetic task")
            )
        elif args.action == "advance":
            row = connection.execute(select(tasks)).mappings().one()
            evidence = row["evidence"]
            evidence["claims"][0]["text"] += " The refreshed contract now includes a new detail."
            connection.execute(
                tasks.update().values(evidence=evidence, revision=row["revision"] + 1)
            )
        elif args.action == "long":
            row = connection.execute(select(tasks)).mappings().one()
            evidence = row["evidence"]
            evidence["claims"][0]["text"] += " ThisVeryLongUnbrokenFixtureIdentifier" * 15
            connection.execute(
                tasks.update().values(
                    title="Keeping your place through a very long adventure with many optional guide steps",
                    evidence=evidence,
                )
            )
        elif args.action == "missing":
            connection.execute(tasks.update().values(subsystem="", summary=""))
        elif args.action in {"lock", "lock-until-release"}:
            print("locked", flush=True)
            if args.action == 'lock':
                time.sleep(1.1)
            else:
                ready, _, _ = io_select.select([sys.stdin], [], [], 10)
                if not ready or sys.stdin.readline().strip() != 'release':
                    raise SystemExit('Disposable fixture lock release timed out or was invalid')
        elif args.action == "count":
            print(len(connection.execute(select(applications)).all()))
    if args.action == "reset":
        seed(engine)
    engine.dispose()


if __name__ == "__main__":
    main()
