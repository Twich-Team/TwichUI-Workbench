"""Fresh larger local bounds never widen older saved approvals."""
import copy
import pytest
from workbench.planning_limits import consent_cap,final_bound


def current():
    return dict(investigation_version=7,max_local_requests=8,max_local_completion_tokens=6144,
                planning_deadline_seconds=1800,local_request_deadline_seconds=300)


@pytest.mark.parametrize('version',[None,1,3,4,5,6])
def test_old_consent_retains_2600_even_with_stray_larger_field(version):
    value=current()
    value['investigation_version']=version
    assert consent_cap(value)==2600
    assert final_bound(dict(local_planning_receipt=value,diagnostics={}))==2600
    with pytest.raises(ValueError):
        final_bound(dict(local_planning_receipt=value,diagnostics=dict(request_completion_limit=6144)))


@pytest.mark.parametrize('fault',['none','missing','larger','requests','deadline','absent_actual','wrong_actual','float_version','float_limit'])
def test_new_consent_and_actual_request_bound_both_required(fault):
    value=copy.deepcopy(current())
    diagnostic=dict(request_completion_limit=6144)
    if fault=='missing':
        value.pop('max_local_completion_tokens')
    if fault=='larger':
        value['max_local_completion_tokens']=99999
    if fault=='requests':
        value['max_local_requests']=99
    if fault=='deadline':
        value['planning_deadline_seconds']=99999
    if fault=='absent_actual':
        diagnostic.clear()
    if fault=='wrong_actual':
        diagnostic['request_completion_limit']=7000
    if fault=='float_version':
        value['investigation_version']=7.0
    if fault=='float_limit':
        value['max_local_completion_tokens']=6144.0
    if fault=='none':
        assert consent_cap(value)==6144
        assert final_bound(dict(local_planning_receipt=value,diagnostics=diagnostic))==6144
    else:
        with pytest.raises(ValueError):
            final_bound(dict(local_planning_receipt=value,diagnostics=diagnostic))
