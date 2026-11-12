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


@pytest.mark.parametrize('all_baselines',[False,True])
def test_committed_alias_and_unchanged_baseline_do_not_forge_original_failure(tmp_path,all_baselines):
    (tmp_path/'tests').mkdir()
    (tmp_path/'tests/harness.lua').write_text("""function MakeClient()
 local c=setmetatable({Input=0,handlers={}}, {__index=_G})
 c.Register=function(e,fn)c.handlers[e]=fn end
 c.FireEvent=function(e)if c.handlers[e] then c.handlers[e]() end end
 return c
end
""")
    evidence=event_evidence()
    repro=evidence['plan']['reproducer']
    for phase in repro['phases']:
        phase['code']=phase['code'].replace('client.','c.')
    repro['observations'][0]['incorrect']=0
    if all_baselines:
        for observation in repro['observations']:
            observation['incorrect']=observation['expected']
    evidence['review_digest']=review_digest(evidence['plan'],evidence['bootstrap_source'])
    base="""local state=0
Example={Current=function()return state end}
Register('USER_INPUT_CHANGED',function() BODY end)
"""
    if all_baselines:
        with pytest.raises(ValueError,match='one observation must distinguish'):
            run(tmp_path,evidence,base.replace('BODY','if state==0 then state=Input end'))
        return
    original=run(tmp_path,evidence,base.replace('BODY','if state==0 then state=Input end'))
    candidate=run(tmp_path,evidence,base.replace('BODY','state=Input'))
    assert original.returncode==1,original.stdout+original.stderr
    assert candidate.returncode==0,candidate.stdout+candidate.stderr


def test_equal_initial_baseline_compiles_without_inventing_a_counterexample():
    value=wire()
    value['plan']['reproducer']['phases'][0].update(expected=0,incorrect=0,code='return subject()')
    result,_=compile_program(value,[dict(path=SOURCE,top_level_returns_value=True),dict(path=TEST)],{})
    assert result['plan']['reproducer']['observations'][0]['expected']==0
    assert result['plan']['reproducer']['observations'][0]['incorrect']==0


@pytest.mark.parametrize('code',["return c.os.execute('exit 0')","return c.debug.getregistry()",
                                "return c._G.verdict('pass')",'while true do end'])
def test_committed_client_alias_has_no_additional_authority(tmp_path,code):
    (tmp_path/'tests').mkdir()
    (tmp_path/'tests/harness.lua').write_text("""function MakeClient()
 local c=setmetatable({Input=0,handlers={}}, {__index=_G})
 c.Register=function(e,fn)c.handlers[e]=fn end
 c.FireEvent=function(e)if c.handlers[e] then c.handlers[e]() end end
 return c
end
""")
    evidence=event_evidence()
    evidence['plan']['reproducer']['phases'][0]['code']=code
    evidence['review_digest']=review_digest(evidence['plan'],evidence['bootstrap_source'])
    result=run(tmp_path,evidence,"Example={Current=function()return 0 end}")
    assert result.returncode not in (0,1),result.stdout+result.stderr


def session_evidence():
    evidence=event_evidence()
    repro=evidence['plan']['reproducer']
    phases=[
        ("return client.Example.Current()",0,0),
        ("client.Input=1;client.FireEvent('USER_INPUT_CHANGED');return client.Example.Current()",1,1),
        ("client.Input=2;client.FireEvent('USER_INPUT_CHANGED');return client.Example.Current()",2,2),
        ("state.previous=client;restart_session({'ExampleDB'},'A');return client.Example.Current()",2,0),
        ("return client~=state.previous and client.Input==0",True,False),
        ("restart_session({'ExampleDB'},'B');return client.Example.Current()",0,2),
        ("client.Input=7;client.FireEvent('USER_INPUT_CHANGED');return client.Example.Current()",7,2),
        ("restart_session({'ExampleDB'},'A');return c.Example.Current()",2,0),
        ("return client.ExampleDB.unrelated",'retained','changed'),
    ]
    repro['phases']=[dict(description='Observe actual behavior in a fresh isolated client.',code=code) for code,_,_ in phases]
    repro['observations']=[dict(after_step=n+1,source='return',path=[1],expected=expected,incorrect=incorrect)
                           for n,(_,expected,incorrect) in enumerate(phases)]
    evidence['review_digest']=review_digest(evidence['plan'],evidence['bootstrap_source'])
    return evidence


def session_harness(tmp_path):
    (tmp_path/'tests').mkdir()
    (tmp_path/'tests/harness.lua').write_text('''function MakeClient()
 local c=setmetatable({Input=0,handlers={}}, {__index=_G})
 c.UnitGUID=function()return 'A' end
 c.Register=function(e,fn)c.handlers[e]=fn end
 c.FireEvent=function(e)if c.handlers[e] then c.handlers[e]() end end
 return c
end
''')


