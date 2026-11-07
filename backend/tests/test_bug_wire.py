"""Explicit fixture choices compile without inventing behavior or authority."""
import copy

import pytest

from workbench.bug_evidence import BugPlanningResponse
from workbench.bug_wire import compile_setup
from workbench.planning_response import ResponseValidationError, wire_schema
from test_bug_workflow import CLIENT_SOURCE, CLIENT_TEST, response
from test_verification_profiles import PRODUCTION, TEST


def wire_response(client=False):
    return wire_from_data(response(client), checkpoints=False)


def wire_from_data(original, checkpoints=True):
    value = copy.deepcopy(original)
    value['plan']['other_reads'] = value['plan'].pop('read_paths')
    reproducer = value['plan']['reproducer']
    fixture = reproducer.pop('fixture')
    bootstrap = reproducer.pop('bootstrap', None)
    if bootstrap:
        reproducer['setup'] = dict(kind='client-bootstrap',
            test_path=bootstrap.get('test_path') or value['plan']['focused_test'],
            client=bootstrap['client'], through_line=bootstrap['through_line'])
        value['plan']['reproducer'] = {k: reproducer[k] for k in ('description','setup','steps','observations')}
        return with_checkpoints(value) if checkpoints else value
    steps = reproducer['steps']
    first = next(i for i, step in enumerate(steps) if step['op'] == 'load')
    source = steps[first]['source']
    reproducer['setup'] = (dict(kind='client-loads', sources=[source], before_load=steps[:first])
                           if fixture == 'client' else dict(kind='module', source=source))
    reproducer['steps'] = steps[first+1:]
    for observation in reproducer['observations']:
        observation['after_step'] -= first+1
    value['plan']['reproducer'] = {k: reproducer[k] for k in ('description','setup','steps','observations')}
    return with_checkpoints(value) if checkpoints else value


def with_checkpoints(value):
    value = copy.deepcopy(value)
    reproducer = value['plan']['reproducer']
    steps, observations = reproducer.pop('steps'), reproducer.pop('observations')
    for step in [*steps,*reproducer.get('setup',{}).get('before_load',[])]:
        if step['op'] in {'stub','capture'}:
            fields=step.pop('fields',[])
            args=step.pop('args',[])
            assert not (fields and args)
            step['returns']={'table_fields':fields} if fields else {'values':args}
    checkpoints, offset = [], 0
    for observation in observations:
        result = dict(observation)
        after = result.pop('after_step')
        assert after > offset and steps[after-1]['op'] == 'call'
        checkpoints.append(dict(before_call=steps[offset:after-1],call=steps[after-1],observe=wire_observation(result)))
        offset = after
    assert offset == len(steps)
    reproducer['checkpoints'] = checkpoints
    return value


def wire_observation(result):
    source,path=result['source'],result['path']
    kind={'return':'return_value','field':'client_field','captured':'captured_argument'}[source]
    at=path if source=='field' else dict(number=path[0],path=path[1:])
    return dict(at={kind:at},results={key:result[key] for key in ('expected','incorrect')})


@pytest.mark.parametrize('client', [False, True])
def test_explicit_load_selection_preserves_program_observations_and_input(client):
    value = wire_response(client)
    before = copy.deepcopy(value)
    source, test = (CLIENT_SOURCE, CLIENT_TEST) if client else (PRODUCTION, TEST)
    normalized, facts = compile_setup(value, [dict(path=source), dict(path=test)])
    assert value == before
    actual = BugPlanningResponse.model_validate(normalized).model_dump()
    expected = BugPlanningResponse.model_validate(response(client)).model_dump()
    assert set(actual['plan'].pop('read_paths')) == set(expected['plan'].pop('read_paths'))
    assert actual == expected
    assert facts['explicit_setup'] and facts['approved'] is False and facts['executed'] is False
    assert facts['prepended_steps'] == (2 if client else 1)


