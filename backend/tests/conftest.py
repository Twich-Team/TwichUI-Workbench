from pathlib import Path
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from workbench.api import create_app
from workbench.db import engine_for
from workbench.seed import seed

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def store(tmp_path):
    engine = engine_for(tmp_path / "fixture.sqlite3")
    config = Config(str(ROOT / "alembic.ini"))
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        connection.commit()
    seed(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def client(store):
    with TestClient(create_app(store)) as client:
        yield client


@pytest.fixture
def headers(client):
    return {"origin": "http://testserver", "x-csrf-token": client.get("/api/session").json()["csrf_token"]}
