"""Recover completed read-only receipts after a local worker failure, without editing history."""
import hashlib
import json
import os
from uuid import UUID, uuid5

from workbench.planning_reconciliation import require
from workbench.real_execution import alive


def completed(a, j, i, binding, payload, proof):
    from workbench import investigation_recovery as recovery
    from workbench.planning_limits import request_bound

    roots = [recovery.ROOT.parent]
    inputs = recovery.ROOT.parent / 'ops/inputs.json'
    if inputs.exists():
        from pathlib import Path
        import re
        for name in recovery.private_json(inputs, 1 << 20)['evidence_roots']:
            p = Path(name)
            if (p.parent == recovery.ROOT.parent.parent and
                    re.fullmatch(r'everyday-outcome-[a-f0-9]{7,40}', p.name) and
                    p.resolve() == p and p.stat().st_uid == os.getuid() and not p.stat().st_mode & 0o077):
                roots.append(p)
    candidates = [(root, root/'release/artifacts/runs'/('planning-evidence-'+a['id'])/'events.jsonl')
                  for root in dict.fromkeys(roots)]
    candidates = [(root,p) for root,p in candidates if p.exists()]
    if not candidates:
        return None
    require(len(candidates) == 1, 'Completed investigation evidence is ambiguous')
    root, parent = candidates[0]
    events = recovery.private_json(parent, lines=True)
    aggregate = [e for e in events if e.get('phase') == 'payload-decoded' and
                 e.get('generation_state') == 'finished' and
                 e.get('usage', {}).get('status') == 'complete' and
                 type(e.get('usage', {}).get('requests_observed')) is int]
    if not aggregate:
        return None
    require(len(aggregate) == 1 and not alive(payload.get('scenario_generation_claim', {})),
            'Completed investigation is not settled')
    usage = aggregate[0]['usage']
    count = usage['requests_observed']
    require(1 <= count <= request_bound(payload.get('local_planning_receipt', {})),
            'Completed request count exceeds saved consent')
    followups = [e for e in events if e.get('phase') == 'source-followup']
    require(len(followups) == count-1 and all(
        e.get('turn') == n+1 and e.get('evidence_id') == str(uuid5(UUID(a['id']), 'committed-source-turn-'+str(n)))
        for n,e in enumerate(followups,1)), 'Completed request lineage changed')
    provider = root/'artifacts/codex-evidence'
    provider_events = [(p,recovery.private_json(p, lines=True)) for p in provider.glob('*.jsonl')]
    values, evidence, used = {}, [], set()
    for n in range(count):
        identity = a['id'] if n == 0 else str(uuid5(UUID(a['id']), 'committed-source-turn-'+str(n)))
        directory = root/'release/artifacts/runs'/('planning-evidence-'+identity)
        journal = directory/'events.jsonl'
        rows = recovery.private_json(journal, lines=True)
        received = [e for e in rows if e.get('phase') == 'provider-result']
        require(len(received) == 1 and received[0].get('provider') == 'codex' and
                received[0].get('generation_state') == 'finished' and
                received[0].get('accounting_state') == 'complete', 'Completed provider receipt unavailable')
        actual = received[0]['usage']['values']
        content = recovery.private_json(directory/'provider-content.json', 24000)
        require(content.get('truncated') is False and content.get('credential_redacted') is False and
                hashlib.sha256(content['text'].encode()).hexdigest() == content.get('original_sha256'),
                'Complete content identity unavailable')
        decoded = json.loads(content['text'])
        matches = []
        for path, terminal_events in provider_events:
            terminal = [e for e in terminal_events if e.get('phase') == 'turn-finished']
            if len(terminal) != 1:
                continue
            turn = terminal[0]
            if turn.get('status') != 'completed' or turn.get('usage') != actual:
                continue
            text = turn.get('output_excerpt', '')
            if (len(text.encode()) != turn.get('output_bytes') or
                    hashlib.sha256(text.encode()).hexdigest() != turn.get('output_sha256')):
                continue
            if json.loads(text) == decoded:
                matches.append((path, terminal_events))
        require(len(matches) == 1 and matches[0][0] not in used, 'Provider terminal identity missing or ambiguous')
        path, terminal_events = matches[0]
        used.add(path)
        turns = {(e.get('thread_id'),e.get('turn_id')) for e in terminal_events
                 if e.get('phase') in {'turn-started','lifecycle-turn'}}
        terminals = [e for e in terminal_events if e.get('phase') == 'lifecycle-terminal']
        require(len(turns) == 1 and terminals and all(e.get('status') == 'completed' and
            (e.get('thread_id'),e.get('turn_id')) in turns for e in terminals) and
            any(e.get('phase') == 'isolation-verified' and e.get('capabilities') == proof for e in terminal_events) and
            any(e.get('phase') == 'connection-closed' for e in terminal_events),
            'Completed provider isolation or lifecycle unavailable')
        require(set(actual) in ({'prompt_tokens','completion_tokens','total_tokens'},
            {'prompt_tokens','completion_tokens','total_tokens','reasoning_tokens'}) and
            all(type(v) is int and v >= 0 for v in actual.values()) and
            actual['prompt_tokens']+actual['completion_tokens'] <= actual['total_tokens'],
            'Completed usage malformed')
        for key,value in actual.items():
            values[key] = values.get(key,0)+value
        evidence.append(dict(planning=str(journal),planning_sha256=hashlib.sha256(journal.read_bytes()).hexdigest(),
            provider=str(path),provider_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    require(values == usage.get('values'), 'Aggregate accounting differs from terminal receipts')
    return dict(kind='standing-read-only-quarantine-v1',attempt=a['id'],intake=i['id'],job=j['id'],
        historical_binding=binding,authority_sha256=recovery.AUTHORITY,capability_proof=proof,
        completed_evidence=evidence,requests_started=count,known_completed_requests=count,
        known_completed_values=values,unknown_final_requests=0,provider_terminal_confirmed=True,
        final_outcome='completed',final_usage=values,partial_output_reusable=False,
        successful_cancellation=False,permits_fresh_task=True,old_task_fenced=True)
