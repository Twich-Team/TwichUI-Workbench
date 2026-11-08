"""Read the omitted committed startup; do not write an addon reproduction."""
import copy
import os
from pathlib import Path

import pytest

from workbench import bug_investigation,bug_source_planning
from workbench.source_bootstrap_context import complete
from workbench.trusted_preparation import configuration
from test_bug_workflow import enable_bugs,report,CLIENT_TEST
from test_everyday import installed  # noqa: F401
from test_verification_profiles import git


@pytest.mark.usefixtures('installed')
def test_complete_existing_bootstrap_is_visible_without_provider_or_execution(store,monkeypatch):
    root=enable_bugs(monkeypatch)
    path=root/CLIENT_TEST
    marker='local original_startup_marker = "PRIVATE_COMMITTED_STARTUP"\n'
    path.write_text(marker+'-- Existing long startup context\n'*100+path.read_text())
    git(root,'add','.')
    git(root,'-c','user.name=Fixture','-c','user.email=fixture@invalid','commit','-qm','Controlled long existing bootstrap')
    original=report(True)
    initial=bug_investigation.context(original,'',configuration(store))
    item=next(f for f in initial['files'] if f['path']==CLIENT_TEST)
    assert item['client_bootstrap'] and item['client_bootstrap']['through_line']>100
    assert not any('PRIVATE_COMMITTED_STARTUP' in e['text'] for e in item['excerpts'])
    packet=copy.deepcopy(initial)
    priority,windows,chars,facts=complete(packet,45000,lambda:False)
    assert facts['complete'] and facts['test_path']==CLIENT_TEST and chars>0
    assert windows and priority[0]['line']==1 and 'PRIVATE_COMMITTED_STARTUP' in priority[0]['text']
    assert bug_source_planning.verify_packet(initial,packet)==packet
    wire=bug_source_planning.next_wire(packet,original,'',priority,'Read-only existing startup.')
    delivered=next(f for f in wire['files'] if f['path']==CLIENT_TEST)
    assert any('PRIVATE_COMMITTED_STARTUP' in e['text'] for e in delivered['excerpts'])
    assert Path(os.environ['WORKBENCH_RESEARCH_ROOT'])==root


@pytest.mark.usefixtures('installed')
def test_insufficient_read_budget_does_not_claim_or_add_complete_startup(store,monkeypatch):
    root=enable_bugs(monkeypatch)
    path=root/CLIENT_TEST
    path.write_text('-- Existing startup\n'*100+path.read_text())
    git(root,'add','.')
    git(root,'-c','user.name=Fixture','-c','user.email=fixture@invalid','commit','-qm','Bounded existing bootstrap')
    packet=bug_investigation.context(report(True),'',configuration(store))
    before=copy.deepcopy(packet)
    priority,windows,chars,facts=complete(packet,0,lambda:False)
    assert not facts['complete'] and priority==[] and windows==[] and chars==0 and packet==before


def test_unique_context_budget_does_not_charge_overlapping_source_twice():
    p=dict(plan_review='source-grounded-lua-v1',files=[dict(path='tests/test_example.lua',
        excerpts=[dict(line=1,text='one\ntwo')])],inspection_windows=[dict(path='tests/test_example.lua',
        line=2,text='two\nthree')])
    assert bug_source_planning.source_characters(p)==len('one\ntwo\nthree\n')
    assert bug_source_planning.read_limit(p)==262144
    p['inspection_windows'].append(dict(path='tests/test_example.lua',line=1,text='one\ntwo'))
    assert bug_source_planning.source_characters(p)==len('one\ntwo\nthree\n')
    p['inspection_windows'][-1]['text']='changed'
    with pytest.raises(ValueError):
        bug_source_planning.source_characters(p)


def test_legacy_investigation_keeps_its_existing_source_bound():
    assert bug_source_planning.read_limit(dict(plan_review='source-grounded-v2'))==45000


def test_complete_startup_precedes_other_current_reads_under_retention_bound():
    from workbench.source_memory import retain
    packet=dict(base='fixed',files=[dict(path='aaa.lua',excerpts=[]),dict(path='tests/test_example.lua',excerpts=[])])
    wire=copy.deepcopy(packet)
    startup=dict(path='tests/test_example.lua',line=1,text='setup\n'*129,coverage='complete committed test bootstrap')
    other=dict(path='aaa.lua',line=1,text='other\n'*1000)
    retain(wire,packet,[other,startup],1000)
    assert wire['files'][1]['excerpts'][0]['text'].splitlines()==['setup']*129
    assert wire['source_memory']['retained_characters']<=1000


@pytest.mark.usefixtures('installed')
def test_inspected_context_and_single_request_have_separate_enforced_bounds(store,monkeypatch):
    from workbench.bug_evidence import InvestigationRead
    root=enable_bugs(monkeypatch)
    paths=[f'!!!TwichUI/reading_context_{i}.lua' for i in range(6)]
    text=(' -- bounded committed source context '+('x'*255)+'\n')*160
    for path in paths:
        (root/path).write_text(text)
    git(root,'add','.')
    git(root,'-c','user.name=Fixture','-c','user.email=fixture@invalid','commit','-qm','Inspection context fixture')
    initial=dict(base=git(root,'rev-parse','HEAD').strip(),files=[],policy_sha256='controlled',
                 plan_review='source-grounded-lua-v1')
    packet=copy.deepcopy(initial)
    for path in paths[:3]:
        windows,_,_=bug_source_planning.inspect(packet,[InvestigationRead(path=path,start=1,end=160)],
                                               page_lines=160,result_budget=60000)
    assert bug_source_planning.source_characters(packet)>128000
    assert bug_source_planning.verify_packet(initial,packet)==packet
    wire=bug_source_planning.next_wire(packet,report(),'',windows, 'Inspect bounded committed source.',
                                      text_budget=128000,requested=windows)
    assert wire['source_memory']['retained_characters']<=128000
    assert wire['source_memory']['omitted_characters']>0
    assert all(w['provided'] for w in wire['inspection_result']['visible_requested_windows'])
    for path in paths[3:]:
        bug_source_planning.inspect(packet,[InvestigationRead(path=path,start=1,end=160)],
                                    page_lines=160,result_budget=60000)
    with pytest.raises(ValueError,match='bound'):
        bug_source_planning.verify_packet(initial,packet)


def test_source_limit_hold_explains_actual_stage_without_calling_it_invalid_output():
    from workbench.planning_transport import scenario_failure_message
    message=scenario_failure_message('inspection_limit')
    assert 'reading or turn limit' in message and 'No changes started' in message
    assert 'could not validate' not in message
