"""Fresh check construction after proved terminal failure; no implementation grant."""
import json
from pathlib import Path
from uuid import UUID,uuid4,uuid5

from fastapi import HTTPException
from sqlalchemy import select

from workbench import task_execution,provider_routes,everyday
from workbench.db import model_attempts,planning_jobs,task_run_offers,task_sessions
from workbench.provider_receipts import completed_turns
from workbench.visual_scope import digest


def require(value,message):
    if not value:
        raise HTTPException(409,message)


def settled_feedback_failure(job,linked,roots):
    """Read-only disposition of a contained original-trial feedback crash.

    No synthetic hold is written. A stopped owner, complete confined trials and
    the normal native settlement checks are required before the existing renewal.
    """
    from workbench import real_execution
    from workbench.real_workspace import require_safe_check,sha
    from workbench.trusted_preparation import owned
    from workbench.repair_continuation import private_json
    from workbench.repair_profiles import IndependentProgram,oracle
    payload=job['payload']
    program_claim=payload.get('independent_program_claim') or {}
    claim=payload.get('preparation_claim') or {}
    require(payload.get('worker_operation_failure')=='TypeError' and payload.get('phase')=='held'
        and program_claim.get('source_basis')=='original-only'
        and program_claim.get('review_protocol')=='composed-bounded-v1'
        and not payload.get('independent_profile_program') and not payload.get('check_construction')
        and claim.get('owner') and not real_execution.alive(claim['owner']),
        'The original-only feedback owner must be stopped; other operation failures remain held.')
    root=owned(claim['root'])
    require(any(root.is_relative_to(Path(p)) for p in roots),'Retained feedback is outside configured evidence roots.')
    intent=linked['proposal_review']['task_contract']['bug_intent']
    folders=sorted(root.glob('original-program-trial-*'))
    require(1<=len(folders)<=3,'Completed original-only trial evidence is required.')
    proofs=[]
    rejected=False
    for folder in folders:
        owned(folder)
        trial=private_json(owned(folder/'intent.json'),65536)
        summary=private_json(owned(folder/'summary.json'),16384)
        result=private_json(owned(folder/'result.json'),65536)
        program=IndependentProgram.model_validate(trial['program'])
        compiled=oracle(intent['plan']['focused_test'],program.program)
        oracle_path=owned(folder/'oracle.lua')
        summary_hash=owned(folder/'summary.sha256')
        require(summary_hash.stat().st_size==64 and oracle_path.stat().st_size==len(compiled.encode()),
            'Bounded original-only trial evidence is required.')
        key=digest(dict(base=intent['base'],intent_digest=intent['review_digest'],program=program.model_dump()))
        require(trial['key']==summary['key']==key and folder.name=='original-program-trial-'+key
            and trial['ref']==payload['ref'] and trial['base']==summary['base']==intent['base']
            and trial['intent_digest']==intent['review_digest']
            and summary_hash.read_text()==sha((folder/'summary.json').read_bytes())
            and summary['intent_sha256']==sha((folder/'intent.json').read_bytes())
            and summary['result_sha256']==sha((folder/'result.json').read_bytes())
            and summary['oracle_sha256']==sha(oracle_path.read_bytes()) and oracle_path.read_text()==compiled
            and result.get('network_mode')=='denied' and result.get('usage',{}).get('cgroup_removed') is True,
            'Retained original-only trial evidence changed or remains unsettled.')
        require_safe_check(result)
        rejected|=summary.get('accepted') is False
        proofs.append(dict(key=key,summary_sha256=sha((folder/'summary.json').read_bytes())))
    require(rejected,'A retained original-trial rejection is required for feedback recovery.')
    return dict(failure='TypeError',original_claim_sha256=digest(claim),trials=proofs,owner_stopped=True)


def settled_strategy_program(c,attempt,selection,payload,roots):
    """Read existing child/native receipts; never repair the historical parent ledger."""
    records=[payload,*payload.get('check_preparation_history',[])]
    require(any((r.get('independent_program_claim') or {}).get('id')==attempt['id']
                and r['independent_program_claim'].get('source_basis')=='original-only'
                for r in records),'Independent construction ownership is unavailable.')
    usage=attempt['usage']
    require(attempt['backend']=='smart' and attempt['source']=='strategy-control'
        and attempt['execution_id'] is None and attempt['finished_at']
        and attempt['outcome'] in {'completed','failed'} and isinstance(usage,dict)
        and usage.get('status')=='complete','Previous strategy construction is unsettled.')
    children=list(c.execute(select(model_attempts).where(model_attempts.c.job_id==attempt['job_id'],
        model_attempts.c.task_id==attempt['task_id'],model_attempts.c.purpose=='strategy-request',
        model_attempts.c.started_at>=attempt['started_at'],model_attempts.c.started_at<=attempt['finished_at'])).mappings())
    require(type(usage.get('requests_observed')) is int and len(children)==usage['requests_observed']>0,
        'Strategy construction request inventory differs.')
    stages={s['id']:s for s in selection['strategy']['stages']}
    totals=dict(prompt_tokens=0,completion_tokens=0,total_tokens=0)
    proofs=[]
    evidence_ids=set()
    for child in children:
        observed=child['usage'] or {}
        entries=observed.get('strategy_requests',[])
        require(len(entries)==1,'Strategy request attribution is unavailable.')
        entry=entries[0]
        stage=stages.get(entry.get('stage_id'))
        require(stage and child['source']=='workbench' and child['execution_id'] is None
            and child['policy_revision']==attempt['policy_revision'] and child['outcome']=='completed'
            and child['finished_at'] and child['finished_at']<=attempt['finished_at']
            and child['backend']==stage['provider']==entry.get('provider')=='codex'
            and child['requested_model']==child['actual_model']==entry.get('model')==stage['model']
            and child['requested_effort']==entry.get('effort')==stage['effort']
            and observed.get('status')=='complete' and observed.get('requests_observed')==1
            and observed.get('values')==entry.get('values'),'Strategy request is unsettled or changed.')
        identity=entry['evidence_id']
        require(identity not in evidence_ids and child['id']==str(uuid5(UUID(identity),
            'strategy-provider-request-'+stage['id'])),'Strategy evidence ownership differs.')
        evidence_ids.add(identity)
        proof=completed_turns(identity,observed,roots,allow_unavailable=True,requested_model=stage['model'])
        require(proof['actual_model']==stage['model'] and proof['requests']==1
            and proof['turns'][0]['provider_receipt']==entry.get('native_receipt'),
            'Native strategy terminal attribution differs.')
        for key in totals:
            totals[key]+=observed['values'][key]
        proofs.append(dict(attempt=child['id'],terminal_proof_sha256=digest(proof)))
    require(totals==usage.get('values'),'Strategy aggregate accounting differs.')
    return dict(attempt=attempt['id'],requests=proofs,usage_sha256=digest(usage))


