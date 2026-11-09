"""Closeout regression coverage. All inference is deterministic or transport-stubbed."""

import base64
from contextlib import nullcontext
from io import BytesIO
import json
from pathlib import Path
import socket
import threading
import time
from uuid import uuid4

import pytest
from sqlalchemy import select

from workbench import planning_worker
from workbench.db import intakes, planning_jobs
from workbench.planning_provider import DemoProvider, LocalProvider, PlanningUnavailable
from workbench.planning_transport import LocalHTTP, deadline_seconds
from workbench.schemas import DecompositionOutput

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = json.loads((ROOT / "fixtures/intake/guide-status.json").read_text())


def test_instruction_scope_stays_in_details_not_generated_title():
    subject = "Guide navigation"
    scope = "Research the original request and prepare evidence: Research whether selection persists. No implementation."
    packet = {
        "original": {"title": subject},
        "proposal": {"scope": [scope], "acceptance": ["Return findings."], "delivery": "research"},
    }
    raw, _, _ = DemoProvider().generate(packet, DecompositionOutput)
    task = raw["tasks"][0]
    assert task["title"] == "Assess feasibility — Guide navigation"
    assert task["purpose"] == scope and "No implementation" not in task["title"]
    assert packet["original"]["title"] == subject
    packet["original"]["title"] = "A user-authored title " + ("Long content " * 10)
    raw, _, _ = DemoProvider().generate(packet, DecompositionOutput)
    assert raw["tasks"][0]["title"] == "Assess feasibility"
    assert packet["original"]["title"].startswith("A user-authored title")


def test_representative_real_api_artifacts_review_and_decomposition(client, headers, store):
    body = {**EXAMPLE["request"], "request_id": str(uuid4())}
    saved = client.post("/api/intake", headers=headers, json=body).json()
    identity = saved["id"]
    assert saved["status"] == "queued" and not saved["proposals"]
    planning_worker.process_one(store, DemoProvider())

    def view():
        return client.get("/api/intake/" + identity).json()

    data = view()
    assert data["proposals"][0]["content"] == EXAMPLE["planning"]["proposal"]
    assert data["proposals"][0]["basis"] == "demo"
    old_binding = data["proposals"][0]["binding"]
    for role, name in [("mockup", "mockup"), ("game_context", "context")]:
        p = data["proposals"][0]
        artifact = {
            "artifact_id": str(uuid4()),
            "proposal_id": p["id"],
            "binding": p["binding"],
            "role": role,
            "label": "Synthetic " + name + "; not game or simulator evidence",
            "media_type": "image/png",
            "data_base64": base64.b64encode(
                (ROOT / f"fixtures/intake/guide-status-{name}.png").read_bytes()
            ).decode(),
        }
        response = client.post(
            "/api/intake/" + identity + "/artifacts", headers=headers, json=artifact
        )
        assert response.status_code == 200, response.text
        data = response.json()
    p = data["proposals"][0]
    decision = {
        "command_id": str(uuid4()),
        "proposal_id": p["id"],
        "binding": old_binding,
        "scope_revision": p["scope_revision"],
        "action": "approve",
        "note": "",
    }
    assert (
        client.post(
            "/api/intake/" + identity + "/decisions", headers=headers, json=decision
        ).status_code
        == 409
    )
    decision.update(command_id=str(uuid4()), binding=p["binding"])
    assert (
        client.post(
            "/api/intake/" + identity + "/decisions", headers=headers, json=decision
        ).json()["status"]
        == "pending"
    )
    planning_worker.apply_one(store)
    planning_worker.process_one(store, DemoProvider())
    data = view()
    assert len(data["tasks"]) == 4 and data["status"] == "tasks_ready"
    assert len({t["title"] for t in data["tasks"]}) == 4
    assert {t["title"] for t in data["tasks"] if t["parent"]} == {
        t["title"] for t in EXAMPLE["decomposition"]["tasks"]
    }
    assert any(t["dependencies"] for t in data["tasks"])
    assert not planning_worker.process_one(store, DemoProvider())
    assert len(view()["tasks"]) == 4
    assert data["original"]["title"] == body["title"] and data["priority"]["execution"].startswith(
        "Not authorized"
    )


