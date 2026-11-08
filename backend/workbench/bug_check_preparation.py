"""Compile executable checks after approved intent, with actual isolated feedback.

The provider can propose tests, never source edits or verdict authority. Each
candidate is frozen privately, run on an original disposable tree, and retained.
Only an independently certified final recipe can become a separate run offer.
"""
import copy
import json
import os
import time
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from workbench import bug_evidence,bug_intent,bug_investigation,bug_source_planning
from workbench import intake,real_recipes,task_execution
from workbench.db import model_attempts,planning_jobs,write_transaction
from workbench.real_workspace import Runtime,durable as durable_artifact
from workbench.visual_scope import digest


def durable(path,data):
    """Construction diagnostics are private even outside a service UMask."""
    durable_artifact(path,data)
    if path.is_symlink() or path.stat().st_uid!=os.getuid():
        raise ValueError('Owned private construction evidence required')
    path.chmod(0o600)


def validate_evidence(intent,evidence):
    approved=bug_intent.Intent.model_validate(intent)
    checked=bug_evidence.BugEvidence.model_validate(evidence)
    plan=checked.plan
    scope=approved.plan
    if (plan.scope!=scope.scope or plan.writable_paths!=scope.writable_paths
            or set(plan.read_paths)!=set(scope.read_paths) or plan.focused_test!=scope.focused_test
            or checked.review_digest!=bug_evidence.review_digest(plan.model_dump(mode='json'),checked.bootstrap_source)):
        raise ValueError('Prepared tests cannot change approved behavior or source/write scope')
    return bug_evidence.compile_plan(checked.model_dump(mode='json'))


def load(root,reviewed):
    """Recheck exact private prepared bytes on every offer/dispatch reconstruction."""
    from workbench.trusted_preparation import owned
    path=owned(Path(root)/'prepared-bug-checks.json')
    if not path.is_file() or path.stat().st_mode & 0o077 or path.stat().st_size>65536:
        raise ValueError('Private bounded prepared check evidence is required')
    value=json.loads(path.read_text())
    intent=reviewed['bug_intent']
    if (set(value)!={'version','intent_digest','evidence','provider_review','attempt'}
            or value['version']!=1 or value['intent_digest']!=intent['review_digest']
            or not value['provider_review'].get('ready')
            or value['provider_review'].get('review_binding')!=value.get('attempt',{}).get('candidate_binding')):
        raise ValueError('Prepared checks differ from the approved intent or completed construction review')
    validate_evidence(intent,value['evidence'])
    return value


