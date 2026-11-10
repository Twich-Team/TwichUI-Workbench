"""Trusted local ingress for an express same-task local-Qwen recovery instruction.

This never impersonates the browser or invokes a model. The normal worker owns
the resulting fresh bounded session, admission, execution and independent checks.
"""

import argparse
import json
import os
from pathlib import Path

from workbench import owner_task
from workbench.db import engine_for


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--intake-id", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--scope-approval-id", required=True)
    parser.add_argument("--owner-instruction", type=Path, required=True)
    parser.add_argument("--owner-sha256", required=True)
    parser.add_argument("--continuing", action="store_true", help="Record express continuing permission for this same local repair; never infer it from a one-attempt approval")
    args = parser.parse_args()
    if (
        args.database.is_symlink()
        or not args.database.is_file()
        or args.database.stat().st_uid != os.getuid()
    ):
        parser.error("Existing owned task store required; no store is initialized")
    engine = engine_for(args.database)
    with engine.connect() as c:
        if c.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() != "0012":
            parser.error("Current existing schema0012 required; this command never migrates")
    ref = dict(intake_id=args.intake_id, task_id=args.task_id, approval_id=args.scope_approval_id)
    print(
        json.dumps(owner_task.recover_local(engine, ref, args.owner_instruction, args.owner_sha256,
                                           continuing=args.continuing))
    )


if __name__ == "__main__":
    main()
