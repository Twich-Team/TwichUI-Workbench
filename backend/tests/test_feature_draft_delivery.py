"""Real frozen feature + installed policy reads through a clean connector environment.

Only provider/confinement/remote responses are controlled. Ancestry, completion,
publication permissions, local Git preparation and store restore are actual.
"""
import copy
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import select

from workbench import draft_delivery, everyday, feature_verification, real_publication as publication
from workbench.db import engine_for, real_execution_requests, real_publications, write_transaction
from test_everyday import installed as installed
from test_feature_check_preparation import at_independent_preparation
from test_task_to_pr import ControlledRemote


@pytest.fixture
def store(tmp_path):
    # Match the actual installed layout from the beginning; no policy or
    # historical grant needs to be fabricated when the connector starts.
    from workbench.seed import seed
    artifacts = tmp_path / 'artifacts'
    artifacts.mkdir(mode=0o700)
    database = artifacts / 'workbench.sqlite3'
    engine = engine_for(database)
    config = Config(str(Path(__file__).resolve().parents[2] / 'alembic.ini'))
    with engine.connect() as connection:
        config.attributes['connection'] = connection
        command.upgrade(config, 'head')
        connection.commit()
    database.chmod(0o600)
    seed(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def ready_feature(client, headers, store, installed, monkeypatch, tmp_path):
    from workbench import real_confine, worker
    monkeypatch.setattr(real_confine, 'root', lambda: '/sys/fs/cgroup/workbench-real-CONTROLLED-feature-delivery')
    identity, _, planner, tick = at_independent_preparation(client, headers, store, monkeypatch)
    assert tick() and tick() and tick()
    for _ in range(3):
        assert worker.tick(store, planning_adapter=planner)
    from workbench.db import planning_jobs
    with store.connect() as c:
        job = c.execute(select(planning_jobs).where(planning_jobs.c.kind == 'feature_execution')).mappings().one()
        assert job['payload']['phase'] == 'delivery_ready', job['message']
        execution = job['payload']['final_verification_execution']
        row, grant = publication._load(c, execution)
    release = tmp_path / 'release'
    release.mkdir()
    ops = tmp_path / 'ops'
    ops.mkdir(mode=0o700)
    (ops / 'inputs.json').write_text(json.dumps(dict(research_root=os.environ['WORKBENCH_RESEARCH_ROOT'])))
    (ops / 'inputs.json').chmod(0o600)
    shutil.copyfile(os.environ['WORKBENCH_PREPARATION_CONFIG'], ops / 'preparation.json')
    (ops / 'preparation.json').chmod(0o600)
    config = json.loads(Path(os.environ['WORKBENCH_EVERYDAY_CONFIG']).read_text())
    config['evidence_roots'].append(str(release))
    path = ops / 'everyday.json'
    path.write_text(json.dumps(config))
    path.chmod(0o600)
    monkeypatch.setenv('WORKBENCH_EVERYDAY_CONFIG', str(path))
    monkeypatch.setattr(everyday, 'ROOT', release)
    monkeypatch.setattr(draft_delivery, 'ROOT', release)
    database = Path(store.url.database)
    authority = database.parent / 'draft-authority.json'
    authority.write_text(json.dumps(dict(
        repository='Twich-Team/TwichUI', base_branch='main',
        database_identity=[database.stat().st_dev, database.stat().st_ino],
        owner_instruction_sha256='e' * 64, automatic_since='2099-01-01T00:00:00+00:00')))
    authority.chmod(0o600)
    return identity, row, grant, authority


def connector_step(monkeypatch, execution):
    with monkeypatch.context() as clean:
        for name in ('WORKBENCH_PREPARATION_CONFIG', 'WORKBENCH_EVERYDAY_CONFIG', 'TMPDIR', 'WORKBENCH_RESEARCH_ROOT'):
            clean.delenv(name, raising=False)
        left, right = socket.socketpair()
        try:
            result = draft_delivery.handle(right, execution)
            assert all(name not in os.environ for name in (
                'WORKBENCH_PREPARATION_CONFIG', 'WORKBENCH_EVERYDAY_CONFIG', 'TMPDIR', 'WORKBENCH_RESEARCH_ROOT'))
            return result
        finally:
            left.close()
            right.close()


def test_clean_connector_recovers_exact_preflight_hold_and_delivers_once(
    client, headers, store, ready_feature, monkeypatch, tmp_path,
):
    from workbench.feature_source import LocalCheckedCommit
    _, row, grant, _ = ready_feature
    execution = row['id']
    prefix = '/api/real-executions/' + execution
    review = client.get(prefix + '/draft-review').json()
    assert review['available'] and review['preview']['draft']
    assert (review['repository'], review['base_branch']) == ('Twich-Team/TwichUI', 'main')
    payload = dict(binding=review['binding'])
    assert client.post(prefix + '/draft-delivery', headers=headers, json=payload).status_code == 200

    # Reproduce the installed defect: old connector omitted these local policies.
    root = draft_delivery.configuration(store)['root'] / execution
    remote = ControlledRemote()
    with monkeypatch.context() as old:
        old.delenv('WORKBENCH_PREPARATION_CONFIG')
        assert publication.process_one(store, publisher=remote, execution_id=execution, checked_root=root)
    before = publication.view(store, execution)
    assert before['status'] == 'held' and 'contributions changed' in before['message']
    assert remote.prepares == remote.pushes == remote.creates == 0
    with store.connect() as c:
        held = dict(c.execute(select(real_publications)).mappings().one())
    assert held['state'] == {}
    assert client.post(prefix + '/draft-delivery', headers=headers, json=payload).status_code == 200
    assert client.post(prefix + '/draft-delivery', headers=headers, json=payload).status_code == 200

    # Real isolated Git preparation on the exact frozen patch, no credentials.
    local = LocalCheckedCommit(root, held['snapshot'], scratch_root=root)
    local.permitted = lambda: True
    commit = local.prepare(held['snapshot'], row['result'])
    assert local.prepare(held['snapshot'], row['result']) == commit
    from workbench.real_workspace import git, sha
    assert git(local.workspace, 'rev-parse', commit + '^').strip() == review['base']
    for entry in row['result']['files']:
        assert sha((local.workspace / entry['path']).read_bytes()) == row['result']['manifest']['tree'][entry['path']]
    assert set(git(local.workspace, 'diff', '--name-only', review['base'], commit).splitlines()) == set(review['files'])
    local.cleanup(commit)

    # Restart/independent restore preserves eligibility without running checks.
    restored = tmp_path / 'restored.sqlite3'
    with sqlite3.connect(store.url.database) as src, sqlite3.connect(restored) as dst:
        src.backup(dst)
    readback = engine_for(restored)
    restore_policy = tmp_path / 'restored-preparation.json'
    value = json.loads(Path(os.environ['WORKBENCH_PREPARATION_CONFIG']).read_text())
    value.update(database=str(restored), database_identity=[restored.stat().st_dev, restored.stat().st_ino])
    restore_policy.write_text(json.dumps(value))
    restore_policy.chmod(0o600)
    try:
        with monkeypatch.context() as restore:
            restore.setenv('WORKBENCH_PREPARATION_CONFIG', str(restore_policy))
            assert publication.review_checked(readback, execution) == {k:v for k,v in review.items() if k not in {'available', 'message'}}
    finally:
        readback.dispose()
    monkeypatch.setattr(feature_verification.Runtime, 'execute', lambda *_: pytest.fail('Repeated completed checks'))

    class Publisher(ControlledRemote):
        def prepare(self, snapshot, result):
            self.prepares += 1
            assert result == row['result']
            return commit
    remote = Publisher()
    remote.base = review['base']
    monkeypatch.setattr('workbench.publication_adapter.PilotGitHub', lambda *_a, **_kw: remote)
    for _ in range(8):
        connector_step(monkeypatch, execution)
    assert remote.prepares == remote.pushes == remote.creates == 1
    assert client.post(prefix + '/draft-delivery', headers=headers, json=payload).status_code == 200
    assert not connector_step(monkeypatch, execution)
    with store.connect() as c:
        saved = c.execute(select(real_publications)).mappings().one()
        assert saved['status'] == 'published'
        assert saved['snapshot'] == held['snapshot'] and saved['binding'] == held['binding']
        assert saved['state']['preflight_recovery']['previous_outcome'] == dict(
            status=held['status'], message=held['message'], finished_at=held['finished_at'], state={})


@pytest.mark.parametrize('fault', ['contribution', 'stale-hash', 'missing-proof', 'bundle', 'destination', 'permission', 'remote-intent', 'missing-policy', 'wrong-policy-store'])
def test_changed_or_missing_evidence_permission_and_remote_intent_hold(
    client, headers, store, ready_feature, monkeypatch, fault,
):
    _, row, grant, authority = ready_feature
    execution = row['id']
    review = publication.review_checked(store, execution)
    draft_delivery.request(store, execution, review['binding'])
    if fault == 'contribution':
        child_id = row['result']['contributions'][0]['execution_id']
        with store.connect() as c:
            child, child_grant = publication._load(c, child_id)
        target = Path(child_grant['snapshot']['recovery']['root']) / 'candidates' / child['result']['candidate_id'] / 'files' / child['result']['files'][0]['path']
        target.write_bytes(target.read_bytes() + b'\n-- changed after checking\n')
    elif fault == 'stale-hash':
        changed = copy.deepcopy(row['result'])
        changed['contributions'][0]['result_binding'] = '0' * 64
        with write_transaction(store) as c:
            c.execute(real_execution_requests.update().where(real_execution_requests.c.id == execution).values(result=changed))
    elif fault == 'missing-proof':
        (Path(grant['snapshot']['recovery']['root']) / ('completion-' + execution + '.json')).unlink()
    elif fault == 'bundle':
        (Path(grant['snapshot']['recovery']['root']) / 'feature-draft-bundle/feature-draft.json').unlink()
    elif fault in {'destination', 'permission'}:
        if fault == 'permission':
            authority.unlink()
        else:
            value = json.loads(authority.read_text())
            authority.write_text(json.dumps({**value, 'repository': 'other/repository'}))
    elif fault in {'missing-policy', 'wrong-policy-store'}:
        config = authority.parent.parent / 'ops/preparation.json'
        if fault == 'missing-policy':
            config.unlink()
        else:
            value = json.loads(config.read_text())
            value['database_identity'] = [0, 0]
            config.write_text(json.dumps(value))
    else:
        # An uncertain remote write must never be reset by a repeated submission.
        with write_transaction(store) as c:
            c.execute(real_publications.update().values(status='held', state={'push_started': True},
                message='The combined feature result or its independently checked contributions changed.'))
        draft_delivery.request(store, execution, review['binding'])
    remote = ControlledRemote()
    monkeypatch.setattr('workbench.publication_adapter.PilotGitHub', lambda *_a, **_kw: remote)
    connector_step(monkeypatch, execution)
    assert remote.prepares == remote.pushes == remote.creates == 0
    with store.connect() as c:
        saved = c.execute(select(real_publications)).mappings().one()
        assert saved['status'] != 'published'
        if fault == 'remote-intent':
            assert saved['status'] == 'held' and saved['state'] == {'push_started': True}
    if fault not in {'destination', 'permission', 'remote-intent', 'missing-policy', 'wrong-policy-store'}:
        with pytest.raises(ValueError):
            draft_delivery.request(store, execution, review['binding'])


def test_separate_current_main_checks_preserve_original_result_and_exact_branch(
    store, ready_feature, monkeypatch, tmp_path,
):
    import subprocess
    from workbench import feature_delivery
    from workbench.publication_adapter import PilotGitHub
    from workbench.real_workspace import git
    _, row, grant, _ = ready_feature
    review = publication.review_checked(store, row['id'])
    draft_delivery.request(store, row['id'], review['binding'])
    root = draft_delivery.configuration(store)['root'] / row['id']
    with store.connect() as c:
        before = dict(c.execute(select(real_publications)).mappings().one())
    current = tmp_path / 'new-main'
    subprocess.run(['git', 'clone', '--local', '--shared', '--quiet',
                    grant['snapshot']['contract']['source'], str(current)], check=True)
    current.chmod(0o700)
    (current / 'unrelated.txt').write_text('A separate newer main change.\n')
    git(current, 'add', 'unrelated.txt')
    git(current, '-c', 'user.name=CONTROLLED', '-c', 'user.email=control@invalid', 'commit', '-qm', 'Newer main')
    target = git(current, 'rev-parse', 'HEAD').strip()
    assert target != review['base']
    assert feature_delivery.compatible_base(before['snapshot'], root) == review['base']
    assert feature_delivery.check_base_compatibility(store, row['id'], root, current, target, 'a' * 64) == target
    # Reuse the sealed compatibility result; never run either suite again.
    monkeypatch.setattr(feature_verification.Runtime, '_check', lambda *_a, **_kw: pytest.fail('Repeated compatibility checks'))
    assert feature_delivery.check_base_compatibility(store, row['id'], root, current, target, 'a' * 64) == target
    with store.connect() as c:
        after = dict(c.execute(select(real_publications)).mappings().one())
        original, _ = publication._load(c, row['id'])
    assert after == before and original == row
    adapter = PilotGitHub(root, before['snapshot'], scratch_root=root)
    assert adapter.target_base(before['snapshot']) == target
    value = dict(isDraft=True, isCrossRepository=False, state='OPEN', baseRefName='main',
                 baseRefOid=target, headRefName=review['branch'], headRefOid='c' * 40,
                 body=review['preview']['body'], url='https://github.com/Twich-Team/TwichUI/pull/123')
    assert adapter.valid_pr(before['snapshot'], 'c' * 40, value)
    assert not adapter.valid_pr(before['snapshot'], 'c' * 40, {**value, 'baseRefOid': 'd' * 40})
    check_path = root / 'base-compatibility' / target / 'checks.json'
    raw = check_path.read_bytes()
    altered = json.loads(raw)
    altered['candidate'][-1]['rc'] = 1
    check_path.write_text(json.dumps(altered))
    with pytest.raises(ValueError):
        adapter.target_base(before['snapshot'])
    check_path.write_bytes(raw)
    candidate = root / 'base-compatibility' / target / 'workbench-verify-compatibility/workspace' / row['result']['files'][0]['path']
    candidate.write_bytes(candidate.read_bytes() + b'\n-- post-check change\n')
    with pytest.raises(ValueError):
        adapter.target_base(before['snapshot'])


def test_archived_feature_delivery_cannot_stop_other_worker_work(
    store, ready_feature, monkeypatch,
):
    from uuid import uuid4
    from fastapi import HTTPException
    from workbench import feature_delivery, task_cleanup, worker, planning_worker
    from workbench.db import planning_jobs, task_sessions, model_attempts

    identity, _, _, _ = ready_feature
    context = task_cleanup.context(store, identity)
    assert context['can_archive']
    task_cleanup.apply(store, task_cleanup.CleanupCommand.model_validate(dict(
        command_id=str(uuid4()), action='archive',
        targets=[dict(intake_id=identity, binding=context['binding'])])))
    tables = (planning_jobs, task_sessions, model_attempts, real_publications,
              real_execution_requests)
    def saved():
        with store.connect() as c:
            return {t.name: [dict(r) for r in c.execute(select(t)).mappings()] for t in tables}
    with store.connect() as c:
        job = c.execute(select(planning_jobs).where(
            planning_jobs.c.kind == 'feature_execution')).mappings().one()
        assert feature_delivery.view_in(c, job)['published'] is False
        saved_delivery = job['payload']['feature_delivery']
        row, grant = publication._load(c, saved_delivery['execution_id'])
        with pytest.raises(HTTPException) as refusal:
            feature_delivery.policy(c, row, grant)
        assert refusal.value.status_code == 409
    # A malformed historical descriptor must not even reach active validation.
    with write_transaction(store) as c:
        c.execute(planning_jobs.update().where(planning_jobs.c.id == job['id']).values(
            payload={**job['payload'], 'feature_delivery': {'execution_id': 'missing-history'}}))
    before = saved()
    reached = []
    monkeypatch.setattr(planning_worker, 'process_one',
                        lambda *_a, **_kw: reached.append('other planning') or True)
    monkeypatch.setattr(feature_verification.Runtime, 'execute',
                        lambda *_a, **_kw: pytest.fail('Repeated archived feature checks'))
    assert worker.tick(store)
    assert worker.tick(store)
    assert reached == ['other planning', 'other planning']
    assert saved() == before
    assert task_cleanup.context(store, identity)['archived_at']
