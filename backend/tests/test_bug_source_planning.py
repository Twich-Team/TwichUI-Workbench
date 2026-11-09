"""Real local decoder, task claim, committed reads and accumulated observations."""
import copy
import json

import pytest
from sqlalchemy import select

from workbench import everyday, worker
from workbench.db import model_attempts, planning_jobs
from workbench.planning_provider import LocalBeforeGeneration, LocalProvider, PlanningUnavailable
from test_bug_workflow import enable_bugs, report, response
from test_everyday import installed, save_codex_policy  # noqa: F401
from test_verification_profiles import PRODUCTION, git


def scripted(monkeypatch, answers):
    calls = []

    def generate(self, packet, schema, expected_model):
        self.diagnostics = {'generation_state': 'not_started'}
        calls.append(copy.deepcopy(packet))
        value = answers.pop(0)
        if callable(value):
            value = value(packet, schema)
        if isinstance(value, Exception):
            if not isinstance(value, LocalBeforeGeneration):
                self.diagnostics.update(generation_state='uncertain', request_completion_limit=2600)
            raise value

        class Transport:
            seconds = 120

            def models(self, secret):
                return expected_model

            def context_budget(self, secret, body):
                return dict(prompt_tokens=8000, context_tokens=32768)  # Controlled count, no provider.

            def read(self, request, limit):
                program=packet.get('coverage',{}).get('reproducer_format')=='lua-phases-v1'
                assert len(request.data) <= (131072 if program else 65536) and limit == (65536 if program else 24000)
                system = json.loads(request.data)['messages'][0]['content']
                from workbench.bug_plan_review import ReviewResponse
                from workbench.bug_investigation_stage import InvestigationResponse
                if schema is InvestigationResponse:
                    assert 'before constructing a repair plan' in system
                elif schema is ReviewResponse:
                    assert 'model self-check' in system
                elif not program:
                    assert 'You may request app-owned read-only committed-source search or line inspection' in system
                assert 'You have no tools' not in system
                from workbench.bug_intent import Response as IntentResponse
                if schema is IntentResponse:
                    assert 'Propose a clear bounded repair scope' in system
                    assert 'No source editing, arbitrary tools, publication' in system
                elif not program and schema not in (ReviewResponse, InvestigationResponse):
                    assert 'a read-only inspection request, a plan, a useful question, or a limitation' in system
                    assert 'Missing code is not a real-world question or a limitation' in system
                    assert 'no shell, editing, approval, external-tool or publication authority' in system
                if packet.get('coverage', {}).get('require_inspection'):
                    outcomes = json.loads(request.data)['response_format']['json_schema']['schema']['$defs']['BugOutcome']['anyOf']
                    assert len(outcomes) == 1 and set(outcomes[0]['properties']) == {'inspection'}
                return json.dumps(dict(id='chatcmpl-controlled-inspection', model=expected_model,
                    usage=dict(prompt_tokens=40, completion_tokens=10, total_tokens=50),
                    choices=[dict(finish_reason='stop', message=dict(content=json.dumps(value)))])).encode()

        return self._infer(Transport(), 'inert', schema, json.dumps(packet, separators=(',', ':')),
                           expected_model=expected_model)

    monkeypatch.setattr(LocalProvider, 'generate', generate)
    return calls


def inspection(path=PRODUCTION):
    return dict(finding='I need the exact committed implementation before proposing the check.',
                plan=None, question=None, limitation=None,
                inspection=[dict(path=path, start=1, end=1)])


def test_investigation_reads_beyond_initial_selection_without_widening_write_scope(monkeypatch):
    from workbench import bug_source_planning as source
    from workbench.bug_evidence import InvestigationRead
    import hashlib
    packet=dict(base='pinned',files=[dict(path=f'!!!TwichUI/initial{n}.lua') for n in range(12)])
    raw=b'local value = 1\n'
    def committed(path, base):
        return raw,dict(path=path,git_blob='b'*40,sha256=hashlib.sha256(raw).hexdigest(),support_only=True,excerpts=[])
    monkeypatch.setattr(source,'committed',committed)
    for n in range(12,24):
        name=f'!!!TwichUI/followup{n}.lua'
        windows,_,_=source.inspect(packet,[InvestigationRead(path=name,start=1,end=1)])
        assert windows[0]['path']==name and packet['files'][-1]['support_only'] is True
    with pytest.raises(ValueError,match='24 files'):
        source.inspect(packet,[InvestigationRead(path='!!!TwichUI/beyond.lua',start=1,end=1)])
    assert len(packet['files'])==24


