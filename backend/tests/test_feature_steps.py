"""Real canonical parent/graph and Git bytes
no live provider or execution grant."""
import copy
import json
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from workbench import feature_steps, feature_permission, feature_execution, task_budget, task_sessions
from workbench.db import proposal_decisions, task_run_offers, task_sessions as sessions
from workbench.real_workspace import Runtime, durable
from workbench.repair_cycle import record_readiness
from test_feature_permission import ready, enable, request
from test_everyday import installed as installed
from test_verification_profiles import PRODUCTION, TEST


def prepared_parent(client, headers, store, **changes):
    identity, _ = ready(client, headers, store, **changes)
    enable()
    selected = feature_permission.offer(store, identity)
    body = request(selected)
    feature_permission.request(store, identity, body)
    assert feature_execution.apply_one(store)
    first = next(t['task_id'] for t in selected['scope']['graph'] if not t['dependencies'])
    second = next(t['task_id'] for t in selected['scope']['graph'] if t['dependencies'])
    return identity, str(body.command_id), first, second


def scope():
    return dict(role='implementation', read_paths=[PRODUCTION, TEST],
        writable_paths=[PRODUCTION, TEST], focused_test=TEST, limitation='',
        checks=[dict(acceptance_index=0, observation='Observe actual normalized output.',
            control='The newly required result is absent in unchanged production.')])


def test_step_preparation_pins_parent_scope_and_committed_bytes(client, headers, store, installed):
    _, decision, first, second = prepared_parent(client, headers, store)
    with store.connect() as c:
        saved = feature_steps.compile_scope(c, decision, first, scope())
        assert feature_steps.validate_scope(c, saved) == saved
        assert saved['publication'] == 'hold' and saved['verification'] == 'not_run'
        assert saved['task_id'] == first and saved['evidence'][0]['path'] == PRODUCTION
        assert [x['path'] for x in saved['framework']] == ['tests/harness.lua', 'tests/run.sh']
        with pytest.raises(ValueError, match='checked predecessor'):
            feature_steps.compile_scope(c, decision, second, scope())
        spec, oracle = feature_steps.recipe(c, saved, dict(name='NON-MODEL', email='fixture@invalid'))
        assert spec['category'] == 'Feature' and spec['verification_profile'] == feature_steps.PROFILE
        assert spec['repair_cycle'] == 'scoped-feature-v1' and 'feature-step' in oracle
        assert 'approved_intent' not in spec
        with pytest.raises(ValueError, match='explicit task'):
            task_budget.permission(c, c.execute(select(proposal_decisions).where(proposal_decisions.c.id == decision)).mappings().one())


@pytest.mark.parametrize('fault', ['traversal', 'absolute', 'missing', 'harness', 'library',
    'duplicate', 'uncovered', 'broadened', 'extra-test', 'no-test', 'no-production', 'limitation', 'verification-writes'])
def test_untrusted_step_scope_cannot_invent_or_expand_implementation(
    client, headers, store, installed, fault,
):
    _, decision, first, _ = prepared_parent(client, headers, store)
    value = scope()
    if fault in {'traversal', 'absolute', 'missing', 'harness', 'library'}:
        path = {'traversal':'!!!TwichUI/../secret.lua', 'absolute':'/tmp/secret.lua',
            'missing':'!!!TwichUI/absent.lua', 'harness':'tests/harness.lua',
            'library':'!!!TwichUI/libs/thirdparty.lua'}[fault]
        value['read_paths'].append(path)
        value['writable_paths'][0] = path
    elif fault == 'duplicate':
        value['read_paths'].append(TEST)
    elif fault == 'uncovered':
        value['checks'][0]['acceptance_index'] = 1
    elif fault == 'broadened':
        value['writable_paths'].append('!!!TwichUI/absent.lua')
    elif fault == 'extra-test':
        value['read_paths'].append('tests/test_client.lua')
        value['writable_paths'].append('tests/test_client.lua')
    elif fault == 'no-test':
        value['writable_paths'] = [PRODUCTION]
    elif fault == 'no-production':
        value['writable_paths'] = [TEST]
    elif fault == 'limitation':
        value['limitation'] = 'A required new file is outside the supported current-file scope.'
    else:
        value['role'] = 'verification'
    with store.connect() as c, pytest.raises(ValueError):
        feature_steps.compile_scope(c, decision, first, value)


