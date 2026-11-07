"""Compile explicit observed calls, without inventing actions or results."""
import copy
from typing import Annotated

from pydantic import Field, StrictInt, TypeAdapter
from workbench.bug_evidence import Access, Data, Scalar


class ResultPosition(Data):
    number: StrictInt = Field(ge=1, le=512)
    path: Annotated[Access, Field(max_length=7)]


def compile_observation(value, validate, location):
    from workbench.planning_response import ResponseValidationError

    def reject(path, rule):
        raise ResponseValidationError('Invalid explicit observation target.',
            [*location, *path], rule, None)
    if not isinstance(value, dict) or not ({'at', 'results'} & value.keys()):
        return value  # Complete older data programs keep their strict validation.
    if set(value) != {'at', 'results'}:
        reject([], 'exact at and results fields without conflicting legacy observation fields')
    at, results = value['at'], value['results']
    kinds = {'return_value': 'return', 'client_field': 'field', 'captured_argument': 'captured'}
    if not isinstance(at, dict) or len(at) != 1 or next(iter(at), None) not in kinds:
        reject(['at'], 'one explicit return_value, client_field or captured_argument target')
    kind = next(iter(at))
    if kind == 'client_field':
        path = validate(TypeAdapter(Annotated[Access, Field(min_length=1)]), at[kind], (*location,'at',kind))
    else:
        position = validate(ResultPosition, at[kind], (*location,'at',kind))
        path = [position.number, *position.path]
    if not isinstance(results, dict) or set(results) != {'expected', 'incorrect'}:
        reject(['results'], 'explicit expected and distinct incorrect scalar results')
    values = {key:validate(TypeAdapter(Scalar), results[key], (*location,'results',key))
              for key in ('expected','incorrect')}
    return dict(source=kinds[kind], path=path, **values)


def wire_checkpoints(definitions, references, minimum):
    result = copy.deepcopy(definitions['Observation'])
    for branch in result.get('anyOf', [result]):
        branch['properties'].pop('after_step')
        branch['required'].remove('after_step')
    branches = result.get('anyOf', [result])
    definitions['CheckpointComparison'] = dict(anyOf=[dict(type='object',additionalProperties=False,
        properties={key:branch['properties'][key] for key in ('expected','incorrect')},
        required=['expected','incorrect']) for branch in branches])
    field_path = copy.deepcopy(branches[0]['properties']['path'])
    position = ResultPosition.model_json_schema()
    targets = []
    for key, value in [('return_value',position),('client_field',field_path),('captured_argument',position)]:
        targets.append(dict(type='object',additionalProperties=False,properties={key:value},required=[key]))
    definitions['CheckpointResult'] = dict(type='object',additionalProperties=False,
        properties=dict(at=dict(anyOf=targets),results={'$ref':'#/$defs/CheckpointComparison'}),
        required=['at','results'])
    definitions.pop('Observation')  # No flat observation reference remains on the wire.
    definitions['CallCheckpoint'] = dict(type='object', additionalProperties=False,
        properties=dict(before_call=dict(type='array', minItems=0, maxItems=12,
            items=dict(anyOf=[ref for op, ref in references if op != 'load'])),
            call=next(ref for op, ref in references if op == 'call'),
            observe={'$ref': '#/$defs/CheckpointResult'}),
        required=['before_call', 'call', 'observe'])
    return dict(type='array', minItems=minimum, maxItems=4,
                items={'$ref': '#/$defs/CallCheckpoint'})


def compile_checkpoints(reproducer, validate):
    from workbench.bug_evidence import Observation, Step
    from workbench.planning_response import ResponseValidationError

    def reject(path, expected, value):
        raise ResponseValidationError('Invalid explicit observed-call checkpoints.',
            ['plan', 'reproducer', 'checkpoints', *path], expected, value)

    if 'steps' in reproducer or 'observations' in reproducer:
        reject([], 'checkpoints without conflicting legacy steps or observations', None)
    checkpoints = reproducer['checkpoints']
    if not isinstance(checkpoints, list) or not 1 <= len(checkpoints) <= 4:
        reject([], 'one to four explicitly observed calls', None)
    steps, observations = [], []
    for index, item in enumerate(checkpoints):
        location = ('plan', 'reproducer', 'checkpoints', index)
        if not isinstance(item, dict) or set(item) != {'before_call', 'call', 'observe'}:
            reject([index], 'explicit before_call, call and observe fields', None)
        before = item['before_call']
        if not isinstance(before, list) or len(before) > 12:
            reject([index, 'before_call'], 'zero to twelve explicit actions; the whole program still has at most24 steps', None)
        for offset, value in enumerate(before):
            step = validate(Step, value, (*location, 'before_call', offset))
            if step.op == 'load':
                reject([index, 'before_call', offset], 'source loads belong in the explicit setup', None)
            steps.append(step.model_dump(mode='json'))
        call = validate(Step, item['call'], (*location, 'call'))
        if call.op != 'call':
            reject([index, 'call'], 'an explicit existing function call', call.op)
        steps.append(call.model_dump(mode='json'))
        observation = item['observe']
        if not isinstance(observation, dict) or 'after_step' in observation:
            reject([index, 'observe'], 'one result of this call without a separately supplied step index', None)
        observation = compile_observation(observation, validate, (*location,'observe'))
        checked = validate(Observation, dict(observation, after_step=len(steps)), (*location, 'observe'))
        observations.append(checked.model_dump(mode='json'))
    return steps, observations
