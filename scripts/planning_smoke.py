"""One explicitly requested local planning smoke, isolated DB, no maintenance restart.

Never invoked by ordinary tests. Existing local key and checkout paths are explicit
operator environment configuration, never copied into retained output.
"""
import json
import os
from pathlib import Path
import tempfile
from uuid import uuid4

from alembic import command
from alembic.config import Config
from workbench.db import engine_for
from workbench import intake, planning_worker
from workbench.schemas import IntakeInput
from workbench.planning_provider import LocalProvider


def main():
    if not os.environ.get('WORKBENCH_LOCAL_KEY'):
        raise SystemExit('Set WORKBENCH_LOCAL_KEY explicitly; no service configuration is changed.')
    with tempfile.TemporaryDirectory(prefix='workbench-planning-smoke-') as directory:
        engine = engine_for(Path(directory)/'smoke.sqlite3')
        with engine.connect() as connection:
            config = Config('alembic.ini')
            config.attributes['connection'] = connection
            command.upgrade(config, 'head')
            connection.commit()
        body = IntakeInput(request_id=uuid4(), kind='feature', title='Nonvisual guide navigation regression coverage',
            description='Research whether existing guide navigation tests cover selected-step persistence. Propose a bounded research stage; do not implement or execute addon changes.')
        saved = intake.submit(engine, body)
        planning_worker.process_one(engine, LocalProvider())
        result = intake.view(engine, saved['id'])
        output = dict(status=result['status'], job=result['jobs'][0], evidence=[dict(id=e['id'],label=e['label'],sha256=e['sha256']) for e in result['evidence']],
            proposals=[dict(revision=p['revision'],model=p['model'],basis=p['basis'],delivery=p['content']['delivery'],visual=p['content']['visual']) for p in result['proposals']],
            note='One local-only smoke. Evidence was read, not executed; no verified addon root cause or game rendering claim.')
        Path('artifacts/m3/local-smoke.json').write_text(json.dumps(output,indent=2))
        print(json.dumps(output,indent=2))
        engine.dispose()


if __name__ == '__main__':
    main()