def missing_load():
    value = response()
    value['plan']['reproducer']['steps'][0] = dict(op='table', path=['unused'])
    return value


@pytest.mark.usefixtures('installed')
def test_finished_reproducer_error_can_be_corrected_with_one_claim_and_shared_usage(
    client, headers, store, monkeypatch,
):
    enable_bugs(monkeypatch)
    calls = scripted(monkeypatch, [missing_load(), response()])
    identity = client.post('/api/intake', headers=headers, json=report()).json()['id']
    assert worker.tick(store)
    item = client.get('/api/intake/'+identity).json()
    assert item['status'] == 'completed' and not item['tasks'] and not item['decisions']
    facts = calls[1]['validation_feedback']
    assert facts['rejected'] and facts['approved'] is False and facts['executed'] is False
    assert facts['errors'][0]['path'] == ['plan','reproducer']
    assert facts['errors'][0]['rule'] == 'Select one to six distinct existing Lua sources'
    with store.connect() as connection:
        [attempt] = connection.execute(select(model_attempts).where(model_attempts.c.task_id == identity)).mappings()
    assert attempt['usage']['requests_observed'] == 2 and attempt['usage']['values']['completion_tokens'] == 20
    assert len(calls) == 2 and item['jobs'][0]['attempts'] == 1


@pytest.mark.usefixtures('installed')
def test_unchanged_rejected_candidate_stops_without_unchanged_repeated_generation(
    client, headers, store, monkeypatch,
):
    enable_bugs(monkeypatch)
    calls = scripted(monkeypatch, [missing_load(), missing_load()])
    identity = client.post('/api/intake', headers=headers, json=report()).json()['id']
    assert worker.tick(store)
    item = client.get('/api/intake/'+identity).json()
    assert item['status'] == 'failed' and len(calls) == 2 and not item['proposals']
    with store.connect() as connection:
        usage = connection.execute(select(model_attempts.c.usage).where(model_attempts.c.task_id == identity)).scalar_one()
    assert usage['requests_observed'] == 2


@pytest.mark.parametrize('denial', ['unsafe','scope','ambiguous'])
@pytest.mark.usefixtures('installed')
def test_security_scope_and_ambiguous_denials_do_not_enter_corrective_generation(
    client, headers, store, monkeypatch, denial,
):
    enable_bugs(monkeypatch)
    value = response()
    if denial == 'unsafe':
        value['plan']['reproducer']['steps'][1]['path'] = ['os','execute']
    elif denial == 'scope':
        value['plan']['read_paths'].append('!!!TwichUI/uninspected.lua')
    else:
        value['question'] = 'A meaningful conflicting outcome.'
    calls = scripted(monkeypatch, [value])
    identity = client.post('/api/intake', headers=headers, json=report()).json()['id']
    assert worker.tick(store)
    item = client.get('/api/intake/'+identity).json()
    assert item['status'] == 'failed' and len(calls) == 1 and not item['proposals'] and not item['tasks']


@pytest.mark.usefixtures('installed')
def test_stateful_report_requires_real_committed_line_inspection_before_plan(
    client, headers, store, monkeypatch,
):
    from test_bug_process_rehearsal import three_observations

    enable_bugs(monkeypatch)
    calls = scripted(monkeypatch, [inspection(), three_observations(False)])
    request = report()
    request['description'] += ' Refresh it each time.'
    identity = client.post('/api/intake', headers=headers, json=request).json()['id']
    assert worker.tick(store)
    item = client.get('/api/intake/'+identity).json()
    assert item['status'] == 'completed', item['jobs']
    assert calls[0]['coverage']['require_inspection'] is True
    assert not calls[1]['coverage'].get('require_inspection')
    assert not item['tasks'] and len(calls) == 2


