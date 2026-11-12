"""Connected disposable feature worker with actual Lua checks and frozen changes.

The coding response and confinement telemetry are controlled fixtures. This is
not real Qwen, hosted delivery, sandbox readiness or game-client acceptance.
"""
import json
import subprocess
from pathlib import Path

from sqlalchemy import select

from workbench import (feature_execution, task_sessions,
    task_session, task_budget, task_execution, real_execution, feature_source, worker)
from workbench.db import (planning_jobs, task_sessions as sessions, proposal_decisions,
    real_execution_requests, real_execution_grants, real_publications)
from workbench.real_workspace import Runtime, durable, sha
from workbench.repair_cycle import record_readiness
from test_feature_steps import prepared_parent
from test_feature_preparation import ScopePlanner
from test_everyday import installed as installed
from test_verification_profiles import PRODUCTION, TEST


def controlled_check(self, argv, cwd, ro=(), timeout=120, private_loopback=False):
    result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return dict(command=argv, rc=result.returncode, status='pass' if result.returncode == 0 else 'fail',
        out=result.stdout+result.stderr, network_mode='private-loopback' if private_loopback else 'denied',
        usage=dict(cgroup_removed=True, controlled_fixture=True))


def controlled_readiness(runtime, template):
    runtime.prepare()
    baseline = controlled_check(runtime, ['bash','tests/run.sh'], runtime.workspace, private_loopback=True)
    assert baseline['status'] == 'pass', baseline
    durable(runtime.root/'fresh-baseline-suite.json', json.dumps(baseline).encode())
    boundary = runtime.root/'controlled-boundary.json'
    durable(boundary, json.dumps(dict(checks={'controlled':dict(status='pass',rc=0,
        usage=dict(cgroup_removed=True,controlled_fixture=True))})).encode())
    record_readiness(runtime, boundary)
    durable(runtime.root/'preparation-completed.json', json.dumps(dict(base=runtime.base,
        recipe_sha256=sha((runtime.root/'recipe.json').read_bytes()),no_generation=True)).encode())


class ControlledCodingRuntime(Runtime):
    def preflight(self):
        return self.static_preflight()

    def execute(self, identity, fence, permitted):
        assert permitted()
        self._request_id, self._fence = identity, fence
        self._context_sha = self.context_sha()
        source = self.workspace/PRODUCTION
        test = self.workspace/TEST
        text = source.read_text()
        # Deterministic fixture coding responses, never app code or a real-addon
        # solution. The second response requires the first actual checked edit.
        if not self.feature_input:
            assert 'return false end' not in text  # Failed attempt edits never become this input.
            text = text.replace('function(s) ', 'function(s) if s == nil then return nil end; ')
            addition = 'local ok, observed = pcall(normalize, nil)\nassert(ok and observed == nil)\n'
        else:
            assert 'if s == nil then return nil end' in text
            text = text.replace("return s:match", "if s == '' then return 'anonymous' end; return s:match")
            addition = "assert(normalize('') == 'anonymous')\n"
        source.write_text(text)
        test.write_text(test.read_text()+addition)
        assert self.candidate_feedback(permitted)['status'] == 'pass'
        usage = dict(correction_count=0, read_input_errors=0)
        observation = dict(usage=dict(status='complete', units='tokens', source='controlled_fixture',
            values=dict(prompt_tokens=10,completion_tokens=10,total_tokens=20)))
        return self.freeze_candidate(identity, fence, permitted, usage, observation)


def controlled_slot(engine, config_path, stop_event=None):
    config = json.loads(config_path.read_text())
    ref = config['task']
    with engine.connect() as c:
        approval = c.execute(select(proposal_decisions).where(proposal_decisions.c.id == ref['approval_id'])).mappings().one()
        budget = task_budget.permission(c, approval, task_id=ref['task_id'], offer_id=config['feature_offer_id'])
        linked = task_execution.snapshot(c, **ref)
        template = task_session.preparation_template(c, linked, budget, engine)
    root = Path(config['run_root'])/'real-run'
    root.mkdir()
    durable(root/'recipe.json', json.dumps(template['recipe']).encode())
    durable(root/'recipe-oracle.lua', template['oracle'].encode())
    if template.get('feature_recovery'):
        durable(root/'feature-recovery.json', json.dumps(template['feature_recovery'],sort_keys=True).encode())
        (root/'feature-recovery.json').chmod(0o600)
    runtime = ControlledCodingRuntime(root)
    controlled_readiness(runtime, template)
    runtime.task_ref = ref
    runtime.feature_offer_id = config['feature_offer_id']
    assert task_budget.authorize_one(engine, runtime)
    assert real_execution.process_one(engine, runtime=runtime)
    with engine.connect() as c:
        result = c.execute(select(real_execution_requests).join(real_execution_grants,
            real_execution_requests.c.grant_id == real_execution_grants.c.id).where(
            real_execution_grants.c.snapshot['task_budget']['offer']['id'].as_string() == config['feature_offer_id'])).mappings().one()
        assert result['status'] == 'checked', result['result']
    task_session.finish(engine, config, 'verified', 'Controlled fixture has actual passing Lua checks.')


