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
