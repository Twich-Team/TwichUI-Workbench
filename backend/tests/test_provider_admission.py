from concurrent.futures import ThreadPoolExecutor
import json
import threading
from uuid import uuid4

import pytest

from workbench import ai_strategy as ai, provider_admission as admission
from workbench.claude_native import Held


def strategy():
    return ai.Strategy(stages=[ai.Stage(id='primary',provider='codex',model='gpt-6.1-sol',allocation=50)])


def snapshot(used):
    return ai.Observation(provider='codex',state='available',observed_at=1000,paid_usage='disabled',
        source='Controlled native boundary',windows=[ai.Window(name='short',used_percent=used,resets_at=2000)])


def test_actual_admission_path_rechecks_after_concurrent_request_and_records_overrun(tmp_path):
    tmp_path.chmod(0o700)
    counter={'used':49,'calls':0}
    start=threading.Barrier(2)

    def dispatch(stage):
        counter['calls']+=1
        counter['used']=52
        return {'actual_model':stage.model},snapshot(counter['used'])

    def run():
        start.wait()
        return admission.run(tmp_path,str(uuid4()),strategy(),
            observations=lambda:[snapshot(counter['used'])],dispatch=dispatch,now=lambda:1000)

    with ThreadPoolExecutor(2) as executor:
        results=list(executor.map(lambda _:run(),range(2)))
    assert sorted(r['state'] for r in results)==['completed','waiting']
    assert counter['calls']==1
    completed=next(r for r in results if r['state']=='completed')
    assert completed['admission']['overrun']==[dict(window='short',used_percent=52.,cutoff=50.)]


def test_duplicate_native_identity_never_dispatches_twice_after_restart(tmp_path):
    tmp_path.chmod(0o700)
    identity=str(uuid4())
    calls=[]

    def dispatch(stage):
        calls.append(stage.id)
        return {'ok':True},snapshot(20)

    for _ in range(2):
        result=admission.run(tmp_path,identity,strategy(),observations=lambda:[snapshot(10)],
                             dispatch=dispatch,now=lambda:1000)
    assert result['state']=='held' and calls==['primary']


def test_uncertain_dispatch_keeps_reservation_and_never_replays(tmp_path):
    tmp_path.chmod(0o700)

    def dispatch(stage):
        raise Held('timeout',dispatched=True)

    with pytest.raises(Held):
        admission.run(tmp_path,str(uuid4()),strategy(),observations=lambda:[snapshot(10)],
                      dispatch=dispatch,now=lambda:1000)
    result=admission.run(tmp_path,str(uuid4()),strategy(),observations=lambda:[snapshot(10)],
                         dispatch=lambda _:pytest.fail('Uncertain request replayed'),now=lambda:1000)
    assert result['state']=='held'
    assert json.loads((tmp_path/'allowances.json').read_text())['active']


def test_missing_final_window_reading_never_becomes_zero_consumption(tmp_path):
    tmp_path.chmod(0o700)
    admission.run(tmp_path,str(uuid4()),strategy(),observations=lambda:[snapshot(10)],
                  dispatch=lambda stage:({'ok':True},None),now=lambda:1000)
    result=admission.run(tmp_path,str(uuid4()),strategy(),observations=lambda:[snapshot(11)],
                         dispatch=lambda _:pytest.fail('Unknown consumption spent as zero'),now=lambda:1000)
    assert result['state']=='waiting'


def test_reset_uses_fresh_native_window_and_does_not_reconstruct_old_usage(tmp_path):
    tmp_path.chmod(0o700)
    admission.run(tmp_path,str(uuid4()),strategy(),observations=lambda:[snapshot(49)],
                  dispatch=lambda stage:({'ok':True},snapshot(55)),now=lambda:1000)
    fresh=snapshot(0)
    fresh.windows[0].resets_at=3000
    assert admission.run(tmp_path,str(uuid4()),strategy(),observations=lambda:[fresh],
        dispatch=lambda stage:({'ok':True},fresh),now=lambda:1000)['state']=='completed'


def test_conservative_external_usage_is_included_in_stage_reserve_delta():
    before,after=snapshot(10),snapshot(40)
    assert admission.observed_delta(before,after,{})['short']['consumed']==30


def test_native_non_dispatch_releases_only_its_unused_reservation(tmp_path):
    tmp_path.chmod(0o700)
    def refuse(stage):
        raise Held('authentication',dispatched=False)
    with pytest.raises(Held):
        admission.run(tmp_path,str(uuid4()),strategy(),observations=lambda:[snapshot(10)],
                      dispatch=refuse,now=lambda:1000)
    state=json.loads((tmp_path/'allowances.json').read_text())
    assert state['active'] is None
    assert next(iter(state['strategies'].values()))['primary']['requests']==0


def test_decreasing_or_conflicting_native_percentages_are_unknown_consumption():
    assert admission.observed_delta(snapshot(31),snapshot(30),{})['short']['consumed'] is None


def test_frozen_model_variants_and_reordered_cards_do_not_replenish_fallback_reserve(tmp_path):
    tmp_path.chmod(0o700)
    first=ai.Strategy(stages=[ai.Stage(id='other',provider='claude',model='claude-sonnet-5-5',allocation=50),
        ai.Stage(id='reserve',provider='codex',model='gpt-6.1-sol',allocation=25,role='fallback')])
    absent=ai.Observation(provider='claude',state='unavailable',observed_at=1000)
    admission.run(tmp_path,str(uuid4()),first,observations=lambda:[absent,snapshot(0)],
        dispatch=lambda s:({'ok':True},snapshot(25)),now=lambda:1000)
    changed=first.model_copy(deep=True)
    changed.stages[1].model='another-capable-model'
    changed.stages.append(ai.Stage(id='new-local',provider='local',model='pilot-qwen38-27b'))
    # The current account report is below the new cumulative threshold; the
    # independent consumed reserve still prevents another request.
    result=admission.run(tmp_path,str(uuid4()),changed,observations=lambda:[absent,snapshot(20)],
        dispatch=lambda _:pytest.fail('Frozen variant replenished fallback budget'),now=lambda:1000)
    assert result['state']=='waiting'
