"""Stage input rejection uses the runtime's real capability and policy readers."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import runpy
import shutil
import sqlite3
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from browser_demo import stage
from browser_demo.gate import INTAKE, SingleDemo
from workbench import task_sessions
from workbench.schemas import IntakeInput


@pytest.fixture
def package(store, tmp_path, monkeypatch):
    root = tmp_path / "stage"
    monkeypatch.setattr(stage, "ROOT", root)
    monkeypatch.setattr(task_sessions, "ROOT", root)
    (root / stage.PREPARATIONS).mkdir(parents=True, mode=0o700)
    (root / stage.TEMPLATES).mkdir(parents=True, mode=0o700)
    db = root / stage.DATABASE
    with sqlite3.connect(store.url.database) as source, sqlite3.connect(db) as target:
        source.backup(target)
    shutil.copyfile(db, root / stage.BACKUP)
    binding = dict(database=str(db), database_identity=[db.stat().st_dev, db.stat().st_ino],
                   repository="Twich-Team/TwichUI")
    required = [stage.PROOF, stage.WORKER, stage.PREFLIGHT, stage.VERIFIER,
                stage.STATIC / "index.html", Path("alembic.ini"), Path("package-lock.json")]
    for name in required:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
    config = dict(**binding, mode="explicit-one-off-only", templates_root=str(root / stage.TEMPLATES),
                  templates=[], base_branch="main", model="pilot-qwen38-27b", automatic_fallback=False,
                  total_limit_proof=str(root / stage.PROOF), total_limit_proof_sha256=stage.digest(root / stage.PROOF))
    policy = dict(**binding, version=1, output_root=str(root / stage.PREPARATIONS),
                  allowed_classes=["lua-regression-tests"], scenario_generation=True,
                  identity={"name": "Fixture", "email": "fixture@local.invalid"})
    for path, data in ((stage.PENDING, config), (stage.POLICY, policy)):
        (root / path).write_text(json.dumps(data))
        (root / path).chmod(0o600)
    toolchain = {}
    for name in (".venv", "node_modules"):
        folder = tmp_path / name
        folder.mkdir()
        (root / name).symlink_to(folder)
        toolchain[name] = str(folder)
    manifest = dict(version=2, stage=str(root), task={"base": "fixture"}, fixture_base="fixture",
                    fixture=str(tmp_path), toolchain=toolchain,
                    sha256={str(p): stage.digest(root / p) for p in
                            (*required, stage.DATABASE, stage.BACKUP, stage.POLICY, stage.PENDING)})
    return root, manifest


def freeze(root, manifest):
    path = root / stage.MANIFEST
    path.write_text(json.dumps(manifest))
    path.chmod(0o600)
    return stage.digest(path)


@pytest.mark.parametrize("fault, reason", [
    ("old-stage", "different stage"),
    ("old-store", "does not match the application store"),
    ("old-templates", "templates must belong to the current stage"),
    ("old-proof", "policy or proof belongs to a different stage"),
    ("old-output", "policy or proof belongs to a different stage"),
    ("absent-directory", "template inventory changed"),
    ("missing-template", "template inventory changed"),
    ("unlisted-template", "manifest is missing"),
    ("altered-template", "Supported task template changed"),
    ("escaped-template", "Frozen stage input changed"),
    ("missing-manifest-entry", "missing a required input"),
    ("escaped-manifest-entry", "Frozen stage input changed"),
])
def test_preflight_rejects_self_consistent_but_wrong_package(package, tmp_path, fault, reason):
    root, manifest = package
    path = root / stage.PENDING
    config = json.loads(path.read_text())
    proof = root / stage.PROOF
    old = tmp_path / "old"
    old.mkdir(mode=0o700)
    template = root / stage.TEMPLATES / (("a" * 64) + ".json")
    if fault == "old-stage":
        manifest["stage"] = str(old)
    if fault == "old-store":
        config["database"] = str(old / stage.DATABASE.name)
    if fault == "old-templates":
        config["templates_root"] = str(old / stage.TEMPLATES.name)
    if fault == "old-proof":
        copy = old / proof.name
        shutil.copyfile(proof, copy)
        config["total_limit_proof"] = str(copy)
    if fault == "old-output":
        policy = root / stage.POLICY
        policy.write_text(json.dumps(json.loads(policy.read_text()) | {"output_root": str(old)}))
        manifest["sha256"][str(stage.POLICY)] = stage.digest(policy)
    if fault == "absent-directory":
        template.parent.rmdir()
    if fault in {"missing-template", "altered-template", "escaped-template"}:
        config["templates"] = [template.name]
    if fault in {"unlisted-template", "altered-template"}:
        template.write_text("{}")
    if fault == "escaped-template":
        escaped = old / "template.json"
        escaped.write_text("{}")
        template.symlink_to(escaped)
    if fault in {"altered-template", "escaped-template"}:
        manifest["sha256"][str(template.relative_to(root))] = stage.digest(template)
    if fault == "missing-manifest-entry":
        manifest["sha256"].pop(str(stage.WORKER))
    if fault == "escaped-manifest-entry":
        manifest["sha256"]["../" + proof.name] = stage.digest(proof)
    path.write_text(json.dumps(config))
    manifest["sha256"][str(stage.PENDING)] = stage.digest(path)
    fingerprint = freeze(root, manifest)
    with pytest.raises(ValueError, match=reason):
        stage.preflight(root, fingerprint)
    assert not (root / stage.ACTIVE).exists()


def test_preflight_never_activates_and_later_gate_rechecks_package(package, monkeypatch):
    root, manifest = package
    fingerprint = freeze(root, manifest)
    assert stage.preflight(root, fingerprint)[0] == manifest
    assert not (root / stage.ACTIVE).exists()
    path = root / stage.MANIFEST
    path.write_text(path.read_text() + "\n")
    with pytest.raises(ValueError, match="fingerprint changed"):
        stage.preflight(root, fingerprint)
    freeze(root, manifest)
    active = root / stage.ACTIVE
    shutil.copyfile(root / stage.PENDING, active)
    active.chmod(0o600)
    for key, value in stage.inputs(root, manifest).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv(stage.FINGERPRINT_ENV, fingerprint)
    stage.require_active_stage()
    with monkeypatch.context() as patch:
        patch.setenv("WORKBENCH_STATIC_ROOT", str(root.parent / "prior-stage"))
        with pytest.raises(ValueError, match="environment does not match"):
            stage.require_active_stage()
    (root / stage.TEMPLATES).rmdir()
    with pytest.raises(ValueError, match="template inventory changed"):
        stage.require_active_stage()
    (root / stage.TEMPLATES).mkdir(mode=0o700)
    active.write_text(active.read_text() + "\n")
    with pytest.raises(ValueError, match="Active runtime configuration differs"):
        stage.require_active_stage()
    active.unlink()
    (root / stage.PREFLIGHT).write_text("changed")
    with pytest.raises(ValueError, match="Frozen stage input changed"):
        stage.require_active_stage()
    controller_preflight = runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "scripts/chronicle_lease.py")
    )["controller_preflight"]
    with pytest.raises(ValueError, match="Frozen stage input changed"):
        controller_preflight(root, None)
    assert not (root / "controller-started.json").exists()


def test_owner_draft_waits_for_authenticated_exact_submission(client, tmp_path, monkeypatch):
    origin = "http://127.0.0.1:9187"
    browser = TestClient(SingleDemo(client.app, tmp_path), base_url=origin)
    identity = str(uuid4())
    payload = IntakeInput.model_validate(
        dict(INTAKE, request_id=identity, title="Fixture </script> text")
    ).model_dump(mode="json")
    phase = {"state": "submit", "intake_request_id": identity,
             "intake_payload": {key: value for key, value in payload.items() if key != "request_id"},
             "cutoff": (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()}
    phase_file = tmp_path / "ingress-phase.json"
    phase_file.write_text(json.dumps(phase))
    receipt = tmp_path / "ingress-submit.json"
    headers = {"origin": origin, "x-csrf-token": browser.get("/api/session").json()["csrf_token"]}

    draft = browser.get("/demo/request")
    assert draft.status_code == 200
    assert draft.headers["cache-control"] == "no-store"
    assert r"Fixture \u003c/script> text" in draft.text
    assert "localStorage.setItem" in draft.text
    assert browser.get(f"/api/intake/{identity}").status_code == 404
    assert not receipt.exists()
    assert browser.post("/api/intake", json=payload).status_code == 403
    for change in ({"request_id": str(uuid4())}, {"title": "Changed task"}):
        assert browser.post("/api/intake", headers=headers, json=payload | change).status_code == 409

    with monkeypatch.context() as patch:
        def invalid_stage():
            raise ValueError("Frozen stage input changed")
        patch.setattr(stage, "require_active_stage", invalid_stage)
        assert browser.get("/demo/request").status_code == 409
        assert browser.post("/api/intake", headers=headers, json=payload).status_code == 409
    assert not receipt.exists()

    saved = browser.post("/api/intake", headers=headers, json=payload)
    assert saved.status_code == 200
    assert saved.json()["id"] == identity
    assert saved.json()["status"] == "queued"
    assert json.loads(receipt.read_text())["body"] == payload
    assert browser.get("/demo/request").status_code == 409

    phase["cutoff"] = datetime.now(timezone.utc).isoformat()
    phase_file.write_text(json.dumps(phase))
    assert browser.post("/api/intake", headers=headers, json=payload).status_code == 409
