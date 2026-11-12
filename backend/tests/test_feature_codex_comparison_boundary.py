"""Exercise the supported comparison boundary without an external model call."""
from uuid import uuid4

import pytest
from sqlalchemy import select

from workbench import feature_permission, model_policy, planning_worker, worker
from workbench.db import model_attempts, proposal_decisions, real_execution_grants, real_execution_requests
from test_everyday import installed as installed, save_codex_policy
from test_feature_permission import enable, ready
from test_feature_planning import ControlledFeaturePlanner, submit


def test_separately_selected_codex_feature_gets_a_bound_run_without_changing_local_preferences(
    client, headers, store, installed, monkeypatch,
):
    save_codex_policy(store)
    from workbench.service import now
    catalog = model_policy.CapabilityReport(executors=[model_policy.ExecutorCapability(
        backend='codex', executor='Controlled catalog', source='No external call', observed_at=now(),
        models=[model_policy.ModelCapability(model='simulated-codex')], limitation='Controlled only')])
    monkeypatch.setattr(model_policy, 'capabilities', lambda: catalog)
    item, _ = submit(client, headers, local_planning='codex-once')
    planner = ControlledFeaturePlanner()
    assert worker.tick(store, planning_adapter=planner)
    assert worker.tick(store, planning_adapter=planner)
    proposal = client.get('/api/intake/' + item['id']).json()['proposals'][0]
    command = dict(command_id=str(uuid4()), proposal_id=proposal['id'], binding=proposal['binding'],
        scope_revision=proposal['scope_revision'], action='approve', note='Controlled comparison boundary only.')
    assert client.post('/api/intake/' + item['id'] + '/decisions', headers=headers, json=command).status_code == 200
    assert planning_worker.apply_one(store)
    enable()
    availability = client.get('/api/features/' + item['id'] + '/run-offer')
    assert availability.status_code == 200
    offer = availability.json()['offer']
    assert offer['provider'] == 'codex' and offer['model'] == 'simulated-codex'
    assert offer['budget']['implementation_fallback'] is False
    policy_before = model_policy.read(store)
    result = client.post('/api/features/' + item['id'] + '/run', headers=headers,
        json=dict(command_id=str(uuid4()), binding=offer['binding']))
    assert result.status_code == 200, result.text
    assert model_policy.read(store) == policy_before
    with store.connect() as c:
        assert len(c.execute(select(model_attempts)).all()) == 2  # Controlled planning only.
        decisions = list(c.execute(select(proposal_decisions)).mappings())
        assert len(decisions) == 2
        permission = next(d['payload']['feature_run'] for d in decisions if 'feature_run' in d['payload'])
        assert permission['scope']['provider'] == 'codex'
        assert permission['scope']['route']['model'] == 'simulated-codex'
        assert not c.execute(select(real_execution_grants)).first()
        assert not c.execute(select(real_execution_requests)).first()
    from workbench import feature_execution, feature_preparation, feature_steps, feature_step_source, regression_preparation
    from workbench.visual_scope import digest
    assert feature_execution.apply_one(store)
    decision = result.json()['id']
    with store.connect() as c:
        _, permission = feature_steps.parent(c, decision)
        task = next(t['task_id'] for t in permission['scope']['graph'] if not t['dependencies'])
        packet = feature_preparation.packet(c, decision, task, None)
        root, base = regression_preparation.repository()[:2]
        production = next(f['path'] for f in packet['files'] if f['path'].startswith('!!!TwichUI/'))
        test = next(f['path'] for f in packet['files'] if f['path'].startswith('tests/test_'))
        history = []
        for path in (production, test):
            requests = [dict(path=path, start=1, end=80)]
            packet, observations = feature_step_source.inspect(packet, root, base, requests)
            history.append(dict(confirmed_finished=True, failure=None, inspection=dict(
                requests=requests, result_binding=digest(observations), feature_input_binding=digest(None))))
        prepared = feature_preparation.scope_packet(c, decision, task, None, history, remaining=1)
        assert prepared['coverage']['scope_decision_required'] is True
        wire = feature_steps.wire_schema(prepared['files'], prepared['coverage'])
        assert all('inspection' not in branch['required'] for branch in wire['anyOf'])


