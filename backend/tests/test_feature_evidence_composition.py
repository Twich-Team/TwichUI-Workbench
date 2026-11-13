"""Composed admission through the real verifier, Lua checks and frozen ledgers.

Only provider responses and confinement telemetry use existing controlled fixtures.
The behavior under test (composition/specification/runtime/completion) is not mocked.
"""
import copy
from pathlib import Path

import pytest
from sqlalchemy import select

from workbench import feature_checks, feature_verification as verifier
from workbench.db import planning_jobs, real_execution_requests, real_execution_grants, model_attempts
from workbench.real_workspace import sha
from workbench.visual_scope import digest
from test_feature_check_preparation import at_independent_preparation
from test_feature_checks import program
from test_everyday import installed as installed


def contract(c, job, proposed):
    saved = job['payload']['independent_check_preparation']
    packet = saved['packet']
    records = job['payload']['completed_steps']
    approval, permission, contributions, paths = verifier.checked_chain(
        c, job['payload']['feature_decision_id'], records, records[-1]['input'])
    requirements, evidence = [], []
    for clause in packet['indexed_clauses']:
        identity = 'requirement-' + str(clause['index'])
        refs = ['phase-' + str(i) for i,p in enumerate(proposed['phases'], 1)
                if clause['index'] in p['clause_indices']]
        requirements.append(dict(id=identity, text=clause['requirement'], kind='behavior',
            clause_indices=[clause['index']], evidence=refs,
            rationale='The named observation directly calls the required input and observes its return.'))
    for i, phase in enumerate(proposed['phases'], 1):
        evidence.append(dict(id='phase-'+str(i),kind='phase',phase=i,sha256=digest(phase),
            authorship='original-only', requirements=[r['id'] for r in requirements
                if 'phase-'+str(i) in r['evidence']], rationale=phase['description']))
    # Reuse an exact first-step assertion, not the complete test file as coverage.
    row, grant, _, result = verifier.feature_source._checked(c, records[0]['execution_id'])
    path = next(f['path'] for f in result['files'] if f['path'].startswith('tests/'))
    source = (Path(grant['snapshot']['recovery']['root'])/'candidates'/result['candidate_id']/'files'/path).read_bytes()
    lines = source.decode().splitlines(keepends=True)
    start = next(i for i,line in enumerate(lines) if 'assert(ok and observed == nil)' in line)
    identity = next(r['id'] for r in requirements if 'Absent' in r['text'])
    next(r for r in requirements if r['id'] == identity)['evidence'].append('native-absent')
    evidence.append(dict(id='native-absent',kind='native',execution_id=row['id'],path=path,
        start=start+1,end=start+1,sha256=sha(lines[start].encode()),file_sha256=sha(source),
        result_binding=digest(result),authorship='implementation-authored',requirements=[identity],
        rationale='This exact assertion independently executed the absent-input behavior; it remains in the final source.'))
    value = dict(version=1, input_binding=packet['input_binding'],
        original_request_binding=digest(packet['original']), approved_clauses=packet['indexed_clauses'],
        program_binding=digest(feature_checks.Program.model_validate(proposed).model_dump(mode='json')),
        reviewer='Controlled host evidence audit', authorship={'candidate_disclosed':False},
        contributions_binding=digest(contributions),feature_input_binding=digest(records[-1]['input']),
        paths=paths,requirements=requirements,evidence=evidence)
    return {**value,'binding':digest(value)}


def bind(value):
    value['binding'] = digest({k:v for k,v in value.items() if k != 'binding'})
    return value


