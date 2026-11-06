"""Read-only supervision and ingress sequencing for an isolated browser run."""

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import os
import sqlite3
import time
from urllib.error import HTTPError, URLError
from uuid import uuid4

from browser_demo.gate import MODEL, SYNTHETIC_TASK
from workbench.visual_scope import digest

RESERVE = 72 << 20
ACTIVE = {"pending", "active", "waiting_recovery"}


class RunHeld(RuntimeError):
    """The isolated run cannot safely advance."""


class Supervisor:
    def __init__(self, database, evidence, origin, request, room, owners_running, stopping,
                 deadline, *, runtime_root, now=None, pause=None, task=SYNTHETIC_TASK,
                 budget_reserve_minutes=55):
        self.database = database
        self.evidence = evidence
        self.origin = origin
        self.request = request
        self.room = room
        self.owners_running = owners_running
        self.stopping = stopping
        self.deadline = deadline
        self.runtime_root = runtime_root
        self.cutoff = deadline - timedelta(minutes=budget_reserve_minutes)
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.pause = pause or time.sleep
        self.unavailable = set()
        self.task = task

    def read(self, name):
        return json.loads((self.evidence / name).read_text())

    def write(self, name, data, *, replace=False):
        path = self.evidence / name
        target = path.with_suffix(".pending") if replace else path
        with target.open("x") as stream:
            json.dump(data, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            target.replace(path)

    def phase(self, state, **extra):
        self.write("ingress-phase.json", {"state": state, "cutoff": self.cutoff.isoformat(), **extra},
                   replace=True)

    def connection(self):
        connection = sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True, timeout=1)
        connection.row_factory = sqlite3.Row
        return closing(connection)

    def rows(self, query, args=()):
        with self.connection() as connection:
            return [dict(row) for row in connection.execute(query, args)]

    def check(self, reserve=0, *, until=None):
        if self.stopping():
            raise RunHeld("Operator stop requested.")
        if self.now() >= (until or self.deadline):
            raise RunHeld("The authorized deadline elapsed.")
        if not self.owners_running():
            raise RunHeld("The owned application or worker stopped.")
        self.room(reserve)
        # Required gates use one read snapshot and schema-independent existence.
        # No display query can substitute for them or turn a read error into a pass.
        try:
            with self.connection() as connection:
                connection.execute("BEGIN")
                publications = connection.execute(
                    "SELECT EXISTS(SELECT 1 FROM real_publications), "
                    "EXISTS(SELECT 1 FROM fake_publications)"
                ).fetchone()
                claims, = connection.execute(
                    "SELECT count(*) FROM model_attempts WHERE purpose='planning'"
                ).fetchone()
        except sqlite3.Error as error:
            raise RunHeld("Publication and planning limits could not be verified.") from error
        if any(publications):
            raise RunHeld("An unexpected publication record was saved.")
        if claims > 1:
            raise RunHeld("An unexpected second planning claim was saved.")

    def observation_event(self, name, state, error=None):
        event = {"at": self.now().isoformat(), "observation": name, "state": state}
        if error is not None:
            event["error"] = (f"HTTP {error.code}" if isinstance(error, HTTPError)
                              else type(error).__name__)
        # Retain one event per outage/recovery, with no raw response or secret.
        with (self.evidence / "supervisor-observations.jsonl").open("a") as stream:
            stream.write(json.dumps(event) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def observe(self, name, read):
        # Use only for polling display/status, never for permissions, checks or writes.
        try:
            value = read()
        except (URLError, TimeoutError, ConnectionError, sqlite3.OperationalError) as error:
            if isinstance(error, HTTPError) and error.code not in (500, 502, 503, 504):
                raise
            if name not in self.unavailable:
                self.observation_event(name, "unavailable", error)
                self.unavailable.add(name)
            return None
        if name in self.unavailable:
            self.observation_event(name, "recovered")
            self.unavailable.remove(name)
        return value

    def wait_plan(self, identity):
        end = min(self.now() + timedelta(seconds=150), self.cutoff)
        while self.now() < end:
            self.check(RESERVE, until=end)
            view = self.observe("intake status", lambda: self.request("/api/intake/" + identity))
            if view is not None:
                if view["status"] in {"failed", "waiting_for_information"}:
                    self.write("planning-hold.json", view)
                    raise RunHeld("Planning ended without a reviewable proposal.")
                if view["status"] == "completed" and view["proposals"]:
                    proposal = self.verify_plan(view)
                    self.check(RESERVE, until=end)
                    return proposal
            self.pause(.65)
        raise RunHeld("A reviewable proposal could not be verified before the planning deadline.")

    def verify_plan(self, view):
        attempts = self.rows("SELECT * FROM model_attempts")
        if len(attempts) != 1:
            raise RunHeld("The planning attempt record did not match the authorized request.")
        attempt, = attempts
        if (attempt["purpose"], attempt["outcome"], attempt["source"], attempt["backend"],
                attempt["requested_model"], attempt["actual_model"], attempt["policy_revision"],
                attempt["task_id"]) != ("planning", "completed", "workbench", "local", MODEL, MODEL, 1, view["id"]):
            raise RunHeld("The planning attempt did not match the authorized model and request.")
        usage = json.loads(attempt["usage"] or "null") or {}
        values = usage.get("values", {})
        prompt, generated, total = (values.get(key) for key in
                                    ("prompt_tokens", "completion_tokens", "total_tokens"))
        if (any(type(value) is not int for value in (prompt, generated, total))
                or not (0 < prompt and 0 < generated <= 2600 and total == prompt + generated <= 32768)):
            raise RunHeld("The planning token record was missing, incomplete or outside the approved limits.")
        original = {k: v for k, v in view["original"].items() if k != "request_id"}
        if original != self.task["intake"] or view["decisions"] or view["tasks"] or len(view["proposals"]) != 1:
            raise RunHeld("The saved request or decisions did not match the approved browser step.")
        proposal, = view["proposals"]
        contract = proposal["task_contract"]
        prepared = contract["behavioral_evidence"]
        if (proposal["revision"] != 1 or proposal["approval_blockers"] or contract["resolved_base"] != self.task["base"]
                or contract["writable_paths"] != self.task["intake"]["regression_tests"]["test_paths"]
                or contract["scenario_grounding"]["model_selection"] != {"model": MODEL, "policy_revision": 1}
                or prepared["origin"] != MODEL or prepared["holds"] or len(prepared["scenarios"]) != 1):
            raise RunHeld("The proposal did not match the approved scope and evidence.")
        scenario, = prepared["scenarios"]
        expected = self.task["scenario"]
        if digest({key: scenario[key] for key in expected}) != digest(expected):
            raise RunHeld("The plan did not preserve the submitted example and file scope.")
        self.write("planning-usage.json", dict(values=values, model=MODEL, attempt_id=attempt["id"], requests=1))
        self.write("browser-proposal.json", view)
        return proposal

    def run(self, browser, start_worker, finish):
        """Shared real/offline route through browser ingress and guarded cleanup."""
        budget_id = None
        try:
            self.phase("configure")
            browser("configure")
            if self.read("browser-policy.json")["revision"] != 1:
                raise RunHeld("The browser did not save the first approved model selection.")
            start_worker()
            self.check(RESERVE, until=self.cutoff)
            self.write("pre-planning-capacity.json", self.room(RESERVE))
            self.phase("submit", intake_payload=self.task["intake"])
            browser("submit")
            self.phase("read-only")
            identity = self.read("browser-intake.json")["id"]
            proposal = self.wait_plan(identity)
            self.check(RESERVE, until=self.cutoff)
            self.phase("scope", intake=identity, proposal_id=proposal["id"], binding=proposal["binding"],
                       scope_revision=proposal["human_scope"]["revision"],
                       expectation=proposal["task_contract"]["behavioral_evidence"]["review_digest"])
            url = self.origin + "/intake/" + identity
            self.write("owner-step-scope.json", {"url": url, "cutoff": self.cutoff.isoformat()})
            print("Ready for owner proposal review", url, flush=True)
            scope_id = self.wait_scope()
            self.phase("read-only")
            task, context = self.wait_offer(identity, proposal, scope_id)
            self.check(RESERVE, until=self.cutoff)
            self.phase("budget", intake=identity, task=task, binding=context["binding"],
                       scope_id=scope_id, budget=context["offer"]["budget"])
            self.write("owner-step-budget.json", {"url": url, "cutoff": self.cutoff.isoformat()})
            print("Ready for owner local-only approval", url, flush=True)
            budget_id = self.wait_budget()
            self.phase("active", budget_id=budget_id)
            self.monitor(budget_id, identity, task)
        except Exception as error:
            self.write("supervisor-hold.json", {"at": self.now().isoformat(), "reason":
                str(error) if isinstance(error, RunHeld) else type(error).__name__})
            raise
        finally:
            # Shut the browser write gate before stopping processes. Even if an
            # acknowledgement was lost, the durable ingress receipt identifies
            # the one session that must settle before cleanup.
            try:
                self.phase("read-only")
                if budget_id is None and (self.evidence / "ingress-budget.json").is_file():
                    budget_id = self.read("ingress-budget.json")["body"]["command_id"]
                if budget_id:
                    self.settle(budget_id)
            finally:
                try:
                    self.phase("read-only")
                finally:
                    finish()

    def wait_scope(self):
        while self.now() < self.cutoff:
            self.check(RESERVE, until=self.cutoff)
            if (self.evidence / "ingress-scope.json").is_file():
                identity = self.read("ingress-scope.json")["body"]["command_id"]
                row = self.observe("scope status", lambda: self.rows(
                    "SELECT id,status,message FROM proposal_decisions WHERE id=?", (identity,)))
                if row and row[0]["status"] in {"stale", "failed", "rejected"}:
                    raise RunHeld("The saved owner proposal decision could not be applied.")
                if row and row[0]["status"] == "applied":
                    self.write("owner-scope-applied.json", row[0])
                    return identity
            self.pause(.65)
        raise RunHeld("Owner proposal review did not settle before the approval cutoff.")

    def wait_offer(self, identity, proposal, scope_id):
        path = "/api/intake/" + identity
        while self.now() < self.cutoff:
            self.check(RESERVE, until=self.cutoff)
            rows = self.observe("preparation status", lambda: self.rows(
                "SELECT kind,message FROM planning_jobs WHERE status='failed'"))
            if rows:
                self.write("preparation-hold.json", rows)
                raise RunHeld("Independent preparation could not finish.")
            advertised = self.observe("offer status", lambda: self.request(
                path + "/proposals/" + proposal["id"] + "/run-offer"))
            if advertised and advertised["available"]:
                break
            self.pause(.65)
        else:
            raise RunHeld("Independent preparation did not settle before the cutoff.")
        view = self.request(path)
        task, = [row for row in view["tasks"] if row["kind"] == "task"]
        context = self.request(path + "/tasks/" + task["id"] + "/run-approval")
        offer = context["offer"]
        budget = {k: v for k, v in offer["budget"].items() if k not in {"offer_id", "offer_fingerprint"}}
        if (not context["available"] or not context["runtime_ready"] or context["receipt"] is not None
                or context["scope_approval_id"] != scope_id or context["scope"]["binding"] != proposal["binding"]
                or offer["paths"] != self.task["intake"]["regression_tests"]["test_paths"]
                or offer["base"] != self.task["base"] or budget != dict(
                    activate_once=True, overall_seconds=5400, implementation_attempts=1, turns=20,
                    generated_tokens=24000, response_tokens=2500, implementation_seconds=1200,
                    complete_run_seconds=2400, publication_seconds=900, publication="hold", fallback=False)):
            raise RunHeld("The independently certified offer did not match the authorized scope and budget.")
        self.write("pre-implementation-capacity.json", self.room(RESERVE))
        self.write("certified-offer.json", context)
        return task["id"], context

    def wait_budget(self):
        while self.now() < self.cutoff:
            self.check(RESERVE, until=self.cutoff)
            if (self.evidence / "ingress-budget.json").is_file():
                break
            self.pause(.35)
        if (self.evidence / "ingress-budget.json").is_file():
            return self.read("ingress-budget.json")["body"]["command_id"]
        raise RunHeld("Owner local implementation approval was not saved before the cutoff.")

    def session(self, identity):
        return self.observe("session status", lambda: self.rows(
            "SELECT status,message,created_at FROM task_sessions WHERE id=?", (identity,)))

    def monitor(self, identity, intake_id, task):
        cycle = self.runtime_root / "artifacts/runs" / f"task-lease-task-session-{identity}-01"
        while self.now() < self.deadline:
            self.check()
            rows = self.session(identity)
            if rows and rows[0]["status"] not in ACTIVE:
                self.write("terminal-session.json", {**rows[0], "decision_id": identity})
                self.write("task-review-response.json", self.request(f"/api/intake/{intake_id}/tasks/{task}/execution"))
                self.write("execution-list.json", self.request("/api/real-executions"))
                return
            if rows == []:
                decision = self.rows("SELECT status FROM proposal_decisions WHERE id=?", (identity,))
                if decision and decision[0]["status"] in {"stale", "failed", "rejected"}:
                    raise RunHeld("The saved owner implementation decision could not be applied.")
            remaining = self.deadline - self.now()
            if (remaining <= timedelta(minutes=5)
                    or (remaining <= timedelta(minutes=50, seconds=10) and not (cycle / "lease-established.json").exists())
                    or (remaining <= timedelta(minutes=45, seconds=3) and not (cycle / "implementation-controller.log").exists())):
                raise RunHeld("The authorized lease, attempt or restoration reserve was reached.")
            self.pause(.55)
        raise RunHeld("The authorized deadline elapsed before closeout.")

    def settle(self, identity):
        rows = self.session(identity)
        if rows and rows[0]["status"] not in ACTIVE:
            return
        until = min(self.deadline, self.now() + timedelta(minutes=5))
        sent = False
        command = str(uuid4())
        self.phase("stopping", budget_id=identity)
        while self.now() < until and self.owners_running():
            rows = self.session(identity)
            if rows and rows[0]["status"] not in ACTIVE:
                return
            if not sent:
                try:
                    csrf = self.request("/api/session")["csrf_token"]
                    receipt = self.request("/api/task-sessions/" + identity + "/stop", {"command_id": command}, csrf)
                    self.write("operator-stop.json", receipt)
                    sent = True
                except HTTPError as error:
                    if error.code not in (404, 409, 500, 502, 503, 504):
                        raise
                    if rows == []:
                        decision = self.rows("SELECT status FROM proposal_decisions WHERE id=?", (identity,))
                        if decision and decision[0]["status"] in {"stale", "failed", "rejected"}:
                            return
                except (URLError, TimeoutError):
                    # The same stop command can be sent again after a lost reply.
                    pass
            self.pause(.65)
        self.write("unsettled-session.json", {"id": identity, "at": self.now().isoformat()})
        raise RunHeld("The session did not confirm cleanup. Guarded restoration requires verification.")