def test_codex_feature_execute_receives_current_step_before_any_inference(
    client, headers, store, installed, tmp_path, monkeypatch,
):
    from test_feature_steps import offer_fixture
    from test_codex_repair_feedback import Runtime
    from workbench import codex_provider, codex_runtime, task_execution
    decision, task, _, prepared, offered = offer_fixture(client, headers, store, tmp_path)
    runtime = Runtime(prepared.root, dict(model='controlled-codex', effort='high'))
    runtime.expected_suite = prepared.expected_suite
    with store.connect() as c:
        approval = c.execute(select(proposal_decisions).where(proposal_decisions.c.id == decision)).mappings().one()
        runtime.workflow_task = task_execution.snapshot(c, approval['intake_id'], task, decision)
    monkeypatch.setattr(codex_runtime.confine, 'root', lambda: '/controlled-test-boundary')
    prompts = []

    def capture(model, effort, prompt, schema, stop, **kwargs):
        import json
        from workbench.real_workspace import sha
        assert model == 'controlled-codex' and effort == 'high' and not stop()
        assert 'Implement only the CURRENT feature step' in prompt
        assert 'Later dependent behavior must wait' in prompt
        assert '"checked_predecessor": null' in prompt
        assert runtime.workflow_task['task']['title'] in prompt
        assert all(path in prompt for path in runtime.read_paths)
        intents = list(runtime.root.glob('codex-intent-*-1.json'))
        assert len(intents) == 1  # Durable before the transport can be called.
        intent = json.loads(intents[0].read_text())
        assert intent['request_sha256'] == sha(codex_provider.encode_request(
            model, effort, prompt, schema, kwargs['request_seconds'])+b'\n')
        prompts.append(prompt)
        raise codex_provider.Hold('canceled', started=False)

    monkeypatch.setattr(codex_provider, 'generate', capture)
    with pytest.raises(codex_provider.Hold):
        runtime.execute(str(uuid4()), str(uuid4()), lambda: True)
    assert len(prompts) == 1


def test_existing_run_command_has_no_provider_override_field(
    client, headers, store, installed,
):
    identity, _ = ready(client, headers, store)
    enable()
    offered = feature_permission.offer(store, identity)
    result = client.post('/api/features/' + identity + '/run', headers=headers,
        json=dict(command_id=str(uuid4()), binding=offered['binding'],
            ai_settings=dict(primary=dict(provider='codex', model='simulated-codex'), fallback=None)))
    assert result.status_code == 422
    with store.connect() as c:
        assert len(c.execute(select(proposal_decisions)).all()) == 1
        assert not c.execute(select(real_execution_grants)).first()
        assert not c.execute(select(real_execution_requests)).first()


def test_long_context_does_not_replace_the_topic_test_with_an_unrelated_test(tmp_path, monkeypatch):
    import subprocess
    from workbench import feature_planning, regression_preparation
    root = tmp_path / 'source-selection'
    root.mkdir()
    files = {
        '!!!TwichUI/map/Canvas.lua': '-- map zoom view\nlocal Canvas = {}\n',
        '!!!TwichUI/modules/Notifications.lua': '-- login settings source state\n' * 30,
        'tests/test_map_canvas.lua': 'dofile(TESTS .. "harness.lua")\nlocal f = loadfile(ROOT .. "map/Canvas.lua")\n',
        'tests/harness.lua': 'function MakeClient() return {} end\n',
    }
    for number in range(20):
        files[f'tests/test_archive_{number:02}.lua'] = (
            '-- login settings source state\n' * 30 + 'loadfile(ROOT .. "modules/Notifications.lua")\n')
    for name, value in files.items():
        file = root / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(value)
    def git(*args):
        return subprocess.check_output(['git', '-c', 'core.hooksPath=/dev/null',
            '-c', 'user.name=Controlled', '-c', 'user.email=fixture@invalid', *args], cwd=root, text=True).strip()
    git('init', '-q', '-b', 'main')
    git('add', '.')
    git('commit', '-qm', 'Controlled source-selection evidence')
    monkeypatch.setattr(regression_preparation, 'repository', lambda: (root, git('rev-parse', 'HEAD'), {}))
    packet = feature_planning.research(dict(title='Map canvas zoom', description='login settings source state ' * 50))
    paths = [item['path'] for item in packet['files']]
    assert '!!!TwichUI/map/Canvas.lua' in paths
    assert 'tests/test_map_canvas.lua' in paths
    assert 'tests/harness.lua' in paths
    assert not any(path.startswith('tests/test_archive_') for path in paths)


def test_feature_wire_prevents_task_relationships_that_local_validation_refuses():
    from workbench.schemas import PlanningOutput
    from workbench.planning_response import wire_schema
    ordinary = wire_schema(PlanningOutput)
    feature = wire_schema(PlanningOutput, dict(feature_planning=True))
    assert ordinary['properties']['related_ids']['maxItems'] == 6
    assert feature['properties']['related_ids']['maxItems'] == 0
    assert feature['properties']['evidence_ids'] == ordinary['properties']['evidence_ids']


