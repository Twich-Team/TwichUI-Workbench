"""Observed Workbench activity, independent of provider allowance and billing.

All records in the chosen UTC window are counted, not the last100 detail rows.
Reported token subtotals remain separate from records with unknown accounting.
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Literal
from pydantic import BaseModel
from sqlalchemy import select
from workbench.db import model_attempts, planning_jobs
from workbench.task_identity import links


class UsageBucket(BaseModel):
    backend: str
    model: str
    operations: int
    completed: int
    running: int
    other: int
    reported_tokens: int | None
    unreported_operations: int
    partial_operations: int


class UsageDay(BaseModel):
    day: str
    backend: str
    model: str
    operations: int
    reported_tokens: int | None
    unreported_operations: int


class UsageFallback(BaseModel):
    intake_id: str
    job_id: str
    created_at: str
    from_provider: str
    to_provider: str
    reason: str


class UsageOverview(BaseModel):
    days: int
    start: str
    observed_at: str
    operations: int
    tasks: int
    reported_tokens: int | None
    reported_operations: int
    unreported_operations: int
    partial_operations: int
    undated_operations: int
    session_authored_operations: int
    providers: list[UsageBucket]
    timeline: list[UsageDay]
    fallbacks: list[UsageFallback]
    subscription_allowance: Literal['unavailable'] = 'unavailable'
    api_spending: Literal['unavailable'] = 'unavailable'
    message: str


def stamp(value):
    try:
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, TypeError, AttributeError):
        return None


def token_value(usage):
    if not isinstance(usage,dict) or usage.get('units')!='tokens':
        return None, False
    values=usage.get('values')
    total=values.get('total_tokens') if isinstance(values,dict) else None
    if type(total) is not int or total<0:
        return None, False
    status=usage.get('status')
    if status not in {'complete','partial','not_started'}:
        return None, False  # Unknown accounting cannot become complete through a value.
    if status=='not_started' and (total!=0 or usage.get('requests_observed')!=0):
        return None, False
    return total,status=='partial'


def read(engine, days=7, *, instant=None):
    if days not in {7,30,90}:
        raise ValueError('Choose7,30or90days.')
    end=instant or datetime.now(timezone.utc)
    start=(end-timedelta(days=days-1)).replace(hour=0,minute=0,second=0,microsecond=0)
    with engine.connect() as c:
        c.exec_driver_sql('BEGIN')
        # Preserve source attribution: demo/imported/unknown are never observed totals.
        rows=[dict(r) for r in c.execute(select(model_attempts).where(model_attempts.c.source=='workbench')).mappings()]
        jobs=[dict(r) for r in c.execute(select(planning_jobs.c.id,planning_jobs.c.intake_id,
              planning_jobs.c.created_at,planning_jobs.c.payload)).mappings()]
        owners={r['intake_id']:r['canonical_id'] for r in links(c)}
    selected=[]
    undated=0
    authored=0
    for row in rows:
        at=stamp(row['started_at'])
        if at is None or at>end:
            undated+=1
        elif at>=start:
            if row['backend']=='current-session':
                authored+=1
            else:
                selected.append((row,at))
    buckets=defaultdict(list)
    daily=defaultdict(list)
    values=[]
    partials=0
    for row,at in selected:
        tokens,partial=token_value(row['usage'])
        values.append(tokens)
        partials+=partial
        backend,model=row['backend'] or 'unknown',row['actual_model'] or 'Not reported'
        buckets[backend,model].append((row,tokens,partial))
        daily[at.date().isoformat(),backend,model].append(tokens)
    def subtotal(values):
        known=[v for v in values if v is not None]
        return sum(known) if known else None
    provider_rows=[]
    for (backend,model),items in sorted(buckets.items()):
        completed=sum(r['outcome'] in {'completed','checked'} for r,_,_ in items)
        running=sum(r['outcome']=='running' for r,_,_ in items)
        provider_rows.append(UsageBucket(backend=backend,model=model,operations=len(items),
            completed=completed,running=running,other=len(items)-completed-running,
            reported_tokens=subtotal([v for _,v,_ in items]),
            unreported_operations=sum(v is None for _,v,_ in items),
            partial_operations=sum(p for _,_,p in items)))
    fallbacks=[]
    for job in jobs:
        at=stamp(job['created_at'])
        switch=job['payload'].get('provider_switch',{})
        before,after=switch.get('from_provider'),switch.get('to_provider')
        if (at and start<=at<=end and before in {'local','codex'} and after in {'local','codex'} and before!=after
                and switch.get(before+'_generation_started') is False and isinstance(switch.get('reason'),str)):
            fallbacks.append(UsageFallback(intake_id=job['intake_id'],job_id=job['id'],created_at=job['created_at'],
                from_provider=before,to_provider=after,reason=switch['reason']))
    return UsageOverview(days=days,start=start.isoformat(),observed_at=end.isoformat(),
        operations=len(selected),tasks=len({owners.get(r['task_id'],r['task_id']) for r,_ in selected}),
        reported_tokens=subtotal(values),reported_operations=sum(v is not None for v in values),
        unreported_operations=sum(v is None for v in values),partial_operations=partials,
        undated_operations=undated,session_authored_operations=authored,providers=provider_rows,
        timeline=[UsageDay(day=day,backend=backend,model=model,operations=len(tokens),reported_tokens=subtotal(tokens),
            unreported_operations=sum(v is None for v in tokens)) for (day,backend,model),tokens in sorted(daily.items())],
        fallbacks=sorted(fallbacks,key=lambda f:(f.created_at,f.job_id),reverse=True),
        message='Observed Workbench operations only, grouped by their recorded start date in UTC. Each operation may contain several provider requests. Completed model output is not a verified task result. Token totals include reported partial usage and exclude unreported amounts; missing days are not zero usage. Model attribution uses reported identity, never the requested model as a substitute. Historical retries are grouped when evidenced ownership links are installed. Separately session-authored changes are excluded from model-operation totals. Undated/future records are excluded from this dated window. Subscription allowance, other clients and separately billed API spending are unavailable; tokens do not imply credits or money.')
