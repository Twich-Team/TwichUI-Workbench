"""Test-only API and worker entry points for the isolated browser rehearsal."""

from contextlib import nullcontext
import json
import os
from pathlib import Path
import signal
from threading import Event
import time

from acceptance_transport_guard import install_offline_guard
from workbench import planning_provider, task_session, task_sessions, trusted_preparation, worker
from workbench.db import engine_for

from browser_demo.gate import MODEL, SingleDemo

evidence = Path(os.environ["WB_REHEARSAL_CASE"])
database = Path(os.environ["WORKBENCH_DB"])
assert evidence.is_relative_to(database.parents[1]) and database.parent.name == "store"
assert os.environ.get("WORKBENCH_LOCAL_KEY") == str(evidence / "inert-key")
task_sessions.ROOT = task_session.ROOT = database.parent / "controlled-session-owner"


def create_app():
    from workbench.api import app
    return SingleDemo(app, evidence)


def run_worker():
    from test_grounded_workflow import ControlledPlanner, ControlledPreparer
    from local_browser_fixture import advance_local

    install_offline_guard()
    native = os.environ["WB_REHEARSAL_OUTCOME"] == "addon"
    # Only the supported rehearsal runs the native Lua suite, in its own
    # delegated worker group. Transport is scripted in either mode.
    if native:
        group = Path("/sys/fs/cgroup" + Path("/proc/self/cgroup").read_text().strip().split("::", 1)[1])
        assert group.name == os.environ["WB_REHEARSAL_UNIT"] + ".service"
        assert group.stat().st_uid == os.getuid()
        main = group / "main"
        main.mkdir()
        (main / "cgroup.procs").write_text(str(os.getpid()))
        (group / "cgroup.subtree_control").write_text("+memory +pids +cpu")
        os.environ["WORKBENCH_CG_ROOT"] = str(group)
        from workbench.real_confine import root
        root()
    stopped = Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopped.set())

    def exact_example(value, packet):
        example, = packet["examples"]
        if native:
            expected = json.loads((evidence / "rehearsal-task.json").read_text())["scenario"]
            value["draft"]["scenarios"] = [dict(expected, id="fade-threshold")]
            assert example["args"] == expected["args"]
            return
        assert example["args"] == [" ALICE "]
        value["draft"]["scenarios"][0]["args"] = example["args"]

    planner = ControlledPlanner(exact_example)

    class NoWire:
        seconds = 120

        def __init__(self, stop):
            self.stop = stop

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def models(self, secret):
            assert secret == "inert"
            return MODEL

        def read(self, request, limit):
            body = json.loads(request.data)
            packet = json.loads(body["messages"][-1]["content"])
            assert body["model"] == MODEL and body["max_tokens"] == 2600
            assert body["reasoning_budget_tokens"] == 0 and body["tools"] == []
            assert len(request.data) <= 24000 and limit == 24000
            with (evidence / "scripted-calls.jsonl").open("a") as stream:
                stream.write(json.dumps({"purpose": "planning", "wire_bytes": len(request.data)}) + "\n")
            (evidence / "planning-started").touch()
            if os.environ["WB_REHEARSAL_OUTCOME"] == "crash":
                os._exit(37)
            end = time.monotonic() + 12
            while time.monotonic() < end and not (evidence / "return-response").exists():
                if self.stop.wait(.025):
                    raise planning_provider.PlanningUnavailable("Controlled cancellation", "canceled")
            assert (evidence / "return-response").exists()
            response, _, _ = planner.generate(packet, None)
            mode = os.environ["WB_REHEARSAL_OUTCOME"]
            usage = {} if mode == "accounting" else dict(prompt_tokens=90, completion_tokens=30, total_tokens=120)
            reason = "length" if mode == "incomplete" else "stop"
            return json.dumps(dict(model=MODEL, usage=usage,
                choices=[dict(finish_reason=reason, message=dict(content=json.dumps(response)))])).encode()

    planning_provider.LocalHTTP = NoWire
    planning_provider.local_slot = nullcontext
    if not native:
        trusted_preparation.Runtime = ControlledPreparer
    engine = engine_for()
    (evidence / "worker-ready").touch()
    while not stopped.is_set():
        if (evidence / "ingress-budget.json").is_file():
            # The existing offline transport/candidate fixture consumes the real
            # browser decision through worker ticks and verifies real Lua.
            advance_local(engine, database, "wait", None, example=" ALICE ", native=native)
            advance_local(engine, database, "pass", None, example=" ALICE ", native=native)
            stopped.wait(20)
            return
        if not (evidence / "certified-offer.json").exists():
            worker.tick(engine, stop_event=stopped)
        if os.environ.get("WB_REHEARSAL_RECOVERY") and not stopped.is_set():
            for _ in range(3):
                assert not worker.tick(engine, stop_event=stopped)
            return
        stopped.wait(.075)


if __name__ == "__main__":
    run_worker()
