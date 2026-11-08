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
