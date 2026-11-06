"""Run the already approved Codex task in its own bounded verification service."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import sys
from threading import Event
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from workbench import provider_routes, real_execution, task_budget, task_execution  # noqa: E402
from workbench.codex_runtime import CodexRuntime  # noqa: E402
from workbench.db import engine_for, proposal_decisions  # noqa: E402
from workbench.real_package import package_for  # noqa: E402
from workbench.real_workspace import durable  # noqa: E402


def main():
    stopped = Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stopped.set())
    root = Path(sys.argv[1]).resolve()
    if root.parent != ROOT / 'artifacts/runs' or not root.name.startswith('task-lease-'):
        raise ValueError('Owned task run root required.')
    cg = Path('/sys/fs/cgroup' + Path('/proc/self/cgroup').read_text().strip().split('::', 1)[1])
    if cg.name != 'workbench-real-codex-' + root.name + '.service':
        raise ValueError('Dedicated owned Codex verification scope required.')
    (cg / 'main').mkdir()
    (cg / 'main/cgroup.procs').write_text(str(os.getpid()))
    (cg / 'cgroup.subtree_control').write_text('+memory +pids +cpu')
    os.environ['WORKBENCH_CG_ROOT'] = str(cg)
    link = json.loads((root / 'workflow-link.json').read_text())
    claim = json.loads((root / 'session-claimed.json').read_text())
    ref = link['task']
    db = Path(link['database'])
    info = db.stat()
    if (ref != claim['task'] or link['task_budget_fingerprint'] != claim['task_budget_fingerprint']
            or [info.st_dev, info.st_ino] != link['database_identity']
            or not 0 < link['run_deadline'] - time.time() <= 2400):
        raise ValueError('Original workflow binding or deadline changed.')
    engine = engine_for(db)
    with engine.connect() as c:
        linked = task_execution.snapshot(c, **ref)
        route = provider_routes.run_selection(c, linked)
        approval = task_execution.require(c, proposal_decisions, ref['approval_id'])
        policy = task_budget.permission(c, approval, admitting=True)
        if not route or real_execution.digest(policy) != link['task_budget_fingerprint']:
            raise ValueError('Approved Codex route or budget changed.')
    runtime = CodexRuntime(root / 'real-run', route, stopped)
    runtime.task_ref = ref
    package = package_for(root)
    if package is None:
        raise ValueError('An existing task-wide reservation is required.')
    runtime.package = dict(package=str(package.root), baseline_sha256=package.hash)
    runtime.grant_deadline = datetime.fromtimestamp(link['run_deadline'], timezone.utc)
    runtime.deadline = min(runtime.deadline, time.monotonic() + link['run_deadline'] - time.time())
    real_execution.process_one(engine, runtime, stop_event=stopped)
    durable(root / 'lease-controller-result.json', json.dumps({
        'restoration': {'restored': True, 'no_lease': True},
    }).encode())


if __name__ == '__main__':
    main()
