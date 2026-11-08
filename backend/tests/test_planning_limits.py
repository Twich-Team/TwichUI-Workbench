"""Fresh larger local bounds never widen older saved approvals."""

import copy
import pytest
from workbench.planning_limits import consent_cap, final_bound


def current():
    return dict(
        investigation_version=7,
        max_local_requests=8,
        max_local_completion_tokens=6144,
        planning_deadline_seconds=1800,
        local_request_deadline_seconds=300,
    )


@pytest.mark.parametrize("version", [None, 1, 3, 4, 5, 6])
def test_old_consent_retains_2600_even_with_stray_larger_field(version):
    value = current()
    value["investigation_version"] = version
    assert consent_cap(value) == 2600
    assert final_bound(dict(local_planning_receipt=value, diagnostics={})) == 2600
    with pytest.raises(ValueError):
        final_bound(
            dict(local_planning_receipt=value, diagnostics=dict(request_completion_limit=6144))
        )


@pytest.mark.parametrize(
    "fault",
    [
        "none",
        "missing",
        "larger",
        "requests",
        "deadline",
        "absent_actual",
        "wrong_actual",
        "float_version",
        "float_limit",
    ],
)
def test_new_consent_and_actual_request_bound_both_required(fault):
    value = copy.deepcopy(current())
    diagnostic = dict(request_completion_limit=6144)
    if fault == "missing":
        value.pop("max_local_completion_tokens")
    if fault == "larger":
        value["max_local_completion_tokens"] = 99999
    if fault == "requests":
        value["max_local_requests"] = 99
    if fault == "deadline":
        value["planning_deadline_seconds"] = 99999
    if fault == "absent_actual":
        diagnostic.clear()
    if fault == "wrong_actual":
        diagnostic["request_completion_limit"] = 7000
    if fault == "float_version":
        value["investigation_version"] = 7.0
    if fault == "float_limit":
        value["max_local_completion_tokens"] = 6144.0
    if fault == "none":
        assert consent_cap(value) == 6144
        assert final_bound(dict(local_planning_receipt=value, diagnostics=diagnostic)) == 6144
    else:
        with pytest.raises(ValueError):
            final_bound(dict(local_planning_receipt=value, diagnostics=diagnostic))


@pytest.mark.parametrize("fault", ["none", "missing", "more_calls", "float_calls", "deadline"])
def test_shared_codex_investigation_consent_preserves_local_and_global_bounds(fault):
    value = {
        **current(),
        "investigation_version": 8,
        "max_codex_requests": 8,
        "max_total_model_requests": 8,
        "codex_request_deadline_seconds": 120,
    }
    if fault == "missing":
        value.pop("max_total_model_requests")
    if fault == "more_calls":
        value["max_codex_requests"] = 9
    if fault == "float_calls":
        value["max_total_model_requests"] = 8.0
    if fault == "deadline":
        value["codex_request_deadline_seconds"] = 9999
    if fault == "none":
        assert consent_cap(value) == 6144
    else:
        with pytest.raises(ValueError):
            consent_cap(value)


@pytest.mark.parametrize("version,seconds", [(8, 120), (9, 300), (9, 120), (8, 300), (9, True)])
def test_codex_timeout_change_requires_its_actual_saved_consent(version, seconds):
    value = {
        **current(),
        "investigation_version": version,
        "max_codex_requests": 8,
        "max_total_model_requests": 8,
        "codex_request_deadline_seconds": seconds,
    }
    if (version, seconds) in [(8, 120), (9, 300)]:
        assert consent_cap(value) == 6144
    else:
        with pytest.raises(ValueError):
            consent_cap(value)


@pytest.mark.parametrize("version", [8,9,10])
def test_saved_request_count_cannot_expand_when_new_default_grows(version):
    from workbench.planning_limits import request_bound
    value=dict(current(),investigation_version=version,max_codex_requests=8,
               max_total_model_requests=8,codex_request_deadline_seconds=120 if version==8 else 300)
    if version==10:
        value.update(max_local_requests=16,max_codex_requests=16,max_total_model_requests=16)
    assert request_bound(value)==(16 if version==10 else 8)
    for key in ('max_local_requests','max_codex_requests','max_total_model_requests'):
        changed=copy.deepcopy(value)
        changed[key]=8 if version==10 else 16
        with pytest.raises(ValueError):
            request_bound(changed)


