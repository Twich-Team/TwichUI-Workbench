"""No model/server calls: stream assembly and maintenance restoration guards."""
import json
import pytest
from workbench import real_lease as lease
from workbench.real_stream import merge_chunk, NamedStream, WIRE_COMPLETION_LIMIT
from workbench.real_primary import PrimaryHeld


def test_stream_text_and_split_tools_are_untrusted_strings():
    r = {'choices': [{'message': {}, 'finish_reason': None}]}
    merge_chunk(r, {'model': 'fixed', 'choices': [{'delta': {'content': '<script>\n',
        'tool_calls': [{'index': 0, 'id': 'a', 'function': {'name': 'read_', 'arguments': '{'}}]}}]})
    merge_chunk(r, {'choices': [{'delta': {'content': '\ttext', 'reasoning_content': 'thinking',
        'tool_calls': [{'index': 0, 'function': {'name': 'read_file', 'arguments': '}'}}]}, 'finish_reason': 'tool_calls'}],
        'usage': {'completion_tokens': 20, 'prompt_tokens': 5}})
    assert r['choices'][0]['message']['content'] == '<script>\n\ttext'
    assert r['choices'][0]['message']['tool_calls'][0]['function'] == {'name': 'read_file', 'arguments': '{}'}
    assert r['usage']['completion_tokens'] == 20
    with pytest.raises(PrimaryHeld):
        merge_chunk(r, {'choices': [{'delta': {'tool_calls': [{'index': 8}]}}]})


def test_wire_and_epoch_fences_prevent_request(tmp_path):
    client = NamedStream('never-sent', tmp_path/'state', lambda: [1, 2], lambda: True, lambda r: None)
    assert WIRE_COMPLETION_LIMIT < 2500
    with pytest.raises(PrimaryHeld):
        client.request({'max_tokens': 2500}, lambda: True, 10**12)
    client.epoch = lambda: [1, 3]
    with pytest.raises(PrimaryHeld):
        client.request({'max_tokens': 10}, lambda: True, 10**12)
    assert not (tmp_path/'state').exists()


def setup_restore(tmp_path, monkeypatch):
    env = tmp_path/'serve.env'
    env.write_bytes(b'intervening unrelated change\n')
    env.chmod(0o640)
    unit = tmp_path/'unit'
    unit.write_bytes(b'owned unit')
    for name, data in [('serve.env.original', b'ORIGINAL\n'), ('serve.env.temporary', b'TEMP\n'),
                       ('temporary.key', b'PRIVATE'), ('original.key', b'OLD')]:
        (tmp_path/name).write_bytes(data)
    (tmp_path/'state.json').write_text(json.dumps({'restored': False, 'unit_sha256': lease.digest(unit.read_bytes()),
        'original_epoch': [1, 2], 'original_mode': 0o640, 'applied': True, 'model_path': 'fixed'}))
    monkeypatch.setattr(lease, 'ENV', env)
    monkeypatch.setattr(lease, 'UNIT', unit)
    monkeypatch.setattr(lease, 'restart', lambda: pytest.fail('Unsafe restart'))
    monkeypatch.setattr(lease, 'epoch', lambda: [3, 4])
    return env


def test_intervening_change_never_overwritten(tmp_path, monkeypatch):
    env = setup_restore(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match='intervening'):
        lease.restore(tmp_path)
    assert env.read_bytes() == b'intervening unrelated change\n'


def test_wrong_process_never_overwritten(tmp_path, monkeypatch):
    env = setup_restore(tmp_path, monkeypatch)
    env.write_bytes(b'TEMP\n')
    env.chmod(0o600)
    monkeypatch.setattr(lease, 'process_matches', lambda *a: False)
    with pytest.raises(RuntimeError, match='process'):
        lease.restore(tmp_path)
    assert env.read_bytes() == b'TEMP\n'


