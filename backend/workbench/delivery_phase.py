"""Durable processing phases are not owner decisions or proposal revisions.

This read-only ledger retains raw database outcomes. Known terminal receipts can
establish completed generation after a local content rejection; they cannot make
that content valid, authorize execution or resolve consequential uncertainty.
"""
import json

from workbench.provider_receipts import completed_turns,interrupted_turns
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
        read_only=bool(caps) and all(caps.get(k) is False for k in
            ('source_writes','execution_dispatch','external_action_tools'))
        provider_state='unknown'
        terminal_proof=None
        if local in {'queued','running'} or attempt and not terminal:
            provider_state='active'
        elif attempt and terminal and backend=='local' and facts.get('generation_state')=='not_started' and facts.get('investigation_requests')==0:
            from workbench.provider_receipts import not_started_local_turn
            terminal_proof=not_started_local_turn(attempt,evidence_roots,facts)
            provider_state='not_started'
        elif attempt and terminal and isinstance(usage,dict) and usage.get('status')=='complete':
            if backend=='codex':
                terminal_proof=(interrupted_turns(attempt,usage,evidence_roots,facts,caps)
                    if outcome=='incomplete_output' and facts.get('terminal_status')=='interrupted'
                    else completed_turns(attempt,usage,evidence_roots))
                provider_state=terminal_proof['provider_outcome']
            elif (outcome=='completed' or facts.get('generation_state')=='finished' and
                    facts.get('accounting_state')=='complete'):
                provider_state='completed'
            elif backend=='local' and facts.get('generation_state')=='finished':
                from workbench.provider_receipts import completed_local_turns
                terminal_proof=completed_local_turns(attempt,usage,evidence_roots,requested,facts)
                provider_state=terminal_proof['provider_outcome']
                if actual is None:
                    actual=terminal_proof['actual_model']  # Recovered fact only; database stays unchanged.
        if provider_state in {'completed','interrupted'}:
            values=usage.get('values',{})
            if (usage.get('units')!='tokens' or not isinstance(values,dict)
                    or any(type(values.get(k)) is not int or values[k]<0
                           for k in ('prompt_tokens','completion_tokens','total_tokens'))):
                raise ValueError('Completed phase accounting is malformed')
            for key,value in values.items():
                if type(value) is not int or value<0:
                    raise ValueError('Completed phase accounting is malformed')
                totals[key]=totals.get(key,0)+value
        count=0 if provider_state=='not_started' else usage.get('requests_observed',1) if isinstance(usage,dict) else None
        if provider_state in {'completed','interrupted'} and (type(count) is not int or count<1
                or provider_state=='completed' and (actual!=requested or not actual)
                or actual is not None and actual!=requested):
            raise ValueError('Completed operation attribution or request count is unavailable')
        phases.append(dict(job=job,sequence=sequence,local_status=local,local_finished=finished,
            attempt=attempt,backend=backend,requested_model=requested,actual_model=actual,
            database_outcome=outcome,provider_state=provider_state,usage=usage,requests=count,
            input_binding=digest(payload),terminal_proof=terminal_proof,read_only=read_only,
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
        known_usage=totals,unknown_usage=any(p['provider_state'] in {'unknown','active'} for p in phases),
        execution_authorized=False)


def renewable(snapshot):
    """A local rejection is renewable only after every old generation is settled."""
    return bool(snapshot['phases']) and all(
        p['local_status'] in {'completed','failed'} and p['local_finished'] and
        p['provider_state'] in {'completed','interrupted','not_started'} and p['read_only'] and not p['execution_authorized'] for p in snapshot['phases'])


def hold_reason(snapshot):
    if any(p['provider_state']=='active' for p in snapshot['phases']):
        return 'Workbench is still finishing the previous read-only investigation. Its results and usage must settle before another request can start. Refresh this task for progress.'
    return 'Workbench cannot yet confirm the previous investigation’s final state and usage. It has held new generation to avoid duplicate work. Your report, scope choice and earlier plan are saved.'
