"""Moving a verified installation never changes reviewed permission semantics."""

import copy
import json
from types import SimpleNamespace

import pytest
from workbench import everyday, trusted_preparation
from workbench.visual_scope import digest


@pytest.mark.parametrize(
    "fault",
    [None, "classes", "identity", "mode", "generation", "inode", "writable", "symlink", "output"],
)
def test_relocation_accepts_only_exact_private_prior_policy_and_coordinate_changes(
    tmp_path, monkeypatch, fault
):
    stage = tmp_path / "everyday-outcome-abcdef1"
    stage.mkdir(mode=0o700)
    (stage / "ops").mkdir(mode=0o700)
    (stage / "artifacts").mkdir(mode=0o700)
    (stage / "release/artifacts/runs/preparations").mkdir(parents=True, mode=0o700)
    db = stage / "artifacts/workbench.sqlite3"
    db.write_bytes(b"controlled metadata only")
    db.chmod(0o600)
    old = dict(
        version=1,
        database=str(db),
        database_identity=[db.stat().st_dev, db.stat().st_ino],
        output_root=str(stage / "release/artifacts/runs/preparations"),
        repository="Twich-Team/TwichUI",
        allowed_classes=["lua-regression-tests", "lua-offline-bugfix"],
        identity=dict(name="Fixture", email="fixture@invalid"),
        scenario_generation=True,
        bug_plan_review="source-grounded-intent-v1",
    )
    if fault == "inode":
        old["database_identity"][1] += 1
    if fault == "output":
        old["output_root"] = str(tmp_path / "unrelated")
    sha = digest(old)
    path = stage / "ops/preparation.json"
    path.write_text(json.dumps(old))
    path.chmod(0o600)
    current = copy.deepcopy(old)
    current.update(
        database="/new/verified/database",
        database_identity=[1, 2],
        output_root="/new/verified/output",
    )
    if fault == "classes":
        current["allowed_classes"] = ["lua-offline-bugfix"]
    if fault == "identity":
        current["identity"]["email"] = "other@invalid"
    if fault == "mode":
        current["bug_plan_review"] = "source-grounded-lua-v1"
    if fault == "generation":
        current["scenario_generation"] = False
    if fault == "writable":
        path.chmod(0o660)
    if fault == "symlink":
        target = path.with_suffix(".saved")
        path.rename(target)
        path.symlink_to(target)
    monkeypatch.setattr(
        everyday, "configuration", lambda engine: SimpleNamespace(evidence_roots=[str(stage)])
    )
    assert trusted_preparation.relocated_policy(object(), sha, current) == (fault is None)
