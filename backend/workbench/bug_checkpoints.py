"""Compile explicit observed calls, without inventing actions or results."""
import copy


def wire_checkpoints(definitions, references, minimum):
    result = copy.deepcopy(definitions['Observation'])
    for branch in result.get('anyOf', [result]):
        branch['properties'].pop('after_step')
        branch['required'].remove('after_step')
    definitions['CheckpointResult'] = result
    definitions.pop('Observation')  # No flat observation reference remains on the wire.
    definitions['CallCheckpoint'] = dict(type='object', additionalProperties=False,
        properties=dict(before_call=dict(type='array', minItems=0, maxItems=4,
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
        if not isinstance(before, list) or len(before) > 4:
            reject([index, 'before_call'], 'zero to four explicit actions before the observed call', None)
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
        checked = validate(Observation, dict(observation, after_step=len(steps)), (*location, 'observe'))
        observations.append(checked.model_dump(mode='json'))
    return steps, observations
