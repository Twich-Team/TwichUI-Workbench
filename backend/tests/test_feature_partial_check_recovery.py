"""Partial read-only text stays unusable after verified producer termination."""
import copy
import json
from pathlib import Path
from uuid import uuid4

import pytest

from workbench import everyday
from workbench.feature_scope_settlement import readonly_partial_check
from test_everyday import installed as installed


@pytest.mark.parametrize('fault', [None, 'write', 'open', 'other_turn', 'usage',
    'no_ack', 'not_partial', 'different_purpose', 'outside_interval', 'small_context'])
def test_closed_partial_check_preserves_usage_and_never_promotes_output(store, installed, fault):
    identity, receipt = str(uuid4()), str(uuid4())
    values = dict(prompt_tokens=55000, completion_tokens=550, reasoning_tokens=428, total_tokens=55550)
    if fault == 'small_context':
        values.update(prompt_tokens=20000, total_tokens=20550)
    usage = dict(source='controlled_fixture', units='tokens', values=values)
    attempt = dict(id=identity, job_id=str(uuid4()), source='workbench', backend='codex',
        purpose='feature_check_preparation', outcome='failed',
        started_at='2026-11-12T16:00:00+00:00', finished_at='2026-11-12T16:00:10+00:00', usage=usage)
    record = dict(operation_id=identity, confirmed_finished=False, diagnostics=dict(
        provider_receipt=receipt, generation_state='partial', partial_output=True,
        provider_terminal_confirmed=True, terminal_status='interrupted', usage=copy.deepcopy(usage)))
    capability = dict(kind='tool-free-codex-investigation-v1', source_writes=False,
        execution_dispatch=False, external_action_tools=False, owner_config_mounted=False,
        empty_work_directory=True, shell_disabled=True, sandbox='readOnly', approval_policy='never')
    events = [dict(phase='request'), dict(phase='isolation-verified', capabilities=capability),
        dict(phase='turn-started', thread_id='thread', turn_id='turn'),
        dict(phase='lifecycle-terminal', thread_id='thread', turn_id='turn', status='interrupted', usage=values),
        dict(phase='interrupt-result', acknowledged=True, status='interrupted', reason='accounting'),
        dict(phase='turn-held', terminal_status='interrupted', usage=values, partial=True, output_bytes=470),
        dict(phase='connection-closed', status='local-producer-closed')]
    for i, event in enumerate(events):
        event['time'] = f'2026-11-12T16:00:0{i+1}+00:00'
    if fault == 'write':
        capability['source_writes'] = True
    elif fault == 'open':
        events.pop()
    elif fault == 'other_turn':
        events[3]['turn_id'] = 'other'
    elif fault == 'usage':
        record['diagnostics']['usage']['values']['total_tokens'] += 1
    elif fault == 'no_ack':
        events[4]['acknowledged'] = False
    elif fault == 'not_partial':
        record['diagnostics']['partial_output'] = False
    elif fault == 'different_purpose':
        attempt['purpose'] = 'implementation'
    elif fault == 'outside_interval':
        events[-1]['time'] = '2026-11-12T16:00:11+00:00'
    folder = everyday.ROOT/'artifacts/codex-evidence'
    folder.mkdir(mode=0o700, parents=True)
    path = folder/(receipt+'.jsonl')
    path.write_text(''.join(json.dumps(e)+'\n' for e in events))
    path.chmod(0o600)
    authority = everyday.ROOT/'docs/OWNER_READONLY_RECOVERY_20261108.md'
    authority.parent.mkdir(exist_ok=True)
    authority.write_bytes((Path(__file__).resolve().parents[2]/'docs'/authority.name).read_bytes())
    before = copy.deepcopy((record, attempt))
    proof = readonly_partial_check(store, record, attempt)
    assert (record, attempt) == before
    assert bool(proof) is (fault is None)
    if proof:
        assert proof['old_output_reusable'] is False and proof['final_usage'] == 'not_verified'
        assert record['confirmed_finished'] is False
