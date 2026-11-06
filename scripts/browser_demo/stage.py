"""Canonical layout and immutable inputs for a one-off browser stage."""

from contextlib import closing
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3

from sqlalchemy import create_engine
from workbench import task_sessions, trusted_preparation

MANIFEST = "browser-demonstration-manifest.json"
FINGERPRINT_ENV = "WORKBENCH_DEMO_MANIFEST_SHA256"
ROOT = Path(__file__).resolve().parents[2]
STORE = Path("artifacts/runs/browser-demo-store")
DATABASE = STORE / "review.sqlite3"
BACKUP = STORE / "review.before.sqlite3"
POLICY = STORE / "preparation-policy.json"
PREPARATIONS = STORE / "preparations"
PENDING = Path(".local/task-runtime.pending.json")
ACTIVE = Path(".local/task-runtime.json")
TEMPLATES = Path(".local/supported-task-templates")
PROOF = Path(".local/proof/total-limit-proof.json")
STATIC = Path("frontend/dist")
WORKER = Path("scripts/browser_demo/delegated-worker.sh")
PREFLIGHT = Path("scripts/browser_demo/guarded_runtime_preflight.py")
VERIFIER = Path("scripts/browser_demo/verify_terminal.py")
EMPTY_TABLES = (
    "intakes", "planning_jobs", "proposals", "proposal_decisions", "task_run_offers",
    "task_sessions", "model_attempts", "real_execution_grants", "real_execution_requests",
    "real_publications", "fake_publications", "pr_preparations", "execution_policies",
)


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def files(stage, folder):
    return {p.relative_to(stage).as_posix() for p in (stage / folder).rglob("*")
            if (p.is_file() or p.is_symlink()) and "__pycache__" not in p.parts}


def sealed_inputs(stage, fingerprint, *, fresh=False):
    path = stage / MANIFEST
    require(stage.resolve() == stage and path.resolve() == path and path.is_file()
            and path.stat().st_uid == os.getuid() and path.stat().st_mode & 0o077 == 0
            and path.stat().st_size <= 256 << 10, "Private staged manifest required")
    raw = path.read_bytes()
    require(sha256(raw).hexdigest() == fingerprint, "Approved stage manifest fingerprint changed")
    manifest = json.loads(raw)
    require(manifest["version"] == 2 and manifest["stage"] == str(stage),
            "Manifest belongs to a different stage")
    entries = manifest["sha256"]
    required = {str(p) for p in (DATABASE, BACKUP, POLICY, PENDING, PROOF, WORKER, PREFLIGHT, VERIFIER)}
    required |= {f"{STATIC}/index.html", "alembic.ini", "package-lock.json"}
    for folder in ("backend", "migrations", "scripts", STATIC, TEMPLATES):
        required |= files(stage, folder)
    require(required <= entries.keys(), "Stage manifest is missing a required input")
    for name, checksum in entries.items():
        rel = Path(name)
        item = stage / rel
        require(not rel.is_absolute() and ".." not in rel.parts and item.resolve() == item
                and item.is_file() and item.stat().st_uid == os.getuid()
                and (not fresh and rel == DATABASE or digest(item) == checksum),
                f"Frozen stage input changed: {name}")
    for name, target in manifest["toolchain"].items():
        require(name in (".venv", "node_modules") and (stage / name).is_symlink()
                and str((stage / name).resolve()) == target, "Staged toolchain link changed")
    require(set(manifest["toolchain"]) == {".venv", "node_modules"}, "Staged toolchain inputs missing")
    return manifest


def inputs(stage, manifest):
    return {
        "WORKBENCH_DB": str(stage / DATABASE),
        "WORKBENCH_PREPARATION_CONFIG": str(stage / POLICY),
        "WORKBENCH_STATIC_ROOT": str(stage / STATIC),
        "WORKBENCH_RESEARCH_ROOT": manifest["fixture"],
    }


def window_limits(manifest):
    minutes = manifest.get("window_minutes", 90)
    reserve = manifest.get("budget_reserve_minutes", 55)
    review = manifest.get("owner_review_minutes", 0)
    require(all(type(value) is int for value in (minutes, reserve, review))
            and 90 <= minutes <= 120 and 55 <= reserve < minutes
            and 0 <= review < minutes - reserve,
            "The manifest does not specify a supported bounded window.")
    return minutes, reserve, review


def preflight(stage, fingerprint):
    """Inspect the dormant package without publishing a capability or opening a writer."""
    manifest = sealed_inputs(stage, fingerprint, fresh=True)
    require(not (stage / ACTIVE).exists() and not (stage / ACTIVE).is_symlink(),
            "The capability is already active")
    require(type(manifest.get("owner_submits", False)) is bool, "Invalid browser submission mode")
    window_limits(manifest)
    require(manifest["task"]["base"] == manifest["fixture_base"], "Approved task base differs")
    db = stage / DATABASE
    require(digest(db) == digest(stage / BACKUP), "Initial store and backup differ")
    require(not any(Path(str(db) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")),
            "Staged store has unclosed writes")
    uri = db.as_uri() + "?mode=ro&immutable=1"
    with closing(sqlite3.connect(uri, uri=True, timeout=1)) as connection:
        require(connection.execute("PRAGMA quick_check").fetchone() == ("ok",), "Staged store is unhealthy")
        require(all(not connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
                    for table in EMPTY_TABLES), "Staged store already contains work or authority")
    config = validate_policies(stage, stage / PENDING)
    return manifest, config



def validate_policies(stage, capability_path):
    db = stage / DATABASE
    uri = db.as_uri() + "?mode=ro"
    engine = create_engine(f"sqlite:///{db}", creator=lambda: sqlite3.connect(uri, uri=True))
    try:
        config = task_sessions.read_capability(engine, capability_path)
        policy = trusted_preparation.read_configuration(engine, stage / POLICY)
        require(config["total_limit_proof"] == str(stage / PROOF)
                and "templates" in config and policy["output_root"] == str(stage / PREPARATIONS)
                and policy["scenario_generation"], "Runtime policy or proof belongs to a different stage")
        return config
    finally:
        engine.dispose()


def require_active_stage():
    """Recheck immutable inputs immediately before a staged browser write or controller."""
    fingerprint = os.environ.get(FINGERPRINT_ENV)
    if fingerprint:
        manifest = sealed_inputs(ROOT, fingerprint)
        require("WORKBENCH_TASK_TEMPLATES_ROOT" not in os.environ
                and all(os.environ.get(key) == value for key, value in inputs(ROOT, manifest).items()),
                "Launcher environment does not match the tested stage")
        validate_policies(ROOT, ROOT / ACTIVE)
        require(digest(ROOT / ACTIVE) == digest(ROOT / PENDING),
                "Active runtime configuration differs from the tested stage")
