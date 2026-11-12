"""The existing browser recovery binds needed original files and two requests."""
import copy
from uuid import uuid4

import pytest
from sqlalchemy import select

from workbench import feature_check_preparation, feature_scope_recovery, bug_source_planning, regression_preparation
from workbench.db import planning_jobs, model_attempts, proposal_decisions
from workbench.planning_transport import PlanningUnavailable
from test_everyday import installed as installed
from test_feature_check_preparation import at_independent_preparation, IndependentPlanner


def test_existing_readonly_correction_preserves_original_budget_and_binds_dependency(
    client, headers, store, installed, monkeypatch,
):
    identity, decision, _, _ = at_independent_preparation(client, headers, store, monkeypatch)
    with store.connect() as c:
        job = c.execute(select(planning_jobs).where(planning_jobs.c.kind == 'feature_execution')).mappings().one()
    selected = {f['path'] for f in job['payload']['independent_check_preparation']['packet']['files']}
    root, base, _ = regression_preparation.repository()
    names = regression_preparation.git(root, 'ls-tree', '-r', '--name-only', base).decode().splitlines()
    path = next(p for p in names if p.endswith('.lua') and p.startswith('tests/test_') and p not in selected)
    class NeedsDefinition(IndependentPlanner):
        def generate(self, packet, schema, expected_model):
            _, model, _ = super().generate(packet, schema, expected_model)
            return dict(finding='Inspect an existing original fixture dependency.',
                inspection=[dict(path=path, start=1, end=20)], program=None, limitation=None), model, 'local'
    actual_inspect = bug_source_planning.inspect
    def refused(*args, **kwargs):
        raise PlanningUnavailable('This investigation read needs additional source disclosure permission.')
    monkeypatch.setattr(bug_source_planning, 'inspect', refused)
    for _ in range(12):
        with store.connect() as c:
            job = c.execute(select(planning_jobs).where(planning_jobs.c.id == job['id'])).mappings().one()
        assert feature_check_preparation.advance(store, job, adapter=NeedsDefinition())
    monkeypatch.setattr(bug_source_planning, 'inspect', actual_inspect)
    with store.connect() as c:
        before = c.execute(select(planning_jobs).where(planning_jobs.c.id == job['id'])).mappings().one()
        attempts = list(c.execute(select(model_attempts)).mappings())
        approvals = list(c.execute(select(proposal_decisions)).mappings())
        offer = feature_scope_recovery.offer(c, identity)
    assert offer['generation_requests'] == 2 and offer['completion_tokens'] == 6144
    assert [f['path'] for f in offer['approved_inputs']['additional_source']] == [path]
    command = dict(command_id=str(uuid4()), binding=offer['binding'], generation_requests=2)
    url = '/api/features/'+identity+'/prepare-step-again'
    response = client.post(url, headers=headers, json=command)
    assert response.status_code == 200, response.text
    assert client.post(url, headers=headers, json=command).status_code == 200
    assert client.post(url, headers=headers, json={**command, 'generation_requests':3}).status_code == 409
    with store.connect() as c:
        current = c.execute(select(planning_jobs).where(planning_jobs.c.id == job['id'])).mappings().one()
        assert current['payload']['independent_check_preparation']['history'] == before['payload']['independent_check_preparation']['history']
        assert path in {f['path'] for f in current['payload']['independent_check_preparation']['packet']['files']}
        from workbench.feature_steps import parent
        _, permission = parent(c, decision)
        assert permission['budget']['verification_requests'] == 12
        assert feature_check_preparation.request_limit(c, current, permission) == 14
        assert list(c.execute(select(model_attempts)).mappings()) == attempts
        assert list(c.execute(select(proposal_decisions)).mappings()) == approvals
        changed = copy.deepcopy(dict(current))
        changed['payload']['independent_check_corrections'][0]['generation_requests'] = 3
        with pytest.raises(ValueError):
            feature_check_preparation.request_limit(c, changed, permission)
    class StillInspecting(IndependentPlanner):
        def generate(self, packet, schema, expected_model):
            _, model, _ = super().generate(packet, schema, expected_model)
            return dict(finding='Inspect disclosed original source only.',
                inspection=[dict(path=path, start=1, end=20)], program=None, limitation=None), model, 'local'
    for _ in range(2):
        with store.connect() as c:
            current = c.execute(select(planning_jobs).where(planning_jobs.c.id == job['id'])).mappings().one()
        assert feature_check_preparation.advance(store, current, adapter=StillInspecting())
    with store.connect() as c:
        before_contract = c.execute(select(planning_jobs).where(planning_jobs.c.id == job['id'])).mappings().one()
        second = feature_scope_recovery.offer(c, identity)
    assert second['approved_inputs']['additional_source'] == []
    assert second['approved_inputs']['preparation_contract'] == feature_check_preparation.contract_binding()
    command2 = dict(command_id=str(uuid4()), binding=second['binding'], generation_requests=2)
    response = client.post(url, headers=headers, json=command2)
    assert response.status_code == 200, response.text
    assert client.post(url, headers=headers, json=command2).status_code == 200
    with store.connect() as c:
        corrected = c.execute(select(planning_jobs).where(planning_jobs.c.id == job['id'])).mappings().one()
        assert len(corrected['payload']['independent_check_corrections']) == 2
        assert corrected['payload']['independent_check_corrections'][0] == before_contract['payload']['independent_check_corrections'][0]
        assert corrected['payload']['independent_check_preparation']['history'] == before_contract['payload']['independent_check_preparation']['history']
        assert corrected['payload']['independent_check_preparation']['packet'] == before_contract['payload']['independent_check_preparation']['packet']
        assert feature_check_preparation.request_limit(c, corrected, permission) == 16
    for _ in range(2):
        with store.connect() as c:
            current = c.execute(select(planning_jobs).where(planning_jobs.c.id == job['id'])).mappings().one()
        assert feature_check_preparation.advance(store, current, adapter=StillInspecting())
    with store.connect() as c:
        assert feature_scope_recovery.offer(c, identity) is None
