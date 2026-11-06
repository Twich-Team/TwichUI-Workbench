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
from browser_demo import gate
from browser_demo.gate import INTAKE, SingleDemo
from browser_demo.supervisor import RunHeld, Supervisor
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

    with monkeypatch.context() as patch:
        before_cutoff = datetime.now(timezone.utc)
        cutoff = datetime.fromisoformat(phase["cutoff"])
        ticks = iter([before_cutoff, cutoff])

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return next(ticks)

        patch.setattr(gate, "datetime", Clock)
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


def test_local_run_handoff_is_read_only_and_tracks_ready_saved_and_closed(client, tmp_path):
    browser = TestClient(SingleDemo(client.app, tmp_path), base_url="http://127.0.0.1:9187")
    intake = str(uuid4())
    future = datetime.now(timezone.utc) + timedelta(minutes=15)
    phase = tmp_path / "ingress-phase.json"

    def set_phase(state, cutoff=future):
        phase.write_text(json.dumps(dict(state=state, cutoff=cutoff.isoformat(), intake=intake)))

    set_phase("read-only")
    waiting = browser.get("/demo/local-run")
    assert waiting.headers["cache-control"] == "no-store"
    assert 'http-equiv="refresh"' in waiting.text
    assert "Waiting for the local-run offer" in waiting.text
    assert "Review the local run in Workbench" not in waiting.text
    assert "Mountain time" in waiting.text and future.isoformat() in waiting.text

    set_phase("budget")
    ready = browser.get("/demo/local-run")
    assert f'href="/intake/{intake}#local-run-review"' in ready.text
    assert "Your local-run decision is ready" in ready.text
    assert browser.post("/demo/local-run", json={}).status_code == 409
    receipt = tmp_path / "ingress-budget.json"
    assert not receipt.exists()

    set_phase("budget", future - timedelta(minutes=30))
    closed = browser.get("/demo/local-run")
    assert "Local-run review closed" in closed.text
    assert "Review the local run in Workbench" not in closed.text
    (tmp_path / "ingress-submit.json").write_text(json.dumps({"body": {"request_id": intake}}))
    receipt.write_text("{}")
    saved = browser.get("/demo/local-run")
    assert "Local-run decision saved" in saved.text
    assert "whether it has been applied" in saved.text
    assert f'href="/intake/{intake}"' in saved.text


@pytest.mark.parametrize("late", [False, True])
def test_supervisor_opens_review_only_with_the_full_review_interval(tmp_path, monkeypatch, late):
    start = datetime.now(timezone.utc)
    elapsed = timedelta()
    supervisor = Supervisor(None, tmp_path, "http://127.0.0.1:9187", None, lambda _: {},
                            lambda: True, lambda: False, start + timedelta(minutes=120),
                            runtime_root=tmp_path, now=lambda: start + elapsed,
                            owner_submits=True, budget_reserve_minutes=72, owner_review_minutes=15)
    assert supervisor.cutoff == start + timedelta(minutes=48)
    monkeypatch.setattr(supervisor, "check", lambda *_, **__: None)
    monkeypatch.setattr(supervisor, "wait_owner_intake", lambda: "intake")
    monkeypatch.setattr(supervisor, "wait_plan", lambda _: dict(id="plan", binding="plan-binding",
        human_scope={"revision": 1}, task_contract={"behavioral_evidence": {"review_digest": "review"}}))
    monkeypatch.setattr(supervisor, "wait_scope", lambda: "scope-approval")

    def wait_offer(*_):
        nonlocal elapsed
        elapsed = timedelta(minutes=33, seconds=0 if late else -1)
        return "task", {"binding": "offer-binding", "offer": {"budget": {}}}

    def wait_budget():
        assert supervisor.read("ingress-phase.json")["state"] == "budget"
        raise RunHeld("Test reached owner decision")

    monkeypatch.setattr(supervisor, "wait_offer", wait_offer)
    monkeypatch.setattr(supervisor, "wait_budget", wait_budget)
    supervisor.write("browser-policy.json", {"revision": 1})
    with pytest.raises(RunHeld, match="too little time" if late else "reached owner decision"):
        supervisor.run(lambda _: None, lambda: None, lambda: None)
    assert (tmp_path / "owner-step-budget.json").exists() is not late
    assert supervisor.read("ingress-phase.json")["state"] == "read-only"
