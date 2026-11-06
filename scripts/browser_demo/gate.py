"""Restrict a single isolated demonstration. Forward original browser commands."""
import asyncio
from datetime import datetime, timezone
from html import escape
import json
import os
from pathlib import Path
from secrets import token_urlsafe
from uuid import UUID
from zoneinfo import ZoneInfo

from starlette.responses import HTMLResponse, JSONResponse
from workbench.model_policy import ExecutionPolicy
from workbench.schemas import IntakeInput, ProposalDecisionInput, TaskRunApprovalInput

TITLE = "Cover lowercase name normalization"
MODEL = "pilot-qwen38-27b"
SOURCE = "!!!TwichUI/normalize.lua"
TARGET = "tests/test_normalize.lua"
BASE = "ebb16fff02f755bf523f3a7c30787ebdc0c8e894"
INTAKE = dict(kind="feature", title=TITLE,
    description="In the synthetic fixture, cover trimming surrounding spaces and lowercasing names.",
    visual_requirement="nonvisual", lua_error="",
    regression_tests=dict(test_paths=[TARGET],
        acceptance=["The first result for a spaced uppercase name is the trimmed lowercase name."],
        text_example=dict(input=" ALICE ", expected="alice", incorrect="ALICE")))
SYNTHETIC_TASK = dict(base=BASE, intake=INTAKE, scenario=dict(
    args=[" ALICE "], observe=dict(path=[], expected="alice"), incorrect="ALICE",
    fixture="module", target=[], method=False, setup=[], addons=["!!!TwichUI"],
    source_paths=[SOURCE], test_path=TARGET, acceptance_index=0))

