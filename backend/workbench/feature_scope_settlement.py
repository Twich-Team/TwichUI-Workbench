"""Independently recognize a native pre-send input rejection, retaining NULL usage."""

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
        if (diagnostics.get('generation_state') != 'not_started'
                or diagnostics.get('category') != 'input_budget'
                or attempt['outcome'] != 'failed' or attempt['usage'] is not None
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
