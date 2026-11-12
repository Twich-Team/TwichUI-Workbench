"""Passive, native simulator evidence alongside an existing combined result.

The model workspace cannot write this directory. No browser route imports images,
starts a simulator, changes a result, or authorizes publication.
"""

import base64
import re
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field
from sqlalchemy import select

from workbench.db import real_execution_requests, real_execution_grants
from workbench.feature_context_storage import read, json_read, publish
from workbench.real_workspace import sha
from workbench.schemas import StrictModel
from workbench.trusted_preparation import owned
from workbench.visual_scope import digest


class CaptureConditions(StrictModel):
    simulator: str = Field(min_length=1, max_length=120)
    simulator_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    ui_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    ui_version: str = Field(min_length=1, max_length=80)
    viewport: tuple[int, int]
    ui_scale: float = Field(ge=0.5, le=2)
    state: str = Field(min_length=1, max_length=500)
    settings: str = Field(min_length=1, max_length=500)
    scenario: str = Field(min_length=1, max_length=120)
    scenario_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    capture_seconds: int = Field(ge=0, le=30)
    network: Literal[False] = False
    game_install_access: Literal[False] = False


class CaptureImage(StrictModel):
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    bytes: int = Field(ge=1, le=4 << 20)
    source_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    conditions_binding: str = Field(pattern=r"^[a-f0-9]{64}$")
    stdout_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    stderr_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class SimulatorComparison(StrictModel):
    version: Literal[1] = 1
    kind: Literal["native-simulator-comparison-v1"] = "native-simulator-comparison-v1"
    execution_id: str
    candidate_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    result_binding: str = Field(pattern=r"^[a-f0-9]{64}$")
    captured_at: str
    status: Literal["captured", "unsupported"]
    conditions: CaptureConditions
    before: CaptureImage | None = None
    after: CaptureImage | None = None
    limitations: list[str] = Field(min_length=1, max_length=12)


class ComparisonView(StrictModel):
    status: Literal["not_recorded", "captured", "unsupported", "unavailable"]
    message: str
    evidence: SimulatorComparison | None = None


def coordinates(engine, identity):
    """Only the ledger's app-owned native directory supplies evidence paths."""
    try:
        identity = str(UUID(identity))
    except ValueError:
        raise HTTPException(404, "Saved result not found.") from None
    with engine.connect() as c:
        row = (
            c.execute(
                select(real_execution_requests).where(real_execution_requests.c.id == identity)
            )
            .mappings()
            .first()
        )
        if not row:
            raise HTTPException(404, "Saved result not found.")
        grant = (
            c.execute(
                select(real_execution_grants).where(real_execution_grants.c.id == row["grant_id"])
            )
            .mappings()
            .one()
        )
    if (
        grant["binding"] != digest(grant["snapshot"])
        or grant["snapshot"].get("operation_kind") != "feature-verification-v1"
        or row["status"] != "checked"
        or not row["finished_at"]
        or (row["result"] or {}).get("verification") != "passed"
    ):
        return row, grant, None
    candidate = row["result"].get("candidate_id")
    if not isinstance(candidate, str) or not re.fullmatch(r"[a-f0-9]{64}", candidate):
        raise ValueError("The combined candidate identity is unavailable.")
    from workbench.trusted_preparation import configuration

    # Existing passive context transfer preserves these files on installation.
    # Simulator work/cache stays outside this namespace and the checked run.
    root = (
        Path(configuration(engine)["output_root"])
        / "feature-context"
        / "simulator-comparisons"
        / identity
        / candidate
    )
    return row, grant, root


def create_root(root):
    missing = []
    ancestor = root.parent
    while not ancestor.exists():
        missing.append(ancestor)
        ancestor = ancestor.parent
    owned(ancestor)
    if ancestor.stat().st_mode & 0o077:
        raise ValueError("Simulator evidence requires private parent storage.")
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
    root.mkdir(mode=0o700)


