from pathlib import Path
import subprocess
import tempfile
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from workbench.api import create_app
from workbench.db import engine_for
from workbench.seed import seed

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def managed_temporary_files(tmp_path, monkeypatch):
    """Keep test clones/children inside pytest's retained run, not orphaned /tmp roots.

    Explicit installation/sandbox scratch fixtures can still override this.
    The environment also covers fresh Python subprocesses, not only this process.
    """
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    monkeypatch.setenv('TMPDIR', str(tmp_path))


@pytest.fixture
def disposable_legacy_addon(tmp_path, monkeypatch):
    """Real Git/source boundaries for controlled transport and delivery tests."""
    from workbench import real_execution
    source=tmp_path/'legacy-addon'
    source.mkdir()

    def git(*args):
        return subprocess.check_output(['git','-c','core.hooksPath=/dev/null',
            '-c','user.name=NON-MODEL','-c','user.email=fixture@example.invalid',
            *args],cwd=source,text=True,timeout=10).strip()

    git('init','-q')
    for index,path in enumerate(real_execution.PATHS):
        p=source/path
        p.parent.mkdir(parents=True,exist_ok=True)
        # Keep real nonempty windows through line140 for the existing SSE
        # transport checks. No private addon code or runnable answer fixture.
        p.write_text(''.join(f'-- controlled source {index}, line {n}\n' for n in range(1,161)))
    git('add','.')
    git('commit','-qm','Controlled legacy base')
    monkeypatch.setattr(real_execution,'SOURCE',str(source))
    monkeypatch.setattr(real_execution,'BASE',git('rev-parse','HEAD'))
    return source


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
