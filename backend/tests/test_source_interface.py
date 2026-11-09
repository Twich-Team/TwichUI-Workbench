"""Function-shaped source context, not guessed exports or executable diagnostics."""
import copy
import pytest
from workbench.source_interface import definitions,context,usage_examples
from workbench.source_memory import retain
from workbench import bug_source_planning as source


def test_literal_interface_keeps_inline_calls_and_does_not_execute_source():
    rows=definitions(b'''error('MUST NOT RUN')
-- function M.Fake() end
local note="function M.AlsoFake() end"
local M={}
function M.Read() return M.Inner() end
function M.Inner(value) return value end
M.Visit = function(v) return M.Read(v) end
return M
''')
    assert {r['name'] for r in rows}=={'M.Read','M.Inner','M.Visit'}
    assert 'M.Inner' in next(r for r in rows if r['name']=='M.Read')['calls']
    assert next(r for r in rows if r['name']=='M.Visit')['args']=='v'


def test_usage_examples_preserve_exact_calls_not_fabricated_definitions():
    raw=b'''error('SOURCE MUST NOT EXECUTE')
-- Other:Forged(true)
local note="Other:Quoted(true)"
local function hidden() Other:Nested(true) end
Controller:Activate("widget", true)
assert(Controller.IsActive())
local handle=io.open("MUST_NOT_OPEN", "w")
handle:close()
'''
    [example]=usage_examples(raw)
    assert example['name']=='Controller:Activate' and example['call_line']==5
    assert 'Controller:Activate("widget", true)' in example['text']
    assert 'assert(Controller.IsActive())' in example['text']
    assert not {'args','definition','expected','approved'} & set(example)


def test_selected_test_call_is_hash_checked_pinned_and_does_not_expand_source(monkeypatch):
    path='tests/test_unrelated_widget.lua'
    raw=b'local Widget = client.Widget\nWidget:Activate("preview", true)\nassert(Widget.IsActive())\n'
    item=dict(path=path,sha256='fixed',excerpts=[])
    packet=dict(base='fixed',plan_review='source-grounded-lua-v1',files=[item])
    before=copy.deepcopy(packet['files'])
    monkeypatch.setattr(source,'committed',lambda p,b:(raw,dict(sha256='fixed')))
    def inspect(packet,requests):
        result=[dict(path=path,sha256='fixed',line=r.start,through_line=r.end,
            text='\n'.join(raw.decode().splitlines()[r.start-1:r.end])) for r in requests]
        packet.setdefault('inspection_windows',[]).extend(result)
        return result,[],sum(len(w['text']) for w in result)
    monkeypatch.setattr(source,'inspect',inspect)
    priority,interface=context(packet,dict(title='Widget bug',description='Its preview should respond.'))
    assert packet['files']==before
    [example]=interface['committed_usage_examples']
    assert example['name']=='Widget:Activate' and example['path']==path
    assert 'text' not in example and any(w['coverage']=='committed public usage example' for w in priority)
    wire=dict(files=copy.deepcopy(packet['files']))
    retain(wire,packet,priority,1000)
    assert 'Widget:Activate("preview", true)' in wire['files'][0]['excerpts'][0]['text']
    monkeypatch.setattr(source,'committed',lambda p,b:(raw,dict(sha256='changed')))
    with pytest.raises(ValueError,match='usage-example source changed'):
        context(packet,dict(title='Widget bug',description='Preview'))


def test_usage_examples_are_distributed_across_selected_tests(monkeypatch):
    raw={
        'tests/test_many.lua':('local Many=client.Many\n'+'\n'.join(f'Many:Call{i}(true)' for i in range(24))).encode(),
        'tests/test_other.lua':b'local Other=client.Other\nOther:Activate(true)\n',
    }
    packet=dict(base='fixed',files=[dict(path=p,sha256='fixed',excerpts=[]) for p in raw])
    monkeypatch.setattr(source,'committed',lambda p,b:(raw[p],dict(sha256='fixed')))
    def inspect(packet,requests):
        result=[dict(path=r.path,sha256='fixed',line=r.start,through_line=r.end,
            text='\n'.join(raw[r.path].decode().splitlines()[r.start-1:r.end])) for r in requests]
        packet.setdefault('inspection_windows',[]).extend(result)
        return result,[],sum(len(w['text']) for w in result)
    monkeypatch.setattr(source,'inspect',inspect)
    _,interface=context(packet,dict(title='Widget bug',description='Preview'))
    examples=interface['committed_usage_examples']
    assert len(examples)<=16 and any(e['name']=='Other:Activate' for e in examples)


