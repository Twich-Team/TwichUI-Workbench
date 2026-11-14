"""Contain a selected operation in its existing journal; never infer settlement.

Selection is explicit, before task data is interpreted. Database and capacity
failures remain shared hazards. Unidentified failures also propagate: catching an
error without an owning durable row would conceal a broken worker invariant.
"""
from contextvars import ContextVar
from functools import wraps
import errno
import logging

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError, NoResultFound, MultipleResultsFound

from workbench.db import write_transaction
from workbench.service import now

log = logging.getLogger(__name__)
owner = ContextVar('worker_operation', default=None)
in_scheduler = ContextVar('worker_scheduler', default=False)


def scheduler(function):
    @wraps(function)
    def run(*args, **kwargs):
        token = in_scheduler.set(True)
        try:
            return function(*args, **kwargs)
        finally:
            in_scheduler.reset(token)
    return run


def selected(table, row):
    if owner.get() is not None:
        owner.set((table, row['id']))


def shared_failure(error):
    from workbench.everyday import CapacityHold
    from workbench.storage_resources import StorageHold
    return (isinstance(error, SQLAlchemyError) and not isinstance(error, (NoResultFound, MultipleResultsFound))
            or isinstance(error, CapacityHold)
            or isinstance(error, StorageHold) and error.code in {'low_disk', 'low_inodes'}
            or isinstance(error, OSError) and error.errno in {errno.ENOSPC, errno.EDQUOT, errno.ENOMEM})


def isolated(function):
    @wraps(function)
    def run(engine, *args, **kwargs):
        if not in_scheduler.get():
            return function(engine, *args, **kwargs)
        token = owner.set(())
        try:
            return function(engine, *args, **kwargs)
        except Exception as error:
            if shared_failure(error) or not owner.get():
                raise
            table, identity = owner.get()
            # Keep provider receipts, claims, grants, fences and unknown usage
            # exactly as recorded. A failed operation cannot dispatch again.
            # Existing explicit recovery still owns any eventual disposition.
            expected = isinstance(error, (ValueError, OSError, HTTPException, KeyError, TypeError))
            message = ('Task operation could not complete' if expected else
                       'Unexpected task operation failure; possible effects need recovery')
            message += f' ({type(error).__name__}). No automatic replay; original evidence and permissions are retained.'
            with write_transaction(engine) as c:
                row = c.execute(select(table).where(table.c.id == identity)).mappings().one()
                if (table.name == 'planning_jobs' and row['status'] == 'running'
                        and row['kind'] == 'feature_execution' and row['lease']
                        and isinstance(row['payload'], dict) and row['payload'].get('phase') in
                        {'preparing_scope', 'preparing_independent_checks'}):
                    from workbench.feature_preparation import hold_interrupted
                    hold_interrupted(c, row)
                    log.exception('Contained interrupted feature preparation id=%s', identity)
                    return True
                # Never replace an independently committed successful outcome.
                if row['status'] not in {'completed', 'checked', 'published', 'applied', 'admitted', 'prepared',
                                         'cancelled', 'expired', 'rejected', 'blocked', 'stale', 'held', 'failed',
                                         'ended-before-result'}:
                    values = dict(status='failed', message=message, finished_at=now())
                    if table.name in {'task_sessions', 'real_publications'}:
                        values['status'] = 'held'
                    if table.name == 'planning_jobs' and isinstance(row['payload'], dict):
                        values['payload'] = {**row['payload'], 'phase': 'held',
                                             'worker_operation_failure': type(error).__name__}
                    c.execute(table.update().where(table.c.id == identity).values(**values))
                    if table.name == 'planning_jobs':
                        from workbench.db import intakes
                        c.execute(intakes.update().where(intakes.c.id == row['intake_id'],
                            intakes.c.status.in_(['queued', 'investigating']),
                            intakes.c.revision < row['revision']).values(status='failed'))
            log.exception('Contained operation failure journal=%s id=%s', table.name, identity)
            return True
        finally:
            owner.reset(token)
    return run


def eligible(c, column):
    """Exclude canonical archived/deleted requests and their linked histories."""
    from workbench.task_cleanup import lifecycle
    from workbench.task_identity import links
    states = lifecycle(c)
    excluded = {key for key, value in states.items() if value['archived_at'] or value['deleted_at']}
    if excluded:
        excluded.update(r['intake_id'] for r in links(c) if r['canonical_id'] in excluded)
    from workbench import bug_authority
    for identity in c.execute(select(bug_authority.planning_jobs.c.intake_id).where(
            bug_authority.planning_jobs.c.payload['routine_bug_authority'].as_string().is_not(None)).distinct()).scalars():
        if bug_authority.frozen(c, identity):
            try:
                bug_authority.current(c, identity)
            except ValueError:
                excluded.add(identity)
    return column.not_in(excluded)
