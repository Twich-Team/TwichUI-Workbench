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
    if not original['payload'].get('local_planning'):
        return None
    job=available(c,original)
    if job:
        return binding(original,job)
    run=available_run(c,original)
    return run['binding'] if run else None


def route(c,original,job,body,*,frozen_override=None):
    saved=model_policy.read_connection(c)
    choice=body.choice
    if not saved.providers[choice.provider].enabled:
        raise ValueError('Enable this provider in AI Settings first.')
    provider_routes.validate_selection(choice.provider,choice.model,choice.effort)
    frozen=frozen_override or provider_routes.approved(c,original)
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
        from workbench.db import task_sessions
        from sqlalchemy import or_
        states=c.execute(select(task_sessions.c.state).where(or_(
            task_sessions.c.snapshot['task']['intake_id'].as_string()==identity,
            task_sessions.c.snapshot['intake_id'].as_string()==identity))).scalars()
        for state in states:
            sources=[state,state.get('feature_slot_state',{})]
            for source in sources:
                for receipt in [*source.get('ai_manual_history',[]),source.get('ai_manual') or {}]:
                    if receipt.get('command',{}).get('command_id')==str(body.command_id):
                        if receipt['command']!=command:
                            raise HTTPException(409,'This Start now command already belongs to another choice.')
                        return
        job=available(c,original)
        if not job:
            return start_run(c,original,body)
        if binding(original,job)!=body.binding:
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


def available_run(c,original):
    """A settled between-turn coding wait can receive a new explicit model choice."""
    from workbench import real_execution,task_execution
    from workbench.codex_runtime import CodexRuntime
    from workbench.smart_coding_wait import load
    from workbench.db import real_execution_requests as requests,real_execution_grants as grants,task_sessions
    rows=c.execute(select(requests,grants.c.snapshot.label('grant_snapshot'),grants.c.binding.label('grant_binding'))
        .join(grants,requests.c.grant_id==grants.c.id).where(requests.c.status=='waiting_ai',
        grants.c.snapshot['workflow_task']['intake_id'].as_string()==original['id'])).mappings().all()
    if len(rows)!=1:
        return None
    row=rows[0]
    grant=c.execute(select(grants).where(grants.c.id==row['grant_id'])).mappings().one()
    base=grant['snapshot']['runtime'].get('codex_selection')
    if not base or not base.get('strategy') or not real_execution.valid(grant):
        return None
    try:
        task_execution.validate(c,grant)
        runtime=CodexRuntime(__import__('pathlib').Path(grant['snapshot']['recovery']['root']),base)
        checkpoint=load(runtime,row,grant)
        parent_id=grant['snapshot']['task_budget']['decision_id']
        parent=c.execute(select(task_sessions).where(task_sessions.c.id==parent_id)).mappings().one()
        if parent['state'].get('cancel_requested'):
            return None
    except (ValueError,OSError,KeyError,TypeError):
        return None
    state=parent['state'].get('feature_slot_state',parent['state'])
    bound=intake.digest(dict(original=original['payload'],execution_id=row['id'],fence=row['fence'],
        grant_binding=grant['binding'],checkpoint=row['result']['ai_checkpoint'],manual=state.get('ai_manual')))
    return dict(request=row,grant=grant,parent=parent,state=state,checkpoint=checkpoint,binding=bound,base=base)


def start_run(c,original,body):
    from workbench.db import real_execution_requests as requests,task_sessions
    value=available_run(c,original)
    if not value:
        raise HTTPException(409,'This coding wait changed, has expired, or needs settlement. Refresh before choosing a model.')
    state=dict(value['state'])
    command=body.model_dump(mode='json')
    history=list(state.get('ai_manual_history',[]))
    for item in [*history,state.get('ai_manual') or {}]:
        if item.get('command',{}).get('command_id')==str(body.command_id):
            if item['command']!=command:
                raise HTTPException(409,'This Start now command already has another choice.')
            return
    if value['binding']!=body.binding:
        raise HTTPException(409,'This coding wait changed. Refresh before choosing a model.')
    if len(history)>=32:
        raise HTTPException(409,'The bounded model-choice history needs review.')
    try:
        selected=route(c,original,None,body,frozen_override=value['base'])
    except ValueError as error:
        raise HTTPException(409,str(error)) from None
    if state.get('ai_manual'):
        history.append(state['ai_manual'])
    row,grant=value['request'],value['grant']
    receipt=dict(actor='owner',command=command,execution_id=row['id'],fence=row['fence'],
        grant_binding=grant['binding'],checkpoint_sha256=row['result']['ai_checkpoint']['sha256'],
        route=selected,approved_at=intake.now())
    from pathlib import Path
    from workbench.real_workspace import durable
    from workbench.provider_admission import read
    path=Path(grant['snapshot']['recovery']['root'])/('ai-manual-'+str(body.command_id)+'.json')
    if path.exists():
        previous=read(path,None)
        if {k:v for k,v in previous.items() if k!='approved_at'}!={k:v for k,v in receipt.items() if k!='approved_at'}:
            raise HTTPException(409,'This model-choice receipt already belongs to different work.')
        receipt=previous
    durable(path,__import__('json').dumps(receipt,sort_keys=True).encode())
    path.chmod(0o600)
    state.update(ai_manual=receipt,ai_manual_history=history,ai_wait=None,ai_wait_since={})
    parent=value['parent']
    parent_state={**parent['state'],'feature_slot_state':state} if parent['snapshot'].get('feature_parent') else state
    c.execute(task_sessions.update().where(task_sessions.c.id==parent['id']).values(state=parent_state,status='pending',
        finished_at=None,message='Ready to check the owner’s Start now choice within the existing run permission.'))
    wait={**row['result']['ai_wait'],'recheck_at':__import__('time').time(),
        'reason':'Ready to check the owner’s explicit model choice. Native limits still apply.','provider_reset_at':None}
    c.execute(requests.update().where(requests.c.id==row['id']).values(
        result={**row['result'],'ai_manual_sha256':intake.digest(receipt),'ai_wait':wait},message=wait['reason']))


def effective_for_run(c,row,grant,checkpoint):
    """Additional owner authorization leaves the original runtime/grant immutable."""
    expected=(row['result'] or {}).get('ai_manual_sha256')
    if not expected:
        return None
    from workbench.db import task_sessions
    parent=c.execute(select(task_sessions).where(task_sessions.c.id==grant['snapshot']['task_budget']['decision_id'])).mappings().one()
    state=parent['state'].get('feature_slot_state',parent['state'])
    receipt=state.get('ai_manual',{})
    if (parent['state'].get('cancel_requested') or receipt.get('actor')!='owner'
            or intake.digest(receipt)!=expected or receipt.get('execution_id')!=row['id']
            or receipt.get('fence')!=row['fence'] or receipt.get('grant_binding')!=grant['binding']
            or receipt.get('checkpoint_sha256')!=row['result']['ai_checkpoint']['sha256']
                and checkpoint.get('manual_authorization_sha256')!=expected):
        raise ValueError('The explicit run model choice changed or does not belong to the saved checkpoint.')
    return receipt