def test_explicit_read_focus_keeps_real_callees_instead_of_unrelated_keyword_matches(monkeypatch):
    focused='!!!TwichUI/Focused.lua'
    shared='!!!TwichUI/Shared.lua'
    noise='!!!TwichUI/Noise.lua'
    test='tests/test_noise.lua'
    raw={focused:b'local M={}\nfunction M.Target() return Shared.Value() end\nreturn M\n',
         shared:b'local Shared={}\nfunction Shared.Value() return 42 end\nreturn Shared\n',
         noise:b'local Noise={}\nfunction Noise.Read() return 0 end\nreturn Noise\n',
         test:b'local Noise=client.Noise\nNoise:Read()\n'}
    packet=dict(base='fixed',plan_review='source-grounded-intent-v1',
        files=[dict(path=p,sha256='fixed',excerpts=[]) for p in raw])
    monkeypatch.setattr(source,'committed',lambda p,b:(raw[p],dict(sha256='fixed')))
    def inspect(packet,requests):
        result=[dict(path=r.path,sha256='fixed',line=r.start,through_line=r.end,
            text='\n'.join(raw[r.path].decode().splitlines()[r.start-1:r.end])) for r in requests]
        packet.setdefault('inspection_windows',[]).extend(result)
        return result,[],sum(len(w['text']) for w in result)
    monkeypatch.setattr(source,'inspect',inspect)
    priority,interface=context(packet,dict(title='Noise bug',description='Noise must refresh.'),focus_paths={focused})
    assert {w['path'] for w in priority} == {focused,shared}
    assert not interface['committed_usage_examples']
    assert noise in interface['declarations']  # Navigation remains; unrelated bodies do not dominate.


def test_relevant_function_and_caller_are_read_pinned_and_catalogued(monkeypatch):
    path='!!!TwichUI/Example.lua'
    raw=b'''local M={}
function M.Noise() return 0 end
function M.Whisker() return M.Inner() end
function M.Inner() return 1 end
function M.Request() return M.Whisker() end
function M.Begin() return M.Request() end
return M
'''
    item=dict(path=path,sha256='fixed',excerpts=[dict(line=1,text='local M={}')])
    packet=dict(base='fixed',plan_review='source-grounded-lua-v1',files=[item])
    before=copy.deepcopy(item)
    calls=[]
    monkeypatch.setattr(source,'committed',lambda p,b:(raw,dict(sha256='fixed')))
    def inspect(packet,requests):
        windows=[]
        for request in requests:
            calls.append((request.start,request.end))
            lines=raw.decode().splitlines()
            w=dict(path=path,sha256='fixed',line=request.start,through_line=request.end,
                   text='\n'.join(lines[request.start-1:request.end]))
            windows.append(w)
        packet.setdefault('inspection_windows',[]).extend(windows)
        return windows,[],sum(len(w['text']) for w in windows)
    monkeypatch.setattr(source,'inspect',inspect)
    priority,catalog=context(packet,dict(title='Whisker bug',description='Whisker should update.'))
    assert packet['files'][0]==before and calls
    assert any('M.Whisker()@3-3' in x for x in catalog['declarations'][path])
    assert 'not runtime export proof' in catalog['notice']
    assert any(w['line']==3 for w in priority) and any(w['line']==5 for w in priority)
    wire=dict(files=copy.deepcopy(packet['files']))
    retain(wire,packet,priority,500)
    text='\n'.join(w['text'] for w in wire['files'][0]['excerpts'])
    assert 'function M.Whisker' in text and 'function M.Request' in text
    before_calls=list(calls)
    context(packet,dict(title='Whisker bug',description='Whisker should update.'))
    assert calls==before_calls


def test_changed_interface_hash_is_not_used(monkeypatch):
    packet=dict(base='base',files=[dict(path='!!!TwichUI/Example.lua',sha256='old')])
    monkeypatch.setattr(source,'committed',lambda *a:(b'return {}',dict(sha256='changed')))
    with pytest.raises(ValueError,match='changed'):
        context(packet,dict(title='A bug',description='An ordinary report.'))


def test_related_context_keeps_late_state_declarations_callees_writers_and_registration(monkeypatch):
    from workbench.source_interface import surrounding
    path='!!!TwichUI/Example.lua'
    text='local Root=Example\nlocal M={}\nRoot.Module=M\n'
    text+='\n'.join(f'function M.Noise{i}() local inside=99; return inside end' for i in range(20))+'\n'
    text+='''local prefix="note:"
local current="before"
local function Finish(v) return prefix..v end
local function Render(v) return Finish(v) end
function M.Whisker() return Render(current) end
function M.Refresh(v)
 current=v
end
Root.On("VISIT",function(_,v)
 M.Refresh(v)
end)
'''
    raw=text.encode()
    declarations=surrounding(raw)
    assert not any('inside' in r['names'] for r in declarations)
    assert any('Root.On' in r['text'] and 'M.Refresh' in r['text'] for r in declarations)
    monkeypatch.setattr(source,'committed',lambda p,b:(raw,dict(sha256='fixed')))
    packet=dict(base='fixed',plan_review='source-grounded-lua-v1',files=[dict(path=path,sha256='fixed',
        excerpts=[dict(line=1,text='local Root=Example')])])
    def inspect(packet,requests):
        windows=[]
        for r in requests:
            w=dict(path=path,sha256='fixed',line=r.start,through_line=r.end,
                   text='\n'.join(text.splitlines()[r.start-1:r.end]))
            windows.append(w)
        packet.setdefault('inspection_windows',[]).extend(windows)
        return windows,[],sum(len(w['text']) for w in windows)
    monkeypatch.setattr(source,'inspect',inspect)
    priority,_=context(packet,dict(title='Whisker stale',description='Whisker should refresh.'))
    wire=dict(files=copy.deepcopy(packet['files']))
    retain(wire,packet,priority,1500)
    visible='\n'.join(w['text'] for w in wire['files'][0]['excerpts'])
    for required in ['Root.Module=M','local prefix=','local current=', 'function Render', 'function Finish',
                     'function M.Whisker','function M.Refresh','Root.On("VISIT"']:
        assert required in visible,required
