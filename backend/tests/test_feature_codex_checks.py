"""The independent producer receives original source and its saved provider."""
import copy
import time
from uuid import uuid4

import pytest
from sqlalchemy import select

from workbench import codex_provider, feature_checks, feature_check_preparation, feature_steps
from workbench.db import planning_jobs, model_attempts
from test_everyday import installed as installed
from test_feature_checks import program
from test_feature_execution import missing_names_parent
from test_feature_check_preparation import at_independent_preparation


def test_codex_disclosure_can_bind_a_checked_predecessor_without_reading_original_head(tmp_path):
    import subprocess
    from workbench.feature_steps import _file
    root = tmp_path/'checked'
    root.mkdir()
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args]).decode().strip()
    git('init', '-q')
    git('config', 'user.name', 'Controlled fixture')
    git('config', 'user.email', 'fixture@invalid')
    (root/'tests').mkdir()
    path = 'tests/test_session.lua'
    (root/path).write_text('return "original"\n')
    git('add', '.')
    git('commit', '-qm', 'Controlled original')
    original = git('rev-parse', 'HEAD')
    (root/path).write_text('return "checked predecessor"\n')
    git('commit', '-qam', 'Controlled checked input')
    base = git('rev-parse', 'HEAD')
    metadata, _ = _file(root, base, path)
    packet = dict(base=base, files=[dict(metadata,
        excerpts=[dict(line=1, text='return "checked predecessor"')])])
    planner = codex_provider.Planner(dict(model='controlled-codex', effort='high'), lambda: False)
    planner.bind_source(packet, source_root=root)
    planner.validate_source(packet)
    changed = copy.deepcopy(packet)
    changed['files'][0]['excerpts'][0]['text'] = 'return "original"'
    with pytest.raises(codex_provider.Hold, match='disclosure'):
        planner.validate_source(changed)
    changed['base'] = original
    with pytest.raises(codex_provider.Hold, match='disclosure'):
        planner.bind_source(changed, source_root=root)


@pytest.mark.parametrize('review', [False, True])
def test_codex_prepares_blind_feature_program_and_review_with_exact_disclosure(
    client, headers, store, installed, monkeypatch, review,
):
    _, decision, _, _ = missing_names_parent(client, headers, store)
    with store.connect() as c:
        original = feature_checks.packet(c, decision)
    packet = copy.deepcopy(original)
    response = dict(finding='Observe original behavior.', inspection=None,
        program=program(packet), limitation=None)
    schema = feature_checks.Response
    if review:
        packet.update(proposed_program=response['program'], program_binding='a'*64)
        schema = feature_checks.Review
        response = dict(program_binding='a'*64, ready=True, issues=[])
    planner = codex_provider.Planner(dict(model='controlled-codex', effort='high'), lambda: False)
    planner.bind_source(original)
    calls = []

    def generate(model, effort, prompt, wire, stop, **bounds):
        assert model == 'controlled-codex' and effort == 'high' and not stop()
        assert 'original' in prompt.lower() and 'candidate' in prompt.lower()
        expected = feature_checks.REVIEW_INSTRUCTIONS if review else feature_checks.INSTRUCTIONS
        assert prompt.startswith(expected)
        assert wire['type'] == 'object'
        assert wire['title'] == ('ReviewResponse' if review else 'ProgramResponse')
        assert bounds == dict(input_limit=codex_provider.MAX_BUG_INPUT,
            request_seconds=codex_provider.SECONDS)
        calls.append(prompt)
        return (response if review else dict(response=response)), dict(prompt_tokens=20, completion_tokens=10, reasoning_tokens=2, total_tokens=30)

    monkeypatch.setattr(codex_provider, 'generate', generate)
    result, model, basis = planner.generate(packet, schema, expected_model='controlled-codex')
    assert schema.model_validate(result) and model == 'controlled-codex' and basis == 'codex'
    assert len(calls) == 1 and planner.diagnostics['accounting_state'] == 'complete'
    changed = copy.deepcopy(packet)
    changed['inspection_windows'] = [dict(path=original['files'][0]['path'],
        sha256=original['files'][0]['sha256'], line=1, through_line=1, text='unrelated private bytes')]
    with pytest.raises(codex_provider.Hold, match='disclosure'):
        planner.generate(changed, schema, expected_model='controlled-codex')
    changed.pop('inspection_windows')
    changed['inspection_searches'] = [dict(paths=None, matches=[])]
    with pytest.raises(codex_provider.Hold, match='disclosure'):
        planner.generate(changed, schema, expected_model='controlled-codex')
    packet['files'].append(dict(path='!!!TwichUI/candidate.lua', sha256='b'*64, excerpts=[]))
    with pytest.raises(codex_provider.Hold, match='disclosure'):
        planner.generate(packet, schema, expected_model='controlled-codex')
    assert len(calls) == 1