@pytest.mark.parametrize('fault', ['parent', 'task', 'source', 'hash', 'framework', 'checks', 'publication'])
def test_frozen_scope_cannot_be_rebound_after_preparation(client, headers, store, installed, fault):
    _, decision, first, second = prepared_parent(client, headers, store)
    with store.connect() as c:
        saved = feature_steps.compile_scope(c, decision, first, scope())
        value = copy.deepcopy(saved)
        if fault == 'parent':
            value['parent_binding'] = 'a'*64
        elif fault == 'task':
            value['task_id'] = second
        elif fault == 'source':
            value['base'] = 'b'*40
        elif fault == 'hash':
            value['evidence'][0]['sha256'] = 'c'*64
        elif fault == 'framework':
            value['framework'] = []
        elif fault == 'checks':
            value['scope']['checks'][0]['observation'] = 'A different observation'
        else:
            value['publication'] = 'draft-pr'
        with pytest.raises(ValueError):
            feature_steps.validate_scope(c, value)


def offer_fixture(client, headers, store, tmp_path):
    _, decision, first, second = prepared_parent(client, headers, store)
    with store.connect() as c:
        saved = feature_steps.compile_scope(c, decision, first, scope())
        spec, oracle = feature_steps.recipe(c, saved, dict(name='NON-MODEL', email='fixture@invalid'))
    root = tmp_path/'feature-prepared'
    root.mkdir(mode=0o700)
    durable(root/'recipe.json', json.dumps(spec).encode())
    durable(root/'recipe-oracle.lua', oracle.encode())
    runtime = Runtime(root)
    runtime.prepare()
    # Controlled confinement telemetry, explicitly NOT installation readiness.
    result = dict(status='pass', rc=0, usage=dict(cgroup_removed=True, controlled_fixture=True))
    run = subprocess.run(['bash','tests/run.sh'], cwd=runtime.workspace, capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    baseline = dict(**result, command=['bash','tests/run.sh'], network_mode='private-loopback',
        out=run.stdout + run.stderr)
    durable(root/'fresh-baseline-suite.json', json.dumps(baseline).encode())
    boundary = root/'controlled-boundary.json'
    durable(boundary, json.dumps(dict(checks={'controlled':result})).encode())
    record_readiness(runtime, boundary)
    offered = feature_steps.register_offer(store, runtime)
    return decision, first, second, runtime, offered


def test_feature_offer_is_immutable_and_task_specific_without_forged_decisions(
    client, headers, store, installed, tmp_path,
):
    decision, first, second, runtime, offered = offer_fixture(client, headers, store, tmp_path)
    assert feature_steps.register_offer(store, runtime) == offered
    with store.connect() as c:
        row = c.execute(select(proposal_decisions).where(proposal_decisions.c.id == decision)).mappings().one()
        policy = task_budget.permission(c, row, task_id=first)
        historical = task_budget.permission(c, row, task_id=first, offer_id=offered['id'], require_current=False)
        assert policy == historical
        assert policy['feature_task_id'] == first and policy['feature_attempt'] == 1
        assert policy['budget']['publication'] == 'hold' and policy['budget']['generated_tokens'] == 24000
        assert len(c.execute(select(proposal_decisions)).all()) == 2
        assert len(c.execute(select(task_run_offers)).all()) == 1
        with pytest.raises(ValueError):
            task_budget.permission(c, row, task_id=second, offer_id=offered['id'])
        with pytest.raises(ValueError):
            task_budget.permission(c, row, task_id=first, require_current=False)
        inputs = task_budget.preparation_inputs(c, offered)
        assert inputs['recipe'] == runtime.recipe and inputs['feature_input'] is None
    with pytest.raises(ValueError, match='recovery record'):
        feature_steps.register_offer(store, runtime, attempt=2)


def test_feature_session_uses_actual_parent_command_and_stopping_closes_only_its_slot(
    client, headers, store, installed, tmp_path,
):
    decision, first, _, runtime, offered = offer_fixture(client, headers, store, tmp_path)
    saved = feature_steps.queue_session(store, offered)
    assert saved['id'] == decision and saved['task_id'].startswith('intake-')
    assert feature_steps.queue_session(store, offered)['id'] == saved['id']
    from workbench.feature_sessions import projection
    slot = projection(saved)
    assert slot['id'] != decision and slot['task_id'] == first
    config_path = task_sessions._config(store, slot)
    config = json.loads(config_path.read_text())
    command = json.loads(Path(config['owner_instruction']).read_text())
    assert command['decision_id'] == decision and config['task']['approval_id'] == decision
    assert config['feature_offer_id'] == offered['id'] and not config['allow_publication']
    from uuid import uuid4
    assert task_sessions.cancel(store, saved['id'], str(uuid4()))['status'] == 'cancelled'
    with store.connect() as c:
        parent = c.execute(select(proposal_decisions).where(proposal_decisions.c.id == decision)).mappings().one()
        assert parent['status'] == 'applied'
        with pytest.raises(ValueError, match='stopped'):
            task_budget.permission(c, parent, task_id=first, offer_id=offered['id'])
        assert task_budget.permission(c, parent, task_id=first, offer_id=offered['id'], require_current=False)
        assert len(c.execute(select(proposal_decisions)).all()) == 2
        assert len(c.execute(select(sessions)).all()) == 1
