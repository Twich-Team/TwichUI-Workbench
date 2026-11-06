"""Data-only behavioral scenarios; generated Lua is NEVER its own semantic authority.

The planning owner may supply a bounded model draft. A separate human scope decision
must confirm its exact expectations. Runtime/profile owners still run every control
and independently certify the results. There is no provider, shell or activation here.
"""
import difflib
import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictBool, StrictFloat, StrictInt, StrictStr, model_validator

from workbench.visual_scope import digest


class Data(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


Scalar = StrictBool | StrictInt | StrictFloat | StrictStr | None
Key = Annotated[str, Field(min_length=1, max_length=80)]
Keys = Annotated[list[Key], Field(min_length=1, max_length=8)]
LuaPath = Annotated[str, Field(pattern=r'^(?:!!!TwichUI|TwichUI_Settings)/[A-Za-z0-9_./!-]+\.lua$', max_length=240)]
TestPath = Annotated[str, Field(pattern=r'^tests/test_[A-Za-z0-9_-]+\.lua$', max_length=240)]
FORBIDDEN = {'_G', 'os', 'io', 'debug', 'package', 'require', 'dofile', 'loadfile', 'loadstring',
             'getfenv', 'setfenv', 'setmetatable', 'getmetatable'}


class Setup(Data):
    op: Literal['set', 'call']
    path: Keys
    value: JsonValue = None
    args: Annotated[list[Scalar], Field(max_length=8)] = []
    method: bool = False


class Observation(Data):
    # Empty path means the first return value; otherwise an exact client field.
    path: Annotated[list[Key], Field(max_length=8)] = []
    expected: Scalar


class Scenario(Data):
    id: Annotated[str, Field(pattern=r'^[a-z][a-z0-9-]{0,47}$')]
    acceptance_index: Annotated[int, Field(ge=0, le=7)]
    test_path: TestPath
    fixture: Literal['module', 'client', 'loaded-client']
    source_paths: Annotated[list[LuaPath], Field(min_length=1, max_length=4)]
    # Module fixture: the first source file returns a function or a table.
    target: Annotated[list[Key], Field(max_length=8)] = []
    name: Annotated[str, Field(min_length=1, max_length=80)] = 'Regression'
    addons: Annotated[list[Key], Field(min_length=1, max_length=8)] = ['!!!TwichUI']
    setup: Annotated[list[Setup], Field(max_length=8)] = []
    args: Annotated[list[Scalar], Field(max_length=8)] = []
    method: bool = False
    observe: Observation
    incorrect: Scalar  # Reviewed behavioral counterexample, never an arbitrary sentinel.

    @model_validator(mode='after')
    def bounded_data(self):
        raw = self.model_dump(mode='json')
        encoded = json.dumps(raw, allow_nan=False)
        if len(encoded.encode()) > 6000:
            raise ValueError('Scenario exceeds the bounded data budget')
        def walk(value, depth=0):
            if depth > 8:
                raise ValueError('Scenario nesting limit')
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in FORBIDDEN or key.startswith('__'):
                        raise ValueError('Only ordinary fixture data and exported functions are supported')
                    walk(child, depth+1)
            elif isinstance(value, list):
                for child in value:
                    walk(child, depth+1)
        walk(raw)
        paths = [self.target, self.observe.path, *(a.path for a in self.setup)]
        if any(k in FORBIDDEN or k.startswith('__') for p in paths for k in p):
            raise ValueError('Unsafe fixture path')
        if any('..' in Path(p).parts for p in self.source_paths):
            raise ValueError('Source traversal refused')
        if self.fixture in ('client', 'loaded-client') and not self.target:
            raise ValueError('A client scenario needs an exported target function')
        if self.fixture in ('module', 'loaded-client') and (self.setup or self.observe.path):
            label = 'Module' if self.fixture == 'module' else 'Loaded-client'
            raise ValueError(f'{label} scenarios support a scalar first return only')
        if self.fixture == 'loaded-client' and (len(self.source_paths) != 1
                or self.addons != ['!!!TwichUI'] or not self.source_paths[0].startswith('!!!TwichUI/')):
            raise ValueError('A loaded client needs one core addon source')
        if self.method and not self.target:
            raise ValueError('A method needs a receiver path')
        if self.observe.expected == self.incorrect:
            raise ValueError('Specify an incorrect outcome distinguishable from the expected result')
        return self


class Question(Data):
    acceptance_index: Annotated[int, Field(ge=0, le=7)]
    question: Annotated[str, Field(min_length=1, max_length=400)]


class ScenarioDraft(Data):
    scenarios: Annotated[list[Scenario], Field(max_length=3)] = []
    unresolved: Annotated[list[Question], Field(max_length=8)] = []


class ScenarioResponse(Data):
    input_sha256: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
    draft: ScenarioDraft
    model: Annotated[str, Field(min_length=1, max_length=160)]


class BehavioralPreparation(Data):
    version: Literal[1] = 1
    status: Literal['hold', 'review_required']
    scenarios: list[Scenario]
    holds: list[str]
    origin: str
    input_sha256: str
    review_digest: str = ''
    execution_authorized: Literal[False] = False


def input_binding(original, feedback, base, paths):
    return digest(dict(original=original, feedback=feedback, repository='Twich-Team/TwichUI',
                       resolved_base=base, writable_paths=paths))


def plan(original, paths, draft=None, *, origin='no-behavior-planner', feedback='', base=None):
    acceptance = original['regression_tests']['acceptance']
    parsed = ScenarioDraft.model_validate(draft or {})
    holds = []
    covered = set()
    seen = set()
    for scenario in parsed.scenarios:
        if scenario.id in seen:
            holds.append('Give each behavioral example a distinct identity.')
        seen.add(scenario.id)
        if scenario.test_path not in paths:
            holds.append(f'Example {scenario.id}: select its existing test file in the reviewed scope.')
        if scenario.acceptance_index >= len(acceptance):
            holds.append(f'Example {scenario.id}: identify an existing expected result from the request.')
        else:
            covered.add(scenario.acceptance_index)
    for question in parsed.unresolved:
        if question.acceptance_index < len(acceptance):
            holds.append(f'Expected result {question.acceptance_index+1} ({acceptance[question.acceptance_index]}): {question.question}')
    for i, expected in enumerate(acceptance):
        if i not in covered:
            holds.append(f'Expected result {i+1} ({expected}): specify a concrete input/setup, the operation, its expected observable result and a behavior-relevant incorrect outcome to reject. No reviewed behavioral example establishes this yet.')
    if len(set(s.test_path for s in parsed.scenarios)) > 1 or len(paths) != 1:
        holds.append('Reusable scenario checks currently require one existing focused test file; separate the expectations for other files.')
    if len(set(p for s in parsed.scenarios for p in s.source_paths)) > 7:
        holds.append('Select at most seven committed Lua source files for the bounded example checks.')
    if feedback and draft is None:
        # Feedback can change behavior beyond old examples; require a new proposal
        # rather than silently borrowing a previous model draft.
        holds.append('Confirm how the retained review feedback changes the example inputs and expected outcomes: ' + feedback[:400])
    value = BehavioralPreparation(status='hold' if holds else 'review_required', scenarios=parsed.scenarios,
        holds=holds, origin=origin, input_sha256=input_binding(original, feedback, base, paths)).model_dump(mode='json')
    value['review_digest'] = expectation_digest(value)
    return value


def expectation_digest(prepared):
    value = BehavioralPreparation.model_validate(prepared).model_dump(mode='json')
    value.pop('review_digest')
    return digest(value)


def validate_expectation_review(review, payload):
    from fastapi import HTTPException
    prepared = (review.get('task_contract') or {}).get('behavioral_evidence')
    if prepared and prepared['status'] == 'review_required':
        actual = expectation_digest(prepared)
        if prepared['review_digest'] != actual or payload.get('expectation_review') != actual:
            raise HTTPException(422, 'Independently confirm the fixture, operation, inputs, expected outcomes and meaningful incorrect outcomes to reject before approving this scope. Model authorship does not establish their correctness.')
    elif payload.get('expectation_review') is not None:
        raise HTTPException(422, 'There are no complete behavioral examples in this review to confirm.')


def lua(value):
    """Encode data, never Lua expressions. UTF-8 bytes have fixed decimal escapes."""
    if value is None:
        return 'nil'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float)):
        # Lua 5.1 doubles must represent inputs/expectations without rounding.
        if isinstance(value, int) and abs(value) > 2**53:
            raise ValueError('Expected integer is outside exact Lua 5.1 representation')
        return json.dumps(value, allow_nan=False)
    if isinstance(value, str):
        return '"' + ''.join(chr(b) if 32 <= b < 127 and chr(b) not in '\\"' else f'\\{b:03d}' for b in value.encode()) + '"'
    if isinstance(value, list):
        return '{' + ','.join(f'[{i}]={lua(v)}' for i,v in enumerate(value, 1)) + '}'
    if isinstance(value, dict):
        return '{' + ','.join(f'[{lua(k)}]={lua(v)}' for k,v in sorted(value.items())) + '}'
    raise ValueError('Unsupported scenario value')


