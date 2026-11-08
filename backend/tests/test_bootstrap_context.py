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