def test_recoverable_restore_exact_bytes_mode_and_access(tmp_path, monkeypatch):
    env = setup_restore(tmp_path, monkeypatch)
    env.write_bytes(b'TEMP\n')
    env.chmod(0o600)
    monkeypatch.setattr(lease, 'process_matches', lambda *a: True)
    calls=[]
    monkeypatch.setattr(lease, 'restart', lambda: calls.append('restart'))
    monkeypatch.setattr(lease, 'wait_ready', lambda *a: {'model_path': 'fixed', 'default_generation_settings': {'n_ctx': 32768}})
    monkeypatch.setattr(lease, 'request', lambda *a: (401, b''))
    monkeypatch.setattr(lease, 'epoch', lambda: [3, 4])
    state = lease.restore(tmp_path)
    assert env.read_bytes() == b'ORIGINAL\n' and env.stat().st_mode & 0o777 == 0o640
    assert calls == ['restart'] and state['original_access_verified']
    lease.restore(tmp_path)
    assert calls == ['restart']


def test_restore_resumes_normal_service_after_a_recorded_stop_finished_late(tmp_path, monkeypatch):
    env = setup_restore(tmp_path, monkeypatch)
    env.write_bytes(b'ORIGINAL\n')
    state = json.loads((tmp_path/'state.json').read_text())
    state['restoration_started'] = True
    (tmp_path/'state.json').write_text(json.dumps(state))
    epoch = [None]
    calls = []
    def restart():
        calls.append('restart')
        epoch[0] = [3, 4]
    monkeypatch.setattr(lease, 'epoch', lambda: epoch[0])
    monkeypatch.setattr(lease, 'restart', restart)
    monkeypatch.setattr(lease, 'process_matches', lambda *a: True)
    monkeypatch.setattr(lease, 'wait_ready', lambda *a: {'model_path': 'fixed', 'default_generation_settings': {'n_ctx':32768}})
    monkeypatch.setattr(lease, 'request', lambda *a: (401, b''))
    assert lease.restore(tmp_path)['restored']
    assert calls == ['restart'] and epoch[0] == [3, 4]


def test_stop_timeout_covers_the_existing_systemd_stop_deadline(monkeypatch):
    calls = []
    def run(args, **kwargs):
        from types import SimpleNamespace
        calls.append((args, kwargs['timeout']))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(lease.subprocess, 'run', run)
    lease.restart()
    assert [(args[-2:], cap) for args,cap in calls] == [(['stop',lease.SERVICE],110),(['start',lease.SERVICE],30)]


def test_named_transport_probe_and_normal_completion_use_same_fence(tmp_path, monkeypatch):
    import io
    import time
    import workbench.real_stream as stream
    sent=[]
    reports=[]
    revoke_on_read = [False]
    revoked = [False]
    class Response(io.BytesIO):
        def readline(self, n=-1):
            time.sleep(.1)  # Let the disposable observer see the busy task.
            if revoke_on_read[0]:
                revoked[0] = True
            return super().readline(n)
    class Client:
        def __init__(self, *args):
            self.ends_at=time.monotonic()+100
            self.opener=self
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self, *args, **kwargs):
            return json.dumps([{'id': 0, 'id_task': 42, 'is_processing': True,
                               'next_token': [{'n_decoded': 2}]}]).encode()
        def open(self, req, **kwargs):
            sent.append(json.loads(req.data))
            chunks=[{'model': 'fixed', 'choices': [{'delta': {'content': 'text'}}]},
                    {'choices': [{'delta': {}, 'finish_reason': 'stop'}],
                     'usage': {'prompt_tokens': 10, 'completion_tokens': 3}}]
            return Response(b''.join(b'data: '+json.dumps(c).encode()+b'\n\n' for c in chunks)+b'data: [DONE]\n\n')
    class Cancel:
        def __init__(self, secret, nonce, task, slot, epoch, expected, **kwargs):
            assert task == 42 and slot == 0 and epoch() == expected
            self.nonce=nonce
        def delete(self, nonce):
            assert nonce == self.nonce
        def observe(self, nonce):
            return dict(nonce=nonce, task_id=42, is_processing=False, producer_done=True, n_decoded=0)
    monkeypatch.setattr(stream, 'LocalHTTP', Client)
    monkeypatch.setattr(stream, 'NativeCancellation', Cancel)
    client=NamedStream('not-in-body', tmp_path/'fence', lambda: [1, 2], lambda: True, reports.append)
    client.request({'model': 'fixed', 'max_tokens': 60}, lambda: True, time.monotonic()+30, probe=True)
    result=client.request({'model': 'fixed', 'max_tokens': 2496}, lambda: True, time.monotonic()+120)
    assert reports[0]['canceled'] and not reports[0]['completed']
    assert reports[1]['completed'] and reports[1]['fence_state'] == 'released'
    assert result['usage']['completion_tokens'] == 3
    assert all(b['stream'] and b['stream_options']['include_usage'] for b in sent)
    assert 'not-in-body' not in json.dumps(sent)+json.dumps(reports)
    revoke_on_read[0] = True
    with pytest.raises(PrimaryHeld, match='no longer current'):
        client.request({'max_tokens': 10}, lambda: not revoked[0], time.monotonic()+120)
    assert reports[-1]['fence_state'] == 'released'  # Cancellation remains allowed after grant revocation.
    assert not reports[-1]['completed']