def test_codex_feature_plan_and_source_use_the_existing_complete_envelope_bound(monkeypatch):
    import json
    from workbench import admission, codex_provider
    from workbench.schemas import PlanningOutput
    provider = codex_provider.Planner(dict(model='controlled-codex', effort='high'), lambda: False)
    provider.source_manifest = {}  # No source disclosure in this transport-only fixture.
    provider.reference_manifest = {}
    calls = []

    def generate(model, effort, prompt, schema, stop, **kwargs):
        assert len(prompt.encode()) > codex_provider.MAX_PACKET
        assert kwargs['input_limit'] == codex_provider.MAX_BUG_INPUT
        assert kwargs['request_seconds'] == codex_provider.SECONDS
        assert schema['properties']['related_ids']['maxItems'] == 0
        calls.append(prompt)
        return json.loads(admission.FIXTURE.read_text())['planning'], dict(
            prompt_tokens=10, completion_tokens=5, reasoning_tokens=0, total_tokens=15)

    monkeypatch.setattr(codex_provider, 'generate', generate)
    provider.generate(dict(original=dict(feature_planning=True), files=[],
        coverage=dict(feature_planning=True), feedback='x' * 30000), PlanningOutput,
        expected_model='controlled-codex')
    assert len(calls) == 1


def test_scope_projection_preserves_complete_committed_lines(client, headers, store, installed, monkeypatch):
    from test_feature_preparation import context
    from workbench import feature_planning, feature_preparation, feature_steps
    decision, task, _, _ = context(client, headers, store)
    from test_verification_profiles import PRODUCTION
    raw = ('-- committed line ' + 'x' * 200 + '\n') * 10
    actual_file = feature_steps._file

    def source(root, base, path):
        metadata, value = actual_file(root, base, path)
        return metadata, raw.encode() if path == PRODUCTION else value

    monkeypatch.setattr(feature_steps, '_file', source)
    monkeypatch.setattr(feature_planning, 'research', lambda *args, **kwargs: dict(
        related=[], files=[dict(path=PRODUCTION, excerpts=[dict(line=1, text='names only')])]))
    with store.connect() as c:
        packet = feature_preparation.packet(c, decision, task, None)
    excerpt = packet['files'][0]['excerpts'][0]['text']
    assert 0 < len(excerpt) <= 1000
    assert excerpt.splitlines() == raw.splitlines()[:len(excerpt.splitlines())]


@pytest.mark.parametrize('fault', [None, 'unknown', 'reported-model', 'response-event'])
def test_codex_scope_preflight_recovery_requires_zero_requests_and_schema_only_evidence(
    store, installed, fault,
):
    from workbench import everyday
    from workbench.feature_scope_settlement import not_started
    from workbench.planning_response import Evidence
    from workbench.feature_steps import StepScope
    from workbench.service import now
    identity = str(uuid4())
    attempt = dict(id=identity, backend='codex', outcome='failed', actual_model=None,
        started_at=now(), usage=dict(source='Workbench verified no Codex turn started',
            units='tokens', status='not_started', requests_observed=0,
            values=dict(prompt_tokens=0, completion_tokens=0, reasoning_tokens=0, total_tokens=0)))
    evidence = Evidence(everyday.ROOT, identity, StepScope)
    path = evidence.file
    evidence.close()
    attempt['finished_at'] = now()
    record = dict(operation_id=identity, diagnostics=dict(generation_state='not_started'))
    if fault == 'unknown':
        attempt['usage'] = None
    elif fault == 'reported-model':
        attempt['actual_model'] = 'controlled-codex'
    elif fault == 'response-event':
        path.write_bytes(path.read_bytes() * 2)
    with store.connect() as c:
        result = not_started(c.engine, record, attempt)
    assert bool(result) is (fault is None)
    if result:
        assert result['no_provider_request'] and result['original_records_and_zero_usage_retained']
        assert attempt['usage']['requests_observed'] == 0


def test_codex_scope_wire_has_an_object_root_and_unwraps_only_its_explicit_decision(monkeypatch):
    from workbench import codex_provider
    from workbench.feature_steps import StepScope
    provider = codex_provider.Planner(dict(model='controlled-codex', effort='high'), lambda: False)
    provider.source_manifest = {}
    provider.reference_manifest = {}
    decision = dict(inspection=[dict(path='tests/test_actual.lua', start=1, end=80)], limitation='')

    def generate(model, effort, prompt, schema, stop, **kwargs):
        assert schema['type'] == 'object' and 'anyOf' not in schema
        assert 'anyOf' in schema['properties']['scope'] and '$defs' in schema
        assert 'scope field' in prompt
        return dict(scope=decision), dict(prompt_tokens=10, completion_tokens=5, total_tokens=15)

    monkeypatch.setattr(codex_provider, 'generate', generate)
    result, _, _ = provider.generate(dict(original=dict(feature_planning=True), files=[],
        coverage=dict(focused_test_candidates=['tests/test_actual.lua'])), StepScope,
        expected_model='controlled-codex')
    assert result == decision
    assert StepScope.model_validate(result).inspection


