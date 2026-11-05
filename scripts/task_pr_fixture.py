"""Test harness only: controlled worker/remote, always an owned disposable store."""

import json
from pathlib import Path
import sys
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/tests"))
from test_task_to_pr import template_data, ControlledRuntime, ControlledRemote, ORACLE  # noqa: E402
from workbench import worker  # noqa: E402
from workbench.db import proposal_decisions, backlog_items  # noqa: E402
from workbench.real_workspace import durable, sha  # noqa: E402


def setup(path):
    folder = path.parent / "task-templates"
    folder.mkdir(exist_ok=True)
    data = template_data()
    raw = json.dumps(data, sort_keys=True).encode()
    target = folder / (sha(raw) + ".json")
    if not target.exists():
        target.write_bytes(raw)


def advance(engine, path, steps=1, hold=False):
    with engine.connect() as c:
        approved = (
            c.execute(
                select(proposal_decisions).where(
                    proposal_decisions.c.payload["run_budget"]["offer_id"].as_string().is_not(None)
                )
            )
            .mappings()
            .first()
        )
        task = (
            c.execute(
                select(backlog_items).where(
                    backlog_items.c.kind == "task",
                    backlog_items.c.intake_id == approved["intake_id"],
                )
            )
            .mappings()
            .first()
            if approved
            else None
        )
    if not task:
        for _ in range(steps):
            worker.tick(engine)
        return
    root = path.parent / "NON_MODEL_TASK_RUNTIME"
    root.mkdir(exist_ok=True)
    template = template_data()
    durable(root / "recipe.json", json.dumps(template["recipe"]).encode())
    durable(root / "recipe-oracle.lua", ORACLE)
    durable(root / "oracle.lua", ORACLE)
    durable(root / "base-manifest.json", b"{}")
    runtime = ControlledRuntime(root)
    runtime.task_ref = dict(
        intake_id=task["intake_id"], task_id=task["id"], approval_id=approved["id"]
    )
    state = path.parent / "NON_MODEL_REMOTE.json"
    remote = ControlledRemote()
    if state.exists():
        remote.__dict__.update(json.loads(state.read_text()))
    if hold:
        remote.base = "a" * 40
    for _ in range(steps):
        worker.tick(engine, runtime=runtime, publisher=remote)
    state.write_text(
        json.dumps({k: v for k, v in remote.__dict__.items() if k != "permitted"}, indent=2)
    )
