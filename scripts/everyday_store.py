"""Copy a stopped Workbench store without changing its database or opening an app."""
import argparse
from contextlib import closing
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
from uuid import UUID

from workbench.trusted_preparation import owned
from workbench.visual_scope import digest


def require_publications_settled(connection):
    """Carry paused, unclaimed delivery as data, never as install-time work.

    real_publication commits owner/fence before preparation and remote intent
    before writes. An empty journal is necessary but not sufficient: the exact
    task must also be durably paused so worker restart cannot dispatch it.
    Cutover still stops services and compares the complete store fingerprint.
    """
    from workbench.model_policy import ExecutionPolicy
    from workbench.real_publication import ACTIVE

    policy = connection.execute('SELECT payload FROM execution_policies ORDER BY revision DESC LIMIT 1').fetchone()
    pauses = (ExecutionPolicy.model_validate_json(policy[0]).task_pauses or {}) if policy else {}
    for status, raw_state, raw_snapshot, binding, identity, task_id, finished in connection.execute(
            'SELECT status,state,snapshot,binding,execution_id,task_id,finished_at FROM real_publications'):
        state = json.loads(raw_state)
        # A terminal hold can retain uncertain remote intent. It is not dormant.
        if status != 'published' and any(state.get(key) for key in ('push_started', 'pr_started', 'uncertain')):
            raise ValueError('A publication requires remote reconciliation before installation.')
        if status not in ACTIVE:
            continue
        snapshot = json.loads(raw_snapshot)
        row = connection.execute('''SELECT r.status,r.result,g.id,g.binding,g.snapshot
            FROM real_execution_requests r JOIN real_execution_grants g ON g.id=r.grant_id
            WHERE r.id=?''', (identity,)).fetchone()
        if row:
            result, grant = json.loads(row[1]), json.loads(row[4])
            linked = (grant.get('native_task') or {}).get('task_id') or (grant.get('workflow_task') or {}).get('intake_id')
        if (status != 'pending' or state != {} or finished is not None or not row
                or row[0] != 'checked' or not linked
                or task_id != snapshot.get('preview', {}).get('task_id')
                or not pauses.get(UUID(linked))
                or binding != digest(snapshot) or row[3] != digest(grant)
                or snapshot.get('execution_id') != identity or snapshot.get('grant_id') != row[2]
                or snapshot.get('grant_binding') != row[3]
                or snapshot.get('result_digest') != digest(result)
                or snapshot.get('candidate_id') != result.get('candidate_id')):
            raise ValueError('The source store still has unsettled publication work.')


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
        'pr_preparations': ('pending', 'preparing', 'reconciling'),
    }.items():
        placeholders = ','.join('?' for _ in active)
        if connection.execute(f'SELECT 1 FROM {table} WHERE status IN ({placeholders}) LIMIT 1', active).fetchone():
            raise ValueError('The source store still has unsettled work.')
    require_publications_settled(connection)
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
