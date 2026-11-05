"""Scripted/non-model evidence only. No inference, server, real addon or grant."""
import io
import json
import time
from types import SimpleNamespace

import pytest

from workbench.real_execution import PATHS
from workbench.real_provider import TOOLS, implement
from workbench.real_stream import NamedStream
from workbench.real_tool_contract import (MAX_LINE, READ_PARAMETERS, ToolInputError,
    ToolDenied, ToolBudgetExceeded, parse_arguments, invoke)
from workbench.real_workspace import Tools


@pytest.fixture
def reader(tmp_path):
    p = tmp_path/PATHS[0]
    p.parent.mkdir(parents=True)
    p.write_text(''.join(f'line {i}\n' for i in range(1, 601)))
    return Tools(SimpleNamespace(workspace=tmp_path), lambda: True), p


def test_advertised_contract_defaults_and_pagination(reader):
    tools, _ = reader
    schema = TOOLS[0]['function']['parameters']
    assert schema == READ_PARAMETERS and schema['properties']['path']['enum'] == list(PATHS)
    assert schema['additionalProperties'] is False and schema['properties']['start']['default'] == 1
    assert 'default' not in schema['properties']['end']  # Dependent default cannot be a JSON-schema constant.
    assert tools.handle('read_file', {'path': PATHS[0]}).splitlines() == [f'line {i}' for i in range(1,251)]
    assert tools.handle('read_file', {'path': PATHS[0], 'start': 251}).splitlines() == [f'line {i}' for i in range(251,501)]
    assert tools.handle('read_file', {'path': PATHS[0], 'end': 2}) == 'line 1\nline 2\n'
    assert tools.handle('read_file', {'path': PATHS[0], 'start': 500, 'end': 500}) == 'line 500\n'


@pytest.mark.parametrize('args,code', [
    ({'start': 0}, 'start_line'), ({'start': -1}, 'start_line'),
    ({'start': True}, 'start_line'), ({'start': 1.0}, 'start_line'),
    ({'start': '1'}, 'start_line'), ({'start': None}, 'start_line'),
    ({'end': False}, 'end_line'), ({'end': None}, 'end_line'),
    ({'end': '250'}, 'end_line'), ({'end': 0}, 'end_line'),
    ({'start': 9, 'end': 8}, 'reversed_range'), ({'end': 251}, 'read_window'),
    ({'start': MAX_LINE+1}, 'start_line'), ({'end': MAX_LINE+1}, 'end_line'),
    ({'extra': 'DO-NOT-LOG-THIS'}, 'unknown_fields')])
def test_invalid_ranges_have_stable_codes_and_no_read(reader, args, code):
    tools, _ = reader
    with pytest.raises(ToolInputError) as error:
        tools.handle('read_file', {'path': PATHS[0], **args})
    assert error.value.code == code and tools.read == 0
    assert error.value.as_result()['error']['continuation_enabled'] is False


@pytest.mark.parametrize('start,end,expected', [(600, 700, 'line 600\n'), (601, None, ''), (MAX_LINE, None, '')])
def test_eof_clips_and_beyond_eof_is_empty(reader, start, end, expected):
    tools, _ = reader
    args = {'path': PATHS[0], 'start': start}
    if end is not None:
        args['end'] = end
    assert tools.handle('read_file', args) == expected
    assert tools.read == len(expected)


def test_empty_file_and_exact_whitespace(reader):
    tools, path = reader
    path.write_bytes(b'\tfirst\r\n second \n\nlast')
    assert tools.handle('read_file', {'path': PATHS[0], 'start': 2, 'end': 4}) == ' second \n\nlast'
    assert tools.handle('read_file', {'path': PATHS[0], 'end': 1}) == '\tfirst\r\n'
    path.write_bytes(b'')
    assert tools.handle('read_file', {'path': PATHS[0]}) == ''


@pytest.mark.parametrize('raw,code', [('{', 'argument_json'), ('[]', 'argument_object'),
    ('null', 'argument_object'), ('{"start":1,"start":2}', 'duplicate_field'),
    ('{"start":NaN}', 'argument_json'), ('{"start":Infinity}', 'argument_json')])
def test_actual_json_parser_rejects_invalid_inputs(raw, code):
    with pytest.raises(ToolInputError) as error:
        parse_arguments('read_file', raw)
    assert error.value.code == code


