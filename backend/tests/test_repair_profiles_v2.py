"""Actual Lua/Python behavior with controlled providers and confinement telemetry."""
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from workbench import bug_intent, worker, task_session
from workbench.db import task_run_offers,planning_jobs
from workbench.planning_provider import LocalProvider
from test_bug_authority import enable
from test_bug_workflow import enable_bugs, report
from test_bug_intent_workflow import scope
from test_everyday import installed, write_policy  # noqa: F401
from test_verification_profiles import git, PRODUCTION, TEST, GOOD_TEST, CORRECT
from test_grounded_workflow import ControlledPreparer


class ProfileRuntime(ControlledPreparer):
    def check(self, argv, *args, **kwargs):
        if os.environ.get('WORKBENCH_TEST_REAL_PROFILE'):
            from workbench.real_workspace import Runtime
            return Runtime.check(self,argv,*args,**kwargs)
        result=super().check(argv,*args,**kwargs)
        if self.repair_profile_v2 and argv==self.expected_checks()[-1]:
            result['inventory_version']='all-committed-tests-v2'
        return result


@pytest.mark.usefixtures('installed')
@pytest.mark.parametrize('language,kind,defer_review,rejected_reviews', [(language,kind,False,0) for language in ('lua','python') for kind in ('existing-failure','missing-regression','test-correction')]+[('python-artifact','existing-failure',False,0),('lua','missing-regression',True,0),('python','test-correction',False,1),('lua','missing-regression',False,1),('python','test-correction',False,3)])
def test_existing_real_failure_can_be_prepared_and_repaired_without_changing_original_assertions(client,headers,store,monkeypatch,language,kind,defer_review,rejected_reviews):
    source=enable_bugs(monkeypatch)
    settings_path=Path(os.environ['WORKBENCH_PREPARATION_CONFIG'])
    settings=json.loads(settings_path.read_text())
    settings['bug_plan_review']='source-grounded-intent-v1'
    settings['allowed_classes'].append('python-offline-bugfix')
    write_policy(settings_path,settings)
    # Every v2 Python inventory entry must contain actual discovered tests.
    for path in (source/'tests').glob('test_*.py'):
        path.write_text('import unittest\nclass Existing(unittest.TestCase):\n def test_environment(self): self.assertEqual(1+1,2)\n')
    proposed=scope()
    proposed['repair_profile']=dict(version=2,kind=kind)
    production,focused=PRODUCTION,TEST
    correct=CORRECT
    if language.startswith('python'):
        production,focused='tools/normalize.py','tests/test_normalize.py'
        (source/'tools').mkdir(exist_ok=True)
        (source/production).write_text('def normalize(value):\n return value.strip()\n')
        (source/focused).write_text('import unittest,runpy\nnormalize=runpy.run_path("../tools/normalize.py")["normalize"]\nclass Normalize(unittest.TestCase):\n def test_reported_case(self): self.assertEqual(normalize(" ALICE "),"alice")\n')
        correct='def normalize(value):\n return value.strip().lower()\n'
        proposed.update(read_paths=[production,focused],writable_paths=[production,focused],focused_test=focused)
    else:
        (source/TEST).write_text((source/TEST).read_text()+GOOD_TEST)
    if language=='python-artifact':
        (source/'tools/normalize.py').unlink()
        (source/'tests/test_normalize.py').unlink()
        production,focused='tools/artifact.py','tests/test_artifact.py'
        (source/production).write_text('from pathlib import Path\ndef render(path): Path(path).write_bytes(b"incorrect")\n')
        (source/focused).write_text('import unittest,runpy,tempfile\nfrom pathlib import Path\nrender=runpy.run_path("../tools/artifact.py")["render"]\nclass Render(unittest.TestCase):\n def test_exact_artifact(self):\n  with tempfile.TemporaryDirectory() as folder:\n   output=Path(folder)/"artifact.bin"\n   render(output)\n   self.assertEqual(output.read_bytes(),b"approved")\n')
        correct='from pathlib import Path\ndef render(path): Path(path).write_bytes(b"approved")\n'
        proposed.update(scope='Correct the generator output bytes required by the original contract.',read_paths=[production,focused],writable_paths=[production,focused],focused_test=focused,criteria=[dict(id='artifact-bytes',description='The generator writes the exact approved artifact bytes.')])
    if kind=='missing-regression':
        if language=='lua':
            (source/focused).write_text((source/focused).read_text().replace(GOOD_TEST,''))
        else:
            (source/focused).write_text((source/focused).read_text().replace('normalize(" ALICE ")','normalize("alice")'))
    if kind=='test-correction':
        (source/production).write_text(correct)
        (source/focused).write_text((source/focused).read_text().replace('== "alice")','== "ALICE")') if language=='lua' else (source/focused).read_text().replace('),"alice")','),"ALICE")'))
        proposed['writable_paths']=[focused]
    git(source,'add','.')
    git(source,'-c','user.name=Fixture','-c','user.email=fixture@invalid','commit','-qm','Reported failing baseline')
    original_test=(source/focused).read_bytes()
    enable(store)
    monkeypatch.setattr('workbench.trusted_preparation.Runtime',ProfileRuntime)
    monkeypatch.setattr('workbench.task_sessions.process_one',lambda *a,**k:False)
    program_calls=[]
    review_wait=[]
    reviews=[]
    def generate(self,packet,schema,expected_model):
        self.diagnostics=dict(actual_model=expected_model,generation_state='finished',accounting_state='complete',finish_reason='stop',
            request_completion_limit=6144,usage=dict(units='tokens',values=dict(prompt_tokens=40,completion_tokens=10,total_tokens=50)))
        from workbench.repair_profiles import IndependentProgram
        from workbench.bug_check_preparation import ProfileProgramReview
        if schema is bug_intent.Response:
            response=dict(finding='The committed behavior and original tests define a bounded normalization repair.',plan=proposed)
        elif schema is IndependentProgram:
            assert 'application_verification_plan' in packet
            assert len(packet['prior_design_reviews'])==len(reviews)
            program_calls.append('original-only')
            assert 'candidate' not in packet and packet['purpose']=='original-only independent behavior checks'
            program=('local normalize=dofile("../!!!TwichUI/normalize.lua")\nassert(normalize(" ALICE ")=="alice")' if language=='lua' else
                'import runpy\nnormalize=runpy.run_path("../tools/normalize.py")["normalize"]\nassert normalize(" ALICE ")=="alice"')
            response=dict(rationale='The original requirements specify trimming and lowercase output for uppercase input.',
                criteria=['normalization'],program=program,evidence=[dict(criterion='normalization',kind='report',path=None,quote='The intended behavior is trimmed lowercase output for uppercase input.')] if kind=='test-correction' else [],negative_control=[dict(path=production,old=':lower()' if language=='lua' else '.lower()',new='')] if kind=='test-correction' else [])
        elif schema is ProfileProgramReview:
            if defer_review and not review_wait:
                review_wait.append('verified-no-dispatch')
                from workbench.smart_provider import Waiting
                from workbench.ai_strategy import Decision
                self.diagnostics=dict(generation_state='not_started')
                raise Waiting(Decision(state='waiting',reason='Controlled allowance wait before review',recheck_at=1).model_dump())
            assert 'candidate execution proof is enforced later' in packet['review_instructions']
            assert 'actually changed candidate assertion line must fail' in str(packet['application_verification_plan'])
            reviews.append(packet['proposed_check'])
            response=dict(ready=len(reviews)>rejected_reviews,covered_criteria=['normalization'],reason='The original requirement needs a more explicit behavioral check.' if len(reviews)<=rejected_reviews else 'The independent assertions use original behavior and reject missing normalization.')
        else:
            raise AssertionError(schema)
        return response,expected_model,'local'
    monkeypatch.setattr(LocalProvider,'generate',generate)
    body=report()
    body.update(title='Normalize reported uppercase value',description=f'The intended behavior is trimmed lowercase output for uppercase input. In {production}, normalize uppercase names. The existing {focused} assertion fails for ALICE; preserve it.')
    if language=='python-artifact':
        body.update(title='Generator writes incorrect artifact bytes',description=f'{production} should write the exact bytes approved. The original {focused} assertion catches incorrect output; preserve this assertion.')
    saved=client.post('/api/intake',headers=headers,json=body)
    assert saved.status_code==200,saved.text
    identity=saved.json()['id']
    for _ in range(18):
        worker.tick(store)
    item=client.get('/api/intake/'+identity).json()
    with store.connect() as c:
        offered=c.execute(select(task_run_offers)).mappings().first()
    if rejected_reviews==3:
        assert not offered,item
        from workbench.db import model_attempts
        with store.connect() as c:
            attempt=c.execute(select(model_attempts).where(model_attempts.c.purpose=='check_preparation')).mappings().one()
            job=c.execute(select(planning_jobs).where(planning_jobs.c.kind=='behavioral_preparation')).mappings().one()
        assert job['status']=='failed' and attempt['outcome']=='failed'
        assert attempt['usage']['requests_observed']==6 and len(reviews)==3
        assert job['payload']['independent_program_claim']['review_protocol']=='composed-bounded-v1'
        return
    assert offered,item
    if kind in {'missing-regression','test-correction'}:
        assert program_calls==['original-only']*(1+rejected_reviews)
    if defer_review:
        assert review_wait==['verified-no-dispatch']
        with store.connect() as c:
            preparation=c.execute(select(planning_jobs).where(planning_jobs.c.intake_id==identity,planning_jobs.c.kind=='behavioral_preparation')).mappings().one()
        assert preparation['payload']['independent_program_resume']
    assert offered['snapshot']['specification']['verification_profile']=='offline-repair-v2'
    prepared=Path(offered['snapshot']['ready_preparation']['root'])
    if kind!='existing-failure':
        design=json.loads((prepared/'independent-program.json').read_text())
        assert len(design['design_reviews'])==1+rejected_reviews
        assert all(not r['accepted'] for r in design['design_reviews'][:-1])
        assert design['design_reviews'][-1]['accepted']
        assert len({r['evidence_id'] for r in design['design_reviews']})==1+rejected_reviews
    baseline=json.loads((prepared/'fresh-baseline-suite.json').read_text())
    assert baseline['status']==('pass' if kind=='missing-regression' else 'fail')
    assert focused.split('/')[-1] in baseline['out']
    run=prepared.parent/('profile-repair-'+language)
    run.mkdir(mode=0o700)
    from workbench.real_workspace import durable
    for name in ('recipe.json','recipe-oracle.lua'):
        durable(run/name,(prepared/name).read_bytes())
    runtime=ProfileRuntime(run)
    reference=json.loads((prepared/'independent-program-reference.json').read_text()) if kind!='existing-failure' else None
    task_session.provision(runtime,dict(control_patches={},**(dict(independent_program_reference=reference) if reference else {})))
    runtime._request_id,runtime._fence,runtime._context_sha=str(uuid4()),str(uuid4()),None
    if kind!='test-correction':
        (runtime.workspace/production).write_text(correct)
    if kind=='missing-regression':
        target=runtime.workspace/focused
        target.write_text(target.read_text()+(GOOD_TEST if language=='lua' else '\n def test_uppercase(self): self.assertEqual(normalize(" ALICE "),"alice")\n'))
    elif kind=='test-correction':
        target=runtime.workspace/focused
        repaired=target.read_text().replace('== "ALICE")','== "alice")') if language=='lua' else target.read_text().replace('),"ALICE")','),"alice")')
        if language=='lua':
            weakened=repaired.replace('assert(normalize("  ALICE ") == "alice")','assert(true)')
        else:
            weakened=repaired.replace('self.assertEqual(normalize(" ALICE "),"alice")','self.assertTrue(True)')
        target.write_text(weakened)
        assert runtime.candidate_feedback(lambda:True)['status']=='fail'
        target.write_text(repaired)
    result=runtime.candidate_feedback(lambda:True)
    assert result['status']=='pass',result
    if kind=='existing-failure':
        assert (runtime.workspace/focused).read_bytes()==original_test
    frozen=runtime.freeze_candidate(runtime._request_id,runtime._fence,lambda:True,
        dict(correction_count=0,read_input_errors=0),dict(usage=dict(status='complete',units='tokens',values=dict(prompt_tokens=1,completion_tokens=1,total_tokens=2))))
    assert frozen['verification']=='passed' and runtime.verify_frozen(frozen)
    assert (source/focused).read_bytes()==original_test
    assert ((source/production).read_text()==correct)==(kind=='test-correction')
    (run/'original-behavior.json').write_text('{}')
    assert not runtime.verify_frozen(frozen)


