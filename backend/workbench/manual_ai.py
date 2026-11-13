"""An explicit new owner choice for waiting work; original frozen input is retained."""
from uuid import UUID
from sqlalchemy import select
from fastapi import HTTPException
from workbench import ai_strategy as ai,intake,model_policy,provider_routes
from workbench.db import intakes,planning_jobs,model_attempts,write_transaction
from workbench.schemas import StrictModel,TaskModelChoice


class StartNow(StrictModel):
    command_id: UUID
    binding: str
    choice: TaskModelChoice
    exceed_workbench_allocation: bool = False


def binding(original,job):
    return intake.digest(dict(original=original['payload'],job_id=job['id'],revision=job['revision'],
        scope_revision=job['payload'].get('scope_revision'),manual=job['payload'].get('manual_ai')))


def available(c,original):
    job=c.execute(select(planning_jobs).where(planning_jobs.c.intake_id==original['id'],
        planning_jobs.c.status=='queued',planning_jobs.c.kind.in_(['planning','decomposition']))
        .order_by(planning_jobs.c.revision.desc())).mappings().first()
    if not original['payload'].get('local_planning') or not job:
        return None
    if c.execute(select(model_attempts.c.id).where(model_attempts.c.task_id==original['id'],model_attempts.c.finished_at.is_(None))).first():
        return None
    # Changing an in-flight or uncertain execution is never a manual start.
    attempts=c.execute(select(model_attempts).where(model_attempts.c.job_id==job['id'])).mappings().all()
    if any(not a['finished_at'] or a['outcome'] not in {'waiting','completed','not_started'} for a in attempts):
        return None
    return job


def offer(c,original):
    job=available(c,original)
    return binding(original,job) if job else None


def route(c,original,job,body):
    saved=model_policy.read_connection(c)
    choice=body.choice
    if not saved.providers[choice.provider].enabled:
        raise ValueError('Enable this provider in AI Settings first.')
    provider_routes.validate_selection(choice.provider,choice.model,choice.effort)
    frozen=provider_routes.approved(c,original)
    strategy=ai.Strategy.model_validate(frozen['strategy']) if frozen.get('strategy') else saved.strategy
    if strategy:
        strategy=strategy.model_copy(deep=True)
        selected=next((s for s in strategy.stages if s.provider==choice.provider),None)
    else:
        selected=None
    if selected is None:
        if choice.provider!='local' and not body.exceed_workbench_allocation:
            raise ValueError('This provider has no task strategy allocation. Explicitly authorize a Workbench allocation override to use it.')
        selected=ai.Stage(id='manual',provider=choice.provider,model=choice.model,effort=choice.effort,
                          allocation=None if choice.provider=='local' else 100,on_limit='wait')
        strategy=ai.Strategy(stages=[selected])
    selected.model,selected.effort=choice.model,choice.effort
    result=dict(provider='smart',model=choice.model,effort=choice.effort,fallback=[],
        policy_revision=saved.revision,strategy=strategy.model_dump(),requested_stage=selected.id)
    if body.exceed_workbench_allocation:
        result['manual_stage']=selected.id
    return result


def start(engine,identity,body):
    with write_transaction(engine) as c:
        original=intake.require(c,intakes,identity)
        jobs=c.execute(select(planning_jobs).where(planning_jobs.c.intake_id==identity)).mappings().all()
        command=body.model_dump(mode='json')
        for job in jobs:
            for receipt in [*job['payload'].get('manual_ai_history',[]),job['payload'].get('manual_ai') or {}]:
                if receipt.get('command',{}).get('command_id')==str(body.command_id):
                    if receipt['command']!=command:
                        raise HTTPException(409,'This Start now command already belongs to another choice.')
                    return
        job=available(c,original)
        if not job or binding(original,job)!=body.binding:
            raise HTTPException(409,'Work changed or a provider request needs settlement. Refresh the task before starting.')
        try:
            selected=route(c,original,job,body)
        except ValueError as error:
            raise HTTPException(409,str(error)) from None
        payload=dict(job['payload'])
        history=list(payload.get('manual_ai_history',[]))
        if len(history)>=32:
            raise HTTPException(409,'This operation has reached its bounded manual choice history. Review its saved work.')
        if payload.get('manual_ai'):
            history.append(payload['manual_ai'])
        receipt=dict(command=command,original_binding=intake.digest(original['payload']),
            scope_revision=payload.get('scope_revision'),route=selected,approved_at=intake.now())
        payload.update(manual_ai=receipt,manual_ai_history=history)
        # Choice is a new authorization, not evidence that quota reset.
        payload.pop('ai_wait',None)
        payload.pop('ai_wait_since',None)
        c.execute(planning_jobs.update().where(planning_jobs.c.id==job['id']).values(payload=payload,
            message='Ready to check the owner’s Start now choice. Native limits and safety checks still apply.'))
