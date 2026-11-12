"""Passive capture validation and native Results ingress; rendered QA is separate."""

import copy
import json
from pathlib import Path
import struct
import zlib

import pytest
from sqlalchemy import select

from workbench import simulator_comparison as comparison
from workbench.db import planning_jobs
from workbench.feature_context_storage import publish
from workbench.real_workspace import sha
from workbench.visual_scope import digest
from test_everyday import installed as installed
from test_feature_check_preparation import at_independent_preparation


def png(width=320, height=320):
    def chunk(kind, value):
        return (
            struct.pack(">I", len(value))
            + kind
            + value
            + struct.pack(">I", zlib.crc32(kind + value))
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress((b"\0" + b"\0" * (width * 3)) * height))
        + chunk(b"IEND", b"")
    )


def controlled_sidecar(root, row, grant):
    comparison.create_root(root)
    spec = grant["snapshot"]["feature_verification"]
    conditions = dict(
        simulator="CONTROLLED format test, not an actual rendered capture",
        simulator_sha256="1" * 64,
        ui_revision="2" * 40,
        ui_version="controlled",
        viewport=[320, 320],
        ui_scale=1,
        state="Controlled state",
        settings="Same settings",
        scenario="controlled",
        scenario_sha256="3" * 64,
        capture_seconds=2,
        network=False,
        game_install_access=False,
    )
    conditions = comparison.CaptureConditions.model_validate(conditions).model_dump(mode="json")
    value = dict(
        execution_id=row["id"],
        candidate_id=row["result"]["candidate_id"],
        result_binding=digest(row["result"]),
        captured_at="2026-11-12T00:00:00+00:00",
        status="captured",
        conditions=conditions,
        limitations=["Controlled format test only; not simulator acceptance."],
    )
    for side, revision in [
        ("before", spec["original_base"]),
        ("after", spec["feature_input"]["commit"]),
    ]:
        raw = png()
        publish(root / (side + ".png"), raw, limit=4 << 20)
        for stream in ["stdout", "stderr"]:
            publish(root / (side + "." + stream), b"CONTROLLED", limit=1024)
        value[side] = dict(
            sha256=sha(raw),
            bytes=len(raw),
            source_revision=revision,
            conditions_binding=digest(conditions),
            stdout_sha256=sha(b"CONTROLLED"),
            stderr_sha256=sha(b"CONTROLLED"),
        )
    return value


@pytest.fixture
def artifact(tmp_path):
    root = tmp_path / "native"
    row = dict(id="00000000-0000-0000-0000-000000000001", result=dict(candidate_id="4" * 64))
    grant = dict(
        snapshot=dict(
            feature_verification=dict(original_base="5" * 40, feature_input=dict(commit="6" * 40))
        )
    )
    value = controlled_sidecar(root, row, grant)
    return root, row, grant, value


@pytest.mark.parametrize(
    "fault",
    ["revision", "conditions", "result", "png", "log", "link", "dimensions", "unsupported-image"],
)
def test_capture_must_retain_exact_source_conditions_and_native_bytes(artifact, fault):
    root, row, grant, value = artifact
    assert comparison.validate(root, value, row, grant).status == "captured"
    broken = copy.deepcopy(value)
    if fault == "revision":
        broken["after"]["source_revision"] = "7" * 40
    elif fault == "conditions":
        broken["conditions"]["ui_scale"] = 1.25
    elif fault == "result":
        broken["result_binding"] = "8" * 64
    elif fault == "png":
        (root / "after.png").write_bytes(png(321, 320))
    elif fault == "log":
        (root / "after.stdout").write_bytes(b"changed")
    elif fault == "link":
        (root / "after.png").rename(root / "moved.png")
        (root / "after.png").symlink_to(root / "moved.png")
    elif fault == "dimensions":
        raw = png(321, 320)
        (root / "after.png").write_bytes(raw)
        broken["after"].update(sha256=sha(raw), bytes=len(raw))
    else:
        broken["status"] = "unsupported"
    with pytest.raises((ValueError, OSError)):
        comparison.validate(root, broken, row, grant)


def test_unsupported_scenario_retains_honest_limitations_without_images(artifact):
    root, row, grant, value = artifact
    value.update(status="unsupported", before=None, after=None)
    comparison.save(root, value, row, grant)
    saved = json.loads((root / "comparison.json").read_bytes())
    assert saved["status"] == "unsupported" and saved["before"] is None
    assert "not simulator acceptance" in saved["limitations"][0]
    comparison.save(root, value, row, grant)  # Same immutable evidence is idempotent.
    value["limitations"] = ["Changed evidence"]
    with pytest.raises(ValueError, match="immutable"):
        comparison.save(root, value, row, grant)


def test_existing_results_serve_only_bound_passive_capture_and_never_change_history(
    client,
    headers,
    store,
    installed,
    monkeypatch,
):
    from workbench import real_confine
    from everyday_store import fingerprint
    import sqlite3

    monkeypatch.setattr(
        real_confine, "root", lambda: "/sys/fs/cgroup/workbench-real-CONTROLLED-local-feature"
    )
    _, _, _, tick = at_independent_preparation(client, headers, store, monkeypatch)
    assert tick() and tick() and tick()
    with store.connect() as c:
        job = (
            c.execute(select(planning_jobs).where(planning_jobs.c.kind == "feature_execution"))
            .mappings()
            .one()
        )
        identity = job["payload"]["final_verification_execution"]
    with sqlite3.connect(store.url.database) as c:
        before = fingerprint(c)
    url = "/api/real-executions/" + identity + "/simulator"
    assert client.get(url).json()["status"] == "not_recorded"
    row, grant, root = comparison.coordinates(store, identity)
    value = controlled_sidecar(root, row, grant)
    comparison.save(root, value, row, grant)
    assert client.get(url).json()["status"] == "captured"
    image = client.get(url + "/before")
    assert image.status_code == 200 and image.content == png()
    assert image.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in image.headers["content-security-policy"]
    (root / "after.png").write_bytes(b"changed")
    assert client.get(url).json()["status"] == "unavailable"
    assert client.get(url + "/after").status_code == 409
    assert client.get(url + "/../comparison.json").status_code != 200
    with sqlite3.connect(store.url.database) as c:
        assert fingerprint(c) == before


def test_capture_staging_rejects_committed_link_before_any_simulator_starts(tmp_path, monkeypatch):
    import io
    import tarfile
    from workbench import simulator_capture

    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w") as tar:
        entry = tarfile.TarInfo("!!!TwichUI/sensitive.lua")
        entry.type = tarfile.SYMTYPE
        entry.linkname = "/etc/passwd"
        tar.addfile(entry)
    monkeypatch.setattr(simulator_capture, "git", lambda *a, **k: data.getvalue())
    with pytest.raises(ValueError, match="archive entry"):
        simulator_capture.stage(Path("/controlled"), "1" * 40, tmp_path)
