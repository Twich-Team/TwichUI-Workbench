"""Supported same-user local preparation. Never exposed through ordinary HTTP."""
import os
from pathlib import Path
from subprocess import SubprocessError
from fastapi import HTTPException

from workbench import task_budget
from workbench.real_workspace import Runtime


def configuration(engine):
    """Private installation input, bound to this store. Never request/model data."""
    import json
    from pydantic import Field, StrictBool, StrictInt
    from typing import Literal
    from workbench.schemas import StrictModel

    class Configuration(StrictModel):
        version: Literal[1]
        database: str
        database_identity: list[StrictInt] = Field(min_length=2, max_length=2)
        repository: Literal['Twich-Team/TwichUI']
        allowed_classes: list[Literal['lua-regression-tests']] = Field(max_length=1)
        output_root: str
        identity: dict[str, str]
        scenario_generation: StrictBool = False

    path = os.environ.get('WORKBENCH_PREPARATION_CONFIG')
    if not path:
        raise ValueError('Application preparation is not configured; an owned storage/identity policy is required.')
    if not Path(path).is_absolute():
        raise ValueError('Preparation configuration must use an absolute owned path')
    file = owned(path)
    if file.stat().st_mode & 0o077 or file.stat().st_size > 8192:
        raise ValueError('Preparation configuration exceeds its bound')
    config = Configuration.model_validate(json.loads(file.read_text())).model_dump()
    db = owned(engine.url.database)
    if config['database'] != str(db) or config['database_identity'] != [db.stat().st_dev, db.stat().st_ino]:
        raise ValueError('Preparation policy belongs to another application store')
    output = Path(config['output_root'])
    if not output.is_absolute() or output.resolve() != output or not owned(output).is_dir():
        raise ValueError('Configure an existing absolute owned preparation directory')
    identity = config['identity']
    if (set(identity) != {'name', 'email'} or any(not 1 <= len(v) <= 160 or any(ord(c) < 32 for c in v) for v in identity.values())):
        raise ValueError('Configure a bounded local publication identity')
    if 'lua-regression-tests' not in config['allowed_classes']:
        raise ValueError('Preparation policy denies this task class')
    return config


def validate_reviewed_policy(engine, reviewed):
    from workbench.visual_scope import digest
    grounding = reviewed.get('scenario_grounding')
    if grounding and digest(configuration(engine)) != grounding['policy_sha256']:
        raise ValueError('Preparation policy differs from the reviewed scenario policy; request a fresh proposal before preparing or approving a run.')


