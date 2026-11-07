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


def fresh_controlled_source(path):
    """Separate closed disposable scenarios without erasing prior source evidence."""
    import sqlite3
    from uuid import uuid4
    from fixture_ctl import fixture_path
    from everyday_store import require_settled
    from test_grounded_workflow import controlled_source
    path = fixture_path(path)
    with sqlite3.connect(path) as connection:
        require_settled(connection)
    research = path.parent / 'reviewed-research'
    research.mkdir(mode=0o700, exist_ok=True)
    if research.resolve() != research or research.stat().st_uid != os.getuid():
        raise ValueError('Research fixture must stay inside its owned disposable directory')
    source_root = research / 'source'
    if source_root.exists() or source_root.is_symlink():
        if source_root.is_symlink() or not source_root.is_dir() or source_root.stat().st_uid != os.getuid():
            raise ValueError('Refusing an unsafe disposable source fixture')
        retained = research / ('retained-source-' + str(uuid4()))
        source_root.rename(retained)
    return controlled_source(research)


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



def everyday_config(path):
    from workbench import everyday
    from workbench.real_execution import owner_identity

    if os.environ.get('WORKBENCH_E2E_EVERYDAY') != '1':
        return
    root = path.parent / 'controlled-session-owner'
    local = root / '.local'
    local.mkdir(mode=0o700, parents=True, exist_ok=True)
    scratch = root / 'scratch'
    scratch.mkdir(mode=0o700, exist_ok=True)
    policy = local / 'everyday-policy.json'
    binding = dict(database=str(path), database_identity=[path.stat().st_dev, path.stat().st_ino])
    if not policy.exists():
        policy.write_text(json.dumps(dict(**binding, evidence_roots=[str(root)], scratch=str(scratch))))
        policy.chmod(0o600)
        # The offline API process represents readiness. Worker ticks use an
        # offline fixture with no service controller or real transport.
        receipt = local / 'everyday-worker.json'
        receipt.write_text(json.dumps(dict(owner=owner_identity(), database_identity=binding['database_identity'])))
        receipt.chmod(0o600)
    os.environ.update(WORKBENCH_PLANNER='everyday', TMPDIR=str(scratch), WORKBENCH_EVERYDAY_CONFIG=str(policy))
    everyday.ROOT = root
    sys.path.insert(0, str(ROOT / 'scripts'))


def advance(engine, path, action):
    everyday_config(path)
    os.environ['WORKBENCH_RESEARCH_ROOT'] = str(path.parent / 'reviewed-research/source')
    task_sessions.ROOT = path.parent / 'controlled-session-owner'
    task_session.ROOT = task_sessions.ROOT
    if action.startswith('bug-'):
        from unittest.mock import patch
        from test_bug_workflow import BugPlanner, enable_bugs, response
        from test_grounded_workflow import policy_file, ControlledPreparer
        from test_verification_profiles import BUGGY, CORRECT, PRODUCTION
        from pytest import MonkeyPatch
        from workbench import worker, trusted_preparation

        parent = path.parent / 'automatic-workflow'
        parent.mkdir(mode=0o700, exist_ok=True)
        research = path.parent / 'reviewed-research'
        research.mkdir(mode=0o700, exist_ok=True)
        if action == 'bug-setup':
            fresh_controlled_source(path)
            policy_file(engine, parent)
            setup(path)
        os.environ.update(WORKBENCH_RESEARCH_ROOT=str(research / 'source'),
                          WORKBENCH_PREPARATION_CONFIG=str(parent / 'preparation.json'))
        adapter = BugPlanner(response())
        if action == 'bug-setup':
            with MonkeyPatch.context() as isolated:
                enable_bugs(isolated)
        elif action == 'bug-tick':
            with patch.object(trusted_preparation, 'Runtime', ControlledPreparer):
                assert worker.tick(engine, planning_adapter=adapter)
        elif action == 'bug-run':
            from local_browser_fixture import advance_local
            advance_local(engine, path, 'pass', adapter, bug_edit=dict(path=PRODUCTION, old=BUGGY, new=CORRECT))
        return
    if action.startswith('automatic-'):
        from unittest.mock import patch
        from test_grounded_workflow import policy_file, ControlledPlanner, ControlledPreparer
        from workbench import worker, trusted_preparation
        parent = path.parent / 'automatic-workflow'
        parent.mkdir(mode=0o700, exist_ok=True)
        if action == 'automatic-setup':
            research = path.parent / 'reviewed-research'
            research.mkdir(mode=0o700, exist_ok=True)
            fresh_controlled_source(path)
            policy_file(engine, parent)
            setup(path)
            return
        if action == 'automatic-denied-worker':
            policy = json.loads((parent / 'preparation.json').read_text())
            policy['allowed_classes'] = []
            (parent / 'preparation.json').write_text(json.dumps(policy))
        os.environ['WORKBENCH_RESEARCH_ROOT'] = str(path.parent / 'reviewed-research/source')
        os.environ['WORKBENCH_PREPARATION_CONFIG'] = str(parent / 'preparation.json')
        adapter = ControlledPlanner()
        with patch.object(trusted_preparation, 'Runtime', ControlledPreparer):
            if action.startswith('automatic-local-'):
                from local_browser_fixture import advance_local

                advance_local(engine, path, action.removeprefix('automatic-local-'), adapter,
                              example=None if os.environ.get('WORKBENCH_E2E_EVERYDAY') == '1' else '  ALICE ')
            else:
                # Advance the normal worker without exposing an HTTP test-control route.
                for _ in range(1 if action == 'automatic-tick' else 4):
                    if not worker.tick(engine, planning_adapter=adapter):
                        break
        with engine.connect() as c:
            from workbench.db import planning_jobs, model_attempts, real_execution_grants, real_execution_requests, real_publications
            report = dict(jobs=[dict(id=j['id'],kind=j['kind'],status=j['status'],message=j['message'])
                for j in c.execute(select(planning_jobs)).mappings()], adapter_calls=len(adapter.calls),
                model_attempts=[dict(source=a['source'],outcome=a['outcome'],model=a['actual_model']) for a in c.execute(select(model_attempts)).mappings()],
                grants=len(c.execute(select(real_execution_grants)).all()), requests=len(c.execute(select(real_execution_requests)).all()),
                publications=len(c.execute(select(real_publications)).all()))
        print(json.dumps(report))
        return
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