@pytest.mark.usefixtures('installed')
def test_corrective_requests_share_the_saved_request_operation_bound(
    client, headers, store, monkeypatch,
):
    enable_bugs(monkeypatch)
    values = [missing_load() for _ in range(16)]
    for i,value in enumerate(values):
        value['plan']['reproducer']['description'] += f' Candidate {i}.'
    calls = scripted(monkeypatch, values)
    identity = client.post('/api/intake', headers=headers, json=report()).json()['id']
    assert worker.tick(store)
    item = client.get('/api/intake/'+identity).json()
    assert item['status'] == 'failed' and len(calls) == 16 and not item['proposals']
    with store.connect() as connection:
        usage = connection.execute(select(model_attempts.c.usage).where(model_attempts.c.task_id == identity)).scalar_one()
    assert usage['requests_observed'] == 16 and usage['values']['completion_tokens'] == 160


@pytest.mark.usefixtures('installed')
def test_long_source_request_returns_exact_bounded_pages_and_verified_continuation(monkeypatch):
    from workbench.bug_evidence import InvestigationRead
    from workbench.bug_source_planning import inspect, verify_packet

    root = enable_bugs(monkeypatch)
    path = '!!!TwichUI/long_source.lua'
    lines = [f'local value_{i} = {i}' for i in range(120)]
    (root/path).write_text('\n'.join(lines)+'\n')
    git(root, 'add', '.')
    git(root, '-c','user.name=Fixture','-c','user.email=fixture@invalid','commit','-qm','Long committed fixture')
    initial = dict(base=git(root,'rev-parse','HEAD').strip(), files=[], policy_sha256='controlled')
    packet = copy.deepcopy(initial)
    windows, _, count = inspect(packet, [InvestigationRead(path=path,start=1,end=9999)])
    [first] = windows
    assert first['text'] == '\n'.join(lines[:80]) and first['through_line'] == 80
    assert first['next_line'] == 81 and first['requested_through_line'] == 9999
    assert first['coverage'] == 'partial requested committed lines' and count <= 6500
    windows, _, _ = inspect(packet, [InvestigationRead(path=path,start=81,end=9999)])
    assert windows[0]['text'] == '\n'.join(lines[80:])
    assert windows[0]['through_line'] == 120 and windows[0]['next_line'] is None
    assert verify_packet(initial,packet) == packet
    forged = copy.deepcopy(packet)
    forged['inspection_windows'][0]['next_line'] = 99
    with pytest.raises(ValueError,match='continuation'):
        verify_packet(initial,forged)
    with pytest.raises(ValueError,match='start must not exceed'):
        InvestigationRead(path=path,start=81,end=1)


@pytest.mark.usefixtures('installed')
def test_three_large_read_windows_share_output_bound_without_cutting_source_lines(monkeypatch):
    from workbench.bug_evidence import InvestigationRead
    from workbench.bug_source_planning import inspect, verify_packet

    root = enable_bugs(monkeypatch)
    paths = [f'!!!TwichUI/window_{i}.lua' for i in range(3)]
    for path in paths:
        (root/path).write_text(('local value = "'+'x'*140+'"\n')*120)
    git(root,'add','.')
    git(root,'-c','user.name=Fixture','-c','user.email=fixture@invalid','commit','-qm','Large bounded source fixture')
    initial = dict(base=git(root,'rev-parse','HEAD').strip(),files=[],policy_sha256='controlled')
    packet = copy.deepcopy(initial)
    windows,_,count = inspect(packet,[InvestigationRead(path=p,start=1,end=120) for p in paths])
    assert len(windows) == 3 and count == sum(len(w['text']) for w in windows) <= 6500
    assert all(w['through_line'] < 80 and w['next_line'] == w['through_line']+1 for w in windows)
    assert all(w['text'].endswith('"') for w in windows)
    assert verify_packet(initial,packet) == packet


def test_read_endpoint_diagnostics_preserve_safe_values_and_schema_branch_names(tmp_path):
    from workbench.bug_evidence import BugPlanningResponse
    from workbench.planning_response import Evidence
    from uuid import uuid4

    evidence = Evidence(tmp_path,str(uuid4()),BugPlanningResponse)
    value = dict(finding='A read-only inspection is needed.',plan=None,question=None,limitation=None,
                 inspection=[dict(path='tests/harness.lua',start=7,end=902)],
                 PRIVATE_KEY=dict(end=777777))
    evidence.parsed(value,{})
    evidence.close()
    rows = [json.loads(row) for row in evidence.file.read_text().splitlines()]
    structure = rows[-1]['structure']
    assert next(r for r in structure if r['path'] == ['inspection',0,'start'])['value'] == 7
    assert next(r for r in structure if r['path'] == ['inspection',0,'end'])['value'] == 902
    hidden = [r for r in structure if any(str(p).startswith('redacted-key-sha256:') for p in r['path'])]
    assert hidden and all('value' not in r for r in hidden)
    assert evidence.location(['inspection',0,'InvestigationRead','end']) == ['inspection',0,'InvestigationRead','end']


