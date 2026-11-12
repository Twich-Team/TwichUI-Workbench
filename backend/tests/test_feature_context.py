"""Passive feature files/reference metadata in ordinary intake and worker owners.

Source-reader telemetry is controlled. PNG parsing, storage/bindings, restart,
API and worker planning are real; no external request or model generation runs.
"""

import base64
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from workbench import feature_attachments, feature_references, worker
from workbench.api import create_app
from workbench.db import engine_for, intakes, proposals, proposal_artifacts, real_execution_requests
from workbench.schemas import FeatureAttachmentUpload
from workbench.visual_scope import digest
from test_everyday import installed as installed
from test_feature_planning import ControlledFeaturePlanner, submit
from test_feature_references import Reader
from test_intake import png


def upload_body(request_id, *, raw=None, **changes):
    value = dict(
        request_id=str(request_id),
        attachment_id=str(uuid4()),
        filename="Original mockup.png",
        caption="Use the warm colors as design inspiration.",
        role="mockup",
        media_type="image/png",
        data_base64=base64.b64encode(raw if raw is not None else png()).decode(),
    )
    return {**value, **changes}


def fixture_lookup(store, request_id, values):
    from feature_reference_lookup import run

    calls = []

    def execute(args, **options):
        assert args[0] == "systemd-run" and "RuntimeMaxSec=100" in args and "--clearenv" in args
        assert "--unshare-user" in args and "--cap-drop" in args and "--setenv" in args
        assert "WORKBENCH_LOCAL_KEY" not in options["env"] and "GITHUB_TOKEN" not in options["env"]
        state = Path(args[args.index("--bind") + 1])
        request = json.loads((state / "request.json").read_text())
        assert request["request_id"] == str(request_id)
        data = run(request["references"], reader=Reader())
        from workbench.feature_context_storage import json_publish

        json_publish(state / "result.json", data)
        calls.append(1)
        return SimpleNamespace(returncode=0)

    actual = feature_references.lookup(store, request_id, values, execute=execute)
    assert len(calls) == 1
    assert (
        feature_references.lookup(
            store, request_id, values, execute=lambda *a, **k: pytest.fail("Cached lookup replayed")
        )
        == actual
    )
    return actual


def test_optional_files_and_attributed_references_survive_reload_and_enter_source_planning(
    client,
    headers,
    store,
    installed,
):
    request_id = uuid4()
    body = upload_body(request_id)
    uploaded = client.post("/api/feature-attachments", headers=headers, json=body)
    assert uploaded.status_code == 200, uploaded.text
    image = uploaded.json()
    assert image["filename"] == "Original mockup.png" and image["caption"] == body["caption"]
    assert image["size"] == len(png()) and image["width"] == 2
    assert client.post("/api/feature-attachments", headers=headers, json=body).json() == image
    assert (
        client.post(
            "/api/feature-attachments",
            headers=headers,
            json={**body, "caption": "Different intent"},
        ).status_code
        == 409
    )
    references = [
        dict(
            name_or_url="https://github.com/Team/Addon",
            instruction="Learn how it documents defaults.",
        ),
        dict(
            name_or_url="https://curseforge.com/wow/addons/addon",
            instruction="Keep this as an unverified optional reference.",
        ),
    ]
    looked = fixture_lookup(store, request_id, references)
    lookup = client.post(
        "/api/feature-references/lookup",
        headers=headers,
        json=dict(request_id=str(request_id), references=references),
    )
    assert lookup.status_code == 200 and lookup.json()["binding"] == digest(looked)
    assert [r["status"] for r in looked] == ["inspected", "unavailable"]
    claim = {k: image[k] for k in ("id", "sha256", "metadata_binding")}
    saved, original = submit(
        client,
        headers,
        request_id=str(request_id),
        feature_attachments=[claim],
        feature_references=dict(items=references, evidence_binding=digest(looked)),
    )
    with store.connect() as c:
        assert not c.execute(select(proposals)).first()
        assert not c.execute(select(proposal_artifacts)).first()
        assert not c.execute(select(real_execution_requests)).first()
    planner = ControlledFeaturePlanner()
    assert worker.tick(store, planning_adapter=planner)
    assert planner.calls[0]["original"]["description"] == original["description"]
    related = planner.calls[0]["related"]
    assert related[0]["data"]["caption"] == body["caption"]
    assert related[0]["data"]["interpretation"].startswith("Untrusted owner reference")
    assert (
        related[1]["data"]["revision"] == "a" * 40
        and "Ignore prior instructions" in related[1]["data"]["documentation"]["excerpt"]
    )
    assert all(e["kind"] == "addon" for e in planner.calls[0]["evidence"])
    restarted = engine_for(store.url.database)
    with TestClient(create_app(restarted)) as other:
        feature = other.get("/api/features/" + saved["id"]).json()
        assert feature["request_attachments"][0]["id"] == image["id"]
        assert feature["reference_addons"][0]["revision"] == "a" * 40
        content = other.get("/api/feature-attachments/" + saved["id"] + "/" + image["id"])
        assert content.content == png() and content.headers["content-type"] == "image/png"
        assert "sandbox" in content.headers["content-security-policy"]
    restarted.dispose()


