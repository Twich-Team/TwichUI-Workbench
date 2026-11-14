"""Historical source/evidence seals and the real native publication boundary.

Providers/check execution are controlled by native_checked; no addon model runs.
"""
import copy
import json
from pathlib import Path
import shutil

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from test_native_agent import source as source, spec as spec, native_checked as native_checked
from test_task_to_pr import ControlledRemote, delivery_clock as delivery_clock
from workbench import checked_handoff as handoff, native_agent as native
from workbench import real_publication as publication, draft_delivery, work_controls
from workbench.db import real_execution_grants, real_execution_requests, real_publications, intakes


@pytest.fixture
def boundary(store, native_checked, tmp_path, monkeypatch):
    runtime, registered, view = native_checked
    historical = tmp_path / 'historical'
    helpers = historical / 'backend/workbench'
    shutil.copytree(Path(native.__file__).parent, helpers, ignore=shutil.ignore_patterns('__pycache__'))
    scripts = historical / 'scripts'
    scripts.mkdir()
    project = Path(native.__file__).resolve().parents[2]
    for name in ('native_connector.py', 'codex_connector.py', 'strategy_connector.py'):
        shutil.copyfile(project / 'scripts' / name, scripts / name)
    with store.connect() as c:
        row, grant = publication._load(c, registered['id'])
    current = tmp_path / 'current'
    shutil.copytree(historical, current)
    with (current / 'backend/workbench/draft_delivery.py').open('a') as f:
        f.write('\n# Synthetic publisher-only correction\n')
    monkeypatch.setattr(native, '__file__', str(current / 'backend/workbench/native_agent.py'))
    monkeypatch.setattr(handoff, 'ROOT', current)
    monkeypatch.setattr(draft_delivery, 'ROOT', current)
    assert runtime.identity() != grant['snapshot']['runtime']
    assert runtime.verify_frozen(row['result'])
    assert not runtime.recover_completion(row, grant)
    return runtime, row, grant, historical


def qualify(store, boundary, monkeypatch):
    runtime, row, grant, historical = boundary
    # Keep structural/source/evidence checks real on hosts lacking namespaces.
    monkeypatch.setattr(handoff, 'historical_completion', lambda release, r, g:
        dict(result=r['result'], completed_at=r['finished_at']))
    return handoff.qualify(store, row['id'], historical, 'Synthetic explicit linked delivery authority')


def test_original_runtime_readonly_qualification(store, boundary):
    if not Path('/usr/bin/bwrap').exists():
        pytest.skip('Read-only namespace integration requires host bubblewrap')
    runtime, row, grant, historical = boundary
    receipt = handoff.qualify(store, row['id'], historical, 'Synthetic explicit owner')
    assert receipt['binding']['runtime'] == grant['snapshot']['runtime']
    assert publication._proof(row, grant) == row['result']
    assert not runtime.verify_checks(row['result'], grant)


@pytest.mark.parametrize('damage', ['verifier', 'publisher-history', 'check', 'candidate',
    'missing-check', 'missing-completion', 'provider', 'receipt', 'symlink'])
def test_damaged_history_is_refused(store, boundary, monkeypatch, damage):
    receipt = qualify(store, boundary, monkeypatch)
    runtime, row, grant, historical = boundary
    assert publication._proof(row, grant) == row['result']
    paths = dict(verifier=historical / 'backend/workbench/native_gallery_verifier.py',
        **{'publisher-history': historical / 'backend/workbench/draft_delivery.py'},
        check=runtime.root / 'native-check-1.json',
        candidate=runtime.root / 'candidates' / row['result']['candidate_id'] / 'files' / native.FOCUSED,
        **{'missing-check': runtime.root / 'native-check-0.json',
           'missing-completion': runtime.root / ('completion-' + row['id'] + '.json')},
        provider=runtime.root / 'native-provider-result.json',
        receipt=handoff.receipt_path(row['id']), symlink=runtime.root / 'native-check-2.json')
    path = paths[damage]
    if damage.startswith('missing'):
        path.unlink()
    elif damage == 'receipt':
        receipt['binding']['candidate_id'] = '0' * 64
        path.write_text(json.dumps(receipt))
    elif damage == 'symlink':
        data = path.read_bytes()
        path.unlink()
        target = runtime.root / 'unexpected-copy'
        target.write_bytes(data)
        path.symlink_to(target)
    else:
        path.write_bytes(path.read_bytes() + b'corrupt')
    with pytest.raises((ValueError, OSError)):
        publication._proof(row, grant)


@pytest.mark.parametrize('status', ['failed', 'blocked', 'pending'])
def test_unchecked_native_result_cannot_qualify(store, boundary, monkeypatch, status):
    _, row, _, _ = boundary
    with store.begin() as c:
        c.execute(real_execution_requests.update().where(real_execution_requests.c.id == row['id']).values(status=status))
    with pytest.raises(ValueError, match='native authorship'):
        qualify(store, boundary, monkeypatch)


