"""Provider terminal evidence is independent of content parsing and local acceptance."""
import hashlib
import json
import os
from pathlib import Path
import stat
from uuid import UUID,uuid5


def _read(path, *, lines=False, bound=2 << 20):
    path=Path(path)
    info=path.lstat()
    if (path.resolve()!=path or not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid()
            or info.st_mode&0o077 or not 0<info.st_size<=bound):
        raise ValueError('Private terminal evidence is unavailable')
    raw=path.read_bytes()
    data=[json.loads(line) for line in raw.splitlines()] if lines else json.loads(raw)
    return data,hashlib.sha256(raw).hexdigest()


def completed_turns(identity,usage,evidence_roots):
    """Verify known complete turns; never infer terminal state from response content.

    Only private receipt identifiers/usage are returned. No model text, credentials,
    cancellation claim or execution permission is produced by this function.
    """
    if str(UUID(identity))!=identity or not isinstance(usage,dict) or usage.get('status')!='complete':
        raise ValueError('Complete investigation accounting is unavailable')
    count=usage.get('requests_observed',1)
    if type(count) is not int or not 1<=count<=256:
        raise ValueError('Invalid operation receipt count')
    roots=list(dict.fromkeys(Path(root) for root in evidence_roots))
    matches=[root for root in roots if (root/'release/artifacts/runs'/('planning-evidence-'+identity)/'events.jsonl').is_file()]
    if len(matches)!=1:
        raise ValueError('The operation receipt location is missing or ambiguous')
    root=matches[0]
    records=[]
    summed={}
    used=set()
    for index in range(count):
        phase=identity if index==0 else str(uuid5(UUID(identity),'committed-source-turn-'+str(index)))
        events,source_sha=_read(root/'release/artifacts/runs'/('planning-evidence-'+phase)/'events.jsonl',lines=True,bound=65536)
        results=[e for e in events if e.get('phase')=='provider-result']
        if len(results)!=1:
            raise ValueError('A provider result receipt is missing or ambiguous')
        result=results[0]
        receipt=result.get('provider_receipt')
        if (result.get('provider')!='codex' or result.get('generation_state')!='finished'
                or result.get('accounting_state')!='complete' or not isinstance(receipt,str)
                or str(UUID(receipt))!=receipt or receipt in used):
            raise ValueError('Provider completion is not established')
        used.add(receipt)
        terminal_events,terminal_sha=_read(root/'artifacts/codex-evidence'/(receipt+'.jsonl'),lines=True)
        finals=[e for e in terminal_events if e.get('phase')=='turn-finished']
        lifecycles=[e for e in terminal_events if e.get('phase')=='lifecycle-terminal']
        identities={(e.get('thread_id'),e.get('turn_id')) for e in terminal_events
                    if e.get('phase') in {'turn-started','lifecycle-turn'}}
        values=result.get('usage',{}).get('values')
        if (len(finals)!=1 or finals[0].get('status')!='completed' or not lifecycles
                or len(identities)!=1 or not all(all(x) for x in identities)
                or any(e.get('status')!='completed' or (e.get('thread_id'),e.get('turn_id')) not in identities for e in lifecycles)
                or not any(e.get('phase')=='connection-closed' for e in terminal_events)
                or not isinstance(values,dict) or finals[0].get('usage')!=values
                or not all(type(values.get(k)) is int and values[k]>=0
                           for k in ('prompt_tokens','completion_tokens','total_tokens'))
                or values['prompt_tokens']+values['completion_tokens']!=values['total_tokens']):
            raise ValueError('Known terminal state and usage do not agree')
        for key,value in values.items():
            if type(value) is not int or value<0:
                raise ValueError('Malformed reported usage')
            summed[key]=summed.get(key,0)+value
        thread,turn=next(iter(identities))
        records.append(dict(phase=phase,provider_receipt=receipt,thread=thread,turn=turn,
            outcome='completed',usage=values,source_sha256=source_sha,terminal_sha256=terminal_sha))
    if summed!=usage.get('values'):
        raise ValueError('Task accounting differs from provider terminal receipts')
    return dict(attempt=identity,provider_outcome='completed',turns=records,usage=summed,
                output_accepted=False,execution_authorized=False)


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
