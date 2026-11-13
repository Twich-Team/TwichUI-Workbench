import pytest
from pydantic import ValidationError

from workbench import ai_strategy as ai

NOW=1000.


def strategy(**changes):
    return ai.Strategy.model_validate(dict(stages=[
        dict(id='primary',provider='codex',model='gpt-6.1-sol',effort='high',allocation=50),
        dict(id='local',provider='local',model='pilot-qwen38-27b'),
        dict(id='reserve',provider='codex',model='gpt-6.1-sol',effort='high',allocation=25,
             role='fallback',triggers=['unavailable','unsuitable'])],**changes))


def observations(codex=0,claude=0,weekly=0):
    return [ai.Observation(provider=provider,state='available',observed_at=NOW,paid_usage='disabled',
        windows=[] if provider=='local' else [ai.Window(name='short',used_percent=amount,resets_at=2000),
        ai.Window(name='week',used_percent=weekly,resets_at=9000)])
        for provider,amount in [('local',0),('codex',codex),('claude',claude)]]


def decide(s=None,o=None,usage=None,**kwargs):
    return ai.evaluate(s or strategy(),o or observations(),usage or [],now=NOW,**kwargs)


def test_cumulative_budget_fallback_reserve_and_unallocated_capacity():
    assert decide().stage_id=='primary'
    assert decide(o=observations(50),suitable_local=True).stage_id=='local'
    fallback=decide(o=observations(50))
    assert fallback.stage_id=='reserve' and fallback.cutoff==75 and fallback.role=='fallback'
    held=decide(o=observations(75))
    assert held.state=='waiting' and held.condition=='allocation'


def test_primary_cannot_spend_reserve_without_its_trigger():
    s=strategy()
    s.stages[2].triggers=['unavailable']
    assert decide(s=s,o=observations(50)).state=='waiting'
    assert decide(s=s,o=observations(50),failures={'local':'unavailable'}).stage_id=='reserve'


def test_reserve_delta_cannot_borrow_unused_primary_allocation():
    usage=[dict(stage_id='reserve',windows=dict(short=dict(reset=2000,consumed=25)))]
    # The primary itself was unavailable below its account cutoff. Reserve is
    # still limited to its independent 25 points, not the entire remaining 75.
    assert decide(o=observations(30),usage=usage,failures={'primary':'unavailable'}).state=='waiting'


def test_provider_allocations_are_not_shares_of_a_combined_budget():
    s=ai.Strategy(stages=[ai.Stage(id='codex',provider='codex',model='gpt-6.1-sol',allocation=75),
                          ai.Stage(id='claude',provider='claude',model='claude-sonnet-5-5',allocation=80)])
    assert decide(s=s,o=observations(75,79)).provider=='claude'
    assert decide(s=s,o=observations(74,99)).provider=='codex'


def test_weekly_limit_does_not_reset_when_short_window_resets():
    result=decide(o=observations(0,weekly=100))
    assert result.state=='waiting' and result.condition=='provider_limit'
    assert result.provider_reset_at==9000


def test_outside_usage_counts_against_available_headroom():
    assert decide(o=observations(49)).stage_id=='primary'
    assert decide(o=observations(51),suitable_local=True).provider=='local'


def test_no_automatic_complex_task_downgrade_to_local():
    result=decide(o=observations(50))
    assert result.provider=='codex' and result.stage_id=='reserve'
    assert decide(o=observations(75)).state=='waiting'


def test_unknown_stale_or_expired_readings_are_not_zero():
    s=strategy()
    s.stages[0].on_limit='wait'
    for value in [None,800,1001]:
        obs=observations()
        obs[1].observed_at=value
        result=decide(s=s,o=obs)
        assert result.state=='waiting' and result.condition=='unknown'
    obs=observations()
    obs[1].windows[0].resets_at=None
    result=decide(s=s,o=obs)
    assert result.state=='waiting' and result.unknown_usage


def test_conservative_unknown_request_limit_is_explicit_and_consumable():
    s=strategy(unknown_usage='limited_requests',unknown_request_limit=1)
    obs=observations()
    obs[1].windows[0].resets_at=None
    assert decide(s=s,o=obs).state=='selected'
    assert decide(s=s,o=obs,usage=[dict(stage_id='primary',unknown_requests=1)]).stage_id=='reserve'


def test_wait_then_switch_uses_durable_wait_start_not_a_new_timer_each_check():
    s=strategy()
    s.stages[0].on_limit='wait_then_switch'
    s.stages[0].wait_seconds=60
    obs=observations(50)
    assert decide(s=s,o=obs,waiting_since={'primary':970}).stage_id=='primary'
    assert decide(s=s,o=obs,waiting_since={'primary':940},suitable_local=True).stage_id=='local'


def test_manual_choice_bypasses_only_workbench_caps_and_never_billing_or_provider_limits():
    s=strategy(ceiling='strict')
    assert decide(s=s).state=='waiting'
    assert decide(s=s,o=observations(99),manual_stage='primary').state=='selected'
    assert decide(s=s,o=observations(100),manual_stage='primary').state=='waiting'
    obs=observations()
    obs[1].paid_usage='enabled'
    assert decide(s=s,o=obs,manual_stage='primary').state=='waiting'
    obs[1].paid_usage='disabled'
    obs[1].state='authentication'
    assert decide(s=s,o=obs,manual_stage='primary').condition=='authentication'


@pytest.mark.parametrize('change', [
    lambda s:s['stages'][2].update(allocation=51),
    lambda s:s['stages'][1].update(effort='high'),
    lambda s:s['stages'][1].update(allocation=25),
    lambda s:s['stages'][0].update(role='fallback'),
    lambda s:s['stages'][2].update(id='primary'),
    lambda s:s.update(unknown_usage='limited_requests'),
    lambda s:s['stages'][0].update(model='sk-private-key'),
])
def test_impossible_strategies_are_rejected(change):
    data=strategy().model_dump()
    change(data)
    with pytest.raises(ValidationError):
        ai.Strategy.model_validate(data)


def test_unknown_wait_choice_holds_even_when_another_subscription_is_available():
    s=ai.Strategy(stages=[ai.Stage(id='one',provider='codex',model='gpt-6.1-sol',allocation=50),
        ai.Stage(id='two',provider='claude',model='claude-sonnet-5-5',allocation=50)])
    obs=observations()
    obs[1].windows[0].resets_at=None
    assert decide(s=s,o=obs).stage_id=='one'
    s.unknown_usage='switch'
    assert decide(s=s,o=obs).stage_id=='two'
