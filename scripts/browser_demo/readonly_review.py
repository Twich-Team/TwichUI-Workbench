"""Serve the saved result from a closed, isolated browser demonstration."""

import json
import os
from pathlib import Path
import sqlite3

from sqlalchemy import create_engine
from starlette.responses import JSONResponse

from workbench.api import create_app as workbench_app
from workbench.trusted_preparation import owned


def create_app():
    database = owned(os.environ["WORKBENCH_DB"])
    config = owned(os.environ["WORKBENCH_PREPARATION_CONFIG"])
    policy = json.loads(config.read_text())
    stat = database.stat()
    if (policy["database"] != str(database)
            or policy["database_identity"] != [stat.st_dev, stat.st_ino]
            or any(Path(str(database) + suffix).exists() for suffix in ("-wal", "-shm", "-journal"))):
        raise ValueError("An exact closed, inode-bound demonstration store is required.")
    evidence = owned(os.environ["WORKBENCH_DEMO_EVIDENCE"])
    phase = json.loads((evidence / "ingress-phase.json").read_text())
    if phase["state"] != "read-only" or (evidence / "unsettled-session.json").exists():
        raise ValueError("Close the write gate and settle the owned session before opening review.")

    def connect():
        return sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1", uri=True,
                               check_same_thread=False)

    app = workbench_app(engine=create_engine(f"sqlite:///{database}", creator=connect))

    @app.middleware("http")
    async def read_only(request, call_next):
        if request.method not in ("GET", "HEAD"):
            return JSONResponse({"detail": "Local read-only review."}, status_code=405)
        return await call_next(request)

    return app
