"""Scope approval must not pretend executable checks or a repair already exist."""
import copy

import pytest
from fastapi import HTTPException

from workbench import bug_intent,bug_evidence


def source():
    files=[dict(path=p,git_blob='b'*40,sha256='c'*64) for p in
           ('!!!TwichUI/sample.lua','tests/test_sample.lua','tests/harness.lua')]
    files[-1]['support_only']=True
    return dict(base='a'*40,files=files)


def plan():
    return dict(scope='Correct the reported behavior and preserve unrelated data.',
        read_paths=['!!!TwichUI/sample.lua','tests/test_sample.lua'],
        writable_paths=['!!!TwichUI/sample.lua','tests/test_sample.lua'],focused_test='tests/test_sample.lua',
        criteria=[dict(id='reported-operation',description='Check the actual operation before and after the reported event.'),
                  dict(id='saved-state',description='Check separate identities and preserved state after a fresh client startup.')])


def test_scope_is_bound_to_all_owner_requirements_without_execution_or_test_program(monkeypatch):
    from workbench import regression_preparation as source_module
    monkeypatch.setattr(source_module,'repository',lambda:(None,'a'*40,{'tests/run.sh':'d'*40,'tests/harness.lua':'e'*40}))
    monkeypatch.setattr(source_module,'blob',lambda root,oid:b'controlled framework')
    original=dict(title='A reported bug',description='The actual behavior is wrong.')
    feedback='Keep each identity separate and preserve unknown data.'
    contract,content,question,limitation=bug_intent.resolve(dict(finding='The inspected operation differs from the request.',
        plan=plan()),source(),original,feedback)
    assert not question and not limitation
    assert contract['bug_intent']['checks_prepared'] is False
    assert not contract['execution_authorized'] and not contract['publication_authorized']
    assert 'bug_evidence' not in contract
    assert 'Prepare and check executable behavioral tests.' in content['stages']
    saved=contract['bug_intent']
    assert bug_intent.validate_review(saved,original,feedback,source()).review_digest==saved['review_digest']
    with pytest.raises(ValueError):
        bug_intent.validate_review(saved,original,feedback+' A material change.',source())
    with pytest.raises(HTTPException) as denied:
        bug_evidence.validate_expectation_review(dict(task_contract=contract),{})
    assert denied.value.status_code==422
    bug_evidence.validate_expectation_review(dict(task_contract=contract),dict(expectation_review=saved['review_digest']))


@pytest.mark.parametrize('fault',['unseen','read_only','duplicate_criteria','missing_source','missing_test','changed_source'])
def test_scope_cannot_broaden_writes_or_hide_missing_criteria_and_source(fault):
    packet=source()
    proposed=copy.deepcopy(plan())
    if fault=='unseen':
        proposed['read_paths'].append('!!!TwichUI/unseen.lua')
    if fault=='read_only':
        proposed['read_paths'].append('tests/harness.lua')
        proposed['writable_paths'].append('tests/harness.lua')
    if fault=='duplicate_criteria':
        proposed['criteria'].append(proposed['criteria'][0])
    if fault=='missing_source':
        proposed['writable_paths']=['tests/test_sample.lua']
    if fault=='missing_test':
        proposed['writable_paths']=['!!!TwichUI/sample.lua']
    if fault=='changed_source':
        saved=bug_intent.bind(proposed,packet,{},'').model_dump(mode='json')
        packet['files'][0]['sha256']='d'*64
        with pytest.raises(ValueError):
            bug_intent.validate_review(saved,{},'',packet)
        return
    with pytest.raises(ValueError):
        bug_intent.bind(proposed,packet,{},'')


@pytest.mark.parametrize('path,eligible', [
    ('!!!TwichUI/discovered.lua', True), ('TwichUI_Settings/pages/Discovered.lua', True),
    ('tests/test_discovered.lua', True), ('tests/harness.lua', False),
    ('tests/fixtures/helper.lua', False), ('!!!TwichUI/libs/ThirdParty.lua', False),
    ('!!!TwichUI/../outside.lua', False)])
def test_new_inspection_can_propose_project_repair_but_cannot_promote_helpers(path, eligible):
    packet = source()
    packet['files'].append(dict(path=path,support_only=True,git_blob='d'*40,sha256='e'*64))
    assert path not in bug_intent.scope_candidates(packet)
    packet['inspection_windows'] = [dict(path=path,line=1,through_line=1,text='exact inspected code')]
    assert (path in bug_intent.scope_candidates(packet)) is eligible
    assert packet['files'][-1]['support_only'] is True  # No permission or historical metadata rewritten.


def test_discovered_source_enters_fresh_bound_scope_not_an_execution_grant():
    packet = source()
    path = '!!!TwichUI/discovered.lua'
    packet['files'].append(dict(path=path,support_only=True,git_blob='d'*40,sha256='e'*64))
    packet['inspection_windows'] = [dict(path=path,line=1,through_line=1,text='exact inspected code')]
    proposal = plan()
    proposal['read_paths'][0] = proposal['writable_paths'][0] = path
    intent = bug_intent.bind(proposal,packet,{},'')
    assert intent.checks_prepared is False
    assert bug_intent.validate_review(intent,{},'',packet) == intent
    packet['files'][-1]['sha256'] = 'f'*64
    with pytest.raises(ValueError):
        bug_intent.validate_review(intent,{},'',packet)
