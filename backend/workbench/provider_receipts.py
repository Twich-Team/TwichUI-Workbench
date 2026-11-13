"""Provider terminal evidence is independent of content parsing and local acceptance."""
import hashlib
import json
import os
from pathlib import Path
import stat
from uuid import UUID,uuid5


def request_state(identity, backend, requested, actual, outcome, usage, diagnostics,
                  capabilities, evidence_roots, *, local_status, local_finished, finished):
    """Shared interpretation of durable provider facts, never a state transition.

    Planning, investigation and feature adapters keep their existing records and
    dispatch owners. Content validity and token availability are separate from
    execution. Native coding recovery still requires its source/owner/side-effect
    proofs as well as the shared coding-accounting interpretation.
    """
    facts, caps = diagnostics, capabilities
    read_only = bool(caps) and all(caps.get(k) is False for k in
        ('source_writes','execution_dispatch','external_action_tools'))
    state, proof = 'unknown', None
    if local_status in {'queued','running'} or identity and not finished:
        state = 'active'
    elif identity and finished and backend == 'local' and facts.get('generation_state') == 'not_started' and facts.get('investigation_requests') == 0:
        proof = not_started_local_turn(identity,evidence_roots,facts)
        state = 'not_started'
    elif identity and finished and backend == 'local' and local_status == 'failed' and read_only:
        try:
            proof = settled_local_turns(identity,usage,evidence_roots,requested,diagnostics=facts)
        except (ValueError,OSError,KeyError,TypeError):
            pass
        else:
            state = proof['provider_outcome']
            actual = actual or proof['actual_model']
    elif (identity and finished and backend == 'codex' and read_only
            and (not isinstance(usage,dict) or usage.get('status') != 'complete')):
        try:
            proof = completed_turns(identity,usage,evidence_roots,allow_unavailable=True,
                capabilities=caps,requested_model=requested)
        except (ValueError,OSError,KeyError,TypeError):
            pass
        else:
            state = proof['provider_outcome']
    elif identity and finished and isinstance(usage,dict) and usage.get('status') == 'complete':
        if backend == 'codex':
            proof = (interrupted_turns(identity,usage,evidence_roots,facts,caps)
                if outcome == 'incomplete_output' and facts.get('terminal_status') == 'interrupted'
                else completed_turns(identity,usage,evidence_roots,allow_unavailable=read_only,
                    capabilities=caps,requested_model=requested))
            state = proof['provider_outcome']
        elif outcome == 'completed' or facts.get('generation_state') == 'finished' and facts.get('accounting_state') == 'complete':
            state = 'completed'
        elif backend == 'local' and facts.get('generation_state') == 'finished':
            proof = completed_local_turns(identity,usage,evidence_roots,requested,facts)
            state = proof['provider_outcome']
            actual = actual or proof['actual_model']
    if proof and proof.get('actual_model'):
        if actual and proof['actual_model'] != actual:
            raise ValueError('Native terminal model attribution changed')
        actual = actual or proof['actual_model']
    count = (0 if state == 'not_started' else proof['requests'] if proof and 'requests' in proof
             else usage.get('requests_observed',1) if isinstance(usage,dict) else None)
    if state in {'completed','interrupted'} and (type(count) is not int or count < 1
            or state == 'completed' and (actual != requested or not actual)
            or actual is not None and actual != requested):
        raise ValueError('Completed operation attribution or request count is unavailable')
    reasons = [r.get('finish_reason') for r in (proof or {}).get('records', [])]
    content_complete = (False if facts.get('generation_state') == 'partial' or 'length' in reasons
                        or facts.get('partial_output') is True else
                        True if state == 'completed' and local_status == 'completed' else None)
    return dict(attempt=identity, provider_state=state, actual_model=actual, requests=count,
        terminal_proof=proof, read_only=read_only, local_status=local_status, local_finished=local_finished,
        dispatch='not_started' if state == 'not_started' else 'observed' if proof or state == 'completed' else 'unresolved',
        transport='not_dispatched' if state == 'not_started' else
            'complete_response' if proof and proof.get('records') else
            'terminal_acknowledged' if proof else 'unresolved',
        cancellation_acknowledged=state == 'interrupted',
        cancellation_requested=facts.get('cancellation_requested') is True or state == 'interrupted',
        content_complete=content_complete,
        content_validity='accepted' if local_status == 'completed' and state == 'completed' else
            'rejected' if local_status == 'failed' and state == 'completed' else 'unvalidated',
        output_accepted=local_status == 'completed' and state == 'completed',
        possible_external_side_effects=not read_only, execution_authorized=False)


