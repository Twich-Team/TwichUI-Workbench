"""Offline protocol and request-boundary tests. These never start the real CLI."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from workbench import codex_provider as codex
from workbench.api import create_app


def allowance(used=10):
    window = {'usedPercent': used, 'resetsAt': int(time.time()) + 300}
    return {'ordinaryUsageAllowed': True, 'rateLimits': {
        'limitId': 'codex', 'primary': window, 'secondary': window, 'credits': None,
    }}


class Transcript:
    def __init__(self, *, account='chatgpt', limit=None, events=()):
        self.started = False
        self.deadline = time.monotonic() + 30
        self.events = []
        self.turn_events = list(events)
        self.interrupted = []
        self.calls = []
        self.responses = {
            'initialize': {},
            'account/read': {'account': {'type': account}},
            'account/rateLimits/read': allowance() if limit is None else limit,
            'model/list': {'data': [{'model': 'fixture-model', 'hidden': False,
                                     'supportedReasoningEfforts': [{'reasoningEffort': 'low'}]}]},
            'thread/start': {'thread': {'id': 'thread'}, 'model': 'fixture-model',
                             'modelProvider': 'openai', 'cwd': '/work', 'approvalPolicy': 'never',
                             'sandbox': {'type': 'readOnly'}},
            'turn/start': {'turn': {'id': 'turn'}},
        }

    def call(self, method, params):
        self.calls.append((method, params))
        if method == 'turn/start':
            self.events.extend(self.turn_events)
        return self.responses[method]

    def stop(self):
        return False

    def send(self, _payload):
        pass

    def receive(self):
        raise codex.Hold('canceled', started=self.started)

    def interrupt(self, payload):
        self.interrupted.append(payload)


def completed():
    return [
        {'method': 'item/completed', 'params': {'item': {
            'type': 'agentMessage', 'phase': 'final_answer', 'text': json.dumps({'old': 'a', 'new': 'b'}),
        }}},
        {'method': 'thread/tokenUsage/updated', 'params': {'tokenUsage': {'total': {
            'inputTokens': 20, 'outputTokens': 4, 'reasoningOutputTokens': 1, 'totalTokens': 24,
        }}}},
        {'method': 'turn/completed', 'params': {'turn': {'id': 'turn', 'status': 'completed'}}},
    ]


@pytest.mark.parametrize('account,limit,reason', [
    ('apiKey', allowance(), 'authentication'), ('chatgpt', allowance(95), 'capacity'),
    ('chatgpt', {}, 'accounting'), ('chatgpt', allowance() | {'ordinaryUsageAllowed': None}, 'accounting'),
])
def test_account_holds_without_transmitting_task_content(account, limit, reason):
    rpc = Transcript(account=account, limit=limit)
    with pytest.raises(codex.Hold) as caught:
        codex.turn(rpc, 'fixture-model', 'low', 'task source', {})
    assert caught.value.reason == reason and not rpc.started
    assert 'task source' not in repr(rpc.calls)
    assert ('account/read', {'refreshToken': False}) in rpc.calls


def test_single_turn_with_exact_model_and_usage():
    rpc = Transcript(events=completed())
    assert codex.turn(rpc, 'fixture-model', 'low', 'task source', {}) == {
        'output': {'old': 'a', 'new': 'b'},
        'usage': dict(prompt_tokens=20, completion_tokens=4, reasoning_tokens=1, total_tokens=24),
    }
    turns = [p for method, p in rpc.calls if method == 'turn/start']
    assert len(turns) == 1 and turns[0]['effort'] == 'low' and turns[0]['model'] == 'fixture-model'


@pytest.mark.parametrize('events,reason', [
    ([], 'canceled'), ([{'method': 'model/rerouted'}], 'protocol'),
    ([{'method': 'item/started', 'params': {'item': {'type': 'commandExecution'}}}], 'protocol'),
    ([completed()[0], *completed()], 'protocol'), ([completed()[-1]], 'accounting'),
])
def test_uncertain_turn_interrupts_without_replay(events, reason):
    rpc = Transcript(events=events)
    with pytest.raises(codex.Hold) as caught:
        codex.turn(rpc, 'fixture-model', None, 'task source', {})
    assert caught.value.reason == reason and caught.value.started and rpc.started
    assert len(rpc.interrupted) == (0 if caught.value.terminal else 1)
    assert [method for method, _ in rpc.calls].count('turn/start') == 1


def test_lan_requires_exact_socket_peer_authority_tls_and_origin(store, monkeypatch):
    monkeypatch.setenv('WORKBENCH_PRIVATE_LAN_IP', '10.0.0.9')
    monkeypatch.setenv('WORKBENCH_PRIVATE_LAN_CLIENT', '10.0.0.42')
    app = create_app(store)
    with TestClient(app, base_url='https://10.0.0.9:9487', client=('10.0.0.42', 1234)) as client:
        assert client.get('/api/session').status_code == 200
        assert client.get('/api/session', headers={'Origin': 'https://example.invalid'}).status_code == 403
        assert client.get('/api/session', headers={'Host': '127.0.0.1:9487'}).status_code == 400
    with TestClient(app, base_url='https://10.0.0.9:9487', client=('10.0.0.43', 1234)) as client:
        assert client.get('/api/session', headers={'X-Forwarded-For': '10.0.0.42'}).status_code == 403
    with TestClient(app, base_url='http://10.0.0.9:9487', client=('10.0.0.42', 1234)) as client:
        assert client.get('/api/session').status_code == 403

@pytest.mark.parametrize('with_output,terminal', [(False, True), (True, True), (True, False)])
def test_failed_turn_retains_known_usage_and_partial_output(with_output, terminal):
    events = [completed()[1]]
    if with_output:
        events.append({'method': 'item/agentMessage/delta', 'params': {'delta': 'some output'}})
    if terminal:
        events.append({'method': 'turn/completed', 'params': {'turn': {'id': 'turn', 'status': 'failed'}}})
    rpc = Transcript(events=events)
    with pytest.raises(codex.Hold) as caught:
        codex.turn(rpc, 'fixture-model', None, 'task source', {})
    error = caught.value
    assert error.started and error.terminal is terminal and error.partial is with_output
    assert error.counted['total_tokens'] == 24
    assert bool(rpc.interrupted) is not terminal
