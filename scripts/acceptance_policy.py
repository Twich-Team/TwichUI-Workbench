"""Read-only policy checks for the sealed delegated acceptance cases."""

import argparse
from contextlib import closing
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import sys

from everyday_store import require_settled
from workbench import bug_investigation, grounded_scenarios as ground, regression_preparation as prep
from workbench import regression_evidence as behavior
from workbench.real_execution import LIMITS
from workbench.planning_limits import model_cap, consent_cap
from workbench.schemas import IntakeInput, TaskBudget
from workbench.visual_scope import digest


ROOT = Path(__file__).resolve().parents[1]
BASE = '1caf530212bbc6eaf37e41d668902a391aa936c1'
SOURCE = '!!!TwichUI/map/ViewMath.lua'
MODELS = {'codex': 'gpt-6.1-sol', 'local': 'pilot-qwen38-27b'}
ROUTES = (
    ('codex-once', 'codex', None, [0, 1], 31, 32),
    ('qwen-once', 'local', None, [0, 0], 32, 31),
    ('codex-with-local-fallback', 'local', 'unavailable', [0, -1], 32, 31),
    ('qwen-with-codex-fallback', 'codex', 'local_unreachable', [0, 18000], -2, -1),
)
NOTE = 'Owner-delegated acceptance automation · four sealed regression cases.'
CAMPAIGN = dict(minutes=960, restorationMinutes=120, attempts=8, revisions=3,
                localRuns=5, codexRuns=4, qwenRequests=110, qwenGeneratedTokens=133300,
                codexTurns=8)
OUTCOME = dict(mode='owner-outcome-v1', authority='STANDING_OWNER_DIRECTION_AUTONOMOUS_DELIVERY.md',
               cumulative_caps=False, preserve_accounting=True)


class AcceptanceHeld(ValueError):
    """Only static policy-check text may be exposed by the operator driver."""


def require(condition, message):
    if not condition:
        raise AcceptanceHeld(message)


def build_policy(inputs, *, bug_input=None):
    """Compile allowlisted review content from the pinned source, without running it."""
    require(len(inputs) == len(ROUTES), 'case count changed')
    cases = []
    for case, (choice, provider, reason, args, wanted, wrong) in zip(inputs, ROUTES, strict=True):
        path = 'tests/test_map_viewmath_tileat.lua'
        require(case['provider'] == choice and case['test_path'] == path, 'case changed')
        request = dict(kind='feature', title=case['title'], description=case['description'], lua_error='',
            visual_requirement='nonvisual', local_planning=choice,
            regression_tests=dict(acceptance=[case['expected']], test_paths=[path]))
        original = IntakeInput.model_validate({**request, 'request_id': '00000000-0000-4000-8000-000000000000'}).model_dump(mode='json', exclude_none=True)
        preliminary = prep.prepare(original)
        packet = ground.context(original, '', preliminary, {})
        schema, wire = ground.planning_request(packet)
        [example] = packet['examples']
        require((example['args'], example['expected'], example['incorrect']) == (args, wanted, wrong),
                'sealed example changed')
        [option] = [o for o in wire['binding_options'] if o['target'] == ['TwichUI', 'MapViewMath', 'TileAt']]
        require(packet['resolved_base'] == BASE and option['source_path'] == SOURCE and not option['method']
                and set(e['path'] for e in wire['evidence']) == {'tests/run.sh', 'tests/harness.lua', path, SOURCE}
                and not any('text' in e for e in wire['evidence']), 'committed source disclosure boundary changed')
        raw = {k: packet[k] for k in ('input_sha256', 'evidence_sha256', 'policy_sha256')}
        raw.update(model=MODELS[provider], draft=dict(scenarios=[
            dict(id='acceptance-example', binding_id=option['binding_id'], **{k: v for k, v in example.items() if k != 'setup'})]))
        parsed = ground.resolve_response(raw, schema, wire, packet)
        contract = prep.prepare(original, scenario_response=parsed.model_dump(
            exclude={'evidence_sha256', 'policy_sha256'}))
        require(not contract['scope_missing'] and not contract['behavioral_evidence']['holds'], 'plan contract held')
        content = prep.proposal_content(original, contract)
        test = prep.repository()[0]
        excerpt = prep.git(test, 'show', f'{BASE}:{path}').splitlines(keepends=True)[:120]
        cases.append(dict(choice=choice, provider=provider, reason=reason, model=MODELS[provider],
            request=request, content=content, contract=contract, evidenceSha=packet['evidence_sha256'],
            excerptSha=sha256(b''.join(excerpt)).hexdigest(), bindings=wire['binding_options'], fixtureResponse=raw))
    budget = TaskBudget(activate_once=True, offer_id='00000000-0000-0000-0000-000000000000',
                        offer_fingerprint='0' * 64).model_dump(mode='json')
    policy = dict(base=BASE, models=MODELS, cases=cases, budget=budget, limits=LIMITS, note=NOTE,
                  outcome=OUTCOME)
    if bug_input:
        case = build_bug_case(bug_input)
        policy.update(note='Owner-delegated acceptance automation · sealed provider cases and flight-path report.')
        cases.append(case)
    return policy