def test_composed_contract_rejects_missing_stale_irrelevant_evidence_and_completes_once(
    client, headers, store, installed, monkeypatch,
):
    import json
    from workbench import real_confine, real_execution, worker, feature_delivery
    monkeypatch.setattr(real_confine, 'root', lambda: '/sys/fs/cgroup/workbench-real-CONTROLLED-composed')
    identity, _, planner, tick = at_independent_preparation(client, headers, store, monkeypatch)
    with store.connect() as c:
        job = dict(c.execute(select(planning_jobs).where(planning_jobs.c.kind=='feature_execution')).mappings().one())
        proposed = program(job['payload']['independent_check_preparation']['packet'])
        composed = contract(c, job, proposed)
        operations = len(c.execute(select(model_attempts)).all())
    before = copy.deepcopy(job['payload']['independent_check_preparation'])
    for fault in ('missing', 'irrelevant', 'hash', 'revision', 'receipt', 'assertion', 'duplicate', 'suite-only'):
        changed = copy.deepcopy(composed)
        if fault == 'missing':
            changed['requirements'].pop()
        elif fault == 'irrelevant':
            changed['requirements'][0]['evidence'] = ['phase-1']
        elif fault == 'hash':
            changed['evidence'][0]['sha256'] = '0'*64
        elif fault == 'revision':
            changed['feature_input_binding'] = '0'*64
        elif fault == 'receipt':
            changed['evidence'][-1]['result_binding'] = '0'*64
        elif fault == 'assertion':
            changed['evidence'][-1]['sha256'] = '0'*64
        elif fault == 'duplicate':
            changed['requirements'].append(changed['requirements'][0])
        else:
            changed['evidence'][0].update(kind='control',control='full-suite')
        bind(changed)
        with pytest.raises((ValueError, KeyError)):
            verifier.compose(store, job['id'], proposed, changed, 'Controlled owner instruction')
    receipt = verifier.compose(store, job['id'], proposed, composed, 'Controlled owner instruction')
    assert verifier.compose(store, job['id'], proposed, composed, 'Controlled owner instruction') == receipt
    with store.connect() as c:
        current = dict(c.execute(select(planning_jobs).where(planning_jobs.c.id==job['id'])).mappings().one())
        assert current['payload']['independent_check_preparation'] == before
        spec, _ = verifier.specification(c, current)
        assert spec['independent_checks']['evidence_composition'] == composed
        # A stale history receipt cannot authorize completion, even with a
        # recomputed outer checksum.
        stale = copy.deepcopy(current)
        stale['payload']['evidence_composition']['history_binding'] = '0'*64
        bind(stale['payload']['evidence_composition'])
        with pytest.raises(ValueError):
            verifier.specification(c, stale)
    # A controlled resource timeout travels through the actual dispatcher,
    # safety rejection, failed-outcome seal and bounded retry admission.
    transport = verifier.Runtime.check
    def timeout_once(self, argv, cwd, **kwargs):
        self.check = lambda argv, cwd, **options: transport(self, argv, cwd, **options)
        usage = dict(status='timeout',cgroup_removed=True,leaked_after_kill=False)
        (self.root/'usage.jsonl').write_text(json.dumps(usage)+'\n')
        return dict(command=argv,status='timeout',rc=-9,out='Controlled partial suite',usage=usage)
    monkeypatch.setattr(verifier.Runtime, 'check', timeout_once)
    assert tick()
    with store.connect() as c:
        failed = c.execute(select(planning_jobs).where(planning_jobs.c.id==job['id'])).mappings().one()
        assert failed['status'] == 'failed' and failed['payload']['phase'] == 'held'
    retry = verifier.retry_composed_timeout(store, job['id'], 'Controlled same-contract timeout recovery')
    assert verifier.retry_composed_timeout(store, job['id'], 'Controlled same-contract timeout recovery') == retry
    monkeypatch.setattr(verifier.Runtime, 'check', transport)
    # Lose the response only AFTER the create-only successful completion seal.
    # Recovery may read that seal; it cannot execute the observations again.
    complete = verifier.Runtime.record_completion
    def response_lost(self, *args, **kwargs):
        complete(self, *args, **kwargs)
        raise SystemExit('CONTROLLED local response loss after frozen completion')
    monkeypatch.setattr(verifier.Runtime, 'record_completion', response_lost)
    with pytest.raises(SystemExit):
        tick()
    monkeypatch.setattr(verifier.Runtime, 'record_completion', complete)
    monkeypatch.setattr(real_execution, 'alive', lambda owner: False)
    def forbid_checks(*args, **kwargs):
        raise AssertionError('Recovery must not manufacture completion by replaying checks')
    monkeypatch.setattr(verifier.Runtime, 'check', forbid_checks)
    assert tick()
    with store.connect() as c:
        current = c.execute(select(planning_jobs).where(planning_jobs.c.id==job['id'])).mappings().one()
        row = c.execute(select(real_execution_requests).where(
            real_execution_requests.c.id==current['payload']['final_verification_execution'])).mappings().one()
        assert row['status'] == 'checked', row['result']
        assert len(current['payload']['completed_checks']) == 1
        assert len(c.execute(select(model_attempts)).all()) == operations
        assert len(current['payload']['verification_history']) == 1
        assert current['payload']['feature_verification_attempt'] == 2
        count = len(c.execute(select(real_execution_requests)).all())
    assert not planner.blind_packets  # No new program generation or fake model review.
    assert feature_delivery.prepare_scope(store, job['id'])
    assert tick() and tick()  # Existing delivery admission and actual local bundle.
    assert client.get('/api/features/'+identity).json()['state'] == 'ready_to_review'
    with store.connect() as c:
        assert len(c.execute(select(real_execution_requests)).all()) == count
    assert not worker.tick(store, planning_adapter=planner)
    from uuid import uuid4
    from fastapi import HTTPException
    from workbench import task_cleanup
    context = task_cleanup.context(store, identity)
    assert context['can_archive']
    task_cleanup.apply(store, task_cleanup.CleanupCommand.model_validate(dict(
        command_id=str(uuid4()), action='archive',
        targets=[dict(intake_id=identity, binding=context['binding'])])))
    with store.connect() as c:
        history = c.execute(select(planning_jobs).where(planning_jobs.c.id == job['id'])).mappings().one()
        assert feature_delivery.view_in(c, history)['published'] is False
        with pytest.raises(HTTPException):
            verifier.checked(c, row, c.execute(select(real_execution_grants).where(real_execution_grants.c.id == row['grant_id'])).mappings().one())


def test_unsafe_interrupted_check_is_retained_before_rejection(tmp_path):
    import json
    from workbench.real_workspace import ToolDenied
    runtime = object.__new__(verifier.Runtime)
    runtime.root = tmp_path
    runtime.oracle_source = tmp_path/'oracle.lua'
    runtime.stop_event = None
    # Only resource transport telemetry is controlled. Admission still uses the
    # actual _check safety validation and create-only receipt writer.
    timeout = dict(status='timeout', rc=-9, command=['bash','tests/run.sh'],out='partial inventory',
        usage=dict(cgroup_removed=True, leaked_after_kill=False))
    observed = []
    def transport(argv, cwd, **kwargs):
        observed.append(kwargs['timeout'])
        return timeout
    runtime.check = transport
    with pytest.raises(ToolDenied):
        runtime._check(['bash','tests/run.sh'], tmp_path, loopback=True)
    assert observed == [600]
    assert json.loads((tmp_path/'local-checks/01.json').read_text()) == timeout
