"""Current-attempt usage from bound immutable observations; never historical repair."""
import hashlib
import json
from pathlib import Path

from workbench.real_execution import LIMITS


def summarize(root, request_id, fence, model):
    folder = Path(root)/'attempt'
    records, invalid_records = [], []
    known_prompt = known_completion = responses = validated = incomplete_receipts = 0
    unavailable, requests = [], []
    corrections = errors = tool_errors = 0
    correction_unknown = False

    def load(path, turn):
        try:
            if not path.exists():
                return None
            if not path.is_file() or path.is_symlink() or path.stat().st_size > 16384:
                raise ValueError()
            raw = path.read_bytes()
            row = json.loads(raw)
            if (not isinstance(row, dict) or row.get('request_id') != request_id or
                    row.get('fence') != fence or row.get('turn') != turn):
                raise ValueError()
            records.append(dict(path=str(path.relative_to(root)), sha256=hashlib.sha256(raw).hexdigest()))
            return row
        except (OSError, ValueError, RecursionError):
            invalid_records.append(str(path.relative_to(root)))
            return None

    for turn in range(LIMITS['turns']):
        marker = folder/f'request-{turn:02}.json'
        response = folder/f'turn-{turn:02}.json'
        if not marker.exists() and not response.exists():
            continue
        requests.append(turn)
        request, row = load(marker, turn), load(response, turn)
        if row and row.get('response_completed', True) is True:
            responses += 1
        usage = row.get('usage') if row else None
        cap = row.get('request_limit') if row else None
        valid = (row and row.get('usage_validated') is True and row.get('model') == model
                 and isinstance(usage, dict) and type(cap) is int and 0 < cap <= 2496
                 and type(usage.get('completion_tokens')) is int and 0 <= usage['completion_tokens'] <= cap
                 and type(usage.get('prompt_tokens')) is int and usage['prompt_tokens'] >= 0)
        if valid:
            known_prompt += usage['prompt_tokens']
            known_completion += usage['completion_tokens']
            validated += 1
            if row.get('response_completed') is False:
                incomplete_receipts += 1
        if not valid or not request or request.get('request_limit') != cap:
            unavailable.append(turn)
        for index in range(8):
            path = folder/f'tool-{turn:02}-{index:02}.json'
            existed = path.exists()
            event = load(path, turn)
            if event:
                count, observed = event.get('correction_count'), event.get('read_input_errors')
                total_errors = event.get('tool_input_errors', observed)
                if (type(count) is int and 0 <= count <= 2 and type(observed) is int
                        and 0 <= observed <= 3 and type(total_errors) is int
                        and count <= total_errors <= 3 and observed <= total_errors):
                    corrections, errors = max(corrections, count), max(errors, observed)
                    tool_errors = max(tool_errors,total_errors)
                else:
                    correction_unknown = True
            elif existed:
                correction_unknown = True
    complete = bool(requests) and not unavailable
    usage = dict(units='tokens', status='complete' if complete else 'partial' if validated else 'unavailable',
                 values=dict(prompt_tokens=known_prompt if complete else None,
                             completion_tokens=known_completion if complete else None,
                             total_tokens=known_prompt+known_completion if complete else None),
                 known_values=dict(prompt_tokens=known_prompt, completion_tokens=known_completion),
                 requests_observed=len(requests), completed_responses=responses, validated_responses=validated,
                 incomplete_response_receipts=incomplete_receipts,
                 unavailable_turns=unavailable,
                 provenance=dict(method='bound immutable current-attempt observations', request_id=request_id,
                                 fence=fence, records=records, invalid_records=invalid_records))
    return dict(usage=usage, inference_ran=True if responses or validated else None,
                correction_count=None if correction_unknown else corrections,
                read_input_errors=None if correction_unknown else errors,
                tool_input_errors=None if correction_unknown else tool_errors,
                known_correction_count=corrections)