def test_small_replacements_reuse_scope_and_rollback(tmp_path):
    from workbench.real_workspace import Tools
    from workbench.real_execution import PATHS
    class Runtime:
        workspace=tmp_path/'workspace'
        def enforce_storage(self):
            pass
        def scope(self):
            if (self.workspace/PATHS[0]).read_text() == 'out of scope':
                raise ValueError('Scope violation')
    path=tmp_path/'workspace'/PATHS[0]
    path.parent.mkdir(parents=True)
    path.write_text('first\nsecond\n')
    from test_verification_profiles import git
    runtime=Runtime()
    runtime.root=tmp_path/'evidence'
    runtime.root.mkdir()
    runtime.paths=PATHS
    for name in PATHS[1:]:
        other=runtime.workspace/name
        other.parent.mkdir(parents=True,exist_ok=True)
        other.write_text('-- unchanged allowed fixture\n')
    git(runtime.workspace,'init','-q')
    git(runtime.workspace,'add','.')
    git(runtime.workspace,'-c','user.name=Fixture','-c','user.email=fixture@invalid','commit','-qm','Controlled base')
    runtime.base=git(runtime.workspace,'rev-parse','HEAD').strip()
    tools=Tools(runtime, lambda: True)
    tools.handle('replace_text', {'path': PATHS[0], 'old': 'first', 'new': 'changed'})
    assert path.read_text() == 'changed\nsecond\n'
    with pytest.raises(ValueError, match='exactly one'):
        tools.handle('replace_text', {'path': PATHS[0], 'old': 'missing', 'new': 'new'})
    with pytest.raises(ValueError, match='Scope'):
        tools.handle('replace_text', {'path': PATHS[0], 'old': path.read_text(), 'new': 'out of scope'})
    assert path.read_text() == 'changed\nsecond\n'


def test_watchdog_restores_on_owner_loss_or_deadline(tmp_path, monkeypatch):
    state={'restored': False, 'owner_pid': 9999999999, 'owner_start': '0', 'setup_deadline': 10**12}
    (tmp_path/'state.json').write_text(json.dumps(state))
    calls=[]
    monkeypatch.setattr(lease, 'restore', lambda p: calls.append(p))
    lease.watchdog(tmp_path)
    assert calls == [tmp_path]
    state.update(owner_pid=1, setup_deadline=0)
    (tmp_path/'state.json').write_text(json.dumps(state))
    lease.watchdog(tmp_path)
    assert calls == [tmp_path, tmp_path]


