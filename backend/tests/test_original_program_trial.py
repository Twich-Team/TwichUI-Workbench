"""Original-only trial receipts, disclosure, nonreplay and safety boundaries."""
import json
from pathlib import Path

import pytest

from workbench import bug_check_preparation as preparation
from workbench.repair_profiles import IndependentProgram
from test_bug_workflow import enable_bugs
from test_everyday import installed  # noqa: F401


@pytest.mark.usefixtures('installed')
@pytest.mark.parametrize('fault', ['none','timeout','oom','environment','authority','stop','source-write'])
def test_trial_saves_private_output_and_never_replays_an_incomplete_or_unsafe_check(store,monkeypatch,tmp_path,fault):
    from workbench import regression_preparation
    enable_bugs(monkeypatch)
    _,base,_=regression_preparation.repository()
    intent=dict(base=base,review_digest='b'*64,plan=dict(focused_test='tests/test_trial.py',
        repair_profile=dict(kind='test-correction')))
    monkeypatch.setattr(preparation.task_execution,'snapshot',lambda *a,**k:
        dict(proposal_review=dict(task_contract=dict(bug_intent=intent))))
    root=tmp_path/'trial'
    root.mkdir(mode=0o700)
    program=IndependentProgram(rationale='Verify actual original behavior before any candidate exists.',
        criteria=['original'],program='values=None\nassert values[0]=="expected"')
    calls=[]
    stops=[]
    validations=[]
    secret='UNDISCLOSED_DEPENDENCY_SOURCE_MUST_STAY_PRIVATE'
    def validate():
        validations.append(True)
        if fault=='authority' and len(validations)==3:
            raise ValueError('Owner pause/revocation or source binding changed')
    def run(argv,cwd,timeout,**options):
        calls.append(argv)
        assert timeout==120 and options['ro'] and not options.get('private_loopback')
        if fault=='source-write':
            (Path(cwd)/'unapproved.txt').write_text('changed')
        if fault=='stop':
            stops.append(True)
        return dict(status='timeout' if fault=='timeout' else 'fail',rc=1,
            out=f'File "{argv[-1]}", line 6\n{secret}\n'+
                ('ModuleNotFoundError' if fault=='environment' else 'TypeError')+'\nWB_BEHAVIOR bug-reproducer FAIL\n',
            usage=dict(cgroup_removed=True,oom_kill=int(fault=='oom')))
    monkeypatch.setattr('workbench.real_confine.run',run)
    if fault=='none':
        first=preparation.profile_trial(store,{},root,program,validate,lambda:bool(stops))
        assert not first['accepted'] and first['program_locations']==[dict(line=2,body='assert values[0]=="expected"')]
        assert secret not in json.dumps(first)
        assert preparation.profile_trial(store,{},root,program,validate,lambda:False)==first
        assert len(calls)==1
        folder=next(root.glob('original-program-trial-*'))
        assert secret in (folder/'result.json').read_text()
        summary=json.loads((folder/'summary.json').read_text())
        summary['accepted']=True
        (folder/'summary.json').write_text(json.dumps(summary))
        with pytest.raises(ValueError,match='evidence changed'):
            preparation.profile_trial(store,{},root,program,validate,lambda:False)
    else:
        with pytest.raises((ValueError,RuntimeError)):
            preparation.profile_trial(store,{},root,program,validate,lambda:bool(stops))
        assert len(calls)==1
        folder=next(root.glob('original-program-trial-*'))
        assert (folder/'result.json').exists() and not (folder/'summary.json').exists()
        with pytest.raises((ValueError,OSError)):
            preparation.profile_trial(store,{},root,program,lambda:None,lambda:False)
    assert len(calls)==1