@pytest.mark.parametrize("fault", ["foreign-request", "forged-hash", "metadata-tamper", "symlink"])
def test_untrusted_claims_never_select_foreign_or_changed_files(
    client, headers, store, installed, fault
):
    request_id = uuid4()
    body = upload_body(request_id)
    value = client.post("/api/feature-attachments", headers=headers, json=body).json()
    claim = {k: value[k] for k in ("id", "sha256", "metadata_binding")}
    if fault == "foreign-request":
        request_id = uuid4()
    if fault == "forged-hash":
        claim["sha256"] = "f" * 64
    if fault in {"metadata-tamper", "symlink"}:
        path = feature_attachments.folder(store, body["request_id"], body["attachment_id"])
        if fault == "metadata-tamper":
            metadata = json.loads((path / "metadata.json").read_text())
            metadata["caption"] = "Altered after upload"
            (path / "metadata.json").write_text(json.dumps(metadata))
        else:
            (path / "content").unlink()
            (path / "content").symlink_to(path / "metadata.json")
    response = client.post(
        "/api/intake",
        headers=headers,
        json=dict(
            request_id=str(request_id),
            kind="feature",
            requested_work="feature",
            title="Keep original references",
            description="Use the original reference only.",
            feature_attachments=[claim],
        ),
    )
    assert response.status_code == 422
    with store.connect() as c:
        assert not c.execute(select(intakes)).first()


def test_ambiguous_names_and_forged_reference_metadata_require_an_actual_source_choice(
    client, headers, store, installed
):
    request_id = uuid4()
    references = [dict(name_or_url="Addon", instruction="Review its approach.")]
    looked = fixture_lookup(store, request_id, references)
    assert looked[0]["status"] == "needs_clarification" and len(looked[0]["choices"]) == 2
    body = dict(
        request_id=str(request_id),
        kind="feature",
        requested_work="feature",
        title="Reference clarity",
        description="Use the intended reference.",
        feature_references=dict(items=references, evidence_binding=digest(looked)),
    )
    assert client.post("/api/intake", headers=headers, json=body).status_code == 422
    body["feature_references"]["items"][0]["name_or_url"] = "https://github.com/Uninspected/Other"
    assert client.post("/api/intake", headers=headers, json=body).status_code == 422
    with store.connect() as c:
        assert not c.execute(select(intakes)).first()


def test_upload_types_dimensions_text_and_only_the_exact_upload_route_gets_a_larger_body(
    client, headers, store, installed
):
    request_id = uuid4()
    text = b"A private supporting note.\n" + b"x" * 1100000
    body = upload_body(
        request_id,
        raw=text,
        filename="Supporting note.txt",
        media_type="text/plain",
        role="evidence",
    )
    assert client.post("/api/feature-attachments", headers=headers, json=body).status_code == 200
    assert (
        client.post(
            "/api/intake", headers=headers, json={"oversized": body["data_base64"]}
        ).status_code
        == 413
    )
    for changes in [
        dict(media_type="text/html"),
        dict(filename="../../unsafe.png"),
        dict(
            media_type="image/png",
            data_base64=base64.b64encode(b'<svg onload="execute()"/>').decode(),
        ),
        dict(role="mockup", media_type="text/plain"),
    ]:
        response = client.post(
            "/api/feature-attachments",
            headers=headers,
            json={**body, **changes, "attachment_id": str(uuid4())},
        )
        assert response.status_code == 422, response.text
    bad = FeatureAttachmentUpload.model_validate(upload_body(request_id, raw=b"not a PNG"))
    with pytest.raises(Exception) as error:
        feature_attachments.upload(store, bad)
    assert getattr(error.value, "status_code", None) == 422


def test_same_owner_new_submission_can_reuse_exact_reference_identity_without_copy_or_lookup(
    client,
    headers,
    store,
    installed,
):
    first = uuid4()
    target = uuid4()
    upload = client.post(
        "/api/feature-attachments", headers=headers, json=upload_body(first)
    ).json()
    references = [
        dict(
            name_or_url="https://github.com/Team/Addon",
            instruction="Use its documented defaults as context.",
        )
    ]
    looked = fixture_lookup(store, first, references)
    files = [
        dict(
            source_request_id=str(first),
            **{key: upload[key] for key in ("id", "sha256", "metadata_binding")},
        )
    ]
    request = dict(
        request_id=str(target),
        kind="feature",
        requested_work="feature",
        title="Reuse original context",
        description="Keep these exact original reference files and documented source.",
        feature_attachments=files,
        feature_references=dict(
            source_request_id=str(first), items=references, evidence_binding=digest(looked)
        ),
    )
    response = client.post("/api/intake", headers=headers, json=request)
    assert response.status_code == 200, response.text
    shown = client.get("/api/features/" + str(target)).json()
    assert shown["request_attachments"][0]["id"] == upload["id"]
    assert shown["request_attachments"][0]["request_id"] == str(first)
    assert shown["reference_addons"][0]["revision"] == "a" * 40
    assert not feature_attachments.folder(store, target, upload["id"]).exists()
    from workbench.feature_context_storage import base

    root = base(store, "reference-lookups", uuid4())
    assert not root.exists()
    assert feature_references.evidence(store, root.name, references) == []
    assert not root.exists()  # An unavailable view cannot create lookup artifacts.