@pytest.mark.usefixtures('installed')
def test_explicit_bootstrap_from_another_discovered_test_reaches_a_bound_proposal(
    client, headers, store, monkeypatch,
):
    from test_bug_wire import wire_from_data
    from test_bug_workflow import CLIENT_SOURCE, CLIENT_TEST

    root = enable_bugs(monkeypatch)
    focused = 'tests/test_alert_history.lua'
    (root/focused).write_text('dofile(TESTS .. "test_alerts.lua")\n')
    git(root,'add','.')
    git(root,'-c','user.name=Fixture','-c','user.email=fixture@invalid','commit','-qm','Existing alert-history regression')
    value = response(True, bootstrap=True)
    value['plan'].update(focused_test=focused, read_paths=[CLIENT_SOURCE,CLIENT_TEST,focused],
                         writable_paths=[CLIENT_SOURCE,focused])
    wire = wire_from_data(value)
    wire['plan']['other_reads'] = []  # Setup, write and test selections supply all required reads.
    wire['plan']['reproducer']['setup']['test_path'] = CLIENT_TEST
    calls = scripted(monkeypatch,[wire])
    request = report(True)
    request['title'] = 'Wrong name in the online alert history'
    identity = client.post('/api/intake',headers=headers,json=request).json()['id']
    assert worker.tick(store)
    item = client.get('/api/intake/'+identity).json()
    assert item['status'] == 'completed', item['jobs']
    evidence = item['proposals'][0]['task_contract']['bug_evidence']
    assert evidence['plan']['focused_test'] == focused
    assert evidence['plan']['reproducer']['bootstrap']['test_path'] == CLIENT_TEST
    assert evidence['bootstrap_source'].endswith('return c\n')
    assert len(calls) == 1 and not item['tasks'] and not item['decisions']


@pytest.mark.usefixtures('installed')
def test_source_followup_has_one_claim_two_accounted_requests_and_private_receipts(
    client, headers, store, monkeypatch,
):
    enable_bugs(monkeypatch)
    calls = scripted(monkeypatch, [inspection(), response()])
    identity = client.post('/api/intake', headers=headers, json=report()).json()['id']
    assert worker.tick(store)
    item = client.get('/api/intake/'+identity).json()
    assert item['status'] == 'completed', item['jobs']
    assert len(calls) == 2 and len(item['provider_activity']) == 1
    assert item['jobs'][0]['attempts'] == 1
    assert calls[1]['inspection_result']['read_only'] is True
    assert [c['investigation_progress']['request_number'] for c in calls] == [1,2]
    assert [c['investigation_progress']['requests_after_this'] for c in calls] == [15,14]
    assert all(c['investigation_progress']['remaining_seconds'] <= 1800 for c in calls)
    assert calls[1]['source_memory']['retained_characters'] > 0
    assert any(e['text'] for f in calls[1]['files'] if f['path'] == PRODUCTION for e in f['excerpts'])
    with store.connect() as connection:
        [attempt] = connection.execute(select(model_attempts).where(model_attempts.c.task_id == identity)).mappings()
        [job] = connection.execute(select(planning_jobs).where(planning_jobs.c.intake_id == identity)).mappings()
    assert attempt['usage']['requests_observed'] == 2
    assert attempt['usage']['values'] == dict(prompt_tokens=80, completion_tokens=20, total_tokens=100)
    assert job['payload']['bug_inspection_packet']['base']
    assert len(list((everyday.ROOT/'artifacts/runs').glob('planning-evidence-*/events.jsonl'))) == 2
    assert not worker.tick(store)
    assert len(calls) == 2