@pytest.mark.parametrize('fault', [None, 'write-capability', 'open-producer', 'output', 'usage'])
def test_failed_readonly_scope_quarantine_keeps_unknown_usage_and_requires_actual_isolation(
    store, installed, fault,
):
    import json
    from pathlib import Path
    from workbench import everyday
    from workbench.feature_scope_settlement import readonly_terminal
    identity, receipt = str(uuid4()), str(uuid4())
    attempt = dict(id=identity, backend='codex', purpose='feature_step_scope',
        outcome='failed', finished_at='2026-11-12T00:00:00+00:00', usage=None)
    record = dict(operation_id=identity, diagnostics=dict(provider_receipt=receipt,
        generation_state='finished', provider_terminal_confirmed=True,
        terminal_status='failed', partial_output=False))
    capability = dict(kind='tool-free-codex-investigation-v1', source_writes=False,
        execution_dispatch=False, external_action_tools=False, owner_config_mounted=False,
        empty_work_directory=True, shell_disabled=True, sandbox='readOnly', approval_policy='never')
    events = [dict(phase='turn-started', thread_id='controlled-thread', turn_id='controlled-turn'),
        dict(phase='isolation-verified', capabilities=capability),
        dict(phase='lifecycle-terminal', thread_id='controlled-thread', turn_id='controlled-turn', status='failed', usage=None),
        dict(phase='turn-held', terminal_status='failed', partial=False, usage=None, output_bytes=0),
        dict(phase='connection-closed')]
    if fault == 'write-capability':
        capability['source_writes'] = True
    elif fault == 'open-producer':
        events.pop()
    elif fault == 'output':
        events[-2]['output_bytes'] = 1
    elif fault == 'usage':
        attempt['usage'] = dict(status='partial')
    folder = everyday.ROOT/'artifacts/codex-evidence'
    folder.mkdir(parents=True, mode=0o700)
    file = folder/(receipt+'.jsonl')
    file.write_text(''.join(json.dumps(e)+'\n' for e in events))
    file.chmod(0o600)
    authority = everyday.ROOT/'docs/OWNER_READONLY_RECOVERY_20261108.md'
    authority.parent.mkdir()
    authority.write_bytes((Path(__file__).resolve().parents[2]/'docs'/authority.name).read_bytes())
    with store.connect() as c:
        result = readonly_terminal(c.engine, record, attempt)
    assert bool(result) is (fault is None)
    if result:
        assert result['usage'] is None and result['old_output_reusable'] is False
        assert attempt['usage'] is None


@pytest.mark.parametrize('fault', [None, 'path', 'hash', 'line', 'text'])
def test_feature_search_lines_must_match_the_explicit_committed_disclosure(fault):
    from workbench import codex_provider
    provider = codex_provider.Planner(dict(model='controlled-codex', effort=None), lambda: False)
    path, digest = '!!!TwichUI/Core.lua', 'a' * 64
    provider.source_manifest = {path: digest}
    provider.reference_manifest = {}
    provider.source_lines = {path: ['local observed = true']}
    match = dict(path=path, sha256=digest, line=1, text='local observed = true')
    if fault:
        match[{'path':'path', 'hash':'sha256', 'line':'line', 'text':'text'}[fault]] = (
            2 if fault == 'line' else 'unapproved')
    packet = dict(files=[], source_searches=[dict(matches=[match])])
    if fault:
        with pytest.raises(codex_provider.Hold):
            provider.validate_source(packet)
    else:
        provider.validate_source(packet)


def test_scope_recovery_can_select_one_request_without_resetting_prior_limits(client, headers, store, installed):
    from test_feature_scope_recovery import failed
    from workbench import feature_scope_recovery
    from workbench.db import planning_jobs
    identity, decision, task, job_id, old, payload, offered = failed(client, headers, store)
    command = dict(command_id=str(uuid4()), binding=offered['binding'], generation_requests=1)
    endpoint = '/api/features/' + identity + '/prepare-step-again'
    assert client.post(endpoint, headers=headers, json=command).status_code == 200
    assert client.post(endpoint, headers=headers, json=command).status_code == 200
    assert client.post(endpoint, headers=headers, json={**command, 'generation_requests':3}).status_code == 409
    with store.connect() as c:
        job = c.execute(select(planning_jobs).where(planning_jobs.c.id == job_id)).mappings().one()
        parent = c.execute(select(proposal_decisions).where(proposal_decisions.c.id == decision)).mappings().one()
        assert dict(parent) == old
        assert job['payload']['scope_history'] == payload['scope_history']
        maximum, _, _ = feature_scope_recovery.allowance(c, job, task, parent['payload']['feature_run'])
        assert maximum == len(payload['scope_history']) + 1
    for count in (0, 4):
        assert client.post(endpoint, headers=headers, json={**command,
            'command_id':str(uuid4()), 'generation_requests':count}).status_code == 422
