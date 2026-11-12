"""Offline protocol and request-boundary tests. These never start the real CLI."""
import json
import os
import socket
from threading import Thread
import time

import pytest
from fastapi.testclient import TestClient

from workbench import codex_provider as codex
from workbench.api import create_app
import codex_connector as connector
import codex_eligibility_probe as probe


def test_connector_queries_only_the_fixed_hostname_over_unix_socket(tmp_path, monkeypatch):
    path = str(tmp_path / 'resolve.sock')
    monkeypatch.setattr(connector, 'RESOLVER', path)
    requests = []
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(path)
        server.listen()
        server.settimeout(2)

        def reply():
            client, _ = server.accept()
            with client:
                raw = b''
                while not raw.endswith(b'\0'):
                    chunk = client.recv(4096)
                    if not chunk:
                        return
                    raw += chunk
                requests.append(json.loads(raw[:-1]))
                client.sendall(b'{"parameters":{"addresses":[{"family":2,"address":[1,1,1,1]}]}}\0')

        thread = Thread(target=reply, daemon=True)
        thread.start()
        mapping = connector.resolve_backend(lambda: False, time.monotonic() + 2)
        thread.join(timeout=2)
    assert requests == [{'method': 'io.systemd.Resolve.ResolveHostname',
                         'parameters': {'name': 'chatgpt.com', 'family': socket.AF_INET, 'flags': 1}}]
    assert mapping == '1.1.1.1 chatgpt.com\n'
    hosts = tmp_path / 'hosts'
    hosts.write_text(mapping)
    argv = codex.command('/fixture/cli', '/fixture/auth.json', hosts=hosts)
    assert argv[argv.index(str(hosts)) - 1:argv.index(str(hosts)) + 2] == ['--ro-bind', str(hosts), '/etc/hosts']
    assert '--share-net' in argv and '/etc/resolv.conf' not in argv
    assert 'chatgpt_base_url="https://chatgpt.com/backend-api/"' in argv
    assert '--share-net' not in codex.command('/fixture/cli', '/fixture/auth.json')


@pytest.mark.parametrize('address', [[127, 0, 0, 53], [10, 0, 0, 9], [224, 0, 0, 1],
                                     [True, 1, 1, 1], [1, 1, 1], ['1', 1, 1, 1]])
def test_connector_refuses_nonpublic_or_malformed_dns_answer(address):
    with pytest.raises(ValueError):
        connector.host_mapping({'parameters': {'addresses': [{'family': socket.AF_INET, 'address': address}]}})


def test_missing_resolver_fails_before_the_cli_can_start(tmp_path, monkeypatch):
    monkeypatch.setattr(connector, 'RESOLVER', str(tmp_path / 'absent'))
    with pytest.raises(codex.Hold) as caught:
        connector.resolve_backend(lambda: False, time.monotonic() + 2)
    assert caught.value.reason == 'unavailable' and not caught.value.started
    assert caught.value.metadata == {'phase': 'resolve'}


def test_rpc_phase_receipt_omits_server_error_text_and_parameters():
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, b'{"id":1,"error":{"code":-32603,"message":"fixture private error","data":"secret"}}\n')
        channel = codex.Channel(read_fd, lambda _: None, lambda: False, time.monotonic() + 2)
        try:
            with pytest.raises(codex.Hold) as caught:
                channel.call('account/read', {'refreshToken': False})
        finally:
            channel.close()
    finally:
        os.close(read_fd)
        os.close(write_fd)
    assert not caught.value.started
    assert caught.value.metadata == {'phase': 'account/read', 'rpc_code': -32603}
    assert 'secret' not in str(caught.value) and 'fixture private error' not in str(caught.value)


