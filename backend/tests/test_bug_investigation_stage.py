"""Read-only diagnosis has no repair authority and shares the existing operation."""
import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from workbench.bug_investigation_stage import InvestigationResponse, resolve
from workbench.planning_response import ResponseValidationError, wire_schema
from test_bug_plan_review import review, run
from test_bug_source_planning import scripted
from test_bug_workflow import enable_bugs, response
from test_everyday import installed, write_policy  # noqa: F401
from test_verification_profiles import PRODUCTION


def enable(monkeypatch):
    enable_bugs(monkeypatch)
    path = Path(os.environ['WORKBENCH_PREPARATION_CONFIG'])
    policy = json.loads(path.read_text())
    policy['bug_plan_review'] = 'source-grounded-v2'
    write_policy(path, policy)


def diagnosis():
    return dict(finding='The committed implementation retains uppercase input.',
                draft=dict(diagnosis=dict(cause='The returned trimmed name is not lowercased.',
                                          evidence_paths=[PRODUCTION])))


@pytest.mark.usefixtures('installed')
def test_diagnosis_precedes_plan_and_review_without_execution(client, headers, store, monkeypatch):
    enable(monkeypatch)
    calls = scripted(monkeypatch, [diagnosis(), response(), review()])
    item, attempt = run(client, headers, store)
    assert item['status'] == 'completed' and len(item['proposals']) == 1
    assert [c['investigation_progress']['phase'] for c in calls] == [
        'source_diagnosis', 'plan_construction', 'plan_self_check']
    assert calls[1]['investigation_diagnosis']['approved'] is False
    assert calls[1]['investigation_diagnosis']['executed'] is False
    assert attempt['usage']['requests_observed'] == 3
    assert attempt['usage']['values']['completion_tokens'] == 30


@pytest.mark.usefixtures('installed')
def test_read_only_inspection_then_diagnosis_retains_exact_source(client, headers, store, monkeypatch):
    enable(monkeypatch)
    read = dict(finding='Read the exact implementation.',
                draft=dict(inspection=[dict(path=PRODUCTION, start=1, end=1)]))
    calls = scripted(monkeypatch, [read, diagnosis(), response(), review()])
    item, attempt = run(client, headers, store)
    assert item['status'] == 'completed' and attempt['usage']['requests_observed'] == 4
    assert calls[1]['inspection_result']['read_only']
    assert calls[2]['investigation_diagnosis']['evidence_paths'] == [PRODUCTION]


@pytest.mark.usefixtures('installed')
def test_unavailable_guess_returns_tool_feedback_without_losing_valid_reads_or_usage(client, headers, store, monkeypatch):
    enable(monkeypatch)
    missing = '!!!TwichUI/not-a-committed-file.lua'
    read = dict(finding='Read source, without assuming these guessed files exist.', draft=dict(inspection=[
        dict(path=PRODUCTION,start=1,end=1), dict(path=missing,start=1,end=80)]))
    calls = scripted(monkeypatch, [read, diagnosis(), response(), review()])
    item, attempt = run(client, headers, store)
    assert item['status'] == 'completed' and not item['decisions'] and not item['tasks']
    feedback = calls[1]['inspection_result']
    assert feedback['read_errors'][0]['path'] == missing and feedback['read_errors'][0]['status'] == 'unavailable'
    assert feedback['visible_requested_windows'][0]['path'] == PRODUCTION
    assert missing not in {f['path'] for f in calls[1]['files']}
    assert attempt['usage']['requests_observed'] == 4


@pytest.mark.usefixtures('installed')
def test_unchanged_unavailable_reads_do_not_loop(client, headers, store, monkeypatch):
    enable(monkeypatch)
    read = dict(finding='A guessed unavailable file.',draft=dict(inspection=[
        dict(path='!!!TwichUI/not-a-committed-file.lua',start=1,end=80)]))
    calls = scripted(monkeypatch, [read, read])
    item, attempt = run(client, headers, store)
    assert item['status'] == 'failed' and len(calls) == 2 and not item['proposals']
    from sqlalchemy import select
    from workbench.db import planning_jobs
    with store.connect() as c:
        payload = c.execute(select(planning_jobs.c.payload).where(planning_jobs.c.id == attempt['job_id'])).scalar_one()
    assert payload['diagnostics']['category'] == 'inspection_unchanged'
    assert attempt['usage']['requests_observed'] == 2


