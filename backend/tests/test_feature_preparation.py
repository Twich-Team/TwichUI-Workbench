"""Read-only scope operations on actual saved parent/worker records
controlled AI."""
import copy

from sqlalchemy import select, func
import pytest

from workbench import feature_preparation, feature_steps
from workbench.db import planning_jobs, model_attempts, real_execution_grants, write_transaction
from test_feature_steps import prepared_parent, scope
from test_everyday import installed as installed


class ScopePlanner:
    def __init__(self, response=None, *, uncertain=False, before=None):
        self.calls = []
        self.response = response
        self.uncertain = uncertain
        self.before = before
        self.diagnostics = {}

    def generate(self, packet, schema, expected_model):
        assert schema is feature_steps.StepScope
        self.calls.append(copy.deepcopy(packet))
        self.diagnostics = dict(generation_state='uncertain' if self.uncertain else 'finished',
            accounting_state='unknown' if self.uncertain else 'complete',
            usage=None if self.uncertain else dict(source='controlled_fixture', units='tokens',
                values=dict(prompt_tokens=100, completion_tokens=100, total_tokens=200)))
        if self.before:
            self.before()
        if self.uncertain:
            raise ValueError('Controlled lost provider response')
        return self.response or scope(), expected_model, 'demo'


def context(client, headers, store):
    _, decision, first, second = prepared_parent(client, headers, store)
    with store.connect() as c:
        job = c.execute(select(planning_jobs).where(planning_jobs.c.kind == 'feature_execution')).mappings().one()
    return decision, first, second, job['id']


def test_one_parent_prepares_a_pinned_step_without_model_or_source_write_authority(
    client, headers, store, installed,
):
    decision, first, second, job_id = context(client, headers, store)
    planner = ScopePlanner()
    assert feature_preparation.prepare_scope(store, job_id, first, adapter=planner)
    assert not feature_preparation.prepare_scope(store, job_id, first, adapter=planner)
    assert len(planner.calls) == 1
    with store.connect() as c:
        job = c.execute(select(planning_jobs).where(planning_jobs.c.id == job_id)).mappings().one()
        assert job['status'] == 'queued' and job['payload']['phase'] == 'scope_ready'
        saved = job['payload']['current_step_scope']
        assert feature_steps.validate_scope(c, saved) == saved
        assert saved['decision_id'] == decision and saved['task_id'] == first
        record, = job['payload']['scope_history']
        assert record['confirmed_finished'] and record['failure'] is None
        actual = c.execute(select(model_attempts).where(model_attempts.c.id == record['operation_id'])).mappings().one()
        assert actual['outcome'] == 'completed' and actual['usage']['source'] == 'controlled_fixture'
        assert c.execute(select(func.count()).select_from(real_execution_grants)).scalar_one() == 0


def test_settled_invalid_scope_gets_bounded_feedback_and_unknown_generation_never_replays(
    client, headers, store, installed,
):
    _, first, _, job_id = context(client, headers, store)
    bad = scope()
    bad['read_paths'].append('!!!TwichUI/absent.lua')
    invalid = ScopePlanner(bad)
    assert feature_preparation.prepare_scope(store, job_id, first, adapter=invalid)
    unknown = ScopePlanner(uncertain=True)
    assert feature_preparation.prepare_scope(store, job_id, first, adapter=unknown)
    assert 'outside its inspected packet' in unknown.calls[0]['previous_validation_failure']
    assert not feature_preparation.prepare_scope(store, job_id, first, adapter=ScopePlanner())
    with store.connect() as c:
        job = c.execute(select(planning_jobs).where(planning_jobs.c.id == job_id)).mappings().one()
        assert job['status'] == 'failed' and job['payload']['phase'] == 'held'
        assert len(job['payload']['scope_history']) == 2
        assert not job['payload']['scope_history'][-1]['confirmed_finished']
        assert job['payload']['current_step_scope'] is None