def test_bounded_session_restart_proves_saved_state_and_character_separation_without_retaining_runtime(tmp_path):
    session_harness(tmp_path)
    evidence=session_evidence()
    source='''ExampleDB=ExampleDB or {unrelated='retained'}
local identity=UnitGUID('player')
local count=RESTORE
Example={Current=function()return count end}
Register('USER_INPUT_CHANGED',function()
 count=Input
 ExampleDB[identity]={count=count}
end)
'''
    original=run(tmp_path,evidence,source.replace('RESTORE','0'))
    candidate=run(tmp_path,evidence,source.replace('RESTORE',"(ExampleDB[identity] and ExampleDB[identity].count) or 0"))
    assert original.returncode==1,original.stdout+original.stderr
    assert candidate.returncode==0,candidate.stdout+candidate.stderr
    assert 'WB_OBSERVATION bug-reproducer 4 incorrect' in original.stdout
    assert 'WB_OBSERVATION bug-reproducer 9 expected' in candidate.stdout


@pytest.mark.parametrize('replace_in_addon', [False, True])
def test_restart_preserves_saved_input_over_committed_fixture_initializer_but_keeps_addon_writes(
    tmp_path, replace_in_addon,
):
    session_harness(tmp_path)
    evidence = session_evidence()
    # This is the map fixture's actual load shape: a synthetic DB initializer
    # after module loading, followed by startup. Restart must supply saved input
    # rather than reset it through that original test initializer.
    evidence['bootstrap_source'] = evidence['bootstrap_source'].replace(
        'return c\n', "c.ExampleDB={unrelated='fixture-only'}\nreturn c\n")
    phases = evidence['plan']['reproducer']['phases']
    evidence['plan']['reproducer']['event_coverage'] = False
    phases[:] = [
        dict(description='Choose and save a real input.', code="client.ExampleDB={kept=17}; return client.ExampleDB.kept"),
        dict(description='Restart with serialized data.', code="restart_session({'ExampleDB'});return client.ExampleDB.kept"),
    ]
    evidence['plan']['reproducer']['observations'] = [
        dict(after_step=1,source='return',path=[1],expected=17,incorrect=0),
        dict(after_step=2,source='return',path=[1],expected=17,incorrect=0),
    ]
    evidence['review_digest'] = review_digest(evidence['plan'], evidence['bootstrap_source'])
    source = "Example={Current=function() return 0 end}; ExampleDB=ExampleDB or {}\n"
    if replace_in_addon:
        source += "ExampleDB={kept=0} -- actual addon writes must remain observable\n"
    observed = run(tmp_path, evidence, source)
    assert observed.returncode == (1 if replace_in_addon else 0), observed.stdout + observed.stderr
    assert 'WB_FIXTURE_ERROR' not in observed.stdout


@pytest.mark.parametrize('code',[
    "client.ExampleDB={secret=function()end};restart_session({'ExampleDB'});return 2",
    "client.ExampleDB={};client.ExampleDB.self=client.ExampleDB;restart_session({'ExampleDB'});return 2",
    "client.ExampleDB=setmetatable({},{__index={}});restart_session({'ExampleDB'});return 2",
    "restart_session({'../../privateDB'});return 2",
    "restart_session({'ExampleDB'},{});return 2",
    "for i=1,7 do restart_session({'ExampleDB'}) end;return 2",
    "restart_session({'ExampleDB'});return loadfile('/etc/passwd')()",
    "restart_session({'ExampleDB'});return coroutine.wrap(function()while true do end end)()",
])
def test_session_restart_rejects_nonserializable_data_escapes_and_runaway_operations(tmp_path,code):
    session_harness(tmp_path)
    evidence=session_evidence()
    evidence['plan']['reproducer']['phases'][3]['code']=code
    evidence['review_digest']=review_digest(evidence['plan'],evidence['bootstrap_source'])
    source="Example={Current=function()return Input end};Register('USER_INPUT_CHANGED',function()end)"
    result=run(tmp_path,evidence,source)
    assert result.returncode==2,result.stdout+result.stderr
    assert 'WB_BEHAVIOR bug-reproducer PASS' not in result.stdout


def test_saved_variables_restore_before_make_client_loads_modules_with_cached_database_locals(tmp_path):
    session_harness(tmp_path)
    path=tmp_path/'tests/harness.lua'
    text=path.read_text().replace(' return c\n', " c._G=c\n local chunk=assert(loadfile(ROOT..'example.lua'));setfenv(chunk,c);chunk()\n return c\n")
    path.write_text(text)
    evidence=session_evidence()
    bootstrap='dofile(TESTS.."harness.lua");local c=MakeClient();return c'
    evidence['bootstrap_source']=bootstrap
    evidence['review_digest']=review_digest(evidence['plan'],bootstrap)
    source='''ExampleDB=ExampleDB or {unrelated='retained'}
local saved=ExampleDB
local identity=UnitGUID('player')
local count=(saved[identity] and saved[identity].count) or 0
Example={Current=function()return count end}
Register('USER_INPUT_CHANGED',function()count=Input;saved[identity]={count=count}end)
'''
    result=run(tmp_path,evidence,source)
    assert result.returncode==0,result.stdout+result.stderr
