"""Bounded prior rejections are untrusted hints, never plans or authority."""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from workbench.bug_plan_review import ReviewResponse
from workbench.investigation_memory import review_lessons
from workbench.planning_response import Evidence


@pytest.mark.parametrize('fault',['none','unfinished','approved','hash','binding','symlink','mode','duplicate'])
def test_reuse_only_complete_bound_private_rejection(tmp_path,monkeypatch,fault):
    from workbench import everyday
    prior=str(uuid4())
    monkeypatch.setattr(everyday,'configuration',lambda engine:SimpleNamespace(evidence_roots=[str(tmp_path)]))
    journal=Evidence(tmp_path,prior,ReviewResponse)
    journal.record('envelope',generation_state='uncertain' if fault=='unfinished' else 'finished',
        accounting_state='complete',completion='stop',model_matches=1)
    value=dict(review_binding='a'*64,ready=fault=='approved',issues=[] if fault=='approved' else [
        dict(category='source',explanation='The selected original function does not contain the proposed cache.')])
    journal.snapshot('model-plan-review',value)
    journal.record('plan-self-check',binding='b'*64 if fault=='binding' else 'a'*64,ready=fault=='approved')
    if fault=='duplicate':
        journal.record('plan-self-check',binding='a'*64,ready=False)
    journal.close()
    path=journal.directory/'model-plan-review.json'
    if fault=='hash':
        path.write_text(path.read_text()+' ')
    if fault=='mode':
        path.chmod(0o644)
    if fault=='symlink':
        target=tmp_path/'outside.json'
        path.rename(target)
        path.symlink_to(target)
    before={str(p):p.stat().st_mtime_ns for p in [journal.file,path]}
    result=review_lessons(prior,None)
    assert {str(p):p.stat().st_mtime_ns for p in [journal.file,path]}==before
    if fault=='none':
        assert result['predecessor']==prior and result['issues']==value['issues']
        assert 'Untrusted' in result['note'] and not {'plan','expected','approved','code'}&result.keys()
    else:
        assert result is None


@pytest.mark.parametrize('fault',['none','changed','cycle'])
def test_source_only_renewal_keeps_validated_origin_feedback(tmp_path,monkeypatch,fault):
    from workbench import everyday
    monkeypatch.setattr(everyday,'configuration',lambda engine:SimpleNamespace(evidence_roots=[str(tmp_path)]))
    prior=str(uuid4())
    original=Evidence(tmp_path,prior,ReviewResponse)
    original.record('envelope',generation_state='finished',accounting_state='complete',completion='stop',model_matches=1)
    value=dict(review_binding='a'*64,ready=False,issues=[dict(category='source',explanation='The source does not contain that proposed cache.')])
    original.snapshot('model-plan-review',value)
    original.record('plan-self-check',binding='a'*64,ready=False)
    original.close()
    lessons=review_lessons(prior,None)
    fresh=str(uuid4())
    parent=Evidence(tmp_path,fresh,ReviewResponse)
    if fault=='changed':
        lessons['issues'][0]['explanation']='Changed unbound feedback.'
    if fault=='cycle':
        lessons['predecessor']=fresh
    parent.record('rejected-plan-feedback-reused',**lessons,approved=False,executed=False)
    parent.close()
    answer=review_lessons(fresh,None)
    if fault=='none':
        assert answer['predecessor']==prior and answer['issues']==value['issues']
    else:
        assert answer is None
