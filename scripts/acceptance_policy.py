"""Read-only policy checks for the four delegated browser acceptance cases."""

import argparse
from contextlib import closing
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import sys

from everyday_store import require_settled
from workbench import grounded_scenarios as ground, regression_preparation as prep
from workbench import regression_evidence as behavior
from workbench.real_execution import LIMITS
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


def require(condition, message):
    if not condition:
        raise ValueError(message)


def build_policy(inputs):
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
    return dict(base=BASE, models=MODELS, cases=cases, budget=budget, limits=LIMITS, note=NOTE)


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
            require(0 < values['total_tokens'] <= 32768, 'Codex observed threshold exceeded')
        else:
            require(0 <= values['completion_tokens'] <= {
                'planning': 2600, 'implementation': 24000, 'capability_validation': 60,
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
    return dict(attempts=[a['id'] for a in attempts], candidate=result['candidate_id'],
                session=session['id'], restoration='settled by existing controller')


def check_review(connection, request, case, preparation):
    plan = request['proposal']
    actual = plan['task_contract']['behavioral_evidence']
    original = json.loads(connection.execute('SELECT payload FROM intakes WHERE id=?',
                                            (request['intake'],)).fetchone()[0])
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
    key, cap = ('total_tokens', 32768) if case['provider'] == 'codex' else ('completion_tokens', 2600)
    require(0 < usage['values'][key] <= cap, 'planning usage exceeds the approved bound')
    if request['action'] == 'run':
        offer = request['offer']['offer']
        [selected] = connection.execute('SELECT snapshot FROM task_run_offers WHERE id=? AND fingerprint=?',
                                       (offer['id'], offer['fingerprint'])).fetchall()
        recipe = json.loads(selected[0])['specification']
        [test] = case['request']['regression_tests']['test_paths']
        require(recipe['focused_test'] == test and set(recipe['read_paths']) == {test, SOURCE}
                and all(r['path'] in {test, SOURCE} and r['start'] == 1 and r['end'] <= 120
                        for r in recipe['initial_reads']), 'implementation read scope changed')
        require(not connection.execute('SELECT 1 FROM task_sessions WHERE task_id=?',
                                       (request['task'],)).fetchone(), 'prior task session exists')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['freeze', 'admit', 'closeout'])
    parser.add_argument('--policy', type=Path)
    parser.add_argument('--database', type=Path)
    parser.add_argument('--preparation', type=Path)
    args = parser.parse_args()
    if args.action == 'freeze':
        result = build_policy(json.loads((ROOT / 'ops/acceptance-input.json').read_text()))
    else:
        request = json.loads(sys.stdin.buffer.read(65537))
        policy = json.loads(args.policy.read_text())
        case = next(c for c in policy['cases'] if c['choice'] == request['choice'])
        require(build_policy(json.loads((ROOT / 'ops/acceptance-input.json').read_text())) == policy,
                'pinned source or policy changed')
        db = args.database
        require(db.resolve() == db and db.is_file() and db.stat().st_mode & 0o077 == 0
                and db.stat().st_uid == os.getuid(), 'history identity changed')
        with closing(sqlite3.connect(db.as_uri() + '?mode=ro', uri=True, timeout=2)) as connection:
            connection.execute('BEGIN')
            require_settled(connection)
            require(not connection.execute("SELECT 1 FROM model_attempts WHERE outcome='running' OR finished_at IS NULL").fetchone(),
                    'provider activity is unsettled')
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
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, sqlite3.Error):
        print('Acceptance policy check held. Inspect the stage and redacted browser receipt.', file=sys.stderr)
        raise SystemExit(2) from None