def safe_continuation(state):
    """Generation settlement cannot confer permission for consequential replay."""
    return (state['local_status'] in {'completed','failed'} and bool(state['local_finished'])
        and state['provider_state'] in {'completed','interrupted','not_started'}
        and state['read_only'] and not state['execution_authorized'])


def _read(path, *, lines=False, bound=2 << 20):
    path=Path(path)
    info=path.lstat()
    if (path.resolve()!=path or not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid()
            or info.st_mode&0o077 or not 0<info.st_size<=bound):
        raise ValueError('Private terminal evidence is unavailable')
    raw=path.read_bytes()
    data=[json.loads(line) for line in raw.splitlines()] if lines else json.loads(raw)
    return data,hashlib.sha256(raw).hexdigest()


def completed_turns(identity,usage,evidence_roots,*,allow_unavailable=False,capabilities=None,requested_model=None):
    """Verify known complete turns; never infer terminal state from response content.

    Only private receipt identifiers/usage are returned. No model text, credentials,
    cancellation claim or execution permission is produced by this function.
    Strict historical callers require their aggregate accounting. Derived reads
    can retain unavailable ledger/native usage alongside verified completion;
    they never replace the saved ledger with the observed subtotal.
    """
    complete = isinstance(usage,dict) and usage.get('status') == 'complete'
    if str(UUID(identity))!=identity or not complete and not allow_unavailable:
        raise ValueError('Complete investigation accounting is unavailable')
    roots=list(dict.fromkeys(Path(root) for root in evidence_roots))
    matches=[root for root in roots if (root/'release/artifacts/runs'/('planning-evidence-'+identity)/'events.jsonl').is_file()]
    if len(matches)!=1:
        raise ValueError('The operation receipt location is missing or ambiguous')
    root=matches[0]
    first,_=_read(root/'release/artifacts/runs'/('planning-evidence-'+identity)/'events.jsonl',lines=True,bound=65536)
    followups=[e for e in first if e.get('phase')=='source-followup']
    count=usage.get('requests_observed',1) if complete else len(followups)+1
    if (type(count) is not int or not 1<=count<=256 or not complete and
            ([e.get('turn') for e in followups] != list(range(2,count+1)) or
             any(e.get('evidence_id') != str(uuid5(UUID(identity),'committed-source-turn-'+str(index)))
                 for index,e in enumerate(followups,1)))):
        raise ValueError('Invalid operation receipt count')
    records=[]
    summed={}
    used=set()
    unknown=0
    models=set()
    for index in range(count):
        phase=identity if index==0 else str(uuid5(UUID(identity),'committed-source-turn-'+str(index)))
        events,source_sha=_read(root/'release/artifacts/runs'/('planning-evidence-'+phase)/'events.jsonl',lines=True,bound=65536)
        results=[e for e in events if e.get('phase')=='provider-result']
        held_projection=False
        if not results and allow_unavailable:
            stopped=[e for e in events if e.get('phase')=='request-stopped'
                     and e.get('provider_terminal_confirmed') is True and e.get('terminal_status')=='completed']
            if len(stopped)==1:
                results=[dict(provider='codex',generation_state='finished',accounting_state='unavailable',
                    provider_receipt=stopped[0].get('provider_receipt'),usage=None)]
                held_projection=True
        if len(results)!=1:
            raise ValueError('A provider result receipt is missing or ambiguous')
        result=results[0]
        receipt=result.get('provider_receipt')
        accounting = result.get('accounting_state')
        if (result.get('provider')!='codex' or result.get('generation_state')!='finished'
                or accounting not in ({'complete','unavailable'} if allow_unavailable else {'complete'}) or not isinstance(receipt,str)
                or str(UUID(receipt))!=receipt or receipt in used):
            raise ValueError('Provider completion is not established')
        used.add(receipt)
        terminal_events,terminal_sha=_read(root/'artifacts/codex-evidence'/(receipt+'.jsonl'),lines=True)
        finals=[e for e in terminal_events if e.get('phase')=='turn-finished']
        if held_projection:
            caps=capabilities or {}
            expected_code={'backend/workbench/codex_provider.py','scripts/codex_connector.py',
                           'backend/workbench/regression_preparation.py'}
            if (caps.get('kind')!='tool-free-codex-investigation-v1' or not requested_model
                    or not all(caps.get(k) is False for k in ('source_writes','execution_dispatch',
                        'external_action_tools','owner_config_mounted'))
                    or caps.get('empty_work_directory') is not True or caps.get('shell_disabled') is not True
                    or caps.get('sandbox')!='readOnly' or caps.get('approval_policy')!='never'
                    or set(caps.get('code',{}))!=expected_code
                    or not any(e.get('phase')=='isolation-verified' and e.get('capabilities')==caps for e in terminal_events)):
                raise ValueError('Completed held response has no verified tool-free capability binding')
            for name,sha in caps['code'].items():
                path=root/'release'/name
                if (path.resolve()!=path or path.stat().st_uid!=os.getuid() or path.stat().st_mode&0o022
                        or not stat.S_ISREG(path.stat().st_mode) or not 0<path.stat().st_size<=1<<20
                        or hashlib.sha256(path.read_bytes()).hexdigest()!=sha):
                    raise ValueError('Completed held response transport code changed')
            if not finals:
                held=[e for e in terminal_events if e.get('phase')=='turn-held'
                      and e.get('status')=='terminal' and e.get('terminal_status')=='completed']
                if len(held)==1:
                    finals=[dict(held[0],status='completed')]
            if len(finals)==1:
                results[0].update(usage=dict(values=finals[0].get('usage')),
                    accounting_state='complete' if finals[0].get('usage') is not None else 'unavailable',
                    actual_model=requested_model)
                result=results[0]
        lifecycles=[e for e in terminal_events if e.get('phase')=='lifecycle-terminal']
        identities={(e.get('thread_id'),e.get('turn_id')) for e in terminal_events
                    if e.get('phase') in {'turn-started','lifecycle-turn'}}
        values=(result.get('usage') or {}).get('values')
        if (len(finals)!=1 or finals[0].get('status')!='completed' or not lifecycles
                or len(identities)!=1 or not all(all(x) for x in identities)
                or any(e.get('status')!='completed' or (e.get('thread_id'),e.get('turn_id')) not in identities for e in lifecycles)
                or not any(e.get('phase')=='connection-closed' for e in terminal_events)):
            raise ValueError('Known terminal state and usage do not agree')
        native=finals[0].get('usage')
        if native is None and values is None and allow_unavailable and not complete and accounting == 'unavailable':
            unknown+=1
        else:
            if (not isinstance(native,dict) or values != native or not all(
                    type(native.get(k)) is int and native[k]>=0 for k in
                    ('prompt_tokens','completion_tokens','total_tokens')) or
                    native['prompt_tokens']+native['completion_tokens']!=native['total_tokens']):
                raise ValueError('Known terminal state and usage do not agree')
            for key,value in native.items():
                if type(value) is not int or value<0:
                    raise ValueError('Malformed reported usage')
                summed[key]=summed.get(key,0)+value
        if result.get('actual_model'):
            models.add(result['actual_model'])
        thread,turn=next(iter(identities))
        records.append(dict(phase=phase,provider_receipt=receipt,thread=thread,turn=turn,
            outcome='completed',usage=values,source_sha256=source_sha,terminal_sha256=terminal_sha))
    if complete and summed!=usage.get('values'):
        raise ValueError('Task accounting differs from provider terminal receipts')
    if allow_unavailable and len(models)>1:
        raise ValueError('Native terminal models disagree')
    return dict(attempt=identity,provider_outcome='completed',turns=records,usage=summed,
                output_accepted=False,execution_authorized=False,
                **(dict(requests=count,known_usage=summed,unknown_usage_requests=unknown,
                    historical_usage_unavailable=not complete,
                    actual_model=next(iter(models)) if len(models)==1 else None) if allow_unavailable else {}))


