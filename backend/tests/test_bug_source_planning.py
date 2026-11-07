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
        if isinstance(value, Exception):
            if not isinstance(value, LocalBeforeGeneration):
                self.diagnostics['generation_state'] = 'uncertain'
            raise value

        class Transport:
            seconds = 120

            def models(self, secret):
                return expected_model

            def read(self, request, limit):
                assert len(request.data) <= 24000
                system = json.loads(request.data)['messages'][0]['content']
                assert 'You may request app-owned read-only committed-source search or line inspection' in system
                assert 'You have no tools' not in system
                assert 'a read-only inspection request, a plan, a useful question, or a limitation' in system
                assert 'Missing code is not a real-world question or a limitation' in system
                assert 'no shell, editing, approval, external-tool or publication authority' in system
                if packet.get('coverage', {}).get('require_inspection'):
                    outcomes = json.loads(request.data)['response_format']['json_schema']['schema']['$defs']['BugOutcome']['anyOf']
                    assert len(outcomes) == 1 and outcomes[0]['properties']['plan'] == {'type':'null','const':None}
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
def test_corrective_requests_share_the_four_request_operation_bound(
    client, headers, store, monkeypatch,
):
    enable_bugs(monkeypatch)
    values = [missing_load() for _ in range(4)]
    for i,value in enumerate(values):
        value['plan']['reproducer']['description'] += f' Candidate {i}.'
    calls = scripted(monkeypatch, values)
    identity = client.post('/api/intake', headers=headers, json=report()).json()['id']
    assert worker.tick(store)
    item = client.get('/api/intake/'+identity).json()
    assert item['status'] == 'failed' and len(calls) == 4 and not item['proposals']
    with store.connect() as connection:
        usage = connection.execute(select(model_attempts.c.usage).where(model_attempts.c.task_id == identity)).scalar_one()
    assert usage['requests_observed'] == 4 and usage['values']['completion_tokens'] == 40


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


@pytest.mark.usefixtures('installed')
def test_old_one_request_consent_cannot_silently_authorize_source_followups(
    client, headers, store, monkeypatch,
):
    enable_bugs(monkeypatch)
    identity = client.post('/api/intake', headers=headers, json=report()).json()['id']
    with store.begin() as connection:
        job = connection.execute(select(planning_jobs).where(planning_jobs.c.intake_id == identity)).mappings().one()
        payload = copy.deepcopy(job['payload'])
        for key in ('investigation_version', 'max_local_requests', 'max_codex_requests', 'planning_deadline_seconds'):
            payload['local_planning_receipt'].pop(key)
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

    def unavailable(*args):
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