def owned(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.exists():
        raise ValueError('Existing owned paths without links required')
    info = path.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise ValueError('Trusted preparation requires same-user paths without shared write access')
    return path


def register(engine, ref, evidence_root):
    """Bind independently certified evidence; cannot run checks or issue authority.

    Local filesystem access is the existing trusted operator boundary. No request
    data can provide code, checks, a certificate verdict or execution permissions.
    """
    if engine.url.drivername != 'sqlite' or not engine.url.database:
        raise ValueError('An existing local SQLite store is required')
    for path in (engine.url.database, evidence_root):
        owned(path)
    return task_budget.register_reviewed_offer(engine, ref, Runtime(Path(evidence_root).absolute()))


def prepare_regression(engine, ref, evidence_root, identity, *, validate_policy=None):
    """Reusable offline bridge; no task-specific Lua/patch/registration code.

    The existing application worker supplies reviewed scenario data and private
    configured storage/identity. No model or operational activation is called here.
    """
    import json
    from workbench import task_execution, real_recipes, task_session, regression_preparation
    from workbench.regression_evidence import compile_plan, validate_expectation_review
    from workbench.real_workspace import durable

    owned(engine.url.database)
    root = owned(evidence_root)
    if not root.is_dir() or any(root.iterdir()):
        raise ValueError('A fresh empty owned preparation directory is required; retained evidence is never overwritten')
    with engine.connect() as c:
        linked = task_execution.snapshot(c, **ref)
        reviewed = linked['proposal_review'].get('task_contract') or {}
        validate_reviewed_policy(engine, reviewed)
        if reviewed.get('category') != 'lua-regression-tests':
            raise ValueError('Reusable preparation is limited to regression-test additions')
        prepared = reviewed.get('behavioral_evidence')
        if not prepared:
            raise ValueError('Specify concrete inputs and expected observations for the reviewed acceptance criteria')
        validate_expectation_review(linked['proposal_review'], linked['approval']['payload'])
        compiled = compile_plan(prepared)
        spec = real_recipes.from_review(c, ref, oracle=compiled['oracle'], behavior_id=compiled['behavior_id'],
            read_paths=compiled['read_paths'], focused_test=compiled['focused_test'], identity=identity)
    durable(root / 'recipe.json', json.dumps(spec, sort_keys=True).encode())
    durable(root / 'recipe-oracle.lua', compiled['oracle'].encode())
    try:
        if validate_policy:
            validate_policy()
        runtime = Runtime(root)
        task_session.provision(runtime, dict(scenario_preparation=prepared))
        if validate_policy:
            validate_policy()
        # Reconstruct canonical Runtime and independently re-evaluate, exactly as
        # existing registration does. No generated verdict is trusted.
        selected = register(engine, ref, root)
    except ValueError as error:
        outcome = dict(status='hold', message=str(error)[:1600], root=str(root), execution_authorized=False)
        durable(root / 'behavioral-preparation-outcome.json', json.dumps(outcome, sort_keys=True).encode())
        regression_preparation.record_behavioral_outcome(engine, ref, linked['proposal_review']['binding'], outcome)
        raise
    outcome = dict(status='prepared', message='Generated behavioral controls passed independent certification; no execution authority.',
                   root=str(root), execution_authorized=False)
    durable(root / 'behavioral-preparation-outcome.json', json.dumps(outcome, sort_keys=True).encode())
    regression_preparation.record_behavioral_outcome(engine, ref, linked['proposal_review']['binding'], outcome)
    return selected


def main():
    import json
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


def process_one(engine):
    """Dispatch from the existing job journal, with one durable external-work claim.

    Completed artifacts can reconcile after receipt loss. Uncertain interrupted
    work holds; neither a lease expiry nor an HTTP retry repeats preparation.
    """
    from sqlalchemy import select
    from workbench import intake, task_execution, real_execution
    from workbench.db import planning_jobs, write_transaction
    from workbench.visual_scope import digest

    recovering = False
    with write_transaction(engine) as c:
        row = c.execute(select(planning_jobs).where(planning_jobs.c.kind == 'behavioral_preparation',
            planning_jobs.c.status.in_(['queued', 'running'])).order_by(planning_jobs.c.created_at).limit(1)).mappings().first()
        if not row:
            return False
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
                binding = linked['proposal_review']['binding']
                root = Path(config['output_root']) / job['id']
                if root.exists():
                    raise ValueError('Preparation output already exists without a journal claim; retained evidence requires inspection.')
                claim = dict(root=str(root), owner=real_execution.owner_identity(), review_binding=binding,
                    policy_sha256=digest(config), identity=config['identity'])
                payload['preparation_claim'] = claim
                c.execute(planning_jobs.update().where(planning_jobs.c.id == job['id']).values(
                    status='running', payload=payload, attempts=1, message='Preparing reviewed behavioral controls; no execution authority.'))
                result = None
            except (ValueError, OSError, HTTPException) as error:
                result = dict(status='hold', message=str(error.detail if isinstance(error, HTTPException) else error)[:1600], execution_authorized=False)
        if result is not None:
            c.execute(planning_jobs.update().where(planning_jobs.c.id == job['id']).values(status='completed' if result['status']=='prepared' else 'failed',
                payload={**payload, 'preparation_result':result}, finished_at=intake.now(), message=result['message']))
            return True
    try:
        # Claim commits before any output/check process starts.
        def validate_policy():
            if digest(configuration(engine)) != claim['policy_sha256']:
                raise ValueError('Preparation policy changed before dispatch or certification registration')
        validate_policy()
        if recovering:
            root = owned(claim['root'])
            if not (root / 'preparation-completed.json').is_file():
                raise ValueError('Preparation was interrupted after its durable claim. Inspect retained evidence; automatic replay is denied.')
            selected = register(engine, ref, root)
        else:
            root.mkdir(mode=0o700)
            selected = prepare_regression(engine, ref, root, claim['identity'], validate_policy=validate_policy)
        result = dict(status='prepared', message='Reviewed scenarios independently checked; a certified run offer is ready for its separate budget review.',
            offer_id=selected['id'], review_binding=claim['review_binding'], execution_authorized=False)
    except SubprocessError:
        result = dict(status='hold', message='A bounded preparation subprocess did not complete successfully. Inspect the retained preparation evidence; no checks will replay automatically.',
            review_binding=claim['review_binding'], execution_authorized=False)
    except (ValueError, OSError, HTTPException) as error:
        result = dict(status='hold', message=str(error.detail if isinstance(error, HTTPException) else error)[:1600],
            review_binding=claim['review_binding'], execution_authorized=False)
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


if __name__ == '__main__':
    raise SystemExit(main())