@pytest.mark.parametrize('backend',['codex','smart'])
def test_independent_adapter_routes_to_authorized_native_stage_and_never_waits_for_optional_local_work(
    client, headers, store, installed, monkeypatch,backend,
):
    _, _, _, _ = at_independent_preparation(client, headers, store, monkeypatch)
    original_parent = feature_steps.parent

    # Routing unit boundary: supply a saved Codex route, retaining the real
    # original-only packet and existing independent preparation journal.
    def parent(*args, **kwargs):
        approval, permission = original_parent(*args, **kwargs)
        permission = copy.deepcopy(permission)
        permission['scope']['provider'] = backend
        permission['scope']['route'].update(model='controlled-codex', effort='high')
        if backend=='smart':
            from workbench.ai_strategy import Stage,Strategy
            permission['scope']['route'].update(provider='smart',strategy=Strategy(stages=[
                Stage(id='primary',provider='codex',model='controlled-codex',effort='high',allocation=50),
                Stage(id='next',provider='claude',model='claude-sonnet-5-5',effort='high',allocation=60)]).model_dump())
        return approval, permission

    monkeypatch.setattr(feature_steps, 'parent', parent)
    from workbench import local_model_priority
    monkeypatch.setattr(local_model_priority, 'planning_ready', lambda: pytest.fail('Local work must not delay this route'))
    bound = []
    searches = []
    original_bind = codex_provider.Planner.bind_source

    def bind(self, packet):
        bound.append(copy.deepcopy(packet))
        return original_bind(self, packet)

    monkeypatch.setattr(codex_provider.Planner, 'bind_source', bind)
    from workbench import bug_source_planning
    original_inspect = bug_source_planning.inspect
    def inspect(packet, requests, **kwargs):
        searches.append(kwargs['disclosure_paths'])
        return original_inspect(packet, requests, **kwargs)
    monkeypatch.setattr(bug_source_planning, 'inspect', inspect)
    monkeypatch.setattr(codex_provider, 'generate', lambda *args, **kwargs: (
        dict(response=dict(finding='Find only disclosed original definitions.', inspection=[dict(query='normalize')], program=None, limitation=None)),
        dict(prompt_tokens=20, completion_tokens=10, reasoning_tokens=2, total_tokens=30)))
    native_calls=[]
    if backend=='smart':
        from workbench import ai_strategy as ai,smart_provider
        def exchange(request,stop,seconds):
            assert not stop()
            observations=[ai.Observation(provider=p,state='available',observed_at=time.time(),paid_usage='disabled',
                windows=[ai.Window(name='short',used_percent=100 if p=='codex' else 10,resets_at=time.time()+3600)]) for p in ('codex','claude')]
            decision=ai.evaluate(request['strategy'],observations,[],now=time.time())
            assert decision.provider=='claude'
            if request['operation']=='smart-choose':
                return dict(state='choice',decision=decision.model_dump())
            assert request['operation']=='smart-turn' and request['stage_id']=='next'
            native_calls.append(request)
            return dict(state='completed',result=dict(actual_model='claude-sonnet-5-5',provider_receipt=str(uuid4()),
                output=dict(response=dict(finding='Find only disclosed original definitions.',inspection=[dict(query='normalize')],program=None,limitation=None)),
                usage=dict(prompt_tokens=20,completion_tokens=10,total_tokens=30)),admission={})
        monkeypatch.setattr(smart_provider,'exchange',exchange)
    with store.connect() as c:
        job = c.execute(select(planning_jobs).where(planning_jobs.c.kind == 'feature_execution')).mappings().one()
    assert feature_check_preparation.advance(store, job)
    assert len(bound) == 1 and bound[0]['evidence_boundary'].startswith('Only original committed source')
    assert searches == [{f['path'] for f in bound[0]['files']}]
    assert not {'candidate', 'patch', 'completed_steps', 'execution_journal'} & bound[0].keys()
    with store.connect() as c:
        ledger = c.execute(select(model_attempts).where(model_attempts.c.purpose == ('strategy-request' if backend=='smart' else 'feature_check_preparation'),
            model_attempts.c.source=='workbench')).mappings().one()
        assert ledger['backend']==('claude' if backend=='smart' else 'codex')
        assert ledger['actual_model']==('claude-sonnet-5-5' if backend=='smart' else 'controlled-codex')
        assert ledger['usage']['values']['total_tokens'] == 30
        if backend=='smart':
            controller=c.execute(select(model_attempts).where(model_attempts.c.purpose=='feature_check_preparation',
                model_attempts.c.source=='strategy-control')).mappings().one()
            assert controller['usage']['values']['total_tokens']==30
    if backend=='smart':
        assert len(native_calls)==1 and ledger['requested_effort']=='high' and ledger['actual_effort'] is None