def build_bug_case(bug_input):
    request = bug_input['request']
    require(bug_input['id'] == 'bug-flight-paths' and request['kind'] == 'issue'
            and request['local_planning'] in {'qwen-once','codex-once'}, 'bug case or provider changed')
    packet = bug_investigation.context(request, '', dict(
        allowed_classes=[bug_investigation.CATEGORY], scenario_generation=True))
    require(packet['base'] == BASE, 'bug case source base changed')
    paths = [file['path'] for file in packet['files']]
    writable = [f['path'] for f in packet['files'] if not f.get('support_only')
                and f['path'].startswith(('!!!TwichUI/nav/', 'tests/test_nav'))]
    require(any(p.startswith('!!!TwichUI/nav/') for p in writable)
            and any(p.startswith('tests/test_nav') for p in writable), 'bug source envelope is insufficient')
    provider='codex' if request['local_planning']=='codex-once' else 'local'
    return dict(choice=bug_input['id'], provider=provider, model=MODELS[provider], reason=None,
                request=request, criteria=bug_input['criteria'], evidenceSha=digest(packet['files']),
                readPaths=paths, writablePaths=writable)


def check_bug_plan(plan, original, case, preparation, inspection_packet=None):
    contract = plan['task_contract']
    evidence = contract['bug_evidence']
    packet = bug_investigation.context(original, '', preparation)
    require(digest(packet['files']) == case['evidenceSha'] and packet['base'] == BASE
            and [f['path'] for f in packet['files']] == case['readPaths']
            and set(contract['writable_paths']) <= set(case['writablePaths']),
            'bug source or write envelope changed')
    if inspection_packet:
        from workbench.bug_source_planning import verify_packet
        packet = verify_packet(packet, inspection_packet)
    require(contract['scenario_grounding']['evidence_sha256'] == digest(packet['files']),
            'investigated source binding changed')
    reviewed = evidence['plan']
    check = reviewed['reproducer']
    steps = check['steps']
    observations = check['observations']
    actions = [step['path'] for step in steps if step['op'] == 'call']
    require(len({obs['after_step'] for obs in observations}) >= 3
            and (check.get('phases') and check.get('event_coverage') is True
                 or len({tuple(path) for path in actions}) < len(actions))
            and {True, False} <= {obs['expected'] for obs in observations if type(obs['expected']) is bool},
            'initial behavior and two successive flight-master observations are required')
    rebuilt, content, question, limitation = bug_investigation.resolve(dict(
        finding=plan['content']['value'], plan=reviewed, question=None, limitation=None),
        packet, original, '')
    require(not question and not limitation and content == plan['content']
            and rebuilt == {k: v for k, v in contract.items() if k != 'scenario_grounding'}
            and contract['scenario_grounding']['policy_sha256'] == digest(preparation),
            'reviewed bug plan, checked operations or preparation binding changed')
    return evidence


