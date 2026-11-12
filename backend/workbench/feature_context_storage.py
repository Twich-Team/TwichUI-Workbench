"""Private passive feature evidence in the existing preparation namespace."""

import json
import os
from pathlib import Path
import stat
from uuid import UUID, uuid4

from workbench.trusted_preparation import configuration, owned


def base(engine, category, request_id, *, create=False):
    if category not in {"attachments", "reference-lookups"}:
        raise ValueError("Unsupported feature evidence category.")
    request_id = str(UUID(str(request_id)))
    path = Path(configuration(engine)["output_root"]) / "feature-context" / category / request_id
    if create:
        path.mkdir(parents=True, mode=0o700, exist_ok=True)
    if path.exists():
        owned(path)
        if path.stat().st_mode & 0o077:
            raise ValueError("Feature evidence requires private storage.")
    return path


def read(path, limit):
    owned(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or info.st_nlink != 1
            or info.st_size > limit
        ):
            raise ValueError("Private feature evidence is unavailable or changed.")
        return source.read(limit + 1)


def json_read(path, limit=262144):
    return json.loads(read(path, limit))


def publish(path, data, *, limit):
    if not isinstance(data, bytes) or len(data) > limit:
        raise ValueError("Feature evidence exceeds its operation limit.")
    owned(path.parent)
    temp = path.parent / (".pending-" + uuid4().hex)
    try:
        fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        try:
            os.link(temp, path, follow_symlinks=False)
        except FileExistsError:
            if read(path, limit) != data:
                raise ValueError(
                    "Feature evidence already has different immutable content."
                ) from None
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temp.unlink(missing_ok=True)


def json_publish(path, value):
    publish(path, json.dumps(value, sort_keys=True, ensure_ascii=False).encode(), limit=262144)