def interrupted_turns(identity, usage, evidence_roots, diagnostics, capabilities):
    """Prove a completed prefix and confirmed interruption, never accept its output.

    Unlike read-only quarantine this resolves no unknown state. A matching terminal
    identity, reported usage, acknowledged interrupt, verified confinement and closed
    producer are all required. Historical outcomes and attribution remain unchanged.
    """
    receipt=diagnostics.get('provider_receipt')
    if (str(UUID(identity))!=identity or not isinstance(receipt,str) or str(UUID(receipt))!=receipt
            or diagnostics.get('provider_terminal_confirmed') is not True
            or diagnostics.get('terminal_status')!='interrupted'
            or usage.get('status')!='complete' or usage.get('units')!='tokens'
            or capabilities.get('kind')!='tool-free-codex-investigation-v1'
            or not all(capabilities.get(k) is False for k in
                       ('source_writes','execution_dispatch','external_action_tools','owner_config_mounted'))
            or capabilities.get('empty_work_directory') is not True
            or capabilities.get('shell_disabled') is not True
            or capabilities.get('sandbox')!='readOnly' or capabilities.get('approval_policy')!='never'):
        raise ValueError('Known read-only interruption is not established')
    matches=[Path(root)/'artifacts/codex-evidence'/(receipt+'.jsonl')
             for root in dict.fromkeys(evidence_roots)
             if (Path(root)/'artifacts/codex-evidence'/(receipt+'.jsonl')).is_file()]
    if len(matches)!=1:
        raise ValueError('Interrupted receipt location is missing or ambiguous')
    events,source_sha=_read(matches[0],lines=True)
    terminals=[e for e in events if e.get('phase')=='lifecycle-terminal']
    held=[e for e in events if e.get('phase')=='turn-held']
    identities={(e.get('thread_id'),e.get('turn_id')) for e in events
                if e.get('phase') in {'turn-started','lifecycle-turn'}}
    if (len(terminals)!=1 or terminals[0].get('status')!='interrupted'
            or len(identities)!=1 or not all(all(x) for x in identities)
            or (terminals[0].get('thread_id'),terminals[0].get('turn_id')) not in identities
            or len(held)!=1 or held[0].get('terminal_status')!='interrupted'
            or not any(e.get('phase')=='interrupt-result' and e.get('acknowledged') is True
                       and e.get('status')=='interrupted' for e in events)
            or not any(e.get('phase')=='connection-closed' for e in events)
            or not any(e.get('phase')=='isolation-verified' and e.get('capabilities')==capabilities for e in events)):
        raise ValueError('Interrupted lifecycle or isolation is unverified')
    values=terminals[0].get('usage')
    aggregate=usage.get('values')
    count=usage.get('requests_observed')
    if (not isinstance(values,dict) or held[0].get('usage')!=values
            or not isinstance(aggregate,dict) or set(values)!=set(aggregate)
            or type(count) is not int or not 1<=count<=256
            or diagnostics.get('investigation_requests')!=count
            or any(type(v) is not int or v<0 or type(aggregate[k]) is not int or aggregate[k]<v for k,v in values.items())
            or values.get('prompt_tokens',-1)+values.get('completion_tokens',-1)!=values.get('total_tokens')):
        raise ValueError('Interrupted accounting does not match the terminal receipt')
    prefix={k:aggregate[k]-v for k,v in values.items()}
    proof=completed_turns(identity,dict(usage,requests_observed=count-1,values=prefix),evidence_roots) if count>1 else None
    if count==1 and any(prefix.values()):
        raise ValueError('Unattributed interrupted accounting')
    return dict(attempt=identity,provider_outcome='interrupted',completed_prefix=proof,
                final_receipt=receipt,terminal_sha256=source_sha,usage=aggregate,
                output_accepted=False,execution_authorized=False)