def closeout(connection, identity, execution, case):
    """Independently check the persisted terminal result and restoration fence."""
    connection.row_factory = sqlite3.Row
    attempts = connection.execute('SELECT * FROM model_attempts WHERE task_id=?', (identity,)).fetchall()
    expected = ['implementation', 'planning'] + (['capability_validation'] if case['provider'] == 'local' else [])
    require(sorted(a['purpose'] for a in attempts) == sorted(expected), 'provider attempt count differs')
    require(all(a['finished_at'] and a['backend'] == case['provider'] and a['requested_model'] == case['model']
                and a['actual_model'] == case['model'] and a['usage'] for a in attempts), 'attempt attribution or usage missing')
    by_purpose = {a['purpose']: json.loads(a['usage']) for a in attempts}
    for purpose, usage in by_purpose.items():
        if purpose == 'capability_validation':
            require(usage['passed'] is True and usage['wire_started'] is True
                    and usage['generated_upper_bound'] == 60, 'cancellation probe bound differs')
            continue
        require(usage['units'] == 'tokens' and (purpose == 'planning' or usage['status'] == 'complete'),
                'provider accounting incomplete')
        values = usage['values']
        if case['provider'] == 'codex':
            bug = case['request']['kind'] == 'issue'
            count = usage.get('requests_observed', 1) if purpose == 'planning' else 1
            require(type(count) is int and 1 <= count <= (8 if bug else 1)
                    and 0 < values['total_tokens'] <= (65536 if bug else 32768)*count,
                    'Codex observed threshold exceeded')
            if bug and purpose == 'planning':
                require(usage.get('max_request_total_tokens', values['total_tokens']) <= 65536,
                        'A Codex investigation turn exceeded its observed threshold')
        else:
            if purpose == 'planning':
                require(1 <= usage.get('requests_observed', 1) <= (bug_investigation.INVESTIGATION_REQUESTS if case['request']['kind'] == 'issue' else 1),
                        'planning request count exceeds its task consent')
            require(0 <= values['completion_tokens'] <= {
                'planning': bug_investigation.INVESTIGATION_REQUESTS*model_cap(connection, next(a['id'] for a in attempts if a['purpose']=='planning')) if case['request']['kind'] == 'issue' else 2600,
                'implementation': 24000, 'capability_validation': 60,
            }[purpose], 'Qwen completion token bound exceeded')
    [run] = connection.execute('SELECT * FROM real_execution_requests WHERE id=?', (execution,)).fetchall()
    [session] = connection.execute('''SELECT s.* FROM task_sessions s
        JOIN proposal_decisions d ON d.id=s.id WHERE d.intake_id=?''', (identity,)).fetchall()
    result, state = json.loads(run['result']), json.loads(session['state'])
    require(run['status'] == 'checked' and result.get('verification') == 'passed'
            and session['status'] == 'completed' and session['finished_at']
            and not state.get('cancel_requested')
            and not connection.execute('SELECT 1 FROM real_publications WHERE execution_id=?', (execution,)).fetchone(),
            'execution is unsettled or has publication authority')
    grant = connection.execute('SELECT revoked_at FROM real_execution_grants WHERE id=?', (run['grant_id'],)).fetchone()
    require(grant and grant['revoked_at'] and result.get('candidate_id') and result.get('checks'),
            'execution fence or checked result missing')
    if case['request']['kind'] == 'issue':
        require(result['manifest'].get('profile_certificate_sha256')
                and any('WB_BEHAVIOR bug-reproducer PASS' in check.get('out', '') for check in result['checks'])
                and all(file['path'] in case['writablePaths'] for file in result['files'])
                and any(file['path'].startswith('!!!TwichUI/nav/') for file in result['files']),
                'bug repair did not establish an independently checked navigation patch')
    return dict(attempts=[a['id'] for a in attempts], candidate=result['candidate_id'],
                session=session['id'], restoration='settled by existing controller')