@pytest.mark.parametrize('oversize',[False,True])
def test_app_server_echo_accepts_bounded_bug_input_without_losing_lifecycle(oversize):
    # Real failure: accepted 277192-byte input exceeded the former 262144-byte
    # incoming-frame guard. Exercise the actual pipe reader and a doubly escaped
    # prompt, rather than a fake transport that never enforces frame bounds.
    prompt=json.dumps({'source':'\\"' * 70000})
    assert 262144 < len(prompt.encode()) < codex.MAX_BUG_INPUT
    event=dict(method='item/started',params=dict(item=dict(type='userMessage',content=prompt)))
    raw=json.dumps(event).encode()+b'\n'
    if oversize:
        raw=json.dumps(dict(method='item/started',params={'text':'x'*codex.MAX_LINE})).encode()+b'\n'
    read_fd,write_fd=os.pipe()
    def send():
        try:
            view=memoryview(raw)
            while view:
                view=view[os.write(write_fd,view):]
        except BrokenPipeError:
            pass
        finally:
            os.close(write_fd)
    thread=Thread(target=send,daemon=True)
    thread.start()
    channel=codex.Channel(read_fd,lambda _:None,lambda:False,time.monotonic()+5)
    try:
        if oversize:
            with pytest.raises(codex.Hold) as caught:
                channel.receive()
            assert caught.value.metadata['protocol_guard']=='line_bytes'
        else:
            assert channel.receive()==event
            assert channel.used < codex.MAX_WIRE
        assert codex.MAX_PACKET==24000 and codex.MAX_WIRE==2<<20
    finally:
        channel.close()
        os.close(read_fd)
        thread.join(2)


def allowance(used=10):
    window = {'usedPercent': used, 'resetsAt': int(time.time()) + 300}
    return {'ordinaryUsageAllowed': True, 'rateLimits': {
        'limitId': 'codex', 'primary': window, 'secondary': window.copy(),
        'credits': {'hasCredits': False, 'unlimited': False, 'balance': '0.00'},
    }}


class Transcript:
    def __init__(self, *, account='chatgpt', plan='prolite', limit=None, events=()):
        self.started = False
        self.deadline = time.monotonic() + 30
        self.events = []
        self.turn_events = list(events)
        self.interrupted = []
        self.calls = []
        self.responses = {
            'initialize': {},
            'account/read': {'account': {'type': account, 'planType': plan}},
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
    ('chatgpt', allowance() | {'ordinaryUsageAllowed': False}, 'capacity'),
    ('chatgpt', {}, 'accounting'), ('chatgpt', allowance() | {'ordinaryUsageAllowed': None}, 'accounting'),
])
def test_account_holds_without_transmitting_task_content(account, limit, reason):
    rpc = Transcript(account=account, limit=limit)
    with pytest.raises(codex.Hold) as caught:
        codex.turn(rpc, 'fixture-model', 'low', 'task source', {})
    assert caught.value.reason == reason and not rpc.started
    assert 'task source' not in repr(rpc.calls)
    assert ('account/read', {'refreshToken': False}) in rpc.calls


@pytest.mark.parametrize('change,check,state,reason', [
    (lambda r: r.pop('ordinaryUsageAllowed'), 'ordinary_usage', 'absent', 'accounting'),
    (lambda r: r.update(ordinaryUsageAllowed=None), 'ordinary_usage', 'null', 'accounting'),
    (lambda r: r.update(ordinaryUsageAllowed='true'), 'ordinary_usage', 'ambiguous', 'accounting'),
    (lambda r: r.update(ordinaryUsageAllowed=False), 'ordinary_usage', 'contrary', 'capacity'),
    (lambda r: r.update(rateLimitsByLimitId={'codex': None}), 'quota_bucket', 'null', 'accounting'),
    (lambda r: r.update(rateLimitsByLimitId=['codex']), 'quota_bucket', 'ambiguous', 'accounting'),
    (lambda r: r['rateLimits'].pop('secondary'), 'secondary_window', 'absent', 'accounting'),
    (lambda r: r['rateLimits'].update(secondary=[]), 'secondary_window', 'ambiguous', 'accounting'),
    (lambda r: r['rateLimits']['secondary'].update(usedPercent=90), 'secondary_used', 'contrary', 'capacity'),
    (lambda r: r['rateLimits']['secondary'].update(resetsAt=1), 'secondary_reset', 'contrary', 'accounting'),
    (lambda r: r['rateLimits']['primary'].pop('resetsAt'), 'primary_reset', 'absent', 'accounting'),
    (lambda r: r['rateLimits']['primary'].update(resetsAt=1), 'primary_reset', 'contrary', 'accounting'),
    (lambda r: r['rateLimits']['primary'].update(usedPercent=90), 'primary_used', 'contrary', 'capacity'),
    (lambda r: r['rateLimits']['primary'].update(usedPercent=True), 'primary_used', 'ambiguous', 'accounting'),
    (lambda r: r['rateLimits']['credits'].update(unlimited=True), 'unlimited', 'contrary', 'credits'),
])
def test_pre_generation_hold_identifies_the_exact_redacted_check(change, check, state, reason):
    limits = allowance()
    change(limits)
    rpc = Transcript(limit=limits)
    with pytest.raises(codex.Hold) as caught:
        codex.turn(rpc, 'fixture-model', None, 'private task source', {})
    assert caught.value.reason == reason and not caught.value.started
    assert caught.value.metadata == {'phase': 'account/rateLimits/read', 'check': check, 'state': state}
    assert not {'thread/start', 'turn/start'} & {method for method, _ in rpc.calls}