def interrupted_check_construction(identity, root, evidence_roots):
    """Standing read-only policy: isolate a confirmed interruption with lost usage.

    This never changes the original model-attempt row. The caller must fence the
    failed preparation, check current scope/absence of implementation, and append
    this proof to its durable history before starting a different claim directory.
    Historical builds omitted the final receipt identifier; only an unambiguous
    receipt inside the exact stopped request's private time interval can recover it.
    """
    from datetime import datetime
    from workbench.investigation_recovery import AUTHORITY,capabilities
    from workbench.visual_scope import digest
    root=Path(root)
    roots=list(dict.fromkeys(Path(p) for p in evidence_roots))
    locations=[p for p in roots if root.parent==p/'release/artifacts/runs/preparations']
    if len(locations)!=1 or str(UUID(identity))!=identity:
        raise ValueError('Construction evidence location is unavailable')
    stage=locations[0]
    claim,claim_sha=_read(root/'check-construction-claim.json',bound=65536)
    result,result_sha=_read(root/'check-construction-result.json',bound=65536)
    safe=dict(source_writes=False,execution_dispatch=False,external_action_tools=False)
    if (claim.get('id')!=identity or claim.get('capabilities')!=safe
            or result.get('id')!=identity or result.get('content_accepted') is not False
            or result.get('candidate_count')!=0 or result.get('usage') is not None
            or (root/'prepared-bug-checks.json').exists()):
        raise ValueError('Read-only construction isolation is unverified')
    authority,authority_sha=_read_authority(stage)
    if authority_sha!=AUTHORITY:
        raise ValueError('Standing recovery authority changed')
    known=json.loads(result.get('known_usage') or '{}')
    prefix=[]
    stopped=None
    for index in range(16):
        phase=identity if index==0 else str(uuid5(UUID(identity),'committed-source-turn-'+str(index)))
        path=stage/'release/artifacts/runs'/('planning-evidence-'+phase)/'events.jsonl'
        if not path.exists():
            break
        events,events_sha=_read(path,lines=True,bound=65536)
        stop=[e for e in events if e.get('phase')=='request-stopped']
        if stop:
            starts=[e for e in events if e.get('phase')=='request-schema']
            if stopped is not None or len(stop)!=1 or len(starts)!=1:
                raise ValueError('Stopped request binding is ambiguous')
            stopped=(index,starts[0],stop[0],events_sha)
        else:
            prefix.append(index)
    if stopped is None or prefix!=list(range(stopped[0])) or not known:
        raise ValueError('Completed prefix and stopped request are unavailable')
    count,start,end,stopped_sha=stopped
    proof=completed_turns(identity,dict(status='complete',units='tokens',requests_observed=count,values=known),roots)
    begin,finish=(datetime.fromisoformat(e['time']) for e in (start,end))
    if not begin.tzinfo or not finish.tzinfo or not begin<finish:
        raise ValueError('Stopped request interval is invalid')
    candidates=[]
    for path in (stage/'artifacts/codex-evidence').glob('*.jsonl'):
        if not begin.timestamp()-1<=path.stat().st_mtime<=finish.timestamp()+1:
            continue
        events,sha=_read(path,lines=True)
        requests=[e for e in events if e.get('phase')=='request']
        if len(requests)==1 and begin<=datetime.fromisoformat(requests[0]['time'])<=finish:
            candidates.append((path,events,sha))
    if len(candidates)!=1:
        raise ValueError('Final provider receipt is missing or ambiguous')
    path,events,sha=candidates[0]
    receipt=path.stem
    supplied=end.get('provider_receipt') or result.get('diagnostics',{}).get('provider_receipt')
    if supplied is not None and supplied!=receipt:
        raise ValueError('Retained final receipt differs')
    identities={(e.get('thread_id'),e.get('turn_id')) for e in events
                if e.get('phase') in {'turn-started','lifecycle-turn'}}
    terminals=[e for e in events if e.get('phase')=='lifecycle-terminal']
    held=[e for e in events if e.get('phase')=='turn-held']
    isolation=[e.get('capabilities') for e in events if e.get('phase')=='isolation-verified']
    actual=capabilities()
    if (len(identities)!=1 or not all(all(v) for v in identities)
            or len(terminals)!=1 or terminals[0].get('status')!='interrupted'
            or (terminals[0].get('thread_id'),terminals[0].get('turn_id')) not in identities
            or terminals[0].get('usage') is not None or len(held)!=1 or held[0].get('usage') is not None
            or held[0].get('terminal_status')!='interrupted'
            or not any(e.get('phase')=='interrupt-result' and e.get('acknowledged') is True
                       and e.get('status')=='interrupted' for e in events)
            or not any(e.get('phase')=='connection-closed' for e in events)
            or len(isolation)!=1 or not isinstance(isolation[0],dict)
            or {k:v for k,v in isolation[0].items() if k!='code'}!={k:v for k,v in actual.items() if k!='code'}):
        raise ValueError('Final interruption, supported cancellation or actual isolation is unverified')
    code=isolation[0].get('code',{})
    if set(code)!=set(actual['code']) or any(hashlib.sha256((stage/'release'/p).read_bytes()).hexdigest()!=v for p,v in code.items()):
        raise ValueError('Original confinement implementation changed')
    return dict(kind='standing-read-only-check-quarantine-v1',attempt=identity,
        authority_sha256=authority_sha,claim_sha256=claim_sha,result_sha256=result_sha,
        stopped_request_sha256=stopped_sha,completed_prefix=proof,final_receipt=receipt,
        terminal_sha256=sha,capabilities_sha256=digest(isolation[0]),
        final_outcome='interrupted',final_usage=None,unknown_usage_requests=1,
        output_reused=False,execution_authorized=False,requires_fresh_claim=True)