def test_actual_settled_watchdog_preserves_source_with_a_clean_environment(tmp_path):
    """Actual production module, no provider/service calls: already-restored state."""
    import shutil
    import subprocess
    from pathlib import Path

    source = tmp_path / 'source'
    shutil.copytree(Path(lease.__file__).parent, source / 'workbench',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    private = tmp_path / 'private'
    private.mkdir(mode=0o700)
    (private / 'state.json').write_text(json.dumps({'restored': True}))
    before = {str(p.relative_to(source)): p.read_bytes() for p in source.rglob('*') if p.is_file()}
    child = subprocess.run(lease.watchdog_command(private),
                           env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': str(source)},
                           capture_output=True, timeout=15)
    assert child.returncode == 0, child.stderr.decode()
    assert (private / 'watchdog-ready.json').is_file()
    assert not list(source.rglob('__pycache__'))
    assert before == {str(p.relative_to(source)): p.read_bytes()
                      for p in source.rglob('*') if p.is_file()}


@pytest.mark.parametrize('kind', ['stream_bytes', 'line_bytes'])
def test_stream_limit_diagnostics_are_content_free_and_still_cancel(tmp_path, monkeypatch, kind):
    """NON-MODEL: actual named adapter with deterministic installed-format SSE."""
    import io
    import time
    import workbench.real_stream as stream
    secret='PRIVATE_TEST_CREDENTIAL'
    marker='UNTRUSTED_TEST_CONTENT'
    chunk={'id':'chatcmpl-controlled', 'object':'chat.completion.chunk',
           'created':1, 'model':'pilot-qwen38-27b', 'system_fingerprint':'b11638-23deec025',
           'choices':[{'index':0,'delta':{'content':marker},'finish_reason':None}]}
    if kind=='line_bytes':
        chunk['choices'][0]['delta']['content']=marker*4000
    line=b'data: '+json.dumps(chunk).encode()+b'\n\n'
    payload=line*(9000 if kind=='stream_bytes' else 1)
    if kind=='stream_bytes':
        # Framing can exceed the cap while generated text is only198,000 bytes.
        assert len(marker)*9000 < stream.STREAM_BYTES_LIMIT < len(payload)
    class Response(io.BytesIO):
        first=True
        def readline(self, size=-1):
            if self.first:
                self.first=False
                time.sleep(.1)  # Let the actual observer bind the owned task.
            return super().readline(size)
    class Client:
        def __init__(self,*args):
            self.ends_at=time.monotonic()+120
            self.opener=self
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def open(self,*args,**kwargs):return Response(payload)
        def read(self,*args,**kwargs):
            return json.dumps([{'id':0,'id_task':42,'is_processing':True,
                                'next_token':[{'n_decoded':2}]}]).encode()
    deletes=[]
    class Cancel:
        def __init__(self,key,nonce,task,slot,epoch,expected,**kwargs):
            assert key==secret and task==42 and slot==0 and epoch()==expected
            self.nonce=nonce
        def delete(self,nonce):
            assert nonce==self.nonce
            deletes.append(nonce)
        def observe(self,nonce):
            return dict(nonce=nonce,task_id=42,is_processing=False,producer_done=True,n_decoded=0)
    monkeypatch.setattr(stream,'LocalHTTP',Client)
    monkeypatch.setattr(stream,'NativeCancellation',Cancel)
    reports=[]
    client=NamedStream(secret,tmp_path/'state',lambda:[1,2],lambda:True,reports.append)
    with pytest.raises(PrimaryHeld,match='Stream exceeds bounded output'):
        client.request({'max_tokens':2496},lambda:True,time.monotonic()+120)
    report=reports[0]
    assert report['output_limits_exceeded']==[kind]
    assert report['wire_bytes']<=stream.STREAM_BYTES_LIMIT+stream.LINE_BYTES_LIMIT+1
    assert report['largest_line_bytes']<=stream.LINE_BYTES_LIMIT+1
    assert report['wire_lines']>0 if kind=='stream_bytes' else report['wire_lines']==0
    assert report['fence_state']=='released' and len(deletes)==1
    assert not report['completed'] and report['usage'] is None
    assert secret not in json.dumps(report) and marker not in json.dumps(report)
