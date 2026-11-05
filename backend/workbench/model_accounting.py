"""Minimal job-lease accounting. No pilot imports, history reconstruction or quota estimates."""
from typing import Literal
from sqlalchemy import select, func
from workbench.db import model_attempts, planning_jobs
from workbench.schemas import StrictModel


class AttemptView(StrictModel):
    id: str
    job_id: str | None
    execution_id: str | None = None
    task_id: str
    source: str
    backend: str | None
    purpose: str
    requested_model: str | None
    actual_model: str | None
    requested_effort: str | None
    actual_effort: str | None
    policy_revision: int | None
    routing_reason: str
    started_at: str
    finished_at: str | None
    outcome: str
    verification: str | None
    usage: dict | None
    account_ref: str | None


class ActivityCount(StrictModel):
    backend: str
    model: str
    purpose: str
    outcome: str
    tasks: int
    attempts: int
    verified: int


class ProviderCount(StrictModel):
    backend: str
    tasks: int
    attempts: int


class OutcomeCount(StrictModel):
    day: str
    outcome: str
    attempts: int


class UsagePoint(StrictModel):
    backend: str
    model: str
    observed_at: str
    source: str
    units: str
    tokens: int


class Accounting(StrictModel):
    source: Literal['observed', 'demo'] = 'observed'
    tasks: int
    attempts: int
    verified: int
    demo_attempts: int
    imported_attempts: int
    unattributed_attempts: int
    legacy_job_attempts: int
    rows: list[AttemptView]
    distribution: list[ActivityCount]
    message: str
    providers: list[ProviderCount]
    outcomes: list[OutcomeCount]
    usage_points: list[UsagePoint]


def read(engine, recorded_source="workbench"):
    assert recorded_source in {"workbench", "demo"}
    with engine.connect() as connection:
        # SQL bounds aggregate output; detailed recent records capped independently.
        aggregate = connection.execute(select(model_attempts.c.backend, model_attempts.c.actual_model,
            model_attempts.c.purpose, model_attempts.c.outcome,
            func.count().label('attempts'), func.count(func.distinct(model_attempts.c.task_id)).label('tasks'))
            .where(model_attempts.c.source == recorded_source)
            .group_by(model_attempts.c.backend, model_attempts.c.actual_model, model_attempts.c.purpose, model_attempts.c.outcome)
            .limit(500)).mappings().all()
        owned = model_attempts.c.source == recorded_source
        total = connection.execute(select(func.count(), func.count(func.distinct(model_attempts.c.task_id))).where(owned)).one()
        providers = connection.execute(select(model_attempts.c.backend, func.count().label('attempts'),
            func.count(func.distinct(model_attempts.c.task_id)).label('tasks')).where(owned).group_by(model_attempts.c.backend)).mappings().all()
        outcomes = connection.execute(select(func.substr(model_attempts.c.finished_at, 1, 10).label('day'),
            model_attempts.c.outcome, func.count().label('attempts')).where(owned, model_attempts.c.finished_at.is_not(None))
            .group_by('day', model_attempts.c.outcome).order_by(func.substr(model_attempts.c.finished_at, 1, 10).desc()).limit(120)).mappings().all()
        counts = dict(connection.execute(select(model_attempts.c.source, func.count()).group_by(model_attempts.c.source)).all())
        rows_query = select(model_attempts)
        if recorded_source == 'demo':
            rows_query = rows_query.where(owned)
        rows = connection.execute(rows_query.order_by(model_attempts.c.started_at.desc(), model_attempts.c.id).limit(100)).mappings().all()
        # Existing job counters are not unique, timed provider events; never backfill.
        recorded = select(model_attempts.c.job_id, func.count().label('recorded')).group_by(model_attempts.c.job_id).subquery()
        legacy = connection.execute(select(func.coalesce(func.sum(func.max(
            planning_jobs.c.attempts - func.coalesce(recorded.c.recorded, 0), 0)), 0)).select_from(
                planning_jobs.outerjoin(recorded, planning_jobs.c.id == recorded.c.job_id))).scalar_one()
    usage_points = []
    for row in reversed(rows):
        usage = row['usage']
        if row['source'] != recorded_source or not isinstance(usage, dict):
            continue
        values = usage.get('values')
        value = values.get('total_tokens') if isinstance(values, dict) else None
        if type(value) is int and value >= 0 and usage.get('observed_at') and usage.get('units') == 'tokens':
            usage_points.append(UsagePoint(backend=row['backend'] or 'unknown', model=row['actual_model'] or 'unknown',
                observed_at=usage['observed_at'], source=usage['source'], units='tokens', tokens=value))
    return Accounting(source='demo' if recorded_source == 'demo' else 'observed', providers=[ProviderCount(backend=r['backend'] or 'unknown', tasks=r['tasks'], attempts=r['attempts']) for r in providers],
        outcomes=[OutcomeCount(**r) for r in outcomes], usage_points=usage_points, tasks=total[1], attempts=total[0], verified=0,
        demo_attempts=counts.get('demo', 0), imported_attempts=counts.get('pilot', 0),
        unattributed_attempts=counts.get('unknown', 0), legacy_job_attempts=legacy,
        rows=[AttemptView.model_validate(dict(r)) for r in rows],
        distribution=[ActivityCount(backend=r['backend'] or 'Unknown backend', model=r['actual_model'] or 'Unknown model',
            purpose=r['purpose'], outcome=r['outcome'], tasks=r['tasks'], attempts=r['attempts'], verified=0) for r in aggregate],
        message='Planning job attempts are recorded per lease from Milestone 4 onward. One task may have several attempts. Structured output is not independent verification. Demo and imported pilot activity are excluded from Workbench totals. No provider usage is inferred from job counts. Historical attribution/timestamps stay unknown. Last 100 detailed records; distribution capped at 500 groups; dated outcomes capped at 120 day/outcome groups (UTC). No verified-outcome source is integrated.')