def _read_authority(stage):
    path=stage/'release/docs/OWNER_READONLY_RECOVERY_20261108.md'
    if path.resolve()!=path or not path.is_file():
        raise ValueError('Recovery authority unavailable')
    return path.read_bytes(),hashlib.sha256(path.read_bytes()).hexdigest()


def recorded_check_quarantine(connection, identity, evidence_roots):
    """Independent acceptance reader; requires the application's committed fence."""
    row=connection.execute("SELECT job_id,backend,purpose,outcome,usage,finished_at,execution_id FROM model_attempts WHERE id=?",(identity,)).fetchone()
    if (not row or tuple(row[1:4])!=('codex','check_preparation','interrupted_unknown')
            or row[4] not in (None,'null') or not row[5] or row[6] is not None):
        raise ValueError('Not a quarantined read-only check operation')
    job=connection.execute('SELECT payload FROM planning_jobs WHERE id=?',(row[0],)).fetchone()
    payload=json.loads(job[0])
    records=[item for item in payload.get('check_preparation_history',[])
             if item.get('construction',{}).get('id')==identity and item.get('output_reused') is False]
    if len(records)!=1 or records[0]['claim']['root']==payload.get('preparation_claim',{}).get('root'):
        raise ValueError('The old construction claim is not fenced from fresh work')
    proof=interrupted_check_construction(identity,records[0]['claim']['root'],evidence_roots)
    if not any(p.get('attempt')==identity and p.get('read_only_quarantine')==proof for p in records[0].get('settlement',[])):
        raise ValueError('The application has not recorded this exact disposition')
    return dict(proof,requests_started=len(proof['completed_prefix']['turns'])+1)


