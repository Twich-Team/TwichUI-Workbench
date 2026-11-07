"""Read-only, accounted source follow-ups inside one local investigation claim."""
import copy
import hashlib
import json
import re
import time
from uuid import UUID, uuid5

from pydantic import ValidationError

from workbench import bug_investigation as bug, regression_preparation as source
from workbench.bug_evidence import BugPlanningResponse, Reproducer, Step
from workbench.planning_provider import PlanningUnavailable
from workbench.planning_response import Evidence, ResponseValidationError, wire_schema

REQUESTS = bug.INVESTIGATION_REQUESTS
MAX_FILES = 12
READ_CHARS = 45000
WIRE_TEXT = 24000
INSPECT_PATH = re.compile(r'(?:(?:!!!TwichUI|TwichUI_Settings)/[A-Za-z0-9_./!-]+|tests/[A-Za-z0-9_./-]+)\.lua')


def correction(error, raw):
    """Only a finished, single-plan reproducer error may request a new proposal.

    Ambiguous outcomes, unsafe access and scope/binding denials remain terminal.
    This supplies validation facts, never missing behavior or an approved plan.
    """
    if (not isinstance(raw, dict) or set(raw)-{'finding', 'plan', 'question', 'limitation', 'inspection'}
            or not isinstance(raw.get('plan'), dict)
            or any(raw.get(k) is not None for k in ('question', 'limitation', 'inspection'))):
        return None
    rules = {value for function in (Reproducer.validate_reproducer, Step.shape)
             for value in function.__code__.co_consts if isinstance(value, str)}
    rows = []
    if isinstance(error, ValidationError):
        issues = error.errors(include_input=False, include_url=False)
        if not issues or len(issues) > 4:
            return None
        for issue in issues:
            location = issue['loc']
            rule = str(issue.get('ctx', {}).get('error', ''))
            if (location[:2] != ('plan', 'reproducer') or issue['type'] == 'extra_forbidden'
                    or rule == 'Unsafe fixture access path'):
                return None
            # Locations are restricted to schema field names and small indexes;
            # provider-controlled keys/messages never become system instructions.
            known = {'plan', 'reproducer', 'description', 'fixture', 'bootstrap', 'client', 'through_line',
                     'steps', 'observations', 'op', 'path', 'source', 'value', 'args', 'fields', 'method',
                     'after_step', 'expected', 'incorrect', 'checkpoints', 'before_call', 'call', 'observe',
                     'at','results','return_value','client_field','captured_argument','number'}
            if any(not (part in known if type(part) is str else type(part) is int and 0 <= part <= 512)
                   for part in location):
                return None
            rows.append(dict(path=list(location), constraint=issue['type'],
                             rule=rule if rule in rules else 'Conform to the declared field type and constraint.'))
    elif isinstance(error, ResponseValidationError) and str(error) in {
            'The report needs initial and successive action-driven observations.',
            'The report needs a repeated action to check refreshing.'}:
        rows.append(dict(path=list(error.path), constraint='behavior_coverage', rule=str(error)))
    else:
        return None
    previous = json.loads(json.dumps(raw))
    previous.pop('finding', None)
    return dict(rejected=True, approved=False, executed=False, errors=rows,
                previous_candidate=previous if len(json.dumps(previous).encode()) <= 5000 else None,
                note='This completed candidate was rejected locally. Correct it from committed evidence or inspect missing source. '
                     'Never invent an observation, load, intended behavior or permission. All original limits remain.')