def test_explicit_committed_bootstrap_can_differ_from_focused_test():
    value = response(True, bootstrap=True)
    value['plan']['other_reads'] = value['plan'].pop('read_paths') + ['tests/test_setup.lua']
    reproducer = value['plan']['reproducer']
    reproducer.pop('fixture')
    reproducer.pop('bootstrap')
    reproducer['setup'] = dict(kind='client-bootstrap', test_path='tests/test_setup.lua', client='c', through_line=5)
    files = [dict(path=CLIENT_SOURCE), dict(path=CLIENT_TEST),
             dict(path='tests/test_setup.lua', client_bootstrap=dict(client='c', through_line=5))]
    normalized, facts = compile_setup(value, files)
    result = BugPlanningResponse.model_validate(normalized)
    assert result.plan.focused_test == CLIENT_TEST
    assert result.plan.reproducer.bootstrap.test_path == 'tests/test_setup.lua'
    assert result.plan.reproducer.observations[0].after_step == 2
    assert facts['prepended_steps'] == 0


@pytest.mark.parametrize('fault', ['unknown_source', 'conflict', 'missing_steps', 'unsafe_prep', 'unknown_setup', 'changed_bootstrap'])
def test_invalid_or_ambiguous_setup_never_gains_a_program(fault):
    value = wire_response(True)
    r = value['plan']['reproducer']
    files = [dict(path=CLIENT_SOURCE), dict(path=CLIENT_TEST, client_bootstrap=dict(client='c', through_line=5))]
    if fault == 'unknown_source':
        r['setup']['sources'] = ['!!!TwichUI/uninspected.lua']
    elif fault == 'conflict':
        r['fixture'] = 'module'
    elif fault == 'missing_steps':
        r.pop('steps')
    elif fault == 'unsafe_prep':
        r['setup']['before_load'] = [dict(op='call', path=['os','execute'], args=['forbidden'])]
    elif fault == 'unknown_setup':
        r['setup'] = dict(kind='arbitrary-command')
    else:
        r['setup'] = dict(kind='client-bootstrap', test_path=CLIENT_TEST, client='c', through_line=6)
    with pytest.raises((ResponseValidationError, ValueError)):
        compile_setup(value, files)


def test_schema_requires_explicit_setup_and_keeps_write_scope_separate():
    files = [dict(path=CLIENT_SOURCE), dict(path='!!!TwichUI/helper.lua', support_only=True),
             dict(path=CLIENT_TEST, client_bootstrap=dict(client='c', through_line=5))]
    schema = wire_schema(BugPlanningResponse, files=files)
    reproducer = schema['$defs']['Reproducer']
    assert 'setup' in reproducer['required'] and 'fixture' not in reproducer['properties']
    choices = reproducer['properties']['setup']['anyOf']
    assert choices[0]['properties']['test_path']['const'] == CLIENT_TEST
    assert schema['$defs']['BugPlan']['properties']['writable_paths']['items']['enum'] == [CLIENT_SOURCE, CLIENT_TEST]
    raw = response()
    raw['plan']['reproducer']['steps'][0] = dict(op='table', path=['unused'])
    assert compile_setup(raw, files) == (raw, None)
    with pytest.raises(ValueError, match='Select one to six'):
        BugPlanningResponse.model_validate(raw)


def test_setup_validation_reports_the_actual_wire_field_path():
    from pydantic import ValidationError

    value = wire_response(True)
    value['plan']['reproducer']['setup']['before_load'] = [dict(op='unrecognized', path=['Known'])]
    with pytest.raises(ValidationError) as failed:
        compile_setup(value, [dict(path=CLIENT_SOURCE), dict(path=CLIENT_TEST)])
    assert failed.value.errors()[0]['loc'] == ('plan','reproducer','setup','before_load',0,'op')


def test_nonreturning_client_chunk_is_not_offered_as_a_returned_module():
    files = [dict(path=PRODUCTION, top_level_returns_value=True),
             dict(path=CLIENT_SOURCE, top_level_returns_value=False), dict(path=TEST)]
    schema = wire_schema(BugPlanningResponse,files=files)
    choices = schema['$defs']['Reproducer']['properties']['setup']['anyOf']
    module = next(c for c in choices if c['properties']['kind']['const'] == 'module')
    assert module['properties']['source']['enum'] == [PRODUCTION]
    value = wire_response()
    value['plan']['reproducer']['setup']['source'] = CLIENT_SOURCE
    with pytest.raises(ResponseValidationError,match='explicitly selected fixture'):
        compile_setup(value,files)


