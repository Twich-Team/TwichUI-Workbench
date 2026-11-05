from alembic import context
from workbench.db import database_path, engine_for, metadata

config = context.config
# Tests may provide an existing connection; normal CLI uses the explicit local path.
connection = config.attributes.get("connection")
if connection is not None:
    context.configure(connection=connection, target_metadata=metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    database_path().parent.mkdir(parents=True, exist_ok=True)
    with engine_for().connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        context.configure(connection=connection, target_metadata=metadata, render_as_batch=True)
        context.run_migrations()
        connection.commit()
