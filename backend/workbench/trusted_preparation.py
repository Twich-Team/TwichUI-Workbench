"""Supported same-user local preparation. Never exposed through ordinary HTTP."""
from workbench import worker_operation as operation

import json
import os
from pathlib import Path
import re
from subprocess import SubprocessError
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select

from workbench import intake, real_execution, task_budget, task_execution
from workbench.db import planning_jobs, task_run_offers, write_transaction
from workbench.real_workspace import Runtime
from workbench.visual_scope import digest


def configuration(engine):
    """Private installation input, bound to this store. Never request/model data."""
    path = os.environ.get('WORKBENCH_PREPARATION_CONFIG')
    if not path:
        raise ValueError('Application preparation is not configured; an owned storage/identity policy is required.')
    if not Path(path).is_absolute():
        raise ValueError('Preparation configuration must use an absolute owned path')
    return read_configuration(engine, Path(path))


def read_configuration(engine, path):
    from pydantic import Field, StrictBool, StrictInt
    from typing import Literal
    from workbench.schemas import StrictModel

    class Configuration(StrictModel):
        version: Literal[1]
        database: str
        database_identity: list[StrictInt] = Field(min_length=2, max_length=2)
        repository: Literal['Twich-Team/TwichUI']
        allowed_classes: list[Literal['lua-regression-tests', 'lua-offline-bugfix']] = Field(max_length=2)
        output_root: str
        identity: dict[str, str]
        scenario_generation: StrictBool = False
        bug_plan_review: Literal['source-grounded-v1', 'source-grounded-v2', 'source-grounded-lua-v1', 'source-grounded-intent-v1'] | None = None

    file = owned(path)
    if not file.is_file() or file.stat().st_mode & 0o077 or file.stat().st_size > 8192:
        raise ValueError('Preparation configuration exceeds its bound')
    config = Configuration.model_validate(json.loads(file.read_text())).model_dump(exclude_none=True)
    db = owned(engine.url.database)
    if config['database'] != str(db) or config['database_identity'] != [db.stat().st_dev, db.stat().st_ino]:
        raise ValueError('Preparation policy belongs to another application store')
    output = Path(config['output_root'])
    if not output.is_absolute() or output.resolve() != output or not owned(output).is_dir():
        raise ValueError('Configure an existing absolute owned preparation directory')
    identity = config['identity']
    if (set(identity) != {'name', 'email'} or any(not 1 <= len(v) <= 160 or any(ord(c) < 32 for c in v) for v in identity.values())):
        raise ValueError('Configure a bounded local publication identity')
    if not config['allowed_classes']:
        raise ValueError('Preparation policy denies this task class')
    if len(set(config['allowed_classes'])) != len(config['allowed_classes']):
        raise ValueError('Preparation task classes must be unique')
    return config


def validate_reviewed_policy(engine, reviewed):
    from workbench.visual_scope import digest
    grounding = reviewed.get('scenario_grounding')
    if grounding:
        policy = configuration(engine)
        if ((digest(policy) != grounding['policy_sha256'] and not relocated_policy(engine,grounding['policy_sha256'],policy))
                or reviewed['category'] not in policy['allowed_classes']):
            raise ValueError('Preparation policy differs from the reviewed source policy; request a fresh proposal before preparing or approving a run.')