def test_original_requirement_evidence_is_required_for_a_test_correction(monkeypatch):
    from workbench.repair_profiles import IndependentProgram, RequirementEvidence, validate_requirement_evidence
    from workbench import regression_preparation
    proposed=scope()
    proposed['repair_profile']=dict(version=2,kind='test-correction')
    monkeypatch.setattr(regression_preparation,'repository',lambda:(None,'a'*40,{}))
    intent=dict(base='a'*40,plan=proposed)
    program=IndependentProgram(rationale='An independent statement of the original expected behavior.',criteria=['normalization'],program='assert(True)\nassert(True)',negative_control=[])
    with pytest.raises(ValueError,match='requirement evidence'):
        validate_requirement_evidence(program,intent,dict(description='Return lowercase output for uppercase input.'))
    program=program.model_copy(update=dict(evidence=[RequirementEvidence(
        criterion='normalization',kind='report',quote='The candidate explanation says this is correct.')]))
    with pytest.raises(ValueError,match='valid independent original'):
        validate_requirement_evidence(program,intent,dict(description='Return lowercase output for uppercase input.'))


def test_failed_inventory_remains_visible_and_cannot_certify_a_candidate():
    from workbench.repair_profiles import suite_observation
    from workbench.real_workspace import suite_inventory
    observed=dict(status='fail',rc=1,out='ok   test_reported.lua\nFAIL test_unrelated.py\n1 passed, 1 failed',usage=dict(cgroup_removed=True))
    assert suite_observation(observed,['test_reported.lua','test_unrelated.py'])
    assert not suite_inventory(observed,['test_reported.lua','test_unrelated.py'])
    assert not suite_observation({**observed,'out':observed['out'].replace('test_unrelated.py','test_reported.lua')},['test_reported.lua','test_unrelated.py'])