def test_compiler_shape_and_module_prelude_preserve_namespace_without_execution(tmp_path):
    from workbench.source_context import compiled_shape, excerpts
    from workbench.bug_investigation import words

    marker = tmp_path/'must-not-exist'
    text = ('local Addon = TwichUI\nAddon.Travel = Addon.Travel or {}\nlocal TV = Addon.Travel\n'
            f'os.execute("touch {marker}")\nfunction TV.Flights() return true end\n')
    raw = text.encode()
    assert compiled_shape(raw)[1] is False and not marker.exists()
    assert compiled_shape(b'return function(x) return x end\n')[1] is True
    assert compiled_shape(b'\x1bLua')[1] is None
    item = dict(path=CLIENT_SOURCE)
    excerpts([item],{CLIENT_SOURCE:('unused',raw,text.splitlines())},{'flight'},words)
    assert any('local TV = Addon.Travel' in e['text'] for e in item['excerpts'])
    assert item['top_level_returns_value'] is False and not marker.exists()


def test_required_reads_derive_only_from_explicit_file_selections():
    value = wire_response(True)
    value['plan']['other_reads'] = []
    value['plan']['reproducer']['steps'].insert(0, dict(op='load', source='!!!TwichUI/support.lua'))
    for observation in value['plan']['reproducer']['observations']:
        observation['after_step'] += 1
    files = [dict(path=p) for p in [CLIENT_SOURCE, CLIENT_TEST, '!!!TwichUI/support.lua']]
    normalized, facts = compile_setup(value, files)
    assert set(normalized['plan']['read_paths']) == {CLIENT_SOURCE, CLIENT_TEST, '!!!TwichUI/support.lua'}
    assert normalized['plan']['writable_paths'] == value['plan']['writable_paths']
    assert normalized['plan']['reproducer']['observations'][0]['expected'] == value['plan']['reproducer']['observations'][0]['expected']
    assert facts['derived_read_count'] == 3
    assert 'other_reads' not in normalized['plan']


@pytest.mark.parametrize('fault', ['unknown_extra', 'unknown_action', 'unknown_write', 'legacy_conflict', 'missing_extra', 'nonstring'])
def test_derived_reads_reject_ambiguous_or_uninspected_selections(fault):
    value = wire_response(True)
    if fault == 'unknown_extra':
        value['plan']['other_reads'] = ['!!!TwichUI/uninspected.lua']
    elif fault == 'unknown_action':
        value['plan']['reproducer']['steps'].append(dict(op='load', source='!!!TwichUI/uninspected.lua'))
    elif fault == 'unknown_write':
        value['plan']['writable_paths'] = ['!!!TwichUI/uninspected.lua']
    elif fault == 'legacy_conflict':
        value['plan']['read_paths'] = [CLIENT_SOURCE, CLIENT_TEST]
    elif fault == 'missing_extra':
        value['plan'].pop('other_reads')
    else:
        value['plan']['other_reads'] = [dict(path=CLIENT_SOURCE)]
    with pytest.raises(ResponseValidationError):
        compile_setup(value, [dict(path=CLIENT_SOURCE), dict(path=CLIENT_TEST)])


def test_modern_schema_has_only_additional_reads_and_legacy_is_not_repaired():
    schema = wire_schema(BugPlanningResponse)
    plan = schema['$defs']['BugPlan']
    assert 'read_paths' not in plan['properties'] and 'other_reads' in plan['required']
    assert plan['properties']['other_reads']['minItems'] == 0
    value = response(True)
    value['plan']['read_paths'] = [CLIENT_TEST]
    assert compile_setup(value, [dict(path=CLIENT_SOURCE), dict(path=CLIENT_TEST)]) == (value, None)


@pytest.mark.parametrize('field,value,location,rule', [
    ('after_step', 24, 'after_step', 'existing listed action'),
    ('incorrect', True, 'incorrect', 'differ from the expected'),
    ('path', ['named'], 'path', 'positional result number'),
])
def test_observation_rejection_retains_exact_field_without_inventing_results(field,value,location,rule):
    from pydantic import ValidationError
    original = response()
    observation = original['plan']['reproducer']['observations'][0]
    if field == 'incorrect':
        value = observation['expected']
    observation[field] = value
    with pytest.raises(ValidationError) as failed:
        BugPlanningResponse.model_validate(original)
    issue = failed.value.errors()[0]
    assert issue['loc'] == ('plan','reproducer','observations',0,location)
    assert rule in str(issue['ctx']['error'])


