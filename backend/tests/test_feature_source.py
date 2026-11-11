"""Actual isolated Git handoff mechanics; synthetic bytes are never model acceptance."""
import hashlib
from uuid import uuid4

import pytest

from workbench.feature_source import LocalCheckedCommit, Held, locked
from workbench.real_workspace import durable
from test_publication_local_prepare import git
from test_everyday import installed as installed
from test_task_execution import linked as linked


@pytest.fixture
def source(tmp_path):
    root = tmp_path/'original'
    root.mkdir()
    git(root,'init','-q')
    git(root,'config','user.name','NON-MODEL fixture')
    git(root,'config','user.email','test@example.invalid')
    (root/'tests').mkdir()
    (root/'tests/test_session.lua').write_text('local session = {}\nreturn session\n')
    git(root,'add','.')
    git(root,'commit','-qm','NON-MODEL base')
    return root


def preparation(tmp_path, source, text):
    """Construct immutable synthetic patch data, not a claimed checked execution."""
    path = 'tests/test_session.lua'
    base = git(source,'rev-parse','HEAD')
    original = (source/path).read_bytes()
    (source/path).write_bytes(text)
    patch = git(source,'diff','--no-ext-diff','--no-textconv','--binary',base,raw=True)
    (source/path).write_bytes(original)
    root = tmp_path/str(uuid4())
    root.mkdir(mode=0o700)
    evidence = root/'frozen'
    candidate = hashlib.sha256(patch).hexdigest()
    target = evidence/'candidates'/candidate
    durable(target/'changes.patch',patch)
    durable(target/'files'/path,text)
    snapshot = dict(execution_id=str(uuid4()),repository='Twich-Team/TwichUI',base_branch='main',
        base=base,branch='maintenance/workbench-'+candidate[:20],evidence_root=str(evidence),
        candidate_id=candidate,preview=dict(title='NON-MODEL handoff mechanics',task_id='NON-MODEL'),
        task_budget=dict(offer=dict(snapshot=dict(specification=dict(
            contract=dict(source=str(source),paths=[path]),
            identity=dict(name='NON-MODEL fixture',email='test@example.invalid'))))))
    # This exercises the low-level adapter only. Production prepare_input must
    # obtain real frozen completion + matching ledger via its existing owner.
    result = dict(inference_ran=True,files=[dict(path=path)],
        manifest=dict(patch_sha256=hashlib.sha256(patch).hexdigest(),
            tree={path:hashlib.sha256(text).hexdigest()}))
    adapter = LocalCheckedCommit(root,snapshot,scratch_root=root)
    adapter.permitted = lambda: True
    return adapter,snapshot,result


def test_two_isolated_inputs_carry_previous_bytes_and_never_change_original(tmp_path,source,monkeypatch):
    from workbench.publication_adapter import PilotGitHub
    def no_credentials(*args,**kwargs):
        pytest.fail('Credentialed publication helper was invoked during local preparation')
    monkeypatch.setattr(PilotGitHub,'_call',no_credentials)
    original = (source/'tests/test_session.lua').read_bytes()
    first = b'local session = {zoom = 2}\nreturn session\n'
    a,snapshot,result = preparation(tmp_path,source,first)
    commit1 = a.prepare(snapshot,result)
    assert a.prepare(snapshot,result) == commit1
    second = b'local session = {zoom = 2}\nfunction session.restore() return session.zoom end\nreturn session\n'
    b,next_snapshot,next_result = preparation(tmp_path,a.workspace,second)
    commit2 = b.prepare(next_snapshot,next_result)
    assert git(b.workspace,'rev-parse','HEAD^') == commit1
    assert (b.workspace/'tests/test_session.lua').read_bytes() == second
    assert git(b.workspace,'rev-list','--count',snapshot['base']+'..'+commit2) == '2'
    assert (source/'tests/test_session.lua').read_bytes() == original
    assert not git(source,'status','--porcelain=v1')
    assert not git(a.workspace,'status','--porcelain=v1')


def test_local_apply_interruption_reconciles_exact_patch_once(tmp_path,source,monkeypatch):
    a,snapshot,result = preparation(tmp_path,source,b'local session = {zoom = 2}\nreturn session\n')
    call = a._call
    interrupted = False
    def stop_after_apply(argv,*args,**kwargs):
        nonlocal interrupted
        value = call(argv,*args,**kwargs)
        if argv[:2] == ['git','apply'] and '--check' not in argv and not interrupted:
            interrupted = True
            raise OSError('NON-MODEL interruption after local apply')
        return value
    monkeypatch.setattr(a,'_call',stop_after_apply)
    with pytest.raises(OSError):
        a.prepare(snapshot,result)
    commit = a.prepare(snapshot,result)
    assert a.prepare(snapshot,result) == commit
    assert git(a.workspace,'rev-list','--count',snapshot['base']+'..HEAD') == '1'


