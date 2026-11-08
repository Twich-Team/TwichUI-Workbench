"""Native reviewed Lua phases, independent expectations and denied escape paths.

Controlled unrelated examples only; never a flight-path acceptance answer.
"""
import copy
import json
import subprocess

import pytest
from pydantic import ValidationError

from workbench.bug_evidence import BugPlanningResponse, compile_plan, review_digest
from workbench.bug_program import compile as compile_program, schema, syntax
from workbench.planning_response import ResponseValidationError
from workbench.visual_scope import digest

SOURCE='!!!TwichUI/example.lua'
TEST='tests/test_example.lua'


def wire(code="return subject(' ALICE ')"):
    return dict(finding='The inspected operation leaves uppercase input.',plan=dict(
        scope='Correct the reported name normalization.',writable_paths=[SOURCE],focused_test=TEST,
        other_reads=[],reproducer=dict(description='The real exported function produces a lowercased name.',
            setup=dict(kind='module',source=SOURCE),phases=[dict(description='Observe the exported function.',
                code=code,expected='alice',incorrect='ALICE')])),question=None,limitation=None,inspection=None)


def compiled(code="return subject(' ALICE ')"):
    raw,facts=compile_program(wire(code),[dict(path=SOURCE,top_level_returns_value=True),dict(path=TEST)],{})
    plan=BugPlanningResponse.model_validate(raw).plan.model_dump(mode='json')
    return dict(plan=plan,bootstrap_source='',review_digest=review_digest(plan,''),
                windows=[dict(path=SOURCE,start=1,end=1)]*4),facts


def run(tmp_path,evidence,source="return function(s) return string.lower((s:gsub('^%s+',''):gsub('%s+$',''))) end"):
    (tmp_path/'!!!TwichUI').mkdir(exist_ok=True)
    tests=tmp_path/'tests'
    tests.mkdir(exist_ok=True)
    (tmp_path/SOURCE).write_text(source)
    (tests/'oracle.lua').write_text(compile_plan(evidence)['oracle'])
    return subprocess.run(['lua5.1','-e','ROOT="../!!!TwichUI/";TESTS="./";REPO="../";dofile("oracle.lua")'],
                          cwd=tests,capture_output=True,text=True,timeout=5)


def test_same_approved_program_proves_original_failure_and_candidate_success(tmp_path):
    evidence,facts=compiled()
    assert facts['executed'] is False and facts['approved'] is False
    original=run(tmp_path,evidence,"return function(s) return (s:gsub('^%s+',''):gsub('%s+$','')) end")
    candidate=run(tmp_path,evidence)
    assert original.returncode==1 and 'WB_BEHAVIOR bug-reproducer FAIL' in original.stdout
    assert candidate.returncode==0 and 'WB_BEHAVIOR bug-reproducer PASS' in candidate.stdout
    assert evidence['plan']['read_paths']==[SOURCE,TEST]


@pytest.mark.parametrize('code',[
    "return os.execute('false')", "return io.open('/etc/passwd')", "return require('socket')",
    "return loadfile('/etc/passwd')()", "return getfenv(1)", "return debug.getregistry()",
    "return getfenv(0).os.execute('false')",
    "print('WB_BEHAVIOR bug-reproducer PASS'); error('private failure')",
    'while true do end', 'return coroutine.wrap(function() while true do end end)()',
])
def test_program_cannot_escape_fixture_or_forge_verdict_and_loops_stop(tmp_path,code):
    evidence,_=compiled(code)
    result=run(tmp_path,evidence)
    assert result.returncode==2, result.stdout+result.stderr
    assert 'WB_BEHAVIOR bug-reproducer PASS' not in result.stdout
    assert 'private failure' not in result.stdout


def test_program_cannot_load_unreviewed_project_file(tmp_path):
    evidence,_=compiled("return loadfile('./other.lua')()")
    (tmp_path/'tests').mkdir()
    (tmp_path/'tests/other.lua').write_text("return 'alice'")
    result=run(tmp_path,evidence)
    assert result.returncode==2 and 'WB_BEHAVIOR' not in result.stdout


def test_bytecode_and_malformed_lua_reject_before_proposal(tmp_path):
    with pytest.raises(ResponseValidationError) as error:
        syntax('function broken(',2)
    assert error.value.path==['plan','reproducer','phases',2,'code']
    with pytest.raises(ResponseValidationError):
        compiled('\x1bLua')
    evidence,_=compiled()
    evidence['plan']['reproducer']['phases'][0]['code']='\x1bLua'
    with pytest.raises(ValidationError):
        compile_plan(evidence)
    target=tmp_path/'must-not-exist'
    syntax("os.execute('touch "+str(target)+"')",0)
    assert not target.exists(), 'Syntax compilation must not execute code'


def test_script_and_expected_value_changes_invalidate_review():
    evidence,_=compiled()
    for key,value in [('code',"return 'alice'"),('expected','changed')]:
        altered=copy.deepcopy(evidence)
        row=(altered['plan']['reproducer']['phases'][0] if key=='code'
             else altered['plan']['reproducer']['observations'][0])
        row[key]=value
        with pytest.raises(ValueError,match='changed'):
            compile_plan(altered)


