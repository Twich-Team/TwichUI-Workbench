"""Record a definitively ended local planning response with partial TEXT.

No response is resumed, approved, executed or marked complete. Every HTTP usage
receipt is required; unknown execution/accounting and implementation remain held.
"""
import argparse
import copy
import hashlib
import json
import time
from pathlib import Path
from uuid import UUID, uuid5

from workbench import everyday
from workbench.db import engine_for, planning_jobs, write_transaction
from workbench.investigation_memory import read_journal
from workbench.planning_reconciliation import require, rows, probe_local
from workbench.trusted_preparation import configuration
from workbench.visual_scope import digest
from workbench.planning_limits import consent_cap, final_bound

KIND='ended-local-planning-partial-text-v1'
KEY='local_partial_planning_settlement'
MESSAGE=('The local planning response ended at its output limit. No approved plan or implementation exists. '
         'Its reported usage is recorded; another investigation requires fresh consent.')


def state(connection, identity):
    require(str(UUID(identity))==identity,'Invalid model identity')
    [attempt]=rows(connection,'SELECT * FROM model_attempts WHERE id=?',(identity,))
    require(attempt['source']=='workbench' and attempt['backend']=='local'
        and attempt['purpose']=='planning' and attempt['outcome']=='incomplete_output'
        and attempt['finished_at'] and attempt['job_id'],'Only ended partial local planning may be recorded')
    [job]=rows(connection,'SELECT * FROM planning_jobs WHERE id=?',(attempt['job_id'],))
    require(job['intake_id']==attempt['task_id'] and job['kind']=='planning'
        and job['status']=='failed' and job['finished_at'] and job['lease'] is None,
        'Planning must have ended without a live lease')
    [intake]=rows(connection,'SELECT * FROM intakes WHERE id=?',(attempt['task_id'],))
    require(intake['status']=='failed' and not rows(connection,'SELECT id FROM proposals WHERE intake_id=?',(intake['id'],))
        and len(rows(connection,'SELECT id FROM model_attempts WHERE task_id=?',(intake['id'],)))==1,
        'A proposal or another execution prevents this settlement')
    payload=json.loads(job['payload'])
    diag=payload.get('diagnostics',{})
    count=diag.get('investigation_requests')
    consent=payload.get('local_planning_receipt',{})
    require(diag.get('generation_state')=='partial' and diag.get('accounting_state')=='complete'
        and diag.get('finish_reason')=='length' and type(count) is int and 1<=count<=8
        and consent.get('investigation_version') in (4,5,6,7) and consent.get('max_local_requests')==8,
        'Partial output needs exact completed accounting and original local consent')
    claim=payload.get('scenario_generation_claim')
    require(isinstance(claim,dict) and type(claim.get('pid')) is int and claim['pid']>0
        and isinstance(claim.get('boot'),str) and isinstance(claim.get('start'),str) and claim['start'].isdigit(),
        'Planning ownership is missing')
    binding=digest(dict(attempt=attempt,intake_payload=intake['payload'],claim=claim,
                        diagnostics=diag,planning_receipt=consent))
    return attempt,job,payload,binding


def observations(engine, identity, count, cap=2600):
    roots=[Path(root) for root in everyday.configuration(engine).evidence_roots]
    totals=dict(prompt_tokens=0,completion_tokens=0,total_tokens=0)
    hashes, response_ids = [],set()
    for index in range(count):
        key=identity if index==0 else str(uuid5(UUID(identity),f'committed-source-turn-{index}'))
        paths=[root/'artifacts/runs'/('planning-evidence-'+key)/'events.jsonl' for root in roots]
        values=[read_journal(p) for p in paths if p.exists()]
        require(bool(values) and all(v==values[0] for v in values),'Missing or conflicting response journal')
        events=values[0]
        schema=[e for e in events if e['phase']=='request-schema']
        envelopes=[e for e in events if e['phase']=='envelope']
        require(len(schema)==len(envelopes)==1 and schema[0].get('request_number')==index+1,
                'Response request identity changed')
        envelope=envelopes[0]
        last=index+1==count
        require(envelope.get('generation_state')==('partial' if last else 'finished')
            and envelope.get('completion')==('length' if last else 'stop')
            and envelope.get('accounting_state')=='complete' and envelope.get('model_matches')==1,
            'Only a complete known response prefix and final length stop qualify')
        response=envelope.get('response_identity')
        require(isinstance(response,str) and len(response)==64 and response not in response_ids,
                'Repeated or missing provider response identity')
        response_ids.add(response)
        limits=[e['completion_limit'] for e in events if e['phase']=='request-limits']
        require(len(limits)<=1 and (not limits or type(limits[0]) is int and limits[0] in (2600,6144) and limits[0]<=cap), 'Request completion bound changed')
        limit=limits[0] if limits else 2600
        usage=envelope.get('usage',{}).get('values',{})
        require(set(usage)==set(totals) and all(type(v) is int and v>=0 for v in usage.values())
            and usage['prompt_tokens']+usage['completion_tokens']==usage['total_tokens']
            and usage['completion_tokens']<=limit,'Response usage is malformed or exceeds original bound')
        for key in totals:
            totals[key]+=usage[key]
        hashes.append(hashlib.sha256(json.dumps(events,sort_keys=True).encode()).hexdigest())
    return totals,hashes


