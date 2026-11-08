"""Real Lua checks with a controlled Codex response stream, no real-provider claim."""
import json
import time
from uuid import uuid4

import pytest

from workbench import codex_provider,verification_profiles as profiles
from workbench.codex_runtime import CodexRuntime
from workbench import codex_runtime
from workbench.real_workspace import Runtime as Canonical
from test_verification_profiles import setup,BUGGY,CORRECT,PRODUCTION,ControlledLuaRuntime,boundary


class Runtime(CodexRuntime,ControlledLuaRuntime):
    def preflight(self):
        return [dict(name='controlled-boundary',status='passed')]


def prepared(tmp_path):
    original=setup(tmp_path,profiles.BUGFIX)
    root=original.root
    spec=json.loads((root/'recipe.json').read_text())
    spec['read_budget_policy']='distinct-lines-v1'
    spec['source_context']=[dict(path=p,start=1,end=60) for p in spec['read_paths']]
    (root/'recipe.json').write_text(json.dumps(spec))
    runtime=Runtime(root,dict(model='controlled-codex',effort=None))
    profiles.prepare(runtime,{})
    runtime.record_controls(boundary(runtime))
    return runtime


def test_real_check_feedback_corrects_a_known_incomplete_edit_without_changing_the_oracle(tmp_path,monkeypatch):
    runtime=prepared(tmp_path)
    monkeypatch.setattr(codex_runtime.confine,'root',lambda:'/controlled-test-boundary')
    calls=[]
    incomplete=BUGGY+'-- This harmless change does not repair the observed result.\n'
    def generate(model,effort,prompt,schema,stop,**kwargs):
        assert model=='controlled-codex' and not stop()
        calls.append(prompt)
        if len(calls)==1:
            edits=[dict(path=PRODUCTION,old=BUGGY,new=incomplete)]
        else:
            assert '"status": "fail"' in prompt
            assert incomplete.strip() in prompt.replace('\\n','\n')
            edits=[dict(path=PRODUCTION,old=incomplete,new=CORRECT)]
        return dict(edits=edits),dict(prompt_tokens=100,completion_tokens=20,total_tokens=120)
    monkeypatch.setattr(codex_provider,'generate',generate)
    oracle=runtime.oracle_source.read_bytes()
    result=runtime.execute(str(uuid4()),1,lambda:True)
    assert len(calls)==2
    assert result['verification']=='passed'
    assert result['usage']['values']['total_tokens']==240
    assert result['usage']['requests_observed']==2
    assert runtime.oracle_source.read_bytes()==oracle
    assert Canonical(runtime.root).verify_frozen(result)
    assert (tmp_path/'source'/PRODUCTION).read_text()==BUGGY


def test_uncertain_later_turn_retains_known_usage_and_never_replays_or_freezes_success(tmp_path,monkeypatch):
    runtime=prepared(tmp_path)
    monkeypatch.setattr(codex_runtime.confine,'root',lambda:'/controlled-test-boundary')
    calls=[]
    def generate(model,effort,prompt,schema,stop,**kwargs):
        calls.append(prompt)
        if len(calls)>1:
            raise codex_provider.Hold('timeout',started=True,terminal=False)
        return dict(edits=[dict(path=PRODUCTION,old=BUGGY,new=BUGGY+'-- Still incorrect.\n')]),dict(prompt_tokens=100,completion_tokens=20,total_tokens=120)
    monkeypatch.setattr(codex_provider,'generate',generate)
    identity=str(uuid4())
    with pytest.raises(codex_provider.Hold):
        runtime.execute(identity,1,lambda:True)
    assert len(calls)==2 and not list((runtime.root/'candidates').glob('*/result.json'))
    facts=runtime.failure_evidence(identity,1)
    assert facts['usage']['values'] is None
    assert facts['usage']['known_values']['total_tokens']==120
    assert facts['provider_terminal'] is False
    assert time.monotonic()<runtime.implementation_deadline