@pytest.mark.usefixtures('installed')
def test_new_committed_dependency_is_inspected_read_only_and_revalidated(
    client, headers, store, monkeypatch,
):
    root = enable_bugs(monkeypatch)
    dependency = '!!!TwichUI/dependency.lua'
    (root/dependency).write_text('local marker = 1\n')
    git(root, 'add', '.')
    git(root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@invalid',
        'commit', '-qm', 'Committed read-only dependency fixture')
    calls = scripted(monkeypatch, [inspection(dependency), response()])
    identity = client.post('/api/intake', headers=headers, json=report()).json()['id']
    assert worker.tick(store)
    item = client.get('/api/intake/'+identity).json()
    assert item['status'] == 'completed', item['jobs']
    selected = next(f for f in calls[1]['files'] if f['path'] == dependency)
    assert selected['support_only'] is True
    assert selected['excerpts'][0]['text'] == 'local marker = 1'
    assert dependency not in item['proposals'][0]['task_contract']['writable_paths']


@pytest.mark.usefixtures('installed')
def test_literal_search_and_committed_harness_read_can_precede_the_plan(
    client, headers, store, monkeypatch,
):
    enable_bugs(monkeypatch)
    search = {**inspection(), 'inspection':[dict(query='MakeClient')]}
    read = inspection('tests/harness.lua')
    read['inspection'][0].update(start=2,end=4)
    calls = scripted(monkeypatch, [search, read, response()])
    identity = client.post('/api/intake', headers=headers, json=report()).json()['id']
    assert worker.tick(store)
    item = client.get('/api/intake/'+identity).json()
    assert item['status'] == 'completed', item['jobs']
    assert len(calls) == 3
    assert any(m['path']=='tests/harness.lua' for m in calls[1]['inspection_result']['search_matches'])
    selected = next(f for f in calls[2]['files'] if f['path']=='tests/harness.lua')
    assert selected['support_only'] and selected['excerpts'][0]['text']
    assert 'tests/harness.lua' not in item['proposals'][0]['task_contract']['writable_paths']
    with store.connect() as connection:
        usage = connection.execute(select(model_attempts.c.usage).where(model_attempts.c.task_id == identity)).scalar_one()
    assert usage['requests_observed'] == 3 and usage['values']['completion_tokens'] == 30


@pytest.mark.parametrize('old_version', [1, 2, 3, 4])
@pytest.mark.usefixtures('installed')
def test_old_one_request_consent_cannot_silently_authorize_source_followups(
    client, headers, store, monkeypatch, old_version,
):
    enable_bugs(monkeypatch)
    identity = client.post('/api/intake', headers=headers, json=report()).json()['id']
    with store.begin() as connection:
        job = connection.execute(select(planning_jobs).where(planning_jobs.c.intake_id == identity)).mappings().one()
        payload = copy.deepcopy(job['payload'])
        if old_version == 4:
            payload['local_planning_receipt'].pop('local_request_deadline_seconds',None)
        if old_version == 1:
            for key in ('investigation_version', 'max_local_requests', 'max_codex_requests', 'planning_deadline_seconds'):
                payload['local_planning_receipt'].pop(key)
        else:
            payload['local_planning_receipt'].update(investigation_version=old_version, max_local_requests=8 if old_version==4 else 4,
                planning_deadline_seconds=600 if old_version==4 else 300 if old_version==3 else 120)
        connection.execute(planning_jobs.update().where(planning_jobs.c.id == job['id']).values(payload=payload))
    monkeypatch.setattr(LocalProvider, 'generate', lambda *a, **kw:pytest.fail('Used stale one-request consent'))
    assert worker.tick(store)
    assert client.get('/api/intake/'+identity).json()['status'] == 'failed'


@pytest.mark.parametrize('path', ['../../.codex/auth.json', '!!!TwichUI/../private.lua', 'other/private.lua'])
@pytest.mark.usefixtures('installed')
def test_invalid_read_cannot_become_a_plan_or_start_another_provider_request(
    client, headers, store, monkeypatch, path,
):
    enable_bugs(monkeypatch)
    calls = scripted(monkeypatch, [inspection(path)])
    identity = client.post('/api/intake', headers=headers, json=report()).json()['id']
    assert worker.tick(store)
    item = client.get('/api/intake/'+identity).json()
    assert item['status'] == 'failed' and not item['proposals'] and not item['tasks']
    assert len(calls) == 1
    with store.connect() as connection:
        usage = connection.execute(select(model_attempts.c.usage).where(model_attempts.c.task_id == identity)).scalar_one()
    assert usage['values']['completion_tokens'] == 10


