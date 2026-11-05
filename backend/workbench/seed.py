import json
from pathlib import Path
from sqlalchemy import select
from workbench.db import engine_for, tasks, write_transaction


def seed(engine=None):
    engine = engine or engine_for()
    rows = json.loads((Path(__file__).resolve().parents[2] / "fixtures/tasks.json").read_text())
    with write_transaction(engine) as connection:
        for row in rows:
            if not connection.execute(select(tasks.c.id).where(tasks.c.id == row["id"])).first():
                connection.execute(tasks.insert().values(**row))


if __name__ == "__main__":
    seed()
