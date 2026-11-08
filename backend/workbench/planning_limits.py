"""Local operation bounds follow the actual saved consent, including old history."""
import json

LEGACY_COMPLETION = 2600
PROGRAM_COMPLETION = 6144
PROGRAM_REASONING = 3072


def consent_cap(consent):
    version = consent.get('investigation_version')
    if version is not None and type(version) is not int:
        raise ValueError('Malformed local operation consent')
    if version in (7, 8, 9, 10):
        requests = 16 if version == 10 else 8
        if (not all(type(consent.get(k)) is int for k in ('max_local_completion_tokens',
                'max_local_requests','planning_deadline_seconds','local_request_deadline_seconds'))
                or consent.get('max_local_completion_tokens') != PROGRAM_COMPLETION
                or consent.get('max_local_requests') != requests
                or consent.get('planning_deadline_seconds') != 1800
                or consent.get('local_request_deadline_seconds') != 300):
            raise ValueError('The saved local operation bounds changed')
        if version in (8, 9, 10) and (not all(type(consent.get(k)) is int for k in (
                'max_codex_requests','max_total_model_requests','codex_request_deadline_seconds'))
                or consent['max_codex_requests'] != requests or consent['max_total_model_requests'] != requests
                or consent['codex_request_deadline_seconds'] != (300 if version >= 9 else 120)):
            raise ValueError('The saved shared investigation bounds changed')
        return PROGRAM_COMPLETION
    if version is None or version in (1, 2, 3, 4, 5, 6):
        return LEGACY_COMPLETION
    raise ValueError('Unknown local operation consent')


def final_bound(payload):
    cap = consent_cap(payload.get('local_planning_receipt', {}))
    actual = payload.get('diagnostics', {}).get('request_completion_limit',
        LEGACY_COMPLETION if cap == LEGACY_COMPLETION else None)
    if type(actual) is not int or actual not in (LEGACY_COMPLETION, PROGRAM_COMPLETION) or actual > cap:
        raise ValueError('The final request bound is unavailable')
    return actual


def model_cap(connection, identity):
    sql = '''SELECT j.payload FROM planning_jobs j JOIN model_attempts a ON a.job_id=j.id
             WHERE a.id=?'''
    cursor = (connection.exec_driver_sql(sql, (identity,)) if hasattr(connection, 'exec_driver_sql')
              else connection.execute(sql, (identity,)))
    row = cursor.fetchone()
    return consent_cap(json.loads(row[0]).get('local_planning_receipt', {})) if row else LEGACY_COMPLETION


def request_bound(consent):
    """Read the immutable claim bound; a new default never enlarges old consent."""
    consent_cap(consent)
    return 16 if consent.get('investigation_version') == 10 else 8


def attempt_request_bound(connection, identity):
    sql = '''SELECT j.payload FROM planning_jobs j JOIN model_attempts a ON a.job_id=j.id
             WHERE a.id=?'''
    cursor = (connection.exec_driver_sql(sql, (identity,)) if hasattr(connection, 'exec_driver_sql')
              else connection.execute(sql, (identity,)))
    row = cursor.fetchone()
    if row is None:
        raise ValueError('Saved task consent is unavailable')
    return operation_request_bound(json.loads(row[0]))


def operation_request_bound(payload):
    bound=request_bound(payload.get('local_planning_receipt',{}))
    followup=payload.get('bug_followup',{})
    if followup.get('budget_version')==2:
        used,remaining=followup.get('consumed_requests'),followup.get('remaining_requests')
        if (type(used) is not int or type(remaining) is not int or not 1<=used<bound or
                used+remaining!=bound or remaining<=0 or followup.get('max_total_requests')!=bound):
            raise ValueError('The continuation request accounting changed')
        return remaining
    return bound


def followup_budget(connection, initial):
    """Fresh reply consumes the original task allowance, with its original source scope."""
    from sqlalchemy import select
    from workbench.db import model_attempts
    attempts=connection.execute(select(model_attempts).where(
        model_attempts.c.job_id==initial['id'],model_attempts.c.purpose=='planning')).mappings().all()
    if len(attempts)!=1:
        raise ValueError('The original investigation accounting is unavailable')
    parent=attempts[0]
    return continuation_budget(initial,parent)


