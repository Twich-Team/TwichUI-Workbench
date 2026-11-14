"""Committed reproductions use existing isolation and journal, with no shell tool."""
import copy
import json
import os
from pathlib import Path

import pytest
from sqlalchemy import select
from workbench import bug_intent, worker
from workbench.db import planning_jobs, model_attempts
from workbench.planning_provider import LocalProvider
from workbench.investigation_checks import Check, command, classify, run, verify_observations
from test_bug_authority import enable
from test_bug_workflow import enable_bugs, report
from test_bug_intent_workflow import scope
from test_everyday import installed, write_policy  # noqa: F401


@pytest.mark.parametrize('capability,path',[('lua-test','tests/../secret.lua'),('python-test','tools/install.py'),('repository-suite','tests/run.sh;anything')])
def test_model_cannot_supply_commands_arguments_or_unapproved_programs(capability,path):
    with pytest.raises(ValueError):
        Check(capability=capability,path=path)


def test_duplicate_read_paths_are_normalized_without_weakening_write_or_source_checks():
    original=scope()
    proposed=copy.deepcopy(original)
    proposed['read_paths']=[*proposed['read_paths'],*proposed['read_paths']]
    assert bug_intent.Plan.model_validate(proposed).model_dump()==bug_intent.Plan.model_validate(original).model_dump()
    proposed['writable_paths'].append(proposed['writable_paths'][0])
    with pytest.raises(ValueError):
        bug_intent.Plan.model_validate(proposed)
    assert command(Check(capability='python-test',path='tests/test_generator.py'))==(['python3','-B','-m','unittest','-v','test_generator'],'tests')
    assert classify(dict(status='fail',rc=1,out='AssertionError'))=='assertion-failure'
    assert classify(dict(status='fail',rc=1,out='ModuleNotFoundError'))=='environment-or-fixture-failure'


@pytest.mark.usefixtures('installed')
@pytest.mark.parametrize('native', [False, True])
def test_normal_investigation_runs_a_check_retains_observation_and_proposes_grounded_scope(client,headers,store,monkeypatch,native):
    if native and not os.environ.get('WORKBENCH_TEST_REAL_REPRODUCTION'):
        pytest.skip('Native confinement acceptance runs in its dedicated delegated service')
    from workbench import real_confine
    from workbench.bug_source_planning import verify_packet
    source_root=enable_bugs(monkeypatch)
    if native:
        from test_verification_profiles import git
        focused=source_root/scope()['focused_test']
        focused.write_text(focused.read_text()+"\nassert(false, 'controlled reported failure')\n")
        git(source_root,'add','.')
        git(source_root,'-c','user.name=Fixture','-c','user.email=fixture@invalid','commit','-qm','Controlled failing baseline')
    config=Path(os.environ['WORKBENCH_PREPARATION_CONFIG'])
    value=json.loads(config.read_text())
    value['bug_plan_review']='source-grounded-intent-v1'
    write_policy(config,value)
    enable(store)
    seen=[]
    calls=[]
    def confined(argv,cwd,timeout,**kwargs):
        seen.append(dict(argv=argv,cwd=cwd,timeout=timeout,options=kwargs))
        assert 'workbench-verify-' in cwd and kwargs['ro']
        assert not kwargs.get('net')
        return dict(status='fail',rc=1,out='test_normalize.lua:4: assertion failed!',usage=dict(cgroup_removed=True))
    original_confine=real_confine.run
    def execute(argv,cwd,timeout,**kwargs):
        seen.append(dict(argv=argv,cwd=cwd,timeout=timeout,options=kwargs))
        return original_confine(argv,cwd,timeout,**kwargs)
    monkeypatch.setattr(real_confine,'run',execute if native else confined)
    def generate(self,packet,schema,expected_model):
        calls.append(copy.deepcopy(packet))
        self.diagnostics=dict(actual_model=expected_model,generation_state='finished',accounting_state='complete',finish_reason='stop',
            request_completion_limit=6144,usage=dict(units='tokens',values=dict(prompt_tokens=40,completion_tokens=10,total_tokens=50)))
        result=(dict(finding='Run the existing committed normalization test before explaining its cause.',checks=[dict(capability='lua-test',path=scope()['focused_test'])])
            if len(calls)==1 else dict(finding='The check failed; missing normalization is a hypothesis supported by the inspected implementation.',plan={**scope(),'repair_profile':dict(version=2,kind='existing-failure')}))
        return result,expected_model,'local'
    monkeypatch.setattr(LocalProvider,'generate',generate)
    response=client.post('/api/intake',headers=headers,json=report())
    assert response.status_code==200,response.text
    identity=response.json()['id']
    assert worker.tick(store)
    item=client.get('/api/intake/'+identity).json()
    assert len(calls)==2,item
    assert len(seen)==1
    observation=calls[-1]['reproduction_observations'][0]
    assert observation['observed'] and observation['outcome']=='assertion-failure'
    assert observation['diagnosis_verified'] is False
    assert 'out' not in observation and 'evidence_root' not in observation
    with store.connect() as c:
        job=c.execute(select(planning_jobs).where(planning_jobs.c.intake_id==identity)).mappings().one()
        attempt=c.execute(select(model_attempts).where(model_attempts.c.job_id==job['id'])).mappings().one()
    packet=job['payload']['bug_inspection_packet']
    verify_observations(packet)
    # The same durable request reuses its result, never reruns the test.
    reused=run(store,attempt['id'],packet,dict(capability='lua-test',path=scope()['focused_test']),lambda:False)
    assert reused==packet['reproductions'][0] and len(seen)==1
    # A stale/corrupted check cannot ground a plan or qualify a repair.
    folder=Path(reused['evidence_root'])
    (folder/'result.json').write_text('{}')
    with pytest.raises(ValueError):
        verify_observations(packet)
    # The retained model finding stays a hypothesis, independent of that refusal.
    assert item['proposals'] and not item['decisions']
    assert not any(Path(call['cwd']).exists() for call in seen)
    assert verify_packet  # The production saved-packet validator invokes receipt verification.
