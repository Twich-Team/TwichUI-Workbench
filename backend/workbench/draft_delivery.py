"""Standing draft authority and delivery over the existing private egress connector."""

from datetime import datetime
from contextlib import contextmanager, nullcontext
import json
import os
from pathlib import Path
import re
import select
import socket
import time
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field
from sqlalchemy import select as sql_select

from workbench import real_publication as publication
from workbench.db import engine_for, real_publications, real_execution_requests, real_execution_grants
from workbench.real_workspace import durable
from workbench.schemas import StrictModel
from workbench.visual_scope import digest

ROOT = Path(__file__).resolve().parents[2]


class DraftRequest(StrictModel):
    binding: str = Field(pattern=r"^[0-9a-f]{64}$")


def configuration(engine=None):
    stage = ROOT.parent
    path = stage / "artifacts/draft-authority.json"
    database = stage / "artifacts/workbench.sqlite3"
    try:
        if (path.resolve() != path or not path.is_file() or path.stat().st_uid != os.getuid()
                or path.stat().st_mode & 0o077 or path.stat().st_size > 4096
                or database.resolve() != database or not database.is_file()
                or database.stat().st_uid != os.getuid() or database.stat().st_mode & 0o077):
            return None
        data = json.loads(path.read_text())
        if (set(data) != {"repository", "base_branch", "owner_instruction_sha256",
                         "database_identity", "automatic_since"}
                or data["repository"] != "Twich-Team/TwichUI" or data["base_branch"] != "main"
                or not isinstance(data["owner_instruction_sha256"], str)
                or not re.fullmatch("[0-9a-f]{64}", data["owner_instruction_sha256"])
                or data["database_identity"] != [database.stat().st_dev, database.stat().st_ino]
                or datetime.fromisoformat(data["automatic_since"]).utcoffset() is None
                or engine is not None and engine.url.database != str(database)):
            return None
        return {**data, "digest": digest(data), "database": database,
                "root": stage / "artifacts/draft-delivery"}
    except (ValueError, OSError, TypeError, KeyError):
        return None


def approval_current(root, approval):
    policy = configuration()
    return bool(policy and root.parent == policy["root"]
                and approval["standing_policy_sha256"] == policy["digest"]
                and approval["owner_instruction_sha256"] == policy["owner_instruction_sha256"])


def review(engine, identity):
    if not configuration(engine):
        return dict(available=False, message="Standing draft publication is unavailable.")
    try:
        result = publication.review_checked(engine, identity)
        return dict(available=True, message="Submit this exact checked change as a draft PR.", **result)
    except (ValueError, OSError, HTTPException):
        return dict(available=False, message="This saved result needs review before draft delivery.")