class SingleDemo:
    def __init__(self, app, evidence):
        self.app = app
        self.evidence = evidence
        self.lock = asyncio.Lock()

    def phase(self):
        return json.loads((self.evidence / "ingress-phase.json").read_text())

    def allow(self, method, path, raw, phase):
        state = phase["state"]
        if state in {"active","stopping"}:
            return method == "POST" and path == f'/api/task-sessions/{phase["budget_id"]}/stop'
        if datetime.now(timezone.utc) >= datetime.fromisoformat(phase["cutoff"]):
            return False
        if state == "configure" and method == "PUT" and path == "/api/models/policy":
            policy = ExecutionPolicy.model_validate(raw)
            provider = policy.providers["local"]
            return (policy.revision == 0 and policy.preferred == "local" and not policy.fallback
                    and policy.substitution == "deny" and policy.when_unavailable == "block"
                    and provider.model == MODEL and provider.enabled and provider.effort is None
                    and all(not policy.providers[name].enabled and not policy.providers[name].model
                            for name in ("codex", "claude")))
        if state == "submit" and method == "POST" and path == "/api/intake":
            body = IntakeInput.model_validate(raw).model_dump(mode="json")
            identity = body.pop("request_id")
            return (body == phase["intake_payload"]
                    and (not phase.get("intake_request_id") or identity == phase["intake_request_id"]))
        root = "/api/intake/" + phase.get("intake", "")
        if state == "scope" and method == "POST" and path == root + "/decisions":
            body = ProposalDecisionInput.model_validate(raw)
            return (body.action == "approve" and body.run_budget is None and not body.note
                    and str(body.proposal_id) == phase["proposal_id"] and body.binding == phase["binding"]
                    and body.scope_revision == phase["scope_revision"]
                    and body.expectation_review == phase["expectation"])
        if state == "budget" and method == "POST" and path == root + f'/tasks/{phase["task"]}/run-approval':
            body = TaskRunApprovalInput.model_validate(raw)
            return (body.binding == phase["binding"] and str(body.scope_approval_id) == phase["scope_id"]
                    and body.run_budget.model_dump(mode="json") == phase["budget"] and not body.note)
        return False

    def owner_intake_form(self, scope):
        hosts = [value for key, value in scope["headers"] if key == b"host"]
        if hosts not in ([b"127.0.0.1:9187"], [b"127.0.0.1:9387"]):
            return JSONResponse({"detail": "Open the isolated trial on its loopback address."}, status_code=400)
        try:
            phase = self.phase()
            if (phase["state"] != "submit" or not phase.get("intake_request_id")
                    or datetime.now(timezone.utc) >= datetime.fromisoformat(phase["cutoff"])
                    or (self.evidence / "ingress-submit.json").exists()):
                raise ValueError("Intake is closed")
            from browser_demo.stage import require_active_stage
            require_active_stage()
            body = IntakeInput.model_validate(dict(phase["intake_payload"], request_id=phase["intake_request_id"]))
        except (KeyError, OSError, ValueError):
            return JSONResponse({"detail": "The trial request is not available for submission."}, status_code=409)
        # Populate only a browser draft. The real form owns the explicit submit
        # action, and the gate requires the same request and identity.
        nonce = token_urlsafe(18)
        draft = body.model_dump_json().replace("<", r"\u003c")
        key = json.dumps(f"intake-draft:{body.kind}")
        url = json.dumps(f"/intake/new?kind={body.kind}")
        return HTMLResponse(
            '<!doctype html><html lang="en"><meta charset="utf-8"><title>Open the trial request</title>'
            '<p>Opening the prepared task. Review it and select Submit request when ready.</p>'
            f'<script nonce="{nonce}">localStorage.setItem({key},JSON.stringify({draft}));'
            f'location.replace({url});</script></html>',
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                     "Content-Security-Policy": f"default-src 'none'; script-src 'nonce-{nonce}'; frame-ancestors 'none'"},
        )

    def owner_run_status(self):
        """A read-only handoff. The ordinary task page still owns the decision."""
        refresh = ""
        action = ""
        deadline = ""
        try:
            phase = self.phase()
            cutoff = datetime.fromisoformat(phase["cutoff"])
            mountain = cutoff.astimezone(ZoneInfo("America/Denver"))
            deadline = f'<p>Browser decisions close at <time datetime="{cutoff.isoformat()}">{mountain.strftime("%B %d at %I:%M:%S %p %Z")}</time> (Mountain time).</p>'
            if (self.evidence / "ingress-budget.json").is_file():
                title = "Local-run decision saved"
                detail = "Workbench recorded your decision. Open the task to see whether it has been applied."
                intake = json.loads((self.evidence / "ingress-submit.json").read_text())
                action = f'<a href="/intake/{UUID(intake["body"]["request_id"])}">Open your task</a>'
            elif datetime.now(timezone.utc) >= cutoff or (self.evidence / "supervisor-hold.json").is_file():
                title = "Local-run review closed"
                detail = "The trial closed without a saved local-run decision. The earlier plan remains in the record."
            elif phase["state"] == "budget":
                identity = UUID(phase["intake"])
                title = "Your local-run decision is ready"
                detail = "Review what can change and the run limits. Approve in Workbench only if you want the local run to start."
                action = f'<a href="/intake/{identity}#local-run-review">Review the local run in Workbench</a>'
            else:
                title = "Waiting for the local-run offer"
                detail = "The local-run decision comes after the plan review and independent checks. This page will update when it is ready."
                refresh = '<meta http-equiv="refresh" content="3">'
        except (OSError, ValueError, KeyError, TypeError):
            title = "Trial status unavailable"
            detail = "Reload to check the current status before making a decision."
        return HTMLResponse(
            '<!doctype html><html lang="en"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{escape(title)}</title>{refresh}'
            '<main style="max-width:42rem;margin:2rem auto;padding:0 1rem;font:1.125rem/1.55 system-ui">'
            f'<h1>{escape(title)}</h1><p>{escape(detail)}</p>{deadline}<p>{action}</p></main></html>',
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                     "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'"},
        )

    async def __call__(self, scope, receive, send):
        method = scope.get("method")
        if scope["type"] == "http" and method == "GET" and scope["path"] == "/demo/request":
            return await self.owner_intake_form(scope)(scope, receive, send)
        if scope["type"] == "http" and method == "GET" and scope["path"] == "/demo/local-run":
            return await self.owner_run_status()(scope, receive, send)
        if scope["type"] != "http" or method in ("GET", "HEAD"):
            return await self.app(scope, receive, send)
        path = scope["path"]
        chunks = bytearray()
        while True:
            event = await receive()
            if event["type"] != "http.request":
                return
            chunks.extend(event.get("body", b""))
            if len(chunks) > 16384:
                return await JSONResponse({"detail":"Request exceeds this run's boundary."}, 413)(scope, receive, send)
            if not event.get("more_body"):
                break
        async with self.lock:
            try:
                phase = self.phase()
                raw = json.loads(chunks)
                allowed = self.allow(method, path, raw, phase)
                if allowed and phase["state"] not in {"active", "stopping"}:
                    from browser_demo.stage import require_active_stage
                    require_active_stage()
                    allowed = datetime.now(timezone.utc) < datetime.fromisoformat(phase["cutoff"])
            except (OSError, ValueError, KeyError, TypeError):
                allowed = False
            if not allowed:
                return await JSONResponse({"detail":"This action is outside the current demonstration step or cutoff."}, 409)(scope, receive, send)
            receipt = self.evidence / ("ingress-" + phase["state"] + ".json")
            if receipt.exists() and json.loads(receipt.read_text())["body"] != raw:
                return await JSONResponse({"detail":"One command is already recorded for this step."}, 409)(scope, receive, send)

            delivered = False
            async def replay():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type":"http.request","body":bytes(chunks),"more_body":False}
                return await receive()

            async def capture(message):
                if message["type"] == "http.response.start" and 200 <= message["status"] < 300 and not receipt.exists():
                    pending = receipt.with_suffix(".pending")
                    with pending.open("w") as record:
                        json.dump({"at":datetime.now(timezone.utc).isoformat(),
                                   "method":method,"path":path,"body":raw}, record)
                        record.write("\n")
                        record.flush()
                        os.fsync(record.fileno())
                    pending.replace(receipt)
                await send(message)

            await self.app(scope, replay, capture)

if "WORKBENCH_DEMO_EVIDENCE" in os.environ:
    from workbench.api import app as normal_api
    app = SingleDemo(normal_api, Path(os.environ["WORKBENCH_DEMO_EVIDENCE"]))
