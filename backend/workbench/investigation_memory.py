"""Reuse exact committed reads from settled local investigations, never authority.

Source is fetched and checked afresh. A separately bounded rejected self-review
may supply untrusted negative feedback after source revalidation; no diagnosis,
plan, approval, expected answer or generated change is reused.
"""
import copy
import os
from pathlib import Path
import re
import stat
from uuid import UUID, uuid5

from sqlalchemy import select

from workbench.db import intakes, model_attempts, planning_jobs, proposals
from workbench.trusted_preparation import owned


def same_report(left, right):
    keys = ('kind', 'description', 'lua_error', 'visual_requirement')
    # Counter suffixes distinguish acceptance submissions, not source relevance.
    def title(value):
        return re.sub(r'\s+\d+$', '', value.get('title', '')).strip()
    return (all(left.get(k, '') == right.get(k, '') for k in keys)
            and title(left) == title(right))


def read_journal(path):
    owned(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 65536):
            raise ValueError('Unsafe private source journal')
        with os.fdopen(descriptor, 'r', closefd=False) as stream:
            raw = stream.read(65537)
        if len(raw.encode()) > 65536:
            raise ValueError('Oversized private source journal')
        from workbench.planning_response import strict_json
        lines = raw.splitlines()
        if len(lines) > 32 or any(len(line.encode()) > 16384 for line in lines):
            raise ValueError('Private source journal exceeds record bounds')
        rows = [strict_json(line) for line in lines]
        if any(not isinstance(row, dict) or not isinstance(row.get('phase'), str) for row in rows):
            raise ValueError('Malformed private source journal')
        return rows
    finally:
        os.close(descriptor)


def settled(connection, attempt, job):
    if (attempt['source'] != 'workbench' or attempt['backend'] != 'local' or attempt['purpose'] != 'planning'
            or not attempt['finished_at'] or job['status'] != 'failed'):
        return False
    if connection.execute(select(proposals.c.id).where(
            proposals.c.intake_id == attempt['task_id'])).first():
        return False
    if attempt['outcome'] == 'incomplete_output':
        from workbench.partial_planning_reconciliation import accounting
        accounting(connection, attempt['id'], connection.engine)
        return True
    if attempt['outcome'] == 'interrupted_unknown':
        from workbench.planning_reconciliation import accounting
        accounting(connection, attempt['id'])
        return True
    usage = attempt['usage']
    return (attempt['outcome'] in {'failed', 'completed', 'superseded'}
            and isinstance(usage, dict) and usage.get('status') == 'complete'
            and job['payload'].get('diagnostics', {}).get('generation_state') == 'finished')


def seed(packet, original, current_attempt, engine):
    from workbench import everyday, bug_source_planning as source
    from workbench.bug_evidence import InvestigationRead
    from workbench.trusted_preparation import configuration
    from workbench.visual_scope import digest

    policy = configuration(engine)
    if digest(policy) != packet['policy_sha256']:
        raise ValueError('Source memory preparation policy changed')
    roots = [Path(root) for root in everyday.configuration(engine).evidence_roots]
    with engine.connect() as connection:
        query = (select(model_attempts, planning_jobs.c.payload.label('job_payload'),
                        planning_jobs.c.status.label('job_status'), intakes.c.payload.label('original'))
                 .join(planning_jobs, planning_jobs.c.id == model_attempts.c.job_id)
                 .join(intakes, intakes.c.id == model_attempts.c.task_id)
                 .where(model_attempts.c.backend == 'local', planning_jobs.c.status == 'failed',
                        planning_jobs.c.kind == 'planning')
                 .order_by(model_attempts.c.started_at.desc()).limit(16))
        for row in connection.execute(query).mappings():
            attempt = dict(row)
            if attempt['id'] == current_attempt or not same_report(row['original'], original):
                continue
            try:
                if not settled(connection, attempt, dict(status=row['job_status'], payload=row['job_payload'])):
                    continue
                prior = str(UUID(attempt['id']))
                windows = []
                for index in range(8):
                    identity = prior if index == 0 else str(uuid5(UUID(prior), f'committed-source-turn-{index}'))
                    matches = [root/'artifacts/runs'/('planning-evidence-'+identity)/'events.jsonl' for root in roots]
                    matches = [path for path in matches if path.exists()]
                    if not matches:
                        continue
                    journals = [read_journal(path) for path in matches]
                    if any(journal != journals[0] for journal in journals[1:]):
                        raise ValueError('Conflicting source journals')
                    completed = False
                    for event in journals[0]:
                        if event.get('phase') == 'envelope':
                            completed = (event.get('generation_state') == 'finished'
                                         and event.get('accounting_state') == 'complete'
                                         and event.get('completion') == 'stop'
                                         and event.get('model_matches') == 1)
                        if event.get('phase') in {'source-memory-reused','source-bootstrap-context'} or (
                                completed and event.get('phase') == 'source-read-result'):
                            windows.extend(event.get('windows', []))
                candidate = copy.deepcopy(packet)
                used = []
                seen = set()
                for window in windows:
                    key = (window['path'], window['line'], window['through_line'], window['sha256'])
                    if key in seen:
                        continue
                    seen.add(key)
                    if len(seen) > 24:
                        raise ValueError('Source memory exceeds window bound')
                    _, item = source.committed(key[0], packet['base'])
                    if item['sha256'] != key[3]:
                        raise ValueError('Cached source changed')
                    returned, _, _ = source.inspect(candidate, [InvestigationRead(
                        path=key[0], start=key[1], end=key[2])])
                    if len(returned) != 1 or (returned[0]['line'], returned[0]['through_line']) != key[1:3]:
                        raise ValueError('Cached source range changed')
                    used.extend(returned)
                initial_chars = sum(len(e['text']) for f in [*packet['files'], *packet.get('api_references', [])]
                                    for e in f['excerpts'])
                footprint=(source.source_characters(candidate) if packet.get('plan_review') == 'source-grounded-lua-v1'
                           else initial_chars+sum(len(w['text']) for w in used))
                if footprint > source.read_limit(packet):
                    raise ValueError('Source memory exceeds read bound')
                source.verify_packet(packet, candidate)
                if used:
                    return candidate, used, prior
            except (ValueError, OSError, KeyError, TypeError):
                # A cache miss never makes unknown execution eligible for replay.
                # Admission reconciles execution separately before any request.
                continue
    return packet, [], None