@pytest.mark.parametrize('client', [False,True])
def test_observed_call_checkpoints_preserve_explicit_program_and_results(client):
    wire = wire_from_data(response(client))
    frozen = copy.deepcopy(wire)
    source,test = (CLIENT_SOURCE,CLIENT_TEST) if client else (PRODUCTION,TEST)
    compiled,facts = compile_setup(wire,[dict(path=source),dict(path=test)])
    actual = BugPlanningResponse.model_validate(compiled).model_dump()
    expected = BugPlanningResponse.model_validate(response(client)).model_dump()
    assert set(actual['plan'].pop('read_paths'))==set(expected['plan'].pop('read_paths'))
    assert actual==expected and wire==frozen and facts['explicit_checkpoints']
    assert facts['approved'] is False and facts['executed'] is False


@pytest.mark.parametrize('fault',['mixed','missing_call','not_call','index','unsafe','load','missing_result','too_many_actions'])
def test_checkpoint_compilation_rejects_missing_ambiguous_or_unsafe_content(fault):
    wire = wire_from_data(response(True))
    r = wire['plan']['reproducer']
    point = r['checkpoints'][0]
    if fault=='mixed':
        r['steps']=[]
    elif fault=='missing_call':
        point.pop('call')
    elif fault=='not_call':
        point['call']=dict(op='table',path=['ignored'])
    elif fault=='index':
        point['observe']['after_step']=1
    elif fault=='unsafe':
        point['call']=dict(op='call',path=['os','execute'])
    elif fault=='load':
        point['before_call']=[dict(op='load',source=CLIENT_SOURCE)]
    elif fault=='missing_result':
        point['observe']['results'].pop('expected')
    else:
        point['before_call']=[dict(op='table',path=['ignored'])]*13
    with pytest.raises((ValueError,ResponseValidationError)):
        compiled,_=compile_setup(wire,[dict(path=CLIENT_SOURCE),dict(path=CLIENT_TEST)])
        BugPlanningResponse.model_validate(compiled)


def test_three_state_checkpoints_have_distinct_call_indices_without_filling_values():
    from test_bug_process_rehearsal import three_observations
    wire = wire_from_data(three_observations(True))
    compiled,_=compile_setup(wire,[dict(path=CLIENT_SOURCE),dict(path=CLIENT_TEST)])
    result=BugPlanningResponse.model_validate(compiled)
    indexes=[o.after_step for o in result.plan.reproducer.observations]
    assert len(set(indexes))==3
    assert all(result.plan.reproducer.steps[i-1].op=='call' for i in indexes)
    assert [o.expected for o in result.plan.reproducer.observations]==[o['observe']['results']['expected'] for o in wire['plan']['reproducer']['checkpoints']]
    schema=wire_schema(BugPlanningResponse,dict(minimum_observations=3))
    r=schema['$defs']['Reproducer']
    assert r['required']==['description','setup','checkpoints']
    assert r['properties']['checkpoints']['minItems']==3


def test_literal_committed_client_access_paths_are_context_not_guessed_exports():
    from workbench.bug_investigation import client_access_paths
    lines=['local c=MakeClient("x")','-- local X=c.Hidden','local S="local X=c.Secret"',
           'local A = c.Addon.Widget','local B = Other.Private','local D = c.Real',
           'local Later=c.Later']
    actual=client_access_paths(lines,dict(client='c',through_line=6))
    assert [(x['path'],x['line']) for x in actual]==[(['Addon','Widget'],4),(['Real'],6)]
    assert all('unverified' in x['kind'] for x in actual)