def continuation_budget(initial,parent):
    """Pure validation shared by the worker and independent persisted-result review."""
    from workbench.visual_scope import digest
    usage=parent['usage']
    bound=request_bound(initial['payload'].get('local_planning_receipt',{}))
    if (parent['outcome']!='completed' or not parent['finished_at'] or not isinstance(usage,dict)
            or usage.get('status')!='complete' or usage.get('units')!='tokens'):
        raise ValueError('The original investigation is not settled with complete accounting')
    used=usage.get('requests_observed',1)
    if type(used) is not int or not 1<=used<bound:
        raise ValueError('The task has no remaining investigation requests')
    packet=initial['payload'].get('bug_inspection_packet')
    if not isinstance(packet,dict) or not packet.get('files') or not packet.get('base'):
        raise ValueError('The original source disclosure scope is unavailable')
    return dict(budget_version=2,parent_attempt=parent['id'],parent_usage_sha256=digest(usage),
        consumed_requests=used,remaining_requests=bound-used,max_total_requests=bound,
        source_scope=dict(base=packet['base'],files={f['path']:f['sha256'] for f in packet['files']}))


def settled_planning_chain(connection, identity, revision=None):
    """Return every settled phase; a clarification cannot hide the first phase's usage."""
    from workbench.visual_scope import digest
    query='''SELECT j.id,j.revision,j.status,j.attempts,j.payload,a.id,a.usage,a.outcome,
                    a.finished_at,a.backend,a.actual_model,a.requested_model
             FROM planning_jobs j JOIN model_attempts a ON a.job_id=j.id
             WHERE j.intake_id=? AND j.kind='planning' AND a.purpose='planning'
             ORDER BY j.revision'''
    cursor=(connection.exec_driver_sql(query,(identity,)) if hasattr(connection,'exec_driver_sql')
            else connection.execute(query,(identity,)))
    records=[]
    for row in cursor.fetchall():
        job,rev,status,claims,payload,attempt,usage,outcome,finished,backend,actual,requested=row
        payload=json.loads(payload) if isinstance(payload,str) else payload
        usage=json.loads(usage) if isinstance(usage,str) else usage
        if (status!='completed' or claims!=1 or outcome!='completed' or not finished
                or not isinstance(usage,dict) or usage.get('status')!='complete'
                or usage.get('units')!='tokens' or not actual or actual!=requested):
            raise ValueError('The investigation phase is unsettled or lacks complete accounting')
        records.append(dict(id=attempt,job_id=job,revision=rev,payload=payload,usage=usage,
            outcome=outcome,finished_at=finished,backend=backend,actual_model=actual,requested_model=requested))
    if ([r['revision'] for r in records] not in ([1],[1,2])
            or revision is not None and records[-1]['revision']!=revision):
        raise ValueError('The investigation phase history changed')
    first=records[0]
    if len(records)==2:
        second=records[1]
        expected=continuation_budget(dict(payload=first['payload']),first)
        received=second['payload'].get('bug_followup',{})
        if (any(received.get(k)!=v for k,v in expected.items())
                or second['payload'].get('local_planning_receipt')!=first['payload'].get('local_planning_receipt')
                or second['backend']!=first['backend'] or second['actual_model']!=first['actual_model']
                or second['payload'].get('provider_switch')):
            raise ValueError('The continuation provider, source or consumed accounting changed')
        # Also validate its own remaining bound, including integer types.
        operation_request_bound(second['payload'])
    counts=[r['usage'].get('requests_observed',1) for r in records]
    if (any(type(n) is not int or not 1<=n<=operation_request_bound(r['payload'])
            for n,r in zip(counts,records,strict=True))
            or sum(counts)>request_bound(first['payload'].get('local_planning_receipt',{}))):
        raise ValueError('The task-wide investigation request allowance was exceeded')
    # Digest retained here documents that all observations remain individually attributable.
    for r in records:
        r['usage_sha256']=digest(r['usage'])
    return records


def codex_observations(connection, identity, usage, *, bug, planning):
    """Validate reported observations, never promise a provider token/billing ceiling."""
    from workbench.codex_provider import BUG_OBSERVED, MAX_OBSERVED
    if not isinstance(usage, dict) or not isinstance(usage.get('values'), dict):
        raise ValueError('Codex usage observations are unavailable')
    bound = attempt_request_bound(connection, identity) if planning and bug else 1
    count = usage.get('requests_observed', 1) if planning else 1
    total = usage.get('values', {}).get('total_tokens')
    limit = BUG_OBSERVED if bug else MAX_OBSERVED
    maximum = usage.get('max_request_total_tokens', total)
    if not (usage.get('units') == 'tokens' and type(count) is int and 1 <= count <= bound
            and type(total) is int and 0 < total <= limit*count
            and type(maximum) is int and 0 < maximum <= limit):
        raise ValueError('Codex observations exceed the saved operation or current post-observation guard')
    return count
