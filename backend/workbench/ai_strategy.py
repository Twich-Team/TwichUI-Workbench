"""Ordered selection for the existing provider routes; no queue or executor.

Percentages are provider-specific request-start cutoffs. The connector supplies
fresh native observations and serialized stage consumption before each dispatch.
Selection cannot confer task, disclosure, execution or billing permission.
"""
from typing import Literal
import math

from pydantic import Field, model_validator
from workbench.schemas import StrictModel

Provider = Literal['local', 'codex', 'claude']
Reason = Literal['allocation', 'provider_limit', 'unavailable', 'authentication', 'unsuitable', 'unknown']


class Stage(StrictModel):
    id: str = Field(pattern=r'^[a-zA-Z0-9-]{1,64}$')
    provider: Provider
    model: str = Field(min_length=1, max_length=160, pattern=r'^[A-Za-z0-9_.:/-]+$')
    effort: str | None = Field(default=None, max_length=24, pattern=r'^[a-z0-9_-]+$')
    allocation: float | None = Field(default=None, gt=0, le=100, allow_inf_nan=False)
    role: Literal['primary', 'fallback'] = 'primary'
    triggers: list[Reason] = Field(default_factory=lambda:['allocation','provider_limit','unavailable','unsuitable'], max_length=6)
    on_limit: Literal['switch', 'wait', 'wait_then_switch'] = 'switch'
    wait_seconds: int = Field(default=1800, ge=30, le=604800)
    local_suitability: Literal['simple_only', 'reviewed_local_work'] = 'simple_only'

    @model_validator(mode='after')
    def valid(self):
        if self.model.startswith(('sk-', 'sk_', 'eyJ', 'Bearer')):
            raise ValueError('Use a model identifier, never a credential.')
        if self.provider == 'local':
            if self.allocation is not None or self.effort is not None:
                raise ValueError('Local Qwen uses resource checks, without subscription percentages or effort.')
        elif self.allocation is None:
            raise ValueError('Choose a provider-specific allocation for each subscription stage.')
        if len(self.triggers) != len(set(self.triggers)) or self.role == 'fallback' and not self.triggers:
            raise ValueError('Choose distinct fallback triggers.')
        return self


class Strategy(StrictModel):
    name: str = Field(default='My AI strategy', min_length=1, max_length=80)
    stages: list[Stage] = Field(min_length=1, max_length=16)
    ceiling: Literal['request_start', 'strict'] = 'request_start'
    unknown_usage: Literal['wait', 'switch', 'limited_requests'] = 'wait'
    unknown_request_limit: int = Field(default=0, ge=0, le=100)
    # Missing reset data never creates a reset. Poll at this owner-selected delay.
    recheck_seconds: int = Field(default=300, ge=30, le=86400)

    @model_validator(mode='after')
    def valid(self):
        if len({s.id for s in self.stages}) != len(self.stages):
            raise ValueError('Every strategy stage needs a distinct identity.')
        if self.stages[0].role != 'primary':
            raise ValueError('The first stage must be primary work, not fallback reserve.')
        for provider in ('codex', 'claude'):
            if sum(s.allocation or 0 for s in self.stages if s.provider==provider)>100:
                raise ValueError('Primary plus fallback allocation cannot exceed 100% for '+provider+'.')
        if self.unknown_usage == 'limited_requests' and not self.unknown_request_limit:
            raise ValueError('Choose an explicit conservative request limit for unknown usage.')
        return self


class Window(StrictModel):
    name: str = Field(max_length=80)
    used_percent: float | None = Field(default=None,ge=0,le=100,allow_inf_nan=False)
    resets_at: float | None = Field(default=None,gt=0,allow_inf_nan=False)
    reset_label: str | None = Field(default=None,max_length=160)
    # A model-family window is relevant only for the named family.
    model_family: str | None = Field(default=None,max_length=40)


class Observation(StrictModel):
    provider: Provider
    state: Literal['available','unavailable','authentication','provider_limit','unknown'] = 'unknown'
    observed_at: float | None = Field(default=None,ge=0,allow_inf_nan=False)
    source: str = Field(default='No supported native observation',max_length=200)
    paid_usage: Literal['disabled','enabled','unknown'] = 'unknown'
    windows: list[Window] = Field(default_factory=list,max_length=8)