@pytest.mark.usefixtures('installed')
def test_later_readiness_refusal_keeps_previous_usage_and_cannot_switch_providers(
    client, headers, store, monkeypatch,
):
    from workbench import codex_provider

    enable_bugs(monkeypatch)
    save_codex_policy(store, both=True)
    calls = scripted(monkeypatch, [inspection(), LocalBeforeGeneration()])
    monkeypatch.setattr(codex_provider, 'generate', lambda *_: pytest.fail('Switched after prior generation'))
    request = {**report(), 'local_planning': 'qwen-with-codex-fallback'}
    identity = client.post('/api/intake', headers=headers, json=request).json()['id']
    assert worker.tick(store)
    item = client.get('/api/intake/'+identity).json()
    assert item['status'] == 'failed' and len(calls) == 2
    assert item['provider_activity'][0]['backend'] == 'local'
    assert item['jobs'][0]['diagnostics']['category'] == 'investigation_stopped'
    with store.connect() as connection:
        usage = connection.execute(select(model_attempts.c.usage).where(model_attempts.c.task_id == identity)).scalar_one()
    assert usage['requests_observed'] == 1 and usage['values']['completion_tokens'] == 10


@pytest.mark.usefixtures('installed')
def test_codex_before_generation_fallback_can_complete_local_source_followups(
    client, headers, store, monkeypatch,
):
    from workbench import codex_provider

    enable_bugs(monkeypatch)
    save_codex_policy(store, both=True)

    def unavailable(*args, **kwargs):
        raise codex_provider.Hold('unavailable')

    monkeypatch.setattr(codex_provider, 'generate', unavailable)
    calls = scripted(monkeypatch, [inspection(), response()])
    identity = client.post('/api/intake', headers=headers,
        json={**report(), 'local_planning':'codex-with-local-fallback'}).json()['id']
    assert worker.tick(store)
    item = client.get('/api/intake/'+identity).json()
    assert item['status'] == 'completed', item['jobs']
    assert len(calls) == 2 and len(item['provider_activity']) == 1
    assert item['provider_activity'][0]['backend'] == 'local'
    with store.connect() as connection:
        usage = connection.execute(select(model_attempts.c.usage).where(model_attempts.c.task_id == identity)).scalar_one()
    assert usage['requests_observed'] == 2 and usage['values']['completion_tokens'] == 20


@pytest.mark.parametrize('uncertain', [False, True])
@pytest.mark.usefixtures('installed')
def test_second_failure_keeps_completed_usage_or_holds_unknown_accounting(
    client, headers, store, monkeypatch, uncertain,
):
    enable_bugs(monkeypatch)
    failure = PlanningUnavailable('Uncertain controlled transport', 'transport') if uncertain else 'invalid object'
    calls = scripted(monkeypatch, [inspection(), failure])
    identity = client.post('/api/intake', headers=headers, json=report()).json()['id']
    assert worker.tick(store)
    item = client.get('/api/intake/'+identity).json()
    assert item['status'] == 'failed' and len(calls) == 2 and not item['tasks']
    with store.connect() as connection:
        usage = connection.execute(select(model_attempts.c.usage).where(model_attempts.c.task_id == identity)).scalar_one()
        payload = connection.execute(select(planning_jobs.c.payload).where(planning_jobs.c.intake_id == identity)).scalar_one()
    if uncertain:
        assert usage is None
        assert json.loads(payload['diagnostics']['known_usage'])['completion_tokens'] == 10
    else:
        assert usage['requests_observed'] == 2 and usage['values']['completion_tokens'] == 20


def test_function_ranges_do_not_execute_untrusted_code_and_skip_keyword_comments(tmp_path):
    from workbench.source_context import excerpts, function_ranges
    from workbench.bug_investigation import words

    marker = tmp_path/'must-not-exist'
    text = '-- refresh refresh refresh\n'*80
    text += f'os.execute("touch {marker}")\nlocal function Refresh(value)\n return value\nend\n'
    raw = text.encode()
    assert function_ranges(raw) and not marker.exists()
    files = [dict(path='!!!TwichUI/state.lua')]
    excerpts(files, {'!!!TwichUI/state.lua': ('unused', raw, text.splitlines())}, {'refresh'}, words)
    assert any('local function Refresh' in e['text'] and 'return value' in e['text'] for e in files[0]['excerpts'])
    assert not marker.exists()