def test_documented_legacy_bucket_used_only_without_a_keyed_codex_snapshot():
    codex.check_allowance(allowance() | {'rateLimitsByLimitId': {'other': {'limitId': 'other'}}})
    response = allowance()
    response['rateLimitsByLimitId'] = {'codex': response['rateLimits'] | {'secondary': []}}
    with pytest.raises(codex.Hold) as caught:
        codex.check_allowance(response)
    assert caught.value.metadata['check'] == 'secondary_window'


@pytest.mark.parametrize('plan,state', [('unknown', 'ambiguous'), ('free', 'contrary'), (None, 'null')])
def test_unknown_or_out_of_policy_plan_stops_at_account_read(plan, state):
    rpc = Transcript()
    rpc.responses['account/read']['account']['planType'] = plan
    with pytest.raises(codex.Hold) as caught:
        codex.turn(rpc, 'fixture-model', None, 'private task source', {})
    assert caught.value.metadata == {'phase': 'account/read', 'check': 'account_plan', 'state': state}
    assert [method for method, _ in rpc.calls] == ['initialize', 'account/read']


def test_catalog_one_page_is_not_an_entitlement_check():
    response = {'data': [], 'nextCursor': 'opaque'}
    assert codex.catalog_checks(response, 'fixture-model', None)[0]['state'] == 'ambiguous'
    assert codex.catalog_checks({'data': [{'model': 'fixture-model'}]}, 'fixture-model', None)[0]['state'] == 'absent'
    assert codex.catalog_checks({'data': [{'model': 'fixture-model', 'hidden': False}]},
                                'fixture-model', None)[0]['reason'] is None


@pytest.mark.parametrize('ordinary', [True, None])
def test_read_only_probe_uses_shared_checks_and_never_sends_turn_or_private_fields(ordinary):
    limit = allowance() | {'ordinaryUsageAllowed': ordinary, 'accountId': 'fixture-private-account'}
    limit['rateLimits']['secondary'] = None
    rpc = Transcript(limit=limit)
    rpc.responses['account/read']['account']['email'] = 'fixture-private-email'
    rpc.responses['model/list']['data'][0]['model'] = probe.MODEL
    rows = []
    hold = probe.inspect_eligibility(rpc, lambda event, **values: rows.append(dict(event=event, **values)))
    expected = ['initialize', 'account/read', 'account/rateLimits/read', 'model/list']
    if ordinary:
        expected += ['account/rateLimits/read']
    assert [method for method, _ in rpc.calls] == expected
    assert hold is None if ordinary else hold == {'check': 'ordinary_usage', 'state': 'null', 'reason': 'accounting'}
    assert 'fixture-private' not in json.dumps(rows)