def request(engine, identity, binding):
    policy = configuration(engine)
    if not policy:
        raise HTTPException(409, "Standing draft publication is unavailable.")
    from workbench.work_controls import read as work_status
    if work_status(engine).paused:
        raise HTTPException(409, "New work is paused. Resume work before starting draft delivery.")
    with engine.connect() as c:
        linked=c.execute(sql_select(real_execution_grants.c.snapshot).join(real_execution_requests,real_execution_requests.c.grant_id==real_execution_grants.c.id).where(real_execution_requests.c.id==identity)).scalar_one().get('workflow_task')
        if linked:
            from workbench.worker_operation import eligible
            from workbench.db import intakes
            if not c.scalar(sql_select(intakes.c.id).where(intakes.c.id==linked['intake_id'],eligible(c,intakes.c.id))):
                raise HTTPException(409,'This task is paused or its current authorization is unavailable. Checked work remains saved.')
    checked = publication.review_checked(engine, identity)
    if binding != checked["binding"]:
        raise HTTPException(409, "The checked draft changed. Reload it before submitting.")
    root = policy["root"] / str(UUID(identity))
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.resolve() != root or root.stat().st_uid != os.getuid() or root.stat().st_mode & 0o077:
        raise HTTPException(409, "The private delivery directory needs checking.")
    approval = dict(execution_id=identity, binding=binding,
                    owner_instruction_sha256=policy["owner_instruction_sha256"],
                    standing_policy_sha256=policy["digest"])
    path = root / "checked-approval.json"
    raw = json.dumps(approval, sort_keys=True).encode()
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        directory = os.open(root, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    durable(path, raw)
    publication.authorize_checked(engine, identity, root)
    return publication.view(engine, identity)


def queue_automatic(engine):
    policy = configuration(engine)
    if not policy:
        return False
    with engine.connect() as c:
        from workbench.worker_operation import eligible
        candidates = c.execute(sql_select(real_execution_requests.c.id)
            .join(real_execution_grants,real_execution_requests.c.grant_id==real_execution_grants.c.id)
            .outerjoin(real_publications, real_publications.c.execution_id == real_execution_requests.c.id)
            .where(eligible(c,real_execution_grants.c.snapshot['workflow_task']['intake_id'].as_string()),real_execution_requests.c.status == "checked",
                   real_execution_grants.c.snapshot['task_budget']['feature_task_id'].as_string().is_(None),
                   real_execution_requests.c.finished_at >= policy["automatic_since"],
                   real_publications.c.id.is_(None))
            .order_by(real_execution_requests.c.finished_at).limit(16)).scalars().all()
    for identity in candidates:
        checked = review(engine, identity)
        if checked["available"]:
            request(engine, identity, checked["binding"])
            return True
    return False


def advance(engine, stop_event=None):
    if not configuration(engine):
        return False
    with engine.connect() as c:
        from workbench.worker_operation import eligible
        identity = c.execute(sql_select(real_publications.c.execution_id)
            .join(real_execution_requests,real_execution_requests.c.id==real_publications.c.execution_id)
            .join(real_execution_grants,real_execution_grants.c.id==real_execution_requests.c.grant_id)
            .where(eligible(c,real_execution_grants.c.snapshot['workflow_task']['intake_id'].as_string()))
            .where(real_publications.c.status.in_(publication.ACTIVE),
                   real_publications.c.snapshot["checked_approval"].as_string().is_not(None))
            .order_by(real_publications.c.created_at).limit(1)).scalar()
    return send(identity, stop_event) if identity else queue_automatic(engine)


def send(identity, stop_event=None):
    """Only the system worker is an accepted peer. No model request is involved."""
    from workbench.codex_provider import connector_path

    path = connector_path()
    if path is None:
        return False
    try:
        deadline = time.monotonic() + 90
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(0.2)
            connection.connect(str(path))
            connection.sendall(json.dumps({"operation": "draft-delivery", "execution_id": identity}).encode() + b"\n")
            response = bytearray()
            while time.monotonic() < deadline and not (stop_event and stop_event.is_set()):
                try:
                    chunk = connection.recv(4097 - len(response))
                except TimeoutError:
                    continue
                response.extend(chunk)
                if not chunk or len(response) > 4096:
                    return False
                if response.endswith(b"\n"):
                    value = json.loads(response)
                    return isinstance(value, dict) and value.get("worked") is True
    except (OSError, ValueError):
        pass
    return False


def handle(connection, identity):
    """The existing connector's peer check runs before this entry point."""
    policy = configuration()
    if not policy:
        return False
    UUID(identity)

    class Stop:
        def is_set(self):
            return bool(select.select([connection], [], [], 0)[0])

    engine = engine_for(policy["database"])
    try:
        with engine.connect() as c:
            row = c.execute(sql_select(real_publications).where(
                real_publications.c.execution_id == identity)).mappings().first()
        if not row or row["status"] not in publication.ACTIVE:
            return False
        # The connector starts with env -i. Feature ancestry also needs the
        # installation's private preparation/relocation policies, just as the
        # API does. Derive fixed paths locally; socket data supplies no policy.
        with feature_policies(engine) if row["snapshot"].get("feature_artifact") else nullcontext():
            return _advance(engine, policy, row, identity, Stop())
    except (ValueError, OSError, KeyError, TypeError):
        return False
    finally:
        engine.dispose()


@contextmanager
def feature_policies(engine):
    from workbench import everyday, trusted_preparation

    stage = ROOT.parent
    inputs = trusted_preparation.owned(stage / "ops/inputs.json")
    if (not inputs.is_file() or inputs.stat().st_mode & 0o077
            or inputs.stat().st_size > 1 << 20):
        raise publication.DeliveryHeld("The installed feature source policy is unavailable.")
    research = Path(json.loads(inputs.read_text())["research_root"])
    if (not research.is_absolute() or research.resolve() != research
            or not research.is_dir() or research.stat().st_uid != os.getuid()):
        raise publication.DeliveryHeld("The installed read-only feature source is unavailable.")
    values = dict(WORKBENCH_PREPARATION_CONFIG=str(stage / "ops/preparation.json"),
                  WORKBENCH_EVERYDAY_CONFIG=str(stage / "ops/everyday.json"),
                  WORKBENCH_RESEARCH_ROOT=str(research),
                  TMPDIR=str(stage / "scratch"))
    previous = {key: os.environ.get(key) for key in values}
    try:
        os.environ.update(values)
        trusted_preparation.configuration(engine)
        everyday.configuration(engine)
        yield
    finally:
        # The connector serves one connection at a time. Do not carry these
        # delivery-only settings into its next model request.
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _advance(engine, policy, row, identity, stop):
    if "checked_approval" in row["snapshot"]:
        root = policy["root"] / identity
        worked = publication.process_one(engine, execution_id=identity, stop_event=stop,
                                         checked_root=root)
        final = publication.view(engine, identity)
        if final["published"]:
            from workbench.publication_adapter import PilotGitHub
            with engine.connect() as c:
                completed = c.execute(sql_select(real_publications).where(
                    real_publications.c.execution_id == identity)).mappings().one()
            try:
                PilotGitHub(root, completed["snapshot"], scratch_root=root).cleanup(completed["state"]["commit"])
            except (OSError, ValueError, RuntimeError):
                pass  # The delivery stays recorded and its owned copy is retained.
        return worked
    # A browser-approved run retains its separate, finite activation and budget.
    root = Path(row["snapshot"]["evidence_root"]) / "publication"
    previous = os.environ.get("WORKBENCH_PUBLICATION_ROOT")
    try:
        os.environ["WORKBENCH_PUBLICATION_ROOT"] = str(root)
        from workbench.publication_adapter import PilotGitHub
        scratch = ROOT.parent / "scratch"
        if (scratch.resolve() != scratch or not scratch.is_dir()
                or scratch.stat().st_uid != os.getuid() or scratch.stat().st_mode & 0o077):
            raise publication.DeliveryHeld("The installed publication scratch directory needs checking.")
        publisher = PilotGitHub(root, row["snapshot"], scratch_root=scratch)
        return publication.process_one(engine, publisher=publisher,
                                       execution_id=identity, stop_event=stop)
    finally:
        if previous is None:
            os.environ.pop("WORKBENCH_PUBLICATION_ROOT", None)
        else:
            os.environ["WORKBENCH_PUBLICATION_ROOT"] = previous