def completed_local_turns(identity, usage, evidence_roots, requested_model, diagnostics):
    """Recover terminal match/usage facts from local envelopes, never plan content."""
    if (str(UUID(identity))!=identity or not isinstance(usage,dict) or usage.get('status')!='complete'
            or diagnostics.get('generation_state')!='finished'):
        raise ValueError('Complete local response accounting is unavailable')
    count=usage.get('requests_observed')
    if type(count) is not int or not 1<=count<=16 or not requested_model:
        raise ValueError('Local response attribution is unavailable')
    roots=list(dict.fromkeys(Path(p) for p in evidence_roots))
    matches=[p for p in roots if (p/'release/artifacts/runs'/('planning-evidence-'+identity)/'events.jsonl').is_file()]
    if len(matches)!=1:
        raise ValueError('Local receipt owner is missing or ambiguous')
    root=matches[0]
    summed={k:0 for k in ('prompt_tokens','completion_tokens','total_tokens')}
    records=[]
    seen=set()
    for index in range(count):
        phase=identity if index==0 else str(uuid5(UUID(identity),'committed-source-turn-'+str(index)))
        rows,sha=_read(root/'release/artifacts/runs'/('planning-evidence-'+phase)/'events.jsonl',lines=True,bound=65536)
        envelopes=[x for x in rows if x.get('phase')=='envelope']
        if len(envelopes)!=1:
            raise ValueError('Local terminal envelope is missing or duplicated')
        e=envelopes[0]
        values=e.get('usage',{}).get('values',{})
        if (e.get('generation_state')!='finished' or e.get('completion')!='stop'
                or e.get('accounting_state')!='complete' or e.get('model_matches')!=1
                or not e.get('response_identity') or e['response_identity'] in seen
                or any(type(values.get(k)) is not int or values[k]<0 for k in summed)
                or values['prompt_tokens']+values['completion_tokens']!=values['total_tokens']):
            raise ValueError('Local terminal state or usage is not established')
        seen.add(e['response_identity'])
        for k in summed:
            summed[k]+=values[k]
        records.append(dict(phase=phase,source_sha256=sha,response_identity=e['response_identity']))
    if summed!=usage.get('values'):
        raise ValueError('Local terminal usage differs from recorded accounting')
    # A later preflight rejection is not another generation. Require its own
    # explicit non-dispatch receipt, rather than inferring it from blank text.
    if diagnostics.get('stopped_request_generation_state')=='not_started':
        phase=str(uuid5(UUID(identity),'committed-source-turn-'+str(count)))
        rows,sha=_read(root/'release/artifacts/runs'/('planning-evidence-'+phase)/'events.jsonl',lines=True,bound=65536)
        if (not any(x.get('phase')=='request-rejected' and x.get('generation_state')=='not_started' for x in rows)
                or any(x.get('phase') in {'wire-received','envelope'} for x in rows)):
            raise ValueError('The stopped local request is not a verified preflight rejection')
        records.append(dict(non_dispatch=phase,source_sha256=sha))
    return dict(provider_outcome='completed',actual_model=requested_model,model_observation='Every retained envelope affirmed equality to the saved requested model.',records=records,usage=summed,database_records_unchanged=True)


