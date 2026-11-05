"""Bounded read-only projections. No pilot modules, writes, raw logs or live review actions."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time

from fastapi import HTTPException
from sqlalchemy import select
from workbench.db import tasks, commands, backlog_items, backlog_dependencies
from workbench.service import task_view
from workbench.visual_scope import task_needs_scope_review

ROOT = Path(__file__).resolve().parents[2]
SOURCES = {'fixture', 'pilot'}


def clean(value, limit=700):
    if not isinstance(value, str):
        return ''
    value = re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', value)
    value = re.sub(r'(?:/home/|/tmp/|[A-Z]:\\)[^\s,;]+', '[private path]', value)
    value = re.sub(r'(?i)(?:sk-[a-z0-9_-]{12,}|(?:token|password|secret)\s*[:=]\s*\S+)', '[redacted]', value)
    return value[:limit]


def instant(value):
    if not isinstance(value, str):
        return None
    try:
        date = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return date.astimezone(timezone.utc).isoformat() if date.tzinfo else None
    except ValueError:
        return None


def record(id, title, **values):
    return dict(id=id, title=clean(title, 240) or 'Title not recorded', parent=None, kind='task',
                summary='', reason='', state='Unknown', attempt_outcome='Unknown', verification='Unknown',
                publication='Unknown', subsystem='Area not recorded', observed_at=None,
                updated_at=None, date_basis='No event time recorded', currently_observed=False,
                review_task_id=None, attempts=[], source_version=None, working=False, **values)


def fixture_records(engine, demo="simple"):
    rows = json.loads((ROOT/'fixtures/catalog.json').read_text())
    if demo != 'simple':
        rows += json.loads((ROOT/'fixtures/demo-catalog.json').read_text())
    # The mutable review task is read from the real application DB, not a success stub.
    with engine.connect() as connection:
        connection.exec_driver_sql('BEGIN')
        current = [task_view(connection, row) for row in connection.execute(select(tasks)).mappings()]
    for task in current:
        receipt = task['latest_command']
        state = {'held':'Needs your review', 'review_confirmed':'Review confirmed', 'changes_requested':'Changes requested'}[task['state']]
        if receipt and receipt['status'] == 'pending':
            state = 'Decision saved · Waiting to apply'
        elif receipt and receipt['status'] == 'stale':
            state = 'Needs another look'
        elif receipt and receipt['status'] == 'failed':
            state = 'Needs investigation'
        rows.append(dict(id=task['id'], title=task['title'], parent='guide-review', kind='task',
                         summary=task['summary'], state=state, attempt_outcome=task['attempt_outcome'],
                         verification=task['verification'], publication=task['publication'],
                         subsystem=task['subsystem'], review_task_id=task['id'], attempts=[],
                         source_version=task['version']))
    # No fixture task means the existing empty-database acceptance remains empty.
    if not current:
        return []
    if demo == 'empty':
        return []
    if demo == 'hierarchy':
        rows = [row for row in rows if row.get('parent') or row['id'] in {'demo-00', 'guide-request'}]
    if demo == 'reviews':
        rows = [row for row in rows if row.get('review_task_id') or row['id'] in {'guide-request', 'guide-review', 'fixture-inspection'}]
    return [dict(record(row['id'], row['title']), **{k:v for k,v in row.items() if k not in {'id','title'}}) for row in rows]


def pilot_records(path):
    path = Path(path)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError('Projection unavailable')
    deadline = time.monotonic() + .3
    rows = []
    size = 0
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=.15)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        db.execute('BEGIN')
        if db.execute('PRAGMA user_version').fetchone()[0] != 1:
            raise ValueError('Unsupported projection schema')
        query = '''SELECT substr(i.id,1,161) AS id,substr(i.kind,1,31) AS kind,
                   substr(i.parent,1,161) AS parent,substr(i.owner_title,1,241) AS owner_title,
                   substr(i.owner_objective,1,4001) AS owner_objective,
                   substr(i.owner_updated_at,1,81) AS owner_updated_at,
                   substr(i.source_version,1,101) AS source_version,
                   substr(i.projection,1,131073) AS projection,
                   substr(o.observed_at,1,81) AS observed_at FROM items i LEFT JOIN observations o ON o.item=i.id
                   ORDER BY i.id LIMIT 501'''
        for raw in db.execute(query):
            if len(rows) == 500:
                return rows, True
            size += len(raw['projection'])
            if size > 2*1024*1024 or len(raw['projection']) > 131072:
                raise ValueError('Projection exceeds read budget')
            projection = json.loads(raw['projection'])
            if not isinstance(projection, dict):
                raise ValueError('Invalid projection')
            key = raw['id']
            if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9:_.~-]{1,160}', key):
                raise ValueError('Invalid record identity')
            row = record(key, raw['owner_title'] or projection.get('title'))
            observed = instant(raw['observed_at'])
            current = bool(observed and 0 <= (datetime.now(timezone.utc)-datetime.fromisoformat(observed)).total_seconds() <= 120)
            row.update(kind=clean(raw['kind'], 30), parent=raw['parent'],
                       working=projection.get('in_progress') is True,
                       summary=clean(raw['owner_objective'] or projection.get('objective') or projection.get('reason')),
                       state=clean(projection.get('owner_category') or projection.get('category') or projection.get('state'), 100) or 'Unknown',
                       reason=clean(projection.get('reason')),
                       subsystem=clean(projection.get('subsystem'), 120) or 'Area not recorded',
                       attempt_outcome=clean(projection.get('outcome'), 80) or 'Unknown',
                       verification=clean(projection.get('verification'), 80) or 'Unknown',
                       publication=clean(projection.get('publication'), 80) or 'Unknown',
                       observed_at=observed, currently_observed=current,
                       updated_at=instant(raw['owner_updated_at'] or projection.get('updated_at')),
                       date_basis='Recorded projection update; not an attempt or publication time',
                       source_version=clean(raw['source_version'], 100) or None)
            # Only explicit recorded attempts; no raw bodies, paths or artifact URLs.
            attempts = projection.get('attempts', [])
            if isinstance(attempts, list):
                row['attempts'] = [dict(outcome=clean(a.get('outcome'), 80) or 'Unknown',
                                        model=clean(a.get('model'), 100) or 'Not recorded')
                                   for a in attempts[-5:] if isinstance(a, dict)]
            rows.append(row)
    return rows, False


def snapshot(engine, source, pilot_path, demo="simple"):
    if source not in SOURCES:
        raise HTTPException(422, 'Choose a supported workspace source.')
    if source == 'fixture':
        rows = fixture_records(engine, demo)
        freshness = dict(state='fixture', observed_at=None,
                         message='Sanitized examples and your real local review receipts. No live implementation is running here.')
        partial = False
    else:
        try:
            if not pilot_path:
                raise ValueError('Adapter not configured')
            rows, partial = pilot_records(pilot_path)
            dates = [r['observed_at'] for r in rows if r['observed_at']]
            observed = max(dates) if dates else None
            fresh = any(r['currently_observed'] for r in rows)
            freshness = dict(state='fresh' if fresh else 'stale', observed_at=observed,
                             message='Read-only projection. Observation freshness does not confirm live inference.' if fresh else
                             'Last-known records only. Current execution and eligibility cannot be confirmed.')
        except (OSError, ValueError, TypeError, sqlite3.Error):
            rows, partial = [], False
            freshness = dict(state='unavailable', observed_at=None,
                             message='Maintenance observations are unavailable. No live state is inferred; fixture reviews remain usable.')
    if source == 'fixture':
        with engine.connect() as connection:
            connection.exec_driver_sql('BEGIN')
            for index, item in enumerate(connection.execute(select(backlog_items).order_by(backlog_items.c.id).limit(501)).mappings()):
                if index == 500:
                    partial = True
                    break
                row = record(item['id'], item['title'])
                scope_review = task_needs_scope_review(connection, item)
                row.update(scope_review_required=scope_review, parent=item['parent'], kind=item['kind'], summary=clean(item['purpose']),
                    state='Scope review required · Execution held' if scope_review else 'Waiting for execution authorization', subsystem='Intake planning',
                    intake_id=item['intake_id'], proposal_id=item['proposal_id'],
                    planning_model=item['model'], planning_acceptance=item['acceptance'],
                    dependencies=list(connection.execute(select(backlog_dependencies.c.requires_id).where(backlog_dependencies.c.item_id == item['id'])).scalars()),
                    publication='Not published', updated_at=item['created_at'],
                    date_basis='Planning record creation; no implementation attempt',
                    source_version=item['proposal_id'])
                rows.append(row)
    # Filter dangling/cyclic parent links for safe browsing without changing the source.
    identities = {r['id'] for r in rows}
    by_id = {r['id']:r for r in rows}
    for row in rows:
        seen = {row['id']}
        parent = row['parent']
        while parent:
            if parent not in identities or parent in seen:
                row['parent'] = None
                break
            seen.add(parent)
            parent = by_id[parent]['parent']
    if source == 'fixture' and any(r.get('intake_id') for r in rows):
        freshness['message'] = 'Local intake planning records alongside simulated examples. Planning never grants execution permission.'
    version = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    return dict(source=source, freshness=freshness, partial=partial, version=version, local_records=any(r.get('intake_id') for r in rows)), rows


def browse(engine, source, pilot_path, parent, q, page, view="open", demo="simple",
           types=(), states=(), areas=(), sort="title", focus=None):
    meta, rows = snapshot(engine, source, pilot_path, demo)
    by_id = {r['id']: r for r in rows}
    filtered = bool(q or types or states or areas or view not in {'open','all'})
    selected = rows if filtered else [r for r in rows if r['id']==focus] if focus and parent is None else [r for r in rows if r['parent'] == parent]
    def matches(r):
        current = source == 'fixture' or r['currently_observed']
        views = {'all': True, 'open': r['kind'] != 'inspection' and r['state'] != 'Finished',
                 'needs_you': current and r['state'] in {'Needs your review','Needs you','Needs another look'},
                 'working': current and r['working'],
                 'waiting': current and r['state'].casefold().startswith('waiting'),
                 'finished': r['state'] == 'Finished'}
        return (views.get(view, False) and q.casefold() in (r['title']+' '+r['summary']).casefold()
                and (not types or r['kind'] in types) and (not states or r['state'] in states)
                and (not areas or r['subsystem'] in areas))
    selected = [r for r in selected if matches(r)]
    selected.sort(key=lambda r: (r['title'].casefold(), r['id']))
    if sort == 'updated':
        selected.sort(key=lambda r: r['updated_at'] or '', reverse=True)
    chosen = selected[(page-1)*12:page*12]
    match_ids = {r['id'] for r in chosen}
    items = []
    seen = set()
    def add(row):
        if row['id'] in seen:
            return
        if filtered and row['parent']:
            add(by_id[row['parent']])
        seen.add(row['id'])
        items.append(dict(row, children=sum(c['parent']==row['id'] for c in rows),
                          context=filtered and row['id'] not in match_ids))
    for row in chosen:
        add(row)
    return {**meta, 'items':items, 'total':len(selected), 'page':page, 'page_size':12,
            'parent':parent, 'filtered':filtered,
            'facets':{key: sorted({r[field] for r in rows}) for key,field in
                      [('type','kind'),('state','state'),('area','subsystem')]}}


def history(engine, source, pilot_path, page, demo="simple"):
    meta, rows = snapshot(engine, source, pilot_path, demo)
    events = []
    if source == 'fixture':
        with engine.connect() as connection:
            for receipt in connection.execute(select(commands).order_by(commands.c.created_at.desc()).limit(100)).mappings():
                events.append(dict(id=receipt['id']+':saved', task_id=receipt['task_id'], title='Review decision saved',
                                   at=receipt['created_at'], date_basis='Committed command submission time',
                                   outcome='pending', message='Saved is separate from application.'))
                if receipt['finished_at']:
                    events.append(dict(id=receipt['id']+':finished', task_id=receipt['task_id'], title='Review '+receipt['status'],
                                       at=receipt['finished_at'], date_basis='Worker outcome time',
                                       outcome=receipt['status'], message=receipt['message']))
    for row in rows:
        if row['updated_at']:
            events.append(dict(id=row['id']+':projection', task_id=row['id'], title=row['title'], at=row['updated_at'],
                               date_basis=row['date_basis'], outcome=row['attempt_outcome'],
                               message='Recorded task state: '+row['state']+'. Verification: '+row['verification']+'. Publication: '+row['publication']+'.'))
    events.sort(key=lambda e: (e['at'],e['id']), reverse=True)
    return {**meta, 'items':events[(page-1)*20:page*20], 'total':len(events), 'page':page, 'page_size':20,
            'message':'Up to 100 recent review commands and the latest dated update per record. Only timezone-aware recorded times are listed; older events and missing times are not reconstructed.'}
