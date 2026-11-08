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
from workbench import bug_plan_review, bug_investigation_stage as stage

REQUESTS = bug.INVESTIGATION_REQUESTS
MAX_FILES = 12
READ_CHARS = 45000
WIRE_TEXT = 24000
INSPECT_PATH = re.compile(r'(?:(?:!!!TwichUI|TwichUI_Settings)/[A-Za-z0-9_./!-]+|tests/[A-Za-z0-9_./-]+)\.lua')


def read_limit(packet):
    return 262144 if packet.get('plan_review') == 'source-grounded-lua-v1' else READ_CHARS


def window_limit(packet):
    return 256 if packet.get('plan_review') == 'source-grounded-lua-v1' else REQUESTS*3


def source_characters(packet):
    """Unique exact source footprint; actual repeated prompt tokens stay accounted."""
    lines = {}
    for item in [*packet['files'], *packet.get('api_references', [])]:
        for excerpt in item['excerpts']:
            for offset, text in enumerate(excerpt['text'].splitlines()):
                key = (item['path'], excerpt['line']+offset)
                if key in lines and lines[key] != text:
                    raise ValueError('Committed source footprint disagrees')
                lines[key] = text
    for excerpt in packet.get('inspection_windows', []):
        for offset, text in enumerate(excerpt['text'].splitlines()):
            key = (excerpt['path'], excerpt['line']+offset)
            if key in lines and lines[key] != text:
                raise ValueError('Committed source footprint disagrees')
            lines[key] = text
    return sum(len(text)+1 for text in lines.values())