@pytest.mark.parametrize('method',[False,True])
def test_reference_arguments_execute_real_table_callback_false_and_nil_in_oracle(tmp_path,method):
    import subprocess
    from workbench.bug_evidence import compile_plan, review_digest

    (tmp_path/'!!!TwichUI').mkdir()
    tests=tmp_path/'tests'
    tests.mkdir()
    (tests/'harness.lua').write_text('function MakeClient() return setmetatable({}, {__index=_G}) end\n')
    original=response(True)
    r=original['plan']['reproducer']
    r['steps']=[dict(op='load',source=CLIENT_SOURCE),dict(op='table',path=['Request']),
        dict(op='set',path=['Request','name'],value='Sam'),
        dict(op='set',path=['FalseValue'],value=False),dict(op='capture',path=['Notify']),
        dict(op='call',path=['Deliver'],args=[dict(ref=['Request']),dict(ref=['Notify']),
                                           dict(ref=['FalseValue']),dict(ref=['MissingValue'])])]
    r['steps'][-1]['method']=method
    r['observations']=[dict(after_step=6,source='captured',path=[2],expected='Sam',incorrect='Unknown')]
    plan=BugPlanningResponse.model_validate(original).plan.model_dump(mode='json')
    evidence=dict(plan=plan,bootstrap_source='',review_digest=review_digest(plan,''),
        windows=[dict(path=CLIENT_SOURCE,start=1,end=1)]*4)
    oracle=compile_plan(evidence)['oracle']
    (tests/'oracle.lua').write_text(oracle)
    params='self, request, callback, flag, ...' if method else 'request, callback, flag, ...'
    receiver=' and self.Request==request' if method else ''
    code=('function Deliver('+params+') assert(type(request)=="table" and type(callback)=="function" '
          'and flag==false and select("#",...)==1 and select(1,...)==nil'+receiver+'); '
          'callback("online", VALUE) end\n')
    for value,expected in [('"Unknown"','FAIL'),('request.name','PASS')]:
        (tmp_path/CLIENT_SOURCE).write_text(code.replace('VALUE',value))
        run=subprocess.run(['lua5.1','-e','ROOT="../!!!TwichUI/";TESTS="./";REPO="../";dofile("oracle.lua")'],
                           cwd=tests,capture_output=True,text=True,timeout=5)
        assert 'WB_BEHAVIOR bug-reproducer '+expected in run.stdout,run.stdout+run.stderr
        assert run.returncode==(1 if expected=='FAIL' else 0)


@pytest.mark.parametrize('fault',['empty','unsafe','extra','plain_object','stub'])
def test_argument_references_cannot_broaden_fixture_authority(fault):
    value=response(True)
    step=value['plan']['reproducer']['steps'][-1]
    step['args']=[dict(ref=['Request'])]
    if fault=='empty':
        step['args']=[dict(ref=[])]
    elif fault=='unsafe':
        step['args']=[dict(ref=['os','execute'])]
    elif fault=='extra':
        step['args']=[dict(ref=['Request'],code='forbidden')]
    elif fault=='plain_object':
        step['args']=[dict(name='Sam')]
    else:
        step['op']='stub'
    with pytest.raises(ValueError):
        BugPlanningResponse.model_validate(value)


@pytest.mark.parametrize('number',['1',True,0,513])
def test_result_positions_require_actual_bounded_numbers_with_exact_wire_location(number):
    from pydantic import ValidationError
    wire=wire_from_data(response())
    wire['plan']['reproducer']['checkpoints'][0]['observe']['at']['return_value']['number']=number
    with pytest.raises(ValidationError) as failed:
        compile_setup(wire,[dict(path=PRODUCTION),dict(path=TEST)])
    assert failed.value.errors()[0]['loc']==('plan','reproducer','checkpoints',0,'observe','at','return_value','number')


@pytest.mark.parametrize('fault',['mixed_target','unknown_target','legacy_conflict','missing_results'])
def test_result_targets_never_infer_a_missing_or_ambiguous_selection(fault):
    wire=wire_from_data(response())
    o=wire['plan']['reproducer']['checkpoints'][0]['observe']
    if fault=='mixed_target':
        o['at']['client_field']=['somewhere']
    elif fault=='unknown_target':
        o['at']={'arbitrary':[]}
    elif fault=='legacy_conflict':
        o['source']='return'
    else:
        o.pop('results')
    with pytest.raises(ResponseValidationError):
        compile_setup(wire,[dict(path=PRODUCTION),dict(path=TEST)])