def not_started_local_turn(identity, evidence_roots, diagnostics):
    """Explicit adapter non-dispatch state plus its private stop event; never blank-content inference."""
    if (str(UUID(identity))!=identity or diagnostics.get('generation_state')!='not_started'
            or type(diagnostics.get('investigation_requests')) is not int or diagnostics.get('investigation_requests')!=0
            or diagnostics.get('stopped_request_generation_state')!='not_started'):
        raise ValueError('Local non-dispatch is not established')
    roots=list(dict.fromkeys(Path(p) for p in evidence_roots))
    paths=[p/'release/artifacts/runs'/('planning-evidence-'+identity)/'events.jsonl' for p in roots]
    paths=[p for p in paths if p.is_file()]
    if len(paths)!=1:
        raise ValueError('Local stop evidence is missing or ambiguous')
    events,sha=_read(paths[0],lines=True,bound=65536)
    stops=[e for e in events if e.get('phase')=='request-stopped']
    if (len(stops)!=1 or stops[0].get('generation_state')!='not_started'
            or stops[0].get('category')!=diagnostics.get('stopped_request_category')
            or any(e.get('phase') in {'wire-received','envelope','provider-result'} for e in events)):
        raise ValueError('Local stop does not prove non-dispatch')
    return dict(provider_outcome='not_started',requests=0,usage_observation=None,source_sha256=sha,database_records_unchanged=True)