@pytest.mark.parametrize('cause', ['The committed project changed during investigation.',
                                  'Source inspection is restricted to committed project Lua and tests.'])
def test_tool_feedback_does_not_hide_policy_or_repository_failures(monkeypatch, cause):
    from workbench import bug_source_planning as source
    from workbench.bug_evidence import InvestigationRead
    def denied(*_): raise ValueError(cause)
    monkeypatch.setattr(source, 'committed', denied)
    errors = []
    with pytest.raises(ResponseValidationError):
        source.inspect(dict(base='pinned'), [InvestigationRead(path=PRODUCTION,start=1,end=80)], errors=errors)
    assert errors == []


def test_missing_external_disclosure_still_holds_before_any_read(monkeypatch):
    from workbench import bug_source_planning as source
    from workbench.bug_evidence import InvestigationRead
    from workbench.planning_transport import PlanningUnavailable
    def unexpected(*_): raise AssertionError('Forbidden read happened')
    monkeypatch.setattr(source, 'committed', unexpected)
    errors = []
    with pytest.raises(PlanningUnavailable) as hold:
        source.inspect(dict(base='pinned'), [InvestigationRead(path=PRODUCTION,start=1,end=80)],
            disclosure_paths=set(), errors=errors)
    assert hold.value.reason == 'disclosure' and errors == []


@pytest.mark.usefixtures('installed')
def test_changed_search_after_missing_read_is_new_evidence_even_without_matches(client, headers, store, monkeypatch):
    enable(monkeypatch)
    missing = dict(path='!!!TwichUI/not-a-committed-file.lua',start=1,end=80)
    first = dict(finding='Check a guessed path.',draft=dict(inspection=[missing]))
    changed = dict(finding='Search the committed project to correct the guess.',draft=dict(inspection=[
        missing, dict(query='NoSuchCommittedSymbol12345')]))
    question = dict(finding='No claim of a fix.',draft=dict(question='What visible behavior should change?'))
    calls = scripted(monkeypatch, [first, changed, question])
    item, attempt = run(client, headers, store)
    assert len(calls) == 3 and item['status'] != 'failed' and not item['tasks']
    assert calls[2]['inspection_result']['search_matches'] == []
    assert attempt['usage']['requests_observed'] == 3


@pytest.mark.parametrize('outcome', ['question', 'limitation'])
@pytest.mark.usefixtures('installed')
def test_nonplan_outcomes_do_not_construct_or_approve_plan(client, headers, store, monkeypatch, outcome):
    enable(monkeypatch)
    scripted(monkeypatch, [dict(finding='A real-world detail is unavailable.', draft={outcome:'A specific missing fact.'})])
    item, attempt = run(client, headers, store)
    assert attempt['usage']['requests_observed'] == 1
    assert all(not p['task_contract'].get('bug_evidence') for p in item['proposals'])


@pytest.mark.parametrize('bad', [
    {}, {'finding':'x', 'draft':{}},
    {'finding':'x', 'draft':{'diagnosis':{'cause':'x', 'evidence_paths':[]}}},
    {'finding':'x', 'draft':{'question':'x', 'limitation':'y'}},
    {'finding':'x', 'draft':{'plan':{}}},
])
def test_malformed_ambiguous_and_premature_plans_reject(bad):
    with pytest.raises(ValidationError):
        InvestigationResponse.model_validate(bad)


