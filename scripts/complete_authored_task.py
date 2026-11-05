"""Apply explicit current-session authored files through the existing worker. No inference."""

from pathlib import Path
import sys
import os
import json
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from workbench import task_session, task_budget, real_execution as job, real_lease  # noqa:E402
from workbench.session_authorship import SessionAuthoredRuntime  # noqa:E402
from workbench.real_package import Package  # noqa:E402
from workbench.real_workspace import durable, sha  # noqa:E402
from workbench.db import engine_for  # noqa:E402
from sqlalchemy import select  # noqa:E402


def server_unchanged(config):
    if (
        sha(real_lease.ENV.read_bytes()) != config["original_server_env_sha256"]
        or real_lease.ENV.stat().st_mode & 0o777 != config["original_server_mode"]
    ):
        raise ValueError(
            "Original model-server configuration changed; this session never modifies it"
        )
    code, _ = real_lease.request(real_lease.KEY.read_text().strip(), "GET", "/props")
    if code != 200:
        raise ValueError("Original server access/readiness could not be verified")
    return dict(
        restored=True,
        unchanged=True,
        no_lease=True,
        original_config_verified=True,
        original_access_verified=True,
        server_epoch=real_lease.epoch(),
        model_generation_requests=0,
    )


def main():
    config_path = Path(sys.argv[1])
    config = task_session.read_config(config_path)
    if config.get("authoring_mode") != "explicit-current-session":
        raise ValueError("Explicit current-session config required")
    root = Path(config["run_root"])
    cg = Path("/sys/fs/cgroup" + Path("/proc/self/cgroup").read_text().strip().split("::", 1)[1])
    if not cg.name.startswith("workbench-real-authored-"):
        raise ValueError("Owned confined verification unit required")
    (cg / "main").mkdir()
    (cg / "main/cgroup.procs").write_text(str(os.getpid()))
    (cg / "cgroup.subtree_control").write_text("+memory +pids +cpu")
    os.environ["WORKBENCH_CG_ROOT"] = str(cg)
    package = Package(root.parent, config["package_baseline_sha256"])
    package.require_time(2700)
    report = dict(
        authoring="explicit-current-session", new_model_calls=0, local_attempt_consumed=False
    )
    engine = None
    try:
        report["restoration"] = server_unchanged(config)
        runtime = SessionAuthoredRuntime(root / "real-run")
        runtime.package = dict(
            package=str(package.root),
            baseline_sha256=package.hash,
            authoring="explicit-current-session",
            local_generation_capacity=0,
        )
        linked = json.loads((root / "workflow-link.json").read_text())
        runtime.grant_deadline = __import__("datetime").datetime.fromtimestamp(
            linked["run_deadline"], __import__("datetime").timezone.utc
        )
        runtime.deadline = min(
            runtime.deadline, time.monotonic() + linked["run_deadline"] - time.time()
        )
        runtime.task_ref = config["task"]
        engine = engine_for(Path(linked["database"]))
        if (
            runtime.author_input()["owner_authorization_sha256"]
            != config["owner_instruction_sha256"]
        ):
            raise ValueError("Session author and exact owner source differ")
        if not task_budget.authorize_one(engine, runtime):
            raise ValueError("Exact task budget did not authorize session-authored application")
        job.process_one(engine, runtime)
        with engine.connect() as c:
            row = (
                c.execute(
                    select(job.requests)
                    .join(job.grants, job.requests.c.grant_id == job.grants.c.id)
                    .where(
                        job.grants.c.snapshot["task_budget"]["decision_id"].as_string()
                        == config["task"]["approval_id"]
                    )
                )
                .mappings()
                .one()
            )
        report["execution_id"] = row["id"]
        report["status"] = row["status"]
        durable(
            root / "implementation-result.json",
            json.dumps(job.review(engine, row["id"]), sort_keys=True).encode(),
        )
    except Exception as error:
        report["error"] = type(error).__name__ + ": " + str(error)[:600]
    finally:
        if engine is not None:
            engine.dispose()
        try:
            report["restoration"] = server_unchanged(config)
        except Exception as error:
            report["restoration"] = dict(restored=False, error=type(error).__name__)
        durable(
            root / "authored-controller-result.json", json.dumps(report, sort_keys=True).encode()
        )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