@pytest.mark.usefixtures('installed')
def test_context_binding_is_stable_across_independent_python_hash_seeds(monkeypatch):
    import os
    import subprocess
    import sys
    from pathlib import Path

    enable_bugs(monkeypatch)
    program = '''import json
from workbench.bug_investigation import context,CATEGORY
from workbench.visual_scope import digest
value=dict(title='Name normalization leaves uppercase letters',description='Entering ALICE returns ALICE instead of alice.')
print(digest(context(value,'',dict(allowed_classes=[CATEGORY],scenario_generation=True))))
'''
    hashes = []
    project = Path(__file__).resolve().parents[2]
    for seed in ('1', '42', '991'):
        result = subprocess.run([sys.executable, '-c', program], capture_output=True, text=True,
            timeout=15, env={**os.environ, 'PYTHONHASHSEED':seed, 'PYTHONDONTWRITEBYTECODE':'1',
                             'PYTHONPATH':str(project/'backend')+os.pathsep+str(project/'scripts')})
        assert result.returncode == 0, result.stderr
        hashes.append(result.stdout.strip())
    assert len(set(hashes)) == 1


@pytest.mark.usefixtures('installed')
def test_material_model_policy_change_stops_before_another_inspection_request(
    client, headers, store, monkeypatch,
):
    from workbench import model_policy

    enable_bugs(monkeypatch)
    calls = scripted(monkeypatch, [inspection()])
    controlled = LocalProvider.generate

    def change_after_response(self, *args, **kwargs):
        answer = controlled(self, *args, **kwargs)
        policy = model_policy.read(store)
        policy.providers['local'].model = 'changed-local-model'
        model_policy.save(store, policy, model_policy.CapabilityReport(executors=[]))
        return answer

    monkeypatch.setattr(LocalProvider, 'generate', change_after_response)
    identity = client.post('/api/intake', headers=headers, json=report()).json()['id']
    assert worker.tick(store)
    item = client.get('/api/intake/'+identity).json()
    assert item['status'] == 'failed' and not item['tasks'] and len(calls) == 1


@pytest.mark.usefixtures('installed')
def test_repeated_read_advances_only_over_verified_contiguous_returned_prefix(monkeypatch):
    from workbench.bug_evidence import InvestigationRead
    from workbench.bug_source_planning import inspect,verify_packet,unread_start

    root=enable_bugs(monkeypatch)
    path='!!!TwichUI/paged.lua'
    lines=[f'local value_{i} = {i}' for i in range(180)]
    (root/path).write_text('\n'.join(lines)+'\n')
    git(root,'add','.')
    git(root,'-c','user.name=Fixture','-c','user.email=fixture@invalid','commit','-qm','Paging source')
    initial=dict(base=git(root,'rev-parse','HEAD').strip(),files=[],policy_sha256='controlled')
    packet=copy.deepcopy(initial)
    request=InvestigationRead(path=path,start=1,end=9999)
    pages=[]
    for _ in range(3):
        windows,_,_=inspect(packet,[request])
        pages+=windows
    assert [(p['line'],p['through_line']) for p in pages]==[(1,80),(81,160),(161,180)]
    assert [p['cached_prefix_through_line'] for p in pages]==[None,80,160]
    assert all(p['requested_start_line']==1 and p['requested_through_line']==9999 for p in pages)
    assert '\n'.join(p['text'] for p in pages)=='\n'.join(lines)
    assert verify_packet(initial,packet)==packet
    forged=copy.deepcopy(packet)
    forged['inspection_windows'][1]['cached_prefix_through_line']=79
    with pytest.raises(ValueError,match='cached source prefix'):
        verify_packet(initial,forged)
    forged=copy.deepcopy(packet)
    forged['inspection_windows'][0]['requested_start_line']=True
    with pytest.raises(ValueError,match='cached source prefix'):
        verify_packet(initial,forged)
    # Exact rereads remain possible; holes and other files cannot be skipped.
    windows,_,_=inspect(packet,[InvestigationRead(path=path,start=1,end=20)])
    assert windows[0]['line']==1 and windows[0]['text']=='\n'.join(lines[:20])
    assert unread_start([dict(path=path,line=5,through_line=80)],path,1,90)==1
    assert unread_start([dict(path='other.lua',line=1,through_line=80)],path,1,90)==1