def access(base, path):
    return base + ''.join('[' + lua(k) + ']' for k in path)


def invocation(base, path, args, method):
    target = access(base, path)
    values = [access(base, path[:-1])] if method else []
    return target + '(' + ','.join([*values, *(lua(v) for v in args)]) + ')'


def addition(scenarios, *, assert_behavior):
    lines = ['\n-- Generated behavioral control: data compiler, not a hand-authored solution.']
    for s in scenarios:
        lines.append('do')
        if s.fixture in ('client', 'loaded-client'):
            lines += ['  dofile(TESTS .. "harness.lua")', f'  local c = MakeClient({lua(s.name)}, {lua(s.addons)})']
            if s.fixture == 'loaded-client':
                relative = s.source_paths[0].removeprefix('!!!TwichUI/')
                lines += [f'  local chunk = assert(loadfile(ROOT .. {lua(relative)}))',
                          '  setfenv(chunk, c)', f'  chunk({lua(s.addons[0])}, {{}})']
        else:
            lines.append(f'  local c = dofile({lua("../"+s.source_paths[0])})')
        for step in s.setup:
            if step.op == 'set':
                lines.append('  ' + access('c', step.path) + ' = ' + lua(step.value))
            else:
                lines.append('  ' + invocation('c', step.path, step.args, step.method))
        lines.append('  local value = ' + invocation('c', s.target, s.args, s.method))
        observed = access('c', s.observe.path) if s.observe.path else 'value'
        if assert_behavior:
            lines.append(f'  assert({observed} == {lua(s.observe.expected)}, {lua("Expected result: "+s.id)})')
        else:
            # A real assertion on the right observation which deliberately accepts
            # the wrong outcome too. Reachability/assertion count is insufficient.
            lines.append(f'  assert({observed} == {lua(s.observe.expected)} or {observed} == {lua(s.incorrect)}, "overly permissive expected behavior")')
        lines.append('end')
    return '\n'.join(lines) + '\n'


