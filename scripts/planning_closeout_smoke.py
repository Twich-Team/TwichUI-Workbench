"""The single remaining WB-15 real smoke; never called by application tests.

Consumes the prior closeout's remaining approach exactly once. Preserves private
request/response and a disposable DB for review; never retries or changes services.
"""

import hashlib
import json
import os
from pathlib import Path
import signal
import threading
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from workbench import planning_provider, planning_worker
from workbench.api import create_app
from workbench.db import engine_for
from workbench.planning_transport import LocalHTTP

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / ".local/m3-closeout"


def main():
    if not os.environ.get("WORKBENCH_LOCAL_KEY") or not os.environ.get("WORKBENCH_RESEARCH_ROOT"):
        raise SystemExit("Explicit existing local key and authorized source checkout are required.")
    os.umask(0o077)
    PRIVATE.mkdir(mode=0o700, exist_ok=True)
    # Durable guard: do not reset this file or launch another attempt after a failure.
    with (PRIVATE / "wb15-second-approach.json").open("x") as handle:
        json.dump(
            {"total_approach": 2, "prior_real_attempts": 1, "automatic_retries": False}, handle
        )
    path = PRIVATE / "observed.sqlite3"
    if path.exists():
        raise SystemExit("Refusing to overwrite retained observed evidence.")
    engine = engine_for(path)
    with engine.connect() as connection:
        config = Config(str(ROOT / "alembic.ini"))
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        connection.commit()
    stopped = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopped.set())

    class RecordedHTTP(LocalHTTP):
        def read(self, request, limit, timeout=None):
            if request.data:
                (PRIVATE / "exact-request.json").write_bytes(
                    request.data
                )  # No HTTP headers/credential.
            raw = super().read(request, limit, timeout)
            if request.data:
                (PRIVATE / "exact-response.json").write_bytes(raw)
            return raw

    planning_provider.LocalHTTP = RecordedHTTP
    with TestClient(create_app(engine)) as client:
        headers = {
            "origin": "http://testserver",
            "x-csrf-token": client.get("/api/session").json()["csrf_token"],
        }
        body = dict(
            request_id=str(uuid4()),
            kind="feature",
            title="Nonvisual guide navigation regression coverage",
            description="Research whether the supplied guide navigation test excerpts cover selected-step persistence. Propose a bounded research stage; do not implement or execute addon changes. Do not claim to have searched beyond the supplied excerpts.",
        )
        response = client.post("/api/intake", headers=headers, json=body)
        response.raise_for_status()
        saved = response.json()
        assert saved["status"] == "queued" and not saved["proposals"]
        planning_worker.process_one(engine, planning_provider.LocalProvider(stopped))
        result = client.get("/api/intake/" + saved["id"]).json()
        assert (
            result["original"]["title"] == body["title"]
            and not result["tasks"]
            and not result["decisions"]
        )
        output = dict(
            total_approaches=2,
            request_persisted_before_processing=True,
            status=result["status"],
            intake_id=result["id"],
            jobs=result["jobs"],
            evidence=[
                {k: e[k] for k in ("id", "label", "sha256", "kind", "source_revision")}
                for e in result["evidence"]
            ],
            proposals=[
                dict(
                    id=p["id"],
                    revision=p["revision"],
                    model=p["model"],
                    basis=p["basis"],
                    delivery=p["content"]["delivery"],
                    visual=p["content"]["visual"],
                )
                for p in result["proposals"]
            ],
            execution_permission_created=False,
            publication_permission_created=False,
            private_db="observed.sqlite3",
            note="One real feature-planning invocation under approach two. No automatic retry; no service/model/policy changes.",
        )
        for name in ("exact-request.json", "exact-response.json"):
            if (PRIVATE / name).exists():
                output[name + "_sha256"] = hashlib.sha256((PRIVATE / name).read_bytes()).hexdigest()
        (ROOT / "artifacts/m3-closeout/observed-smoke.json").write_text(
            json.dumps(output, indent=2)
        )
        print(json.dumps(output, indent=2))
    engine.dispose()
    return 0 if result["proposals"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
