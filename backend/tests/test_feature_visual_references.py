"""Owner images become revision references only through an explicit native action."""

import copy
import struct
from uuid import uuid4
import zlib

import pytest
from sqlalchemy import select, func

from workbench import feature_attachments, worker
from workbench.db import (
    model_attempts,
    proposal_artifacts,
    proposal_decisions,
    real_execution_requests,
)
from workbench.schemas import PlanningOutput
from test_everyday import installed as installed
from test_feature_context import upload_body
from test_feature_planning import ControlledFeaturePlanner, submit
from test_intake import png, decision, decide


def planned(client, headers, store, *, large=False):
    request_id = str(uuid4())
    files = []
    for role in ["mockup", "game_context"]:
        raw = png()
        if large:
            text = b"Comment\0" + b"x" * (1 << 20)
            chunk = struct.pack(">I", len(text)) + b"tEXt" + text
            raw = raw[:-12] + chunk + struct.pack(">I", zlib.crc32(b"tEXt" + text)) + raw[-12:]
        body = upload_body(request_id, raw=raw, role=role, filename=role + " original.png")
        response = client.post("/api/feature-attachments", headers=headers, json=body)
        assert response.status_code == 200, response.text
        files.append(response.json())
    claims = [{k: file[k] for k in ["id", "sha256", "metadata_binding"]} for file in files]
    saved, _ = submit(
        client,
        headers,
        request_id=request_id,
        visual_requirement="visual",
        feature_attachments=claims,
    )

    def visual(result, schema):
        if schema is PlanningOutput:
            result["proposal"]["visual"] = "visual"

    assert worker.tick(store, planning_adapter=ControlledFeaturePlanner(mutate=visual))
    result = client.get("/api/intake/" + saved["id"]).json()
    return result, files


def adoption(data, file):
    proposal = data["proposals"][0]
    return dict(
        artifact_id=str(uuid4()),
        proposal_id=proposal["id"],
        binding=proposal["binding"],
        reference_id=file["id"],
    )


@pytest.mark.parametrize("large", [False, True])
def test_saved_owner_images_require_explicit_revision_choice_and_keep_approval_separate(
    client,
    headers,
    store,
    installed,
    large,
):
    data, files = planned(client, headers, store, large=large)
    original = copy.deepcopy(data["original"])
    assert len(data["proposals"][0]["approval_blockers"]) == 2
    assert data["proposals"][0]["artifacts"] == []
    with store.connect() as c:
        attempts = c.execute(select(func.count()).select_from(model_attempts)).scalar_one()
    first = adoption(data, files[0])
    endpoint = "/api/intake/" + data["id"] + "/reference-artifacts"
    response = client.post(endpoint, headers=headers, json=first)
    assert response.status_code == 200, response.text
    after = response.json()
    assert after["original"] == original
    assert (
        len(after["proposals"][0]["artifacts"]) == 1
        and len(after["proposals"][0]["approval_blockers"]) == 1
    )
    assert client.post(endpoint, headers=headers, json=first).json() == after
    stale = {**adoption(data, files[1])}
    assert client.post(endpoint, headers=headers, json=stale).status_code == 409
    second = adoption(after, files[1])
    response = client.post(endpoint, headers=headers, json=second)
    assert response.status_code == 200, response.text
    final = response.json()
    assert final["proposals"][0]["approval_blockers"] == []
    assert final["original"] == original and final["decisions"] == []
    for file in files:
        artifact = next(a for a in final["proposals"][0]["artifacts"] if a["role"] == file["role"])
        _, raw = feature_attachments.load(store, data["id"], file["id"])
        assert client.get("/api/planning-artifacts/" + artifact["id"]).content == raw
        assert artifact["sha256"] == file["sha256"] and artifact["label"] == file["filename"]
        if large:
            assert len(raw) > 1 << 20
    with store.connect() as c:
        assert c.execute(select(func.count()).select_from(model_attempts)).scalar_one() == attempts
        assert not c.execute(select(proposal_decisions)).first()
        assert not c.execute(select(real_execution_requests)).first()
    assert decide(client, headers, final, decision(final)).status_code == 200
    assert client.post(endpoint, headers=headers, json=adoption(final, files[0])).status_code == 409


@pytest.mark.parametrize("fault", ["foreign-file", "changed-file", "missing-file"])
def test_reference_adoption_rejects_foreign_or_unavailable_owner_evidence(
    client,
    headers,
    store,
    installed,
    fault,
):
    data, files = planned(client, headers, store)
    command = adoption(data, files[0])
    if fault == "foreign-file":
        foreign = client.post(
            "/api/feature-attachments", headers=headers, json=upload_body(uuid4())
        ).json()
        command["reference_id"] = foreign["id"]
        expected = 422
    else:
        path = feature_attachments.folder(store, data["id"], files[0]["id"]) / "content"
        if fault == "missing-file":
            path.unlink()
        else:
            path.write_bytes(b"Changed after original upload")
        expected = 409
    response = client.post(
        "/api/intake/" + data["id"] + "/reference-artifacts", headers=headers, json=command
    )
    assert response.status_code == expected, response.text
    with store.connect() as c:
        assert not c.execute(select(proposal_artifacts)).first()
        assert not c.execute(select(proposal_decisions)).first()
