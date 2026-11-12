"""Recognize native pre-send rejection without rewriting original accounting."""

from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from uuid import UUID

from workbench import everyday
from workbench.feature_context_storage import read


def not_started(engine, record, attempt):
    try:
        diagnostics = record.get('diagnostics') or {}
        usage = attempt['usage']
        codex_refusal = (attempt['backend'] == 'codex'
            and diagnostics == dict(generation_state='not_started')
            and usage == dict(source='Workbench verified no Codex turn started', units='tokens',
                status='not_started', requests_observed=0,
                values=dict(prompt_tokens=0, completion_tokens=0, reasoning_tokens=0, total_tokens=0)))
        if ((not codex_refusal and (diagnostics.get('generation_state') != 'not_started'
                or diagnostics.get('category') != 'input_budget'
                or attempt['usage'] is not None))
                or attempt['outcome'] != 'failed'
                or attempt['actual_model'] is not None or not attempt['finished_at']):
            return None
        identity = str(UUID(record['operation_id']))
        roots = [everyday.ROOT]
        for name in everyday.configuration(engine).evidence_roots:
            stage = Path(name)
            # Accounting roots alone are not authority. Historical native
            # receipts must be in retained sibling installation releases.
            if (stage.parent == everyday.ROOT.parent.parent
                    and re.fullmatch(r'everyday-outcome-[a-f0-9]{7,40}', stage.name)):
                roots.append(stage / 'release')
        paths = {root / 'artifacts/runs' / ('planning-evidence-' + identity) / 'events.jsonl'
                 for root in roots}
        found = [path for path in paths if path.exists()]
        if len(found) != 1:
            return None
        raw = read(found[0], 65536)
        events = [json.loads(line) for line in raw.splitlines()]
        if codex_refusal:
            # A trusted zero-request ledger plus a schema-only journal means
            # the local disclosure/format guard refused before connector dispatch.
            # Any observed response or uncertain usage prevents this recovery.
            if len(events) != 1:
                return None
            schema = events[0]
            if (schema.get('phase') != 'schema' or schema.get('model') != 'StepScope'
                    or schema.get('sequence') != 0
                    or schema.get('schema_sha256') != hashlib.sha256(
                        json.dumps(schema['schema'], sort_keys=True).encode()).hexdigest()
                    or not (datetime.fromisoformat(attempt['started_at'])
                        <= datetime.fromisoformat(schema['time'])
                        <= datetime.fromisoformat(attempt['finished_at']))):
                return None
            return dict(kind='native-codex-feature-preflight-refusal-v1', operation_id=identity,
                evidence_path=str(found[0]), evidence_sha256=hashlib.sha256(raw).hexdigest(),
                usage_sha256=hashlib.sha256(json.dumps(usage, sort_keys=True).encode()).hexdigest(),
                no_provider_request=True, original_records_and_zero_usage_retained=True)
        if len(events) != 2 or [e.get('phase') for e in events] != ['schema', 'request-rejected']:
            return None
        schema, rejected = events
        if (schema.get('model') != 'StepScope' or schema.get('sequence') != 0
                or rejected.get('sequence') != 1
                or schema.get('schema_sha256') != hashlib.sha256(
                    json.dumps(schema['schema'], sort_keys=True).encode()).hexdigest()
                or rejected.get('generation_state') != 'not_started'
                or rejected.get('category') != 'input_budget'
                or rejected.get('prepared_input_limit') != 24000
                or type(rejected.get('prepared_input_bytes')) is not int
                or rejected['prepared_input_bytes'] <= 24000
                or any(rejected.get(k) != diagnostics.get(k)
                       for k in ('prepared_input_bytes', 'prepared_input_limit'))
                or not (datetime.fromisoformat(attempt['started_at'])
                        <= datetime.fromisoformat(schema['time'])
                        <= datetime.fromisoformat(rejected['time'])
                        <= datetime.fromisoformat(attempt['finished_at']))):
            return None
        return dict(kind='native-feature-pre-send-rejection-v1', operation_id=identity,
                    evidence_path=str(found[0]), evidence_sha256=hashlib.sha256(raw).hexdigest(),
                    no_provider_request=True, original_records_and_null_usage_retained=True)
    except (ValueError, OSError, KeyError, TypeError):
        return None


