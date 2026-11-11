"""Copy a stopped Workbench store without changing its database or opening an app."""
import argparse
from contextlib import closing
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3

from workbench.trusted_preparation import owned


def fingerprint(connection):
    if (connection.execute("PRAGMA integrity_check").fetchall() != [('ok',)]
            or connection.execute("PRAGMA foreign_key_check").fetchone()
            or connection.execute("SELECT version_num FROM alembic_version").fetchall() not in ([('0012',)], [('0013',)], [('0014',)])):
        raise ValueError('Store integrity, foreign-key or schema check failed.')
    tables = connection.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
    counts = {name: connection.execute('SELECT COUNT(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0]
              for name, in tables}
    digest = sha256()
    for statement in connection.iterdump():
        digest.update(statement.encode())
        digest.update(b'\n')
    return dict(schema=connection.execute('SELECT version_num FROM alembic_version').fetchone()[0], tables=len(tables), rows=sum(counts.values()), logical_sha256=digest.hexdigest())


def require_settled(connection):
    for table, active in {
        'commands': ('pending',),
        'proposal_decisions': ('pending',),
        'planning_jobs': ('queued', 'running'),
        'task_sessions': ('pending', 'active', 'waiting_recovery'),
        'real_execution_requests': ('pending', 'running', 'checking', 'cancel_requested'),
        'real_publications': ('pending', 'preparing', 'pushing', 'creating', 'reconciling'),
        'pr_preparations': ('pending', 'preparing', 'reconciling'),
    }.items():
        placeholders = ','.join('?' for _ in active)
        if connection.execute(f'SELECT 1 FROM {table} WHERE status IN ({placeholders}) LIMIT 1', active).fetchone():
            raise ValueError('The source store still has unsettled work.')
    if connection.execute("""SELECT 1 FROM real_execution_grants AS g
        LEFT JOIN real_execution_requests AS r ON r.grant_id=g.id
        WHERE r.id IS NULL AND g.revoked_at IS NULL AND julianday(g.expires_at)>julianday('now')
        LIMIT 1""").fetchone():
        raise ValueError('An unused execution permission is still current.')


def copy_store(source, destination):
    source = owned(source)
    if not source.is_file() or source.stat().st_mode & 0o077:
        raise ValueError('An existing private SQLite file is required.')
    destination = destination.absolute()
    parent = owned(destination.parent)
    if parent.stat().st_mode & 0o077:
        raise ValueError('The destination must be a private owned directory.')
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=2)) as connection:
        connection.execute('BEGIN')
        require_settled(connection)
        before = fingerprint(connection)
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        with closing(sqlite3.connect(destination, timeout=2)) as restored:
            connection.backup(restored)
    with closing(sqlite3.connect(destination.as_uri() + '?mode=ro', uri=True, timeout=2)) as restored:
        if fingerprint(restored) != before:
            raise ValueError('Backup contents differ. Preserve it for inspection and hold cutover.')
    with destination.open('rb') as stream:
        os.fsync(stream.fileno())
    fd = os.open(parent, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return before


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    print(json.dumps(copy_store(args.source, args.destination), sort_keys=True))


if __name__ == '__main__':
    main()
