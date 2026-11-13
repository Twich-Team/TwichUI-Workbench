"""Disposable machinery controls; never local inference or addon execution."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
import json

import pytest
from sqlalchemy import select
from workbench import real_execution as real
from workbench.db import write_transaction, model_attempts
from workbench.real_provider import implement, BudgetError
from workbench.real_workspace import durable, Tools


class ControlledRuntime:
    """A test double, never production capability evidence."""
    ready = True
    calls = 0

    def preflight(self):
        return [dict(name='disposable test double', status='passed' if self.ready else 'blocked')]

    def identity(self):
        return {'model': 'unit-test-no-inference'}

    def execute(self, identity, fence, permitted):
        self.calls += 1
        assert permitted()
        return dict(verification='passed', inference_ran=False, candidate_id='controlled')

    def verify_frozen(self, result):
        return result.get('candidate_id') == 'controlled'

    def cleanup(self, row):
        return True


def granted(store):
    runtime = ControlledRuntime()
    grant = real.issue(store, 'TEST OWNER TEXT; NOT AN ACTUAL RUN APPROVAL', 'pytest controlled store', runtime)
    receipt = real.submit(store, grant['id'], str(uuid4()))
    return runtime, grant, receipt


def test_gates_block_issuance_and_do_not_consume_attempt(store):
    runtime = ControlledRuntime()
    runtime.ready = False
    with pytest.raises(ValueError, match='gates'):
        real.issue(store, 'test instruction', 'pytest', runtime)
    with store.connect() as c:
        assert not c.execute(select(real.grants)).all()
        assert not c.execute(select(model_attempts).where(model_attempts.c.purpose == 'implementation')).all()


def test_exact_command_retry_conflict_and_single_grant(store):
    runtime, grant, receipt = granted(store)
    assert real.submit(store, grant['id'], receipt['id'])['id'] == receipt['id']
    with pytest.raises(ValueError, match='Conflicting'):
        real.submit(store, 'different', receipt['id'])
    with pytest.raises(ValueError, match='One request'):
        real.submit(store, grant['id'], str(uuid4()))
    assert real.issue(store, grant['snapshot']['owner_instruction'], 'pytest controlled store', runtime)['id'] == grant['id']
    assert real.process_one(store, runtime)
    assert runtime.calls == 1
    assert not real.process_one(store, runtime)
    with store.connect() as c:
        attempts = c.execute(select(model_attempts).where(model_attempts.c.execution_id == receipt['id'])).mappings().all()
    assert len(attempts) == 1 and attempts[0]['job_id'] is None
    assert real.review(store, receipt['id'])['status'] == 'checked'


@pytest.mark.parametrize('change', ['expiry', 'revoked', 'integrity', 'settings', 'runtime'])
def test_changed_eligibility_blocks_dispatch(store, change):
    runtime, grant, receipt = granted(store)
    with write_transaction(store) as c:
        if change == 'expiry':
            c.execute(real.grants.update().values(expires_at=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()))
        elif change == 'revoked':
            c.execute(real.grants.update().values(revoked_at=real.now()))
        elif change == 'integrity':
            c.execute(real.grants.update().values(binding='a'*64))
        elif change == 'settings':
            altered = {**grant['snapshot'], 'contract': {**real.contract(), 'base': 'wrong'}}
            c.execute(real.grants.update().values(snapshot=altered, binding=real.digest(altered)))
        else:
            runtime.ready = False
    assert real.process_one(store, runtime)
    assert runtime.calls == 0
    assert real.review(store, receipt['id'])['status'] == 'blocked'
    assert real.submit(store, grant['id'], receipt['id'])['id'] == receipt['id']


def test_concurrent_workers_reserve_only_one_attempt(store):
    runtime, _, _ = granted(store)
    def handle():
        from sqlalchemy.exc import OperationalError
        try:
            return real.process_one(store, runtime)
        except OperationalError:
            return False
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda _: handle(), range(2)))
    assert runtime.calls == 1


def test_inference_outside_transaction_and_late_result_fenced(store):
    runtime, _, receipt = granted(store)
    def execute(identity, fence, permitted):
        assert permitted()
        # A second writer can acquire BEGIN IMMEDIATE during implementation.
        with write_transaction(store) as c:
            c.execute(real.requests.update().where(real.requests.c.id == identity).values(
                fence='recovered', status='interrupted', message='Controlled interrupted result'))
        assert not permitted()
        return dict(verification='passed', candidate_id='controlled')
    runtime.execute = execute
    real.process_one(store, runtime)
    assert real.review(store, receipt['id'])['status'] == 'interrupted'


def test_interrupted_owner_no_replay_and_historical_grant_status(store, monkeypatch):
    runtime, _, receipt = granted(store)
    def crash(identity, fence, permitted):
        raise SystemExit('test crash: transaction already committed')
    runtime.execute = crash
    with pytest.raises(SystemExit):
        real.process_one(store, runtime)
    # A live owner cannot be reclaimed just because a lease/time elapsed.
    real.recover(store, runtime)
    assert real.review(store, receipt['id'])['status'] == 'running'
    monkeypatch.setattr(real, 'alive', lambda _: False)
    real.recover(store, runtime)
    assert real.review(store, receipt['id'])['status'] == 'interrupted'
    assert not real.process_one(store, runtime)
    assert runtime.calls == 0


def test_safe_read_only_review_and_authority_separation(store, client):
    runtime, _, receipt = granted(store)
    real.process_one(store, runtime)
    response = client.get('/api/real-executions/'+receipt['id'])
    assert response.status_code == 200
    assert response.json()['publication_allowed'] is False
    assert response.json()['source'] == 'real'
    assert client.post('/api/real-executions/'+receipt['id'], json={}).status_code != 200


@pytest.mark.parametrize('fault', [None, 'binding', 'readiness', 'intent', 'evidence', 'failed-round', 'original-changed', 'unverified'])
def test_scoped_repair_review_reports_original_reproduction_without_inventing_criterion_coverage(store, monkeypatch, fault):
    from workbench import task_execution

    runtime, grant, receipt = granted(store)
    real.process_one(store, runtime)
    linked = dict(intake_id='controlled-intake', task=dict(id='controlled-task', title='Controlled repair'),
        proposal_review=dict(task_contract=dict(bug_intent={'version': 1}, acceptance=['Unproven separate criterion'])))
    result = dict(verification='passed', candidate_id='controlled',
        manifest=dict(scope_ready_sha256='a'*64, workflow_task_binding=real.digest(linked)),
        independent_check_evidence='working-checks/02', model_check_rounds=[dict(check_round=2, status='pass',
            original_test_differential=dict(status='pass', original_production_unchanged=True, result_sha256='b'*64))])
    if fault == 'binding':
        result['manifest']['workflow_task_binding'] = 'changed'
    elif fault == 'readiness':
        result['manifest'].pop('scope_ready_sha256')
    elif fault == 'intent':
        linked['proposal_review']['task_contract'].pop('bug_intent')
        result['manifest']['workflow_task_binding'] = real.digest(linked)
    elif fault == 'evidence':
        result['independent_check_evidence'] = 'working-checks/01'
    elif fault == 'failed-round':
        result['model_check_rounds'][0]['status'] = 'fail'
    elif fault == 'original-changed':
        result['model_check_rounds'][0]['original_test_differential']['original_production_unchanged'] = False
    elif fault == 'unverified':
        result['verification'] = 'failed'
    monkeypatch.setattr(task_execution, 'current', lambda *_: True)
    with write_transaction(store) as c:
        snapshot = {**grant['snapshot'], 'workflow_task': linked}
        c.execute(real.grants.update().where(real.grants.c.id == grant['id']).values(snapshot=snapshot))
        c.execute(real.requests.update().where(real.requests.c.id == receipt['id']).values(result=result))
    review = real.review(store, receipt['id'])
    assert review['behavior_reproduced'] is (fault is None)
    assert review['behavior_checked'] == []
    assert review['publication_allowed'] is False


def test_create_only_artifacts_reject_partial_conflicting_freeze(tmp_path):
    path = tmp_path/'candidate'/'manifest'
    durable(path, b'complete')
    durable(path, b'complete')
    with pytest.raises(ValueError, match='immutable'):
        durable(path, b'partial')
    assert path.read_bytes() == b'complete'


class Transport:
    responses = []
    bodies = []
    def __init__(self, stop_event):
        self.ends_at = float('inf')
    def __enter__(self):
        return self
    def __exit__(self, *_):
        pass
    def read(self, request, limit, timeout):
        assert request.full_url == 'http://127.0.0.1:8080/v1/chat/completions'
        self.bodies.append(json.loads(request.data))
        return json.dumps(self.responses.pop(0)).encode()


def answer(tokens=20, thinking=10):
    return dict(model='controlled', usage=dict(completion_tokens=tokens, prompt_tokens=10,
        completion_tokens_details=dict(reasoning_tokens=thinking)),
        choices=[dict(finish_reason='stop', message={'content': 'test only'})])


def test_bounded_primary_request_and_actual_usage():
    Transport.responses = [answer()]
    Transport.bodies = []
    result = implement(None, 'controlled test only', 'SECRET-NOT-IN-PROMPT', 'controlled',
                       lambda: True, transport=Transport)
    assert result['completion_tokens'] == 20
    assert Transport.bodies[0]['max_tokens'] == 2496
    assert 'reasoning_budget_tokens' not in Transport.bodies[0]
    assert 'SECRET' not in json.dumps(Transport.bodies)


@pytest.mark.parametrize('tokens,thinking', [(2497, 20), (None, None), (-1, 0)])
def test_unverifiable_or_excess_usage_stops(tokens, thinking):
    Transport.responses = [answer(tokens, thinking)]
    with pytest.raises(BudgetError):
        implement(None, 'test', '', 'controlled', lambda: True, transport=Transport)


def test_no_call_on_revoked_grant():
    Transport.bodies = []
    with pytest.raises(BudgetError):
        implement(None, 'test', '', 'controlled', lambda: False, transport=Transport)
    assert not Transport.bodies


def test_bounded_tools_reject_traversal_and_shell(tmp_path):
    class Runtime:
        workspace = tmp_path
    tools = Tools(Runtime(), lambda: True)
    for name, args in [('read_file', {'path': '../oracle.lua'}),
                       ('edit_file', {'path': '/etc/passwd'}),
                       ('run_check', {'name': 'sh -c evil'}), ('shell', {})]:
        with pytest.raises(ValueError):
            tools.handle(name, args)


def test_historical_success_does_not_confer_current_grant(store):
    runtime, grant, receipt = granted(store)
    real.process_one(store, runtime)
    with write_transaction(store) as c:
        c.execute(real.grants.update().where(real.grants.c.id == grant['id']).values(revoked_at=real.now()))
    view = real.review(store, receipt['id'])
    assert view['status'] == 'checked' and view['result']['verification'] == 'passed'
    assert view['current_grant'] is False and view['publication_allowed'] is False


def test_grant_expires_sixty_minutes_after_issuance(store):
    runtime, grant, _ = granted(store)
    assert datetime.fromisoformat(grant['expires_at'])-datetime.fromisoformat(grant['issued_at']) == timedelta(minutes=60)
    with pytest.raises(ValueError, match='different immutable'):
        real.issue(store, grant['snapshot']['owner_instruction'], 'conflicting owner provenance', runtime)


def test_source_read_budget_and_range_are_enforced(tmp_path):
    class Runtime:
        workspace = tmp_path
    for path in real.PATHS:
        f = tmp_path/path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text('x'*23000)
    tools = Tools(Runtime(), lambda: True)
    assert len(tools.handle('read_file', {'path': real.PATHS[0]})) == 23000
    with pytest.raises(ValueError, match='character limit'):
        tools.handle('read_file', {'path': real.PATHS[1]})
    with pytest.raises(ValueError, match='250 lines'):
        tools.handle('read_file', {'path': real.PATHS[0], 'start': 1, 'end': 251})


def test_twenty_turn_limit_has_no_replacement_attempt():
    class Tools:
        def handle(self, name, args):
            return 'test response'
    response = answer(20, 10)
    response['choices'][0]['message']['tool_calls'] = [
        {'id': 'controlled', 'function': {'name': 'run_check', 'arguments': '{"name":"focused"}'}}]
    response['choices'][0]['finish_reason'] = 'tool_calls'
    Transport.responses = [response]*21
    Transport.bodies = []
    with pytest.raises(BudgetError, match='Twenty-turn'):
        implement(Tools(), 'controlled', '', 'controlled', lambda: True, transport=Transport)
    assert len(Transport.bodies) == 20


def test_total_completion_budget_reduces_remaining_request():
    class Tools:
        def handle(self, name, args):
            return 'test'
    intermediate = answer(2496, 100)
    intermediate['choices'][0]['message']['tool_calls'] = [
        {'id': 'controlled', 'function': {'name': 'run_check', 'arguments': '{"name":"focused"}'}}]
    intermediate['choices'][0]['finish_reason'] = 'tool_calls'
    Transport.responses = [intermediate]*9+[answer(1536, 100)]
    Transport.bodies = []
    result = implement(Tools(), 'test', '', 'controlled', lambda: True, transport=Transport)
    assert result['completion_tokens'] == 24000
    assert [b['max_tokens'] for b in Transport.bodies] == [2496]*9+[1536]


def test_primary_credential_reuses_explicit_private_file_only(tmp_path, monkeypatch):
    from workbench.real_workspace import Runtime
    key = tmp_path/'private-key'
    key.write_text('controlled secret')
    key.chmod(0o600)
    monkeypatch.setenv('WORKBENCH_LOCAL_KEY', str(key))
    assert Runtime(tmp_path).secret == 'controlled secret'
    key.chmod(0o644)
    with pytest.raises(ValueError, match='private owned'):
        Runtime(tmp_path)


def test_runtime_budget_probe_has_no_scripts_package_import_requirement(tmp_path, monkeypatch):
    from workbench import real_workspace as workspace
    monkeypatch.delenv('WORKBENCH_LOCAL_KEY', raising=False)
    monkeypatch.setattr(workspace, 'git', lambda path, *args: real.BASE if args[0]=='rev-parse' else '')
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def read(self, *_):
            return b'{}'
    class Opener:
        def open(self, *args, **kwargs):
            return Response()
    monkeypatch.setattr(workspace.urllib.request, 'build_opener', lambda *args: Opener())
    gates=workspace.Runtime(tmp_path).preflight()
    assert any(g['name']=='aggregate evidence reservation' for g in gates)
    assert any(g['status']=='blocked' for g in gates)


def test_model_tools_cannot_enable_verification_network(tmp_path):
    from workbench.real_workspace import Tools
    class Runtime:
        workspace = tmp_path
        seen = None
        def check(self, argv, cwd, **kwargs):
            self.seen = kwargs
            return {'status':'pass','rc':0}
    runtime=Runtime()
    Tools(runtime,lambda:True).handle('run_check', {'name':'focused','private_loopback':True})
    assert not runtime.seen.get('private_loopback')


def test_lease_deadline_caps_grant_and_cannot_be_extended(store):
    runtime=ControlledRuntime()
    runtime.grant_deadline=datetime.now(timezone.utc)+timedelta(minutes=45)
    grant=real.issue(store,'NON-MODEL bounded lease test','pytest',runtime)
    with store.connect() as c:
        grant=dict(c.execute(select(real.grants).where(real.grants.c.id==grant['id'])).mappings().one())
    assert grant['expires_at']==runtime.grant_deadline.isoformat() and real.valid(grant)
    changed={**grant,'expires_at':(runtime.grant_deadline+timedelta(seconds=1)).isoformat()}
    assert not real.valid(changed)
    assert real.issue(store,'NON-MODEL bounded lease test','pytest',runtime)['id']==grant['id']


@pytest.mark.parametrize('deadline',[datetime.now(timezone.utc)-timedelta(seconds=1),datetime.now()])
def test_invalid_lease_deadline_issues_no_grant(store,deadline):
    runtime=ControlledRuntime()
    runtime.grant_deadline=deadline
    with pytest.raises(ValueError,match='deadline'):
        real.issue(store,'NON-MODEL invalid lease test','pytest',runtime)
    with store.connect() as c:
        assert not c.execute(select(real.grants)).all()


def test_smart_wait_resumes_same_grant_request_and_fence_without_a_second_attempt(store,monkeypatch):
    from workbench.smart_provider import Waiting
    from workbench import smart_coding_wait,ai_strategy
    runtime,grant,receipt=granted(store)
    calls=[]
    def execute(identity,fence,permitted):
        assert permitted()
        calls.append((identity,fence))
        if len(calls)==1:
            runtime.ai_checkpoint=dict(path='/controlled-checkpoint',sha256='a'*64,sequence=1)
            runtime.ai_wait=ai_strategy.Decision(state='waiting',reason='Waiting for verified native allowance.',recheck_at=1).model_dump()
            runtime.ai_wait_usage=dict(status='complete',units='tokens',requests_observed=1,
                values=dict(prompt_tokens=10,completion_tokens=5,total_tokens=15))
            raise Waiting(runtime.ai_wait)
        return dict(verification='passed',inference_ran=False,candidate_id='controlled')
    runtime.execute=execute
    monkeypatch.setattr(smart_coding_wait,'load',lambda *a:dict(sequence=1,waiting_since={}))
    assert real.process_one(store,runtime)
    with store.connect() as c:
        first=dict(c.execute(select(real.requests).where(real.requests.c.id==receipt['id'])).mappings().one())
    assert first['status']=='waiting_ai' and first['finished_at'] is None
    assert first['result']['usage']['values']['total_tokens']==15
    assert real.process_one(store,runtime)
    with store.connect() as c:
        final=c.execute(select(real.requests)).mappings().one()
        attempts=c.execute(select(model_attempts).where(model_attempts.c.execution_id==receipt['id'])).mappings().all()
        grants=c.execute(select(real.grants)).mappings().all()
    assert calls==[(receipt['id'],first['fence'])]*2
    assert final['status']=='checked' and final['started_at']==first['started_at']
    assert len(attempts)==len(grants)==1
    assert not real.process_one(store,runtime)
