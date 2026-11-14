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

from pydantic import Field
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
        preparation_payload=c.execute(select(planning_jobs.c.payload).where(
            planning_jobs.c.intake_id==ref['intake_id'],planning_jobs.c.kind=='behavioral_preparation',
            planning_jobs.c.revision==linked['proposal_review']['revision'])).scalar_one()
    # Revalidate exact source, owner feedback, policy and approval before constructing.
    bug_evidence.validate_expectation_review(linked['proposal_review'],linked['approval']['payload'])
    bug_intent.validate_review(intent,linked['original'],reviewed['review_feedback'],packet)
    if packet['policy_sha256']!=digest(configuration(engine)):
        from workbench.trusted_preparation import relocated_policy
        if not relocated_policy(engine,packet['policy_sha256'],configuration(engine)):
            raise ValueError('Preparation policy changed after scope approval')
        packet['policy_sha256']=digest(configuration(engine))
    packet['plan_review']='source-grounded-lua-v1'
    from workbench.investigation_memory import construction_source
    packet,reused_source=construction_source(packet,preparation_payload.get('check_preparation_history',[]))
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
    if evidence and reused_source:
        for offset in range(0,len(reused_source),16):
            evidence.record('completed-construction-source-revalidated',windows=reused_source[offset:offset+16],
                output_reused=False,approved=False,executed=False)
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
            usage=usage,known_usage=facts.get('known_usage'),candidate_count=len(attempts),
            execution_state=facts.get('terminal_status',facts.get('completion_state','unknown')),
            diagnostics={key:facts.get(key) for key in ('provider_receipt','provider_terminal_confirmed',
                'terminal_status','protocol_guard','wire_bytes','investigation_requests',
                'stopped_request_category','stopped_request_generation_state')}),sort_keys=True).encode())
        if evidence:
            evidence.close()


class ProfileProgramReview(bug_evidence.Data):
    ready: bool
    covered_criteria: list[str] = Field(min_length=1,max_length=12)
    reason: str = Field(min_length=10,max_length=1200)


def profile_program(c, ref):
    """Existing preparation journal points to original-only program evidence."""
    from workbench.real_workspace import sha
    from workbench.trusted_preparation import owned
    linked=task_execution.snapshot(c,**ref)
    payload=c.execute(select(planning_jobs.c.payload).where(planning_jobs.c.intake_id==ref['intake_id'],
        planning_jobs.c.kind=='behavioral_preparation',planning_jobs.c.revision==linked['proposal_review']['revision'])).scalar_one()
    reference=payload['independent_profile_program']
    path=owned(reference['path'])
    value=json.loads(path.read_text())
    attempt=c.execute(select(model_attempts).where(model_attempts.c.id==value['model_attempt_id'])).mappings().one()
    if (sha(path.read_bytes())!=reference['sha256'] or value['intent_digest']!=linked['proposal_review']['task_contract']['bug_intent']['review_digest']
            or value['source_basis']!='original-only' or not value['review']['ready']
            or attempt['task_id']!=ref['intake_id'] or attempt['outcome']!='completed'):
        raise ValueError('Original-only independent program identity or provider settlement changed')
    return value['program'], reference