def test_read_only_probe_does_not_continue_after_unsettled_account_rpc():
    rpc = Transcript()
    original_call = rpc.call

    def lost(method, params):
        if method == 'account/read':
            raise codex.Hold('timeout', phase=method)
        return original_call(method, params)

    rpc.call = lost
    rows = []
    with pytest.raises(codex.Hold, match='time limit'):
        probe.inspect_eligibility(rpc, lambda event, **values: rows.append(dict(event=event, **values)))
    assert rows[-1] == {'event': 'read_attempt', 'method': 'account/read'}
    assert not any(row['event'] == 'read_response' for row in rows)


@pytest.mark.parametrize('secondary_is_null', [False, True])
def test_single_turn_with_exact_model_and_usage(secondary_is_null):
    limit = allowance()
    if secondary_is_null:
        limit['rateLimits']['secondary'] = None
    update = {'method': 'account/rateLimits/updated', 'params': {'rateLimits': limit['rateLimits']}}
    rpc = Transcript(limit=limit, events=[update, *completed()])
    rpc.events.append(update)
    assert codex.turn(rpc, 'fixture-model', 'low', 'task source', {}) == {
        'output': {'old': 'a', 'new': 'b'},
        'usage': dict(prompt_tokens=20, completion_tokens=4, reasoning_tokens=1, total_tokens=24),
    }
    turns = [p for method, p in rpc.calls if method == 'turn/start']
    assert len(turns) == 1 and turns[0]['effort'] == 'low' and turns[0]['model'] == 'fixture-model'
    assert turns[0]['serviceTierForTurn'] == 'default'
    methods = [method for method, _ in rpc.calls]
    assert methods[:methods.index('turn/start')].count('account/rateLimits/read') == 2


@pytest.mark.parametrize('tokens',[66618,150000,codex.BUG_OBSERVED,codex.BUG_OBSERVED+1])
@pytest.mark.parametrize('title',['InvestigationResponse','BugIntentResponse','ProgramResponse','ReviewResponse'])
def test_larger_context_uses_current_post_observation_guard_without_a_billing_guarantee(tokens,title):
    events=completed()
    values=events[1]['params']['tokenUsage']['total']
    values.update(inputTokens=tokens-4,totalTokens=tokens)
    rpc=Transcript(events=events)
    if tokens<=codex.BUG_OBSERVED:
        result=codex.turn(rpc,'fixture-model',None,'committed context',dict(title=title))
        assert result['usage']['total_tokens']==tokens and not rpc.interrupted
    else:
        with pytest.raises(codex.Hold) as held:
            codex.turn(rpc,'fixture-model',None,'committed context',dict(title=title))
        assert held.value.reason=='accounting' and held.value.counted['total_tokens']==tokens
        assert len(rpc.interrupted)==1
    assert len([m for m,_ in rpc.calls if m=='turn/start'])==1
    assert len([m for m,_ in rpc.calls if m=='account/rateLimits/read'])==2


@pytest.mark.parametrize('denied_read', [1, 2])
@pytest.mark.parametrize('ordinary,reason', [(False, 'capacity'), (None, 'accounting'), ('timeout', 'timeout')])
def test_null_secondary_requires_both_allowance_reads_to_affirm_usage(denied_read, ordinary, reason):
    limit = allowance()
    limit['rateLimits']['secondary'] = None
    rpc = Transcript(limit=limit)
    prior_call = rpc.call

    def call(method, params):
        result = prior_call(method, params)
        if method == 'account/rateLimits/read' and sum(name == method for name, _ in rpc.calls) == denied_read:
            if ordinary == 'timeout':
                raise codex.Hold('timeout', phase=method)
            return {**result, 'ordinaryUsageAllowed': ordinary}
        return result

    rpc.call = call
    with pytest.raises(codex.Hold) as caught:
        codex.turn(rpc, 'fixture-model', None, 'private task source', {})
    assert caught.value.reason == reason and not rpc.started
    assert 'private task source' not in repr(rpc.calls)


def test_null_secondary_does_not_pass_on_a_pushed_snapshot_alone():
    limit = allowance()['rateLimits'] | {'secondary': None}
    with pytest.raises(codex.Hold) as caught:
        codex.check_snapshot(limit)
    assert caught.value.metadata['check'] == 'secondary_window'


