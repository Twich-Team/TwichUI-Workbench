"""A setup correction consumes remaining original requests, never a new budget."""
import copy

import pytest
from sqlalchemy import select

from workbench import feature_check_preparation, feature_checks
from workbench.db import planning_jobs, model_attempts, proposal_decisions, write_transaction
from workbench.visual_scope import digest
from test_everyday import installed as installed
from test_feature_check_preparation import at_independent_preparation, IndependentPlanner


class MissingSetup(IndependentPlanner):
    def generate(self, packet, schema, expected_model):
        value, model, basis = super().generate(packet, schema, expected_model)
        return dict(finding='Controlled missing startup metadata.', inspection=None,
            program=None, limitation='The existing committed startup was not recognized.'), model, 'local'


def failed_context(client, headers, store, monkeypatch):
    at_independent_preparation(client, headers, store, monkeypatch)
    with store.connect() as c:
        job = c.execute(select(planning_jobs).where(planning_jobs.c.kind == 'feature_execution')).mappings().one()
    assert feature_check_preparation.advance(store, job, adapter=MissingSetup())
    with store.connect() as c:
        job = c.execute(select(planning_jobs).where(planning_jobs.c.id == job['id'])).mappings().one()
        decisions = list(c.execute(select(proposal_decisions)).mappings())
        attempts = list(c.execute(select(model_attempts)).mappings())
    original_packet = feature_checks.packet
    def corrected(c, decision):
        # Protocol unit fixture: bootstrap syntax/discovery is separately
        # exercised against real source by test_large_client_bootstrap.
        value = copy.deepcopy(original_packet(c, decision))
        test = next(f for f in value['files'] if f['path'].startswith('tests/test_'))
        test['client_bootstrap'] = dict(client='c', through_line=411)
        value['input_binding'] = digest({k:v for k,v in value.items() if k != 'input_binding'})
        return value
    monkeypatch.setattr(feature_checks, 'packet', corrected)
    return job, decisions, attempts, corrected


def test_refresh_preserves_all_failures_usage_and_original_remaining_allowance(
    client, headers, store, installed, monkeypatch,
):
    job, decisions, attempts, _ = failed_context(client, headers, store, monkeypatch)
    old = copy.deepcopy(job['payload']['independent_check_preparation'])
    receipt = feature_check_preparation.refresh_original_setup(store, job['id'])
    assert receipt['prior_context'] == old and receipt['requests_consumed'] == 1
    assert receipt['requests_remaining'] == 11 and receipt['original_request_limit'] == 12
    assert not receipt['budget_changed'] and not receipt['requests_reset'] and not receipt['candidate_disclosed']
    with store.connect() as c:
        new = c.execute(select(planning_jobs).where(planning_jobs.c.id == job['id'])).mappings().one()
        assert new['status'] == 'queued' and new['payload']['phase'] == 'prepare_independent_checks'
        assert new['payload']['independent_check_preparation']['history'] == old['history']
        assert list(c.execute(select(proposal_decisions)).mappings()) == decisions
        assert list(c.execute(select(model_attempts)).mappings()) == attempts
        feature_checks.validate_packet(new['payload']['independent_check_preparation']['initial_packet'],
            new['payload']['independent_check_preparation']['packet'])
    with pytest.raises(ValueError):
        feature_check_preparation.refresh_original_setup(store, job['id'])


@pytest.mark.parametrize('fault', ['unknown_generation', 'different_requirements'])
def test_setup_refresh_cannot_cover_uncertainty_or_changed_task(
    client, headers, store, installed, monkeypatch, fault,
):
    job, _, _, corrected = failed_context(client, headers, store, monkeypatch)
    if fault == 'unknown_generation':
        payload = copy.deepcopy(job['payload'])
        payload['independent_check_preparation']['history'][0]['confirmed_finished'] = False
        with write_transaction(store) as c:
            c.execute(planning_jobs.update().where(planning_jobs.c.id == job['id']).values(payload=payload))
    else:
        def changed(c, decision):
            value = corrected(c, decision)
            value['indexed_clauses'][0]['requirement'] = 'A different feature'
            value['input_binding'] = digest({k:v for k,v in value.items() if k != 'input_binding'})
            return value
        monkeypatch.setattr(feature_checks, 'packet', changed)
    with store.connect() as c:
        before = c.execute(select(planning_jobs).where(planning_jobs.c.id == job['id'])).mappings().one()
    with pytest.raises(ValueError):
        feature_check_preparation.refresh_original_setup(store, job['id'])
    with store.connect() as c:
        assert c.execute(select(planning_jobs).where(planning_jobs.c.id == job['id'])).mappings().one() == before
