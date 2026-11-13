"""Exact connector strategy operation; replace only native provider IO."""
from contextlib import contextmanager
from pathlib import Path
import json
import time
from uuid import uuid4

import pytest

import strategy_connector as connector
from workbench import ai_strategy as ai,codex_provider as codex
from test_codex_provider import Transcript,completed,allowance


class Connection:
    def sendall(self,raw):
        self.result=json.loads(raw)


def strategy():
    return ai.Strategy(stages=[ai.Stage(id='first',provider='codex',model='fixture-model',effort='low',allocation=50)])


def request(operation='smart-turn'):
    return dict(operation=operation,strategy=strategy().model_dump(),suitable_local=False,reviewed_local=False,
        waiting_since={},manual_stage=None,requested_stage=None,**(dict(request_id=str(uuid4()),stage_id='first',prompt='Return JSON.',
            schema={'type':'object'},request_seconds=120) if operation=='smart-turn' else {}))


@pytest.fixture
def boundary(tmp_path,monkeypatch):
    evidence=tmp_path/'artifacts'/'codex-evidence'
    evidence.parent.mkdir()
    runtime=tmp_path/'runtime'
    runtime.mkdir()
    calls=[]
    monkeypatch.setattr(connector.Boundary,'stop',lambda _:False)
    monkeypatch.setattr(connector.Boundary,'hosts',lambda *a,**k:tmp_path/'hosts')
    state={'used':49}
    def observations(self,strategy):
        self.fresh=[connector.codex_observation(allowance(state['used']))]
        return self.fresh
    monkeypatch.setattr(connector.Boundary,'observations',observations)
    @contextmanager
    def app_server(*args):
        rpc=Transcript(limit=allowance(state['used']),events=completed())
        calls.append(rpc)
        yield rpc
        if rpc.started:
            state['used']=52
    monkeypatch.setattr(codex,'app_server',app_server)
    # Native after-read must reflect consumption before the producer closes.
    original=Transcript.call
    def call(self,method,params):
        if method=='account/rateLimits/read' and self.started:
            return allowance(52)
        return original(self,method,params)
    monkeypatch.setattr(Transcript,'call',call)
    def run(value):
        connection=Connection()
        connector.handle(connection,value,Path('/binary'),Path('/auth'),runtime,evidence,tmp_path/'ops'/'claude.json',lambda *a:'')
        return connection.result
    return run,state,calls,tmp_path


def test_exact_smart_turn_native_admission_and_cumulative_cutoff(boundary):
    run,state,calls,root=boundary
    assert run(request('smart-choose'))['decision']['stage_id']=='first'
    result=run(request())
    assert result.get('state')=='completed',result
    assert result['result']['actual_model']=='fixture-model'
    assert result['result']['usage']['total_tokens']==24
    assert result['admission']['overrun'][0]['used_percent']==52
    assert run(request())['state']=='waiting'
    assert len(calls)==1 and calls[0].started
    assert json.loads((root/'artifacts'/'ai-allowances'/'allowances.json').read_text())['active'] is None


def test_fresh_native_second_read_stops_dispatch_if_personal_cli_crossed_cutoff(boundary,monkeypatch):
    run,state,calls,_=boundary
    original=Transcript.call
    def updated(self,method,params):
        if method=='account/rateLimits/read':
            return allowance(51)
        return original(self,method,params)
    monkeypatch.setattr(Transcript,'call',updated)
    result=run(request())
    assert result.get('state')=='waiting',result
    assert calls and not calls[0].started
    assert not any(method=='turn/start' for method,_ in calls[0].calls)
    assert run(request('smart-choose'))['state']=='choice'  # Unused reservation released.


def test_duplicate_identity_is_held_across_new_connector_instances(boundary):
    run,_,calls,_=boundary
    value=request()
    assert run(value).get('state')=='completed'
    assert run(value)['state']=='held'
    assert len(calls)==1


def test_unknown_reset_is_unknown_and_credits_permission_remains_separate():
    result=allowance(12)
    result['rateLimits']['primary']['resetsAt']=None
    observation=connector.codex_observation(result)
    assert observation.windows[0].resets_at is None
    assert observation.paid_usage=='disabled'
    assert ai.evaluate(strategy(),[observation],[],now=time.time()).condition=='unknown'
    result['rateLimits']['credits']['hasCredits']=True
    assert connector.codex_observation(result).paid_usage=='unknown'
