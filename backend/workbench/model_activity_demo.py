"""Synthetic activity through the real accounting reducer in a disposable database.

Never writes the application store, invokes a provider, or reconstructs history.
"""
from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryDirectory
from workbench.db import metadata, engine_for, intakes, planning_jobs, model_attempts
from workbench.model_accounting import read


@lru_cache(maxsize=1)
def activity():
    with TemporaryDirectory(prefix='workbench-model-activity-demo-') as temporary:
        engine = engine_for(Path(temporary)/'demo.sqlite3')
        try:
            metadata.create_all(engine)
            with engine.begin() as c:
                for task, attempts in [('planning', 3), ('summary', 1), ('implementation', 3)]:
                    identity = 'synthetic-'+task
                    c.execute(intakes.insert().values(id=identity, payload={'title':'Synthetic '+task},
                        status='completed', revision=1, created_at='2026-01-01T09:00:00+00:00'))
                    c.execute(planning_jobs.insert().values(id=identity, intake_id=identity, kind='planning',
                        revision=1, status='completed', attempts=attempts, payload={}, message='Synthetic accounting fixture',
                        created_at='2026-01-01T09:00:00+00:00'))
                records = [
                    ('planning', 'local', 'demo-local', 'planning', 'failed', '01', 'Outage fixture; explicitly permitted fallback follows.'),
                    ('planning', 'local', 'demo-local', 'planning', 'failed', '02', 'Second recorded local attempt on the same synthetic task.'),
                    ('planning', 'codex', 'demo-codex', 'planning', 'completed', '03', 'Synthetic escalation from local; same task identity, not another completed task.'),
                    ('summary', 'claude', 'demo-claude', 'summary', 'completed', '04', 'Synthetic summary attempt; no provider inference.'),
                    ('implementation', None, None, 'other', 'interrupted_unknown', '05', 'Synthetic historical attribution missing; actual model and end time remain unknown.'),
                    ('implementation', 'local', 'demo-local', 'implementation', 'failed', '06', 'Synthetic failed implementation attempt; no addon work occurred.'),
                ]
                for task, backend, model, purpose, outcome, day, reason in records:
                    stamp = f'2026-01-{day}T09:00:00+00:00'
                    c.execute(model_attempts.insert().values(id='demo-attempt-'+day, job_id='synthetic-'+task,
                        task_id='synthetic-'+task, source='demo', backend=backend, purpose=purpose,
                        requested_model=model, actual_model=model, policy_revision=None, routing_reason=reason,
                        started_at=stamp, finished_at=None if outcome=='interrupted_unknown' else f'2026-01-{day}T09:01:00+00:00',
                        outcome=outcome, usage={'source':'Synthetic local response fixture', 'units':'tokens', 'observed_at':stamp,
                        'values':{'total_tokens':120}} if day=='01' else None))
            result = read(engine, recorded_source='demo')
            result.message = ('Synthetic disposable activity, reduced by the same accounting API as observed records. '
                'Three distinct tasks, six attempts; local-to-Codex escalation retains one task identity. '
                'Two recorded job completions are not independently verified task completions. One legacy attempt has no reconstructed event. '
                'Unknown attribution stays unknown. All records and token values are simulated and excluded from observed provider totals. No quota or real usage is shown.')
            return result
        finally:
            engine.dispose()