def relocated_policy(engine, previous_sha, current):
    """Installation coordinates may move; reviewed permission semantics cannot.

    Only exact retained private installation policies in configured evidence roots
    can establish equivalence. Provider/model/source/approval bindings are checked
    separately by their existing owners. No caller-supplied policy is accepted.
    """
    from workbench import everyday
    coordinates={'database','database_identity','output_root'}
    semantic={k:v for k,v in current.items() if k not in coordinates}
    matches=[]
    for value in dict.fromkeys(everyday.configuration(engine).evidence_roots):
        stage=Path(value)
        if not re.fullmatch(r'everyday-outcome-[a-f0-9]{7,40}',stage.name):
            continue
        path=stage/'ops/preparation.json'
        try:
            owned(stage)
            info=owned(path).stat()
            if info.st_mode&0o077 or not path.is_file() or info.st_size>8192:
                continue
            old=json.loads(path.read_text())
            if digest(old)!=previous_sha:
                continue
            database=owned(stage/'artifacts/workbench.sqlite3')
            stat=database.stat()
            if (old.get('database')!=str(database) or old.get('database_identity')!=[stat.st_dev,stat.st_ino]
                    or old.get('output_root')!=str(stage/'release/artifacts/runs/preparations')
                    or {k:v for k,v in old.items() if k not in coordinates}!=semantic):
                continue
            matches.append(str(path))
        except (OSError,ValueError,TypeError):
            continue
    return len(matches)==1