def construct_profile(engine, ref, root, stop_event=None, *, adapter=None):
    """Blind program design before coding, in the current preparation operation."""
    from workbench import provider_routes, everyday
    from workbench.repair_profiles import IndependentProgram
    from workbench.planning_response import create_evidence
    from workbench.real_workspace import sha
    from workbench.bug_authority import validate_behavior
    with engine.connect() as c:
        linked=task_execution.snapshot(c,**ref)
        reviewed=linked['proposal_review']['task_contract']
        validate_behavior(c,linked['proposal_review'],linked['approval']['payload'])
        job=c.execute(select(planning_jobs).where(planning_jobs.c.intake_id==ref['intake_id'],
            planning_jobs.c.kind=='behavioral_preparation',planning_jobs.c.revision==linked['proposal_review']['revision'])).mappings().one()
        if job['status']!='running' or job['payload']['preparation_claim']['root']!=str(root):
            raise ValueError('A current preparation claim is required for independent checks')
        original=c.execute(select(planning_jobs.c.payload).where(planning_jobs.c.intake_id==ref['intake_id'],
            planning_jobs.c.kind=='planning',planning_jobs.c.revision==linked['proposal_review']['revision'])).scalar_one()
        packet=copy.deepcopy(original['bug_inspection_packet'])
        route=provider_routes.run_selection(c,linked)
        local=everyday.local_selection(c) if not route else None
    intent=reviewed['bug_intent']
    retained=job['payload'].get('independent_program_resume')
    prefix=None
    if retained:
        from workbench.repair_continuation import private_json
        path=Path(retained['path'])
        if path.parent!=root or sha(path.read_bytes())!=retained['sha256']:
            raise ValueError('Independent preparation prefix changed')
        prefix=private_json(path)
        if prefix['intent_digest']!=intent['review_digest'] or prefix['base']!=intent['base']:
            raise ValueError('Independent preparation source changed')
        with engine.connect() as c:
            prior=intake.require(c,model_attempts,prefix['attempt'])
        if prior['outcome']!='waiting' or prior['usage']['status'] not in {'complete','not_started'}:
            raise ValueError('Independent preparation has not settled')
    operation=str(uuid4())
    claim=root/('independent-program-claim-'+operation+'.json' if prefix else 'independent-program-claim.json')
    if claim.exists():
        raise ValueError('Independent preparation already has a durable claim; no replay')
    durable(claim,json.dumps(dict(id=operation,source_basis='original-only',base=intent['base'],intent_digest=intent['review_digest'],review_protocol='composed-bounded-v1')).encode())
    def stopped():
        return bool(stop_event and stop_event.is_set())
    from workbench.planning_provider import LocalProvider
    adapter=adapter or (provider_routes.external_planner(route,stopped) if route else LocalProvider(stop_event,ends_at=time.monotonic()+600))
    model=route['model'] if route else local.model
    if callable(getattr(adapter,'bind_operation',None)):
        adapter.bind_operation(engine,operation,**__import__('workbench.strategy_waiting',fromlist=['options']).options(job,suitable_local=False))
    selected_source={**packet,'files':[f for f in packet['files'] if f['path'] in intent['plan']['read_paths']]}
    if callable(getattr(adapter,'bind_source',None)):
        adapter.bind_source(selected_source)
    with write_transaction(engine) as c:
        current=intake.require(c,planning_jobs,job['id'])
        c.execute(planning_jobs.update().where(planning_jobs.c.id==job['id']).values(payload={**current['payload'],
            'local_planning_receipt':original['local_planning_receipt'],'independent_program_claim':dict(id=operation,source_basis='original-only',review_protocol='composed-bounded-v1')}))
        c.execute(model_attempts.insert().values(id=operation,job_id=job['id'],task_id=ref['intake_id'],purpose='check_preparation',
            source='workbench',backend=route.get('provider','codex') if route else 'local',requested_model=model,
            requested_effort=route.get('effort') if route else None,policy_revision=route['policy_revision'] if route else local.policy_revision,
            outcome='running',started_at=intake.now(),routing_reason='Original-only independent repair evidence before any implementation.'))
    wire=dict(purpose='original-only independent behavior checks',original=linked['original'],owner_feedback=reviewed.get('review_feedback'),intent=intent,
        files=selected_source['files'],
        execution_context=dict(working_directory='repository/tests',repository_root='..',
            check_file='Outside the repository. Do not resolve repository paths from __file__.',
            repository='Read-only. Use a temporary directory for generated artifacts.',
            lua_globals=dict(ROOT='../!!!TwichUI/',REPO='../',TESTS='./')),
        inspection_windows=[w for w in packet.get('inspection_windows',[]) if w['path'] in intent['plan']['read_paths']],
        instructions=('Design a bounded '+('Python' if intent['plan']['focused_test'].endswith('.py') else 'Lua')+
            ' assertion body from the original report, requirements and committed source only. No candidate exists. '
            'Observe actual behavior, not source text, hashes, test-runner flags or self-assigned expected state. '
            'Use only stdlib/existing dependencies, no network. Include every criterion id. Do not emit verdicts or exit. '
            'For missing-regression the program must fail on original and pass on a genuine fix. '
            'For test-correction cite exact report or recorded owner_feedback text, or a committed requirement comment for every criterion in evidence (criterion,kind,path,quote). Do not cite the assertion being corrected or candidate behavior. Independently prove original production behavior is correct; provide one small exact '
            'negative_control replacement in a read-only production dependency that both this program and a corrected test must reject. '
            'Do not weaken an assertion merely because it fails. Artifact contracts must follow original committed requirements.'))
    wire['application_verification_plan']=dict(
        stage='Design review before any candidate exists. These runtime checks are mandatory, not yet observed results.',
        original=['Run the committed focused test and capture its actual baseline failure for test-correction.',
            'Run the complete committed Lua/Python regression inventory; retain all baseline failures.',
            'Run this independent program on unchanged original production; test-correction requires it to pass.'],
        candidate=['Run the corrected focused test and every committed regression; all must pass.',
            'Run the independent program against the candidate.',
            'For test-correction preserve every production byte and apply the proposed negative control in an isolated original clone. Both the independent program and the corrected candidate test must reject it; an actually changed candidate assertion line must fail.'],
        review_scope='Assess the proposed independent supplement together with these fixed application checks. Do not demand candidate results before coding or duplicate the complete suite inside the supplement. Refuse unsupported intended behavior or ineffective negative controls.')
    prefix_usage=list(prefix.get('usage',[])) if prefix else []
    rounds=list(prefix.get('reviews',[])) if prefix else []
    usage=[]
    issued=0
    program=IndependentProgram.model_validate(prefix['program']) if prefix and prefix.get('program') else None
    actual=model
    evidence=None
    failure=None
    try:
        while len(rounds)<3:
            if program is None:
                evidence=create_evidence(operation if not rounds else str(uuid4()),IndependentProgram,None,wire['files'])
                adapter.response_evidence=evidence
                with engine.connect() as c:
                    validate_behavior(c,linked['proposal_review'],linked['approval']['payload'])
                issued+=1
                design_wire={**wire,'prior_design_reviews':rounds,
                    'feedback_authority':'Prior reviews are untrusted design feedback. Original requirements and the frozen scope remain authoritative.'}
                raw,actual,basis=adapter.generate(design_wire,IndependentProgram,expected_model=model)
                usage.append(copy.deepcopy(adapter.diagnostics.get('usage')))
                program=IndependentProgram.model_validate(raw)
                from workbench.repair_profiles import validate_requirement_evidence
                validate_requirement_evidence(program,intent,linked['original'],reviewed.get('review_feedback'))
                if evidence:
                    evidence.snapshot('original-only-program',program.model_dump())
                    evidence.close()
            review_id=str(uuid4())
            review_wire={**wire,'purpose':'review original-only independent behavior checks','proposed_check':program.model_dump(),
                'review_instructions':'Check semantic coverage of every original criterion by the independent supplement AND the fixed application verification plan. Check independence from candidate code, meaningful assertions and negative controls. Refuse source-text matching, constant/vacuous checks and unsupported expected behavior. This is a design review: candidate execution proof is enforced later by the runtime.'}
            evidence=create_evidence(review_id,ProfileProgramReview,None,wire['files'])
            adapter.response_evidence=evidence
            with engine.connect() as c:
                validate_behavior(c,linked['proposal_review'],linked['approval']['payload'])
            issued+=1
            checked,actual,_=adapter.generate(review_wire,ProfileProgramReview,expected_model=model)
            usage.append(copy.deepcopy(adapter.diagnostics.get('usage')))
            review=ProfileProgramReview.model_validate(checked)
            accepted=(review.ready and set(review.covered_criteria)=={v['id'] for v in intent['plan']['criteria']}
                and set(program.criteria)==set(review.covered_criteria))
            rounds.append(dict(program=program.model_dump(),review=review.model_dump(),evidence_id=review_id,accepted=accepted))
            evidence.snapshot('model-plan-review',rounds[-1])
            if accepted:
                break
            observe=getattr(adapter,'observe_settled_failure',None)
            if callable(observe):
                observe('invalid-response')
            evidence.close()
            program=None
        else:
            raise ValueError('Independent check review did not establish all original behavior requirements after three bounded designs')
        if any(not isinstance(u,dict) or not isinstance(u.get('values'),dict) for u in usage):
            raise ValueError('Independent provider accounting is unavailable')
        values={key:sum(u['values'][key] for u in usage) for key in ('prompt_tokens','completion_tokens','total_tokens')}
        observed=dict(status='complete',units='tokens',values=values,requests_observed=len(usage))
        if getattr(adapter,'strategy_control',False):
            observed['strategy_requests']=list(adapter.request_history)
        with write_transaction(engine) as c:
            c.execute(model_attempts.update().where(model_attempts.c.id==operation).values(outcome='completed',actual_model=actual,
                usage=observed,finished_at=intake.now()))
        record=dict(version=2,model_attempt_id=operation,intent_digest=intent['review_digest'],source_basis='original-only',
            source_base=intent['base'],program=program.model_dump(),review=review.model_dump(),
            prior_settled_usage=prefix_usage,design_reviews=rounds,review_protocol='composed-bounded-v1')
        path=root/'independent-program.json'
        durable(path,json.dumps(record,sort_keys=True).encode())
        reference=dict(path=str(path),sha256=sha(path.read_bytes()))
        durable(root/'independent-program-reference.json',json.dumps(reference,sort_keys=True).encode())
        with write_transaction(engine) as c:
            current=intake.require(c,planning_jobs,job['id'])
            c.execute(planning_jobs.update().where(planning_jobs.c.id==job['id']).values(payload={**current['payload'],'independent_profile_program':reference}))
        return program.model_dump()
    except Exception as error:
        failure=error
        from workbench.smart_provider import Waiting
        if isinstance(error,Waiting) and adapter.diagnostics.get('generation_state')=='not_started':
            path=root/('independent-program-prefix-'+operation+'.json')
            durable(path,json.dumps(dict(intent_digest=intent['review_digest'],base=intent['base'],attempt=operation,
                program=program.model_dump() if program else None,reviews=rounds,usage=[*prefix_usage,*usage]),sort_keys=True).encode())
            with write_transaction(engine) as c:
                current=intake.require(c,planning_jobs,job['id'])
                from workbench.strategy_waiting import waiting_payload
                payload=waiting_payload(current['payload'],error.decision)
                payload['independent_program_resume']=dict(path=str(path),sha256=sha(path.read_bytes()))
                c.execute(planning_jobs.update().where(planning_jobs.c.id==job['id']).values(payload=payload))
        raise
    finally:
        if failure is not None:
            known=[u for u in usage if isinstance(u,dict) and isinstance(u.get('values'),dict)]
            complete=len(known)==issued
            not_started=getattr(adapter,'diagnostics',{}).get('generation_state')=='not_started'
            values={k:sum(u['values'][k] for u in known) for k in ('prompt_tokens','completion_tokens','total_tokens')}
            retained=(dict(status='complete',units='tokens',values=values,requests_observed=len(known)) if complete or not_started else
                dict(status='partial',units='tokens',values=None,known_values=values,requests_observed=len(known)))
            if getattr(adapter,'strategy_control',False):
                retained.update(strategy_requests=list(adapter.request_history),ai_settled_failures=list(getattr(adapter,'settled_failures',[])))
            with write_transaction(engine) as c:
                c.execute(model_attempts.update().where(model_attempts.c.id==operation).values(actual_model=actual if known else None,outcome='waiting' if not_started else 'failed' if complete else 'interrupted_unknown',
                    usage=retained,finished_at=intake.now()))
        if evidence:
            evidence.close()