@pytest.mark.parametrize('fault',[None,'count','bool','total','maximum','units','missing_values'])
def test_shared_codex_observation_check_uses_bound_and_actual_reported_values(monkeypatch,fault):
    from workbench import planning_limits as limits
    monkeypatch.setattr(limits,'attempt_request_bound',lambda *args:16)
    usage=dict(units='tokens',requests_observed=16,max_request_total_tokens=100000,
               values=dict(total_tokens=900000))
    if fault=='count':
        usage['requests_observed']=17
    if fault=='bool':
        usage['requests_observed']=True
    if fault=='total':
        usage['values']['total_tokens']=-1
    if fault=='maximum':
        usage['max_request_total_tokens']=999999
    if fault=='units':
        usage['units']='dollars'
    if fault=='missing_values':
        usage['values']=None
    if fault:
        with pytest.raises(ValueError):
            limits.codex_observations(None,'current',usage,bug=True,planning=True)
    else:
        assert limits.codex_observations(None,'current',usage,bug=True,planning=True)==16


@pytest.mark.parametrize('fault',[None,'used_bool','remaining_bool','reset','total','exhausted','negative'])
def test_followup_cannot_reset_or_expand_the_original_request_bound(fault):
    from workbench.planning_limits import operation_request_bound
    consent=dict(current(),investigation_version=10,max_local_requests=16,
        max_codex_requests=16,max_total_model_requests=16,codex_request_deadline_seconds=300)
    followup=dict(budget_version=2,consumed_requests=5,remaining_requests=11,max_total_requests=16)
    if fault=='used_bool':
        followup['consumed_requests']=True
    if fault=='remaining_bool':
        followup['remaining_requests']=True
    if fault=='reset':
        followup['remaining_requests']=16
    if fault=='total':
        followup['max_total_requests']=32
    if fault=='exhausted':
        followup.update(consumed_requests=16,remaining_requests=0)
    if fault=='negative':
        followup.update(consumed_requests=-1,remaining_requests=17)
    payload=dict(local_planning_receipt=consent,bug_followup=followup)
    if fault:
        with pytest.raises(ValueError):
            operation_request_bound(payload)
    else:
        assert operation_request_bound(payload)==11
        assert operation_request_bound(dict(local_planning_receipt=consent))==16


@pytest.mark.parametrize('fault',[None,'unknown_status','missing_tokens','wrong_total','failed','unfinished','wrong_model','investigation','null_values'])
def test_single_turn_terminal_receipt_compatibility_does_not_accept_uncertainty(fault):
    import json
    import sqlite3
    from workbench.codex_provider import usage_record
    from workbench.planning_limits import settled_planning_chain
    usage=usage_record(dict(prompt_tokens=40,completion_tokens=10,total_tokens=50,reasoning_tokens=0))
    payload=dict(local_planning_receipt={})
    if fault=='unknown_status':
        usage['status']='unknown'
    if fault=='null_values':
        usage['values']=None
    if fault=='missing_tokens':
        usage['values'].pop('total_tokens')
    if fault=='wrong_total':
        usage['values']['total_tokens']=99
    if fault=='investigation':
        payload['local_planning_receipt']=dict(current(),investigation_version=10,max_local_requests=16,
            max_codex_requests=16,max_total_model_requests=16,codex_request_deadline_seconds=300)
    with sqlite3.connect(':memory:') as c:
        c.execute('CREATE TABLE planning_jobs (id,revision,status,attempts,payload,intake_id,kind)')
        c.execute('CREATE TABLE model_attempts (id,usage,outcome,finished_at,backend,actual_model,requested_model,job_id,purpose)')
        c.execute('INSERT INTO planning_jobs VALUES (?,?,?,?,?,?,?)',
            ('job',1,'completed',1,json.dumps(payload),'intake','planning'))
        c.execute('INSERT INTO model_attempts VALUES (?,?,?,?,?,?,?,?,?)',
            ('attempt',json.dumps(usage),'failed' if fault=='failed' else 'completed',
             None if fault=='unfinished' else 'terminal','codex','wrong' if fault=='wrong_model' else 'model',
             'model','job','planning'))
        if fault:
            with pytest.raises(ValueError):
                settled_planning_chain(c,'intake',1)
        else:
            chain=settled_planning_chain(c,'intake',1)
            assert len(chain)==1 and chain[0]['usage']==usage


@pytest.mark.parametrize('version',[2,3])
@pytest.mark.parametrize('fault',['float_version','bool_version','unknown_version'])
def test_malformed_continuation_version_cannot_restore_the_full_operation_bound(version,fault):
    from workbench.planning_limits import operation_request_bound
    followup=dict(budget_version=float(version) if fault=='float_version' else True if fault=='bool_version' else 999,
        consumed_requests=5,remaining_requests=11,max_total_requests=16)
    consent=dict(current(),investigation_version=10,max_local_requests=16,
        max_codex_requests=16,max_total_model_requests=16,codex_request_deadline_seconds=300)
    with pytest.raises(ValueError):
        operation_request_bound(dict(local_planning_receipt=consent,bug_followup=followup))
