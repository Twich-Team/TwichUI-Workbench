"""Install/restart boundaries over a genuinely checked, durably queued result.

The native provider/check fixture is controlled; installer subprocesses are real.
The separate real-store rehearsal uses these same entry points without seeding it.
"""
import copy
import json
from pathlib import Path
import sqlite3

import pytest
from sqlalchemy import select

from test_installation_preflight import make_installation, invoke, save
from test_native_agent import source as source, spec as spec, native_checked as native_checked
from test_task_to_pr import checked_draft as checked_draft, supported as supported
from workbench import draft_delivery, real_publication, work_controls
from workbench.db import engine_for, real_publications, real_execution_grants
from everyday_store import copy_store, fingerprint


@pytest.fixture
def paused_delivery(store, native_checked, tmp_path, monkeypatch):
    _, registered, _ = native_checked
    policy = dict(root=tmp_path / 'delivery', digest='d' * 64,
        owner_instruction_sha256='e' * 64, automatic_since='2000-01-01T00:00:00+00:00')
    monkeypatch.setattr(draft_delivery, 'configuration', lambda *args: policy)
    assert draft_delivery.queue_automatic(store)
    with store.connect() as c:
        row = dict(c.execute(select(real_publications)).mappings().one())
    work_controls.task_save(store, row['task_id'], work_controls.WorkControlInput(
        revision=work_controls.read(store).revision, paused=True))
    # Make actual copy_store ownership/privacy requirements part of the test.
    Path(store.url.database).chmod(0o600)
    return row, policy, registered['id']


def test_paused_delivery_survives_real_install_repeat_and_rollback(store, paused_delivery, tmp_path, monkeypatch):
    row, _, identity = paused_delivery
    envelope, value, root = make_installation(tmp_path, store.url.database)
    before = Path(value['store']).read_bytes()
    assert invoke(envelope, 'preflight').returncode == 0
    assert invoke(envelope).returncode == 0
    assert invoke(envelope).returncode != 0
    assert invoke(envelope, 'rollback').returncode == 0
    assert Path(value['store']).read_bytes() == before
    # Reopening the worker's engine retains pause and never reaches connector.
    def forbidden(*args):
        pytest.fail('Installation/restart dispatched dormant publication')
    monkeypatch.setattr(draft_delivery, 'send', forbidden)
    reopened = engine_for(Path(value['store']))
    try:
        assert work_controls.task_read(reopened, row['task_id']).paused
        assert not draft_delivery.advance(reopened)
        with reopened.connect() as c:
            assert dict(c.execute(select(real_publications)).mappings().one()) == row
    finally:
        reopened.dispose()
    # The exact saved candidate is eligible only after separate supported resume.
    work_controls.task_save(store, row['task_id'], work_controls.WorkControlInput(
        revision=work_controls.read(store).revision, paused=False))
    assert real_publication.review_checked(store, identity)['candidate_id'] == row['snapshot']['candidate_id']


@pytest.mark.parametrize('status,state', [
    ('pending', {'owner': {'pid': 1}}), ('pending', {'dispatch': 'unknown'}),
    ('pending', {'commit': 'a' * 40}), ('pending', {'push_started': True}),
    ('preparing', {}), ('pushing', {}), ('creating', {}), ('reconciling', {}),
    ('held', {'uncertain': 'push'}), ('held', {'pr_started': True}),
])
def test_active_or_uncertain_delivery_refuses_real_install_and_copy(store, paused_delivery, tmp_path, status, state):
    with store.begin() as c:
        c.execute(real_publications.update().values(status=status, state=state))
    envelope, _, root = make_installation(tmp_path, store.url.database)
    assert invoke(envelope).returncode != 0
    assert not (root / 'events').exists()
    with pytest.raises(ValueError):
        copy_store(Path(store.url.database), root / 'refused.sqlite3')
    assert not (root / 'refused.sqlite3').exists()


