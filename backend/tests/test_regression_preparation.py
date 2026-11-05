"""Controlled requests exercise reusable preparation, never inference or publication."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import subprocess
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from workbench import planning_worker, regression_preparation as preparation
from workbench.db import (
    backlog_items, intakes, model_attempts, planning_jobs, proposal_decisions,
    real_execution_grants, real_execution_requests, real_publications, task_run_offers, task_sessions,
    write_transaction,
)
from workbench.schemas import IntakeInput


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.DEVNULL).decode().strip()


def commit(root):
    git(root, 'add', '.')
    git(root, '-c', 'user.name=Controlled fixture', '-c', 'user.email=fixture@invalid',
        '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'Disposable preparation fixture')
    return git(root, 'rev-parse', 'HEAD')


@pytest.fixture
def research(tmp_path, monkeypatch):
    root = tmp_path / 'research'
    root.mkdir()
    git(root, 'init', '-q')
    git(root, 'remote', 'add', 'origin', 'https://github.com/Twich-Team/TwichUI.git')
    tests = root / 'tests'
    tests.mkdir()
    # Real runner syntax; its deliberate side effect must never be executed.
    (tests / 'run.sh').write_text('''#!/bin/bash
touch MUST_NOT_RUN
for t in test_*.lua; do
lua5.1 -e 'require("bit")' "$t"
# PASSED / ERROR:
done
''')
    (tests / 'harness.lua').write_text('-- Controlled framework evidence; not a model test\n')
    (tests / 'test_profiles.lua').write_text('assert(true) -- fixture profile coverage\n')
    (tests / 'test_share.lua').write_text('assert(true) -- fixture sharing coverage\n')
    (tests / 'test_link.lua').symlink_to(tests / 'test_share.lua')
    base = commit(root)
    monkeypatch.setenv('WORKBENCH_RESEARCH_ROOT', str(root))
    return root, base


def body(path='tests/test_profiles.lua', **changes):
    return dict(request_id=str(uuid4()), kind='feature', title='Add profile regression coverage',
        description=f'Add coverage in {path} for an invalid profile name being rejected.',
        visual_requirement='nonvisual', regression_tests=dict(
            acceptance=['Invalid profile names are rejected without changing the current profile.']), **changes)


def request(client, headers, value=None):
    response = client.post('/api/intake', json=value or body(), headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def read(client, identity):
    response = client.get('/api/intake/' + identity)
    assert response.status_code == 200, response.text
    return response.json()


def counts(store):
    with store.connect() as c:
        return {table.name: c.scalar(select(func.count()).select_from(table)) for table in (
            model_attempts, task_run_offers, proposal_decisions, real_execution_grants,
            real_execution_requests, real_publications, task_sessions, backlog_items)}


class ForbiddenModel:
    def generate(self, *_):
        pytest.fail('A reusable preparation request must not invoke a model')


def test_two_distinct_requests_prepare_without_registration_or_authority(store, client, headers, research):
    before = counts(store)
    first = request(client, headers)
    second_input = body('tests/test_share.lua')
    second_input['title'] = 'Add share payload regressions'
    second_input['description'] = 'In tests/test_share.lua cover truncated payloads being rejected.'
    second_input['regression_tests']['acceptance'] = ['A truncated payload is rejected without applying settings.']
    second = request(client, headers, second_input)
    for _ in range(2):
        assert planning_worker.process_one(store, ForbiddenModel())
    for initial, path in [(first, 'tests/test_profiles.lua'), (second, 'tests/test_share.lua')]:
        saved = read(client, initial['id'])
        assert saved['status'] == 'completed' and not saved['decisions'] and not saved['tasks']
        proposal = saved['proposals'][0]
        assert proposal['basis'] == 'preparation'
        contract = proposal['task_contract']
        assert contract['repository'] == 'Twich-Team/TwichUI'
        assert contract['resolved_base'] == research[1]
        assert contract['writable_paths'] == [path]
        assert contract['acceptance'] == saved['original']['regression_tests']['acceptance']
        assert contract['intended_behavior'] == saved['original']['description']
        assert not contract['scope_missing']
        assert contract['execution_authorized'] is False and contract['publication_authorized'] is False
        assert all(c['status'] == 'not_run' for c in contract['verification_commands'])
        assert contract['verification_commands'][-1]['argv'] == ['bash', 'tests/run.sh']
        assert any('known-good' in c and 'negative controls' in c for c in contract['independent_checks'])
        assert contract['pr_base_branch'] == 'main' and contract['pr_draft'] is True
        assert contract['resource_limits']['implementation_attempts'] == 1
        assert contract['resource_limits']['fallback_attempts'] == 0
        assert len(contract['evidence']) == 3
        offer = client.get(f"/api/intake/{initial['id']}/proposals/{proposal['id']}/run-offer").json()
        assert offer['available'] is False
        assert request(client, headers, saved['original'])['id'] == initial['id']
    assert counts(store) == before
    assert not planning_worker.process_one(store, ForbiddenModel())
    assert not list(research[0].rglob('MUST_NOT_RUN'))


def test_commit_evidence_ignores_dirty_worktree_and_is_stable_on_reload(store, client, headers, research):
    root, base = research
    original = (root / 'tests/test_profiles.lua').read_bytes()
    (root / 'tests/test_profiles.lua').write_text('dirty untrusted working tree\n')
    item = request(client, headers)
    planning_worker.process_one(store, ForbiddenModel())
    view = read(client, item['id'])
    p = view['proposals'][0]
    evidence = next(e for e in p['task_contract']['evidence'] if e['path'] == 'tests/test_profiles.lua')
    assert evidence['sha256'] == hashlib.sha256(original).hexdigest()
    (root / 'tests/test_profiles.lua').write_text('newer committed content\n')
    assert commit(root) != base
    assert read(client, item['id']) == view


@pytest.mark.parametrize('problem', ['unconfigured', 'wrong-origin', 'no-head', 'bad-runner', 'missing-harness', 'symlink', 'large-test'])
def test_missing_evidence_yields_requirement_not_confidence(store, client, headers, research, monkeypatch, problem):
    root, _ = research
    value = body()
    if problem == 'unconfigured':
        monkeypatch.delenv('WORKBENCH_RESEARCH_ROOT')
    elif problem == 'wrong-origin':
        git(root, 'remote', 'set-url', 'origin', 'https://example.invalid/untrusted/repo.git')
    elif problem == 'no-head':
        git(root, 'symbolic-ref', 'HEAD', 'refs/heads/unborn')
    elif problem == 'bad-runner':
        (root / 'tests/run.sh').write_text('do_something_else\n')
        commit(root)
    elif problem == 'missing-harness':
        (root / 'tests/harness.lua').unlink()
        commit(root)
    elif problem == 'symlink':
        value = body('tests/test_link.lua')
    elif problem == 'large-test':
        (root / 'tests/test_profiles.lua').write_text('x' * 131073)
        commit(root)
    item = request(client, headers, value)
    assert planning_worker.process_one(store, ForbiddenModel())
    saved = read(client, item['id'])
    assert saved['status'] == 'waiting_for_information'
    p = saved['proposals'][0]
    assert p['task_contract']['scope_missing']
    assert p['approval_blockers']
    assert not p['task_contract']['verification_commands']
    assert not p['task_contract']['execution_authorized']


def test_ambiguous_paths_require_clarification_and_can_reuse_same_request(store, client, headers, research):
    value = body()
    value['description'] = 'Cover rejection of invalid profiles.'
    item = request(client, headers, value)
    planning_worker.process_one(store, ForbiddenModel())
    old = read(client, item['id'])
    assert old['status'] == 'waiting_for_information'
    contract = old['proposals'][0]['task_contract']
    assert not contract['writable_paths'] and contract['path_candidates']
    response = client.post('/api/intake/' + item['id'] + '/clarification', headers=headers,
        json=dict(command_id=str(uuid4()), revision=old['revision'], text='Use tests/test_profiles.lua.'))
    assert response.status_code == 200, response.text
    planning_worker.process_one(store, ForbiddenModel())
    current = read(client, item['id'])
    assert current['original'] == old['original']
    assert current['status'] == 'completed'
    assert len(current['proposals']) == 2
    assert current['proposals'][0]['task_contract']['writable_paths'] == ['tests/test_profiles.lua']
    assert current['proposals'][1] == old['proposals'][0]


def test_scope_approval_decomposes_once_but_cannot_self_authorize(store, client, headers, research):
    item = request(client, headers)
    planning_worker.process_one(store, ForbiddenModel())
    p = read(client, item['id'])['proposals'][0]
    decision = dict(command_id=str(uuid4()), proposal_id=p['id'], binding=p['binding'],
                    scope_revision=p['scope_revision'], action='approve', note='Approve proposed scope only.')
    forged = {**decision, 'run_budget': dict(offer_id=str(uuid4()), offer_fingerprint='0' * 64, activate_once=True)}
    assert client.post('/api/intake/' + item['id'] + '/decisions', headers=headers, json=forged).status_code == 409
    assert client.post('/api/intake/' + item['id'] + '/decisions', headers=headers, json=decision).status_code == 200
    before = counts(store)
    assert planning_worker.apply_one(store)
    assert planning_worker.process_one(store, ForbiddenModel())
    assert not planning_worker.process_one(store, ForbiddenModel())
    saved = read(client, item['id'])
    assert saved['status'] == 'tasks_ready' and len(saved['tasks']) == 2
    assert saved['decisions'][0]['status'] == 'applied'
    after = counts(store)
    assert after.pop('backlog_items') == before.pop('backlog_items') + 2
    assert after == before


def test_contract_is_in_review_binding_and_forged_old_review_is_stale(store, client, headers, research):
    item = request(client, headers)
    planning_worker.process_one(store, ForbiddenModel())
    old = read(client, item['id'])['proposals'][0]
    with write_transaction(store) as c:
        job = c.execute(select(planning_jobs).where(planning_jobs.c.intake_id == item['id'])).mappings().one()
        payload = {**job['payload']}
        payload['task_contract']['writable_paths'] = ['tests/test_share.lua']
        c.execute(planning_jobs.update().where(planning_jobs.c.id == job['id']).values(payload=payload))
    fresh = read(client, item['id'])['proposals'][0]
    assert fresh['binding'] != old['binding']
    response = client.post('/api/intake/' + item['id'] + '/decisions', headers=headers,
        json=dict(command_id=str(uuid4()), proposal_id=old['id'], binding=old['binding'],
                  scope_revision=old['scope_revision'], action='approve', note=''))
    assert response.status_code == 409


def test_crash_and_concurrent_preparation_do_not_duplicate(store, client, headers, research):
    item = request(client, headers)
    with pytest.raises(RuntimeError, match='Simulated preparation crash'):
        planning_worker.process_one(store, ForbiddenModel(), fail_before_commit=True)
    assert read(client, item['id'])['proposals'] == []
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda _: planning_worker.process_one(store, ForbiddenModel()), range(2)))
    assert len(read(client, item['id'])['proposals']) == 1
    with store.connect() as c:
        assert c.scalar(select(func.count()).select_from(planning_jobs).where(planning_jobs.c.intake_id == item['id'])) == 1


@pytest.mark.parametrize('extra', [{'run_budget': {}}, {'repository': 'other'}, {'execution_authorized': True},
    {'recipe': {}}, {'oracle_sha256': '0' * 64}, {'test_paths': ['../tests/test_profiles.lua']},
    {'test_paths': ['!!!TwichUI/Core.lua']}, {'acceptance': ['   ']}])
def test_request_cannot_supply_authority_or_arbitrary_paths(client, headers, extra):
    value = body()
    value['regression_tests'].update(extra)
    assert client.post('/api/intake', json=value, headers=headers).status_code == 422


def test_preserves_original_and_blocks_mixed_or_issue_categories(client, headers):
    value = body()
    value['title'] = 'Keep $(touch BAD) literal <script>alert(1)</script>'
    assert IntakeInput.model_validate(value).model_dump(mode='json')['title'] == value['title']
    value['kind'] = 'issue'
    assert client.post('/api/intake', json=value, headers=headers).status_code == 422
    value['kind'] = 'feature'
    value['supported_task'] = '0' * 64
    assert client.post('/api/intake', json=value, headers=headers).status_code == 422


def test_changed_scope_during_git_inspection_is_rejected(store, client, headers, research, monkeypatch):
    item = request(client, headers)
    original = preparation.prepare

    def changed(*args):
        result = original(*args)
        with write_transaction(store) as c:
            c.execute(intakes.update().where(intakes.c.id == item['id']).values(status='declined'))
        return result

    monkeypatch.setattr(preparation, 'prepare', changed)
    planning_worker.process_one(store, ForbiddenModel())
    saved = read(client, item['id'])
    assert not saved['proposals']
    assert saved['jobs'][0]['status'] == 'failed'


def test_preparation_never_claims_permission_when_planner_is_unavailable(store, client, headers, research, monkeypatch):
    monkeypatch.setenv('WORKBENCH_PLANNER', 'unavailable')
    item = request(client, headers)
    assert planning_worker.process_one(store)
    assert read(client, item['id'])['proposals'][0]['basis'] == 'preparation'


def test_preparation_scope_change_does_not_borrow_previous_contract(store, client, headers, research):
    item = request(client, headers)
    planning_worker.process_one(store, ForbiddenModel())
    p = read(client, item['id'])['proposals'][0]
    response = client.post('/api/intake/' + item['id'] + '/scope', headers=headers,
        json=dict(command_id=str(uuid4()), proposal_id=p['id'], binding=p['binding'],
                  visual_requirement='visual', stage='implementation', note='Now requires interface changes.'))
    assert response.status_code == 200, response.text
    revised = response.json()['proposals'][0]
    assert 'task_contract' not in revised
    assert revised['approval_blockers']
    assert 'fresh regression-test preparation' in ' '.join(revised['approval_blockers'])
    assert response.json()['status'] == 'queued'
    assert planning_worker.process_one(store, ForbiddenModel())
    refreshed = read(client, item['id'])['proposals'][0]
    assert refreshed['revision'] > revised['revision']
    assert refreshed['task_contract']['review_feedback'] == 'Now requires interface changes.'
    assert refreshed['human_scope']['visual_requirement'] == 'visual'
    assert refreshed['approval_blockers']  # The minimum visual safeguards still apply.


def test_git_output_cap_stops_inspection_without_running_repository_code(research):
    with pytest.raises(preparation.RepositoryUnavailable, match='bounded preparation limit'):
        preparation.git(research[0], 'ls-tree', '-z', research[1] + ':tests', max_bytes=8)
    assert not list(research[0].rglob('MUST_NOT_RUN'))


def test_clarified_path_and_complete_feedback_survive_later_change_request(store, client, headers, research):
    value = body()
    value['description'] = 'Reject invalid profile names.'
    item = request(client, headers, value)
    planning_worker.process_one(store, ForbiddenModel())
    saved = read(client, item['id'])
    response = client.post('/api/intake/' + item['id'] + '/clarification', headers=headers,
        json=dict(command_id=str(uuid4()), revision=saved['revision'], text='Use tests/test_profiles.lua.'))
    assert response.status_code == 200
    planning_worker.process_one(store, ForbiddenModel())
    p = read(client, item['id'])['proposals'][0]
    note = 'Cover whitespace-only names as well. ' + 'Preserve the original selection. ' * 25
    assert len(note) > 700
    response = client.post('/api/intake/' + item['id'] + '/decisions', headers=headers,
        json=dict(command_id=str(uuid4()), proposal_id=p['id'], binding=p['binding'],
            scope_revision=p['scope_revision'], action='changes', note=note))
    assert response.status_code == 200
    planning_worker.apply_one(store)
    planning_worker.process_one(store, ForbiddenModel())
    p = read(client, item['id'])['proposals'][0]
    assert p['task_contract']['writable_paths'] == ['tests/test_profiles.lua']
    assert note in p['task_contract']['review_feedback']
    assert 'Use tests/test_profiles.lua.' in p['task_contract']['review_feedback']
