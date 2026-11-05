from fastapi import HTTPException
from sqlalchemy import select
from workbench.db import display_settings, write_transaction

DEFAULTS = dict(revision=0, resource_hours=1, refresh_seconds=30)


def read(engine):
    with engine.connect() as connection:
        row = connection.execute(select(display_settings)).mappings().first()
        return {key: row[key] for key in DEFAULTS} if row else DEFAULTS.copy()


def save(engine, body):
    with write_transaction(engine) as connection:
        row = connection.execute(select(display_settings)).mappings().first()
        revision = row['revision'] if row else 0
        values = body.model_dump()
        # Safe reconciliation of a lost response; no duplicate revision on retry.
        if row and all(row[key] == values[key] for key in ('resource_hours', 'refresh_seconds')) and values['revision'] == revision-1:
            return {key: row[key] for key in DEFAULTS}
        if values['revision'] != revision:
            raise HTTPException(409, 'Display settings changed. Your edits are kept. Reload the saved values before saving again.')
        values['revision'] += 1
        if row:
            connection.execute(display_settings.update().where(display_settings.c.id == 1).values(**values))
        else:
            connection.execute(display_settings.insert().values(id=1, **values))
        return values