def owned(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.exists():
        raise ValueError('Existing owned paths without links required')
    info = path.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise ValueError('Trusted preparation requires same-user paths without shared write access')
    return path


def register(engine, ref, evidence_root, *, recovery_id=None):
    """Bind independently certified evidence; cannot run checks or issue authority.

    Local filesystem access is the existing trusted operator boundary. No request
    data can provide code, checks, a certificate verdict or execution permissions.
    """
    if engine.url.drivername != 'sqlite' or not engine.url.database:
        raise ValueError('An existing local SQLite store is required')
    for path in (engine.url.database, evidence_root):
        owned(path)
    return task_budget.register_reviewed_offer(
        engine, ref, Runtime(Path(evidence_root).absolute()), recovery_id=recovery_id
    )


def prepare_regression(engine, ref, evidence_root, identity, *, validate_policy=None, recovery=None, stop_event=None):
    """Reusable offline bridge; no task-specific Lua/patch/registration code.

    The existing application worker supplies reviewed scenario data and private
    configured storage/identity. No model or operational activation is called here.
    """
    from workbench import bug_evidence, real_recipes, task_session, regression_preparation
    from workbench.regression_evidence import compile_plan
    from workbench.real_workspace import durable

    owned(engine.url.database)
    root = owned(evidence_root)
    if not root.is_dir() or any(root.iterdir()):
        raise ValueError('A fresh empty owned preparation directory is required; retained evidence is never overwritten')
    with engine.connect() as c:
        linked=task_execution.snapshot(c,**ref)
    reviewed=linked['proposal_review'].get('task_contract') or {}
    validate_reviewed_policy(engine,reviewed)
    prepared=None
    if reviewed.get('bug_intent'):
        from workbench.repair_cycle import oracle
        from workbench.api_mock_contract import source_support
        plan=reviewed['bug_intent']['plan']
        if any(not path.endswith('.lua') for path in plan['writable_paths']):
            raise ValueError('This investigation plan is review-only; the isolated repair runtime supports Lua.')
        api_contracts,_=source_support(regression_preparation.repository()[0],reviewed['resolved_base'],plan['read_paths'])
        compiled=dict(oracle=oracle(plan['focused_test'],api_contracts),behavior_id='bug-reproducer',
            read_paths=plan['read_paths'],focused_test=plan['focused_test'])
        template=dict(control_patches={})
    elif reviewed.get('category')=='lua-offline-bugfix' and reviewed.get('bug_evidence'):
        compiled=bug_evidence.compile_plan(reviewed['bug_evidence'])
        template=dict(control_patches={})
    elif reviewed.get('category')=='lua-regression-tests' and reviewed.get('behavioral_evidence'):
        prepared_scenarios=reviewed['behavioral_evidence']
        compiled=compile_plan(prepared_scenarios)
        template=dict(scenario_preparation=prepared_scenarios)
    else:
        raise ValueError('A reviewed behavior scope, reproducer or regression example is required.')
    bug_evidence.validate_expectation_review(linked['proposal_review'],linked['approval']['payload'])
    with engine.connect() as c:
        spec=real_recipes.from_review(c,ref,oracle=compiled['oracle'],behavior_id=compiled['behavior_id'],
            read_paths=compiled['read_paths'],focused_test=compiled['focused_test'],identity=identity,prepared=prepared,
            repair_cycle=bool(reviewed.get('bug_intent')),
            api_contracts=api_contracts if reviewed.get('bug_intent') else None)
        if recovery:
            _validate_recovery_binding(c,linked,spec,recovery['authorization'])
    durable(root / 'recipe.json', json.dumps(spec, sort_keys=True).encode())
    durable(root / 'recipe-oracle.lua', compiled['oracle'].encode())
    try:
        if validate_policy:
            validate_policy()
        runtime = Runtime(root, stop_event=stop_event)
        task_session.provision(runtime, template)
        if validate_policy:
            validate_policy()
        # Reconstruct canonical Runtime and independently re-evaluate, exactly as
        # existing registration does. No generated verdict is trusted.
        selected = register(engine, ref, root, recovery_id=recovery['id'] if recovery else None)
    except ValueError as error:
        outcome = dict(status='hold', message=str(error)[:1600], root=str(root), execution_authorized=False)
        durable(root / 'behavioral-preparation-outcome.json', json.dumps(outcome, sort_keys=True).encode())
        regression_preparation.record_behavioral_outcome(engine, ref, linked['proposal_review']['binding'], outcome)
        raise
    outcome = dict(status='prepared', message=('The approved scope, isolated workspace and existing baseline are ready. Repair and test development require separate run approval; correctness is not yet verified.' if spec.get('repair_cycle') else
                   'The reviewed behavior passed independent baseline checks. Implementation requires separate approval.'),
                   root=str(root), execution_authorized=False)
    durable(root / 'behavioral-preparation-outcome.json', json.dumps(outcome, sort_keys=True).encode())
    regression_preparation.record_behavioral_outcome(engine, ref, linked['proposal_review']['binding'], outcome)
    return selected


def main():
    import sys
    from workbench.db import engine_for
    try:
        database, intake_id, task_id, approval_id, root = sys.argv[1:]
        owned(database)  # Check the supplied path before engine_for resolves it.
        selected = register(engine_for(database), dict(intake_id=intake_id, task_id=task_id,
                            approval_id=approval_id), root)
        print(json.dumps(dict(offer_id=selected['id'], fingerprint=selected['fingerprint'],
                              execution_authority=False)))
    except (ValueError, OSError, HTTPException) as error:
        print('Preparation held: ' + str(error.detail if isinstance(error, HTTPException) else error), file=sys.stderr)
        return 2
    return 0


def _terminal_inputs(c, job, binding):
    """Check the original failed claim and reconstruct its exact reviewed inputs."""
    from workbench import grounded_scenarios, real_recipes, regression_preparation

    payload = job['payload']
    claim = payload.get('preparation_claim') or {}
    result = payload.get('preparation_result') or {}
    if (job['kind'] != 'behavioral_preparation' or job['status'] != 'failed'
            or job['attempts'] != 1 or not job['finished_at']
            or result.get('status') != 'hold' or result.get('review_binding') != binding
            or claim.get('review_binding') != binding):
        raise ValueError('Recovery requires one terminally failed preparation claim with the exact review binding')
    config = configuration(c.engine)
    policy_sha = digest(config)
    original_root = Path(config['output_root']) / job['id']
    if (claim.get('policy_sha256') != policy_sha or claim.get('root') != str(original_root)
            or claim.get('identity') != config['identity']):
        raise ValueError('Preparation policy or original evidence root changed. Request a fresh review.')
    if not owned(original_root).is_dir() or original_root.stat().st_mode & 0o077:
        raise ValueError('Original preparation requires a private owned evidence directory')
    if any(os.path.lexists(original_root / name) for name in (
            'preparation-completed.json', 'controls-passed.json')):
        raise ValueError('Original preparation already has certification or completion evidence')
    outcome = original_root / 'behavioral-preparation-outcome.json'
    if (outcome.is_symlink() or not outcome.is_file()
            or outcome.stat().st_uid != os.getuid() or outcome.stat().st_size > 8192):
        raise ValueError('A retained terminal preparation hold is required')
    saved_outcome = json.loads(outcome.read_text())
    if (not isinstance(saved_outcome, dict) or saved_outcome.get('status') != 'hold'
            or saved_outcome.get('root') != str(original_root)
            or saved_outcome.get('execution_authorized') is not False):
        raise ValueError('A retained terminal preparation hold is required')

    ref = payload['ref']
    try:
        linked = task_execution.snapshot(c, **ref)
    except task_execution.Waiting as error:
        raise ValueError('Current scope, expectation approval and task mapping are required. Request a fresh review.') from error
    review = linked['proposal_review']
    approval = linked['approval']['payload']
    reviewed = review.get('task_contract') or {}
    if (review['binding'] != binding or job['revision'] != review['revision']
            or job['intake_id'] != ref['intake_id'] or approval.get('run_budget')
            or approval.get('execution_continuation')
            or reviewed.get('category') != 'lua-regression-tests'):
        raise ValueError('The original preparation requires current scope-only human approval for this exact revision')

    if not (original_root / 'recipe.json').is_file():
        raise ValueError('The original preparation recipe is unavailable')
    spec = real_recipes.load(original_root)
    if not spec or spec['identity'] != config['identity']:
        raise ValueError('The original preparation recipe is missing or differs from its policy')
    actual = real_recipes.from_review(
        c, ref, oracle=(original_root / 'recipe-oracle.lua').read_text(),
        behavior_id=spec['behavior']['id'], read_paths=spec['read_paths'],
        focused_test=spec['focused_test'], identity=config['identity'],
    )
    if spec != actual:
        raise ValueError('The original recipe differs from the current reviewed repository or evidence')
    grounding = reviewed.get('scenario_grounding')
    if grounding:
        feedback = reviewed['review_feedback']
        original = linked['original']
        packet = grounded_scenarios.context(
            original, feedback, regression_preparation.prepare(original, feedback), config
        )
        if packet['evidence_sha256'] != grounding['evidence_sha256']:
            raise ValueError('Committed grounding evidence changed. Request a fresh review.')
        draft = grounded_scenarios.GroundedDraft.model_validate(
            dict(scenarios=reviewed['behavioral_evidence']['scenarios'])
        )
        grounded_scenarios.validate_generation(draft, packet)
        _validate_model_selection(c, linked)
    return dict(
        proposal_id=review['id'], proposal_revision=review['revision'],
        review_binding=binding, scope_approval_id=ref['approval_id'],
        task_binding=digest(linked), policy_sha256=policy_sha,
        recipe_sha256=digest(spec), original_claim_sha256=digest(claim),
        original_outcome_sha256=digest(saved_outcome),
    )


def _validate_model_selection(c, linked):
    from workbench import everyday, provider_routes

    grounding = linked['proposal_review']['task_contract'].get('scenario_grounding') or {}
    if everyday.enabled() and linked['original'].get('local_planning') in provider_routes.CHOICES:
        provider_routes.run_selection(c, linked)
    elif grounding.get('model_selection') and grounding.get('basis') == 'local' and not grounding.get('fallback_reason'):
        from workbench.model_policy import grounded_local_selection
        if grounded_local_selection(c).model_dump() != grounding['model_selection']:
            raise ValueError('The saved grounding model policy changed. Request a fresh review.')


def _require_no_offer(c, proposal_id):
    if c.execute(select(task_run_offers.c.id).where(
            task_run_offers.c.proposal_id == proposal_id).limit(1)).first():
        raise ValueError('This proposal already has a run offer. Preparation recovery is held.')


def _recovery_root(job, recovery_id):
    original = Path(job['payload']['preparation_claim']['root'])
    return original.with_name(f'{job["id"]}-recovery-{recovery_id}')


def request_recovery(engine, job_id, binding):
    """Record one explicit same-user authorization. A failed recovery cannot be reauthorized."""
    if str(UUID(job_id)) != job_id or not re.fullmatch(r'[a-f0-9]{64}', binding):
        raise ValueError('Use the full preparation job ID and exact review binding')
    with write_transaction(engine) as c:
        job = intake.require(c, planning_jobs, job_id)
        if 'recovery' in job['payload']:
            raise ValueError('This preparation job already has a one-shot recovery authorization')
        authorization = _terminal_inputs(c, job, binding)
        _require_no_offer(c, authorization['proposal_id'])
        identity = str(uuid4())
        recovery = dict(
            id=identity, original_job_id=job_id, original_attempt=job['attempts'],
            requested_at=intake.now(), requested_by_uid=os.getuid(), authorization=authorization,
            status='queued', attempts=0, root=str(_recovery_root(job, identity)), execution_authorized=False,
            message='One offline preparation recovery is authorized for the targeted worker.',
        )
        _save_recovery(c, job, recovery)
    return recovery


def _save_recovery(c, job, recovery):
    c.execute(planning_jobs.update().where(planning_jobs.c.id == job['id']).values(
        payload={**job['payload'], 'recovery': recovery}
    ))


def _validate_recovery_binding(c, linked, spec, authorization):
    if (digest(linked) != authorization['task_binding']
            or digest(spec) != authorization['recipe_sha256']):
        raise ValueError('Review or recipe changed after authorizing preparation recovery')
    _validate_model_selection(c, linked)


def validate_recovery_offer(c, linked, spec, root, recovery_id, existing):
    """Fence recovery registration inside the canonical offer writer transaction."""
    review = linked['proposal_review']
    recovery = c.execute(select(planning_jobs.c.payload['recovery']).where(
        planning_jobs.c.intake_id == linked['intake_id'],
        planning_jobs.c.kind == 'behavioral_preparation',
        planning_jobs.c.revision == review['revision'],
    )).scalar()
    if recovery_id is None:
        if (recovery and not existing and recovery['authorization']['review_binding'] == review['binding']
                and (recovery['root'] == str(root) or recovery['status'] in {'running', 'completed'})):
            raise ValueError('This reviewed preparation has a durable recovery claim')
        return
    if (not recovery or recovery['id'] != recovery_id or recovery['status'] != 'running'
            or recovery['root'] != str(root)
            or _recovery_owner(recovery) != real_execution.owner_identity()):
        raise ValueError('Preparation recovery no longer owns this certification claim')
    _validate_recovery_binding(c, linked, spec, recovery['authorization'])
    if not existing:
        _require_no_offer(c, review['id'])


def _recovery_owner(recovery):
    return recovery.get('reconciliation_owner') or recovery['claim']['owner']


def process_recovery(engine, job_id):
    """Targeted worker dispatch. An interrupted claim can only reconcile complete evidence."""
    with write_transaction(engine) as c:
        job = intake.require(c, planning_jobs, job_id)
        recovery = job['payload'].get('recovery')
        if not recovery:
            raise ValueError('No recovery authorization for this preparation job')
        if recovery['status'] in {'completed', 'failed'}:
            return recovery
        if recovery['status'] not in {'queued', 'running'}:
            raise ValueError('Unknown preparation recovery state')
        reconciling = recovery['status'] == 'running'
        if reconciling and real_execution.alive(_recovery_owner(recovery)):
            raise ValueError('The preparation recovery claim is active')
        authorization = recovery['authorization']
        root = Path(recovery['root'])
        try:
            if _terminal_inputs(c, job, authorization['review_binding']) != authorization:
                raise ValueError('The preparation recovery binding changed. Request a fresh review.')
            if (recovery['original_job_id'] != job_id or recovery['original_attempt'] != job['attempts']
                    or recovery['requested_by_uid'] != os.getuid() or root != _recovery_root(job, recovery['id'])):
                raise ValueError('The recovery authorization does not match the original claim, owner or evidence root')
            if not reconciling:
                _require_no_offer(c, authorization['proposal_id'])
                if os.path.lexists(root):
                    raise ValueError('Recovery evidence path already exists without a claim')
        except (ValueError, OSError, HTTPException, SubprocessError) as error:
            recovery = _recovery_result(recovery, _hold(error))
            _save_recovery(c, job, recovery)
            return recovery
        owner = real_execution.owner_identity()
        if reconciling:
            recovery = {**recovery, 'reconciliation_owner': owner}
        else:
            claim = dict(
                owner=owner, root=str(root), policy_sha256=authorization['policy_sha256'],
                review_binding=authorization['review_binding'],
                identity=job['payload']['preparation_claim']['identity'], started_at=intake.now(),
            )
            recovery = {**recovery, 'status': 'running', 'attempts': 1, 'claim': claim,
                        'message': 'The worker claimed the one offline preparation recovery.'}
        _save_recovery(c, job, recovery)

    result = _run_claim(engine, job['payload']['ref'], recovery['claim'], reconciling, recovery=recovery)
    with write_transaction(engine) as c:
        current = intake.require(c, planning_jobs, job_id)
        if current['payload'].get('recovery') != recovery:
            raise ValueError('Recovery journal claim changed. Inspect retained evidence.')
        recovery = _recovery_result(recovery, result)
        _save_recovery(c, current, recovery)
    return recovery


def _recovery_result(recovery, result):
    return {**recovery, 'status': 'completed' if result['status'] == 'prepared' else 'failed',
            'result': result, 'finished_at': intake.now(), 'message': result['message']}


def _hold(error):
    detail = error.detail if isinstance(error, HTTPException) else str(error)
    from workbench.storage_resources import StorageHold
    return dict(status='hold', message=str(detail)[:1600], execution_authorized=False,
                **(dict(storage=dict(code=error.code,observation=error.observation)) if isinstance(error,StorageHold) else {}))


@operation.isolated
def process_one(engine, *, stop_event=None):
    """Dispatch from the existing job journal, with one durable external-work claim.

    Completed artifacts can reconcile after receipt loss. Uncertain interrupted
    work holds; neither a lease expiry nor an HTTP retry repeats preparation.
    """
    from workbench import everyday

    recovering = False
    with write_transaction(engine) as c:
        row = c.execute(select(planning_jobs).where(operation.eligible(c, planning_jobs.c.intake_id)).where(planning_jobs.c.kind == 'behavioral_preparation',
            planning_jobs.c.status.in_(['queued', 'running'])).order_by(planning_jobs.c.created_at).limit(1)).mappings().first()
        if not row:
            return False
        operation.selected(planning_jobs, row)
        job = dict(row)
        payload = dict(job['payload'])
        ref = payload['ref']
        if job['status'] == 'running':
            if real_execution.alive(payload['preparation_claim']['owner']):
                return False
            # Take only reconciliation ownership; never repeat check processes.
            claim = dict(payload['preparation_claim'])
            claim['owner'] = real_execution.owner_identity()
            payload['preparation_claim'] = claim
            c.execute(planning_jobs.update().where(planning_jobs.c.id == job['id']).values(payload=payload))
            recovering, result = True, None
        else:
            try:
                config = configuration(engine)
                linked = task_execution.snapshot(c, **ref)
                validate_reviewed_policy(engine, linked['proposal_review'].get('task_contract') or {})
                if everyday.enabled():
                    from workbench.db import intakes
                    original = intake.require(c, intakes, ref['intake_id'])
                    from workbench.provider_routes import CHOICES
                    if original['payload'].get('local_planning') not in CHOICES:
                        raise ValueError('Only locally approved everyday requests can prepare checks.')
                    try:
                        everyday.require_room(everyday.configuration(engine))
                    except everyday.CapacityHold as error:
                        if job['message'] != str(error):
                            resource=dict(state='waiting',code=error.code,automatic=True,message=str(error),observed_at=intake.now())
                            c.execute(planning_jobs.update().where(planning_jobs.c.id == job['id']).values(
                                message=str(error),payload={**payload,'storage_recovery':resource}))
                        return False
                payload.pop('storage_recovery',None)
                binding = linked['proposal_review']['binding']
                round_id=payload.get('preparation_round_id')
                if round_id is not None and str(UUID(round_id))!=round_id:
                    raise ValueError('Invalid preparation round identity')
                root = Path(config['output_root']) / (job['id']+'-round-'+round_id if round_id else job['id'])
                if root.exists():
                    raise ValueError('Preparation output already exists without a journal claim; retained evidence requires inspection.')
                claim = dict(root=str(root), owner=real_execution.owner_identity(), review_binding=binding,
                    policy_sha256=digest(config), identity=config['identity'])
                payload['preparation_claim'] = claim
                c.execute(planning_jobs.update().where(planning_jobs.c.id == job['id']).values(
                    status='running', payload=payload, attempts=job['attempts']+1, message='Preparing reviewed behavioral controls; no execution authority.'))
                result = None
            except (ValueError, OSError, HTTPException) as error:
                result = _hold(error)
        if result is not None:
            c.execute(planning_jobs.update().where(planning_jobs.c.id == job['id']).values(status='completed' if result['status']=='prepared' else 'failed',
                payload={**payload, 'preparation_result':result}, finished_at=intake.now(), message=result['message']))
            return True
    result = _run_claim(engine, ref, claim, recovering, stop_event=stop_event)
    with write_transaction(engine) as c:
        current = intake.require(c, planning_jobs, job['id'])
        if current['payload'].get('preparation_claim') != claim:
            raise ValueError('Preparation journal claim changed; retained output requires inspection')
        c.execute(planning_jobs.update().where(planning_jobs.c.id == job['id']).values(status='completed' if result['status']=='prepared' else 'failed',
            payload={**current['payload'], 'preparation_result':result}, finished_at=intake.now(), message=result['message']))
    # Existing diagnostic projection supplies specific browser holds, including
    # failures before Runtime could create its own outcome receipt.
    try:
        from workbench.regression_preparation import record_behavioral_outcome
        record_behavioral_outcome(engine, ref, claim['review_binding'], result)
    except (ValueError, HTTPException):
        pass  # Superseded scope cannot receive a diagnostic from the old claim.
    return True


def _run_claim(engine, ref, claim, reconciling, *, recovery=None, stop_event=None):
    try:
        # Claim commits before any output/check process starts.
        def validate_policy():
            if digest(configuration(engine)) != claim['policy_sha256']:
                raise ValueError('Preparation policy changed before dispatch or certification registration')
        validate_policy()
        root = Path(claim['root'])
        if reconciling:
            marker = root / 'preparation-completed.json'
            if marker.is_symlink() or not marker.is_file():
                raise ValueError('Preparation was interrupted after its durable claim. Inspect retained evidence; automatic replay is denied.')
            selected = register(engine, ref, root, recovery_id=recovery['id'] if recovery else None)
        else:
            root.mkdir(mode=0o700)
            selected = prepare_regression(engine, ref, root, claim['identity'],
                                          validate_policy=validate_policy, recovery=recovery, stop_event=stop_event)
        from workbench import everyday
        cleaned = not everyday.enabled() or everyday.clean_workspace(root)
        message = 'Reviewed scenarios independently checked; a certified run offer is ready for its separate budget review.'
        if not cleaned:
            message += ' An owned scratch copy needs attention.'
        result = dict(status='prepared', message=message,
            offer_id=selected['id'], review_binding=claim['review_binding'], execution_authorized=False)
    except SubprocessError:
        result = dict(status='hold', message='A bounded preparation subprocess did not complete successfully. Inspect the retained preparation evidence; no checks will replay automatically.',
            review_binding=claim['review_binding'], execution_authorized=False)
    except (ValueError, OSError, HTTPException) as error:
        result = {**_hold(error), 'review_binding': claim['review_binding']}
    if reason := getattr(stop_event, 'reason', None):
        if result['status'] != 'prepared':
            result['message'] = reason + ' Preparation stopped and its diagnostics were kept.'
    return result


if __name__ == '__main__':
    raise SystemExit(main())