def check_review(connection, request, case, preparation):
    plan = request['proposal']
    original = json.loads(connection.execute('SELECT payload FROM intakes WHERE id=?',
                                            (request['intake'],)).fetchone()[0])
    bug = case['request']['kind'] == 'issue'
    if bug:
        job = connection.execute("SELECT payload FROM planning_jobs WHERE intake_id=? AND kind='planning'",
                                 (request['intake'],)).fetchone()
        inspected = json.loads(job[0]).get('bug_inspection_packet') if job else None
        actual = check_bug_plan(plan, original, case, preparation, inspected)
    else:
        actual = plan['task_contract']['behavioral_evidence']
        require(actual['review_digest'] == behavior.expectation_digest(actual)
                and actual['input_sha256'] == behavior.input_binding(original, '', BASE,
                    case['request']['regression_tests']['test_paths'])
                and plan['task_contract']['scenario_grounding']['policy_sha256'] == digest(preparation),
                'expectation or preparation binding changed')
    [row] = connection.execute('''SELECT status, attempts, payload FROM planning_jobs
        WHERE intake_id=? AND kind='planning' ''', (request['intake'],)).fetchall()
    switched = json.loads(row[2]).get('provider_switch')
    before = 'codex' if case['provider'] == 'local' else 'local'
    expected = (dict(from_provider=before, to_provider=case['provider'], reason=case['reason'],
                     **{before + '_generation_started': False}) if case['reason'] else None)
    require(row[:2] == ('completed', 1) and switched == expected, 'switch evidence or planning claim changed')
    [usage, outcome, model] = connection.execute('''SELECT usage, outcome, actual_model FROM model_attempts
        WHERE task_id=? AND purpose='planning' ''', (request['intake'],)).fetchone()
    usage = json.loads(usage)
    require(outcome == 'completed' and model == case['model'] and usage['units'] == 'tokens',
            'planning usage or provider identity was not reported')
    local_cap = consent_cap(json.loads(row[2]).get('local_planning_receipt', {}))
    key, cap = ('total_tokens', 65536*bug_investigation.INVESTIGATION_REQUESTS if bug else 32768) if case['provider'] == 'codex' else ('completion_tokens', bug_investigation.INVESTIGATION_REQUESTS*local_cap if bug else 2600)
    if bug and case['provider'] == 'codex':
        require(1 <= usage.get('requests_observed', 1) <= bug_investigation.INVESTIGATION_REQUESTS,
                'Codex inspection request accounting changed')
    if bug and case['provider'] == 'local':
        require(1 <= usage.get('requests_observed', 1) <= bug_investigation.INVESTIGATION_REQUESTS
                and usage.get('max_request_completion_tokens', usage['values'][key]) <= local_cap,
                'local inspection request accounting changed')
    elif case['provider'] == 'local':
        require(usage.get('requests_observed', 1) == 1, 'supporting planning request count changed')
    require(0 < usage['values'][key] <= cap, 'planning usage exceeds the approved bound')
    if request['action'] == 'run':
        offer = request['offer']['offer']
        [selected] = connection.execute('SELECT snapshot FROM task_run_offers WHERE id=? AND fingerprint=?',
                                       (offer['id'], offer['fingerprint'])).fetchall()
        recipe = json.loads(selected[0])['specification']
        if bug:
            require(recipe['focused_test'] == actual['plan']['focused_test']
                    and recipe['read_paths'] == actual['plan']['read_paths']
                    and recipe['initial_reads'] == actual['windows']
                    and recipe['contract']['paths'] == actual['plan']['writable_paths']
                    and recipe['contract']['publication_allowed'] is False,
                    'bug implementation read, write or publication scope changed')
        else:
            [test] = case['request']['regression_tests']['test_paths']
            require(recipe['focused_test'] == test and set(recipe['read_paths']) == {test, SOURCE}
                    and all(r['path'] in {test, SOURCE} and r['start'] == 1 and r['end'] <= 120
                            for r in recipe['initial_reads']), 'implementation read scope changed')
        require(not connection.execute('SELECT 1 FROM task_sessions WHERE task_id=?',
                                       (request['task'],)).fetchone(), 'prior task session exists')


