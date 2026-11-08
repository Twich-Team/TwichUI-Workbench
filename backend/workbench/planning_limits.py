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
    version=followup.get('budget_version')
    if version is not None and (type(version) is not int or version not in (2,3,4)):
        raise ValueError('The continuation accounting version is unavailable')
    if version == 4:
        if (followup.get('operation_request_bound') != bound
                or type(followup.get('consumed_requests')) is not int
                or followup['consumed_requests'] < 1 or not followup.get('prior_phases')
                or not followup.get('source_scope') or not followup.get('renewal_consent')):
            raise ValueError('Fresh operation consent or preserved phase accounting is unavailable')
        return bound
    if version in (2,3):
        used,remaining=followup.get('consumed_requests'),followup.get('remaining_requests')
        if (type(used) is not int or type(remaining) is not int or not 1<=used<bound or
                used+remaining!=bound or remaining<=0 or followup.get('max_total_requests')!=bound):
            raise ValueError('The continuation request accounting changed')
        return remaining
    return bound


def followup_budget(connection, initial, revision=1):
    """Continue settled phases without resetting their operation safety bound."""
    phases=settled_planning_chain(connection,initial['intake_id'],revision)
    return continuation_budget(initial,phases[-1],phases)


def continuation_budget(initial,parent,phases=None):
    """Pure validation shared by the worker and independent persisted-result review."""
    from workbench.visual_scope import digest
    usage=parent['usage']
    bound=request_bound(initial['payload'].get('local_planning_receipt',{}))
    if (parent['outcome']!='completed' or not parent['finished_at'] or not isinstance(usage,dict)
            or usage.get('status')!='complete' or usage.get('units')!='tokens'):
        raise ValueError('The original investigation is not settled with complete accounting')
    phases=phases or [parent]
    counts=[phase['usage'].get('requests_observed',1) for phase in phases]
    if any(type(count) is not int or count<1 for count in counts):
        raise ValueError('The previous investigation request accounting is unavailable')
    used=sum(counts)
    if not 1<=used<bound:
        raise ValueError('The task has no remaining investigation requests')
    packet=initial['payload'].get('bug_inspection_packet')
    if not isinstance(packet,dict) or not packet.get('files') or not packet.get('base'):
        raise ValueError('The original source disclosure scope is unavailable')
    result=dict(budget_version=2 if len(phases)==1 else 3,parent_attempt=parent['id'],parent_usage_sha256=digest(usage),
        consumed_requests=used,remaining_requests=bound-used,max_total_requests=bound,
        source_scope=dict(base=packet['base'],files={f['path']:f['sha256'] for f in packet['files']}))
    if len(phases)>1:
        result['prior_attempts']=[dict(id=p['id'],usage_sha256=digest(p['usage']),
            requests=p['usage'].get('requests_observed',1)) for p in phases]
    return result


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
        # The existing single-turn regression connector records terminal outcome
        # and token values without the multi-turn investigation's status tag.
        # Accept that supported receipt only for the original non-investigation
        # phase, with complete, internally consistent reported token values.
        values=usage.get('values',{}) if isinstance(usage,dict) else {}
        values=values if isinstance(values,dict) else {}
        single_receipt=(rev==1 and not payload.get('local_planning_receipt',{}).get('investigation_version')
            and isinstance(usage,dict) and 'status' not in usage
            and all(type(values.get(k)) is int and values[k]>=0 for k in
                ('prompt_tokens','completion_tokens','total_tokens'))
            and values['total_tokens']>0
            and values['prompt_tokens']+values['completion_tokens']==values['total_tokens'])
        valid_values=(all(type(values.get(k)) is int and values[k]>=0 for k in
            ('prompt_tokens','completion_tokens','total_tokens')) and values['total_tokens']>0
            and values['prompt_tokens']+values['completion_tokens']==values['total_tokens'])
        if (not valid_values or status!='completed' or claims!=1 or outcome!='completed' or not finished
                or not isinstance(usage,dict) or not (usage.get('status')=='complete' or single_receipt)
                or usage.get('units')!='tokens' or not actual or actual!=requested):
            raise ValueError('The investigation phase is unsettled or lacks complete accounting')
        records.append(dict(id=attempt,job_id=job,revision=rev,payload=payload,usage=usage,
            outcome=outcome,finished_at=finished,backend=backend,actual_model=actual,requested_model=requested))
    if (not records or [r['revision'] for r in records]!=list(range(1,len(records)+1))
            or revision is not None and records[-1]['revision']!=revision):
        raise ValueError('The investigation phase history changed')
    first=records[0]
    for index,second in enumerate(records[1:],1):
        expected=continuation_budget(dict(payload=first['payload']),records[index-1],records[:index])
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
    count = usage.get('requests_observed', 1)
    if not planning and bug and count!=1:
        query='''SELECT g.snapshot FROM model_attempts a JOIN real_execution_requests r ON a.execution_id=r.id
                 JOIN real_execution_grants g ON r.grant_id=g.id WHERE a.id=?'''
        cursor=(connection.exec_driver_sql(query,(identity,)) if hasattr(connection,'exec_driver_sql')
                else connection.execute(query,(identity,)))
        row=cursor.fetchone()
        if not row:
            raise ValueError('The implementation operation grant is unavailable')
        grant=json.loads(row[0]) if isinstance(row[0],str) else row[0]
        spec=grant.get('task_budget',{}).get('offer',{}).get('snapshot',{}).get('specification',{})
        from workbench.task_budget import valid_contract
        if (not spec.get('source_context') or not valid_contract(spec.get('contract',{}))
                or spec['contract']!=grant.get('contract')):
            raise ValueError('Multiple implementation turns require the exact reviewed runtime')
        bound=spec['contract']['limits']['turns']
    total = usage.get('values', {}).get('total_tokens')
    limit = BUG_OBSERVED if bug else MAX_OBSERVED
    maximum = usage.get('max_request_total_tokens', total)
    if not (usage.get('units') == 'tokens' and type(count) is int and 1 <= count <= bound
            and type(total) is int and 0 < total <= limit*count
            and type(maximum) is int and 0 < maximum <= limit):
        raise ValueError('Codex observations exceed the saved operation or current post-observation guard')
    return count


