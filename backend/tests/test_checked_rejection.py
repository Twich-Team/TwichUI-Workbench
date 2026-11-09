"""Private independent review may disprove a checked result, never grant a replay.

Controlled stores/Lua only. Real confined rejection proof is a separate receipt.
"""

import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess

import pytest
from sqlalchemy import select

from acceptance_policy import checked_result_rejected
from test_real_execution import granted
from test_verification_profiles import setup, TEST, PRODUCTION, GOOD_TEST
from workbench import real_execution as real, verification_profiles as profiles
from workbench.db import write_transaction
from workbench.repair_cycle import oracle
from workbench.real_workspace import tree
from workbench.visual_scope import digest


def rejection(store, tmp_path):
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    runtime = setup(fixture, profiles.BUGFIX)
    _, grant, receipt = granted(store)
    # The unit store's fake runner must actually reach its terminal/revoked fence.
    from test_real_execution import ControlledRuntime

    assert real.process_one(store, ControlledRuntime())
    original = (runtime.workspace / TEST).read_text()
    (runtime.workspace / TEST).write_text(original + GOOD_TEST)
    program = oracle(TEST, {})
    tmp = tmp_path / "checker.lua"
    tmp.write_text(program)
    run = subprocess.run(
        ["lua5.1", str(tmp)], cwd=runtime.workspace / "tests", capture_output=True, timeout=5
    )
    assert run.returncode != 0
    check = dict(
        status="fail",
        rc=run.returncode,
        out=(run.stdout + run.stderr).decode(),
        usage=dict(cgroup_removed=True, controlled_fixture=True),
    )
    candidate_test = (runtime.workspace / TEST).read_bytes()
    manifest = dict(tree=tree(runtime.workspace))
    result = dict(
        verification="passed",
        candidate_id=hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest(),
        manifest=manifest,
    )
    plan = dict(focused_test=TEST, read_paths=[PRODUCTION, TEST], criteria=[dict(id="behavior")])
    snapshot = {
        **grant["snapshot"],
        "contract": {
            **grant["snapshot"]["contract"],
            "source": str(runtime.source),
            "base": runtime.base,
        },
        "workflow_task": dict(
            intake_id="earlier",
            proposal_review=dict(task_contract=dict(bug_intent=dict(plan=plan))),
        ),
    }
    with write_transaction(store) as c:
        c.execute(
            real.grants.update()
            .where(real.grants.c.id == grant["id"])
            .values(snapshot=snapshot, revoked_at=real.now())
        )
        c.execute(
            real.requests.update().where(real.requests.c.id == receipt["id"]).values(result=result)
        )
    folder = Path(store.url.database).parent / "acceptance/rejected-results"
    folder.mkdir(mode=0o700, parents=True)
    for suffix, data in [
        (".test.lua", candidate_test),
        (".oracle.lua", program.encode()),
        (".check.json", json.dumps(check).encode()),
    ]:
        path = folder / (receipt["id"] + suffix)
        path.write_bytes(data)
        path.chmod(0o600)
    review = dict(
        version=1,
        approved=False,
        execution=receipt["id"],
        candidate=result["candidate_id"],
        criterion="behavior",
        reason="Independent post-edit evidence disproves the previously checked candidate.",
        candidate_tree_sha256=digest(manifest["tree"]),
        oracle_sha256=hashlib.sha256(program.encode()).hexdigest(),
        check_sha256=hashlib.sha256(json.dumps(check).encode()).hexdigest(),
    )
    path = folder / (receipt["id"] + ".json")
    path.write_text(json.dumps(review))
    path.chmod(0o600)
    return receipt["id"], folder, review, grant


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "missing",
        "approved",
        "candidate",
        "criterion",
        "tree",
        "oracle",
        "check",
        "test",
        "permissions",
        "active-grant",
        "checked-status",
        "syntax",
    ],
)
def test_only_bound_private_meaningful_failure_can_disprove_checked_result(store, tmp_path, fault):
    identity, folder, review, grant = rejection(store, tmp_path)
    path = folder / (identity + ".json")
    if fault == "missing":
        path.unlink()
    elif fault == "approved":
        review["approved"] = True
    elif fault == "candidate":
        review["candidate"] = "b" * 64
    elif fault == "criterion":
        review["criterion"] = "not-reviewed"
    elif fault == "tree":
        review["candidate_tree_sha256"] = "b" * 64
    elif fault == "oracle":
        (folder / (identity + ".oracle.lua")).write_text("assert(false)")
    elif fault == "check":
        (folder / (identity + ".check.json")).write_text("{}")
    elif fault == "test":
        (folder / (identity + ".test.lua")).write_text("assert(false)")
    elif fault == "permissions":
        path.chmod(0o640)
    elif fault == "active-grant":
        with write_transaction(store) as c:
            c.execute(
                real.grants.update().where(real.grants.c.id == grant["id"]).values(revoked_at=None)
            )
    elif fault == "checked-status":
        with write_transaction(store) as c:
            c.execute(
                real.requests.update()
                .where(real.requests.c.id == identity)
                .values(status="running")
            )
    elif fault == "syntax":
        check = dict(
            status="fail",
            rc=1,
            out="syntax error\nWB_BEHAVIOR bug-reproducer FAIL\n",
            usage=dict(cgroup_removed=True),
        )
        data = json.dumps(check).encode()
        (folder / (identity + ".check.json")).write_bytes(data)
        review["check_sha256"] = hashlib.sha256(data).hexdigest()
    if path.exists():
        path.write_text(json.dumps(review))
    with sqlite3.connect(store.url.database) as c:
        assert checked_result_rejected(c, identity) is (fault is None)
        # A read-only rejection records no new attempt or execution permission.
        assert c.execute("select count(*) from real_execution_grants").fetchone()[0] == 1
    with store.connect() as c:
        assert c.execute(
            select(real.requests.c.status).where(real.requests.c.id == identity)
        ).scalar_one() == ("running" if fault == "checked-status" else "checked")