def known_values(attempt, payload):
    diag=payload['diagnostics']
    stored=json.loads(attempt['usage']) if attempt['usage'] not in (None,'null') else None
    known=json.loads(diag['known_usage']) if diag.get('known_usage') is not None else (
        stored.get('values') if isinstance(stored,dict) else None)
    require(isinstance(known,dict) and set(known)=={'prompt_tokens','completion_tokens','total_tokens'}
        and all(type(v) is int and v>=0 for v in known.values())
        and known['prompt_tokens']+known['completion_tokens']==known['total_tokens']
        and known['completion_tokens']<=diag['investigation_requests']*consent_cap(payload.get('local_planning_receipt',{})),
        'Complete retained token accounting is required')
    if stored is not None:
        require(stored.get('status')=='complete' and type(stored.get('requests_observed')) is int
            and stored['requests_observed']==diag['investigation_requests']
            and digest(stored.get('values'))==digest(known),'Original usage differs from retained accounting')
    return known


def observed_accounting(count, totals):
    return dict(kind='observed-local-planning-receipts',units='tokens',values=totals,
        requests_started=count,complete_usage_receipts=count,complete_text_responses=count-1,
        partial_text_responses=1,unavailable_usage_requests=0,
        completion_tokens_upper_bound=totals['completion_tokens'])


def expected(connection, engine, identity):
    attempt,job,payload,binding=state(connection,identity)
    count=payload['diagnostics']['investigation_requests']
    totals,hashes=observations(engine,identity,count,consent_cap(payload.get('local_planning_receipt',{})))
    require(digest(known_values(attempt,payload))==digest(totals),
            'Observed journal usage differs from retained accounting')
    accounting=observed_accounting(count,totals)
    return attempt,job,payload,binding,hashes,accounting


def accounting(connection, identity, engine=None):
    attempt,job,payload,binding=state(connection,identity)
    result=observed_accounting(payload['diagnostics']['investigation_requests'],known_values(attempt,payload))
    receipt=payload.get(KEY,{})
    require(receipt.get('kind')==KIND and receipt.get('binding')==binding
        and receipt.get('attempt')==identity and receipt.get('job')==job['id']
        and receipt.get('intake')==attempt['task_id'] and receipt.get('model')==attempt['requested_model']
        and isinstance(receipt.get('response_journal_sha256'),list)
        and len(receipt['response_journal_sha256'])==result['requests_started']
        and all(isinstance(h,str) and len(h)==64 and set(h)<={'0','1','2','3','4','5','6','7','8','9','a','b','c','d','e','f'}
                for h in receipt['response_journal_sha256'])
        and receipt.get('external_execution_stopped') is True
        and receipt.get('output_complete') is False and receipt.get('no_approved_plan_or_execution') is True
        and type(receipt.get('server_task')) is int and receipt['server_task']>=0
        and receipt.get('snapshot')==dict(task=receipt['server_task'],idle=True,completion_limit=final_bound(payload))
        and digest(receipt.get('accounting'))==digest(result) and job['message']==MESSAGE,
        'Missing or stale ended-planning receipt')
    return result


def reconcile(engine, identity, server_task):
    configuration(engine)
    with engine.connect() as connection:
        before=expected(connection,engine,identity)
        if before[2].get(KEY):
            return accounting(connection,identity,engine)
    require(type(server_task) is int and server_task>=0,'Invalid server task')
    from workbench.planning_provider import local_slot
    with local_slot():
        limit=final_bound(before[2])
        snapshot=(probe_local(before[0]['requested_model'],server_task) if limit==2600 else
                  probe_local(before[0]['requested_model'],server_task,limit))
        require(snapshot==dict(task=server_task,idle=True,completion_limit=limit),'Exact local activity must be idle')
        receipt=dict(kind=KIND,binding=before[3],attempt=identity,job=before[1]['id'],
            intake=before[0]['task_id'],model=before[0]['requested_model'],server_task=server_task,
            snapshot=snapshot,external_execution_stopped=True,output_complete=False,
            no_approved_plan_or_execution=True,response_journal_sha256=before[4],accounting=before[5],
            recorded_at=time.time(),original_message=before[1]['message'],
            operator='same-user local diagnostic CLI; no execution or replay authority')
        changed=copy.deepcopy(before[2])
        changed[KEY]=receipt
        with write_transaction(engine) as connection:
            current=expected(connection,engine,identity)
            require(current==before,'Planning state changed during local metadata probe')
            connection.execute(planning_jobs.update().where(planning_jobs.c.id==before[1]['id']).values(
                payload=changed,message=MESSAGE))
            return accounting(connection,identity,engine)


def main():
    from sqlalchemy.exc import SQLAlchemyError
    from workbench.planning_transport import PlanningUnavailable
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('attempt')
    parser.add_argument('--server-task',type=int,required=True)
    args=parser.parse_args()
    try:
        result=reconcile(engine_for(),args.attempt,args.server_task)
    except (ValueError,OSError,KeyError,TypeError,PlanningUnavailable,SQLAlchemyError):
        print(json.dumps(dict(held=True,reason='Ended local planning checks failed; no work authorized.')))
        raise SystemExit(2) from None
    print(json.dumps(dict(settled=True,output_complete=False,accounting=result)))


if __name__=='__main__':
    main()
