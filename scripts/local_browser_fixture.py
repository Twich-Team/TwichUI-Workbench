"""Disposable browser tests with scripted model transport and real Lua checks."""
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from pytest import MonkeyPatch
from sqlalchemy import select
from acceptance_transport_guard import install_offline_guard

from workbench import real_execution, real_workspace, task_session, worker
from workbench.db import real_execution_grants, real_execution_requests, real_publications, task_sessions, proposal_decisions
from workbench.real_workspace import durable


def advance_local(engine, database, outcome, planner, *, example="  ALICE ", native=False, staged=False):
    from test_regression_evidence import ControlledPreparer
    from test_real_transport import final_response, scripted_http, tool_response
    from test_verification_profiles import TEST

    install_offline_guard()
    if outcome == 'expire':
        assert worker.tick(engine, planning_adapter=planner)
        with engine.begin() as c:
            for row in c.execute(select(proposal_decisions)).mappings():
                stamp = "2000-01-01T00:00:00+00:00" if row["payload"].get("run_budget") else "1999-01-01T00:00:00+00:00"
                c.execute(proposal_decisions.update().where(proposal_decisions.c.id == row["id"]).values(created_at=stamp))
        assert worker.tick(engine, planning_adapter=planner)
        with engine.connect() as c:
            assert c.execute(select(task_sessions.c.status)).scalar_one() == 'held'
            assert not c.execute(select(real_execution_requests)).first()
        return

    if outcome == 'wait':
        # Apply the browser decision and queue the session. The worker has not claimed it.
        assert worker.tick(engine, planning_adapter=planner)
        assert worker.tick(engine, planning_adapter=planner)
        with engine.connect() as c:
            assert c.execute(select(task_sessions.c.status)).scalar_one() == 'pending'
            assert not c.execute(select(real_execution_requests)).first()
        return

    original = task_session.process_one
    sandbox = database.parent
    scratch = Path(tempfile.gettempdir() if native else sandbox) / 'implementation-scratch'
    scratch.mkdir(mode=0o700, exist_ok=True)

    class SimulatedRuntime(real_workspace.Runtime if native else ControlledPreparer):
        def preflight(self):
            # Real preparation checks, with the service/auth boundary simulated.
            return self.static_preflight()

    def launch(command):
        cycle = Path(command[command.index('--root') + 1])
        assert cycle.parent == task_session.ROOT / "artifacts/runs"
        ref = json.loads((cycle / 'workflow-link.json').read_text())['task']
        runtime = SimulatedRuntime(cycle / 'real-run')
        runtime.task_ref = ref
        tape = scripted_http(cycle, isolated)
        runtime.primary = tape.adapter
        if staged and native:
            from chronicle_lease import controller_preflight
            verified = controller_preflight(cycle, Path(command[command.index('--owner-instruction') + 1]))
            assert verified is not None
            verified[0].dispose()
        else:
            durable(cycle / 'controller-started.json', b'{"simulated": true}')

        def scripted_edit(body):
            rendered = json.loads(body['messages'][1]['content'].split('\n', 1)[1])
            scenario, = rendered['examples']
            if native:
                # This fixture sends a generated positive control through the
                # implementation parser. It is never supplied to a live model.
                from workbench.regression_evidence import compile_plan
                prepared = runtime.workflow_task['proposal_review']['task_contract']['behavioral_evidence']
                compiled = compile_plan(prepared)
                assert compiled['focused_test'] == test and scenario['source_paths'] == sources
                old = (runtime.workspace / test).read_text()
                addition = compiled['additions']['known-good-oracle-control']
                return tool_response([('replace_text', dict(path=test, old=old, new=old + addition))])
            assert (scenario['call_args'], scenario['expected_observation'],
                    scenario['incorrect_observation_for_same_call']) == ([example], 'alice', 'ALICE')
            old = (runtime.workspace / TEST).read_text()
            expected = scenario['expected_observation'] if outcome == 'pass' else scenario['incorrect_observation_for_same_call']
            assertion = f"assert(normalize({json.dumps(scenario['call_args'][0])}) == {json.dumps(expected)})\n"
            return tool_response([('replace_text', dict(path=TEST, old=old, new=old + assertion))])

        test, sources = runtime.recipe['focused_test'], [
            path for path in runtime.recipe['read_paths'] if path != runtime.recipe['focused_test']]
        read = tool_response([('read_file', dict(path=path)) for path in (test, *sources)])
        check = tool_response([('run_check', dict(name='candidate'))])
        tape.steps = [read, scripted_edit, check, final_response()]
        if outcome == 'fail':
            # Repeating a failed check requires a real edit. Finishing here
            # exercises the refusal to continue on an unchanged rejected patch.
            tape.steps = [read, scripted_edit, check, check, final_response()]
        if outcome == 'interrupt':
            tape.steps = [read]
            receive = tape.adapter.request

            def interrupted(*args, **kwargs):
                receive(*args, **kwargs)
                raise KeyboardInterrupt('Simulated controller loss before recording the response')

            tape.adapter.request = interrupted
        try:
            assert real_execution.process_one(engine, runtime)
        except KeyboardInterrupt:
            assert outcome == 'interrupt' and len(tape.bodies) == 1
            return  # Leave the claimed request and controller receipt absent for recovery.
        assert outcome in {'pass', 'fail'} and len(tape.bodies) == len(tape.steps), (outcome, len(tape.bodies))
        durable(cycle / 'lease-controller-result.json', b'{"restoration":{"restored":true},"simulated":true}')

    def advance(store, config, stop_event=None):
        return original(store, config, stop_event=stop_event,
                        prepare=task_session.provision if staged and native else prepare, launch=launch)

    def prepare(runtime, template):
        task_session.provision(SimulatedRuntime(runtime.root), template)

    with MonkeyPatch.context() as isolated:
        isolated.delenv('WORKBENCH_LOCAL_KEY', raising=False)
        isolated.setattr(tempfile, 'tempdir', str(scratch))
        if not native:
            isolated.setattr(real_workspace.confine, 'root', lambda: str(sandbox / 'simulated-cgroup'))
        # Installed sampler, credential lease, and cgroup control remain out of this fixture.
        from contextlib import nullcontext
        proof = nullcontext() if staged else patch('runpy.run_path', return_value={'checked_limit': lambda _root: None})
        with (proof,
              patch.object(task_session, 'process_one', advance)):
            assert worker.tick(engine, planning_adapter=planner)
            if outcome != 'interrupt':
                assert not worker.tick(engine, planning_adapter=planner)
    with engine.connect() as c:
        session = c.execute(select(task_sessions)).mappings().one()
        request = c.execute(select(real_execution_requests)).mappings().one()
        grant = c.execute(select(real_execution_grants)).mappings().one()
        assert grant['snapshot']['task_budget']['budget']['publication'] == 'hold'
        assert not c.execute(select(real_publications)).first()
        expected_session, expected_request = {
            'pass': ('completed', 'checked'), 'fail': ('failed', 'failed'),
            'interrupt': ('waiting_recovery', 'running'), 'recover': ('interrupted', 'interrupted'),
        }[outcome]
        assert (session['status'], request['status']) == (expected_session, expected_request), (
            session['message'], request.get('result'))
        assert (grant['revoked_at'] is not None) == (outcome != 'interrupt')
        if outcome == 'pass':
            runtime = real_workspace.Runtime(grant['snapshot']['recovery']['root'])
            assert runtime.recover_completion(request, grant)
