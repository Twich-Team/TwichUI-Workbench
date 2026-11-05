"""Browser fixture: actual Lua/contracts/certification; no model, leases or publication.

All mutations are confined by fixture_ctl to its disposable browser database.
The only executor is an explicit callback to the existing finite-session owner.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
from sqlalchemy import select

from workbench import intake, planning_worker, regression_preparation, real_recipes
from workbench import verification_profiles, task_budget, task_sessions, task_session
from workbench.db import intakes, backlog_items, proposal_decisions
from workbench.real_workspace import durable, sha

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend/tests'))
from test_reviewed_offers import source, PreparedLuaRuntime  # noqa: E402
from test_verification_profiles import TEST, REGRESSION_ORACLE, controls, boundary  # noqa: E402


def setup(path):
    parent = path.parent / 'reviewed-research'
    parent.mkdir(mode=0o700, exist_ok=True)
    if not (parent / 'source').exists():
        source(parent, verification_profiles.REGRESSION)
    runtime_owner = path.parent / 'controlled-session-owner'
    runtime_owner.mkdir(mode=0o700, exist_ok=True)
    local = runtime_owner / '.local'
    local.mkdir(mode=0o700, exist_ok=True)
    proof = local / 'proof.json'
    proof.write_text('{"controlled_fixture":true}')
    config = dict(mode='explicit-one-off-only', database=str(path),
                  database_identity=[path.stat().st_dev, path.stat().st_ino],
                  templates_root=str(local / 'supported-task-templates'), repository='Twich-Team/TwichUI',
                  base_branch='main', model='pilot-qwen38-27b', automatic_fallback=False,
                  total_limit_proof=str(proof), total_limit_proof_sha256=sha(proof.read_bytes()))
    (local / 'task-runtime.json').write_text(json.dumps(config))
    (local / 'task-runtime.json').chmod(0o600)


def advance(engine, path, action):
    os.environ['WORKBENCH_RESEARCH_ROOT'] = str(path.parent / 'reviewed-research/source')
    task_sessions.ROOT = path.parent / 'controlled-session-owner'
    task_session.ROOT = task_sessions.ROOT
    if action in {'scenario-plan', 'scenario-wrong-plan'}:
        from workbench.regression_evidence import input_binding
        with engine.connect() as c:
            original = c.execute(select(intakes.c.payload)).scalar_one()
        _, base, _ = regression_preparation.repository()
        response = dict(input_sha256=input_binding(original, '', base, [TEST]),
            model='CONTROLLED model response; no inference', draft=dict(scenarios=[dict(
                id='capital-name', acceptance_index=0, test_path=TEST, fixture='module',
                source_paths=['!!!TwichUI/normalize.lua'], target=[], args=['  ALICE '],
                observe=dict(expected='ALICE' if action == 'scenario-wrong-plan' else 'alice'),
                incorrect='alice' if action == 'scenario-wrong-plan' else 'ALICE')]))
        assert regression_preparation.process_one(engine, scenario_response=response)
        return
    if action in {'scenario-certify', 'scenario-certify-hold'}:
        from unittest.mock import patch
        from workbench import trusted_preparation
        with engine.connect() as c:
            original = c.execute(select(intakes)).mappings().one()
            task = c.execute(select(backlog_items).where(backlog_items.c.kind == 'task')).mappings().one()
            approval = c.execute(select(proposal_decisions)).mappings().one()
            ref = dict(intake_id=original['id'], task_id=task['id'], approval_id=approval['id'])
        root = path.parent / ('certified-' + ref['intake_id'])
        root.mkdir(mode=0o700)
        class ControlledPreparer(PreparedLuaRuntime):
            def check(self, argv, *args, **kwargs):
                if argv[:2] == ['python3', '-c']:
                    return dict(command=argv, status='pass', rc=0, out='CONTROLLED boundary only',
                        usage=dict(cgroup_removed=True, controlled_fixture=True))
                return super().check(argv, *args, **kwargs)
        with patch.object(trusted_preparation, 'Runtime', ControlledPreparer):
            try:
                trusted_preparation.prepare_regression(engine, ref, root,
                    dict(name='Controlled author', email='fixture@invalid'))
            except ValueError as error:
                if action != 'scenario-certify-hold':
                    raise
                print(json.dumps(dict(status='hold', message=str(error), no_generation=True)))
                return
        assert action != 'scenario-certify-hold', 'Expected the deliberately wrong expectation to hold'
        return
    if action == 'plan':
        assert regression_preparation.process_one(engine)
        return
    if action in {'prepare', 'scope'}:
        assert planning_worker.apply_one(engine)
        assert regression_preparation.process_one(engine)
        if action == 'scope':
            return
    if action in {'prepare', 'certify'}:
        with engine.connect() as c:
            original = c.execute(select(intakes)).mappings().one()
            task = c.execute(select(backlog_items).where(backlog_items.c.kind == 'task')).mappings().one()
            approval = c.execute(select(proposal_decisions)).mappings().one()
            ref = dict(intake_id=original['id'], task_id=task['id'], approval_id=approval['id'])
            spec = real_recipes.from_review(c, ref, oracle=REGRESSION_ORACLE, behavior_id='normalize',
                read_paths=[TEST], focused_test=TEST, identity=dict(name='Controlled author',email='fixture@invalid'))
        root = path.parent / ('certified-' + ref['intake_id'])
        root.mkdir(mode=0o700)
        durable(root / 'recipe.json', json.dumps(spec, sort_keys=True).encode())
        durable(root / 'recipe-oracle.lua', REGRESSION_ORACLE.encode())
        runtime = PreparedLuaRuntime(root)
        runtime.prepare()
        verification_profiles.prepare(runtime, controls(runtime))
        runtime.record_controls(boundary(runtime))
        # Exercise the supported operation, not direct DB construction/registration.
        subprocess.run([sys.executable, 'scripts/wb.py', 'prepare-offer', '--database', str(path),
            '--intake-id', ref['intake_id'], '--task-id', ref['task_id'],
            '--scope-approval-id', ref['approval_id'], '--evidence-root', str(root)], check=True)
        return
    if action == 'stale':
        # Controlled evidence loss, never an installed preparation root.
        with engine.connect() as c:
            row = c.execute(select(task_budget.offers)).mappings().one()
        Path(row['snapshot']['certified_preparation']['root'], 'profile-evidence.json').write_text('{}')
        return
    assert action == 'controller'
    assert planning_worker.apply_one(engine)
    assert task_sessions.queue_one(engine)
    seen = []
    def controlled(store, cfg, stop_event=None):
        value = json.loads(cfg.read_text())
        with store.connect() as c:
            approval = intake.require(c, proposal_decisions, value['task']['approval_id'])
            policy = task_budget.permission(c, approval)
            from workbench.task_execution import snapshot
            linked = snapshot(c, **value['task'])
            template = task_session.preparation_template(c, linked, policy, store)
        target = Path(value['run_root']) / 'controlled-preparation'
        target.mkdir(mode=0o700)
        durable(target / 'recipe.json', json.dumps(template['recipe'],sort_keys=True).encode())
        durable(target / 'recipe-oracle.lua', template['oracle'].encode())
        class Executor(PreparedLuaRuntime):
            def check(self, argv, *args, **kwargs):
                if argv[:2] == ['python3','-c']:
                    return dict(status='pass',rc=0,usage=dict(cgroup_removed=True,controlled_fixture=True))
                return super().check(argv,*args,**kwargs)
        runtime = Executor(target)
        task_session.provision(runtime, template)
        seen.append(dict(task=value['task'], recipe=runtime.recipe, certified=runtime.controls_valid(),
                         policy=policy, no_generation=True, controlled_executor=True))
        durable(Path(value['run_root']) / 'session-finished.json', json.dumps(dict(task=value['task'],
                status='held', message='CONTROLLED executor prepared and certified the bound task. No model, lease or PR.')).encode())
    assert task_sessions.process_one(engine, advance=controlled)
    assert not task_sessions.process_one(engine, advance=controlled)
    assert not task_sessions.queue_one(engine)
    from test_reviewed_offers import authority_counts
    result = dict(controller_calls=len(seen), preparations=seen, authority_counts=authority_counts(engine))
    (path.parent / 'BROWSER_HANDOFF.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result))