def correction(error, raw):
    """Only finished single-plan wording/reproducer errors may be corrected.

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
            known = {'plan', 'scope', 'reproducer', 'description', 'fixture', 'bootstrap', 'client', 'through_line',
                     'steps', 'observations', 'op', 'path', 'source', 'value', 'args', 'fields', 'method',
                     'after_step', 'expected', 'incorrect', 'checkpoints', 'before_call', 'call', 'observe',
                     'at','results','return_value','client_field','captured_argument','number',
                     'returns','values','table_fields','phases','code','event_coverage'}
            if any(not (part in known if type(part) is str else type(part) is int and 0 <= part <= 512)
                   for part in location):
                return None
            rows.append(dict(path=list(location), constraint=issue['type'],
                             rule=rule if rule in rules else 'Conform to the declared field type and constraint.'))
    elif isinstance(error, ResponseValidationError) and str(error) == 'The proposed reproduction contains invalid Lua syntax.':
        rows.append(dict(path=list(error.path),constraint='Lua_syntax',rule=error.expected))
    elif isinstance(error, ResponseValidationError) and str(error) in {
            'The report needs initial and successive action-driven observations.',
            'The report needs a repeated action to check refreshing.',
            'Describe the bounded repair in plain language, not only a file path'}:
        rows.append(dict(path=list(error.path), constraint='behavior_coverage', rule=str(error)))
    else:
        return None
    previous = json.loads(json.dumps(raw))
    previous.pop('finding', None)
    return dict(rejected=True, approved=False, executed=False, errors=rows,
                previous_candidate=previous if len(json.dumps(previous).encode()) <= (10000 if raw['plan'].get('reproducer', {}).get('phases') else 5000) else None,
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


def search(base, query, paths=None):
    root, actual, _ = source.repository()
    if actual != base:
        raise ValueError('The committed project changed during search.')
    bug.clean_base(root, base)
    result, scanned = [], 0
    inventory = source.git(root, 'ls-tree', '-rz', '-l', base, '--',
                           *(sorted(paths) if paths is not None else ['!!!TwichUI', 'TwichUI_Settings', 'tests']), max_bytes=1 << 20)
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
    if (set(supplied) - set(initial) - {'inspection_windows','inspection_searches'}
            or supplied.get('base') != initial['base'] or supplied.get('policy_sha256') != initial['policy_sha256']
            or supplied.get('plan_review') != initial.get('plan_review')
            or supplied.get('api_references') != initial.get('api_references')
            or supplied.get('declarations') != initial.get('declarations')
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
    if len(windows) > window_limit(supplied) or (source_characters(supplied) > read_limit(supplied)
            if supplied.get('plan_review') == 'source-grounded-lua-v1' else sum(len(w['text']) for w in windows) > READ_CHARS):
        raise ValueError('The committed inspection evidence exceeds its operation bound.')
    for index, window in enumerate(windows):
        raw, item = committed(window['path'], initial['base'])
        lines = raw.decode().splitlines()
        start, end = window['line'], window['through_line']
        if (not 1 <= start <= end < start+160 or end > len(lines)
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
    from workbench.investigation_notebook import verify
    verify(initial,supplied,search)
    return supplied


def inspect(packet, requests, *, disclosure_paths=None, page_lines=80, result_budget=6500):
    total = 0
    windows, matches = [], []
    for index, request in enumerate(requests):
        if hasattr(request, 'query'):
            found = search(packet['base'], request.query, disclosure_paths) if disclosure_paths is not None else search(packet['base'], request.query)
            from workbench.investigation_notebook import record
            record(packet,request.query,found,disclosure_paths)
            allowance = (result_budget-total)//max(1, len(requests)-index)
            for match in found:
                if len(match['text']) > allowance:
                    break
                matches.append(match)
                allowance -= len(match['text'])
                total += len(match['text'])
            continue
        if disclosure_paths is not None and request.path not in disclosure_paths:
            raise PlanningUnavailable('This investigation read needs additional source disclosure permission.', 'disclosure')
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
        end = min(request.end, first+page_lines-1, len(lines))
        allowance = (result_budget-total)//max(1, len(requests)-index)
        selected_lines = []
        for line in lines[first-1:end]:
            candidate = '\n'.join([*selected_lines, line])
            if len(candidate) > allowance:
                # A single whole line can use remaining room; never fabricate
                # a complete line by silently cutting its contents.
                if not selected_lines and len(candidate) <= result_budget-total:
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
    if total > result_budget:
        raise ResponseValidationError('The source inspection result exceeds its bounded size.',
            ['inspection'], f'at most {result_budget} source characters', total)
    packet.setdefault('inspection_windows', []).extend(windows)
    return windows, matches, total


def next_wire(packet, original, feedback, windows, finding, matches=None, *, programs=(), requested=(), text_budget=None, packing_context=None):
    _, wire = bug.planning_request(packet, original, feedback)
    from workbench.source_memory import retain, requested_visibility
    if packet.get('plan_review')=='source-grounded-lua-v1':
        from workbench.source_interface import context
        priority,interface=context(packet,original,programs)
        windows=[*windows,*priority]
        wire['selected_source_interface']=interface
        wire['requested_behavior_clauses']=[s.strip() for s in re.split(r'(?<=[.!?])\s+',original['description']) if s.strip()][:12]
    if packing_context is not None:
        packing_context.clear()
        packing_context.update(windows=list(windows),requested=list(requested))
    retain(wire, packet, windows, (text_budget if text_budget is not None else 36000 if packet.get('plan_review') == 'source-grounded-lua-v1' else WIRE_TEXT)
           -sum(len(m['text']) for m in (matches or [])), requested=requested)
    wire['inspection_result'] = dict(prior_finding=finding, read_only=True,
        note='Exact committed source windows are supplied within the response limit. Partial ranges include continuation pointers; '
             'a repeated range advances past a contiguous previously returned prefix when unread lines remain. '
             'requested_start_line preserves the request and cached_prefix_through_line identifies that exact prior prefix. '
             'omitted ranges do not establish absence. No addon change or behavioral result has been produced.')
    wire['inspection_result']['visible_requested_windows'] = requested_visibility(wire, requested)
    if packet.get('inspection_searches'):
        from workbench.investigation_notebook import wire as completed_searches
        wire['completed_searches'] = completed_searches(packet)
    if matches is not None:
        wire['inspection_result']['search_matches'] = matches
        wire['inspection_result']['search_limit'] = 'At most twelve literal matches; not an exhaustive absence proof.'
    return wire


def generate(adapter, initial, original, feedback, should_stop, expected_model, *, engine=None, max_requests=None):
    """One claim/provider/deadline, distinct durable receipts and shared usage."""
    requests = REQUESTS if max_requests is None else max_requests
    if type(requests) is not int or not 1 <= requests <= REQUESTS:
        raise ValueError("Invalid saved investigation request bound")
    packet = copy.deepcopy(initial)
    if callable(getattr(adapter, 'bind_source', None)):
        adapter.bind_source(initial)
    schema, wire = bug.planning_request(packet, original, feedback)
    review_required = initial.get('plan_review') in {'source-grounded-v1', 'source-grounded-v2', 'source-grounded-lua-v1'}
    investigating = initial.get('plan_review') in {'source-grounded-v2', 'source-grounded-lua-v1'}
    diagnosis = None
    bootstrap_priority, bootstrap_fact = [], None
    pending, planning_wire, review_rounds = None, None, 0
    if wire['coverage'].get('call_driven') and not review_required:
        wire['coverage']['require_inspection'] = True
    parent = adapter.response_evidence
    journals, receipts = [], []
    lessons = None
    predecessor = None
    historical_pending = False
    packing_context = {}
    program_mode = initial.get('plan_review') == 'source-grounded-lua-v1'

    def make_wire(*args,**kwargs):
        before=len(packet.get('inspection_windows',[]))
        # Codex can retain all of the bounded inspected context in its larger
        # supported request envelope. Local Qwen keeps its smaller context.
        if program_mode and getattr(adapter, 'source_manifest', None) is not None:
            kwargs['text_budget'] = 128000
        kwargs['packing_context'] = packing_context
        result=next_wire(*args,**kwargs)
        added=packet.get('inspection_windows',[])[before:]
        if parent:
            for offset in range(0,len(added),16):
                parent.record('source-function-context',windows=[{k:w[k] for k in ('path','sha256','line','through_line')}
                    for w in added[offset:offset+16]],approved=False,executed=False)
        return result
    if investigating and parent and engine is not None:
        from workbench.investigation_memory import seed
        packet, remembered, predecessor = seed(packet, original, parent.identifier, engine)
        if remembered:
            if initial.get('plan_review') == 'source-grounded-lua-v1' and getattr(adapter, 'reuse_review_lessons', True):
                from workbench.investigation_memory import review_lessons
                lessons = review_lessons(predecessor, engine)
                if lessons:
                    parent.record('rejected-plan-feedback-reused', **lessons, approved=False, executed=False)
            wire = make_wire(packet, original, feedback, remembered,
                             'Previously read source was checked against the current committed repository. No diagnosis or approval was reused.')
            for offset in range(0,len(remembered),16):
                parent.record('source-memory-reused', predecessor=predecessor,
                    windows=[{key:window[key] for key in ('path','sha256','line','through_line')}
                             for window in remembered[offset:offset+16]],
                    approved=False, executed=False)
    limit = read_limit(packet)
    search_chars = 0
    read_chars = (source_characters(packet) if program_mode else
        sum(len(e['text']) for f in [*initial['files'],*initial.get('api_references',[])] for e in f['excerpts'])
        + sum(len(w['text']) for w in packet.get('inspection_windows', [])))
    if program_mode:
        from workbench.source_bootstrap_context import complete
        bootstrap_priority, extra, added, bootstrap_fact = complete(packet, limit-read_chars, should_stop)
        read_chars = source_characters(packet)
        if extra and parent:
            parent.record('source-bootstrap-context',characters=added,
                windows=[{k:v for k,v in w.items() if k!='text'} for w in extra],approved=False,executed=False)
        wire = make_wire(packet, original, feedback,
                         [*packet.get('inspection_windows', []), *bootstrap_priority],
                         'Current committed source and existing startup context; no diagnosis or approval reused.')
        if bootstrap_fact:
            wire['committed_bootstrap_context'] = bootstrap_fact
    if read_chars > limit:
        raise PlanningUnavailable('Initial source context exceeds the investigation read bound.', 'inspection_limit')
    if program_mode and lessons and lessons['predecessor'] == predecessor:
        # Source and the settled complete rejected self-check were revalidated.
        # Keep objections, not a rejected answer to copy or another diagnosis loop.
        investigating = False
        wire['self_review'] = dict(approved=False, executed=False,
            review_binding=lessons['review_binding'], issues=lessons['issues'],
            note='Completed rejected self-check DATA only. Build a fresh complete proposal from the owner report and current committed source; no old program is supplied.')
        wire['fresh_proposal_required'] = 'Resolve the retained objections with actual observations. Inspect genuinely missing source; do not repeat the rejected behavior.'
        if parent:
            parent.record('rejected-review-to-fresh-construction', predecessor=predecessor,
                review_binding=lessons['review_binding'], approved=False, executed=False)
    if program_mode and predecessor and engine is not None and not (lessons and lessons['predecessor'] == predecessor):
        from workbench.investigation_memory import draft_for_critique
        prior=draft_for_critique(predecessor,engine,packet,original,feedback)
        if prior:
            pending=prior['candidate']
            investigating=False
            historical_pending=True
            focused=make_wire(packet,original,feedback,bootstrap_priority,
                'Criticise a fully finished unapproved draft before creating a fresh proposal.',
                programs=[p['code'] for p in pending['plan']['reproducer']['phases']])
            focused['committed_bootstrap_context']=bootstrap_fact
            planning_wire=copy.deepcopy(focused)
            wire=bug_plan_review.request(focused,pending)
            wire['historical_draft_notice']='Unapproved historical DATA only. Even a ready self-check cannot approve, execute or return this draft as the new plan. A fresh complete proposal is required.'
            parent.record('prior-draft-for-critique',predecessor=prior['predecessor'],
                candidate_sha256=prior['sha256'],approved=False,executed=False)

    adapter.ends_at = adapter.ends_at or time.monotonic()+bug.INVESTIGATION_SECONDS
    evidence = parent
    diagnostics = {}
    response_added = True
    original_stop = adapter.stop_event
    rejected = set()
    review_rejected = set()
    rejected_behaviors = ({lessons['rejected_behavior_sha256']} if lessons and
                          lessons.get('rejected_behavior_sha256') else set())

    class Stop:
        def is_set(self):
            return should_stop()

    adapter.stop_event = Stop()
    original_packer = getattr(adapter, 'request_packer', None)
    if getattr(adapter, 'source_investigation', False):
        from workbench.request_context import fit
        adapter.request_packer = lambda outgoing, encode, limit: fit(
            outgoing, packet, packing_context.get('windows', []),
            packing_context.get('requested', []), encode, limit, should_stop)


    def observe_usage(facts):
        usage = facts.get('usage')
        values = usage.get('values') if isinstance(usage, dict) else None
        if (isinstance(values, dict) and all(type(values.get(k)) is int and values[k] >= 0
                for k in ('prompt_tokens', 'completion_tokens', 'total_tokens'))
                and (values['prompt_tokens']+values['completion_tokens'] == values['total_tokens']
                     or getattr(adapter, 'source_investigation', False) and
                     values['prompt_tokens']+values['completion_tokens'] <= values['total_tokens'])):
            receipts.append(usage)
            return True
        return False
    try:
        for index in range(requests):
            schema = (bug_plan_review.ReviewResponse if pending is not None else
                      stage.InvestigationResponse if investigating else BugPlanningResponse)
            if should_stop() or time.monotonic() >= adapter.ends_at:
                raise PlanningUnavailable('The source investigation was stopped or timed out.', 'cancelled')
            if program_mode:
                read_chars=source_characters(packet)+search_chars
            wire['investigation_progress'] = dict(request_number=index+1,max_requests=requests,
                requests_after_this=requests-index-1,last_request=index+1==requests,
                remaining_seconds=max(0,int(adapter.ends_at-time.monotonic())),
                source_characters_read=read_chars,source_characters_remaining=max(0,limit-read_chars),
                source_budget_basis='unique committed characters plus literal search results' if program_mode else 'returned source characters',
                phase='plan_self_check' if pending is not None else
                      'source_diagnosis' if investigating else 'plan_construction',
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
                    request_number=index+1,requests_after_this=requests-index-1)
            response_added = False
            if getattr(adapter, 'source_manifest', None) is not None:
                wire['external_source_scope']=dict(paths=sorted(adapter.source_manifest),
                    note='Searches and follow-up disclosure are restricted to these selected committed files. Outside reads hold for new source permission.')
            if lessons:
                wire['prior_rejected_plan_feedback'] = lessons
            try:
                raw, model, basis = adapter.generate(wire, schema, expected_model=expected_model)
            except PlanningUnavailable as error:
                diagnostics = dict(adapter.diagnostics)
                response_added = observe_usage(diagnostics)
                diagnostics['stopped_request_category'] = error.reason
                diagnostics['stopped_request_generation_state'] = adapter.diagnostics.get('generation_state', 'not_started')
                adapter.diagnostics.update(diagnostics)
                if evidence:
                    evidence.record('request-stopped', category=error.reason, generation_state=diagnostics['stopped_request_generation_state'])
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
                if getattr(adapter, 'source_investigation', False):
                    from workbench.codex_wire import normalize as normalize_inspection
                    raw, changes = normalize_inspection(raw)
                    if changes and evidence:
                        evidence.record('inspection-wire-normalized', changes=changes)
                if pending is not None:
                    review = bug_plan_review.ReviewResponse.model_validate(raw)
                    if review.review_binding != bug.digest(pending):
                        raise ResponseValidationError('The model self-review binding changed.',
                            ['review_binding'], 'the exact proposed-plan binding', review.review_binding)
                    review_rounds += 1
                    if evidence:
                        evidence.snapshot('model-plan-review', review.model_dump(mode='json'))
                    diagnostics.update(plan_self_review_rounds=review_rounds,
                                       plan_self_review_ready=int(review.ready))
                    adapter.diagnostics.update(plan_self_review_rounds=review_rounds,
                                               plan_self_review_ready=int(review.ready))
                    if evidence:
                        evidence.record('plan-self-check',binding=review.review_binding,ready=review.ready,
                            categories=[issue.category for issue in review.issues],independent_verification=False,
                            approved=False,executed=False)
                    if not review.ready and program_mode:
                        rejected_behaviors.add(bug_plan_review.behavior_binding(pending))
                    if historical_pending:
                        if not review.ready:
                            review_rejected.add(review.review_binding)
                        wire=bug_plan_review.feedback(planning_wire,pending,review)
                        wire['fresh_proposal_required']='Create a complete fresh proposal against the current owner request and source. No historical plan or approval is reused.'
                        historical_pending=False
                        pending=None
                        continue
                    if review.ready:
                        return pending, model, basis, packet
                    review_rejected.add(review.review_binding)
                    wire = bug_plan_review.feedback(planning_wire, pending, review)
                    pending = None
                    if index+1 == requests:
                        raise PlanningUnavailable('The plan needs revision after its model self-check.', 'review_limit')
                    continue
                if investigating:
                    investigated = stage.resolve(raw, packet)
                    if isinstance(investigated.draft, stage.Diagnosed):
                        diagnosis = investigated.draft.diagnosis.model_dump(mode='json')
                        if evidence:
                            evidence.snapshot('investigation-diagnosis', raw)
                            evidence.record('source-diagnosis-completed', approved=False, executed=False)
                        investigating = False
                        if program_mode and not (bootstrap_fact and bootstrap_fact.get('complete')):
                            from workbench.source_bootstrap_context import complete
                            bootstrap_priority, extra, added, bootstrap_fact = complete(
                                packet, limit-read_chars, should_stop, diagnosis['evidence_paths'])
                            read_chars = source_characters(packet)+search_chars
                            if extra and evidence:
                                evidence.record('source-read-result',characters=added,search_matches=0,
                                    purpose='complete-existing-test-bootstrap',
                                    windows=[{k:v for k,v in w.items() if k!='text'} for w in extra])
                        wire = make_wire(packet, original, feedback, bootstrap_priority, investigated.finding)
                        if bootstrap_fact:
                            wire['committed_bootstrap_context'] = bootstrap_fact
                        wire['investigation_diagnosis'] = dict(
                            **diagnosis, untrusted=True, approved=False, executed=False)
                        if index+1 == requests:
                            raise PlanningUnavailable('Source diagnosis completed without a reviewed repair plan.', 'inspection_limit')
                        continue
                    if isinstance(investigated.draft, stage.Inspect):
                        raw = dict(finding=investigated.finding, inspection=[r.model_dump(mode='json')
                                   for r in investigated.draft.inspection], plan=None, question=None, limitation=None)
                    else:
                        outcome = investigated.draft.model_dump(mode='json')
                        return {**dict(finding=investigated.finding, plan=None, question=None,
                                      limitation=None, inspection=None), **outcome}, model, basis, packet
                from workbench.bug_wire import compile_setup
                from workbench.bug_program import compile as compile_program
                raw, program_fact = compile_program(raw, packet['files'], wire['coverage'])
                if program_fact and evidence:
                    evidence.record('reviewed-program-compiled', **program_fact)
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
                if (facts and index+1 < requests and stamp not in rejected
                        and diagnostics.get('generation_state') == 'finished'
                        and diagnostics.get('accounting_state') == 'complete'
                        and (diagnostics.get('finish_reason') == 'stop' or
                             getattr(adapter, 'source_investigation', False) and
                             diagnostics.get('completion_state') == 'completed')):
                    rejected.add(stamp)
                    wire['validation_feedback'] = facts
                    continue
                raise
            if draft.inspection is None:
                if review_required and draft.plan is not None:
                    if bug.digest(raw) in review_rejected or (program_mode and bug_plan_review.behavior_binding(raw) in rejected_behaviors):
                        raise PlanningUnavailable('The proposed plan did not change after its rejected self-check.',
                                                  'review_unchanged')
                    if index+1 == requests:
                        raise PlanningUnavailable('The plan needs its model self-check before review.', 'review_limit')
                    pending = raw
                    wire.pop('validation_feedback', None)  # Its constraints have now passed; journal retains the rejected draft.
                    if evidence:
                        evidence.snapshot('proposed-plan', raw)
                    planning_wire = copy.deepcopy(wire)
                    if program_mode:
                        focused=make_wire(packet,original,feedback,bootstrap_priority,'Read exact definitions used by the proposed program.',
                            programs=[p['code'] for p in raw['plan']['reproducer']['phases']])
                        focused['committed_bootstrap_context']=bootstrap_fact
                        planning_wire.update({k:focused[k] for k in ('files','source_memory','selected_source_interface','requested_behavior_clauses')})
                    wire = bug_plan_review.request(planning_wire, raw)
                    continue
                return raw, model, basis, packet
            if should_stop():
                raise PlanningUnavailable('The investigation binding changed before source inspection.', 'cancelled')
            if wire.get('completed_searches') and all(hasattr(r,'query') for r in draft.inspection):
                known={entry['query'].casefold() for entry in wire['completed_searches']['searches']}
                if all(r.query.casefold() in known for r in draft.inspection):
                    raise PlanningUnavailable('The provider repeated completed searches already present in its request.',
                                              'inspection_unchanged')
            if evidence:
                evidence.record('source-inspection', turn=index+1,
                    windows=[dict(query_sha256=hashlib.sha256(r.query.encode()).hexdigest()) if hasattr(r, 'query')
                             else dict(path=r.path, start=r.start, end=r.end) for r in draft.inspection])
            if index+1 == requests:
                raise PlanningUnavailable('The bounded investigation needs more source inspection.', 'inspection_limit')
            windows, matches, chars = (inspect(packet, draft.inspection, disclosure_paths=adapter.source_manifest,
                          page_lines=160,result_budget=16000)
                if getattr(adapter, 'source_manifest', None) is not None else inspect(packet, draft.inspection))
            if evidence:
                evidence.record('source-read-result',characters=chars,search_matches=len(matches),
                    windows=[{key:value for key,value in window.items() if key!='text'} for window in windows])
            if program_mode:
                search_chars += sum(len(m['text']) for m in matches)
                read_chars = source_characters(packet)+search_chars
            else:
                read_chars += chars
            if read_chars > limit:
                raise PlanningUnavailable('The investigation reached its source-read bound.', 'inspection_limit')
            wire = make_wire(packet, original, feedback, [*windows, *bootstrap_priority], draft.finding, matches,
                             requested=windows)
            if bootstrap_fact:
                wire['committed_bootstrap_context'] = bootstrap_fact
            if diagnosis:
                wire['investigation_diagnosis'] = dict(**diagnosis, untrusted=True, approved=False, executed=False)
            wire['inspection_result']['requested'] = [r.model_dump(mode='json') for r in draft.inspection]
            if wire['coverage'].get('call_driven') and not packet.get('inspection_windows') and not review_required:
                wire['coverage']['require_inspection'] = True
    finally:
        current = dict(adapter.diagnostics)
        # Failed/current uncertain wire activity has no complete aggregate.
        unknown = (current.get('generation_state') == 'uncertain'
                   or current.get('accounting_state') == 'unavailable'
                   or not response_added and current.get('generation_state') != 'not_started')
        summed = {k: sum(r['values'][k] for r in receipts)
                  for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')}
        if receipts and all('reasoning_tokens' in r['values'] for r in receipts):
            summed['reasoning_tokens']=sum(r['values']['reasoning_tokens'] for r in receipts)
        diagnostics.update(current, investigation_requests=len(receipts), source_read_chars=read_chars)
        if review_required:
            diagnostics['plan_self_review_rounds'] = review_rounds
        if receipts and not unknown:
            diagnostics['usage'] = dict(source=getattr(adapter, 'usage_source', 'local llama.cpp investigation responses'), units='tokens',
                values=summed, requests_observed=len(receipts), max_request_completion_tokens=max(
                    r['values']['completion_tokens'] for r in receipts), max_request_total_tokens=max(
                    r['values']['total_tokens'] for r in receipts), status='complete')
            if current.get('generation_state') != 'partial':
                diagnostics['generation_state'] = 'finished'
        elif receipts:
            diagnostics['known_usage'] = json.dumps(summed, sort_keys=True)
            diagnostics['usage'] = None
        adapter.diagnostics = diagnostics
        adapter.response_evidence = parent
        adapter.stop_event = original_stop
        if getattr(adapter, 'source_investigation', False):
            adapter.request_packer = original_packer
        for journal in journals:
            journal.close()
