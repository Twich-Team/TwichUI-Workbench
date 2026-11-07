"""Explicit fixture choices compile without inventing behavior or authority."""
import copy

import pytest

from workbench.bug_evidence import BugPlanningResponse
from workbench.bug_wire import compile_setup
from workbench.planning_response import ResponseValidationError, wire_schema
from test_bug_workflow import CLIENT_SOURCE, CLIENT_TEST, response
from test_verification_profiles import PRODUCTION, TEST


def wire_response(client=False):
    return wire_from_data(response(client))


def wire_from_data(original):
    value = copy.deepcopy(original)
    reproducer = value['plan']['reproducer']
    fixture = reproducer.pop('fixture')
    bootstrap = reproducer.pop('bootstrap', None)
    if bootstrap:
        reproducer['setup'] = dict(kind='client-bootstrap',
            test_path=bootstrap.get('test_path') or value['plan']['focused_test'],
            client=bootstrap['client'], through_line=bootstrap['through_line'])
        value['plan']['reproducer'] = {k: reproducer[k] for k in ('description','setup','steps','observations')}
        return value
    steps = reproducer['steps']
    first = next(i for i, step in enumerate(steps) if step['op'] == 'load')
    source = steps[first]['source']
    reproducer['setup'] = (dict(kind='client-loads', sources=[source], before_load=steps[:first])
                           if fixture == 'client' else dict(kind='module', source=source))
    reproducer['steps'] = steps[first+1:]
    for observation in reproducer['observations']:
        observation['after_step'] -= first+1
    value['plan']['reproducer'] = {k: reproducer[k] for k in ('description','setup','steps','observations')}
    return value


@pytest.mark.parametrize('client', [False, True])
def test_explicit_load_selection_preserves_program_observations_and_input(client):
    value = wire_response(client)
    before = copy.deepcopy(value)
    source, test = (CLIENT_SOURCE, CLIENT_TEST) if client else (PRODUCTION, TEST)
    normalized, facts = compile_setup(value, [dict(path=source), dict(path=test)])
    assert value == before
    assert BugPlanningResponse.model_validate(normalized).model_dump() == BugPlanningResponse.model_validate(response(client)).model_dump()
    assert facts['explicit_setup'] and facts['approved'] is False and facts['executed'] is False
    assert facts['prepended_steps'] == (2 if client else 1)


def test_explicit_committed_bootstrap_can_differ_from_focused_test():
    value = response(True, bootstrap=True)
    value['plan']['read_paths'].append('tests/test_setup.lua')
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