@pytest.mark.parametrize('lost', ['push', 'pr'])
def test_native_selected_once_and_durably_reconciled(store, boundary, monkeypatch, lost, delivery_clock):
    qualify(store, boundary, monkeypatch)
    runtime, row, grant, historical = boundary
    policy = dict(root=handoff.ROOT.parent / 'artifacts/draft-delivery', digest='d' * 64,
        owner_instruction_sha256='e' * 64, automatic_since='2000-01-01T00:00:00+00:00')
    monkeypatch.setattr(draft_delivery, 'configuration', lambda *args: policy)
    assert draft_delivery.queue_automatic(store)
    assert not draft_delivery.queue_automatic(store)
    remote = ControlledRemote()
    remote.base = grant['snapshot']['contract']['base']
    remote.lose = lost
    monkeypatch.setattr('workbench.publication_adapter.PilotGitHub', lambda *a, **kw: remote)
    root = policy['root'] / row['id']
    seen = []

    def send(identity, stop):
        seen.append(identity)
        return publication.process_one(store, publisher=remote, execution_id=identity, checked_root=root)

    monkeypatch.setattr(draft_delivery, 'send', send)
    # With an excluded task in the policy, NULL legacy identity must not hide native.
    with store.begin() as c:
        c.execute(intakes.insert().values(id='11111111-1111-4111-8111-111111111111',
            payload={}, status='tasks_ready', revision=1, created_at=row['finished_at']))
    work_controls.task_save(store, '11111111-1111-4111-8111-111111111111',
        work_controls.WorkControlInput(revision=work_controls.read(store).revision, paused=True))
    for _ in range(12):
        draft_delivery.advance(store)
    with store.connect() as c:
        rows = c.execute(select(real_publications)).mappings().all()
    assert len(rows) == 1 and rows[0]['status'] == 'published'
    assert remote.prepares == remote.pushes == remote.creates == 1
    assert set(seen) == {row['id']}


@pytest.mark.parametrize('change', ['pause', 'revoke', 'destination', 'withdraw'])
def test_current_authority_still_required(store, boundary, monkeypatch, tmp_path, change):
    qualify(store, boundary, monkeypatch)
    _, row, grant, _ = boundary
    if change == 'pause':
        work_controls.task_save(store, grant['snapshot']['native_task']['task_id'],
            work_controls.WorkControlInput(revision=work_controls.read(store).revision, paused=True))
    elif change == 'withdraw':
        handoff.receipt_path(row['id']).unlink()
    else:
        with store.begin() as c:
            if change == 'revoke':
                c.execute(real_execution_grants.update().where(real_execution_grants.c.id == grant['id']).values(revoked_at=row['finished_at']))
            else:
                snapshot = copy.deepcopy(grant['snapshot'])
                snapshot['native_task']['contract']['repository'] = 'wrong/destination'
                c.execute(real_execution_grants.update().where(real_execution_grants.c.id == grant['id']).values(snapshot=snapshot))
    with pytest.raises((ValueError, OSError, HTTPException)):
        publication.review_checked(store, row['id'])


def test_moved_main_refuses_delivery_without_fresh_compatibility(store, boundary, monkeypatch):
    qualify(store, boundary, monkeypatch)
    _, row, _, _ = boundary
    policy = dict(root=handoff.ROOT.parent / 'artifacts/draft-delivery', digest='d' * 64,
        owner_instruction_sha256='e' * 64, automatic_since='2000-01-01T00:00:00+00:00')
    monkeypatch.setattr(draft_delivery, 'configuration', lambda *a: policy)
    assert draft_delivery.queue_automatic(store)
    remote = ControlledRemote()
    remote.base = 'f' * 40
    assert publication.process_one(store, publisher=remote, execution_id=row['id'],
        checked_root=policy['root'] / row['id'])
    with store.connect() as c:
        saved = c.execute(select(real_publications)).mappings().one()
    assert saved['status'] == 'held' and 'base moved' in saved['message']
    assert remote.prepares == remote.pushes == remote.creates == 0


@pytest.mark.parametrize('started', [False, True])
def test_linked_approval_renewal_retains_old_hold_and_refuses_remote_intent(store, boundary, monkeypatch, started):
    qualify(store, boundary, monkeypatch)
    _, row, _, _ = boundary
    policy = dict(root=handoff.ROOT.parent / 'artifacts/draft-delivery', digest='d' * 64,
        owner_instruction_sha256='e' * 64, automatic_since='2000-01-01T00:00:00+00:00')
    monkeypatch.setattr(draft_delivery, 'configuration', lambda *a: policy)
    assert draft_delivery.queue_automatic(store)
    with store.connect() as c:
        before = dict(c.execute(select(real_publications)).mappings().one())
    if started:
        with store.begin() as c:
            c.execute(real_publications.update().values(state={'push_started': True}))
    root = policy['root'] / row['id']
    approval = json.loads((root / 'checked-approval.json').read_text())
    # Separate explicit approval under the new owner direction; no standing
    # policy inference and no edit to the original grant/verification result.
    approval.pop('standing_policy_sha256')
    approval['owner_instruction_sha256'] = 'a' * 64
    (root / 'checked-approval.json').write_text(json.dumps(approval))
    if started:
        with pytest.raises(ValueError, match='different delivery'):
            publication.authorize_checked(store, row['id'], root)
    else:
        assert publication.authorize_checked(store, row['id'], root)
        with store.connect() as c:
            after = c.execute(select(real_publications)).mappings().one()
        assert after['id'] == before['id'] and after['status'] == 'pending'
        recovery = after['state']['checked_approval_recovery']
        assert recovery['previous_snapshot'] == before['snapshot']
        assert recovery['previous_binding'] == before['binding']
        assert recovery['previous_state'] == {} and recovery['previous_status'] == 'pending'