@pytest.mark.parametrize('paths', [['outside.lua'], [PRODUCTION, PRODUCTION]])
def test_diagnosis_cannot_invent_inspected_evidence(paths):
    value = diagnosis()
    value['draft']['diagnosis']['evidence_paths'] = paths
    with pytest.raises(ResponseValidationError):
        resolve(value, {'files':[{'path':PRODUCTION}]})


def test_diagnosis_schema_has_no_repair_or_execution_fields():
    schema = wire_schema(InvestigationResponse)
    assert set(schema['properties']) == {'finding', 'draft'}
    assert set(schema['$defs']['Diagnosed']['properties']) == {'diagnosis'}
    assert 'BugPlan' not in schema['$defs']


@pytest.mark.usefixtures('installed')
def test_renewal_reuses_only_revalidated_source_not_plan_or_approval(client, headers, store, monkeypatch):
    enable(monkeypatch)
    read = dict(finding='Read the exact implementation.',
                draft=dict(inspection=[dict(path=PRODUCTION, start=1, end=1)]))
    first = scripted(monkeypatch, [read, diagnosis(), response(), review(False), response()])
    item, old = run(client, headers, store)
    assert item['status'] == 'failed' and len(first) == 5
    second = scripted(monkeypatch, [diagnosis(), response(), review()])
    item, renewed = run(client, headers, store)
    assert item['status'] == 'completed' and len(second) == 3
    assert second[0]['source_memory']['read_windows'][0]['path'] == PRODUCTION
    assert 'candidate_plan' not in second[0] and 'self_review' not in second[0]
    assert renewed['id'] != old['id'] and renewed['usage']['requests_observed'] == 3
    assert not item['tasks'] and not item['decisions']


@pytest.mark.usefixtures('installed')
def test_changed_report_does_not_reuse_prior_source(client, headers, store, monkeypatch):
    enable(monkeypatch)
    read = dict(finding='Read the exact implementation.',
                draft=dict(inspection=[dict(path=PRODUCTION, start=1, end=1)]))
    scripted(monkeypatch, [read, diagnosis(), response(), review(False), response()])
    item, _ = run(client, headers, store)
    assert item['status'] == 'failed'
    from workbench import bug_investigation
    from workbench.investigation_memory import seed
    from workbench.trusted_preparation import configuration
    from test_bug_workflow import report
    original = report()
    original['description'] += ' A different reported behavior.'
    packet = bug_investigation.context(original, '', configuration(store))
    result, windows, parent = seed(packet, original, '00000000-0000-0000-0000-000000000001', store)
    assert result == packet and windows == [] and parent is None


@pytest.mark.parametrize('change',[None,'text','scope','base','uncertain'])
@pytest.mark.usefixtures('installed')
def test_completed_packet_reuses_only_fresh_committed_reads(client,headers,store,monkeypatch,change):
    import copy
    from sqlalchemy import select
    from workbench import bug_investigation
    from workbench.db import planning_jobs,model_attempts
    from workbench.investigation_memory import completed_packet
    from workbench.trusted_preparation import configuration
    from test_bug_workflow import report
    enable(monkeypatch)
    read=dict(finding='Inspect the existing implementation.',draft=dict(
        inspection=[dict(path=PRODUCTION,start=1,end=1)]))
    scripted(monkeypatch,[read,diagnosis(),dict(finding='A missing real-world fact remains.',
        plan=None,question='A specific missing fact.',limitation=None)])
    item,attempt=run(client,headers,store)
    assert item['status']=='waiting_for_information' and attempt['outcome']=='completed'
    with store.begin() as c:
        job=c.execute(select(planning_jobs).where(planning_jobs.c.intake_id==item['id'])).mappings().one()
        payload=copy.deepcopy(job['payload'])
        cached=payload['bug_inspection_packet']
        cached['policy_sha256']='historical-policy-not-authority'
        cached['old_plan']='DO_NOT_COPY'
        if change=='text':
            cached['inspection_windows'][0]['text']='FORGED_CACHE_MUST_NOT_COPY'
        if change=='scope':
            cached['inspection_windows'][0]['path']='!!!TwichUI/unselected.lua'
        if change=='base':
            cached['base']='different'
        c.execute(planning_jobs.update().where(planning_jobs.c.id==job['id']).values(payload=payload))
        if change=='uncertain':
            c.execute(model_attempts.update().where(model_attempts.c.id==attempt['id']).values(outcome='interrupted_unknown',usage=None))
    packet=bug_investigation.context(report(),'',configuration(store))
    original=copy.deepcopy(packet)
    result=completed_packet(packet,report(),'00000000-0000-0000-0000-000000000001',store)
    assert packet==original
    if change in {'scope','base','uncertain'}:
        assert result is None
    else:
        fresh,windows,prior=result
        assert prior==attempt['id'] and windows and fresh['policy_sha256']==packet['policy_sha256']
        assert 'old_plan' not in fresh and 'FORGED_CACHE' not in json.dumps(fresh)
        assert fresh['files']==packet['files']


