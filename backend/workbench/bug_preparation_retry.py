"""Fresh check construction after proved terminal failure; no implementation grant."""
import json
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select

from workbench import task_execution,provider_routes,everyday
from workbench.db import model_attempts,planning_jobs,task_run_offers,task_sessions
from workbench.provider_receipts import completed_turns
from workbench.visual_scope import digest


def require(value,message):
    if not value:
        raise HTTPException(409,message)


def request_in(c,job,body):
    require(body.command_id and body.review_binding,'A fresh check-construction command and exact scope binding are required.')
    command=str(body.command_id)
    signature=digest(body.model_dump(mode='json'))
    payload=dict(job['payload'])
    commands=payload.get('check_retry_commands',{})
    if command in commands:
        require(commands[command]==signature,'This command already belongs to another check-construction request.')
        return
    require(job['kind']=='behavioral_preparation' and job['status']=='failed' and job['finished_at'] and
            payload.get('preparation_result',{}).get('status')=='hold','Check construction must be terminal before renewal.')
    linked=task_execution.snapshot(c,**payload['ref'])
    reviewed=linked['proposal_review']['task_contract']
    require(reviewed.get('bug_intent') and linked['proposal_review']['binding']==body.review_binding,
            'Current approved behavior scope is required; no scope change is authorized by renewal.')
    require(not c.execute(select(task_run_offers.c.id).where(task_run_offers.c.proposal_id==linked['proposal_review']['id'])).first()
            and not c.execute(select(task_sessions.c.id).where(task_sessions.c.task_id==payload['ref']['task_id'])).first(),
            'An offered or dispatched implementation cannot renew check construction.')
    from workbench.trusted_preparation import validate_reviewed_policy
    validate_reviewed_policy(c.engine,reviewed)
    selection=provider_routes.run_selection(c,linked)
    provider='codex' if selection else 'local'
    model=selection['model'] if selection else everyday.local_selection(c).model
    roots=everyday.configuration(c.engine).evidence_roots
    attempts=list(c.execute(select(model_attempts).where(model_attempts.c.job_id==job['id'],
        model_attempts.c.purpose=='check_preparation')).mappings())
    require(attempts,'Retained check-construction accounting is required.')
    proofs=[]
    for attempt in attempts:
        usage=attempt['usage']
        require(attempt['source']=='workbench' and attempt['execution_id'] is None
                and attempt['backend']==provider and attempt['requested_model']==model and attempt['actual_model']==model
                and attempt['finished_at'] and attempt['outcome'] in {'completed','failed'}
                and isinstance(usage,dict) and usage.get('status')=='complete','Previous check activity or its accounting is unsettled.')
        if provider=='codex':
            try:
                proof=completed_turns(attempt['id'],usage,roots)
            except (ValueError,OSError,KeyError,TypeError):
                raise HTTPException(409,'Independent provider terminal receipts are unavailable.') from None
            proofs.append(dict(attempt=attempt['id'],terminal_proof_sha256=digest(proof)))
        else:
            require(usage.get('units')=='tokens' and type(usage.get('requests_observed')) is int
                    and usage['requests_observed']>0,'Previous local check accounting is unavailable.')
            records=[*payload.get('check_preparation_history',[]),dict(
                claim=payload.get('preparation_claim'),construction=payload.get('check_construction'))]
            matched=[item for item in records if item.get('construction',{}).get('id')==attempt['id']]
            require(len(matched)==1,'Local construction ownership is unavailable.')
            from workbench.trusted_preparation import owned
            try:
                path=owned(Path(matched[0]['claim']['root'])/'check-construction-result.json')
                require(path.is_file() and not path.stat().st_mode&0o077 and path.stat().st_size<=65536,
                        'Local construction receipt is unavailable.')
                result=json.loads(path.read_text())
            except (ValueError,OSError,KeyError,TypeError):
                raise HTTPException(409,'Private local construction receipt is unavailable.') from None
            require(result.get('id')==attempt['id'] and result.get('generation_state')=='finished'
                    and result.get('usage')==usage,'Local construction is not definitively settled.')
            proofs.append(dict(attempt=attempt['id'],usage_sha256=digest(usage),receipt_sha256=digest(result)))
    if everyday.enabled():
        everyday.require_room(everyday.configuration(c.engine))
    history=list(payload.get('check_preparation_history',[]))
    history.append(dict(attempts=job['attempts'],finished_at=job['finished_at'],message=job['message'],
        claim=payload.get('preparation_claim'),result=payload.get('preparation_result'),
        construction=payload.get('check_construction'),settlement=proofs,output_reused=False))
    renewed={k:v for k,v in payload.items() if k not in {'preparation_claim','preparation_result','check_construction','recovery'}}
    renewed.update(check_preparation_history=history,check_retry_commands={**commands,command:signature},
                   preparation_round_id=str(uuid4()))
    c.execute(planning_jobs.update().where(planning_jobs.c.id==job['id']).values(status='queued',finished_at=None,
        payload=renewed,message='A fresh bounded check-construction operation is queued with the same approved scope and provider. No repair is authorized.'))