def readonly_terminal(engine, record, attempt):
    """Fence a failed tool-free scope turn; retain its unavailable usage forever."""
    try:
        diagnostics = record.get('diagnostics') or {}
        receipt = str(UUID(diagnostics['provider_receipt']))
        identity = str(UUID(record['operation_id']))
        if (attempt['id'] != identity or attempt['backend'] != 'codex'
                or attempt['purpose'] != 'feature_step_scope' or attempt['outcome'] != 'failed'
                or not attempt['finished_at'] or attempt['usage'] is not None
                or diagnostics.get('generation_state') != 'finished'
                or diagnostics.get('provider_terminal_confirmed') is not True
                or diagnostics.get('terminal_status') != 'failed'
                or diagnostics.get('partial_output') is not False):
            return None
        paths = {Path(root)/'artifacts/codex-evidence'/(receipt+'.jsonl')
                 for root in everyday.configuration(engine).evidence_roots}
        found = [path for path in paths if path.exists()]
        if len(found) != 1:
            return None
        raw = read(found[0], 65536)
        events = [json.loads(line) for line in raw.splitlines()]
        identities = {(e.get('thread_id'), e.get('turn_id')) for e in events
                      if e.get('phase') in {'turn-started', 'lifecycle-turn'}}
        terminal = [e for e in events if e.get('phase') == 'lifecycle-terminal']
        held = [e for e in events if e.get('phase') == 'turn-held']
        isolated = [e['capabilities'] for e in events if e.get('phase') == 'isolation-verified']
        if (len(identities) != 1 or not all(all(pair) for pair in identities)
                or len(terminal) != 1 or terminal[0].get('status') != 'failed'
                or (terminal[0].get('thread_id'), terminal[0].get('turn_id')) not in identities
                or terminal[0].get('usage') is not None or len(held) != 1
                or held[0].get('terminal_status') != 'failed' or held[0].get('partial') is not False
                or held[0].get('usage') is not None or held[0].get('output_bytes') != 0
                or len(isolated) != 1 or any(e.get('phase') == 'first-output' for e in events)
                or not any(e.get('phase') == 'connection-closed' for e in events)):
            return None
        capability = isolated[0]
        if (capability.get('kind') != 'tool-free-codex-investigation-v1'
                or not all(capability.get(k) is False for k in (
                    'source_writes', 'execution_dispatch', 'external_action_tools', 'owner_config_mounted'))
                or capability.get('empty_work_directory') is not True
                or capability.get('shell_disabled') is not True
                or capability.get('sandbox') != 'readOnly' or capability.get('approval_policy') != 'never'):
            return None
        authority = everyday.ROOT/'docs/OWNER_READONLY_RECOVERY_20261108.md'
        from workbench.investigation_recovery import AUTHORITY
        authority_sha = hashlib.sha256(authority.read_bytes()).hexdigest()
        if authority_sha != AUTHORITY:
            return None
        return dict(kind='standing-readonly-feature-scope-quarantine-v1', operation_id=identity,
            evidence_path=str(found[0]), evidence_sha256=hashlib.sha256(raw).hexdigest(),
            authority_sha256=authority_sha,
            provider_outcome='failed', usage=None, old_output_reusable=False,
            consequential_capabilities=False, producer_closed=True)
    except (ValueError, OSError, KeyError, TypeError):
        return None


