"""Opt-in rehearsal of the delegated browser driver on the four pinned cases."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import subprocess
from threading import Event, Thread
import time
from uuid import uuid4

import pytest
import uvicorn
from sqlalchemy import select

from workbench import codex_provider, intake, real_execution, task_session, task_sessions, trusted_preparation, worker
from workbench.api import create_app
from workbench.db import intakes, model_attempts, proposal_decisions, task_sessions as sessions
from workbench.planning_provider import LocalBeforeGeneration, LocalProvider
from workbench.real_workspace import confine, durable
from test_everyday import installed  # noqa: F401
from test_regression_evidence import ControlledPreparer

ROOT = Path(__file__).resolve().parents[2]
SOURCE = os.environ.get('WB_ACCEPTANCE_OFFLINE_SOURCE')
pytestmark = pytest.mark.skipif(not SOURCE, reason='Run with the pinned local TwichUI acceptance checkout')


@contextmanager
def serve(app):
    config = uvicorn.Config(app, lifespan='off', log_level='warning', access_log=False)
    server = uvicorn.Server(config)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        from workbench.api import AUTHORITIES
        authority = f"127.0.0.1:{listener.getsockname()[1]}"
        AUTHORITIES.add(authority)
        thread = Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
        thread.start()
        try:
            end = time.monotonic() + 5
            while not server.started and time.monotonic() < end:
                time.sleep(.02)
            assert server.started
            yield f'http://127.0.0.1:{listener.getsockname()[1]}'
        finally:
            server.should_exit = True
            thread.join(5)
            assert not thread.is_alive()
            AUTHORITIES.remove(authority)


def test_delegated_journeys(store, installed, tmp_path, monkeypatch):  # noqa: F811
    import acceptance_policy
    import local_browser_fixture
    import test_regression_evidence
    from acceptance_transport_guard import install_offline_guard
    from project_env import environment
    from workbench.codex_runtime import CodexRuntime

    monkeypatch.setenv('WORKBENCH_RESEARCH_ROOT', SOURCE)
    monkeypatch.setenv('WORKBENCH_STATIC_ROOT', str(ROOT / 'frontend/dist'))
    database = Path(store.url.database)
    database.chmod(0o600)
    with store.connect() as connection:
        connection.exec_driver_sql('PRAGMA journal_mode=WAL')  # Disposable fixture only.
    policy = acceptance_policy.build_policy(json.loads((ROOT / 'ops/acceptance-input.json').read_text()))
    path = tmp_path / 'policy-browser.json'
    path.write_text(json.dumps(policy))
    phase = tmp_path / 'fixture-phase'
    phase.write_text('')
    calls = []
    stopped = Event()
    errors = []
    counted = dict(prompt_tokens=40, completion_tokens=10, reasoning_tokens=0, total_tokens=50)
    by_choice = {case['choice']: case for case in policy['cases']}

    def controlled(packet):
        choice = packet['original']['local_planning']
        case = by_choice[choice]
        value = {**case['fixtureResponse'], **{k: packet[k] for k in
                 ('input_sha256', 'evidence_sha256', 'policy_sha256')}}
        assert packet['evidence_sha256'] == case['evidenceSha']
        calls.append((choice, 'planning', case['provider']))
        return value

    def local(self, packet, schema, expected_model):
        if packet['original']['local_planning'] == 'qwen-with-codex-fallback':
            assert phase.read_text() == 'qwen-refused'
            raise LocalBeforeGeneration()
        self.diagnostics = dict(actual_model=expected_model, generation_state='finished',
                                usage=codex_provider.usage_record(counted))
        return controlled(packet), expected_model, 'local'

    def codex(model, effort, prompt, schema, stop):
        assert model == policy['models']['codex'] and effort is None and not stop()
        if 'Untrusted task data:\n' in prompt:
            packet = json.loads(prompt.split('Untrusted task data:\n', 1)[1])
            if packet['original']['local_planning'] == 'codex-with-local-fallback':
                assert phase.read_text() == 'codex-off'
                raise codex_provider.Hold('unavailable')
            return controlled(packet), counted
        choice, edit = current_edit
        calls.append((choice, 'implementation', 'codex'))
        return edit, counted

    monkeypatch.setattr(codex_provider, 'connector_path', lambda: None if phase.read_text() == 'codex-off'
                        else installed / 'simulated.sock')
    monkeypatch.setenv('WORKBENCH_CODEX_SOCKET', str(installed / 'simulated.sock'))
    monkeypatch.setattr(codex_provider, 'generate', codex)
    monkeypatch.setattr(LocalProvider, 'generate', local)
    monkeypatch.setattr(local_browser_fixture, 'install_offline_guard', lambda: None)

    class BrowserFixtureRuntime(ControlledPreparer):
        def check(self, argv, *args, **kwargs):
            # The four journeys exercise the actual focused test and behavioral
            # controls. The unchanged, expensive suite and kernel proof have
            # controlled outcomes in this browser fixture only.
            if argv == ['bash', 'tests/run.sh']:
                inventory = self.expected_suite()
                return dict(command=argv, status='pass', rc=0, network_mode='private-loopback',
                            out='\n'.join([*(f'ok   {name}' for name in inventory),
                                          f'{len(inventory)} passed, 0 failed']),
                            usage=dict(controlled_fixture=True, cgroup_removed=True))
            return super().check(argv, *args, **kwargs)

    monkeypatch.setattr(trusted_preparation, 'Runtime', BrowserFixtureRuntime)
    monkeypatch.setattr(test_regression_evidence, 'ControlledPreparer', BrowserFixtureRuntime)

    class Runtime(CodexRuntime, BrowserFixtureRuntime):
        def preflight(self):
            return self.static_preflight()

    def launch(command):
        nonlocal current_edit
        from workbench import provider_routes, task_execution
        assert 'codex_task.py' in command[-2]
        cycle = Path(command[-1])
        ref = json.loads((cycle / 'workflow-link.json').read_text())['task']
        with store.connect() as connection:
            linked = task_execution.snapshot(connection, **ref)
            route = provider_routes.run_selection(connection, linked)
        runtime = Runtime(cycle / 'real-run', route)
        runtime.task_ref = ref
        case = by_choice[linked['original']['local_planning']]
        example = case['contract']['behavioral_evidence']['scenarios'][0]
        x, y = example['args']
        wanted = example['observe']['expected']
        old = 'print("MAP VIEWMATH TILEAT PASSED")'
        current_edit = (case['choice'], {'old': old, 'new': f'assert(VM.TileAt({x}, {y}) == {wanted})\n{old}'})
        assert real_execution.process_one(store, runtime)
        durable(cycle / 'lease-controller-result.json', b'{"restoration":{"restored":true},"simulated":true}')

    def advance(engine, path, stop_event=None):
        def prepare(runtime, template):
            task_session.provision(BrowserFixtureRuntime(runtime.root), template)
        return task_session.process_one(engine, path, stop_event, prepare=prepare, launch=launch)

    current_edit = None

    def advance_worker():
        try:
            while not stopped.wait(.04):
                with store.connect() as connection:
                    waiting = connection.execute(select(intakes.c.id, intakes.c.payload).join_from(
                        sessions, proposal_decisions,
                        sessions.c.id == proposal_decisions.c.id,
                    ).join(intakes).where(sessions.c.status == 'pending')).first()
                if waiting:
                    case = by_choice[waiting.payload['local_planning']]
                    if case['provider'] == 'local':
                        with store.begin() as connection:
                            at = intake.now()
                            connection.execute(model_attempts.insert().values(id=str(uuid4()), task_id=waiting.id,
                                source='workbench', backend='local', purpose='capability_validation',
                                requested_model=case['model'], actual_model=case['model'], routing_reason='Fixture probe',
                                started_at=at, finished_at=at, outcome='passed',
                                usage=dict(passed=True, wire_started=True, generated_upper_bound=60)))
                        local_browser_fixture.advance_local(store, Path(store.url.database), 'pass', None, example=None)
                        calls.append((case['choice'], 'implementation', 'local'))
                    else:
                        with pytest.MonkeyPatch.context() as patch:
                            patch.setattr('runpy.run_path', lambda _path: {'checked_limit': lambda _root: None})
                            patch.setattr(confine, 'root', lambda: str(installed / 'simulated-cgroup'))
                            assert task_sessions.process_one(store, advance=advance)
                else:
                    worker.tick(store)
        except Exception as error:
            errors.append(error)

    with serve(create_app(store)) as origin:
        install_offline_guard()
        thread = Thread(target=advance_worker, daemon=True)
        thread.start()
        try:
            run = subprocess.run(['node', 'e2e/delegated_acceptance.mjs', origin, str(path), str(phase),
                                  str(database), os.environ['WORKBENCH_PREPARATION_CONFIG']],
                                 env=environment(), cwd=ROOT, capture_output=True, text=True, timeout=850)
            if errors:
                raise errors[0]
            assert run.returncode == 0, run.stdout + run.stderr
            assert calls == [(case['choice'], action, case['provider'])
                             for case in policy['cases'] for action in ('planning', 'implementation')]
            with store.connect() as connection:
                assert len(connection.execute(select(model_attempts).where(model_attempts.c.purpose == 'planning')).all()) == 4
        finally:
            stopped.set()
            thread.join(60)