@pytest.mark.parametrize('events,reason', [
    ([], 'canceled'), ([{'method': 'model/rerouted'}], 'protocol'),
    ([{'method': 'item/started', 'params': {'item': {'type': 'commandExecution'}}}], 'protocol'),
    ([{'method': 'error', 'params': {'willRetry': True}}], 'protocol'),
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
    monkeypatch.setenv('WORKBENCH_PRIVATE_LAN_SUBNET', '10.0.0.0/24')
    app = create_app(store)
    with TestClient(app, base_url='https://10.0.0.9:9487', client=('10.0.0.42', 1234)) as client:
        assert client.get('/api/session').status_code == 200
        assert client.get('/api/session', headers={'Origin': 'https://example.invalid'}).status_code == 403
        assert client.get('/api/session', headers={'Host': '127.0.0.1:9487'}).status_code == 400
    with TestClient(app, base_url='https://10.0.0.9:9487', client=('10.0.0.254', 1234)) as client:
        assert client.get('/api/session').status_code == 200
    for peer in ('10.0.1.42', '10.0.0.0', '10.0.0.255', '192.168.1.42', '2001:db8::42'):
        with TestClient(app, base_url='https://10.0.0.9:9487', client=(peer, 1234)) as client:
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

@pytest.mark.parametrize('credits', [
    None, {}, {'hasCredits': True, 'unlimited': False},
    {'hasCredits': False, 'unlimited': True},
    {'hasCredits': False, 'unlimited': False, 'balance': '1.00'},
])
def test_unknown_or_available_credits_hold_before_sending_source(credits):
    limit = allowance()
    limit['rateLimits']['credits'] = credits
    limit['rateLimits']['secondary'] = None
    rpc = Transcript(limit=limit)
    with pytest.raises(codex.Hold) as caught:
        codex.turn(rpc, 'fixture-model', None, 'task source', {})
    assert caught.value.reason == 'credits' and not rpc.started
    assert 'task source' not in repr(rpc.calls)


def test_credit_change_before_turn_holds_before_sending_source():
    rpc = Transcript()
    prior_call = rpc.call

    def call(method, params):
        if method == 'account/rateLimits/read' and any(m == method for m, _ in rpc.calls):
            rpc.responses[method]['rateLimits']['credits']['hasCredits'] = True
        return prior_call(method, params)

    rpc.call = call
    with pytest.raises(codex.Hold) as caught:
        codex.turn(rpc, 'fixture-model', None, 'task source', {})
    assert caught.value.reason == 'credits' and not rpc.started
    assert 'task source' not in repr(rpc.calls)


def test_null_secondary_keeps_the_primary_reserve():
    limit = allowance(90)
    limit['rateLimits']['secondary'] = None
    rpc = Transcript(limit=limit)
    with pytest.raises(codex.Hold) as caught:
        codex.turn(rpc, 'fixture-model', None, 'private task source', {})
    assert caught.value.reason == 'capacity' and not rpc.started
    assert caught.value.metadata['check'] == 'primary_used'
    assert 'private task source' not in repr(rpc.calls)


def test_credit_change_during_turn_interrupts_without_replay():
    limit = allowance()['rateLimits']
    limit['secondary'] = None
    limit['credits']['hasCredits'] = True
    rpc = Transcript(events=[{'method': 'account/rateLimits/updated', 'params': {'rateLimits': limit}}])
    with pytest.raises(codex.Hold) as caught:
        codex.turn(rpc, 'fixture-model', None, 'task source', {})
    assert caught.value.reason == 'credits' and caught.value.started and not caught.value.terminal
    assert len(rpc.interrupted) == 1


def test_workspace_billing_does_not_admit_a_turn():
    rpc = Transcript(plan='self_serve_business_usage_based')
    with pytest.raises(codex.Hold) as caught:
        codex.turn(rpc, 'fixture-model', None, 'task source', {})
    assert caught.value.reason == 'accounting' and not rpc.started
    assert 'task source' not in repr(rpc.calls)


@pytest.mark.parametrize('network', ['10.0.0.0/8', '10.0.1.0/24', '0.0.0.0/0', '10.0.0.1/24'])
def test_lan_rejects_broad_mismatched_and_noncanonical_networks(monkeypatch, network):
    from workbench.private_lan import configured

    monkeypatch.setenv('WORKBENCH_PRIVATE_LAN_IP', '10.0.0.9')
    monkeypatch.setenv('WORKBENCH_PRIVATE_LAN_SUBNET', network)
    with pytest.raises(ValueError):
        configured()


def test_bug_input_roundtrip_is_larger_without_widening_output_or_legacy_limit(
    tmp_path, monkeypatch
):
    path = tmp_path / "connector.sock"
    monkeypatch.setattr(codex, "connector_path", lambda: path)
    observed = []
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(str(path))
        listener.listen()

        def reply():
            peer, _ = listener.accept()
            with peer:
                raw = peer.makefile("rb").readline(codex.MAX_BUG_INPUT + 2)
                observed.append(json.loads(raw))
                peer.sendall(
                    json.dumps(
                        dict(
                            output=dict(answer="controlled"),
                            usage=dict(
                                prompt_tokens=40,
                                completion_tokens=10,
                                total_tokens=50,
                                reasoning_tokens=0,
                            ),
                        )
                    ).encode()
                    + b"\n"
                )

        thread = Thread(target=reply, daemon=True)
        thread.start()
        output, usage = codex.generate(
            "fixture-model", None, "source" * 6000, {}, input_limit=codex.MAX_BUG_INPUT
        )
        thread.join(2)
    assert output == dict(answer="controlled") and usage["total_tokens"] == 50
    assert len(observed) == 1 and len(observed[0]["prompt"]) > 24000
    assert codex.MAX_PACKET == 24000 and codex.MAX_BUG_INPUT == 393216
    with pytest.raises(ValueError):
        codex.generate("fixture-model", None, "source" * 6000, {})
    with pytest.raises(ValueError):
        codex.generate(
            "fixture-model", None, "x" * codex.MAX_BUG_INPUT, {}, input_limit=codex.MAX_BUG_INPUT
        )


@pytest.mark.parametrize('seconds', [None,120,300,301,True])
def test_connector_accepts_bounded_bug_context_only_from_allowed_peer_without_starting_real_cli(
    tmp_path, monkeypatch, seconds
):
    from contextlib import contextmanager
    from types import SimpleNamespace

    monkeypatch.setattr(connector, "allowed_cgroup", lambda group, uid: True)
    monkeypatch.setattr(
        connector, "resolve_backend", lambda stop, deadline: "1.1.1.1 chatgpt.com\n"
    )

    @contextmanager
    def app_server(*args):
        yield SimpleNamespace(started=False)

    monkeypatch.setattr(codex, "app_server", app_server)
    seen = []

    def turn(rpc, **body):
        seen.append(body)
        return dict(
            output=dict(ok=True),
            usage=dict(prompt_tokens=40, completion_tokens=10, total_tokens=50),
        )

    monkeypatch.setattr(codex, "turn", turn)
    left, right = socket.socketpair()
    with left, right:
        thread = Thread(
            target=connector.handle,
            args=(right, "/fixture/cli", "/fixture/auth", tmp_path, tmp_path / "evidence"),
            daemon=True,
        )
        thread.start()
        left.sendall(
            json.dumps(
                dict(model="fixture-model", effort=None, prompt="source" * 6000, schema={},
                     **({"request_seconds":seconds} if seconds is not None else {}))
            ).encode()
            + b"\n"
        )
        result = json.loads(left.makefile("rb").readline())
        thread.join(2)
    if seconds is True or seconds == 301:
        assert result['error']=='protocol' and not result['started'] and not seen
        assert not (tmp_path/'evidence').exists()
    else:
        assert result["output"] == dict(ok=True) and len(seen) == 1 and len(seen[0]["prompt"]) > 24000
        receipt=tmp_path/'evidence'/(result['provider_receipt']+'.jsonl')
        assert receipt.stat().st_mode&0o777==0o600
        assert 'source'*10 not in receipt.read_text()
        assert 'request_sha256' in receipt.read_text()
