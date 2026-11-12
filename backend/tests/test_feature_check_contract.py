"""Rehearse the failed generation boundary through the actual saved-step entry."""
import copy
import json

import pytest
from sqlalchemy import select

from workbench import codex_provider, feature_checks, feature_check_preparation, feature_steps
from workbench.db import planning_jobs, model_attempts, real_execution_requests
from test_everyday import installed as installed
from test_feature_check_preparation import at_independent_preparation
from test_feature_checks import program


def test_generation_grammar_rejects_incomplete_program_plus_limitation():
    schema = feature_checks.wire_schema()
    value = dict(finding='Missing behavior.', inspection=None, program=None,
        limitation='Cannot cover lifecycle reset.')
    assert feature_checks.Response.model_validate(value)
    assert len(schema['anyOf']) == 3
    for variant, selected in zip(schema['anyOf'], ('inspection', 'program', 'limitation'), strict=True):
        assert variant['properties'][selected].get('type') != 'null'
        assert all(variant['properties'][key] == dict(type='null')
            for key in ('inspection', 'program', 'limitation') if key != selected)
    with pytest.raises(ValueError):
        feature_checks.Response.model_validate({**value, 'inspection': [dict(query='settings')]})
    with pytest.raises(ValueError):
        feature_checks.Response.model_validate({**value, 'limitation': None})


def test_actual_preparation_entry_uses_exclusive_codex_wire_and_preserves_checked_steps(
    client, headers, store, installed, monkeypatch,
):
    identity, decision, _, _ = at_independent_preparation(client, headers, store, monkeypatch)
    actual_parent = feature_steps.parent
    def codex_parent(*args, **kwargs):
        approval, permission = actual_parent(*args, **kwargs)
        permission = copy.deepcopy(permission)
        permission['scope']['provider'] = 'codex'
        permission['scope']['route'].update(model='controlled-codex', effort='high')
        return approval, permission
    monkeypatch.setattr(feature_steps, 'parent', codex_parent)
    calls = []
    def generate(model, effort, prompt, schema, stop, **bounds):
        packet = json.loads(prompt.split('Untrusted task data:\n', 1)[1])
        assert packet['evidence_boundary'].startswith('Only original committed source')
        assert not {'candidate', 'patch', 'completed_steps', 'feature_input'} & packet.keys()
        assert bounds['input_limit'] == codex_provider.MAX_BUG_INPUT
        if schema['title'] == 'ReviewResponse':
            response = dict(program_binding=packet['program_binding'], ready=True, issues=[])
        else:
            response = dict(response=dict(finding='Complete original-source observations.',
                inspection=None, limitation=None, program=program(packet)))
            if not calls:
                # A complete provider turn returning two alternatives is held,
                # with its exact response/usage retained, never repaired silently.
                response['response']['limitation'] = 'Incomplete.'
            else:
                assert schema['type'] == 'object' and len(schema['properties']['response']['anyOf']) == 3
                feature_checks.Response.model_validate(response['response'])
        calls.append(copy.deepcopy(response))
        return response, dict(prompt_tokens=20, completion_tokens=10, total_tokens=30, reasoning_tokens=0)
    monkeypatch.setattr(codex_provider, 'generate', generate)
    def read():
        with store.connect() as c:
            return c.execute(select(planning_jobs).where(planning_jobs.c.kind == 'feature_execution')).mappings().one()
    before = read()
    assert feature_check_preparation.advance(store, before)
    held = read()
    assert held['payload']['independent_check_preparation']['history'][0]['failure']
    assert not held['payload']['independent_check_preparation'].get('proposed_program')
    assert feature_check_preparation.advance(store, held)
    assert feature_check_preparation.advance(store, read())
    final = read()
    assert final['payload']['phase'] == 'independent_checks_ready'
    assert final['payload']['completed_steps'] == before['payload']['completed_steps']
    history = final['payload']['independent_check_preparation']['history']
    assert len(history) == 3 and all(r['confirmed_finished'] for r in history)
    assert not feature_check_preparation.advance(store, final)
    assert len(calls) == 3
    with store.connect() as c:
        assert len(c.execute(select(real_execution_requests)).all()) == 2
        ledgers = list(c.execute(select(model_attempts).where(model_attempts.c.purpose == 'feature_check_preparation')).mappings())
        assert len(ledgers) == 3 and sum(r['usage']['values']['total_tokens'] for r in ledgers) == 90