def review_lessons(prior, engine):
    """Completed rejected self-check DATA, never a replacement plan or authority.

    Caller must first obtain prior from seed's settled/current-source validation.
    Snapshot hash and exact review binding must match the complete private journal.
    """
    import hashlib
    from workbench import everyday
    from workbench.bug_plan_review import ReviewResponse
    from workbench.planning_response import strict_json
    roots=[Path(root) for root in everyday.configuration(engine).evidence_roots]
    result=None
    for index in range(8):
        identity=str(UUID(prior) if index==0 else uuid5(UUID(prior),f'committed-source-turn-{index}'))
        paths=[root/'artifacts/runs'/('planning-evidence-'+identity)/'events.jsonl' for root in roots]
        paths=[p for p in paths if p.exists()]
        try:
            if not paths:
                continue
            journals=[read_journal(p) for p in paths]
            if any(j!=journals[0] for j in journals[1:]):
                continue
            events=journals[0]
            if not any(e.get('phase')=='envelope' and e.get('generation_state')=='finished'
                    and e.get('accounting_state')=='complete' and e.get('completion')=='stop'
                    and e.get('model_matches')==1 for e in events):
                continue
            markers=[e for e in events if e.get('phase')=='private-proposed-data' and e.get('name')=='model-plan-review']
            checks=[e for e in events if e.get('phase')=='plan-self-check' and e.get('ready') is False]
            if len(markers)!=1 or len(checks)!=1:
                continue
            copies=[]
            for path in paths:
                path=path.with_name('model-plan-review.json')
                owned(path)
                fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
                try:
                    info=os.fstat(fd)
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=os.getuid() or info.st_mode&0o077 or info.st_size>8192:
                        raise ValueError('Unsafe private review snapshot')
                    raw=os.read(fd,8193)
                finally:
                    os.close(fd)
                if len(raw)!=markers[0]['bytes'] or hashlib.sha256(raw).hexdigest()!=markers[0]['sha256']:
                    raise ValueError('Private review snapshot changed')
                copies.append(raw)
            if any(raw!=copies[0] for raw in copies[1:]):
                continue
            review=ReviewResponse.model_validate(strict_json(copies[0]))
            if review.ready or review.review_binding!=checks[0]['binding']:
                continue
            result=dict(predecessor=prior,review_binding=review.review_binding,
                issues=[x.model_dump() for x in review.issues],
                note='Untrusted rejected model self-check from the same settled report with freshly verified source. '
                     'Reassess these objections against current evidence; no prior plan, diagnosis, expected answer or approval is reused.')
        except (OSError,ValueError,KeyError,TypeError):
            continue
    return result