def test_missing_fields_shape_and_oversize(reader):
    tools, _ = reader
    with pytest.raises(ToolInputError, match='requires path'):
        tools.handle('read_file', {})
    with pytest.raises(ToolInputError, match='object'):
        tools.handle('read_file', [])
    for raw in [' '*2049, json.dumps({'path': PATHS[0], 'ignored': 'é'*1100})]:
        with pytest.raises(ToolBudgetExceeded, match='byte limit'):
            parse_arguments('read_file', raw)
    assert tools.read == 0


@pytest.mark.parametrize('path', ['../../private.key', '/etc/passwd', '!!!TwichUI/other.lua', {'path': 'secret'}])
def test_denied_paths_are_fatal_and_never_read(reader, path):
    tools, _ = reader
    with pytest.raises(ToolDenied):
        tools.handle('read_file', {'path': path})
    assert tools.read == 0


def test_symlink_file_or_parent_cannot_escape(reader, tmp_path):
    tools, path = reader
    outside = tmp_path/'outside'
    outside.mkdir()
    (outside/'Data.lua').write_text('SENSITIVE-CONTENT')
    path.unlink()
    path.symlink_to(outside/'Data.lua')
    with pytest.raises(ToolDenied):
        tools.handle('read_file', {'path': PATHS[0]})
    path.unlink()
    path.parent.rmdir()
    path.parent.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ToolDenied):
        tools.handle('read_file', {'path': PATHS[0]})
    assert tools.read == 0


def test_read_budget_exact_and_repeated_reads_charge(reader):
    tools, path = reader
    path.write_text('x'*22500)
    for _ in range(2):
        assert len(tools.handle('read_file', {'path': PATHS[0]})) == 22500
    assert tools.read == 45000
    with pytest.raises(ToolBudgetExceeded, match='character limit'):
        tools.handle('read_file', {'path': PATHS[0]})
    assert tools.read == 45000


def test_diagnostics_omit_contents_strings_unknown_keys_and_identifiers(reader):
    tools, _ = reader
    events = []
    malicious = '</script>\nSECRET\x1b[31m'
    calls = [dict(id=malicious, function=dict(name='read_file', arguments=json.dumps(
        {'path': PATHS[0], 'start': malicious}))),
        dict(function=dict(name='read_file', arguments=json.dumps({'path': malicious}))),
        dict(function=dict(name=malicious, arguments=malicious))]
    for i, call in enumerate(calls):
        with pytest.raises(ValueError):
            invoke(tools, call, 0, i, events.append)
    raw = json.dumps(events, ensure_ascii=True, allow_nan=False)
    assert 'SECRET' not in raw and '</script>' not in raw and '\\u001b' not in raw
    assert len(raw) < 2048 and [e['error_class'] for e in events] == ['input','security','security']
    assert events[0]['start'] == {'kind': 'string'} and events[1]['path'] == 'denied'
    assert not any(e['continuation_enabled'] for e in events)


def test_third_invalid_input_aborts_after_two_authorized_corrections(reader):
    tools, _ = reader
    class ScriptedOwnership:
        calls=0
        def request(self, *args, **kwargs):
            self.calls += 1
            return {'model':'scripted', 'usage':{'completion_tokens':20,'prompt_tokens':10},
                'choices':[{'finish_reason':'tool_calls','message':{'role':'assistant','tool_calls':[
                    {'id':'test','function':{'name':'read_file','arguments':json.dumps({'path':PATHS[0],'end':251})}}]}}]}
    adapter=ScriptedOwnership()
    events=[]
    with pytest.raises(ToolBudgetExceeded, match='third error'):
        implement(tools, 'NON-MODEL scripted test', '', 'scripted', lambda:True, ownership=adapter, on_tool=events.append)
    assert adapter.calls == 3 and events[0]['error_code']=='read_window'
    assert events[0]['continuation_enabled'] and events[1]['correction_count']==2
    assert events[2]['error_code']=='read_correction_limit' and not events[2]['continuation_enabled']
    assert events[0]['end']['value']==251


