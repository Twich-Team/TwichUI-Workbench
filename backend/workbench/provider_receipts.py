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