def missing_names_plan(result, schema, *, verification=False):
    from workbench.schemas import PlanningOutput
    if schema is PlanningOutput:
        plan = result['proposal']
        plan.update(summary='Handle absent and blank names without changing normal name cleanup.',
            value='Avoid absent-name errors and keep a useful blank-name default.',
            feasibility='Controlled existing-file fixture; actual Lua checks are required.',
            exclusions=['No new settings or persistence.', 'No interface or harness changes.'],
            stages=['Handle absent input.', 'Restore the blank-name default.', 'Check the combined behavior.'],
            scope=['Return nil for an absent name without throwing.',
                'Use anonymous for blank names while preserving normal cleanup and missing-input behavior.'],
            behavior=['Absent names return nil.', 'Blank names return anonymous.',
                'Other names retain whitespace trimming and lowercase normalization.'],
            acceptance=['Absent input returns nil without an error.',
                'Blank input returns anonymous and ordinary names retain existing normalization.'])
    else:
        result['tasks'][0].update(title='Handle missing input', purpose='Return nil safely for an absent name.',
            acceptance=['Absent input returns nil without throwing and normal cleanup remains unchanged.'])
        result['tasks'][1].update(title='Restore the blank-name default',
            purpose='Return anonymous for blank names using the checked missing-input implementation.',
            acceptance=['Blank input returns anonymous, missing input still returns nil, and normal cleanup remains unchanged.'])

    if verification and schema is not PlanningOutput:
        for key,title,dependency in (
            ('verify','Check the complete feature','restore'),
            ('deliver','Prepare the feature delivery','verify'),
        ):
            result['tasks'].append(dict(key=key,kind='task',parent=None,title=title,purpose=title,
                acceptance=['The complete absent/blank behavior and original normalization are independently checked.']
                    if key=='verify' else ['One coherent feature result is prepared for review.'],
                scope_indices=[0,1],depends_on=[dependency],source_ids=result['tasks'][0]['source_ids']))


def missing_names_parent(client, headers, store, *, verification=False):
    from test_feature_planning import ControlledFeaturePlanner
    def proposed(result,schema):
        missing_names_plan(result,schema,verification=verification)
    return prepared_parent(client, headers, store,
        browser=True,
        planner=ControlledFeaturePlanner(mutate=proposed), title='Handle absent and blank names safely',
        description='Return nil for an absent name and anonymous for an empty name. Keep existing trimming and lowercase behavior for other names.')


def test_two_dependent_steps_use_one_parent_and_real_checked_source_with_no_child_prs(
    client, headers, store, installed, monkeypatch,
):
    monkeypatch.setattr(Runtime, 'check', controlled_check)
    _, decision, first, second = missing_names_parent(client, headers, store)
    from workbench.regression_preparation import repository, git
    original, base, _ = repository()
    original_bytes = (original/PRODUCTION).read_bytes()
    planner = ScopePlanner()
    def tick():
        return worker.tick(store, planning_adapter=planner, feature_provision=controlled_readiness,
            session_adapter=controlled_slot)
    for task_id in (first, second):
        assert tick()
        assert tick()
        assert tick()
        with store.connect() as c:
            parent = c.execute(select(sessions)).mappings().one()
            assert parent['id'] == decision
            assert parent['state']['feature_slot']['task_id'] == task_id
        assert tick()
        # A finished child has not completed the parent and cannot replay.
        assert not task_sessions.process_one(store, advance=controlled_slot)
        with store.connect() as c:
            parent = c.execute(select(sessions)).mappings().one()
            assert parent['status'] == 'pending'
            assert parent['state']['feature_slot_result']['status'] == 'completed'
        assert tick()
    with store.connect() as c:
        job = c.execute(select(planning_jobs).where(planning_jobs.c.kind == 'feature_execution')).mappings().one()
        completed = job['payload']['completed_steps']
        assert [item['task_id'] for item in completed] == [first,second]
        assert len(c.execute(select(proposal_decisions)).all()) == 2
        assert len(c.execute(select(sessions)).all()) == 1
        assert len(c.execute(select(real_execution_requests)).all()) == 2
        assert not c.execute(select(real_publications)).first()
        linked = task_execution.snapshot(c, job['intake_id'], second, decision)
        from workbench.trusted_preparation import configuration
        carried = feature_source.prerequisite_tasks(c, completed[-1]['input'], linked, configuration(store))
        assert carried == {first,second}
        final = Path(completed[-1]['input']['source'])
        assert 'if s == nil then return nil end' in (final/PRODUCTION).read_text()
        assert "if s == '' then return 'anonymous' end" in (final/PRODUCTION).read_text()
        assert 'assert(ok and observed == nil)' in (final/TEST).read_text()
        assert "assert(normalize('') == 'anonymous')" in (final/TEST).read_text()
        # Exact frozen source can be reread after the original config root moves.
        changed_policy = {**configuration(store), 'output_root':'/different/new/release'}
        feature_source.validate_input(c, completed[-1]['input'], linked, changed_policy)
    assert (original/PRODUCTION).read_bytes() == original_bytes
    assert git(original, 'rev-parse','HEAD').decode().strip() == base
    assert feature_execution.advance(store)  # Requires the distinct combined path.
    with store.connect() as c:
        parent = c.execute(select(sessions)).mappings().one()
        assert parent['status'] == 'held' and parent['finished_at']
    # A settled held parent can be preserved and upgraded; it is not an active
    # writer or a fabricated completed feature. Unknown work would still hold.
    import sqlite3
    from everyday_store import require_settled
    with sqlite3.connect(store.url.database) as c:
        require_settled(c)
    data = client.get('/api/features/'+job['intake_id']).json()
    assert data['state'] != 'completed' and data['integrated_verification'] == 'not_recorded'
    assert len(planner.calls) == 2