def request_in(c,job,body):
    require(body.command_id and body.review_binding,'A fresh check-construction command and exact scope binding are required.')
    command=str(body.command_id)
    signature=digest(body.model_dump(mode='json'))
    payload=dict(job['payload'])
    commands=payload.get('check_retry_commands',{})
    if command in commands:
        require(commands[command]==signature,'This command already belongs to another check-construction request.')
        return
    require(job['kind']=='behavioral_preparation' and job['status']=='failed' and job['finished_at'],
            'Check construction must be terminal before renewal.')
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
    provider=selection.get('provider','codex') if selection else 'local'
    model=selection['model'] if selection else everyday.local_selection(c).model
    roots=everyday.configuration(c.engine).evidence_roots
    feedback_failure=None
    if (payload.get('preparation_result') or {}).get('status')!='hold':
        try:
            feedback_failure=settled_feedback_failure(job,linked,roots)
        except (ValueError,OSError,KeyError,TypeError):
            raise HTTPException(409,'Read-only feedback recovery requires intact, complete original-trial evidence.') from None
    attempts=list(c.execute(select(model_attempts).where(model_attempts.c.job_id==job['id'],
        model_attempts.c.purpose=='check_preparation')).mappings())
    require(attempts,'Retained check-construction accounting is required.')
    if feedback_failure:
        require(any(a['id']==payload['independent_program_claim']['id'] and a['outcome']=='failed'
            for a in attempts),'The failed feedback construction has no terminal model owner.')
    proofs=[]
    for attempt in attempts:
        usage=attempt['usage']
        if provider=='smart' and attempt['backend']=='smart':
            try:
                proofs.append(settled_strategy_program(c,attempt,selection,payload,roots))
            except (ValueError,OSError,KeyError,TypeError):
                raise HTTPException(409,'Independent strategy terminal receipts are unavailable.') from None
            continue
        require(attempt['source']=='workbench' and attempt['execution_id'] is None
                and attempt['backend']==provider and attempt['requested_model']==model
                and attempt['finished_at'],'Previous check activity is unsettled.')
        if provider=='codex' and attempt['outcome']=='interrupted_unknown' and usage is None:
            from workbench.provider_receipts import interrupted_check_construction
            from workbench.codex_quarantine import local_generation_absent
            records=[*payload.get('check_preparation_history',[]),dict(
                claim=payload.get('preparation_claim'),construction=payload.get('check_construction'))]
            matched=[item for item in records if item.get('construction',{}).get('id')==attempt['id']]
            require(len(matched)==1 and local_generation_absent(),'Read-only construction is not isolated.')
            try:
                proof=interrupted_check_construction(attempt['id'],matched[0]['claim']['root'],roots)
            except (ValueError,OSError,KeyError,TypeError):
                raise HTTPException(409,'Standing read-only recovery evidence is unavailable; unknown usage remains held.') from None
            proofs.append(dict(attempt=attempt['id'],read_only_quarantine=proof))
            continue
        require(attempt['actual_model']==model and attempt['outcome'] in {'completed','failed'}
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
        independent_program_claim=payload.get('independent_program_claim'),
        construction=payload.get('check_construction'),settlement=proofs,output_reused=False,
        **(dict(worker_operation_failure=payload['worker_operation_failure'],phase=payload['phase'],
            read_only_feedback_failure=feedback_failure) if feedback_failure else {})))
    renewed={k:v for k,v in payload.items() if k not in {'preparation_claim','preparation_result','check_construction','recovery',
        'independent_program_claim','independent_program_resume','independent_profile_program'}}
    renewed.update(check_preparation_history=history,check_retry_commands={**commands,command:signature},
                   preparation_round_id=str(uuid4()))
    if feedback_failure:
        renewed.pop('worker_operation_failure')
        renewed.pop('phase')
    c.execute(planning_jobs.update().where(planning_jobs.c.id==job['id']).values(status='queued',finished_at=None,
        payload=renewed,message='A fresh bounded check-construction operation is queued with the same approved scope and provider. No repair is authorized.'))