def test_environment_profile_retains_a_diagnosis_and_cannot_advertise_a_code_fix(monkeypatch):
    from test_bug_intent import source as packet_fixture, plan
    from workbench import regression_preparation
    monkeypatch.setattr(regression_preparation,'repository',lambda:(None,'a'*40,{'tests/run.sh':'d'*40,'tests/harness.lua':'e'*40}))
    monkeypatch.setattr(regression_preparation,'blob',lambda *args:b'controlled framework')
    proposed=plan()
    proposed['repair_profile']=dict(version=2,kind='environment')
    packet=packet_fixture()
    original=dict(title='Dependency missing',description='A required installed dependency cannot be imported.')
    request=dict(finding='The available evidence points to a missing dependency, without a production repair.',plan=proposed)
    contract,*_=bug_intent.resolve(request,packet,original,'')
    assert contract['scope_missing'] and not contract['verification_commands']
    assert 'No environmental diagnosis has been established' in contract['scope_missing'][0]
    packet['reproductions']=[dict(outcome='environment-or-fixture-failure',observed=True,base='a'*40)]
    contract,*_=bug_intent.resolve(request,packet,original,'')
    assert 'No source repair or draft PR' in contract['scope_missing'][0]
    assert not contract['execution_authorized'] and not contract['publication_authorized']


def test_only_bound_original_owner_clarifications_can_justify_test_correction(monkeypatch):
    from workbench.repair_profiles import IndependentProgram,validate_requirement_evidence
    from workbench import regression_preparation
    from workbench.visual_scope import digest
    monkeypatch.setattr(regression_preparation,'repository',lambda:(None,'a'*40,{}))
    original=dict(description='The original report leaves the expected normalization unclear.')
    reply='Return lowercase output for uppercase input; preserve the original production behavior.'
    plan={**scope(),'repair_profile':dict(version=2,kind='test-correction')}
    intent=dict(base='a'*40,plan=plan,report_binding=digest(dict(original=original,feedback=reply)))
    program=IndependentProgram(rationale='The recorded owner clarification establishes the intended behavior before implementation.',
        criteria=['normalization'],program='assert(True)\nassert(True)',evidence=[dict(criterion='normalization',kind='report',quote=reply)])
    validate_requirement_evidence(program,intent,original,reply)
    with pytest.raises(ValueError,match='clarification differs'):
        validate_requirement_evidence(program,intent,original,'A candidate suggested '+reply)
    with pytest.raises(ValueError,match='valid independent original'):
        validate_requirement_evidence(program,intent,original)