@pytest.mark.parametrize('change', ['symlink', 'hardlink', 'public', 'oversize'])
def test_unsafe_source_journal_rejects_without_reading(tmp_path, change):
    from workbench.investigation_memory import read_journal
    path = tmp_path/'events.jsonl'
    path.write_text('{}\n')
    path.chmod(0o600)
    if change == 'symlink':
        target = tmp_path/'original'
        path.rename(target)
        path.symlink_to(target)
    elif change == 'hardlink':
        os.link(path, tmp_path/'other')
    elif change == 'public':
        path.chmod(0o644)
    else:
        path.write_bytes(b'x'*65537)
    with pytest.raises(ValueError):
        read_journal(path)


@pytest.mark.parametrize('raw', ['[]\n', '{"phase":"x","phase":"y"}\n', '{}\n', '{'])
def test_malformed_source_journal_is_not_cache_input(tmp_path, raw):
    from workbench.investigation_memory import read_journal
    path = tmp_path/'events.jsonl'
    path.write_text(raw)
    path.chmod(0o600)
    with pytest.raises(ValueError):
        read_journal(path)


def test_stage_diagnostics_identify_actual_schema_and_known_union_paths():
    from workbench.bug_evidence import validation_diagnostics
    from workbench.visual_scope import digest
    try:
        InvestigationResponse.model_validate(dict(finding='x',draft=dict(diagnosis=dict(
            cause='x',evidence_paths=[]))))
    except ValidationError as error:
        facts = validation_diagnostics(error)
    else:
        raise AssertionError('Invalid diagnosis accepted')
    assert facts['validation_schema_sha256'] == digest(InvestigationResponse.model_json_schema())
    assert 'draft.Diagnosed.diagnosis.evidence_paths' in facts['validation_locations']


def test_diagnosis_decoder_uses_exact_selected_file_enum():
    schema = wire_schema(InvestigationResponse, files=[{'path':PRODUCTION}, {'path':'tests/test_example.lua'}])
    assert schema['$defs']['Diagnosis']['properties']['evidence_paths']['items'] == {
        'type':'string', 'enum':[PRODUCTION, 'tests/test_example.lua']}


@pytest.mark.usefixtures('installed')
def test_invalid_citation_text_retained_privately_before_semantic_rejection(
    client, headers, store, monkeypatch, tmp_path,
):
    enable(monkeypatch)
    value = diagnosis()
    bad_path = PRODUCTION+':1-7'
    value['draft']['diagnosis']['evidence_paths'] = [bad_path]
    scripted(monkeypatch, [value])
    item, attempt = run(client, headers, store)
    assert item['status'] == 'failed' and not item['proposals']
    assert attempt['usage']['requests_observed'] == 1
    root = tmp_path/'artifacts/runs'/('planning-evidence-'+attempt['id'])
    content=json.loads((root/'provider-content.json').read_text())
    assert json.loads(content['text']) == value and not content['truncated']
    assert bad_path not in (root/'events.jsonl').read_text()
    assert 'provider-content' in (root/'events.jsonl').read_text()
