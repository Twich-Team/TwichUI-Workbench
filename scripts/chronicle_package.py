"""Trusted one-package CLI. Existing worker owns actual execution; no replay loop."""
import argparse
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'backend'))
from workbench.real_workspace import Runtime, durable  # noqa: E402
from workbench import real_execution  # noqa: E402
from workbench.db import engine_for  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'preflight', 'activate'])
    parser.add_argument('--run-root', type=Path, required=True)
    parser.add_argument('--owner-instruction', type=Path)
    args = parser.parse_args()
    root = args.run_root.resolve()
    if root == ROOT or (ROOT/'.local/m5-review').resolve() in [root, *root.parents]:
        parser.error('9487 and source directories are not execution output roots')
    runtime = Runtime(root)
    if args.action == 'prepare':
        runtime.prepare()
        print('Independent clone and oracle frozen. No grant or inference.')
        return 0
    gates = runtime.preflight()
    durable(root/('activation-'+str(uuid4())+'.json'), json.dumps(gates, indent=2).encode())
    if args.action == 'preflight' or any(g['status'] != 'passed' for g in gates):
        print(json.dumps(dict(gates=gates, grant_issued=False, inference_ran=False), indent=2))
        return 0 if all(g['status'] == 'passed' for g in gates) else 2
    if not args.owner_instruction:
        parser.error('Exact owner instruction/provenance required; no sample approval may be reused')
    # Dedicated acceptance store only. Never engine_for()'s default/live database.
    from alembic import command
    from alembic.config import Config
    engine = engine_for(root/'acceptance.sqlite3')
    with engine.connect() as c:
        config = Config(str(ROOT/'alembic.ini'))
        config.attributes['connection'] = c
        command.upgrade(config, 'head')
        c.commit()
    grant = real_execution.issue(engine, args.owner_instruction.read_text(),
                                 str(args.owner_instruction.resolve()), runtime)
    receipt = real_execution.submit(engine, grant['id'], str(uuid4()))
    # Same single owned handler used by workbench.worker; no second dispatch loop.
    real_execution.process_one(engine, runtime)
    print(json.dumps(real_execution.review(engine, receipt['id']), indent=2))
    engine.dispose()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