def campaign_admission(connection, request, case, policy):
    """Campaign reservations supplement the unchanged per-task application bounds."""
    outcome = policy.get('outcome')
    if not policy.get('campaign') and not outcome:
        require(not request.get('campaign'), 'Unexpected campaign authority')
        return
    require(outcome == OUTCOME if outcome else policy['campaign'] == CAMPAIGN, 'Delivery authority changed')
    require(not outcome or not policy.get('campaign') and not request.get('campaign'), 'Obsolete campaign ceiling supplied')
    campaign = request.get('outcome' if outcome else 'campaign') or {}
    require(type(campaign.get('attempt')) is int and 1 <= campaign['attempt'] and (outcome or campaign['attempt'] <= 8)
            and type(campaign.get('revision')) is int and 0 <= campaign['revision'] and (outcome or campaign['revision'] <= 3)
            and campaign.get('opened_at') == request['opened']
            and set(campaign.get('prior_attempts', [])) <= set(request['prior']),
            'Campaign receipt changed')
    rows = connection.execute('''SELECT id, task_id, backend, purpose, outcome, finished_at, usage
        FROM model_attempts WHERE julianday(started_at)>=julianday(?)''', (request['opened'],)).fetchall()
    intakes = connection.execute('''SELECT id, payload FROM intakes
        WHERE julianday(created_at)>=julianday(?)''', (request['opened'],)).fetchall()
    needs_bug = not outcome or any(c['choice'] == 'bug-flight-paths' for c in policy.get('cases', []))
    require(not needs_bug or intakes or case['choice'] == 'bug-flight-paths',
            'The campaign must start with the real bug journey')
    same_case, bug_intakes = [], set()
    for identity, raw in intakes:
        original = json.loads(raw)
        if original['kind'] == 'issue':
            bug_intakes.add(identity)
        choice = ('bug-flight-paths' if original['kind'] == 'issue' else original.get('local_planning'))
        require(choice in {row[0] for row in ROUTES} | {'bug-flight-paths'},
                'Unapproved campaign intake')
        if choice == case['choice']:
            same_case.append(identity)
            if identity != request.get('intake'):
                require(not connection.execute('''SELECT 1 FROM real_execution_requests r
                    JOIN real_execution_grants g ON g.id=r.grant_id
                    WHERE r.status='checked' AND
                    json_extract(g.snapshot,'$.workflow_task.intake_id')=?''', (identity,)).fetchone(),
                    'A checked case cannot be replayed')
    admission = int(request['action'] == 'submit')
    require(outcome or len(intakes)+admission <= 8 and len(same_case)+admission <= (
        3 if case['choice'] == 'bug-flight-paths' else 2),
        'Campaign case allocation exhausted')
    before = [row for row in rows if row[1] != request.get('intake')]
    local_settled, quarantined = {}, {}
    if outcome:
        from workbench.planning_reconciliation import accounting
        for row in before:
            if row[2:5] == ('codex', 'planning', 'interrupted_unknown'):
                try:
                    from workbench.codex_quarantine import accounting as quarantined_accounting
                    quarantined[row[0]] = quarantined_accounting(connection, row[0])
                except (ValueError, OSError, KeyError, TypeError):
                    pass  # Only the exact permanently quarantined historical turn is covered.
                if row[0] not in quarantined:
                    try:
                        from workbench.investigation_recovery import accounting as recovered_accounting
                        quarantined[row[0]] = recovered_accounting(connection,row[0])
                    except (ValueError,OSError,KeyError,TypeError):
                        pass  # Standing policy requires a verified immutable read-only disposition.
            if row[2] == 'local' and row[3] == 'planning' and row[4] in {'interrupted_unknown','incomplete_output'}:
                try:
                    if row[4]=='incomplete_output':
                        from workbench.partial_planning_reconciliation import accounting as partial_accounting
                        local_settled[row[0]] = partial_accounting(connection,row[0])
                    else:
                        local_settled[row[0]] = accounting(connection, row[0])
                except (ValueError, KeyError, TypeError):
                    pass  # Missing/stale settlement retains the normal hold.
    require(all(row[0] in request['prior'] and row[5] and (row[4] in
                {'completed','checked','failed','passed'} or row[0] in local_settled or row[0] in quarantined) for row in before),
            'Prior execution is unsettled or uncertain')
    local_tasks, codex_tasks = set(), set()
    qwen_requests = qwen_tokens = codex_turns = 0
    for identity, task, backend, purpose, row_outcome, finished, raw in rows:
        if identity in quarantined:
            codex_tasks.add(task)
            codex_turns += quarantined[identity]['requests_started']
            continue  # Unknown final usage stays unknown; no fabricated token or dollar bound.
        if identity in local_settled:
            reserved = local_settled[identity]
            local_tasks.add(task)
            qwen_requests += reserved['requests_started']
            qwen_tokens += reserved['completion_tokens_upper_bound']
            continue  # Receipt distinguishes observed usage from a conservative missing-receipt bound.
        require(finished and raw, 'Campaign accounting unavailable')
        usage = json.loads(raw)
        require(isinstance(usage, dict) and (purpose == 'capability_validation' or
                usage.get('units') == 'tokens'), 'Campaign usage malformed')
        if backend == 'local':
            local_tasks.add(task)
            if purpose == 'capability_validation':
                require(usage.get('passed') is True and usage.get('generated_upper_bound') == 60,
                        'Cancellation probe accounting changed')
                qwen_requests += 1
                qwen_tokens += 60
            else:
                require(purpose == 'planning' or usage.get('status') == 'complete',
                        'Local usage incomplete')
                value = usage.get('values', {}).get('completion_tokens')
                count = usage.get('requests_observed', 1) if purpose == 'planning' else usage.get('requests_observed')
                cap=model_cap(connection,identity) if purpose=='planning' else 24000
                require(type(count) is int and count>=1 and type(value) is int and 0<=value<=
                        (min(count,bug_investigation.INVESTIGATION_REQUESTS)*cap if purpose=='planning' and outcome else cap)
                        and (purpose!='planning' or type(usage.get('max_request_completion_tokens',0)) is int
                             and 0<=usage.get('max_request_completion_tokens',0)<=cap),
                        'Local request accounting unavailable')
                qwen_requests += count
                qwen_tokens += value
        elif backend == 'codex':
            codex_tasks.add(task)
            count = usage.get('requests_observed', 1) if purpose == 'planning' else 1
            require(purpose in {'planning','implementation'} and
                    type(count) is int and 1 <= count <= (8 if task in bug_intakes else 1) and
                    type(usage.get('values', {}).get('total_tokens')) is int and
                    0 < usage['values']['total_tokens'] <= (65536 if task in bug_intakes else 32768)*count,
                    'Codex observations unavailable')
            codex_turns += count
        else:
            raise ValueError('Unexpected campaign provider')
    if request['action'] == 'submit':
        new = 'reserved-next-task'
        (local_tasks if case['provider'] == 'local' else codex_tasks).add(new)
        require(len({row[1] for row in rows}) < campaign['attempt'],
                'Campaign attempt number reused')
    require(outcome or len(local_tasks) <= 5 and len(codex_tasks) <= 4 and
            qwen_requests <= 110 and qwen_tokens <= 133300 and codex_turns <= 8,
            'Campaign usage limit reached')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['freeze', 'admit', 'closeout'])
    parser.add_argument('--policy', type=Path)
    parser.add_argument('--include-bug', action='store_true')
    parser.add_argument('--database', type=Path)
    parser.add_argument('--preparation', type=Path)
    args = parser.parse_args()
    def frozen(include_bug):
        bug_input = json.loads((ROOT / 'ops/bug-acceptance-input.json').read_text()) if include_bug else None
        return build_policy(json.loads((ROOT / 'ops/acceptance-input.json').read_text()), bug_input=bug_input)

    if args.action == 'freeze':
        result = frozen(args.include_bug)
    else:
        request = json.loads(sys.stdin.buffer.read(65537))
        policy = json.loads(args.policy.read_text())
        case = next(c for c in policy['cases'] if c['choice'] == request['choice'])
        if case['request']['kind'] == 'issue':
            sealed = json.loads((ROOT / 'ops/bug-acceptance-input.json').read_text())
            require((policy.get('outcome') == OUTCOME or policy.get('windowMinutes') == 600) and case['request'] == sealed['request']
                    and case['criteria'] == sealed['criteria'], 'pinned bug request changed')
            if request['action'] == 'submit':
                require(build_bug_case(sealed) == case, 'pinned bug source envelope changed')
        else:
            legacy = frozen(False)
            require(case == next(c for c in legacy['cases'] if c['choice'] == request['choice'])
                    and all(policy[key] == legacy[key] for key in ('base', 'models', 'budget', 'limits')),
                    'pinned regression source or policy changed')
        db = args.database
        require(db.resolve() == db and db.is_file() and db.stat().st_mode & 0o077 == 0
                and db.stat().st_uid == os.getuid(), 'history identity changed')
        with closing(sqlite3.connect(db.as_uri() + '?mode=ro', uri=True, timeout=2)) as connection:
            connection.execute('BEGIN')
            require_settled(connection)
            require(not connection.execute("SELECT 1 FROM model_attempts WHERE outcome='running' OR finished_at IS NULL").fetchone(),
                    'provider activity is unsettled')
            campaign_admission(connection, request, case, policy)
            new_attempts = connection.execute('''SELECT id, task_id, purpose FROM model_attempts
                WHERE julianday(started_at)>=julianday(?)''', (request['opened'],)).fetchall()
            expected = set(request['prior'])
            if args.action == 'closeout':
                result = closeout(connection, request['intake'], request['execution'], case)
                expected.update(result['attempts'])
            elif request['action'] == 'submit':
                require(not connection.execute('''SELECT 1 FROM intakes WHERE
                    json_extract(payload, '$.request_id')=? OR json_extract(payload, '$.title')=?''',
                    (request['request']['request_id'], case['request']['title'])).fetchone(), 'duplicate intake')
                result = dict(settled=True)
            else:
                current = [a for a in new_attempts if a[1:] == (request['intake'], 'planning')]
                require(len(current) == 1, 'expected exactly one owned planning attempt')
                expected.add(current[0][0])
                check_review(connection, request, case, json.loads(args.preparation.read_text()))
                result = dict(settled=True)
            require({a[0] for a in new_attempts} == expected, 'unexpected or duplicate provider activity in window')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except AcceptanceHeld as error:
        # All require() reasons are literal application text, never provider
        # output, file contents, credentials or arbitrary exception messages.
        print(json.dumps(dict(held=True, reason=str(error))), file=sys.stderr)
        raise SystemExit(2) from None
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, sqlite3.Error):
        print(json.dumps(dict(held=True, reason='Acceptance policy input or state is unavailable.')), file=sys.stderr)
        raise SystemExit(2) from None