@pytest.mark.parametrize("reason", ["canceled", "timeout"])
def test_deadline_or_shutdown_interrupts_blocked_socket_read(reason):
    stop = threading.Event()
    transport = LocalHTTP(stop)
    receiver, sender = socket.socketpair()
    transport.sockets.append(receiver)

    class Opener:
        def open(self, *_, **__):
            return receiver.makefile("rb")

    transport.opener = Opener()
    if reason == "timeout":
        transport.ends_at = time.monotonic() + 0.08
    trigger = threading.Timer(0.08, stop.set) if reason == "canceled" else None
    if trigger:
        trigger.start()
    started = time.monotonic()
    try:
        with pytest.raises(PlanningUnavailable) as error:
            with transport:
                transport.read(None, 100)
        assert error.value.reason == reason and time.monotonic() - started < 1
        assert receiver.fileno() == -1
    finally:
        sender.close()
        if trigger:
            trigger.join()


@pytest.mark.parametrize("value", ["0", "121", "invalid"])
def test_deadline_configuration_is_bounded(value, monkeypatch):
    monkeypatch.setenv("WORKBENCH_PLANNING_DEADLINE_S", value)
    with pytest.raises(PlanningUnavailable):
        deadline_seconds()


def test_worker_failure_category_is_safe_durable_and_lease_exceeds_deadline(client, headers, store):
    saved = client.post(
        "/api/intake", headers=headers, json={**EXAMPLE["request"], "request_id": str(uuid4())}
    ).json()

    class Failed:
        diagnostics = {"configured_model": "recorded-local-model", "deadline_s": 120}

        def generate(self, packet, schema):
            with store.connect() as c:
                job = c.execute(select(planning_jobs)).mappings().one()
                assert job["status"] == "running"
                from datetime import datetime

                assert (
                    datetime.fromisoformat(job["lease_until"])
                    - datetime.fromisoformat(job["created_at"])
                ).total_seconds() > 120
                assert (
                    c.execute(select(intakes.c.payload)).scalar_one()["title"]
                    == EXAMPLE["request"]["title"]
                )
            raise PlanningUnavailable(
                "secret must not reach user", "provider_rejection", {"http_status": 400}
            )

    planning_worker.process_one(store, Failed())
    data = client.get("/api/intake/" + saved["id"]).json()
    assert data["jobs"][0]["diagnostics"]["category"] == "provider_rejection"
    assert data["jobs"][0]["diagnostics"]["configured_model"] == "recorded-local-model"
    assert "secret" not in json.dumps(data)
    assert not data["proposals"]


def test_provider_records_rejection_and_model_identity_without_raw_error(tmp_path, monkeypatch):
    from urllib.error import HTTPError
    from workbench import planning_provider as module

    key = tmp_path / "key"
    key.write_text("synthetic")
    monkeypatch.setenv("WORKBENCH_LOCAL_KEY", str(key))
    monkeypatch.setattr(module, "local_slot", lambda **_: nullcontext())

    class Fake:
        def open(self, request, **_):
            if request.get_method() == "GET":
                return BytesIO(b'{"data":[{"id":"recorded-local"}]}')
            raise HTTPError(request.full_url, 400, "private error", {}, None)

    monkeypatch.setattr(module.urllib.request, "build_opener", lambda *_: Fake())
    provider = LocalProvider()
    with pytest.raises(PlanningUnavailable) as error:
        provider.generate({}, DecompositionOutput)
    assert error.value.reason == "provider_rejection"
    assert (
        provider.diagnostics["http_status"] == 400
        and provider.diagnostics["configured_model"] == "recorded-local"
    )
    assert "private error" not in json.dumps(provider.diagnostics)