@pytest.mark.parametrize('returned,native',[
    ({'values':[]},{'args':[]}),
    ({'values':[None,False,7]},{'args':[None,False,7]}),
    ({'table_fields':[{'path':[1,'id'],'value':7},{'path':[1,'ready'],'value':False}]},
     {'fields':[{'path':[1,'id'],'value':7},{'path':[1,'ready'],'value':False}]}),
])
def test_explicit_returns_preserve_selected_values_without_filling_contents(returned,native):
    from workbench.bug_wire import compile_returns
    value=dict(op='stub',path=['Provider','Rows'],returns=returned)
    before=copy.deepcopy(value)
    assert compile_returns(value,['step'])==dict(op='stub',path=['Provider','Rows'],**native)
    assert value==before


@pytest.mark.parametrize('returned,extra',[
    ({'values':[],'table_fields':[]},{}),({},{}),({'unknown':[]},{}),
    ({'table_fields':[]},{}),({'values':[]},{'args':[]}),({'values':[]},{'fields':[]}),
])
def test_ambiguous_missing_and_empty_table_returns_cannot_be_reinterpreted(returned,extra):
    from workbench.bug_wire import compile_returns
    with pytest.raises(ResponseValidationError):
        compile_returns(dict(op='stub',path=['Provider'],returns=returned,**extra),['step'])


def test_nested_stub_table_fields_execute_original_fail_candidate_pass(tmp_path):
    import subprocess
    from workbench.bug_evidence import compile_plan,review_digest
    (tmp_path/'!!!TwichUI').mkdir()
    tests=tmp_path/'tests'
    tests.mkdir()
    (tests/'harness.lua').write_text('function MakeClient() return setmetatable({}, {__index=_G}) end\n')
    value=response(True)
    fixture=value['plan']['reproducer']
    fixture['steps']=[dict(op='load',source=CLIENT_SOURCE),
        dict(op='stub',path=['Rows'],fields=[dict(path=[1,'id'],value=7),dict(path=[1,'ready'],value=False)]),
        dict(op='call',path=['ReadRow'],args=[])]
    fixture['observations']=[dict(after_step=3,source='return',path=[1],expected=False,incorrect=True)]
    wire=wire_from_data(value)
    output,_=compile_setup(wire,[dict(path=CLIENT_SOURCE),dict(path=CLIENT_TEST)])
    plan=BugPlanningResponse.model_validate(output).plan.model_dump(mode='json')
    evidence=dict(plan=plan,bootstrap_source='',review_digest=review_digest(plan,''),
                  windows=[dict(path=CLIENT_SOURCE,start=1,end=1)]*4)
    (tests/'oracle.lua').write_text(compile_plan(evidence)['oracle'])
    for expression,wanted in [('true','FAIL'),('row.ready','PASS')]:
        (tmp_path/CLIENT_SOURCE).write_text('function ReadRow() local row=Rows()[1]; assert(row.id==7); return '+expression+' end\n')
        result=subprocess.run(['lua5.1','-e','ROOT="../!!!TwichUI/";TESTS="./";REPO="../";dofile("oracle.lua")'],
                              cwd=tests,capture_output=True,text=True,timeout=5)
        assert 'WB_BEHAVIOR bug-reproducer '+wanted in result.stdout,result.stdout+result.stderr
        assert result.returncode==(1 if wanted=='FAIL' else 0)


def test_historical_file_scope_stays_readable_but_cannot_gain_new_approval():
    from fastapi import HTTPException
    from workbench.bug_evidence import BugEvidence,review_digest,validate_expectation_review
    value=response()
    value['plan']['scope']=PRODUCTION
    plan=BugPlanningResponse.model_validate(value).plan.model_dump(mode='json')
    evidence=dict(plan=plan,bootstrap_source='',review_digest=review_digest(plan,''),
                  windows=[dict(path=PRODUCTION,start=1,end=1)]*4)
    assert BugEvidence.model_validate(evidence).plan.scope==PRODUCTION
    with pytest.raises(HTTPException) as error:
        validate_expectation_review(dict(task_contract=dict(bug_evidence=evidence)),
                                   dict(expectation_review=evidence['review_digest']))
    assert error.value.status_code==422