def test_legacy_review_binding_does_not_acquire_new_optional_fields():
    from test_bug_workflow import response
    validated=BugPlanningResponse.model_validate(response()).plan.model_dump(mode='json')
    old_shape=copy.deepcopy(validated)
    old_shape['reproducer'].pop('phases')
    old_shape['reproducer'].pop('event_coverage')
    expected=digest(dict(kind='offline-lua-reproducer-v1',plan=old_shape,bootstrap_source=''))
    assert review_digest(validated,'')==expected


def test_wire_is_small_and_requires_actual_known_setup_and_paths():
    result=schema(dict(minimum_observations=3),[
        dict(path=SOURCE,top_level_returns_value=True),dict(path=TEST)])
    assert len(json.dumps(result).encode())<14000
    assert result['$defs']['Reproduction']['properties']['phases']['minItems']==3
    assert 'Step' not in result['$defs']
    setup=result['$defs']['Reproduction']['properties']['setup']['anyOf']
    assert setup[0]['properties']['source']['enum']==[SOURCE]


def event_evidence():
    code=["return client.Example.Current()",
          "client.Input=1; client.FireEvent('USER_INPUT_CHANGED'); return client.Example.Current()",
          "client.Input=2; client.FireEvent('USER_INPUT_CHANGED'); return client.Example.Current()"]
    original=wire()
    r=original['plan']['reproducer']
    r['setup']=dict(kind='client-bootstrap',test_path=TEST,client='c',through_line=5)
    r['phases']=[dict(description='Measure actual event-driven state.',code=text,
                      expected=i,incorrect=-1 if i==0 else 0 if i==1 else 1) for i,text in enumerate(code)]
    raw,_=compile_program(original,[dict(path=SOURCE),dict(path=TEST,client_bootstrap=dict(client='c',through_line=5))],dict(call_driven=True))
    plan=BugPlanningResponse.model_validate(raw).plan.model_dump(mode='json')
    bootstrap='''dofile(TESTS .. "harness.lua")
local c=MakeClient()
local chunk=assert(loadfile(ROOT .. "example.lua"));setfenv(chunk,c);chunk()
return c
'''
    return dict(plan=plan,bootstrap_source=bootstrap,review_digest=review_digest(plan,bootstrap),
                windows=[dict(path=SOURCE,start=1,end=1)]*4)


def test_stateful_program_drives_real_registered_callback_and_later_refresh(tmp_path):
    (tmp_path/'tests').mkdir()
    (tmp_path/'tests/harness.lua').write_text('''function MakeClient()
 local c=setmetatable({Input=0,handlers={}}, {__index=_G})
 c.Register=function(e,fn)c.handlers[e]=fn end
 c.FireEvent=function(e)if c.handlers[e] then c.handlers[e]() end end
 return c
end
''')
    evidence=event_evidence()
    base='''local state=0
Example={Current=function()return state end}
Register('USER_INPUT_CHANGED',function() BODY end)
'''
    original=run(tmp_path,evidence,base.replace('BODY','if state==0 then state=Input end'))
    candidate=run(tmp_path,evidence,base.replace('BODY','state=Input'))
    assert original.returncode==1,original.stdout+original.stderr
    assert candidate.returncode==0,candidate.stdout+candidate.stderr
    assert 'WB_FIXTURE_PHASE bug-reproducer 1 events=0' in candidate.stdout
    assert 'WB_FIXTURE_PHASE bug-reproducer 3 events=1' in candidate.stdout


@pytest.mark.parametrize('index,code',[(0,"client.FireEvent('USER_INPUT_CHANGED'); return client.Example.Current()"),
    (1,'return client.Example.Current()'),(2,"client.Input=2;client.FireEvent('OTHER_EVENT');return client.Example.Current()")])
def test_stateful_program_cannot_omit_or_substitute_repeated_event(tmp_path,index,code):
    (tmp_path/'tests').mkdir()
    (tmp_path/'tests/harness.lua').write_text('function MakeClient() return {Example={Current=function()return 0 end},FireEvent=function()end} end')
    evidence=event_evidence()
    evidence['plan']['reproducer']['phases'][index]['code']=code
    evidence['review_digest']=review_digest(evidence['plan'],evidence['bootstrap_source'])
    result=run(tmp_path,evidence,'-- unchanged unrelated module')
    assert result.returncode==2 and 'WB_BEHAVIOR bug-reproducer PASS' not in result.stdout


def test_coroutines_captured_by_committed_module_before_phases_keep_instruction_bound(tmp_path):
    evidence,_=compiled('return subject()')
    source='''local create,resume=coroutine.create,coroutine.resume
return function()
 local t=create(function()while true do end end)
 return resume(t)
end
'''
    result=run(tmp_path,evidence,source)
    assert result.returncode==2 and 'instruction bound' in result.stdout


def test_syntax_compilation_does_not_replace_missing_source_or_result():
    value=wire()
    del value['plan']['reproducer']['phases'][0]['expected']
    with pytest.raises(ValidationError):
        compile_program(value,[dict(path=SOURCE,top_level_returns_value=True)],{})
    with pytest.raises(ResponseValidationError):
        compile_program(wire(),[dict(path=SOURCE,top_level_returns_value=False)],{})