def renewed_budget(connection, initial, *, exclude_job=None):
    """A fresh bounded operation preserves settled history instead of resetting it.

    This is explicit same-provider consent. It grants neither implementation nor
    permission to repeat an active, unknown or consequential operation.
    """
    from workbench import delivery_phase, everyday
    roots=everyday.configuration(connection.engine).evidence_roots
    snapshot=delivery_phase.observe(connection,initial['intake_id'],roots,exclude_job=exclude_job)
    if not delivery_phase.renewable(snapshot):
        raise ValueError('Previous generation is active, uncertain, consequential or lacks terminal evidence')
    phases=snapshot['phases']
    provider=phases[0]['backend']
    model=phases[0]['requested_model']
    if any(p['backend']!=provider or p['requested_model']!=model
            or p['actual_model'] is not None and p['actual_model']!=model for p in phases):
        raise ValueError('Renewal cannot silently switch the investigation provider')
    packet=initial['payload'].get('bug_inspection_packet')
    if not isinstance(packet,dict) or not packet.get('files') or not packet.get('base'):
        raise ValueError('The original committed disclosure scope is unavailable')
    return dict(budget_version=4,renewal_consent=True,
        operation_request_bound=request_bound(initial['payload']['local_planning_receipt']),
        consumed_requests=sum(p['requests'] for p in phases),
        known_prior_usage=snapshot['known_usage'],
        prior_phases=[{k:p[k] for k in ('job','sequence','attempt','backend','actual_model',
                       'database_outcome','provider_state','usage','input_binding')} for p in phases],
        next_sequence=snapshot['next_sequence'],
        source_scope=dict(base=packet['base'],files={f['path']:f['sha256'] for f in packet['files']}))
