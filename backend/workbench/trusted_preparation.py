"""Supported same-user local preparation. Never exposed through ordinary HTTP."""
import os
from pathlib import Path
from fastapi import HTTPException

from workbench import task_budget
from workbench.real_workspace import Runtime


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


def prepare_regression(engine, ref, evidence_root, identity):
    """Reusable offline bridge; no task-specific Lua/patch/registration code.

    This is an internal preparer entry, not the final ordinary-user workflow. The
    future application-owned preparer must supply reviewed model-draft data and
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
        runtime = Runtime(root)
        task_session.provision(runtime, dict(scenario_preparation=prepared))
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


if __name__ == '__main__':
    raise SystemExit(main())
