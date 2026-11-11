"""Evidence-bound historical ownership, separate from task execution/dependencies.

Only an explicit reviewed manifest reconciles originals. Titles never establish
ownership. Immutable payload digests and source receipts prevent guessed links.
"""
from sqlalchemy import inspect, select
from workbench.db import intakes, planning_jobs, task_lineage, write_transaction
from workbench.intake import digest
from workbench.service import now


def reconcile(engine, manifest):
    members = manifest['members']
    root, title = manifest['canonical_id'], manifest['title']
    if (not 2 <= len(members) <= 128 or len({m['intake_id'] for m in members}) != len(members)
            or root not in {m['intake_id'] for m in members} or not 1 <= len(title.strip()) <= 160):
        raise ValueError('An explicit root and distinct evidenced members are required.')
    with write_transaction(engine) as c:
        for member in members:
            identity = member['intake_id']
            from workbench.task_cleanup import require_current
            require_current(c, identity)
            row = c.execute(select(intakes).where(intakes.c.id == identity)).mappings().one()
            evidence = member['evidence']
            if (digest(row['payload']) != member['payload_sha256'] or not evidence
                    or any(set(e) != {'source', 'sha256'} or not e['source']
                           or len(e['sha256']) != 64 or any(x not in '0123456789abcdef' for x in e['sha256']) for e in evidence)):
                raise ValueError('Original request or evidence binding differs.')
            relation = 'request' if identity == root else member['relation']
            if relation not in {'request', 'retry', 'implementation', 'verification', 'recovery', 'publication'} or (identity != root and relation == 'request'):
                raise ValueError('Choose an internal relationship; distinct requests stay separate.')
            if c.execute(select(planning_jobs.c.id).where(planning_jobs.c.intake_id == identity,
                    planning_jobs.c.status.in_(['queued', 'running']))).first():
                raise ValueError('Active planning cannot be reconciled.')
            expected = dict(intake_id=identity, canonical_id=root, title=title, relation=relation,
                            evidence=dict(payload_sha256=member['payload_sha256'], sources=evidence))
            old = c.execute(select(task_lineage).where(task_lineage.c.intake_id == identity)).mappings().first()
            if old and any(old[k] != v for k, v in expected.items()):
                raise ValueError('An existing ownership link differs; no history was overwritten.')
            if not old:
                if c.execute(select(task_lineage.c.intake_id).where(task_lineage.c.canonical_id == identity,
                        task_lineage.c.canonical_id != root)).first():
                    raise ValueError('Nested or cyclic request ownership is not supported.')
                c.execute(task_lineage.insert().values(**expected, created_at=now()))
    return len(members)


def links(c):
    # Older migration/recovery stores remain readable before the additive upgrade.
    if not inspect(c).has_table('task_lineage'):
        return []
    return [dict(r) for r in c.execute(select(task_lineage)).mappings()]


def history(c, identity):
    if not inspect(c).has_table('task_lineage'):
        return None
    root = c.scalar(select(task_lineage.c.canonical_id).where(task_lineage.c.intake_id == identity))
    if root is None:
        return None
    links = c.execute(select(task_lineage, intakes.c.status, intakes.c.created_at.label('original_created_at'))
                      .join(intakes, task_lineage.c.intake_id == intakes.c.id)
                      .where(task_lineage.c.canonical_id == root)
                      .order_by(intakes.c.created_at, task_lineage.c.intake_id)).mappings().all()
    return dict(canonical_id=root, title=links[0]['title'], members=[dict(
        intake_id=r['intake_id'], relation=r['relation'], status=r['status'],
        created_at=r['original_created_at'], evidence=r['evidence']) for r in links])


def reverse(engine, manifest):
    """Explicit organizational rollback; the caller retains the exported link receipt."""
    expected_ids = {m['intake_id'] for m in manifest['members']}
    with write_transaction(engine) as c:
        from workbench.task_cleanup import require_current
        require_current(c, manifest['canonical_id'])
        rows = [dict(r) for r in c.execute(select(task_lineage).where(
            task_lineage.c.canonical_id == manifest['canonical_id'])).mappings()]
        if not rows:
            return []
        members = {m['intake_id']: m for m in manifest['members']}
        if {r['intake_id'] for r in rows} != expected_ids or any(
                r['title'] != manifest['title'] or r['relation'] != (
                    'request' if r['intake_id'] == manifest['canonical_id'] else members[r['intake_id']]['relation'])
                or r['evidence'] != dict(payload_sha256=members[r['intake_id']]['payload_sha256'],
                                        sources=members[r['intake_id']]['evidence']) for r in rows):
            raise ValueError('Reconciliation changed; rollback must match its entire original manifest.')
        c.execute(task_lineage.delete().where(task_lineage.c.canonical_id == manifest['canonical_id']))
        return rows