def test_e2e_scripted_named_stream_parser_validator_read_and_finish(reader, tmp_path, monkeypatch):
    """Actual production adapter branch/NamedStream with ONLY the HTTP boundary faked."""
    import workbench.real_stream as stream
    tools, _ = reader
    bodies=[]
    class Client:
        def __init__(self, *args):
            self.ends_at=time.monotonic()+120
            self.opener=self
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self, *args, **kwargs):
            if args[0].full_url.endswith('/input_tokens'):
                return b'{"input_tokens":10}'
            return json.dumps([{'id':0,'id_task':42,'is_processing':True,'next_token':[{'n_decoded':3}]}]).encode()
        def open(self, req, **kwargs):
            body=json.loads(req.data)
            bodies.append(body)
            if len(bodies)==1:
                args=json.dumps({'path':PATHS[0],'start':251})
                chunks=[{'model':'scripted','choices':[{'delta':{'role':'assistant','tool_calls':[
                    {'index':0,'id':'scripted-call','function':{'name':'read_', 'arguments':args[:20]}}]}}]},
                    {'choices':[{'delta':{'tool_calls':[{'index':0,'function':{'name':'read_file','arguments':args[20:]}}]},'finish_reason':'tool_calls'}]}]
            else:
                assert body['messages'][-1]['role']=='tool'
                assert body['messages'][-1]['content'].splitlines()==[f'line {i}' for i in range(251,501)]
                chunks=[{'model':'scripted','choices':[{'delta':{'content':'Scripted fixture finished.'},'finish_reason':'stop'}]}]
            chunks.append({'choices':[],'usage':{'prompt_tokens':10,'completion_tokens':20}})
            class Response(io.BytesIO):
                def readline(self, n=-1):
                    time.sleep(.06)
                    return super().readline(n)
            return Response(b''.join(b'data: '+json.dumps(c).encode()+b'\n\n' for c in chunks)+b'data: [DONE]\n\n')
    class Cancel:
        def __init__(self, secret, nonce, *args, **kwargs):
            self.nonce=nonce
        def delete(self, nonce):
            assert self.nonce==nonce
        def observe(self, nonce):
            return dict(nonce=nonce,task_id=42,producer_done=True,is_processing=False,n_decoded=0)
    monkeypatch.setattr(stream,'LocalHTTP',Client)
    monkeypatch.setattr(stream,'NativeCancellation',Cancel)
    adapter=NamedStream('SCRIPTED-NOT-A-CREDENTIAL',tmp_path/'session',lambda:[1,2],lambda:True,lambda e:None)
    events=[]
    result=implement(tools,'NON-MODEL scripted test only','','scripted',lambda:True,ownership=adapter,on_tool=events.append)
    assert result['completion_tokens']==40 and len(bodies)==2 and events[0]['outcome']=='completed'
    assert events[0]['start']=={'kind':'integer','value':251} and events[0]['end']=={'kind':'omitted'}
    assert tools.read==len(''.join(f'line {i}\n' for i in range(251,501)))
    assert '1-based inclusive' in bodies[0]['messages'][0]['content']
    # Run-owned pytest temporary evidence; never a historical artifact directory.
    (tmp_path/'scripted-evidence.json').write_text(json.dumps(dict(
        role='NON-MODEL scripted fixture; not the historical rejected request',
        transport='Actual NamedStream with fake HTTP/cancellation boundary',
        scripted_responses=2, source_characters=tools.read,
        parsed_range=dict(start=251, end='omitted; resolves to500'),
        tool_diagnostics=events, result=result), ensure_ascii=True, indent=2))


def test_budget_diagnostic_is_terminal_and_contains_no_oversize_payload(reader):
    tools, _ = reader
    events=[]
    with pytest.raises(ToolBudgetExceeded):
        invoke(tools, {'function':{'name':'read_file','arguments':'SENSITIVE-ARGUMENT'*200}}, 0, 0, events.append)
    assert events[0]['error_class']=='budget' and events[0]['error_code']=='argument_limit'
    assert len(json.dumps(events))<512 and 'SENSITIVE' not in json.dumps(events)


def test_reply_truncation_remains_explicit_and_full_selection_is_charged(reader):
    tools, path = reader
    path.write_text(('x'*99+'\n')*250)
    class ScriptedOwnership:
        calls=0
        def request(self, body, *args, **kwargs):
            self.calls += 1
            if self.calls==1:
                choice={'finish_reason':'tool_calls','message':{'role':'assistant','tool_calls':[
                    {'id':'fixture','function':{'name':'read_file','arguments':json.dumps({'path':PATHS[0]})}}]}}
            else:
                reply=body['messages'][-1]['content']
                assert len(reply.encode())<8192 and reply.endswith('[Tool output truncated at 8 KiB.]')
                choice={'finish_reason':'stop','message':{'role':'assistant','content':'Scripted fixture only'}}
            return {'model':'scripted','usage':{'prompt_tokens':10,'completion_tokens':20},'choices':[choice]}
    result=implement(tools,'NON-MODEL scripted truncation test','','scripted',lambda:True,ownership=ScriptedOwnership())
    assert result['completion_tokens']==40 and tools.read==25000
