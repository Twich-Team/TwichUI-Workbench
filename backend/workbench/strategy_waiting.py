"""Durable waiting fields on the existing planning job, without another scheduler."""
import time
from sqlalchemy import or_,select
from workbench.db import planning_jobs,model_attempts,intakes,write_transaction
from workbench import intake
from workbench.smart_provider import Waiting,choose


def due():
    return or_(planning_jobs.c.payload['ai_wait']['recheck_at'].as_float().is_(None),
               planning_jobs.c.payload['ai_wait']['recheck_at'].as_float()<=time.time())


def options(job,*,suitable_local=False,reviewed_local=False):
    return dict(suitable_local=suitable_local,reviewed_local=reviewed_local,
                waiting_since=job['payload'].get('ai_wait_since',{}),
                failures=job['payload'].get('ai_failures',job['payload'].get('diagnostics',{}).get('ai_failures',{})),
                settled_failures=job['payload'].get('ai_settled_failures',job['payload'].get('diagnostics',{}).get('ai_settled_failures',[])))


def waiting_payload(payload,decision):
    payload=dict(payload)
    first=dict(payload.get('ai_wait_since',{}))
    if decision.get('stage_id'):
        first.setdefault(decision['stage_id'],time.time())
    payload.update(ai_wait=decision,ai_wait_since=first)
    if not decision.get('recheck_at'):
        payload['ai_wait']={**decision,'recheck_at':time.time()+300}
    return payload


def preflight(engine,job,route,stop=lambda:False,**suitability):
    if not route.get('strategy'):
        return False
    try:
        eligibility=options(job,**suitability)
        eligibility.pop('settled_failures',None)
        decision=choose(route,stop,**eligibility).model_dump()
    except Waiting as error:
        with write_transaction(engine) as c:
            current=intake.require(c,planning_jobs,job['id'])
            if current!=job:
                return True
            payload=waiting_payload(job['payload'],error.decision)
            message=error.decision['reason']+' Work is saved and will be checked automatically.'
            c.execute(planning_jobs.update().where(planning_jobs.c.id==job['id']).values(payload=payload,message=message))
        job.update(payload=payload,message=message)
        return True
    # The native dispatch checks this choice again. This is a dashboard fact only.
    with write_transaction(engine) as c:
        if intake.require(c,planning_jobs,job['id'])!=job:
            return True
        since=dict(job['payload'].get('ai_wait_since',{}))
        since.pop(decision.get('stage_id'),None)
        payload={**job['payload'],'ai_decision':decision,'ai_wait_since':since}
        payload.pop('ai_wait',None)
        c.execute(planning_jobs.update().where(planning_jobs.c.id==job['id']).values(payload=payload))
    job['payload']=payload
    return False


def resume_valid(c,job):
    """Only a controller-retired, settled phase can resume; unknown native work holds."""
    resumes=job['payload'].get('ai_resumed_phases',[])
    if not resumes or len(resumes)>32:
        return False
    for phase in resumes:
        row=c.execute(select(model_attempts).where(model_attempts.c.id==phase.get('attempt'),
            model_attempts.c.job_id==job['id'])).mappings().first()
        if (not row or row['outcome']!='waiting' or not row['finished_at']
                or row['usage']!=phase.get('usage') or row['usage'].get('status') not in {'complete','not_started'}):
            return False
    return True


def pause(c,job,original,adapter,decision,*,active_seconds=0):
    """Called by the existing fenced result owner after a known non-dispatch."""
    facts=adapter.diagnostics
    if facts.get('stopped_request_generation_state',facts.get('generation_state'))!='not_started':
        return False
    usage=facts.get('usage') or dict(status='not_started',requests_observed=0,
        source='Verified no native request started in this phase',units='tokens',
        values=dict(prompt_tokens=0,completion_tokens=0,total_tokens=0))
    if usage.get('status') not in {'complete','not_started'}:
        return False
    previous=list(job['payload'].get('ai_resumed_phases',[]))
    if len(previous)>=32:
        return False
    previous.append(dict(attempt=job['lease'],usage=usage,
        claim=job['payload'].get('scenario_generation_claim') or job['payload'].get('feature_generation_claim'),
        active_seconds=max(0,active_seconds)))
    payload=waiting_payload(job['payload'],decision)
    payload.update(ai_failures=facts.get('ai_failures',{}),ai_settled_failures=facts.get('ai_settled_failures',[]))
    payload.update(ai_resumed_phases=previous)
    retained=getattr(adapter,'inspection_packet',None)
    if retained:
        payload['ai_retained_packet']=retained
    c.execute(model_attempts.update().where(model_attempts.c.id==job['lease']).values(
        source='strategy-control',outcome='waiting',usage=usage,finished_at=intake.now()))
    c.execute(planning_jobs.update().where(planning_jobs.c.id==job['id']).values(status='queued',
        payload=payload,lease=None,lease_until=None,finished_at=None,
        message=decision['reason']+' Work is saved and will be checked automatically.'))
    c.execute(intakes.update().where(intakes.c.id==original['id']).values(status='investigating'))
    return True


def remaining(job,bound):
    spent=sum(p['usage'].get('requests_observed',0) for p in job['payload'].get('ai_resumed_phases',[]))
    return bound-spent


def seconds_remaining(job,bound):
    return bound-sum(p.get('active_seconds',0) for p in job['payload'].get('ai_resumed_phases',[]))


def pause_preparation(c,active,adapter,decision,parent_id,claim_key):
    """Retain a read-only preparation claim without consuming a native request."""
    facts=adapter.diagnostics
    if facts.get('generation_state')!='not_started':
        return False
    previous=list(active['payload'].get('ai_deferred_claims',[]))
    if len(previous)>=32:
        return False
    previous.append(dict(attempt=active['lease'],claim=active['payload'].get(claim_key),decision=decision))
    payload=waiting_payload(active['payload'],decision)
    payload['ai_deferred_claims']=previous
    payload['phase']='prepare_scopes' if claim_key=='feature_scope_claim' else 'prepare_independent_checks'
    c.execute(model_attempts.update().where(model_attempts.c.id==active['lease']).values(
        source='strategy-control',outcome='waiting',finished_at=intake.now(),usage=dict(status='not_started',
            requests_observed=0,source='Verified no native preparation request started',units='tokens',
            values=dict(prompt_tokens=0,completion_tokens=0,total_tokens=0))))
    c.execute(planning_jobs.update().where(planning_jobs.c.id==active['id']).values(status='queued',
        payload=payload,lease=None,lease_until=None,finished_at=None,message=decision['reason']))
    from workbench.db import task_sessions
    c.execute(task_sessions.update().where(task_sessions.c.id==parent_id).values(status='pending',
        finished_at=None,message=decision['reason']+' Work is saved and will resume when eligible.'))
    return True