def settled_local_turns(identity, usage, evidence_roots, requested_model, *, diagnostics=None):
    """A final HTTP envelope settles execution independently of text and tokens.

    LocalHTTP closes its bounded non-streaming response before recording the
    envelope. A disconnect/closed socket alone is never terminal evidence.
    Historical accounting is returned unchanged; absent receipts stay unknown.
    """
    identity = str(UUID(identity))
    roots = list(dict.fromkeys(Path(p) for p in evidence_roots))
    matches = {folder/('planning-evidence-'+identity)/'events.jsonl' for p in roots
               for folder in (p/'release/artifacts/runs', p/'artifacts/runs')
               if (folder/('planning-evidence-'+identity)/'events.jsonl').is_file()}
    if len(matches) != 1 or not requested_model:
        raise ValueError('Local receipt owner is missing or ambiguous')
    first_path = next(iter(matches))
    runs = first_path.parent.parent
    first, _ = _read(first_path, lines=True, bound=65536)
    followups = [e for e in first if e.get('phase') == 'source-followup']
    count = len(followups)+1
    if count > 16 or [e.get('turn') for e in followups] != list(range(2, count+1)):
        raise ValueError('Local request sequence is unavailable')
    totals = dict(prompt_tokens=0, completion_tokens=0, total_tokens=0)
    records, seen, unknown = [], set(), 0
    dispatched = count
    for index in range(count):
        phase = identity if index == 0 else str(uuid5(UUID(identity), 'committed-source-turn-'+str(index)))
        if index and followups[index-1].get('evidence_id') != phase:
            raise ValueError('Local request identity changed')
        events, sha = _read(runs/('planning-evidence-'+phase)/'events.jsonl', lines=True, bound=65536)
        schemas = [e for e in events if e.get('phase') == 'request-schema']
        envelopes = [e for e in events if e.get('phase') == 'envelope']
        wires = [e for e in events if e.get('phase') == 'wire-received']
        single_schema = [e for e in events if e.get('phase') == 'schema' and
                         e.get('model') in {'PlanningOutput','FeatureDecomposition'}]
        identified = (len(schemas) == 1 and schemas[0].get('request_number') == index+1
                      or count == 1 and not schemas and len(single_schema) == 1)
        # A prepared follow-up can stop before dispatch after a completed prefix.
        # Its own native stop receipt is required; blank/missing output is not
        # sufficient. A late wire/result invalidates the non-dispatch proof.
        facts = diagnostics or {}
        if index == count-1 and index and facts.get('stopped_request_generation_state') == 'not_started':
            stops = [e for e in events if e.get('phase') == 'request-stopped']
            if (not identified or len(stops) != 1 or stops[0].get('generation_state') != 'not_started'
                    or stops[0].get('category') != facts.get('stopped_request_category')
                    or facts.get('investigation_requests') != index
                    or any(e.get('phase') in {'wire-received','envelope','provider-result'} for e in events)):
                raise ValueError('The final prepared follow-up has no verified non-dispatch receipt')
            records.append(dict(non_dispatch=phase,source_sha256=sha))
            dispatched -= 1
            continue
        if (not identified
                or len(envelopes) != 1 or len(wires) != 1 or wires[0].get('bounded_prefix') is not False):
            raise ValueError('Local final response is missing or incomplete')
        end = envelopes[0]
        response = end.get('response_identity')
        if (end.get('completion') not in {'stop', 'length', 'content_filter'}
                or end.get('model_matches') != 1 or not isinstance(response, str)
                or len(response) != 64 or response in seen
                or end.get('generation_state') != ('partial' if end['completion'] == 'length' else 'finished')):
            raise ValueError('Local final response identity or finish reason is unverified')
        seen.add(response)
        values = (end.get('usage') or {}).get('values')
        valid = (isinstance(values, dict) and set(values) == set(totals)
                 and all(type(v) is int and v >= 0 for v in values.values())
                 and values['prompt_tokens']+values['completion_tokens'] == values['total_tokens'])
        if valid and end.get('accounting_state') == 'complete':
            for key in totals:
                totals[key] += values[key]
        else:
            unknown += 1
        records.append(dict(phase=phase, source_sha256=sha, response_identity=response,
                            finish_reason=end['completion'], usage=values if valid else None))
    if isinstance(usage, dict) and usage.get('status') == 'complete':
        if unknown or usage.get('values') != totals or usage.get('requests_observed') != dispatched:
            raise ValueError('Local final receipts disagree with saved accounting')
    return dict(provider_outcome='completed', actual_model=requested_model, requests=dispatched,
                records=records, known_usage=totals, unknown_usage_requests=unknown,
                historical_usage_unavailable=not isinstance(usage, dict) or usage.get('status') != 'complete',
                database_records_unchanged=True, output_accepted=False, execution_authorized=False)


def settled_coding_accounting(row):
    """Unknown local generation usage stays unknown after proven epoch termination.

    This never covers active/uncertain effects or unknown separately billed usage.
    The native failed outcome, no late tool dispatch and exact source are retained.
    """
    from pathlib import Path
    from workbench.repair_continuation import private_json
    usage=(row['result'] or {}).get('usage',{})
    if usage.get('status')=='complete':
        return True
    if usage.get('status') not in {'partial','unavailable'} or row['grant_snapshot']['runtime'].get('codex_selection'):
        return False
    try:
        from workbench.local_generation_recovery import absent_epoch
        from workbench.real_workspace import Runtime,tree
        from workbench import real_execution as execution
        root=Path(row['grant_snapshot']['recovery']['root'])
        proof=private_json(root/'terminated-generation.json')
        runtime=Runtime(root)
        return (proof['execution_id']==row['id'] and proof['fence']==row['fence']
            and proof['outcome']=='failed' and proof['generation_final_outcome']=='unknown'
            and proof['final_usage']=='unknown' and proof['partial_response_dispatched'] is False
            and proof['provider_epoch_terminated'] is True and absent_epoch(proof['terminated_epoch'])
            and runtime.owns_recovery(dict(snapshot=row['grant_snapshot']))
            and proof['source_tree_sha256']==execution.digest(tree(runtime.workspace))
            and runtime.failure_evidence(row['id'],row['fence'])['usage']==usage)
    except (OSError, ValueError, KeyError, TypeError):
        return False