def compile_plan(prepared):
    value = BehavioralPreparation.model_validate(prepared)
    if value.status != 'review_required' or value.holds or not value.scenarios:
        raise ValueError('Behavioral preparation held: ' + '; '.join(value.holds or ['Specify a concrete expected observation.']))
    body = Path(__file__).with_name('real_checks').joinpath('regression_scenarios.lua').read_text()
    scenarios = [s.model_dump(mode='json') for s in value.scenarios]
    for s in scenarios:
        s['fault'] = s['incorrect']
        s['args_n'] = len(s['args'])
        for step in s['setup']:
            step['args_n'] = len(step['args'])
    stamp = hashlib.sha256(Path(__file__).read_bytes() + body.encode()).hexdigest()
    oracle = '-- Scenario compiler ' + stamp + '\nlocal scenarios = ' + lua(scenarios) + '\n' + body
    good = addition(value.scenarios, assert_behavior=True)
    if len(good.splitlines()) > 150 or len(oracle.encode()) > 65536:
        raise ValueError('The example setup exceeds the existing control line/byte budget; split the expected behaviors into a smaller task.')
    return dict(oracle=oracle, behavior_id='regression-scenarios',
        read_paths=list(dict.fromkeys([value.scenarios[0].test_path, *(p for s in value.scenarios for p in s.source_paths)])),
        focused_test=value.scenarios[0].test_path,
        additions={'known-good-oracle-control': good,
                   'vacuous': '\nassert(true, "vacuous generated control")\n',
                   'wrong-behavior': addition(value.scenarios, assert_behavior=False)})


def patches(runtime, compiled):
    """Pure diffs from frozen base bytes; no task registration or workspace edits."""
    path = compiled['focused_test']
    before = (runtime.workspace / path).read_text()
    if not before.endswith('\n'):
        raise ValueError('The existing test lacks a final newline; select an explicit safe insertion point before generating additive controls.')
    return {name: ''.join(difflib.unified_diff(before.splitlines(keepends=True),
                (before+text).splitlines(keepends=True), fromfile='a/'+path, tofile='b/'+path))
            for name,text in compiled['additions'].items()}