def construct(engine,ref,root,identity,stop_event=None,*,adapter=None):
    """One durable, bounded construction operation; active/unknown work never replays."""
    from workbench import codex_provider,everyday,provider_routes
    from workbench.planning_provider import LocalProvider,PlanningUnavailable
    from workbench.planning_response import create_evidence
    from workbench.trusted_preparation import configuration

    with engine.connect() as c:
        linked=task_execution.snapshot(c,**ref)
        reviewed=linked['proposal_review']['task_contract']
        intent=reviewed['bug_intent']
        job=c.execute(select(planning_jobs).where(planning_jobs.c.intake_id==ref['intake_id'],
            planning_jobs.c.kind=='planning',planning_jobs.c.revision==linked['proposal_review']['revision'])).mappings().one()
        packet=copy.deepcopy(job['payload']['bug_inspection_packet'])
        route=provider_routes.run_selection(c,linked)
        local_selection=everyday.local_selection(c) if not route else None
    # Revalidate exact source, owner feedback, policy and approval before constructing.
    bug_evidence.validate_expectation_review(linked['proposal_review'],linked['approval']['payload'])
    bug_intent.validate_review(intent,linked['original'],reviewed['review_feedback'],packet)
    if packet['policy_sha256']!=digest(configuration(engine)):
        from workbench.trusted_preparation import relocated_policy
        if not relocated_policy(engine,packet['policy_sha256'],configuration(engine)):
            raise ValueError('Preparation policy changed after scope approval')
        packet['policy_sha256']=digest(configuration(engine))
    packet['plan_review']='source-grounded-lua-v1'
    operation=str(uuid4())
    claim=root/'check-construction-claim.json'
    if claim.exists():
        raise ValueError('Check construction already has a durable claim; reconcile it before renewal')
    def stopped():
        return bool(stop_event and stop_event.is_set())
    adapter=adapter or (codex_provider.Planner(route,stopped) if route else LocalProvider(stop_event,ends_at=time.monotonic()+1800))
    adapter.request_seconds=300
    model=route['model'] if route else local_selection.model
    evidence=create_evidence(operation,bug_evidence.BugPlanningResponse,None,packet['files'])
    adapter.response_evidence=evidence
    # Tool-free model + readonly sandbox, no implementation or consequential tool.
    caps=dict(source_writes=False,execution_dispatch=False,external_action_tools=False)
    durable(claim,json.dumps(dict(id=operation,ref=ref,intent_digest=intent['review_digest'],
        capabilities=caps),sort_keys=True).encode())
    with write_transaction(engine) as c:
        preparation=c.execute(select(planning_jobs).where(planning_jobs.c.intake_id==ref['intake_id'],
            planning_jobs.c.kind=='behavioral_preparation',planning_jobs.c.revision==linked['proposal_review']['revision'])).mappings().one()
        if (preparation['payload']['ref']!=ref or preparation['status']!='running'
                or preparation['payload']['preparation_claim']['root']!=str(root)):
            raise ValueError('A current durable worker preparation claim is required')
        c.execute(planning_jobs.update().where(planning_jobs.c.id==preparation['id']).values(payload={**preparation['payload'],
            'local_planning_receipt':job['payload']['local_planning_receipt'],'check_construction':dict(id=operation,capabilities=caps)}))
        c.execute(model_attempts.insert().values(id=operation,job_id=preparation['id'],task_id=ref['intake_id'],
            purpose='check_preparation',source='workbench',backend='codex' if route else 'local',
            requested_model=model,policy_revision=route['policy_revision'] if route else local_selection.policy_revision,
            requested_effort=route['effort'] if route else None,outcome='running',started_at=intake.now(),
            routing_reason='Approved behavior scope; isolated check construction with original-tree feedback. No repair authority.'))
    attempts=[]
    seen=set()
    def check(candidate,current):
        # The model may supply test code; this exact approved scope cannot change.
        stamp=digest(candidate.get('plan',{}))
        if stamp in seen:
            raise ValueError('An unchanged check was repeated after retained sandbox feedback')
        seen.add(stamp)
        plan=candidate.get('plan') or {}
        scope=intent['plan']
        if (plan.get('scope')!=scope['scope'] or plan.get('writable_paths')!=scope['writable_paths']
                or set(plan.get('read_paths',[]))!=set(scope['read_paths'])
                or plan.get('focused_test')!=scope['focused_test']):
            return dict(category='scope',message='Copy the exact approved scope/read paths/write paths/focused test. Tests cannot broaden or change it.')
        contract,_,question,limitation=bug_investigation.resolve(candidate,current,linked['original'],reviewed['review_feedback'])
        if question or limitation:
            raise ValueError('Executable check preparation requires a concrete check')
        prepared=contract['bug_evidence']
        compiled=validate_evidence(intent,prepared)
        folder=root/('check-candidate-'+str(len(attempts)+1))
        folder.mkdir(mode=0o700)
        spec=real_recipes.from_review_for_preparation(engine,ref,compiled,identity,prepared)
        durable(folder/'recipe.json',json.dumps(spec,sort_keys=True).encode())
        durable(folder/'recipe-oracle.lua',compiled['oracle'].encode())
        runtime=Runtime(folder,stop_event)
        try:
            runtime.prepare()
            argv=runtime.expected_checks()[-2]
            result=runtime.check(argv,runtime.workspace/'tests',[runtime.root/'oracle.lua'],timeout=120)
            durable(folder/'original-check.json',json.dumps(result,sort_keys=True).encode())
            from workbench.verification_profiles import behavioral_outcome
            outcome=behavioral_outcome(result,compiled['behavior_id'])
            attempts.append(dict(folder=str(folder),outcome=outcome,evidence=prepared))
            if outcome!='fail':
                return dict(category='original-check',outcome=outcome,
                    message='The original must exhibit the approved bug; fixture errors or an already-passing original are not proof.',
                    diagnostic=result.get('out','')[-5000:])
            return None
        finally:
            # Retain exact check/evidence, remove only the owned disposable tree.
            everyday.clean_workspace(folder)
    adapter.preparation_check=check
    adapter.approved_intent=intent
    failure=None
    try:
        raw,actual,basis,final_packet=bug_source_planning.generate(adapter,packet,linked['original'],
            reviewed['review_feedback'],stopped,model,max_requests=16)
        if not raw.get('plan') or not attempts or attempts[-1]['outcome']!='fail':
            raise ValueError('Construction did not produce a tested original failure')
        contract,_,_,_=bug_investigation.resolve(raw,final_packet,linked['original'],reviewed['review_feedback'])
        saved=dict(version=1,intent_digest=intent['review_digest'],evidence=contract['bug_evidence'],
            provider_review=adapter.preparation_review,
            attempt=dict(id=operation,candidate_binding=digest(raw)))
        validate_evidence(intent,saved['evidence'])
        durable(root/'prepared-bug-checks.json',json.dumps(saved,sort_keys=True).encode())
        (root/'prepared-bug-checks.json').chmod(0o600)
        return saved
    except (ValueError,OSError,PlanningUnavailable) as error:
        failure=error
        raise ValueError('Check construction held: '+str(error)[:1200]) from error
    finally:
        facts=dict(adapter.diagnostics)
        usage=facts.get('usage')
        complete=isinstance(usage,dict) and usage.get('status')=='complete'
        with write_transaction(engine) as c:
            c.execute(model_attempts.update().where(model_attempts.c.id==operation).values(
                actual_model=model if complete else None,usage=usage,
                outcome='completed' if failure is None else 'failed' if complete else 'interrupted_unknown',
                finished_at=intake.now()))
        durable(root/'check-construction-result.json',json.dumps(dict(
            id=operation,content_accepted=failure is None,generation_state=facts.get('generation_state'),
            usage=usage,known_usage=facts.get('known_usage'),candidate_count=len(attempts)),sort_keys=True).encode())
        if evidence:
            evidence.close()