def committed(path, base):
    if (not INSPECT_PATH.fullmatch(path)
            or '..' in path.split('/') or any(p.startswith('.') for p in path.split('/'))
            or {'lib', 'libs', 'vendor'} & {p.lower() for p in path.split('/')}):
        raise ValueError('Source inspection is restricted to committed project Lua and tests.')
    root, actual, _ = source.repository()
    if actual != base:
        raise ValueError('The committed project changed during investigation.')
    bug.clean_base(root, base)
    record = source.git(root, 'ls-tree', '-l', base, '--', path).decode().strip()
    metadata, name = record.split('\t') if '\t' in record else ('', '')
    fields = metadata.split()
    if (name != path or len(fields) != 4 or fields[0] not in {'100644', '100755'}
            or fields[1] != 'blob' or int(fields[3]) > 131072):
        raise ValueError('The requested committed source is unavailable.')
    raw = source.blob(root, fields[2])
    lines = raw.decode().splitlines()
    item = dict(path=path, git_blob=fields[2], sha256=hashlib.sha256(raw).hexdigest(),
                file_lines=len(lines), support_only=True, excerpts=[])
    if path.startswith('tests/test_'):
        setup = bug.client_bootstrap(lines)
        if setup:
            item['client_bootstrap'] = setup
            item['client_access_paths'] = bug.client_access_paths(lines, setup)
    elif path.startswith(('!!!TwichUI/', 'TwichUI_Settings/')):
        from workbench.source_context import compiled_shape
        item['top_level_returns_value'] = compiled_shape(raw)[1]
    return raw, item


def search(base, query):
    root, actual, _ = source.repository()
    if actual != base:
        raise ValueError('The committed project changed during search.')
    bug.clean_base(root, base)
    result, scanned = [], 0
    inventory = source.git(root, 'ls-tree', '-rz', '-l', base, '--',
                           '!!!TwichUI', 'TwichUI_Settings', 'tests', max_bytes=1 << 20)
    for entry in inventory.split(b'\0'):
        if not entry:
            continue
        meta, raw_path = entry.split(b'\t', 1)
        mode, kind, oid, size = meta.decode().split()
        path = raw_path.decode()
        if (mode not in {'100644', '100755'} or kind != 'blob' or int(size) > 131072
                or not INSPECT_PATH.fullmatch(path) or any(p.startswith('.') for p in path.split('/'))
                or {'lib', 'libs', 'vendor'} & {p.lower() for p in path.split('/')}):
            continue
        raw = source.blob(root, oid)
        scanned += len(raw)
        if scanned > 16 << 20:
            raise ValueError('The source search exceeded its bounded local scan.')
        for number, line in enumerate(raw.decode().splitlines(), 1):
            if query.casefold() in line.casefold():
                result.append(dict(path=path, line=number, text=line[:280], truncated=len(line)>280))
                if len(result) == 12:
                    return result
    return result


def unread_start(windows, path, start, end):
    """Advance a repeated range past its exact previously returned prefix.

    An entirely cached/narrow reread remains a reread. Only a contiguous prefix
    is skipped; a hole or an uninspected dependency is never inferred away.
    """
    covered = {number for window in windows if window['path'] == path
               for number in range(window['line'], window['through_line']+1)}
    first = start
    while first in covered and first <= end:
        first += 1
    return first if first <= end else start


