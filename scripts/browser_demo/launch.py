"""Start and stop only the API and worker owned by one isolated demonstration."""

from contextlib import ExitStack
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

from browser_demo.supervisor import RunHeld, Supervisor
from browser_demo.gate import SYNTHETIC_TASK
from project_env import environment, require_ports


def stop_process(process):
    if process.poll() is None:
        process.terminate()
        process.wait(timeout=15)


def api_client(origin):
    opener = build_opener(ProxyHandler({}))

    def request(path, data=None, csrf=None):
        headers = {} if data is None else {
            "Content-Type": "application/json", "Origin": origin, "X-CSRF-Token": csrf,
        }
        body = None if data is None else json.dumps(data).encode()
        with opener.open(Request(origin + path, data=body, headers=headers), timeout=5) as response:
            return json.load(response)

    return request


def run_owned(*, stage, database, evidence, port, env, deadline, room, stopping, activate,
              worker_command, stop_worker, verify_worker, api_app="browser_demo.gate:app", now=None,
              request_wrapper=None, runtime_root=None, task=SYNTHETIC_TASK, budget_reserve_minutes=55,
              owner_submits=False):
    require_ports([port])
    origin = f"http://127.0.0.1:{port}"
    request = api_client(origin)
    if request_wrapper:
        request = request_wrapper(request)
    worker = None
    python = stage / ".venv/bin/python"
    api_env = {**env, "WORKBENCH_DEMO_EVIDENCE": str(evidence)}
    with ExitStack() as owned:
        owned.callback(activate())
        api_log = owned.enter_context((evidence / "api.log").open("x"))
        api = subprocess.Popen(
            [str(python), "-m", "uvicorn", api_app, "--host", "127.0.0.1", "--port", str(port),
             "--no-proxy-headers", *(["--factory"] if api_app.endswith(":create_app") else [])], env=api_env, cwd=stage, stdin=subprocess.DEVNULL,
            stdout=api_log, stderr=subprocess.STDOUT)
        owned.callback(stop_process, api)
        for _ in range(60):
            try:
                request("/api/session")
                break
            except URLError:
                if api.poll() is not None or stopping() or (now or utc_now)() >= deadline:
                    raise RunHeld("The owned browser API did not start.") from None
                time.sleep(.15)
        else:
            raise RunHeld("The owned browser API did not start.")

        def start_worker():
            nonlocal worker
            log = owned.enter_context((evidence / "worker.log").open("x"))
            worker = subprocess.Popen(worker_command, env=env, cwd=stage, stdin=subprocess.DEVNULL,
                                      stdout=log, stderr=subprocess.STDOUT)
            owned.callback(stop_worker, worker)
            for _ in range(180):
                if worker.poll() is not None or stopping() or (now or utc_now)() >= deadline:
                    break
                if verify_worker(worker):
                    return
                time.sleep(.25)
            raise RunHeld("The owned worker did not pass its isolation and readiness checks.")

        def browser(step):
            script = Path(__file__).with_name("submit.cjs")
            with (evidence / f"browser-{step}.log").open("x") as log:
                subprocess.run(["node", str(script), step, str(evidence), origin], cwd=stage,
                               env=environment() | {"TMPDIR": env["TMPDIR"]}, stdin=subprocess.DEVNULL,
                               stdout=log, stderr=subprocess.STDOUT, check=True, timeout=55)

        Supervisor(database, evidence, origin, request, room,
                   lambda: api.poll() is None and worker is not None and worker.poll() is None,
                   stopping, deadline, runtime_root=runtime_root or stage, now=now,
                   task=task, budget_reserve_minutes=budget_reserve_minutes,
                   owner_submits=owner_submits).run(browser, start_worker, owned.close)


def utc_now():
    return datetime.now(timezone.utc)
