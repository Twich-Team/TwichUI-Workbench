"""Bounded data for a reviewed, independently executed Lua bug reproducer."""
import hashlib
import re
from pathlib import Path
from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import Field, StrictInt, StrictStr, ValidationError, model_validator

from workbench.regression_evidence import Data, FORBIDDEN, LuaPath, Scalar, TestPath, lua
from workbench.visual_scope import digest

Key = Annotated[StrictStr, Field(min_length=1, max_length=80)] | Annotated[StrictInt, Field(ge=1, le=512)]
Access = Annotated[list[Key], Field(max_length=8)]
Short = Annotated[str, Field(min_length=1, max_length=450)]


class FixtureField(Data):
    path: Access
    value: Scalar


class ArgumentReference(Data):
    ref: Annotated[Access, Field(min_length=1)]


class Step(Data):
    # No free-form program, loader path, or callable supplied by the model.
    op: Literal['load', 'table', 'set', 'stub', 'capture', 'call']
    path: Access = []
    source: LuaPath | None = None
    value: Scalar = None
    args: Annotated[list[Scalar | ArgumentReference], Field(max_length=8)] = []
    fields: Annotated[list[FixtureField], Field(max_length=16)] = []
    method: bool = False

    @model_validator(mode='after')
    def shape(self):
        if self.op == 'load':
            if not self.source or self.path:
                raise ValueError('A load step needs one selected source and no access path')
        elif self.source is not None or (not self.path and self.op != 'call'):
            raise ValueError('A fixture step needs an access path and no source')
        if ((self.fields and self.op not in {'stub', 'capture'})
                or (self.value is not None and self.op != 'set')
                or (self.args and self.op not in {'stub', 'capture', 'call'})
                or (self.method and self.op not in {'capture', 'call'})
                or (self.fields and self.args)
                or (self.op != 'call' and any(isinstance(a, ArgumentReference) for a in self.args))
                or any(not f.path for f in self.fields)):
            raise ValueError('Unexpected fixture step data')
        return self


class Observation(Data):
    after_step: int = Field(ge=1, le=24)
    source: Literal['field', 'return', 'captured']
    path: Annotated[Access, Field(min_length=1)]
    expected: Scalar
    incorrect: Scalar


class ClientBootstrap(Data):
    through_line: int = Field(ge=1, le=200)
    client: Annotated[str, Field(pattern=r'^[A-Za-z_][A-Za-z0-9_]{0,39}$')]
    test_path: TestPath | None = None


class Reproducer(Data):
    description: Short
    fixture: Literal['module', 'client']
    bootstrap: ClientBootstrap | None = None
    steps: Annotated[list[Step], Field(min_length=1, max_length=24)]
    observations: Annotated[list[Observation], Field(min_length=1, max_length=4)]

    @model_validator(mode='after')
    def validate_reproducer(self):
        paths = [*(o.path for o in self.observations), *(s.path for s in self.steps),
                 *(f.path for s in self.steps for f in s.fields),
                 *(a.ref for s in self.steps for a in s.args if isinstance(a, ArgumentReference))]
        if any(isinstance(k, str) and (k in FORBIDDEN or k.startswith('__')) for p in paths for k in p):
            raise ValueError('Unsafe fixture access path')
        loads = [s.source for s in self.steps if s.op == 'load']
        if ((not loads and not self.bootstrap) or len(loads) != len(set(loads)) or len(loads) > 6
                or self.bootstrap and self.fixture != 'client'
                or any('..' in Path(path).parts for path in loads)):
            raise ValueError('Select one to six distinct existing Lua sources')
        if (not any(s.op == 'call' for s in self.steps)
                or self.fixture == 'module' and (len(loads) != 1 or self.steps[0].op != 'load')
                or len(self.model_dump_json().encode()) > 8200):
            raise ValueError('Specify an operation and a bounded observable failure')
        for index, item in enumerate(self.observations):
            constraints = [
                (item.after_step <= len(self.steps), 'after_step', item.after_step,
                 'Observation step must name an existing listed action'),
                (item.expected != item.incorrect, 'incorrect', item.incorrect,
                 'Incorrect result must differ from the expected scalar result'),
                (item.source not in {'return', 'captured'} or type(item.path[0]) is int, 'path', item.path,
                 'Return and captured paths must start with a positional result number'),
                (item.source != 'captured' or any(s.op == 'capture' for s in self.steps[:item.after_step]),
                 'source', item.source, 'Captured observation requires an earlier explicit capture'),
                (item.source != 'return' or item.after_step > len(self.steps)
                 or self.steps[item.after_step-1].op == 'call', 'after_step', item.after_step,
                 'Return observation must name a call action'),
            ]
            for valid, field, value, rule in constraints:
                if not valid:
                    raise ValidationError.from_exception_data(type(self).__name__, [dict(
                        type='value_error', loc=('observations', index, field), input=value,
                        ctx=dict(error=ValueError(rule)))])
        return self


class BugPlan(Data):
    scope: Short
    read_paths: Annotated[list[LuaPath | TestPath], Field(min_length=2, max_length=12)]
    writable_paths: Annotated[list[LuaPath | TestPath], Field(min_length=1, max_length=3)]
    focused_test: TestPath
    reproducer: Reproducer


class ReadWindow(Data):
    path: LuaPath | TestPath
    start: int = Field(ge=1)
    end: int = Field(ge=1)