def readonly_partial_check(engine, record, attempt):
    """Closed independent-program response; partial text never becomes a program."""
    try:
        diagnostics = record.get('diagnostics') or {}
        receipt = str(UUID(diagnostics['provider_receipt']))
        if (record['operation_id'] != attempt['id'] or attempt['backend'] != 'codex' or attempt['source'] != 'workbench'
                or attempt['purpose'] != 'feature_check_preparation' or attempt['outcome'] != 'failed'
                or not attempt['finished_at'] or record.get('confirmed_finished') is not False
                or diagnostics.get('generation_state') != 'partial'
                or diagnostics.get('partial_output') is not True
                or diagnostics.get('provider_terminal_confirmed') is not True
                or diagnostics.get('terminal_status') != 'interrupted'
                or not attempt['usage'] or attempt['usage'] != diagnostics.get('usage')):
            return None
        paths = {Path(root)/'artifacts/codex-evidence'/(receipt+'.jsonl')
                 for root in everyday.configuration(engine).evidence_roots}
        found = [path for path in paths if path.exists()]
        if len(found) != 1:
            return None
        raw = read(found[0], 65536)
        events = [json.loads(line) for line in raw.splitlines()]
        isolated = [e.get('capabilities', {}) for e in events if e.get('phase') == 'isolation-verified']
        turns = [e for e in events if e.get('phase') == 'turn-started']
        ends = [e for e in events if e.get('phase') == 'lifecycle-terminal']
        held = [e for e in events if e.get('phase') == 'turn-held']
        interrupted = [e for e in events if e.get('phase') == 'interrupt-result']
        if not all(len(v) == 1 for v in (isolated, turns, ends, held, interrupted)):
            return None
        capability = isolated[0]
        values = attempt['usage']['values']
        from workbench.codex_provider import MAX_OBSERVED, BUG_OBSERVED
        from workbench.visual_scope import digest
        from workbench.investigation_recovery import AUTHORITY
        if (capability.get('kind') != 'tool-free-codex-investigation-v1'
                or any(capability.get(k) is not False for k in (
                    'source_writes', 'execution_dispatch', 'external_action_tools', 'owner_config_mounted'))
                or capability.get('empty_work_directory') is not True or capability.get('shell_disabled') is not True
                or capability.get('sandbox') != 'readOnly' or capability.get('approval_policy') != 'never'
                or not turns[0].get('thread_id') or not turns[0].get('turn_id')
                or any(ends[0].get(k) != turns[0][k] for k in ('thread_id', 'turn_id'))
                or ends[0].get('status') != 'interrupted' or ends[0].get('usage') != values
                or interrupted[0].get('acknowledged') is not True or interrupted[0].get('status') != 'interrupted'
                or interrupted[0].get('reason') != 'accounting' or held[0].get('terminal_status') != 'interrupted'
                or held[0].get('usage') != values or held[0].get('partial') is not True
                or type(held[0].get('output_bytes')) is not int or not 0 < held[0]['output_bytes'] <= 24000
                or events[-1].get('phase') != 'connection-closed'
                or events[-1].get('status') != 'local-producer-closed'
                or set(values) != {'prompt_tokens', 'completion_tokens', 'reasoning_tokens', 'total_tokens'}
                or any(type(v) is not int or not 0 <= v <= BUG_OBSERVED for v in values.values())
                or values['prompt_tokens'] <= MAX_OBSERVED
                or values['total_tokens'] < values['prompt_tokens']+values['completion_tokens']):
            return None
        times = [datetime.fromisoformat(e['time']) for e in events]
        if (times != sorted(times) or not datetime.fromisoformat(attempt['started_at']) <= times[0]
                <= times[-1] <= datetime.fromisoformat(attempt['finished_at'])):
            return None
        authority = everyday.ROOT/'docs/OWNER_READONLY_RECOVERY_20261108.md'
        if hashlib.sha256(authority.read_bytes()).hexdigest() != AUTHORITY:
            return None
        return dict(kind='closed-partial-feature-check-quarantine-v1', operation_id=attempt['id'],
            job_id=attempt['job_id'], record_binding=digest(record), usage_binding=digest(attempt['usage']),
            evidence_path=str(found[0]), evidence_sha256=hashlib.sha256(raw).hexdigest(),
            authority_sha256=AUTHORITY, old_output_reusable=False, producer_closed=True,
            final_usage='not_verified', known_observed_values=values)
    except (ValueError, OSError, KeyError, TypeError):
        return None
