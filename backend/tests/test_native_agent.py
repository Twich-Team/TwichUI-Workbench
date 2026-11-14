"""Native boundary and existing-owner controls. All providers here are simulated."""
import copy
import hashlib
import json
import os
import subprocess
from uuid import uuid4

import pytest
from sqlalchemy import select
from workbench import native_agent as native, native_boundary as boundary, native_codex
from workbench import real_execution as execution
from workbench.db import intakes, model_attempts, write_transaction


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), '-c', 'core.hooksPath=/dev/null', *args]).decode().strip()


@pytest.fixture
def source(tmp_path):
    root = tmp_path / 'source'
    root.mkdir()
    (root / 'tests').mkdir()
    (root / native.FOCUSED).write_text('assert False, "synthetic original"\n')
    (root / 'private.txt').write_text('Do not disclose this synthetic source\n')
    git(root, 'init', '-q')
    git(root, 'add', '.')
    git(root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@local.invalid', 'commit', '-qm', 'Synthetic original')
    return root


@pytest.fixture
def spec(source):
    raw = (source / native.FOCUSED).read_bytes()
    return dict(task_id=str(uuid4()), title='Synthetic native control', instruction='Repair the synthetic test.',
        owner_instruction_sha256=hashlib.sha256(b'EXPLICIT TEST OWNER').hexdigest(),
        verification_profile='native-gallery-v1', base_branch='main', draft=True,
        identity=dict(name='Fixture', email='fixture@local.invalid'),
        disclosure={native.FOCUSED: dict(oid=git(source, 'rev-parse', 'HEAD:' + native.FOCUSED),
            sha256=hashlib.sha256(raw).hexdigest(), mode='100644')},
        contract=dict(engine_version=native.ENGINE, repository='Twich-Team/TwichUI', provider='codex',
            model='gpt-6.1-sol', source=str(source), base=git(source, 'rev-parse', 'HEAD'),
            title='Synthetic native control', paths=[native.FOCUSED], operations={native.FOCUSED: ['modify']}, limits=dict(implementation_s=900,
                acceptance_s=1200, memory_bytes=1 << 30, pids=128, cpu_percent=100,
                output_bytes=4 << 20, changed_lines=150, patch_bytes=65536)),
        strategy=dict(stages=[dict(id='native-codex', provider='codex', model='gpt-6.1-sol', effort='high', allocation=100)]))


def test_only_exact_disclosure_blobs_enter_independent_workspace(tmp_path, source, spec):
    target = tmp_path / 'export'
    native.materialize(source, spec['contract']['base'], target, spec['disclosure'])
    assert (target / native.FOCUSED).read_bytes() == (source / native.FOCUSED).read_bytes()
    assert not (target / 'private.txt').exists()
    assert not (target / '.git').exists()
    bad = copy.deepcopy(spec['disclosure'])
    bad[native.FOCUSED]['sha256'] = '0' * 64
    with pytest.raises(ValueError, match='identity'):
        native.materialize(source, spec['contract']['base'], tmp_path / 'bad', bad)


@pytest.mark.parametrize('name', ['../escape', '/absolute', 'tests/../escape', '.codex/config.toml',
    '.git/config', 'AGENTS.md', 'tests/SKILL.md', 'a\\b', 'tests//bad'])
def test_source_control_and_escape_paths_are_not_disclosable(name):
    assert not native.safe_path(name)


@pytest.mark.parametrize('kind', ['modify', 'add', 'ignored', 'delete', 'empty-directory', 'symlink',
    'hardlink', 'fifo', 'privileged', 'executable', 'directory-replacement', 'git-config', 'git-alternates',
    'oversize', 'total-bytes', 'too-many', 'depth'])
def test_strict_export_rejects_entire_candidate_before_creation(tmp_path, kind):
    root = tmp_path / 'workspace'
    root.mkdir()
    allowed = root / 'allowed.py'
    allowed.write_text('original\n')
    other = root / 'other.py'
    other.write_text('original\n')
    (root / '.git').mkdir()
    (root / '.git/config').write_text('original git config\n')
    baseline, _ = boundary.scan(root)
    allowed.write_text('authorized repair\n')
    if kind == 'modify':
        other.write_text('forbidden\n')
    elif kind in {'add', 'ignored'}:
        (root / ('extra.py' if kind == 'add' else '.ignored')).write_text('forbidden\n')
    elif kind == 'delete':
        other.unlink()
    elif kind == 'empty-directory':
        (root / 'extra').mkdir()
    elif kind in {'symlink', 'hardlink'}:
        (root / 'link').symlink_to(other) if kind == 'symlink' else os.link(other, root / 'link')
    elif kind == 'fifo':
        os.mkfifo(root / 'fifo')
    elif kind == 'privileged':
        allowed.chmod(0o4644)
    elif kind == 'executable':
        allowed.chmod(0o755)
    elif kind == 'directory-replacement':
        allowed.unlink()
        allowed.mkdir()
    elif kind == 'git-config':
        (root / '.git/config').write_text('changed\n')
    elif kind == 'git-alternates':
        (root / '.git/objects/info').mkdir(parents=True)
        (root / '.git/objects/info/alternates').write_text('/host/objects')
    elif kind == 'oversize':
        allowed.write_bytes(b'x' * ((4 << 20) + 1))
    elif kind == 'total-bytes':
        for index in range(9):
            (root / str(index)).write_bytes(b'x' * (4 << 20))
    elif kind == 'too-many':
        for index in range(4097):
            (root / str(index)).touch()
    else:
        (root / '/'.join(['deep'] * 20)).mkdir(parents=True)
    destination = tmp_path / 'candidate'
    with pytest.raises((ValueError, OSError)):
        boundary.export(root, baseline, ['allowed.py'], destination)
    assert not destination.exists()


def test_exact_export_is_frozen_and_excludes_control_tree(tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    (root / 'allowed.py').write_text('original\n')
    (root / '.git').mkdir()
    (root / '.git/config').write_text('safe local config')
    baseline, _ = boundary.scan(root)
    (root / 'allowed.py').write_text('fixed\n')
    candidate = tmp_path / 'candidate'
    result = boundary.export(root, baseline, ['allowed.py'], candidate)
    assert result['changes'] == ['allowed.py']
    assert (candidate / 'allowed.py').read_text() == 'fixed\n'
    assert not (candidate / '.git').exists()
    assert (candidate / 'allowed.py').stat().st_mode & 0o222 == 0
    candidate.chmod(0o700)


def test_native_usage_never_calls_one_session_one_model_request():
    result = native_codex.tokens([dict(type='turn.completed', usage=dict(input_tokens=10,
        output_tokens=5, reasoning_output_tokens=3, cached_input_tokens=8))])
    assert result['values']['total_tokens'] == 15
    assert result['values']['reasoning_tokens'] == 3
    assert result['requests_observed'] is None and result['model_requests_observed'] is None
    assert native_codex.tokens([])['values'] is None
    assert native_codex.tokens([dict(type='turn.completed'), dict(type='turn.completed')])['status'] == 'unavailable'


def test_native_contract_does_not_mutate_legacy_defaults(spec):
    original = execution.contract()
    snapshot = dict(contract=spec['contract'], native_task=spec,
        authorization_sha256=spec['owner_instruction_sha256'])
    assert execution.contract_matches(snapshot)
    assert not native.native(dict(contract=original))
    assert execution.contract() == original
    snapshot['native_task'] = copy.deepcopy(spec)
    snapshot['native_task']['contract']['paths'] = ['production.lua']
    assert not execution.contract_matches(snapshot)


def test_candidate_cannot_mutate_the_independent_parent_checker(tmp_path):
    from workbench import native_gallery_verifier as verifier
    root = tmp_path / 'subject'
    (root / 'tests').mkdir(parents=True)
    output = tmp_path / 'outputs'
    output.mkdir()
    candidate = root / native.FOCUSED
    candidate.write_text('''import sys, unittest
class Rebuild(unittest.TestCase):
 def test_manifest_and_artdata_are_what_the_tool_builds(self): pass
 def test_sample_pictures_are_what_the_tool_builds(self):
  sys.modules['__main__'].REJECTED = 0
  raise AssertionError('Synthetic attempt to corrupt the checker')
''')
    with pytest.raises(AssertionError, match='wrong result'):
        verifier.check_subject('rebuild', root, candidate, output, accepts=False)
    assert verifier.REJECTED == 23


def test_existing_worker_claim_and_model_ledger_are_reused_once(store, spec, tmp_path, monkeypatch):
    class Controlled(native.Runtime):
        def __init__(self):
            self.execution_contract = spec['contract']
            self.native_authorization = spec
            self.root = tmp_path
            self.grant_deadline = None
            self.calls = 0

        def identity(self):
            return dict(model='gpt-6.1-sol')

        def preflight(self):
            return [dict(name='SIMULATED native provider', status='passed')]

        def admission_gate(self, pending, grant):
            return dict(name='SIMULATED native allowance admission', status='passed')

        def execute(self, identity, fence, permitted):
            assert permitted()
            self.calls += 1
            return dict(verification='failed', usage=native_codex.tokens([]), inference_ran=False)

        def verify_frozen(self, result):
            return True

        def cleanup(self, row):
            return True

        def record_outcome(self, *args):
            pass

    runtime = Controlled()
    with write_transaction(store) as c:
        c.execute(intakes.insert().values(id=spec['task_id'], payload=native.original_payload(spec),
            status='native-authorized', revision=1, created_at=execution.now()))
    grant = execution.issue(store, 'EXPLICIT TEST OWNER', 'SIMULATED pytest', runtime)
    request = execution.submit(store, grant['id'], str(uuid4()))
    assert execution.process_one(store, runtime)
    assert not execution.process_one(store, runtime)
    assert runtime.calls == 1
    with store.connect() as c:
        rows = c.execute(select(model_attempts).where(model_attempts.c.execution_id == request['id'])).mappings().all()
    assert len(rows) == 1 and rows[0]['task_id'] == spec['task_id']
    assert rows[0]['actual_model'] is None
    assert rows[0]['usage']['values'] is None
    assert execution.review(store, request['id'])['status'] == 'failed'


@pytest.fixture
def native_checked(store, client, spec, tmp_path, monkeypatch, request):
    """Real file seals/DB/publication preview; provider and check results SIMULATED."""
    from workbench import real_confine
    runtime = native.prepare(tmp_path / 'native', spec)
    import shutil
    request.addfinalizer(lambda: shutil.rmtree(runtime.workspace.parent))
    monkeypatch.setattr(runtime, 'preflight', lambda: [dict(name='SIMULATED', status='passed')])
    monkeypatch.setattr(runtime, 'admission_gate', lambda *args: dict(name='SIMULATED allowance', status='passed'))
    monkeypatch.setattr(runtime, 'cleanup', lambda row: True)
    with monkeypatch.context() as registration_patch:
        registration_patch.setattr(native, 'Runtime', lambda root: runtime)
        registered = native.register(store, runtime.root, 'EXPLICIT TEST OWNER', 'SIMULATED pytest')
    intake = client.get('/api/intake/' + spec['task_id'])
    assert intake.status_code == 200, intake.text
    context_url = '/api/intake/' + spec['task_id'] + '/tasks/' + spec['task_id'] + '/execution'
    pending_context = client.get(context_url)
    assert pending_context.status_code == 200, pending_context.text
    assert pending_context.json()['runs'][0]['id'] == registered['id']
    baseline = json.loads((runtime.root / 'native-baseline.json').read_text())
    assert runtime.pristine_input()
    (runtime.workspace / native.FOCUSED).write_text('assert True, "synthetic candidate"\n')
    assert not runtime.pristine_input()
    accepted = boundary.export(runtime.workspace, baseline, [native.FOCUSED], runtime.root / 'accepted-native')
    provider = dict(usage=native_codex.tokens([dict(type='turn.completed', usage=dict(input_tokens=10, output_tokens=2))]),
        terminal=True, settled=True)
    (runtime.root / 'native-provider-result.json').write_text(json.dumps(provider))

    def check(command, cwd, **kwargs):
        assert kwargs['private_loopback'] is True
        assert str(runtime.workspace) not in kwargs['ro']
        assert not kwargs.get('rw')
        return dict(status='pass', rc=0, out='ok   test_reminder_gallery.py\n1 passed, 0 failed',
            usage=dict(cgroup_removed=True, leaked_after_kill=False))

    monkeypatch.setattr(real_confine, 'run', check)
    monkeypatch.setattr(runtime, 'execute', lambda identity, fence, permitted:
        runtime.verify_candidate(identity, fence, accepted, provider, permitted))
    assert execution.process_one(store, runtime)
    view = execution.review(store, registered['id'])
    assert view['status'] == 'checked'
    assert client.get(context_url).json()['stage'] == 'checked'
    return runtime, registered, view


def test_native_candidate_uses_existing_completion_and_exact_draft_owners(store, client, spec, tmp_path, native_checked):
    from workbench import real_publication
    runtime, registered, view = native_checked
    review = real_publication.review_checked(store, registered['id'])
    assert review['repository'] == 'Twich-Team/TwichUI'
    assert review['base'] == spec['contract']['base']
    assert review['files'] == [native.FOCUSED]
    approval = tmp_path / 'approval'
    approval.mkdir(mode=0o700)
    path = approval / 'checked-approval.json'
    path.write_text(json.dumps(dict(execution_id=registered['id'], binding=review['binding'],
        owner_instruction_sha256=spec['owner_instruction_sha256'])))
    path.chmod(0o600)
    assert real_publication.authorize_checked(store, registered['id'], approval)
    assert not real_publication.authorize_checked(store, registered['id'], approval)
    with store.connect() as c:
        records = c.execute(select(real_publication.records)).mappings().all()
    assert len(records) == 1 and records[0]['task_id'] == spec['task_id']
    candidate = runtime.root / 'candidates' / view['result']['candidate_id'] / 'files' / native.FOCUSED
    candidate.write_text('tampered\n')
    with pytest.raises(ValueError, match='integrity'):
        real_publication.review_checked(store, registered['id'])