def test_parent_withdrawal_during_generation_retains_usage_but_discards_late_scope(
    client, headers, store, installed,
):
    _, first, _, job_id = context(client, headers, store)
    from workbench import task_sessions
    def withdraw():
        import json
        path = task_sessions.ROOT/'.local/task-runtime.json'
        value = json.loads(path.read_text())
        value.pop('feature_execution')
        path.write_text(json.dumps(value))
    planner = ScopePlanner(before=withdraw)
    assert feature_preparation.prepare_scope(store, job_id, first, adapter=planner)
    with store.connect() as c:
        job = c.execute(select(planning_jobs).where(planning_jobs.c.id == job_id)).mappings().one()
        assert job['payload']['current_step_scope'] is None
        assert 'stopped or changed' in job['message']
        attempt = c.execute(select(model_attempts).where(model_attempts.c.id == job['payload']['scope_history'][-1]['operation_id'])).mappings().one()
        assert attempt['usage'] and attempt['finished_at']


def test_stopping_parent_before_first_scope_starts_no_model_and_keeps_history(
    client, headers, store, installed,
):
    decision, first, _, job_id = context(client, headers, store)
    from uuid import uuid4
    from workbench import task_sessions
    from workbench.db import proposal_decisions
    planner = ScopePlanner()
    assert task_sessions.cancel(store, decision, str(uuid4()))['status'] == 'cancelled'
    with pytest.raises(ValueError, match='stopped'):
        feature_preparation.prepare_scope(store, job_id, first, adapter=planner)
    assert not planner.calls
    with store.connect() as c:
        row = c.execute(select(proposal_decisions).where(proposal_decisions.c.id == decision)).mappings().one()
        from workbench import feature_permission
        assert feature_permission.validate(c, row, active=False)
        assert c.execute(select(func.count()).select_from(model_attempts)).scalar_one() == 2
    assert client.get('/api/features/'+row['intake_id']).status_code == 200


def test_stop_during_scope_signals_transport_and_fences_its_completed_late_output(
    client, headers, store, installed,
):
    decision, first, _, job_id = context(client, headers, store)
    from uuid import uuid4
    from workbench import task_sessions
    from workbench.db import task_sessions as parents
    def stop():
        assert task_sessions.cancel(store, decision, str(uuid4()))['status'] == 'cancel_requested'
        assert feature_preparation.ParentStop(store, decision, None).is_set()
    planner = ScopePlanner(before=stop)
    assert feature_preparation.prepare_scope(store, job_id, first, adapter=planner)
    with store.connect() as c:
        job = c.execute(select(planning_jobs).where(planning_jobs.c.id == job_id)).mappings().one()
        parent = c.execute(select(parents).where(parents.c.id == decision)).mappings().one()
        assert job['status'] == 'failed' and job['payload']['current_step_scope'] is None
        assert job['payload']['scope_history'][-1]['confirmed_finished']
        assert parent['status'] == 'cancelled' and parent['finished_at']
        assert parent['state']['cancel_requested']
        assert not c.execute(select(real_execution_grants)).first()


def test_old_completed_scope_claim_cannot_be_mistaken_for_a_new_interrupted_check(
    client, headers, store, installed,
):
    _, first, _, job_id = context(client, headers, store)
    assert feature_preparation.prepare_scope(store, job_id, first, adapter=ScopePlanner())
    with write_transaction(store) as c:
        job = c.execute(select(planning_jobs).where(planning_jobs.c.id == job_id)).mappings().one()
        payload = copy.deepcopy(job['payload'])
        payload['phase'] = 'preparing_runtime'
        payload['feature_scope_claim']['owner']['pid'] += 10000000
        c.execute(planning_jobs.update().where(planning_jobs.c.id == job_id).values(
            status='running', payload=payload, lease=None))
    assert not feature_preparation.recover_one(store)
    with store.connect() as c:
        job = c.execute(select(planning_jobs).where(planning_jobs.c.id == job_id)).mappings().one()
        assert job['status'] == 'running' and job['payload']['phase'] == 'preparing_runtime'
        attempt = c.execute(select(model_attempts).where(
            model_attempts.c.id == job['payload']['scope_history'][-1]['operation_id'])).mappings().one()
        assert attempt['outcome'] == 'completed' and attempt['usage']
