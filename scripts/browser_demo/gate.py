"""Restrict a single isolated demonstration. Forward original browser commands."""
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from starlette.responses import JSONResponse
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
            body.pop("request_id")
            return body == INTAKE
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

    async def __call__(self, scope, receive, send):
        method = scope.get("method")
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
                    with receipt.open("x") as record:
                        json.dump({"at":datetime.now(timezone.utc).isoformat(),
                                   "method":method,"path":path,"body":raw}, record)
                        record.write("\n")
                        record.flush()
                        os.fsync(record.fileno())
                await send(message)

            await self.app(scope, replay, capture)

if "WORKBENCH_DEMO_EVIDENCE" in os.environ:
    from workbench.api import app as normal_api
    app = SingleDemo(normal_api, Path(os.environ["WORKBENCH_DEMO_EVIDENCE"]))
