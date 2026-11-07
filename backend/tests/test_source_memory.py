"""Exact source retention and pre-generation local context accounting."""
import copy
import json

import pytest

from workbench.source_memory import retain
from workbench.planning_transport import LocalHTTP, PlanningUnavailable
from workbench.planning_provider import LocalProvider, BUG_INPUT, MAX_OUTPUT
from workbench.bug_evidence import BugPlanningResponse


def window(path, start, text):
    return dict(path=path,line=start,through_line=start+len(text.splitlines())-1,text=text)


def test_committed_memory_accumulates_deduplicates_and_does_not_change_authority():
    path = 'tests/test_example.lua'
    initial = dict(line=1,through_line=2,text='local c = {}\nlocal ready = true',coverage='module prelude')
    old = window(path,10,'local x = 1\nlocal y = 2')
    current = window(path,11,'local y = 2\nreturn c')
    packet = dict(base='committed',files=[dict(path=path,excerpts=[initial])],inspection_windows=[old,current])
    saved = copy.deepcopy(packet)
    wire = dict(files=copy.deepcopy(packet['files']))
    retain(wire,packet,[current])
    assert packet == saved
    assert [(p['line'],p['through_line']) for p in wire['files'][0]['excerpts']] == [(1,2),(10,12)]
    assert wire['files'][0]['excerpts'][1]['text'] == 'local x = 1\nlocal y = 2\nreturn c'
    assert wire['source_memory']['omitted_characters'] == 0
    assert wire['source_memory']['read_windows'] == [dict(path=path,line=10,through_line=11),dict(path=path,line=11,through_line=12)]
    contradictory = copy.deepcopy(current)
    contradictory['text'] = 'different\nreturn c'
    with pytest.raises(ValueError,match='disagree'):
        retain(dict(files=copy.deepcopy(packet['files'])),packet,[contradictory])


def test_memory_evicts_whole_lines_with_current_reads_first_and_honest_omissions():
    path = 'tests/test_example.lua'
    packet = dict(base='committed',files=[dict(path=path,excerpts=[dict(line=1,text='old long line')])],
                  inspection_windows=[window(path,5,'older'),window(path,8,'new')])
    wire = dict(files=copy.deepcopy(packet['files']))
    retain(wire,packet,[packet['inspection_windows'][-1]],budget=5)
    assert wire['files'][0]['excerpts'] == [dict(line=8,through_line=8,text='new',coverage='retained exact committed lines',current_read=True)]
    assert wire['source_memory']['retained_characters'] == 4
    assert wire['source_memory']['omitted_characters'] > 0
    assert 'not a claim' in wire['source_memory']['note']


@pytest.mark.parametrize('fault',['none','missing','bool','bad-count','overlong','transport','cancelled'])
def test_exact_template_count_never_observes_source_or_starts_generation(fault):
    transport = LocalHTTP()
    observed, calls = [], []
    transport.response_observer = lambda *args: observed.append(args)
    original_observer = transport.response_observer
    body = dict(model='controlled',messages=[dict(role='user',content='PRIVATE_SOURCE')],max_tokens=2600,
                reasoning_budget_tokens=1024,chat_template_kwargs=dict(enable_thinking=True))

    def read(req,limit):
        assert transport.response_observer is None
        assert req.get_header('Authorization') == 'Bearer inert'
        path = req.full_url.rsplit('/',1)[1]
        value = json.loads(req.data) if req.data else None
        calls.append((path,value,limit))
        if fault in {'transport','cancelled'}:
            raise PlanningUnavailable('controlled', 'canceled' if fault == 'cancelled' else 'transport')
        if path == 'props':
            return json.dumps({} if fault == 'missing' else dict(default_generation_settings=dict(n_ctx=True if fault == 'bool' else 32768))).encode()
        assert path == 'input_tokens' and value == body
        return json.dumps(dict(input_tokens=True if fault == 'bad-count' else 65537 if fault == 'overlong' else 3)).encode()

    transport.read = read
    if fault == 'none':
        assert transport.context_budget('inert',body) == dict(prompt_tokens=3,context_tokens=32768)
        assert [p for p,_,_ in calls] == ['props','input_tokens']
    else:
        with pytest.raises(PlanningUnavailable) as error:
            transport.context_budget('inert',body)
        assert error.value.reason == ('canceled' if fault == 'cancelled' else 'context_budget')
    assert not observed and transport.response_observer is original_observer


@pytest.mark.parametrize('count,capacity,allowed',[(8000,32768,True),(23000,32768,False),(8000,10000,False),(True,32768,False)])
def test_large_bug_context_is_counted_before_any_generation(count,capacity,allowed):
    provider = LocalProvider()
    provider.diagnostics = dict(generation_state='not_started')
    calls = []

    class Transport:
        seconds = 120

        def models(self,secret):
            return 'controlled'

        def context_budget(self,secret,body):
            calls.append('count')
            assert body['max_tokens'] == 2600 and body['reasoning_budget_tokens'] == 1024
            assert len(json.dumps(body).encode()) < BUG_INPUT
            return dict(prompt_tokens=count,context_tokens=capacity)

        def read(self,req,limit):
            calls.append('generate')
            assert limit == MAX_OUTPUT == 24000
            return json.dumps(dict(model='controlled',usage=dict(prompt_tokens=8000,completion_tokens=10,total_tokens=8010),
                choices=[dict(finish_reason='stop',message=dict(content=json.dumps(dict(finding='Need exact committed source.',
                    draft=dict(inspection=[dict(path='tests/test_example.lua',start=1,end=10)])))))] )).encode()

    context = json.dumps(dict(files=[],description='source context '*2000))
    if allowed:
        provider._infer(Transport(),'inert',BugPlanningResponse,context,expected_model='controlled')
        assert calls == ['count','generate'] and provider.diagnostics['generation_state'] == 'finished'
    else:
        with pytest.raises(PlanningUnavailable) as error:
            provider._infer(Transport(),'inert',BugPlanningResponse,context,expected_model='controlled')
        assert error.value.reason == 'context_budget'
        assert calls == ['count'] and provider.diagnostics['generation_state'] == 'not_started'
