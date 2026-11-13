"""Native Claude boundary faults; no subscription requests or owner credentials."""
import copy
import json
import sys

import pytest

from workbench import claude_native as native

MODEL = 'claude-sonnet-5-5'


def events():
    return [dict(type='system', subtype='init', session_id='owned-session', model=MODEL,
                 tools=[], mcp_servers=[], plugins=[], apiKeySource='none'),
            dict(type='assistant', session_id='owned-session', message=dict(model=MODEL,
                 content=[dict(type='text', text='{"ok":true}')], usage=dict(input_tokens=10,
                 cache_creation_input_tokens=3, cache_read_input_tokens=20, output_tokens=2))),
            dict(type='result', session_id='owned-session', subtype='success', is_error=False,
                 result='{"ok":true}', modelUsage={MODEL:{}}, usage=dict(input_tokens=10,
                 cache_creation_input_tokens=3, cache_read_input_tokens=20, output_tokens=4))]


def decode(records):
    response = native.Response(MODEL)
    for event in records:
        response.event(event)
    return response


def test_native_attribution_cache_accounting_and_actual_effort_unknown():
    result = decode(events()).result(0)
    assert result['output'] == {'ok':True}
    assert result['actual_model'] == MODEL and result['actual_effort'] is None
    assert result['usage']['prompt_tokens'] == 33
    assert result['usage']['total_tokens'] == 37


def test_pinned_native_builtin_plugins_do_not_grant_tools_or_user_plugins():
    records=events()
    records[0]['plugins']=[dict(name=name,path='builtin',source=name+'@builtin') for name in native.BUILTINS]
    assert decode(records).result(0)['actual_model']==MODEL
    records[0]['plugins'][0]['path']='/owner/plugin'
    with pytest.raises(native.Held):
        decode(records)


def test_real_interrupt_shape_exit_zero_and_aggregate_zero_are_not_success():
    records = events()
    records[-1].update(subtype='error_during_execution', is_error=True,
                      usage={k:0 for k in records[-1]['usage']})
    with pytest.raises(native.Held) as error:
        decode(records).result(0)
    assert error.value.terminal and error.value.dispatched
    assert error.value.usage['total_tokens'] == 35  # Observed subtotal, not final aggregate.


@pytest.mark.parametrize('change', [
    lambda x:x[0].update(apiKeySource='env'),
    lambda x:x[0].update(tools=['Bash']),
    lambda x:x[0].update(plugins=['foreign']),
    lambda x:x[1].update(session_id='foreign'),
    lambda x:x[1]['message'].update(model='claude-opus-5-5'),
    lambda x:x[1]['message'].update(content=[{'type':'tool_use'}]),
    lambda x:x.append(copy.deepcopy(x[-1])),
])
def test_unsafe_or_foreign_native_session_is_held(change):
    records = events()
    change(records)
    with pytest.raises(native.Held):
        decode(records).result(0)


@pytest.mark.parametrize('change', [
    lambda x:x[-1].update(result='partial {'),
    lambda x:x[-1].update(modelUsage={}),
    lambda x:x[-1]['usage'].update(input_tokens=True),
    lambda x:x.pop(),
])
def test_incomplete_or_unattributed_output_is_never_accepted(change):
    records = events()
    change(records)
    with pytest.raises(native.Held):
        decode(records).result(0)


def test_native_allowance_optional_values_and_billing_change():
    info = dict(unifiedWindows=dict(five_hour=dict(utilization=.45,resetsAt=1234),
                                  seven_day=dict(utilization=None,resetsAt=None)))
    assert native.allowance_event(info) == [
        dict(name='five_hour',used_percent=45.0,resets_at=1234,status='unknown'),
        dict(name='seven_day',used_percent=None,resets_at=None,status='unknown')]
    response = decode(events()[:1])
    with pytest.raises(native.Held,match='billing'):
        response.event(dict(type='rate_limit_event',rate_limit_info=dict(isUsingOverage=True)))


SCREEN = '''Current session
██████                 12% used
Resets 12:30am (UTC)
Current week (all models)
████████               31% used
Resets Nov 18, 12pm (UTC)
Usage credits
Usage credits are off · /usage-credits to turn them on
'''


def test_billing_preflight_requires_explicit_native_off_and_fresh_display():
    snapshot = native.usage_screen(SCREEN)
    assert snapshot['paid_usage'] == 'disabled'
    assert snapshot['windows'][0]['used_percent'] == 12
    assert snapshot['windows'][0]['resets_at'] is None  # Text label isn't an exact timestamp.
    assert native.usage_screen(SCREEN.replace('Usage credits are off','Unlimited')) is None
    assert native.usage_screen(SCREEN+'Showing last-known usage') is None
    assert native.usage_screen(SCREEN+'Refreshing…') is None


def test_bounded_real_child_uses_native_messages_and_never_dispatches_twice():
    script = 'import sys,json; prompt=sys.stdin.read(); print('+repr('\n'.join(json.dumps(e) for e in events()))+')'
    seen=[]
    result = native.turn([sys.executable,'-c',script],'synthetic',MODEL,
                         observe=lambda phase,**data:seen.append(phase))
    assert result['output']=={'ok':True}
    assert seen.count('dispatch')==1 and seen[-1]=='producer-closed'


def test_cancellation_before_dispatch_never_starts_a_child():
    with pytest.raises(native.Held) as error:
        native.turn(['/does/not/exist'],'synthetic',MODEL,lambda:True)
    assert not error.value.dispatched


@pytest.mark.parametrize('model,effort', [('sonnet',None),(MODEL,'ultra'),('claude-fable-5-1','high')])
def test_unsupported_credit_only_or_alias_choices_cannot_dispatch(model,effort):
    with pytest.raises(native.Held):
        native.validate_selection(model,effort)


def test_optional_family_windows_remain_distinct_from_account_wide_limits():
    screen=SCREEN+'\nCurrent week (Opus)\n█████ 100% used\nResets Nov 18, 11:59am (UTC)\n'
    snapshot=native.usage_screen(screen)
    assert snapshot['windows'][-1]['model_family']=='opus'
    assert snapshot['windows'][-1]['used_percent']==100
    event=native.allowance_event(dict(unifiedWindows=dict(seven_day_opus=dict(utilization=1,resetsAt=2000))))
    assert event[0]['model_family']=='opus' and event[0]['used_percent']==100
