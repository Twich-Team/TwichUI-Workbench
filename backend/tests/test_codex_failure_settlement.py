"""A stopped response cannot be renewed while output/effects remain uncertain."""
import copy

import pytest

from workbench.codex_failure_settlement import terminal
from workbench.real_workspace import sha


def transcript():
    values = [
        dict(phase='request', request_sha256='a'*64),
        dict(phase='isolation-verified', capabilities=dict(kind='tool-free-codex-investigation-v1',
            source_writes=False, execution_dispatch=False, external_action_tools=False,
            owner_config_mounted=False, empty_work_directory=True, shell_disabled=True,
            sandbox='readOnly', approval_policy='never')),
        dict(phase='turn-started', thread_id='thread', turn_id='turn'),
        dict(phase='lifecycle-terminal', thread_id='thread', turn_id='turn', status='interrupted', usage=None),
        dict(phase='interrupt-result', acknowledged=True, status='interrupted'),
        dict(phase='turn-held', terminal_status='interrupted', partial=False, usage=None,
            output_bytes=0, output_sha256=sha(b''), output_excerpt=''),
        dict(phase='connection-closed', status='local-producer-closed'),
    ]
    return [dict(v, time=f'2026-11-12T15:25:{20+i:02d}+00:00') for i, v in enumerate(values)]


def check(events):
    terminal(events, 'a'*64, '2026-11-12T15:25:17+00:00', '2026-11-12T15:25:25+00:00')


def test_closed_response_can_drain_after_local_stop_without_claiming_zero_usage():
    events = transcript()
    original = copy.deepcopy(events)
    check(events)
    assert events == original and events[3]['usage'] is None


@pytest.mark.parametrize('fault', ['different_request', 'source_write', 'dispatch', 'shell',
    'mounted_config', 'open_producer', 'no_cancel_ack', 'partial', 'output', 'late_output',
    'different_turn', 'different_thread', 'known_usage', 'duplicate_turn', 'outside_interval',
    'unbounded_drain', 'missing_terminal'])
def test_uncertain_or_consequential_response_stays_held(fault):
    events = transcript()
    if fault == 'different_request':
        events[0]['request_sha256'] = 'b'*64
    elif fault == 'source_write':
        events[1]['capabilities']['source_writes'] = True
    elif fault == 'dispatch':
        events[1]['capabilities']['execution_dispatch'] = True
    elif fault == 'shell':
        events[1]['capabilities']['shell_disabled'] = False
    elif fault == 'mounted_config':
        events[1]['capabilities']['owner_config_mounted'] = True
    elif fault == 'open_producer':
        events.pop()
    elif fault == 'no_cancel_ack':
        events[4]['acknowledged'] = False
    elif fault == 'partial':
        events[5]['partial'] = True
    elif fault == 'output':
        events[5]['output_bytes'] = 1
    elif fault == 'late_output':
        events.insert(-1, dict(phase='first-output', time=events[-1]['time']))
    elif fault == 'different_turn':
        events[3]['turn_id'] = 'other'
    elif fault == 'different_thread':
        events[3]['thread_id'] = 'other'
    elif fault == 'known_usage':
        events[3]['usage'] = dict(total_tokens=10)
    elif fault == 'duplicate_turn':
        events.insert(3, copy.deepcopy(events[2]))
    elif fault == 'outside_interval':
        events[0]['time'] = '2026-11-12T15:25:16+00:00'
    elif fault == 'unbounded_drain':
        events[-1]['time'] = '2026-11-12T15:25:46+00:00'
    elif fault == 'missing_terminal':
        events.pop(3)
    with pytest.raises(ValueError):
        check(events)
