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


def test_external_repair_feedback_cannot_upload_arbitrary_suite_output_or_unapproved_paths():
    from workbench.codex_runtime import disclosed_feedback
    allowed=['tests/test_allowed.lua']
    private='PRIVATE_OUTSIDE_APPROVED_SOURCE_AND_ENVIRONMENT'
    feedback=dict(status='fail',diagnostic=private,message=private,category=private,
        failed_check_name='complete-suite',exit_code=1,check_round=2,
        context_hint=dict(path='tests/unapproved.lua',start=1,end=10),
        original_test_differential=dict(status='fail',reason=private,diagnostic=private),unexpected=private)
    value=disclosed_feedback(feedback,allowed)
    assert private not in json.dumps(value) and 'unapproved.lua' not in json.dumps(value)
    assert value['failed_check_name']=='complete-suite' and value['original_failure_reproduced'] is False
    feedback['context_hint']=dict(path=allowed[0],start=2,end=6)
    assert disclosed_feedback(feedback,allowed)['context_hint']==feedback['context_hint']
    assert feedback['diagnostic']==private  # Full private evidence is not destroyed.
    assert disclosed_feedback(None,allowed) is None


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


def test_oversize_proposal_is_retained_privately_and_revised_from_feedback_without_partial_apply(tmp_path,monkeypatch):
    runtime=prepared(tmp_path)
    monkeypatch.setattr(codex_runtime.confine,'root',lambda:'/controlled-test-boundary')
    calls=[]
    overlarge=BUGGY+''.join('-- excessive\n' for _ in range(151))
    def generate(model,effort,prompt,schema,stop,**kwargs):
        calls.append(prompt)
        assert (runtime.workspace/PRODUCTION).read_text()==BUGGY
        if len(calls)==1:
            replacement=overlarge
        else:
            assert '"category": "change-budget"' in prompt
            assert '"allowed_changed_lines": 150' in prompt
            replacement=CORRECT
        return dict(edits=[dict(path=PRODUCTION,old=BUGGY,new=replacement)]),dict(prompt_tokens=100,completion_tokens=20,total_tokens=120)
    monkeypatch.setattr(codex_provider,'generate',generate)
    identity=str(uuid4())
    result=runtime.execute(identity,1,lambda:True)
    assert len(calls)==2 and result['verification']=='passed'
    assert result['usage']['requests_observed']==2
    raw=runtime.root/('codex-output-'+identity+'-1.json')
    retained=json.loads(raw.read_text())
    assert retained['completed'] is True and retained['output']['edits'][0]['new']==overlarge
    assert raw.stat().st_mode&0o077==0
    assert Canonical(runtime.root).verify_frozen(result)
