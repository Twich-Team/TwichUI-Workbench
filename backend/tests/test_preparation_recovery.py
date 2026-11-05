"""One authorized recovery on disposable stores, with no model transport."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import select

from workbench import intake, planning_provider, real_execution, task_budget, task_session
from workbench import trusted_preparation as preparation, worker
from workbench.db import engine_for, metadata, model_attempts, proposal_decisions
from workbench.db import task_run_offers, write_transaction
from test_grounded_workflow import (
    ControlledPlanner, approve_and_decompose, configure, preparation_job, save_local_policy, submit,
)
from test_reviewed_offers import authority_counts
from test_verification_profiles import git, PRODUCTION

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def store(tmp_path):
    engine = engine_for(tmp_path / 'recovery.sqlite3')
    metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def reviewed_job(store, client, headers, tmp_path, monkeypatch):
    source, policy, output = configure(store, tmp_path, monkeypatch)

    def unexpected(*_args, **_kwargs):
        pytest.fail('Preparation recovery must never contact the model transport')

    monkeypatch.setattr(planning_provider.LocalProvider, 'generate', unexpected)
    selected = save_local_policy(store, 'pilot-qwen38-27b')

    class FixtureLocal(planning_provider.LocalProvider):
        def generate(self, packet, schema, *, expected_model):
            assert expected_model == selected.providers['local'].model
            response, _, _ = ControlledPlanner().generate(packet, schema)
            return response, expected_model, 'local'

    item = submit(client, headers)
    assert worker.tick(store, planning_adapter=FixtureLocal())
    review = approve_and_decompose(store, client, headers, item)
    return preparation_job(store), review, source, policy, output


@pytest.fixture
def failed_job(store, reviewed_job, monkeypatch):
    def hold(*_args, **_kwargs):
        raise ValueError('Fixture initial certification hold')

    with monkeypatch.context() as initial:
        initial.setattr(task_session, 'provision', hold)
        assert worker.tick(store)
    job = preparation_job(store)
    assert job['status'] == 'failed' and job['attempts'] == 1
    assert job['message'] == 'Fixture initial certification hold'
    return job, *reviewed_job[1:]


def evidence_hashes(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob('*') if path.is_file()}


def assert_original_preserved(store, original, before):
    actual = preparation_job(store)
    actual['payload'].pop('recovery')
    assert actual == original
    assert evidence_hashes(Path(original['payload']['preparation_claim']['root'])) == before


def assert_no_authority(store, *, offer_count):
    with store.connect() as c:
        decisions = c.execute(select(proposal_decisions.c.payload)).scalars().all()
        assert len(decisions) == 1
        assert not any(decisions[0].get(k) for k in ('run_budget', 'execution_continuation'))
        offers = c.execute(select(task_run_offers)).mappings().all()
        assert len(offers) == offer_count
        if offers:
            assert offers[0]['snapshot']['certified_preparation']['certificate']['status'] == 'certified'
        for table in ('fake_publications', 'pr_preparations', 'implementation_requests',
                      'implementation_attempts', 'admission_requests'):
            assert c.exec_driver_sql(f'SELECT count(*) FROM {table}').scalar_one() == 0
        (attempt,) = c.execute(select(model_attempts)).mappings().all()
        assert attempt['purpose'] == 'planning' and attempt['outcome'] == 'completed'
    assert all(count == 0 for table, count in authority_counts(store).items()
               if table != 'model_attempts')


def cli(command, store, job, *args):
    return subprocess.run(
        [sys.executable, str(ROOT / 'scripts/wb.py'), command, '--database', store.url.database,
         '--job-id', job['id'], *args],
        cwd=ROOT, capture_output=True, text=True, timeout=15,
    )


def test_supported_commands_and_guarded_worker_success(store, failed_job, tmp_path, monkeypatch):
    job, review, _, _, output = failed_job
    before = evidence_hashes(output / job['id'])
    denied = cli('run-preparation-recovery', store, job)
    assert denied.returncode == 2 and 'No recovery authorization' in denied.stderr
    response = cli('recover-preparation', store, job, '--review-binding', review['binding'])
    assert response.returncode == 0, response.stderr
    queued = json.loads(response.stdout)
    assert queued['status'] == 'queued' and queued['attempts'] == 0
    assert queued['original_job_id'] == job['id'] and queued['original_attempt'] == 1
    assert queued['authorization']['scope_approval_id'] == job['payload']['ref']['approval_id']
    assert queued['authorization']['review_binding'] == review['binding']
    assert not queued['execution_authorized']
    assert list(output.iterdir()) == [output / job['id']]
    denied = cli('recover-preparation', store, job, '--review-binding', review['binding'])
    assert denied.returncode == 2 and 'one-shot' in denied.stderr
    # General dispatch never executes a targeted recovery or unrelated work for it.
    assert not worker.tick(store)

    # Fresh worker process with the production offline entry point and actual Lua.
    # Only the confinement telemetry is controlled on this fixture.
    program = """