class StageConsumption(StrictModel):
    """Connector-owned observed deltas; includes outside activity conservatively.

    A missing final reading remains unknown. No token/percentage conversion.
    Old windows remain historical; only matching current reset identities count.
    """
    stage_id: str
    requests: int = Field(default=0,ge=0)
    unknown_requests: int = Field(default=0,ge=0)
    windows: dict[str, dict[str,float | None]] = Field(default_factory=dict)


class Decision(StrictModel):
    state: Literal['selected','waiting','unavailable']
    stage_id: str | None = None
    provider: Provider | None = None
    model: str | None = None
    effort: str | None = None
    role: Literal['primary','fallback'] | None = None
    reason: str
    condition: Reason | None = None
    recheck_at: float | None = None
    provider_reset_at: float | None = None
    cutoff: float | None = None
    unknown_usage: bool = False
    # Always false. Existing native and task owners authorize actual dispatch.
    grants_permission: Literal[False] = False


NAMES = dict(local='Local Qwen',codex='Codex',claude='Claude')


def evaluate(strategy, observations, consumption, *, now, failures=None,
             waiting_since=None, suitable_local=False, reviewed_local=False, manual_stage=None, requested_stage=None):
    """Deterministic request-start decision, also used at the dispatch boundary.

    Manual authorization bypasses only Workbench allocation/strict/unknown caps,
    never native availability, provider limits, billing or model suitability.
    """
    strategy = Strategy.model_validate(strategy)
    if not math.isfinite(now):
        raise ValueError('A finite current time is required.')
    observed = {o.provider:o for o in map(Observation.model_validate,observations)}
    spent = {s.stage_id:s for s in map(StageConsumption.model_validate,consumption)}
    failures, waiting_since = failures or {}, waiting_since or {}
    cumulative = dict(local=0.,codex=0.,claude=0.)
    prior_conditions = set(failures.values())
    first_wait = None
    for index, stage in enumerate(strategy.stages):
        cumulative[stage.provider] += stage.allocation or 0
        cutoff = cumulative[stage.provider] if stage.provider != 'local' else None
        if manual_stage is not None and stage.id != manual_stage:
            continue
        label = NAMES[stage.provider]
        base = dict(stage_id=stage.id,provider=stage.provider,model=stage.model,
                    effort=stage.effort,role=stage.role,cutoff=cutoff)
        if stage.role == 'fallback' and manual_stage is None and not prior_conditions.intersection(stage.triggers):
            continue
        condition = failures.get(stage.id)
        reason = None
        reset = None
        unknown = False
        obs = observed.get(stage.provider,Observation(provider=stage.provider))
        age = now-obs.observed_at if obs.observed_at is not None else None
        if condition:
            reason = label+' could not finish the previous safely settled request.'
        elif stage.provider == 'local' and not (suitable_local or reviewed_local and stage.local_suitability=='reviewed_local_work'):
            condition,reason = 'unsuitable','This task needs a model suited to complex coding.'
        elif age is None or age<0 or age>120:
            condition,reason = 'unknown',label+' usage or availability needs a fresh supported reading.'
        elif obs.state != 'available':
            condition = obs.state if obs.state in {'authentication','provider_limit','unavailable'} else 'unknown'
            reason = label+(' sign-in needs attention.' if condition=='authentication' else ' is currently unavailable.')
        elif stage.provider != 'local' and obs.paid_usage != 'disabled':
            condition,reason = 'unavailable',label+' included-only billing could not be verified.'
        elif strategy.ceiling=='strict' and stage.provider!='local' and manual_stage is None:
            condition,reason = 'unknown','Strict ceiling: '+label+' cannot bound the allowance consumed by a request.'
        else:
            relevant = [w for w in obs.windows if w.model_family is None or w.model_family in stage.model]
            relevant_resets = [w.resets_at for w in relevant if w.resets_at is not None and w.resets_at>now]
            # Reset times are a reason to recheck, never proof of renewed capacity.
            reset = max(relevant_resets) if relevant_resets else None
            role_budget=spent.get('@'+stage.provider+':'+stage.role,StageConsumption(stage_id=stage.id))
            budget=spent.get(stage.id,StageConsumption(stage_id=stage.id))
            unknown_delta=any(record.windows.get(w.name,{}).get('reset')==w.resets_at and
                'consumed' in record.windows.get(w.name,{}) and record.windows[w.name]['consumed'] is None
                for record in (budget,role_budget) for w in relevant)
            unknown = (stage.provider!='local' and (unknown_delta or not relevant or any(w.used_percent is None
                       or w.resets_at is None or w.resets_at<=now for w in relevant)))
            if stage.provider!='local' and any(w.used_percent is not None and w.used_percent>=100 for w in relevant):
                condition,reason='provider_limit','Waiting for '+label+' usage to reset.'
                exhausted=[w.resets_at for w in relevant if w.used_percent is not None and w.used_percent>=100]
                reset=max(exhausted) if exhausted and all(t is not None and t>now for t in exhausted) else None
            elif unknown and manual_stage is None and not (
                    strategy.unknown_usage=='limited_requests' and
                    max(budget.unknown_requests,role_budget.unknown_requests) < strategy.unknown_request_limit):
                condition,reason='unknown',label+' allowance or reset time is unknown. '+(
                    'The conservative request limit is spent.' if strategy.unknown_usage=='limited_requests' else 'Following your unknown-usage policy.')
            elif manual_stage is None and stage.provider!='local':
                budget = spent.get(stage.id,StageConsumption(stage_id=stage.id))
                blocked_resets=[]
                for window in relevant:
                    role_record=role_budget.windows.get(window.name,{})
                    role_limit=sum(s.allocation or 0 for s in strategy.stages if s.provider==stage.provider and s.role==stage.role)
                    role_delta=role_record.get('consumed') if role_record.get('reset')==window.resets_at else 0
                    record = budget.windows.get(window.name,{})
                    # Unknown stage consumption cannot be replaced with zero.
                    delta = record.get('consumed') if record.get('reset')==window.resets_at else 0
                    if (window.used_percent is not None and window.used_percent>=cutoff
                            or delta is not None and delta>=stage.allocation
                            or role_delta is not None and role_delta>=role_limit
                            or record and record.get('reset')==window.resets_at and delta is None
                               and not (strategy.unknown_usage=='limited_requests' and
                                   budget.unknown_requests<strategy.unknown_request_limit)):
                        condition,reason='allocation',label+' has reached this '+stage.role+' allocation.'
                        blocked_resets.append(window.resets_at)
                if blocked_resets:
                    reset=max(blocked_resets) if all(t is not None and t>now for t in blocked_resets) else None
        if not condition and requested_stage is not None and stage.id!=requested_stage:
            continue
        if not condition:
            return Decision(state='selected',reason=((first_wait.reason+' ' if first_wait else '')+('Manual choice authorized for this request. ' if manual_stage else
                'Using your '+stage.role+' strategy stage. ')+label+' is eligible to start; a running request may finish above its cutoff.'),
                unknown_usage=unknown,**base)
        prior_conditions.add(condition)
        if requested_stage is not None and stage.id!=requested_stage:
            continue
        wait=Decision(state='waiting',reason=reason,condition=condition,recheck_at=now+strategy.recheck_seconds,
                      provider_reset_at=reset,unknown_usage=unknown,**base)
        if reset is not None and condition in {'allocation','provider_limit'}:
            wait.recheck_at=reset
        if first_wait is None or (first_wait.condition=='unsuitable' and condition!='unsuitable') or (condition!='unsuitable' and wait.recheck_at<first_wait.recheck_at):
            first_wait=wait
        mode = 'switch' if condition=='unsuitable' else stage.on_limit
        if condition=='unknown' and strategy.unknown_usage=='switch':
            mode='switch'
        elif condition=='unknown' and strategy.unknown_usage=='wait' and not reason.startswith('Strict ceiling:'):
            mode='wait'
        if mode=='wait':
            return wait
        if mode=='wait_then_switch':
            since=waiting_since.get(stage.id)
            if since is None or now<since+stage.wait_seconds:
                wait.recheck_at=min(wait.recheck_at, (now if since is None else since)+stage.wait_seconds)
                return wait
    return first_wait or Decision(state='unavailable',reason='All configured AI options are currently unavailable.')
