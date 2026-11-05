"""Run local fixture API, worker and Vite. Never touches the pilot."""

import argparse
import os
from pathlib import Path

from project_env import require_ports
from fixture_projection import create as create_projection
import signal
import subprocess
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--e2e", action="store_true", help="Disposable DB; worker controlled by tests"
    )
    parser.add_argument(
        "--disposable", action="store_true", help="Use a temporary fixture development database"
    )
    parser.add_argument(
        "--built",
        action="store_true",
        help="Serve existing build through loopback API, without Vite",
    )
    parser.add_argument(
        "--review",
        action="store_true",
        help="Use already allowed isolated review port 9187, preserving a prior preview on 8787",
    )
    args = parser.parse_args()
    if args.review and (not args.built or not args.disposable or args.e2e):
        parser.error("--review requires --built --disposable and excludes --e2e")
    os.chdir(ROOT)
    require_ports(
        [5380, 9287]
        if args.e2e
        else [9187 if args.review else 8787]
        if args.built
        else [5180, 8787]
    )
    temporary = (
        tempfile.TemporaryDirectory(prefix="workbench-e2e-" if args.e2e else "workbench-dev-")
        if (args.e2e or args.disposable)
        else None
    )
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "backend")}
    if args.review:
        environment["WORKBENCH_API_PORT"] = "9187"
    if temporary:
        environment["WORKBENCH_PLANNER"] = "demo"
        environment["WORKBENCH_DB"] = str(Path(temporary.name) / "test.sqlite3")
    if args.e2e:
        environment['WORKBENCH_RESEARCH_ROOT'] = str(Path(temporary.name) / 'reviewed-research/source')
        templates = Path(temporary.name) / "task-templates"
        templates.mkdir()
        environment["WORKBENCH_TASK_TEMPLATES_ROOT"] = str(templates)
        projection_path = Path(temporary.name) / "pilot.sqlite3"
        create_projection(projection_path)
        environment["WORKBENCH_PILOT_DB"] = str(projection_path)
        environment.update(
            WORKBENCH_DB=str(Path(temporary.name) / "test.sqlite3"),
            WORKBENCH_WEB_PORT="5380",
            WORKBENCH_API_PORT="9287",
            WORKBENCH_API_ONLY="1",
        )
        (ROOT / ".local").mkdir(exist_ok=True)
        (ROOT / ".local/e2e-db-path").write_text(environment["WORKBENCH_DB"])
    python = str(ROOT / ".venv/bin/python")
    for command in (
        [str(ROOT / ".venv/bin/alembic"), "upgrade", "head"],
        [python, "-m", "workbench.seed"],
    ):
        subprocess.run(command, env=environment, check=True)
    commands = [
        [
            python,
            "-m",
            "uvicorn",
            "scripts.e2e_api:app" if args.e2e else "workbench.api:app",
            "--host",
            "127.0.0.1",
            "--port",
            environment.get("WORKBENCH_API_PORT", "8787"),
            "--no-proxy-headers",
        ],
        ["node", "node_modules/vite/bin/vite.js", "--config", "frontend/vite.config.ts"],
    ]
    if args.built:
        if not (ROOT / "frontend/dist/index.html").is_file():
            raise RuntimeError("Build the frontend with wb.py check first.")
        commands.pop()
    if not args.e2e:
        commands.insert(1, [python, "-m", "workbench.worker"])
    processes = []
    stopped = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stopped.set())
    try:
        for command in commands:
            processes.append(subprocess.Popen(command, env=environment))
        while not stopped.wait(0.5):
            if any(process.poll() is not None for process in processes):
                raise RuntimeError("A development process exited; stopping the owned processes.")
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if temporary:
            temporary.cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