class InvestigationRead(ReadWindow):
    path: Annotated[str, Field(pattern=r'^(?:(?:!!!TwichUI|TwichUI_Settings)/[A-Za-z0-9_./!-]+|tests/[A-Za-z0-9_./-]+)\.lua$', max_length=240)]
    @model_validator(mode='after')
    def bounded(self):
        if self.start > self.end:
            raise ValueError('Source range start must not exceed its end')
        return self


class InvestigationSearch(Data):
    query: Annotated[StrictStr, Field(min_length=3, max_length=80)]

    @model_validator(mode='after')
    def literal(self):
        if len(self.query.strip()) < 3 or '\n' in self.query or '\r' in self.query:
            raise ValueError('Use one bounded literal source search')
        return self


class BugPlanningResponse(Data):
    finding: Short
    plan: BugPlan | None
    question: Short | None
    limitation: Short | None
    inspection: Annotated[list[InvestigationRead | InvestigationSearch], Field(min_length=1, max_length=3)] | None = None

    @model_validator(mode='after')
    def one_outcome(self):
        if sum(value is not None for value in (self.plan, self.question, self.limitation, self.inspection)) != 1:
            raise ValueError('Return exactly one of a plan, clarification, unsupported finding, or source inspection')
        return self


def validation_diagnostics(error):
    """Keep bounded schema locations, never provider values, keys or messages."""
    fields = set().union(*(model.model_fields for model in (
        BugPlanningResponse, BugPlan, Reproducer, ClientBootstrap, Step,
        Observation, FixtureField,
        InvestigationRead, InvestigationSearch,
    )))
    rows = []
    for issue in error.errors(include_input=False, include_context=False, include_url=False)[:4]:
        location = '.'.join(
            str(part) if type(part) is int and 0 <= part <= 512 else
            part if type(part) is str and part in fields else 'unrecognized'
            for part in issue['loc'][:8]
        ) or 'response'
        kind = issue['type']
        kind = kind if re.fullmatch(r'[a-z_]{1,64}', kind) else 'validation_error'
        rows.append(f'{location}:{kind}')
    return dict(validation_errors=error.error_count(),
                validation_locations='; '.join(rows)[:600],
                validation_schema_sha256=digest(BugPlanningResponse.model_json_schema()))


class BugEvidence(Data):
    bootstrap_source: Annotated[str, Field(max_length=16032)] = ''
    windows: Annotated[list[ReadWindow], Field(min_length=4, max_length=4)]
    plan: BugPlan
    review_digest: str


def review_digest(plan, bootstrap_source):
    return digest(dict(kind='offline-lua-reproducer-v1', plan=plan, bootstrap_source=bootstrap_source))


def committed_bootstrap(root, base, plan):
    """Freeze a bounded prefix of the existing focused test."""
    spec = plan['reproducer'].get('bootstrap')
    if not spec:
        return ''
    from workbench import regression_preparation as source

    path = spec.get('test_path') or plan['focused_test']
    lines = source.git(root, 'show', f'{base}:{path}').decode().splitlines(keepends=True)
    if spec['through_line'] > len(lines):
        raise ValueError('The reviewed client setup exceeds the committed test.')
    prefix = ''.join(lines[:spec['through_line']])
    client = spec['client']
    if (len(prefix.encode()) > 16000 or not re.search(
            rf'^local\s+{re.escape(client)}\s*=\s*MakeClient\s*\(', prefix, re.MULTILINE)):
        raise ValueError('Select a bounded committed test setup with an existing client variable.')
    return prefix + '\nreturn ' + client + '\n'


def validate_expectation_review(review, payload):
    from workbench.regression_evidence import validate_expectation_review as validate_regression

    evidence = (review.get('task_contract') or {}).get('bug_evidence')
    if not evidence:
        return validate_regression(review, payload)
    checked = BugEvidence.model_validate(evidence)
    actual = review_digest(checked.plan.model_dump(mode='json'), checked.bootstrap_source)
    if checked.review_digest != actual or payload.get('expectation_review') != actual:
        raise HTTPException(422, 'Confirm that the reproduction and expected behavior match your report before approving this plan.')


def compile_plan(evidence):
    checked = BugEvidence.model_validate(evidence)
    plan = checked.plan.model_dump(mode='json')
    if checked.review_digest != review_digest(plan, checked.bootstrap_source):
        raise ValueError('The reviewed reproducer changed')
    body = Path(__file__).with_name('real_checks').joinpath('bug_reproducer.lua').read_text()
    frame_events = Path(__file__).with_name('real_checks').joinpath('bug_frame_events.lua').read_text()
    assert body.count('-- WB_FRAME_EVENT_HELPER') == 1
    body = body.replace('-- WB_FRAME_EVENT_HELPER', frame_events)
    data = plan['reproducer']
    data['bootstrap_source'] = checked.bootstrap_source
    for step in data['steps']:
        step['args_n'] = len(step['args'])
    source = lua(data)
    stamp = hashlib.sha256(Path(__file__).read_bytes() + body.encode()).hexdigest()
    oracle = '-- Bug reproducer ' + stamp + '\nlocal scenario = ' + source + '\n' + body
    if len(oracle.encode()) > 65536:
        raise ValueError('The reproduction exceeds the independent check budget')
    return dict(oracle=oracle, behavior_id='bug-reproducer', read_paths=plan['read_paths'],
                focused_test=plan['focused_test'])
