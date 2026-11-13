"""Durable processing phases are not owner decisions or proposal revisions.

This read-only ledger retains raw database outcomes. Known terminal receipts can
establish completed generation after a local content rejection; they cannot make
that content valid, authorize execution or resolve consequential uncertainty.
"""
import json

from workbench.provider_receipts import safe_continuation
from workbench.visual_scope import digest


def _rows(connection,query,args):
    cursor=(connection.exec_driver_sql(query,args) if hasattr(connection,'exec_driver_sql')
            else connection.execute(query,args))
    return cursor.fetchall()


def observe(connection,identity,evidence_roots,*,exclude_job=None):
    from workbench.investigation_start import first, validate_prefix
    initial = first(connection, identity)
    excluded = validate_prefix(connection, initial) if initial else set()
    rows=_rows(connection,'''SELECT j.id,j.revision,j.status,j.finished_at,j.payload,
              a.id,a.backend,a.requested_model,a.actual_model,a.outcome,a.finished_at,a.usage
              FROM planning_jobs j LEFT JOIN model_attempts a ON a.job_id=j.id AND a.purpose='planning'
              WHERE j.intake_id=? AND j.kind='planning' ORDER BY j.revision''',(identity,))
    phases=[]
    operations=[]
    totals={}
    for row in rows:
        job,sequence,local,finished,payload,attempt,backend,requested,actual,outcome,terminal,usage=row
        if job==exclude_job or job in excluded:
            continue
        payload=json.loads(payload) if isinstance(payload,str) else payload
        usage=json.loads(usage) if isinstance(usage,str) else usage
        if not attempt:
            from workbench.investigation_start import records
            from workbench.scope_binding import local_operation
            full = records(connection,'SELECT * FROM planning_jobs WHERE id=?',(job,))
            operation=local_operation(connection,full[0])
            if operation:
                operations.append(operation)
                continue
        facts=payload.get('diagnostics',{})
        caps=payload.get('recovery_capabilities',{})
        from workbench.provider_receipts import request_state
        settlement=request_state(attempt,backend,requested,actual,outcome,usage,facts,caps,evidence_roots,
            local_status=local,local_finished=finished,finished=terminal)
        provider_state=settlement['provider_state']
        terminal_proof=settlement['terminal_proof']
        actual=settlement['actual_model']
        read_only=settlement['read_only']
        if provider_state in {'completed','interrupted'} and isinstance(usage,dict) and usage.get('status')=='complete':
            values=usage.get('values',{})
            if (usage.get('units')!='tokens' or not isinstance(values,dict)
                    or any(type(values.get(k)) is not int or values[k]<0
                           for k in ('prompt_tokens','completion_tokens','total_tokens'))):
                raise ValueError('Completed phase accounting is malformed')
            for key,value in values.items():
                if type(value) is not int or value<0:
                    raise ValueError('Completed phase accounting is malformed')
                totals[key]=totals.get(key,0)+value
        elif terminal_proof and terminal_proof.get('known_usage'):
            for key,value in terminal_proof['known_usage'].items():
                totals[key]=totals.get(key,0)+value
        count=settlement['requests']
        phases.append(dict(job=job,sequence=sequence,local_status=local,local_finished=finished,
            attempt=attempt,backend=backend,requested_model=requested,actual_model=actual,
            database_outcome=outcome,provider_state=provider_state,usage=usage,requests=count,
            input_binding=digest(payload),terminal_proof=terminal_proof,read_only=read_only,
            settlement=settlement,
            output_accepted=local=='completed' and provider_state=='completed',
            execution_authorized=False))
    start = initial['revision'] if initial else 1
    sequences=[p['sequence'] for p in phases]
    explained=[n for operation in operations for n in operation['explained_revisions']]
    end=max([*sequences,*explained],default=start)
    if (not phases or len(sequences)!=len(set(sequences)) or set(sequences)&set(explained)
            or sorted(set(sequences+explained))!=list(range(start,end+1))):
        raise ValueError('The processing phase history is unavailable or duplicated')
    return dict(phases=phases,local_operations=operations,next_sequence=end+1,
        known_usage=totals,unknown_usage=any(p['provider_state'] in {'unknown','active'} or
            p['provider_state']!='not_started' and (not isinstance(p['usage'],dict) or p['usage'].get('status')!='complete') for p in phases),
        execution_authorized=False)


def renewable(snapshot):
    """A local rejection is renewable only after every old generation is settled."""
    return bool(snapshot['phases']) and all(safe_continuation(p) for p in snapshot['phases'])


def hold_reason(snapshot):
    if any(p['provider_state']=='active' for p in snapshot['phases']):
        return 'Workbench is still finishing the previous read-only investigation. Its execution must settle before another request can start. Your report, scope choice and earlier plan are saved.'
    unresolved=next((p for p in snapshot['phases'] if p['provider_state']=='unknown'),None)
    if unresolved:
        return (f"Investigation request {unresolved['attempt'] or unresolved['job']} has no verified terminal response. "
                'A disconnected transport does not prove it stopped. New generation is held to avoid duplicate work. '
                'Your report, scope choice, earlier plan and any known usage are saved.')
    return 'The previous investigation lacks verified read-only safeguards or a finished local record. New generation remains held; your report and history are saved.'