def verify_packet(initial, supplied):
    if (set(supplied) - set(initial) - {'inspection_windows'}
            or supplied.get('base') != initial['base'] or supplied.get('policy_sha256') != initial['policy_sha256']
            or supplied.get('files', [])[:len(initial['files'])] != initial['files']
            or not len(initial['files']) <= len(supplied['files']) <= MAX_FILES
            or len({f['path'] for f in supplied['files']}) != len(supplied['files'])):
        raise ValueError('The inspected source binding changed.')
    for item in supplied['files'][len(initial['files']):]:
        raw, expected = committed(item['path'], initial['base'])
        lines = raw.decode().splitlines()
        for window in item['excerpts']:
            start, end = window['line'], window['through_line']
            if (not 1 <= start <= end <= len(lines)
                    or window['text'] != '\n'.join(lines[start-1:end])):
                raise ValueError('An inspected source range changed.')
        expected['excerpts'] = item['excerpts']
        if expected != item:
            raise ValueError('An inspected committed dependency changed.')
    windows = supplied.get('inspection_windows', [])
    if len(windows) > REQUESTS*3 or sum(len(w['text']) for w in windows) > READ_CHARS:
        raise ValueError('The committed inspection evidence exceeds its operation bound.')
    for index, window in enumerate(windows):
        raw, item = committed(window['path'], initial['base'])
        lines = raw.decode().splitlines()
        start, end = window['line'], window['through_line']
        if (not 1 <= start <= end < start+80 or end > len(lines)
                or window['sha256'] != item['sha256'] or window['text'] != '\n'.join(lines[start-1:end])):
            raise ValueError('The inspected source evidence changed.')
        if 'requested_through_line' in window:
            wanted = window['requested_through_line']
            if type(wanted) is not int or wanted < end:
                raise ValueError('The source continuation metadata changed.')
            if 'requested_start_line' in window:
                requested = window['requested_start_line']
                if (type(requested) is not int or not 1 <= requested <= start
                        or start != unread_start(windows[:index], window['path'], requested,
                                                 min(wanted, len(lines)))
                        or window.get('cached_prefix_through_line') != (start-1 if start > requested else None)):
                    raise ValueError('The cached source prefix changed.')
            more = end < min(wanted, len(lines))
            if (window.get('file_lines') != len(lines)
                    or window.get('next_line') != (end+1 if more else None)
                    or window['coverage'] != ('partial requested committed lines' if end < wanted else 'requested committed lines')):
                raise ValueError('The source continuation metadata changed.')
    return supplied


def inspect(packet, requests):
    total = 0
    windows, matches = [], []
    for index, request in enumerate(requests):
        if hasattr(request, 'query'):
            found = search(packet['base'], request.query)
            allowance = (6500-total)//max(1, len(requests)-index)
            for match in found:
                if len(match['text']) > allowance:
                    break
                matches.append(match)
                allowance -= len(match['text'])
                total += len(match['text'])
            continue
        try:
            raw, item = committed(request.path, packet['base'])
        except ValueError as error:
            raise ResponseValidationError('The requested committed source is unavailable or disallowed.',
                ['inspection', index, 'path'], 'an allowed regular committed Lua/test file', request.path) from error
        lines = raw.decode().splitlines()
        if not 1 <= request.start <= request.end or request.start > len(lines):
            raise ResponseValidationError('The requested source lines are outside the committed file.',
                ['inspection', index, 'end'], f'committed line range 1..{len(lines)}', request.end)
        first = unread_start([*packet.get('inspection_windows', []), *windows], request.path,
                             request.start, min(request.end, len(lines)))
        end = min(request.end, first+79, len(lines))
        allowance = (6500-total)//max(1, len(requests)-index)
        selected_lines = []
        for line in lines[first-1:end]:
            candidate = '\n'.join([*selected_lines, line])
            if len(candidate) > allowance:
                # A single whole line can use remaining room; never fabricate
                # a complete line by silently cutting its contents.
                if not selected_lines and len(candidate) <= 6500-total:
                    selected_lines.append(line)
                break
            selected_lines.append(line)
        if not selected_lines:
            continue
        text = '\n'.join(selected_lines)
        end = first+len(selected_lines)-1
        total += len(text)
        window = dict(line=first, through_line=end, text=text,
                      requested_start_line=request.start,
                      cached_prefix_through_line=first-1 if first > request.start else None,
                      requested_through_line=request.end, file_lines=len(lines),
                      next_line=end+1 if end < min(request.end, len(lines)) else None,
                      coverage='partial requested committed lines' if end < request.end else 'requested committed lines')
        selected = next((f for f in packet['files'] if f['path'] == request.path), None)
        if selected is None:
            if len(packet['files']) >= MAX_FILES:
                raise ValueError('The bounded investigation already selected twelve files.')
            item['excerpts'] = [window]
            packet['files'].append(item)
        windows.append(dict(path=request.path, sha256=item['sha256'], **window))
    if total > 6500:
        raise ResponseValidationError('The source inspection result exceeds its bounded size.',
            ['inspection'], 'at most 6500 source characters', total)
    packet.setdefault('inspection_windows', []).extend(windows)
    return windows, matches, total


