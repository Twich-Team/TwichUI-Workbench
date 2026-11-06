"""Workbench-owned model preferences and routing evaluation."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Literal

from fastapi import HTTPException
from pydantic import Field, field_validator, model_validator
from sqlalchemy import select
from workbench.schemas import ScenarioModelSelection, StrictModel
from workbench.db import execution_policies, write_transaction
from workbench.service import now

Backend = Literal['local', 'codex', 'claude']
Condition = Literal['unavailable', 'capability', 'verification', 'capacity', 'unknown', 'stale']


class BackendPreference(StrictModel):
    model: str = Field(default='', max_length=160, pattern=r'^[A-Za-z0-9_.:/-]*$')
    @field_validator('model')
    @classmethod
    def no_credentials(cls, value):
        if value.startswith(('sk-', 'sk_', 'eyJ', 'Bearer')):
            raise ValueError('Use a model identifier, never a credential.')
        return value

    effort: str | None = Field(default=None, max_length=24, pattern=r'^[a-z0-9_-]+$')
    enabled: bool = True
    # Activation is a server-owned boundary, never a credential or editable authorization.
    activated: Literal[False] = False


class ExecutionPolicy(StrictModel):
    revision: int = Field(default=0, ge=0)
    preferred: Backend = 'local'
    fallback: list[Backend] = Field(default_factory=lambda: ['codex'], max_length=2)
    fallback_on: list[Condition] = Field(default_factory=lambda: ['unavailable', 'capability'], max_length=6)
    providers: dict[Backend, BackendPreference] = Field(default_factory=lambda: {
        'local': BackendPreference(), 'codex': BackendPreference(), 'claude': BackendPreference(enabled=False)})
    substitution: Literal['deny', 'configured_fallback_only'] = 'deny'
    test_fallback: Literal['off', 'qwen-to-codex', 'codex-to-qwen', 'both'] | None = Field(
        default=None, exclude_if=lambda value: value is None)
    when_unavailable: Literal['wait', 'block'] = 'wait'

    @model_validator(mode='after')
    def bounded(self):
        if set(self.providers) != {'local', 'codex', 'claude'} or len(set(self.fallback)) != len(self.fallback) or self.preferred in self.fallback or len(set(self.fallback_on)) != len(self.fallback_on):
            raise ValueError('Each backend must occur once; preferred is not a fallback.')
        return self


class ModelCapability(StrictModel):
    model: str = Field(max_length=160, pattern=r'^[A-Za-z0-9_.:/-]+$')
    efforts: list[str] = Field(default_factory=list, max_length=12)


class ExecutorCapability(StrictModel):
    backend: Backend
    executor: str = Field(max_length=80)
    version: str | None = Field(default=None, max_length=80)
    models: list[ModelCapability] = Field(default_factory=list, max_length=64)
    source: str = Field(max_length=200)
    observed_at: str | None = None
    default_model: str = ''
    default_effort: str | None = None
    limitation: str = Field(max_length=600)


class CapabilityReport(StrictModel):
    executors: list[ExecutorCapability] = Field(max_length=3)


def freshness(at, instant=None, ttl=120):
    try:
        parsed = datetime.fromisoformat(at.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            return 'unknown'
        age = ((instant or datetime.now(timezone.utc)) - parsed).total_seconds()
        return 'fresh' if 0 <= age <= ttl else 'stale' if age > ttl else 'unknown'
    except (ValueError, TypeError, AttributeError):
        return 'unknown'


def capabilities(path=None):
    """Only explicitly configured sanitized discovery metadata, never auth/log files."""
    path = path or os.environ.get('WORKBENCH_CAPABILITIES')
    if path:
        try:
            file = Path(path)
            if file.is_absolute() and file.is_file() and file.stat().st_size <= 100_000:
                return CapabilityReport.model_validate(json.loads(file.read_text()))
        except (ValueError, OSError):
            pass
    return CapabilityReport(executors=[ExecutorCapability(backend=b, executor=e, source='No discovery metadata configured',
        limitation=('WB-15: grounded planning and actual decomposition remain unaccepted; 2/2 investigation approaches exhausted. Other local capabilities remain unknown.' if b == 'local' else 'Installed CLI alone does not establish model access, capacity or permission.'))
        for b, e in [('local', 'local llama.cpp'), ('codex', 'Codex CLI'), ('claude', 'Claude Code')]])


def read_connection(connection):
    row = connection.execute(select(execution_policies).order_by(execution_policies.c.revision.desc()).limit(1)).mappings().first()
    return ExecutionPolicy.model_validate(row['payload']) if row else ExecutionPolicy()


def read(engine):
    with engine.connect() as connection:
        return read_connection(connection)


def grounded_local_selection(connection):
    """Pin the saved preference for the only installed grounded planning route."""
    policy = read_connection(connection)
    local = policy.providers['local']
    if not policy.revision or policy.preferred != 'local' or not local.enabled or not local.model:
        raise ValueError('Grounded local planning requires a saved, enabled preferred local model. No fallback will be used.')
    if local.effort is not None:
        raise ValueError('The grounded local adapter uses fixed decoding. Leave local effort unspecified.')
    return ScenarioModelSelection(policy_revision=policy.revision, model=local.model)


def support(report, backend, model, effort, instant=None):
    entry = next((e for e in report.executors if e.backend == backend), None)
    match = next((m for m in entry.models if m.model == model), None) if entry else None
    if not match or freshness(entry.observed_at, instant, ttl=86400) != 'fresh':
        return 'unknown'
    if effort is not None and effort not in match.efforts:
        return 'unsupported'
    return 'verified'


def save(engine, body, report):
    for backend, config in body.providers.items():
        state = support(report, backend, config.model, config.effort)
        if config.effort is not None and state != 'verified':
            raise HTTPException(422, 'Choose an effort verified for this model and executor, or leave effort unspecified. Unknown model identifiers may be saved as not verified.')
    with write_transaction(engine) as connection:
        current = read_connection(connection)
        values = body.model_dump()
        if body.revision == current.revision - 1 and {**values, 'revision': current.revision} == current.model_dump():
            return current
        if body.revision != current.revision:
            raise HTTPException(409, 'Model preferences changed. Your edits are kept. Reload saved preferences before saving again.')
        saved = body.model_copy(update={'revision': current.revision+1})
        connection.execute(execution_policies.insert().values(revision=saved.revision, payload=saved.model_dump(), saved_at=now(), actor='local-owner'))
        return saved


class Observation(StrictModel):
    backend: Backend
    availability: Literal['available', 'unavailable', 'unknown'] = 'unknown'
    observed_at: str | None = None
    capability: Literal['supported', 'limited', 'unknown'] = 'unknown'
    capacity: Literal['available', 'exhausted', 'unknown'] = 'unknown'
    capacity_at: str | None = None
    source: str = 'No supported live observation'


class RoutingInput(StrictModel):
    purpose: Literal['planning', 'decomposition', 'summary', 'implementation', 'other'] = 'planning'
    requires_grounded_planning: bool = True
    permission: bool = False
    holds: list[str] = Field(default_factory=lambda: ['Execution is not authorized in Milestone 4.'], max_length=16)
    attempts: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=3, ge=1, le=3)
    verification_failed: bool = False
    observations: list[Observation] = Field(default_factory=list, max_length=3)


class RoutingDecision(StrictModel):
    state: Literal['selected', 'waiting', 'blocked']
    backend: Backend | None = None
    model: str | None = None
    effort: str | None = None
    reason: str
    policy_revision: int
    purpose: str
    execution_allowed: bool = False
    constraints: list[str]
    evidence: list[str]
    preview: bool = True
    basis: str = 'Saved preferences with unknown live availability'


def evaluate(policy, report, inputs, activated=frozenset(), instant=None):
    """A candidate selection never grants permission. At most three candidates, one pass."""
    constraints = list(inputs.holds)
    if not inputs.permission:
        constraints.append('Permission to execute is held; provider selection cannot release it.')
    evidence = []
    base = dict(policy_revision=policy.revision, purpose=inputs.purpose, constraints=constraints, evidence=evidence)
    if inputs.attempts >= inputs.max_attempts:
        return RoutingDecision(state='blocked', reason='Attempt safeguard reached; explicit review is required.', **base)
    if inputs.verification_failed and 'verification' not in policy.fallback_on:
        return RoutingDecision(state='blocked', reason='Verification failed. This policy does not permit escalation for failed verification.', **base)
    observations = {o.backend:o for o in inputs.observations}
    last_condition = 'unknown'
    last_reason = 'No usable observation.'
    for index, backend in enumerate([policy.preferred, *policy.fallback]):
        if index and (policy.substitution == 'deny' or last_condition not in policy.fallback_on):
            evidence.append('Fallback stopped: substitution or this failure condition is not permitted.')
            break
        config = policy.providers[backend]
        observation = observations.get(backend, Observation(backend=backend))
        state = freshness(observation.observed_at, instant)
        capacity_state = freshness(observation.capacity_at, instant)
        condition = None
        if not config.enabled:
            condition, reason = 'unavailable', 'Saved backend configuration is disabled.'
        elif not config.model:
            condition, reason = 'unknown', 'No actual model identifier configured.'
        elif support(report, backend, config.model, config.effort, instant) != 'verified':
            condition, reason = 'capability', 'Model/effort support is unsupported or not verified.'
        elif state != 'fresh':
            condition, reason = state, 'Availability observation is '+state+'.'
        elif observation.availability != 'available':
            condition, reason = observation.availability, 'Backend availability is '+observation.availability+'.'
        elif inputs.requires_grounded_planning and observation.capability != 'supported':
            condition, reason = 'capability', 'Required grounded planning capability is limited or unknown.'
        elif (backend != 'local' or observation.capacity != 'unknown') and (observation.capacity == 'unknown' or capacity_state != 'fresh'):
            condition, reason = 'stale' if capacity_state == 'stale' else 'unknown', 'Paid capacity is unknown or stale; wait for supported evidence.'
        elif observation.capacity == 'exhausted' and capacity_state == 'fresh':
            condition, reason = 'capacity', 'Waiting for capacity; a fresh observation reports exhaustion.'
        else:
            if backend not in activated:
                constraints.append(f'{backend} execution activation is off. Configuration saved; not activated.')
            evidence.append(f'{backend}: available; availability {state}, capacity {capacity_state}; source: {observation.source}.')
            return RoutingDecision(state='selected', backend=backend, model=config.model, effort=config.effort,
                reason=f'{backend} matches the explicit {inputs.purpose} requirements'+(' after permitted fallback.' if index else ' as preferred backend.'),
                execution_allowed=backend in activated and inputs.permission and not constraints, **base)
        last_condition = condition
        last_reason = f'{backend}: {reason}'
        evidence.append(f'{backend}: {reason} Condition: {condition}; availability {state}, capacity {capacity_state}; source: {observation.source}.')
    return RoutingDecision(state='waiting' if policy.when_unavailable == 'wait' else 'blocked',
        reason='Waiting for a permitted backend. '+last_reason, **base)


CASES = {'saved': 'Saved preferences', 'fallback': 'Local unavailable → Codex fallback', 'capacity': 'Waiting for capacity',
         'disabled': 'Claude disabled', 'stale': 'Stale observations', 'unknown': 'Usage and availability unknown',
         'verification': 'Failed verification', 'limit': 'Attempt safeguard'}


def preview(policy, report, case='saved', purpose='planning'):
    inputs = RoutingInput(purpose=purpose, requires_grounded_planning=purpose in {'planning','decomposition','implementation'})
    active = frozenset()
    if case != 'saved':
        # Explicitly synthetic fixtures: never saved, executed, reserved or counted.
        policy = ExecutionPolicy(revision=policy.revision, substitution='configured_fallback_only',
            fallback=['codex', 'claude'], providers={b:BackendPreference(model='fixture-'+b, enabled=b!='claude') for b in ('local','codex','claude')})
        report = CapabilityReport(executors=[ExecutorCapability(backend=b, executor='Deterministic fixture',
            source='Synthetic routing fixture', observed_at=now(), models=[ModelCapability(model='fixture-'+b)], limitation='Synthetic; no provider inference') for b in ('local','codex','claude')])
        inputs.observations = [Observation(backend=b, availability='available', observed_at=now(), capability='supported', capacity='available', capacity_at=now(), source='Synthetic routing fixture') for b in ('local','codex','claude')]
        active = frozenset({'local','codex','claude'})
        if case in {'fallback','capacity','disabled'}:
            inputs.observations[0].availability = 'unavailable'
        if case == 'capacity':
            inputs.observations[1].capacity = 'exhausted'
        if case == 'disabled':
            policy.preferred, policy.fallback = 'claude', []
        if case == 'stale':
            policy.fallback = ['codex']
            policy.fallback_on = ['stale']
            inputs.observations[0].observed_at = '2000-01-01T00:00:00+00:00'
            inputs.observations[1].capacity_at = '2000-01-01T00:00:00+00:00'
        if case == 'unknown':
            inputs.observations = []
        if case == 'verification':
            inputs.verification_failed = True
        if case == 'limit':
            inputs.attempts = 3
    result = evaluate(policy, report, inputs, active)
    result.basis = 'Synthetic demo case · no real provider observations' if case != 'saved' else result.basis
    result.execution_allowed = False
    return result


class ModelOverview(StrictModel):
    policy: ExecutionPolicy
    capabilities: CapabilityReport
    acknowledgment: str
    acknowledged_revision: int | None = None
    observations: list[Observation]
    routing: RoutingDecision
    cases: dict[str, str]
    usage_message: str
    allocation_message: str


def overview(engine, report):
    policy = read(engine)
    return ModelOverview(policy=policy, capabilities=report,
        acknowledgment='Configuration saved; not activated for implementation. Grounded local planning records its saved model selection per attempt when separately configured.',
        observations=[Observation(backend=b) for b in ('local','codex','claude')],
        routing=preview(policy, report), cases=CASES,
        usage_message='Usage unavailable. No supported provider account allowance/window/reset telemetry is integrated. Workbench activity excludes outside usage. Tokens and request counts are not subscription percentages.',
        allocation_message='Automation allocation, personal reserve and pacing require actual provider windows. No enforceable quota percentage or cost cap is offered. Existing planning safeguards: three attempts per job, one serial worker, coordinated local model lock. These are not subscription limits. Implementation routing and adaptive pacing remain deferred.')