@pytest.mark.parametrize('command', [
    ['git','push','origin','HEAD'],['git','fetch','origin'],
    ['git','ls-remote','https://github.com/Twich-Team/TwichUI.git'],
    ['git','clone','https://github.com/Twich-Team/TwichUI.git','elsewhere'],
    ['bash','-c','true'],['git','daemon'],['git','config','--local','credential.helper','unsafe'],
    ['git','-c','alias.unsafe=!false','unsafe'],
])
def test_local_preparation_refuses_remote_and_non_git_commands(tmp_path,source,command):
    a,_,_ = preparation(tmp_path,source,b'local session = {zoom = 2}\nreturn session\n')
    with pytest.raises(Held):
        a._call(command)


def test_withdrawn_permission_cannot_allocate_or_change_an_input(tmp_path,source):
    a,snapshot,result = preparation(tmp_path,source,b'local session = {zoom = 2}\nreturn session\n')
    a.permitted = lambda: False
    with pytest.raises(ValueError):
        a.prepare(snapshot,result)
    assert not a.location.exists()
    assert not git(source,'status','--porcelain=v1')


def test_same_source_has_one_writer_and_preserves_unknown_partial_work(tmp_path,source):
    a,snapshot,result = preparation(tmp_path,source,b'local session = {zoom = 2}\nreturn session\n')
    with locked(a.root):
        with pytest.raises(Held):
            with locked(a.root):
                pytest.fail('Concurrent checked-source writer entered')
        a.prepare(snapshot,result)
    (a.workspace/'unrelated.txt').write_text('preserve this unknown edit')
    with pytest.raises(ValueError):
        a.prepare(snapshot,result)
    assert (a.workspace/'unrelated.txt').read_text() == 'preserve this unknown edit'


def test_missing_frozen_predecessor_cannot_unlock_a_real_feature_plan(client,headers,store,installed):
    from workbench import worker,planning_worker,task_execution
    from test_feature_planning import submit,ControlledFeaturePlanner
    item,_ = submit(client,headers)
    planner = ControlledFeaturePlanner()
    assert worker.tick(store,planning_adapter=planner)
    assert worker.tick(store,planning_adapter=planner)
    planned = client.get('/api/intake/'+item['id']).json()
    proposal = planned['proposals'][0]
    decision = dict(command_id=str(uuid4()),proposal_id=proposal['id'],binding=proposal['binding'],
        scope_revision=proposal['scope_revision'],action='approve',note='NON-MODEL scope fixture')
    assert client.post('/api/intake/'+item['id']+'/decisions',headers=headers,json=decision).status_code == 200
    assert planning_worker.apply_one(store)
    current = client.get('/api/intake/'+item['id']).json()
    first = next(t for t in current['tasks'] if t['kind']=='task' and not t['dependencies'])
    with store.connect() as c:
        linked = task_execution.snapshot(c,item['id'],first['id'],decision['command_id'])
        assert task_execution.prerequisite_reason(c,linked) is None
        assert 'checked predecessor source' in task_execution.prerequisite_reason(c,linked,dict(
            execution_id=str(uuid4()),source='/untrusted/path',commit='a'*40,receipt_sha256='b'*64))
    assert not client.get('/api/intake/'+item['id']).json()['decisions'][-1].get('run_budget')


def test_legacy_permission_cannot_be_extended_with_a_feature_input(store,linked):
    from sqlalchemy import select,func
    from workbench import real_execution
    task,runtime,_ = linked
    with store.connect() as c:
        count = c.execute(select(func.count()).select_from(real_execution.grants)).scalar_one()
    runtime.feature_input = dict(execution_id=str(uuid4()),source='/untrusted',commit='a'*40)
    with pytest.raises(ValueError,match='checked predecessor source'):
        real_execution.issue(store,'NON-MODEL unauthorized input extension','pytest',runtime,task=task)
    with store.connect() as c:
        assert c.execute(select(func.count()).select_from(real_execution.grants)).scalar_one() == count
    assert runtime.calls == 0


def test_controlled_checked_database_flag_is_not_a_real_frozen_source(store,linked,tmp_path,monkeypatch):
    from sqlalchemy import select
    from workbench import feature_source,real_execution
    _,runtime,grant = linked
    assert real_execution.process_one(store,runtime)
    with store.connect() as c:
        row = c.execute(select(real_execution.requests).where(
            real_execution.requests.c.grant_id == grant['id'])).mappings().one()
        assert row['status']=='checked' and row['result']['inference_ran'] is False
    monkeypatch.setattr(feature_source,'configuration',lambda engine:dict(output_root=str(tmp_path)))
    with pytest.raises(ValueError):
        feature_source.prepare_input(store,row['id'])
    assert not (tmp_path/'feature-inputs').exists()