def png(data, viewport):
    from types import SimpleNamespace
    import struct
    from workbench.intake import validate_artifact

    validate_artifact(
        SimpleNamespace(
            data_base64=base64.b64encode(data).decode(), media_type="image/png", role="evidence"
        ),
        max_bytes=4 << 20,
    )
    if list(struct.unpack(">II", data[16:24])) != list(viewport):
        raise ValueError("Simulator image dimensions differ from its capture conditions.")


def validate(root, value, row, grant):
    value = SimulatorComparison.model_validate(value)
    result = row["result"]
    spec = grant["snapshot"]["feature_verification"]
    conditions = value.conditions.model_dump(mode="json")
    if (
        value.execution_id != row["id"]
        or value.candidate_id != result["candidate_id"]
        or value.result_binding != digest(result)
        or not all(320 <= n <= 2160 for n in value.conditions.viewport)
    ):
        raise ValueError("Simulator evidence belongs to another result or viewport.")
    if value.status == "unsupported":
        if value.before is not None or value.after is not None:
            raise ValueError("An unsupported scenario cannot offer a comparison.")
        return value
    for name, revision in [
        ("before", spec["original_base"]),
        ("after", spec["feature_input"]["commit"]),
    ]:
        image = getattr(value, name)
        if (
            image is None
            or image.source_revision != revision
            or image.conditions_binding != digest(conditions)
        ):
            raise ValueError("Both captures must use equivalent conditions and exact source.")
        data = read(root / (name + ".png"), 4 << 20)
        if len(data) != image.bytes or sha(data) != image.sha256:
            raise ValueError("Native simulator image changed.")
        png(data, value.conditions.viewport)
        for stream in ["stdout", "stderr"]:
            raw = read(root / (name + "." + stream), 2 << 20)
            if sha(raw) != getattr(image, stream + "_sha256"):
                raise ValueError("Native simulator diagnostics changed.")
    return value


def view(engine, identity):
    try:
        row, grant, root = coordinates(engine, identity)
    except ValueError, OSError, KeyError, TypeError:
        return ComparisonView(
            status="unavailable",
            message="The saved simulator evidence could not be verified. The original result is unchanged.",
        )
    if root is None or not (root / "comparison.json").exists():
        return ComparisonView(
            status="not_recorded", message="No simulator comparison is recorded for this result."
        )
    try:
        value = validate(root, json_read(root / "comparison.json"), row, grant)
    except ValueError, OSError, KeyError, TypeError, HTTPException:
        return ComparisonView(
            status="unavailable",
            message="The saved simulator evidence could not be verified. The original result is unchanged.",
        )
    return ComparisonView(
        status=value.status,
        evidence=value,
        message="Original and combined feature under the same simulator conditions. Structural evidence only; game behavior is not verified."
        if value.status == "captured"
        else "The simulator could not capture a reliable comparison for this scenario.",
    )


def image(engine, identity, which):
    if which not in {"before", "after"}:
        raise HTTPException(404, "Capture not found.")
    try:
        row, grant, root = coordinates(engine, identity)
    except ValueError, OSError, KeyError, TypeError:
        raise HTTPException(409, "The saved capture is unavailable or changed.") from None
    if root is None:
        raise HTTPException(404, "No simulator comparison is recorded.")
    try:
        value = validate(root, json_read(root / "comparison.json"), row, grant)
        if value.status != "captured":
            raise ValueError("Scenario unsupported.")
        return read(root / (which + ".png"), 4 << 20)
    except ValueError, OSError, KeyError, TypeError, HTTPException:
        raise HTTPException(409, "The saved capture is unavailable or changed.") from None


def save(root, value, row, grant):
    """Native capture caller only; create-only sidecar, no result/store mutation."""
    value = validate(root, value, row, grant)
    publish(root / "comparison.json", value.model_dump_json().encode(), limit=262144)
