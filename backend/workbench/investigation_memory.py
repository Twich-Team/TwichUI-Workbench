"""Reuse exact committed reads from settled local investigations, never authority.

Source is fetched and checked afresh. A separately bounded rejected self-review
may supply untrusted negative feedback after source revalidation; no diagnosis,
plan authority, approval, expected answer or generated change is reused. A complete
unapproved draft may be provided only as critique data, never directly returned
as the new task plan.
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


def construction_source(packet, history):
    """Reuse checked source coordinates, never an interrupted model's output.

    Only completed-prefix receipts recorded by the renewal gate may seed this
    cache. Every byte is read anew from the currently permitted committed blobs.
    """
    from workbench import bug_source_planning as source
    allowed={f['path']:f['sha256'] for f in packet['files']}
    candidate=copy.deepcopy(packet)
    seen={(w['path'],w['line'],w['through_line']) for w in candidate.get('inspection_windows',[])}
    reused=[]
    for item in history[-1:]:
        for settlement in item.get('settlement',[]):
            proof=settlement.get('read_only_quarantine',{}).get('completed_prefix')
            if not proof or proof.get('attempt')!=item.get('construction',{}).get('id'):
                continue
            prior=Path(item['claim']['root']).parents[1]
            for turn in proof['turns']:
                path=prior/('planning-evidence-'+turn['phase'])/'events.jsonl'
                events=read_journal(path)
                import hashlib
                if hashlib.sha256(path.read_bytes()).hexdigest()!=turn['source_sha256']:
                    raise ValueError('Retained completed source receipt changed')
                for event in events:
                    if event.get('phase') not in {'source-read-result','source-function-context'}:
                        continue
                    for window in event.get('windows',[]):
                        name,start,end,sha=(window[k] for k in ('path','line','through_line','sha256'))
                        key=(name,start,end)
                        if key in seen:
                            continue
                        if name not in allowed or allowed[name]!=sha or type(start) is not int or type(end) is not int or not 1<=start<=end<start+160:
                            raise ValueError('Retained source range exceeds the current source permission')
                        raw,metadata=source.committed(name,packet['base'])
                        lines=raw.decode().splitlines()
                        if metadata['sha256']!=sha or end>len(lines):
                            raise ValueError('Retained committed source changed')
                        fresh=dict(path=name,line=start,through_line=end,sha256=sha,
                            text='\n'.join(lines[start-1:end]),coverage='revalidated completed construction read')
                        candidate.setdefault('inspection_windows',[]).append(fresh)
                        seen.add(key)
                        reused.append({k:v for k,v in fresh.items() if k!='text'})
                        if len(seen)>source.window_limit(packet) or source.source_characters(candidate)>source.read_limit(packet):
                            raise ValueError('Retained source exceeds the current working-set bound')
    source.verify_packet(packet,candidate)
    return candidate,reused


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
    reused = completed_packet(packet, original, current_attempt, engine)
    if reused is not None:
        return reused
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
                        if event.get('phase') in {'source-memory-reused','source-bootstrap-context','source-function-context'} or (
                                completed and event.get('phase') == 'source-read-result'):
                            windows.extend(event.get('windows', []))
                candidate = copy.deepcopy(packet)
                used = []
                existing = [(w['path'],w['line'],w['through_line'],w['sha256'])
                            for w in candidate.get('inspection_windows',[])]
                seen = set(existing)
                for window in windows:
                    key = (window['path'], window['line'], window['through_line'], window['sha256'])
                    if key in seen or any(p==key[0] and sha==key[3] and start<=key[1] and key[2]<=end
                                          for p,start,end,sha in existing):
                        continue
                    seen.add(key)
                    if len(seen) > source.window_limit(candidate):
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
                footprint=(source.source_characters(candidate) if source.program_context(packet)
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


def completed_packet(packet, original, current_attempt, engine):
    """Re-read committed source from a settled packet; copy no generated outcome."""
    from workbench import bug_source_planning as source

    with engine.connect() as connection:
        records = connection.execute(select(
            model_attempts.c.id, model_attempts.c.usage,
            planning_jobs.c.payload.label('job_payload'), intakes.c.payload.label('original'))
            .join(planning_jobs, planning_jobs.c.id==model_attempts.c.job_id)
            .join(intakes, intakes.c.id==model_attempts.c.task_id)
            .where(model_attempts.c.source=='workbench', model_attempts.c.purpose=='planning',
                model_attempts.c.backend.in_(['local','codex']), model_attempts.c.outcome=='completed',
                model_attempts.c.finished_at.is_not(None), planning_jobs.c.status=='completed')
            .order_by(model_attempts.c.started_at.desc()).limit(16)).mappings().all()
    allowed={f['path']:f['sha256'] for f in packet['files']}
    for row in records:
        if (row['id']==current_attempt or not same_report(row['original'],original) or
                not isinstance(row['usage'],dict) or row['usage'].get('status')!='complete'):
            continue
        cached=row['job_payload'].get('bug_inspection_packet')
        if not isinstance(cached,dict) or cached.get('base')!=packet['base']:
            continue
        try:
            candidate=copy.deepcopy(packet)
            windows=cached.get('inspection_windows',[])
            if not isinstance(windows,list) or len(windows)>source.window_limit(packet):
                continue
            used,seen=[],set()
            for window in windows:
                key=window['path'],window['line'],window['through_line'],window['sha256']
                if key in seen:
                    continue
                seen.add(key)
                if (key[0] not in allowed or allowed[key[0]]!=key[3] or
                        type(key[1]) is not int or type(key[2]) is not int):
                    raise ValueError('Cached range exceeds current selected source')
                raw,actual=source.committed(key[0],packet['base'])
                if actual['sha256']!=key[3]:
                    raise ValueError('Cached committed source changed')
                lines=raw.decode().splitlines()
                if not 1<=key[1]<=key[2]<key[1]+160 or key[2]>len(lines):
                    raise ValueError('Cached committed range changed')
                # Re-read exact blobs rather than replaying provider output or
                # pagination state from the old task. Existing overlap is valid.
                fresh=dict(path=key[0],sha256=key[3],line=key[1],through_line=key[2],
                    text='\n'.join(lines[key[1]-1:key[2]]),coverage='revalidated committed source cache')
                candidate.setdefault('inspection_windows',[]).append(fresh)
                used.append(fresh)
                if source.source_characters(candidate)>source.read_limit(packet):
                    raise ValueError('Cached source exceeds current working set')
            source.verify_packet(packet,candidate)
            if used:
                return candidate,used,str(UUID(row['id']))
        except (ValueError,OSError,KeyError,TypeError):
            continue  # Cache miss never grants source, model, plan or execution authority.
    return None


def reviewed_behavior(prior, roots, binding):
    """Digest of a complete private draft matching the rejected review, no code reuse."""
    import hashlib
    from workbench.planning_response import strict_json
    from workbench.bug_evidence import BugPlanningResponse
    from workbench.bug_plan_review import behavior_binding
    from workbench.visual_scope import digest
    for index in range(8):
        identity=str(UUID(prior) if index==0 else uuid5(UUID(prior),f'committed-source-turn-{index}'))
        paths=[root/'artifacts/runs'/('planning-evidence-'+identity)/'events.jsonl' for root in roots]
        paths=[path for path in paths if path.exists()]
        try:
            if not paths:
                continue
            journals=[read_journal(path) for path in paths]
            if any(j!=journals[0] for j in journals[1:]):
                continue
            events=journals[0]
            if not any(e.get('phase')=='envelope' and e.get('generation_state')=='finished'
                    and e.get('accounting_state')=='complete' and e.get('completion')=='stop'
                    and e.get('model_matches')==1 for e in events):
                continue
            markers=[e for e in events if e.get('phase')=='private-proposed-data' and e.get('name')=='proposed-plan']
            if len(markers)!=1:
                continue
            copies=[]
            for path in paths:
                path=path.with_name('proposed-plan.json')
                owned(path)
                fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
                try:
                    info=os.fstat(fd)
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=os.getuid() or info.st_mode&0o077 or info.st_size>24000:
                        raise ValueError('Unsafe private rejected program')
                    raw=os.read(fd,24001)
                finally:
                    os.close(fd)
                if len(raw)!=markers[0]['bytes'] or hashlib.sha256(raw).hexdigest()!=markers[0]['sha256']:
                    raise ValueError('Rejected program snapshot changed')
                copies.append(raw)
            if any(raw!=copies[0] for raw in copies[1:]):
                continue
            candidate=strict_json(copies[0])
            checked=BugPlanningResponse.model_validate(candidate)
            if digest(candidate)==binding and checked.plan and checked.plan.reproducer.phases:
                return behavior_binding(candidate)
        except (ValueError,OSError,KeyError,TypeError):
            continue
    return None


def review_lessons(prior, engine, _seen=()):
    """Completed rejected self-check DATA, never a replacement plan or authority.

    Caller must first obtain prior from seed's settled/current-source validation.
    Snapshot hash and exact review binding must match the complete private journal.
    """
    import hashlib
    from workbench import everyday
    from workbench.bug_plan_review import ReviewResponse
    from workbench.planning_response import strict_json
    if prior in _seen or len(_seen)>=8:
        return None
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
            for inherited in events:
                if inherited.get('phase')=='rejected-plan-feedback-reused' and inherited.get('approved') is False and inherited.get('executed') is False:
                    original=review_lessons(inherited.get('predecessor'),engine,(*_seen,prior))
                    if original and all(original[k]==inherited.get(k) for k in ('predecessor','review_binding','issues','note')):
                        result=original
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
            behavior=reviewed_behavior(prior,roots,review.review_binding)
            if behavior:
                result['rejected_behavior_sha256']=behavior
        except (OSError,ValueError,KeyError,TypeError):
            continue
    return result


def draft_for_critique(prior, engine, packet, original, feedback):
    """A fully finished unapproved draft may be criticised, never directly reused.

    The caller already revalidated this same report's settled source through seed.
    Partial/unknown execution and successful proposals do not qualify.
    """
    import hashlib
    import json
    from workbench import everyday,bug_investigation as bug
    from workbench.bug_evidence import BugPlanningResponse
    from workbench.bug_program import syntax
    with engine.connect() as c:
        attempt=c.execute(select(model_attempts).where(model_attempts.c.id==prior)).mappings().first()
        if (not attempt or attempt['outcome']!='failed' or not attempt['finished_at']
                or not isinstance(attempt['usage'],dict) or attempt['usage'].get('status')!='complete'):
            return None
    roots=[Path(root) for root in everyday.configuration(engine).evidence_roots]
    for index in reversed(range(8)):
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
            markers=[e for e in events if e.get('phase')=='private-proposed-data' and e.get('name')=='proposed-plan']
            if len(markers)!=1:
                continue
            copies=[]
            for path in paths:
                path=path.with_name('proposed-plan.json')
                owned(path)
                fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
                try:
                    info=os.fstat(fd)
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=os.getuid() or info.st_mode&0o077 or info.st_size>24000:
                        raise ValueError('Unsafe unapproved draft')
                    raw=os.read(fd,24001)
                finally:
                    os.close(fd)
                if len(raw)!=markers[0]['bytes'] or hashlib.sha256(raw).hexdigest()!=markers[0]['sha256']:
                    raise ValueError('Unapproved draft changed')
                copies.append(raw)
            if any(raw!=copies[0] for raw in copies[1:]):
                continue
            from workbench.planning_response import strict_json
            value=strict_json(copies[0])
            checked=BugPlanningResponse.model_validate(value)
            if not checked.plan or not checked.plan.reproducer.phases:
                continue
            for n,phase in enumerate(checked.plan.reproducer.phases):
                syntax(phase.code,n)
            bug.resolve(value,packet,original,feedback)  # Validate current source/policy, no fixture execution.
            return dict(candidate=json.loads(json.dumps(value)),predecessor=prior,
                        sha256=markers[0]['sha256'],text_complete=True,approved=False,executed=False)
        except (ValueError,OSError,KeyError,TypeError):
            continue
    return None
