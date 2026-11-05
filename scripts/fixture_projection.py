"""Sanitized SQLite schema-v1 projection for browser acceptance only."""
from datetime import datetime, timezone, timedelta
import json
import sqlite3


def create(path):
    with sqlite3.connect(path) as db:
        db.executescript('''PRAGMA user_version=1;
            CREATE TABLE items(id TEXT PRIMARY KEY,kind TEXT,parent TEXT,owner_title TEXT,
                owner_objective TEXT,owner_updated_at TEXT,source_version TEXT,projection TEXT);
            CREATE TABLE observations(item TEXT PRIMARY KEY,observed_at TEXT);''')
        observed=(datetime.now(timezone.utc)-timedelta(seconds=600)).isoformat()
        for i in range(29):
            key=f'pilot-{i:02}'
            body=dict(title='Recorded guide improvement' if i==0 else f'Recorded guide check {i:02}',
                      objective='Sanitized maintenance observation. Read-only evidence, not permission.',
                      category='Needs you',in_progress=True,subsystem='Guides',outcome='accepted',
                      updated_at='2026-11-01T15:30:00+00:00',
                      attempts=[dict(outcome='accepted',model='Not recorded')])
            db.execute('INSERT INTO items VALUES(?,?,?,?,?,?,?,?)',
                       (key,'request' if i==0 else 'task',None if i==0 else 'pilot-00',None,None,None,'fixture-v1',json.dumps(body)))
            db.execute('INSERT INTO observations VALUES(?,?)',(key,observed))
