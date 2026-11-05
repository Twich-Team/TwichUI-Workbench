"""Check migration/model agreement in a disposable database, never the dev store."""
from pathlib import Path
from tempfile import TemporaryDirectory
from alembic import command
from alembic.config import Config
from workbench.db import engine_for

with TemporaryDirectory(prefix="workbench-schema-") as directory:
    engine = engine_for(Path(directory) / "check.sqlite3")
    with engine.connect() as connection:
        config = Config("alembic.ini")
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        command.check(config)
    engine.dispose()