import sys
from workbench import trusted_preparation, task_session, worker
from test_regression_evidence import ControlledPreparer
from acceptance_transport_guard import OfflineTransportDenied
trusted_preparation.Runtime = ControlledPreparer
provision = task_session.provision
def check_guard(*args, **kwargs):
    for method in ('GET', 'POST'):
        try:
            sys.audit('urllib.Request', 'http://127.0.0.1:8080/', None, {}, method)
        except OfflineTransportDenied:
            pass
        else:
            raise AssertionError('Expected the offline guard before Runtime checks')
    return provision(*args, **kwargs)
task_session.provision = check_guard
raise SystemExit(worker.main())
"""
    monkeypatch.setenv('TMPDIR', str(tmp_path))
    result = subprocess.run(
        [sys.executable, '-c', program, '--preparation-recovery', job['id']],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
        env={**os.environ, 'WORKBENCH_DB': store.url.database,
             'WORKBENCH_LOCAL_KEY': str(tmp_path / 'unused-key'),
             'PYTHONPATH': os.pathsep.join(str(ROOT / path) for path in ('backend', 'scripts', 'backend/tests'))},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    completed = json.loads(result.stdout)
    assert completed['status'] == 'completed' and completed['attempts'] == 1
    assert completed['id'] == queued['id'] and completed['root'] == queued['root']
    assert completed['result']['status'] == 'prepared'
    assert (Path(completed['root']) / 'preparation-completed.json').is_file()
    assert len(list(output.iterdir())) == 2
    replay = cli('run-preparation-recovery', store, job)
    assert replay.returncode == 0 and json.loads(replay.stdout) == completed
    assert_original_preserved(store, job, before)
    assert_no_authority(store, offer_count=1)
    with store.connect() as c:
        assert task_budget.offer(c, queued['authorization']['proposal_id'])['id'] == completed['result']['offer_id']


def test_concurrent_requests_and_worker_claims_only_prepare_once(store, failed_job, monkeypatch):
    job, review, _, _, output = failed_job
    started, finish = Event(), Event()
    calls = []

    def hold(*_args, **_kwargs):
        calls.append(True)
        started.set()
        assert finish.wait(10)
        raise ValueError('Fixture second certification hold')

    monkeypatch.setattr(task_session, 'provision', hold)

    def request():
        try:
            return preparation.request_recovery(store, job['id'], review['binding'])
        except ValueError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        requests = list(pool.map(lambda _: request(), range(2)))
        assert sum(isinstance(r, dict) for r in requests) == 1
        assert any(isinstance(r, str) and 'one-shot' in r for r in requests)
        first = pool.submit(preparation.process_recovery, store, job['id'])
        try:
            assert started.wait(10)
            with pytest.raises(ValueError, match='claim is active'):
                preparation.process_recovery(store, job['id'])
        finally:
            finish.set()
        failed = first.result(timeout=10)
    assert failed['status'] == 'failed' and failed['attempts'] == 1
    assert failed['message'] == 'Fixture second certification hold'
    assert calls == [True] and len(list(output.iterdir())) == 2
    assert json.loads((Path(failed['root']) / 'behavioral-preparation-outcome.json').read_text())['status'] == 'hold'
    restarted = engine_for(store.url.database)
    try:
        assert preparation.process_recovery(restarted, job['id']) == failed
        with pytest.raises(ValueError, match='one-shot'):
            preparation.request_recovery(restarted, job['id'], review['binding'])
        assert not worker.tick(restarted)
    finally:
        restarted.dispose()
    ref = job['payload']['ref']
    assert task_budget.run_context(store, ref['intake_id'], ref['task_id'])['message'] == failed['message']
    assert_no_authority(store, offer_count=0)


@pytest.mark.parametrize('crash_at', ['partial-evidence', 'before-offer', 'after-offer'])
def test_restart_reconciles_only_complete_evidence(
    crash_at, store, failed_job, tmp_path, monkeypatch,
):
    job, review, _, _, output = failed_job
    queued = preparation.request_recovery(store, job['id'], review['binding'])
    original = preparation.prepare_regression
    calls = []

    class PowerLoss(BaseException):
        pass

    def interrupted(*args, **kwargs):
        calls.append(True)
        if crash_at != 'partial-evidence':
            original(*args, **kwargs)
        raise PowerLoss()

    monkeypatch.setattr(preparation, 'prepare_regression', interrupted)
    # Keep controlled workspace creation under this test's disposable directory.
    monkeypatch.setattr('tempfile.tempdir', str(tmp_path))
    with monkeypatch.context() as crash:
        if crash_at == 'before-offer':
            def power_loss(*_args, **_kwargs):
                raise PowerLoss()

            crash.setattr(task_budget, 'register_reviewed_offer', power_loss)
        with pytest.raises(PowerLoss):
            preparation.process_recovery(store, job['id'])
    claimed = preparation_job(store)['payload']['recovery']
    assert claimed['status'] == 'running' and claimed['attempts'] == 1
    # New process observes the old PID/start/boot owner as dead.
    monkeypatch.setattr(real_execution, 'alive', lambda _owner: False)
    restarted = engine_for(store.url.database)
    try:
        terminal = preparation.process_recovery(restarted, job['id'])
        assert preparation.process_recovery(restarted, job['id']) == terminal
        with pytest.raises(ValueError, match='one-shot'):
            preparation.request_recovery(restarted, job['id'], review['binding'])
    finally:
        restarted.dispose()
    assert terminal['id'] == queued['id'] and terminal['attempts'] == 1
    assert terminal['claim'] == claimed['claim']
    assert len(list(output.iterdir())) == 2 and calls == [True]
    if crash_at != 'partial-evidence':
        assert terminal['status'] == 'completed'
    else:
        assert terminal['status'] == 'failed' and 'automatic replay is denied' in terminal['message']
    assert_no_authority(store, offer_count=int(crash_at != 'partial-evidence'))


@pytest.mark.parametrize('stage', ['request', 'dispatch'])
@pytest.mark.parametrize('drift, reason', [
    ('base', 'repository/main base'),
    ('checkout', 'repository/main base'),
    ('approval', 'Request a fresh review'),
    ('expectation', 'Request a fresh review'),
    ('policy', 'Preparation policy'),
    ('model-policy', 'saved grounding model policy changed'),
    ('recipe', 'oracle identity mismatch'),
])
def test_stale_or_invalid_binding_is_held(
    stage, drift, reason, store, client, headers, failed_job,
):
    job, review, source, policy, output = failed_job
    if stage == 'dispatch':
        preparation.request_recovery(store, job['id'], review['binding'])
    if drift == 'base':
        (source / PRODUCTION).write_text('return function(s) return s end\n')
        git(source, 'add', '.')
        git(source, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@invalid',
            'commit', '-qm', 'Changed base')
    elif drift == 'checkout':
        (source / 'unexpected.txt').write_text('dirty checkout')
    elif drift == 'approval':
        response = client.post(f'/api/intake/{job["intake_id"]}/scope', headers=headers, json=dict(
            command_id=str(uuid4()), proposal_id=review['proposal_id'], binding=review['binding'],
            visual_requirement='nonvisual', stage='research', note='Supersede approval on disposable fixture.',
        ))
        assert response.status_code == 200, response.text
    elif drift == 'expectation':
        with write_transaction(store) as c:
            receipt = intake.require(c, proposal_decisions, job['payload']['ref']['approval_id'])
            payload = {k: v for k, v in receipt['payload'].items() if k != 'expectation_review'}
            c.execute(proposal_decisions.update().where(proposal_decisions.c.id == receipt['id'])
                      .values(payload=payload))
    elif drift == 'policy':
        config = json.loads(policy.read_text())
        config['scenario_generation'] = False
        policy.write_text(json.dumps(config))
    elif drift == 'model-policy':
        save_local_policy(store, 'another-offline-fixture-model')
    else:
        (output / job['id'] / 'recipe-oracle.lua').write_text('changed retained fixture oracle')
    if stage == 'request':
        with pytest.raises(ValueError, match=reason):
            preparation.request_recovery(store, job['id'], review['binding'])
        assert 'recovery' not in preparation_job(store)['payload']
    else:
        denied = preparation.process_recovery(store, job['id'])
        assert denied['status'] == 'failed' and denied['attempts'] == 0
        assert reason in denied['message']
        assert preparation.process_recovery(store, job['id']) == denied
        with pytest.raises(ValueError, match='one-shot'):
            preparation.request_recovery(store, job['id'], review['binding'])
    assert len(list(output.iterdir())) == 1
    assert_no_authority(store, offer_count=0)


def test_queued_running_and_certified_jobs_refuse_recovery(
    store, reviewed_job, tmp_path, monkeypatch,
):
    job, review, _, _, _ = reviewed_job
    with pytest.raises(ValueError, match='terminally failed'):
        preparation.request_recovery(store, job['id'], review['binding'])
    original = task_session.provision

    def during_claim(*args, **kwargs):
        with pytest.raises(ValueError, match='terminally failed'):
            preparation.request_recovery(store, job['id'], review['binding'])
        return original(*args, **kwargs)

    monkeypatch.setattr(task_session, 'provision', during_claim)
    monkeypatch.setattr('tempfile.tempdir', str(tmp_path))
    assert worker.tick(store)
    assert preparation_job(store)['status'] == 'completed'
    with pytest.raises(ValueError, match='terminally failed'):
        preparation.request_recovery(store, job['id'], review['binding'])
    assert_no_authority(store, offer_count=1)


@pytest.mark.parametrize('stage', ['request', 'dispatch'])
def test_failed_job_with_separately_certified_offer_cannot_recover(
    stage, store, failed_job, tmp_path, monkeypatch,
):
    job, review, _, _, output = failed_job
    monkeypatch.setattr('tempfile.tempdir', str(tmp_path))
    root = output / 'independent-certification'
    root.mkdir(mode=0o700)
    if stage == 'dispatch':
        preparation.request_recovery(store, job['id'], review['binding'])
    preparation.prepare_regression(
        store, job['payload']['ref'], root, job['payload']['preparation_claim']['identity'],
    )
    if stage == 'request':
        with pytest.raises(ValueError, match='already has a run offer'):
            preparation.request_recovery(store, job['id'], review['binding'])
        assert 'recovery' not in preparation_job(store)['payload']
    else:
        held = preparation.process_recovery(store, job['id'])
        assert held['status'] == 'failed' and held['attempts'] == 0
        assert 'already has a run offer' in held['message']
        assert not Path(held['root']).exists()
    assert_no_authority(store, offer_count=1)


@pytest.mark.parametrize('marker', ['preparation-completed.json', 'controls-passed.json'])
def test_original_certification_markers_hold_even_without_an_offer(store, failed_job, marker):
    job, review, _, _, output = failed_job
    (output / job['id'] / marker).touch()
    with pytest.raises(ValueError, match='certification or completion evidence'):
        preparation.request_recovery(store, job['id'], review['binding'])
    assert 'recovery' not in preparation_job(store)['payload']


def test_policy_change_during_checks_blocks_canonical_offer(store, failed_job, tmp_path, monkeypatch):
    job, review, _, _, _ = failed_job
    preparation.request_recovery(store, job['id'], review['binding'])
    original = task_session.provision

    def provision(*args, **kwargs):
        original(*args, **kwargs)
        save_local_policy(store, 'changed-after-checks')

    monkeypatch.setattr(task_session, 'provision', provision)
    monkeypatch.setattr('tempfile.tempdir', str(tmp_path))
    held = preparation.process_recovery(store, job['id'])
    assert held['status'] == 'failed' and held['attempts'] == 1
    assert 'saved grounding model policy changed' in held['message']
    assert (Path(held['root']) / 'preparation-completed.json').is_file()
    with pytest.raises(ValueError, match='durable recovery claim'):
        preparation.register(store, job['payload']['ref'], held['root'])
    assert not worker.tick(store)
    assert_no_authority(store, offer_count=0)


def test_local_command_rejects_linked_store_and_wrong_binding(store, failed_job, tmp_path):
    job, _, _, _, _ = failed_job
    link = tmp_path / 'link.sqlite3'
    link.symlink_to(store.url.database)
    result = subprocess.run(
        [sys.executable, str(ROOT / 'scripts/wb.py'), 'run-preparation-recovery',
         '--database', str(link), '--job-id', job['id']],
        cwd=ROOT, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 2 and 'owned paths without links' in result.stderr
    result = cli('recover-preparation', store, job, '--review-binding', '0' * 64)
    assert result.returncode == 2 and 'exact review binding' in result.stderr
    assert 'recovery' not in preparation_job(store)['payload']
