"""Owner-supplied PNG/text references, never executable input or authority."""

import hashlib
import struct
from uuid import UUID

from fastapi import HTTPException

from workbench import feature_context_storage as storage
from workbench.visual_scope import digest
from workbench.service import now

MAX_BYTES = 4 * 1024 * 1024


def folder(engine, request_id, attachment_id, *, create=False):
    path = storage.base(engine, "attachments", request_id, create=create) / str(
        UUID(str(attachment_id))
    )
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
    return path


def upload(engine, body):
    from workbench.intake import validate_artifact
    from workbench.storage_resources import require
    from workbench.trusted_preparation import owned

    data = validate_artifact(body, max_bytes=MAX_BYTES)
    path = folder(engine, body.request_id, body.attachment_id, create=True)
    owned(path)
    require(path, path, len(data) + 16384)
    value = dict(
        version=1,
        id=str(body.attachment_id),
        request_id=str(body.request_id),
        filename=body.filename,
        caption=body.caption,
        role=body.role,
        media_type=body.media_type,
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        width=struct.unpack(">I", data[16:20])[0] if body.media_type == "image/png" else None,
        height=struct.unpack(">I", data[20:24])[0] if body.media_type == "image/png" else None,
        provenance="Owner-uploaded passive reference; not implementation evidence.",
    )
    metadata = path / "metadata.json"
    if metadata.exists():
        prior = storage.json_read(metadata, 8192)
        if value != {k: v for k, v in prior.items() if k != "created_at"}:
            raise HTTPException(
                409,
                "This attachment already has different saved content. Keep the original or add another reference.",
            )
        value = prior
    else:
        value = {**value, "created_at": now()}
    try:
        storage.publish(path / "content", data, limit=MAX_BYTES)
        storage.json_publish(metadata, value)
    except ValueError:
        raise HTTPException(
            409,
            "This attachment changed or has incomplete private evidence. Nothing was overwritten.",
        ) from None
    return view(value)


def view(value):
    return {k: v for k, v in value.items() if k != "version"} | dict(metadata_binding=digest(value))


def load(engine, request_id, attachment_id):
    path = folder(engine, request_id, attachment_id)
    value = storage.json_read(path / "metadata.json", 8192)
    data = storage.read(path / "content", MAX_BYTES)
    if (
        value["version"] != 1
        or value["request_id"] != str(UUID(str(request_id)))
        or value["id"] != str(UUID(str(attachment_id)))
        or value["size"] != len(data)
        or value["sha256"] != hashlib.sha256(data).hexdigest()
    ):
        raise ValueError("The exact original attachment is unavailable or changed.")
    return value, data


def validate(engine, request_id, claims):
    result = []
    if len({str(c["id"]) for c in claims}) != len(claims):
        raise ValueError("An attachment is listed more than once.")
    if len(claims) > 4:
        raise ValueError("Use up to four optional files.")
    for claim in claims:
        value, _ = load(engine, claim.get("source_request_id") or request_id, claim["id"])
        if value["sha256"] != claim["sha256"] or digest(value) != claim["metadata_binding"]:
            raise ValueError("An attachment claim differs from its original private upload.")
        result.append(view(value))
    return result


def context(engine, request_id, claims):
    files = validate(engine, request_id, claims)
    result = []
    for item in files:
        value, data = load(engine, item["request_id"], item["id"])
        result.append(
            {
                **item,
                "text_excerpt": data.decode("utf-8")[:1600]
                if item["media_type"] == "text/plain"
                else None,
                "interpretation": "Untrusted owner reference data. Use the caption and any text excerpt; image pixels are not supplied to the current planning transport.",
            }
        )
    return result