def next_wire(packet, original, feedback, windows, finding, matches=None):
    _, wire = bug.planning_request(packet, original, feedback)
    from workbench.source_memory import retain
    retain(wire, packet, windows, WIRE_TEXT-sum(len(m['text']) for m in (matches or [])))
    wire['inspection_result'] = dict(prior_finding=finding, read_only=True,
        note='Exact committed source windows are supplied within the response limit. Partial ranges include continuation pointers; '
             'a repeated range advances past a contiguous previously returned prefix when unread lines remain. '
             'requested_start_line preserves the request and cached_prefix_through_line identifies that exact prior prefix. '
             'omitted ranges do not establish absence. No addon change or behavioral result has been produced.')
    if matches is not None:
        wire['inspection_result']['search_matches'] = matches
        wire['inspection_result']['search_limit'] = 'At most twelve literal matches; not an exhaustive absence proof.'
    return wire


def generate(adapter, initial, original, feedback, should_stop, expected_model):
    """One claim/provider/deadline, distinct durable receipts and shared usage."""
    packet = copy.deepcopy(initial)
    schema, wire = bug.planning_request(packet, original, feedback)
    if wire['coverage'].get('call_driven'):
        wire['coverage']['require_inspection'] = True
    parent = adapter.response_evidence
    journals, receipts = [], []
    read_chars = sum(len(e['text']) for f in initial['files'] for e in f['excerpts'])
    adapter.ends_at = adapter.ends_at or time.monotonic()+bug.INVESTIGATION_SECONDS
    evidence = parent
    diagnostics = {}
    response_added = True
    original_stop = adapter.stop_event
    rejected = set()

    class Stop:
        def is_set(self):
            return should_stop()

    adapter.stop_event = Stop()

    def observe_usage(facts):
        usage = facts.get('usage')
        values = usage.get('values') if isinstance(usage, dict) else None
        if (isinstance(values, dict) and all(type(values.get(k)) is int and values[k] >= 0
                for k in ('prompt_tokens', 'completion_tokens', 'total_tokens'))
                and values['prompt_tokens']+values['completion_tokens'] == values['total_tokens']):
            receipts.append(usage)
            return True
        return False
    try:
        for index in range(REQUESTS):
            if should_stop() or time.monotonic() >= adapter.ends_at:
                raise PlanningUnavailable('The source investigation was stopped or timed out.', 'cancelled')
            wire['investigation_progress'] = dict(request_number=index+1,max_requests=REQUESTS,
                requests_after_this=REQUESTS-index-1,last_request=index+1==REQUESTS,
                remaining_seconds=max(0,int(adapter.ends_at-time.monotonic())),
                source_characters_read=read_chars,source_characters_remaining=max(0,READ_CHARS-read_chars),
                note='Use retained exact source instead of requesting the same visible lines again. '
                     'Propose a plan once evidence is sufficient; never invent missing behavior to meet a budget.')
            if index and parent:
                identity = str(uuid5(UUID(parent.identifier), f'committed-source-turn-{index}'))
                evidence = Evidence(parent.directory.parents[2], identity, schema,
                                    wire.get('coverage'), wire.get('files'))
                journals.append(evidence)
                parent.record('source-followup', turn=index+1, evidence_id=identity)
            adapter.response_evidence = evidence
            if evidence:
                effective = wire_schema(schema, wire.get('coverage'), wire.get('files'))
                evidence.record('request-schema', schema_sha256=hashlib.sha256(
                    json.dumps(effective, sort_keys=True).encode()).hexdigest(),
                    require_inspection=bool(wire.get('coverage', {}).get('require_inspection')),
                    corrective=bool(wire.get('validation_feedback')),
                    request_number=index+1,requests_after_this=REQUESTS-index-1)
            response_added = False
            try:
                raw, model, basis = adapter.generate(wire, schema, expected_model=expected_model)
            except PlanningUnavailable as error:
                diagnostics = dict(adapter.diagnostics)
                response_added = observe_usage(diagnostics)
                if index:
                    # A later readiness refusal cannot re-enable pre-generation
                    # fallback after an earlier inspection generation.
                    raise PlanningUnavailable('Source investigation stopped after generation began.',
                                               'investigation_stopped') from error
                raise
            diagnostics = dict(adapter.diagnostics)
            response_added = observe_usage(diagnostics)
            if not response_added:
                raise PlanningUnavailable('Source investigation usage is unavailable.', 'accounting')
            try:
                from workbench.bug_wire import compile_setup
                raw, setup_fact = compile_setup(raw, packet['files'])
                if setup_fact and evidence:
                    evidence.record('explicit-setup-compiled', **setup_fact)
                draft = BugPlanningResponse.model_validate(raw)
                if wire['coverage'].get('require_inspection') and draft.inspection is None:
                    raise ResponseValidationError('Stateful investigation must inspect committed source before a plan.',
                        ['inspection'], 'a read-only committed source window', None)
                if draft.inspection is None:
                    # Compile/revalidate only; no reproducer or addon execution
                    # takes place before independent plan and run approvals.
                    bug.resolve(raw, packet, original, feedback)
            except (ValidationError, ResponseValidationError) as error:
                if evidence:
                    evidence.rejected(error, raw)
                facts = correction(error, raw)
                stamp = hashlib.sha256(json.dumps(dict(raw=raw, facts=facts), sort_keys=True).encode()).hexdigest()
                if (facts and index+1 < REQUESTS and stamp not in rejected
                        and diagnostics.get('generation_state') == 'finished'
                        and diagnostics.get('accounting_state') == 'complete'
                        and diagnostics.get('finish_reason') == 'stop'):
                    rejected.add(stamp)
                    wire['validation_feedback'] = facts
                    continue
                raise
            if draft.inspection is None:
                return raw, model, basis, packet
            if should_stop():
                raise PlanningUnavailable('The investigation binding changed before source inspection.', 'cancelled')
            if evidence:
                evidence.record('source-inspection', turn=index+1,
                    windows=[dict(query_sha256=hashlib.sha256(r.query.encode()).hexdigest()) if hasattr(r, 'query')
                             else dict(path=r.path, start=r.start, end=r.end) for r in draft.inspection])
            if index+1 == REQUESTS:
                raise PlanningUnavailable('The bounded investigation needs more source inspection.', 'inspection_limit')
            windows, matches, chars = inspect(packet, draft.inspection)
            read_chars += chars
            if read_chars > READ_CHARS:
                raise PlanningUnavailable('The investigation reached its source-read bound.', 'inspection_limit')
            wire = next_wire(packet, original, feedback, windows, draft.finding, matches)
            wire['inspection_result']['requested'] = [r.model_dump(mode='json') for r in draft.inspection]
            if wire['coverage'].get('call_driven') and not packet.get('inspection_windows'):
                wire['coverage']['require_inspection'] = True
    finally:
        current = dict(adapter.diagnostics)
        # Failed/current uncertain wire activity has no complete aggregate.
        unknown = (current.get('generation_state') in {'uncertain', 'partial'}
                   or current.get('accounting_state') == 'unavailable'
                   or not response_added and current.get('generation_state') != 'not_started')
        summed = {k: sum(r['values'][k] for r in receipts)
                  for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')}
        diagnostics.update(current, investigation_requests=len(receipts), source_read_chars=read_chars)
        if receipts and not unknown:
            diagnostics['usage'] = dict(source='local llama.cpp investigation responses', units='tokens',
                values=summed, requests_observed=len(receipts), max_request_completion_tokens=max(
                    r['values']['completion_tokens'] for r in receipts), status='complete')
            diagnostics['generation_state'] = 'finished'
        elif receipts:
            diagnostics['known_usage'] = json.dumps(summed, sort_keys=True)
            diagnostics['usage'] = None
        adapter.diagnostics = diagnostics
        adapter.response_evidence = parent
        adapter.stop_event = original_stop
        for journal in journals:
            journal.close()