def test_eligible_pending_is_preserved_but_not_installable_without_pause(store, paused_delivery, tmp_path, monkeypatch):
    row, _, identity = paused_delivery
    work_controls.task_save(store, row['task_id'], work_controls.WorkControlInput(
        revision=work_controls.read(store).revision, paused=False))
    envelope, _, root = make_installation(tmp_path, store.url.database)
    assert invoke(envelope).returncode != 0
    assert not (root / 'events').exists()
    seen = []
    monkeypatch.setattr(draft_delivery, 'send', lambda selected, stop: seen.append(selected) or True)
    # A fresh worker selects the same eligible canonical delivery exactly once.
    reopened = engine_for(Path(store.url.database))
    try:
        assert draft_delivery.advance(reopened)
        assert seen == [identity]
    finally:
        reopened.dispose()


def test_revocation_survives_copy_and_resume_cannot_restore_it(store, paused_delivery, tmp_path):
    row, _, identity = paused_delivery
    with store.begin() as c:
        c.execute(real_execution_grants.update().values(revoked_at='2026-11-14T00:00:00+00:00'))
    destination = tmp_path / 'copied.sqlite3'
    copy_store(Path(store.url.database), destination)
    reopened = engine_for(destination)
    try:
        work_controls.task_save(reopened, row['task_id'], work_controls.WorkControlInput(
            revision=work_controls.read(reopened).revision, paused=False))
        with pytest.raises(ValueError):
            real_publication.review_checked(reopened, identity)
        with reopened.connect() as c:
            assert c.scalar(select(real_execution_grants.c.revoked_at)) == '2026-11-14T00:00:00+00:00'
    finally:
        reopened.dispose()


def test_interrupted_install_keeps_dormant_delivery_and_one_journal(store, paused_delivery, tmp_path):
    envelope, value, root = make_installation(tmp_path, store.url.database)
    script = Path(value['activation_script'])
    script.write_text('kill -KILL "$PPID"\n')
    from hashlib import sha256
    for item in value['files']:
        if item['path'] == str(script):
            item['sha256'] = sha256(script.read_bytes()).hexdigest()
    save(envelope, value)
    assert invoke(envelope).returncode < 0
    assert invoke(envelope).returncode != 0
    assert invoke(envelope, 'rollback').returncode == 0
    with sqlite3.connect(value['store']) as c:
        assert fingerprint(c) == value['history']
    assert json.loads((root / 'installation-journal.json').read_text())['state'] == 'rolled_back'


@pytest.mark.parametrize('damage', ['publication_binding', 'grant_binding', 'candidate', 'missing_pause'])
def test_pause_alone_does_not_qualify_corrupt_publication(store, paused_delivery, tmp_path, damage):
    row, _, _ = paused_delivery
    with store.begin() as c:
        if damage == 'grant_binding':
            c.execute(real_execution_grants.update().values(binding='a' * 64))
        elif damage == 'publication_binding':
            c.execute(real_publications.update().values(binding='a' * 64))
        elif damage == 'candidate':
            snapshot = copy.deepcopy(row['snapshot'])
            snapshot['candidate_id'] = 'a' * 64
            c.execute(real_publications.update().values(snapshot=snapshot))
    if damage == 'missing_pause':
        work_controls.task_save(store, row['task_id'], work_controls.WorkControlInput(
            revision=work_controls.read(store).revision, paused=False))
    envelope, _, root = make_installation(tmp_path, store.url.database)
    assert invoke(envelope).returncode != 0
    assert not (root / 'events').exists()


def test_legacy_checked_delivery_keeps_its_original_owner(store, checked_draft, tmp_path):
    original, approval_root, _, _ = checked_draft
    assert real_publication.authorize_checked(store, original['id'], approval_root)
    with store.connect() as c:
        _, grant = real_publication._load(c, original['id'])
    work_controls.task_save(store, grant['snapshot']['workflow_task']['intake_id'], work_controls.WorkControlInput(
        revision=work_controls.read(store).revision, paused=True))
    envelope, value, _ = make_installation(tmp_path, store.url.database)
    assert invoke(envelope).returncode == 0
    with sqlite3.connect(value['store']) as c:
        assert fingerprint(c) == value['history']