@pytest.mark.usefixtures('installed')
def test_file_only_scope_is_corrected_before_any_plan_approval(client,headers,store,monkeypatch):
    enable_bugs(monkeypatch)
    bad=response()
    bad['plan']['scope']=PRODUCTION
    calls=scripted(monkeypatch,[bad,response()])
    identity=client.post('/api/intake',headers=headers,json=report()).json()['id']
    assert worker.tick(store)
    item=client.get('/api/intake/'+identity).json()
    assert item['status']=='completed' and len(calls)==2 and not item['decisions'] and not item['tasks']
    facts=calls[1]['validation_feedback']
    assert facts['errors'][0]['path']==['plan','scope']
    assert facts['errors'][0]['rule']=='Describe the bounded repair in plain language, not only a file path'


@pytest.mark.usefixtures('installed')
@pytest.mark.parametrize('mode',['source-grounded-lua-v1','source-grounded-intent-v1'])
def test_codex_larger_pages_keep_exact_source_and_many_small_windows_stay_bounded(monkeypatch,mode):
    from workbench.bug_evidence import InvestigationRead
    from workbench.bug_source_planning import inspect, verify_packet
    root = enable_bugs(monkeypatch)
    path = '!!!TwichUI/paged.lua'
    lines = [f'local value_{i} = {i}' for i in range(150)]
    (root/path).write_text('\n'.join(lines)+'\n')
    git(root,'add','.')
    git(root,'-c','user.name=Fixture','-c','user.email=fixture@invalid','commit','-qm','Paged fixture')
    initial=dict(base=git(root,'rev-parse','HEAD').strip(),files=[],policy_sha256='controlled',
                 plan_review=mode)
    packet=copy.deepcopy(initial)
    windows,_,size=inspect(packet,[InvestigationRead(path=path,start=1,end=150)],
                           page_lines=160,result_budget=16000)
    assert windows[0]['text']=='\n'.join(lines) and size<16000
    assert windows[0]['next_line'] is None and verify_packet(initial,packet)==packet
    # Many tiny real inspection/provenance windows must not exhaust a limit
    # intended for larger source buffers. The unique-byte bound still applies.
    for line in range(1,90):
        inspect(packet,[InvestigationRead(path=path,start=line,end=line)])
    assert len(packet['inspection_windows'])==90 and verify_packet(initial,packet)==packet
    packet['inspection_windows']=[copy.deepcopy(windows[0]) for _ in range(20)]
    assert sum(len(w['text']) for w in packet['inspection_windows'])>45000
    assert verify_packet(initial,packet)==packet
    bad=copy.deepcopy(packet)
    bad['inspection_windows'][0]['text']='forged'
    with pytest.raises(ValueError,match='evidence|disagree'):
        verify_packet(initial,bad)
    bad=copy.deepcopy(packet)
    bad['inspection_windows']*=13
    with pytest.raises(ValueError,match='bound'):
        verify_packet(initial,bad)


@pytest.mark.usefixtures('installed')
def test_all_saved_owner_behavior_clauses_reach_planning_and_self_review(store,monkeypatch):
    from workbench import bug_investigation,bug_source_planning,trusted_preparation,bug_plan_review
    enable_bugs(monkeypatch)
    original=report()
    packet=bug_investigation.context(original,'',trusted_preparation.configuration(store))
    packet['plan_review']='source-grounded-lua-v1'
    feedback=' '.join('Keep owner requirement number '+str(i)+'.' for i in range(15))
    wire=bug_source_planning.next_wire(packet,original,feedback,[],'Completed source facts.')
    assert 'Keep owner requirement number 14.' in wire['requested_behavior_clauses']
    assert wire['owner_reply']==feedback
    reviewed=bug_plan_review.request(wire,response())
    assert reviewed['requested_behavior_clauses']==wire['requested_behavior_clauses']
    assert reviewed['owner_reply']==feedback
