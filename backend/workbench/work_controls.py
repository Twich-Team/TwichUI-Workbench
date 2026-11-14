"""Global admission preference in the existing append-only execution policy."""

from fastapi import HTTPException
from pydantic import Field
from workbench import model_policy
from workbench.db import execution_policies, write_transaction
from workbench.schemas import StrictModel
from workbench.service import now


class WorkControlInput(StrictModel):
    revision: int = Field(ge=0)
    paused: bool


class WorkControl(WorkControlInput):
    message: str


def view(policy):
    return WorkControl(
        revision=policy.revision,
        paused=bool(policy.pause_new_work),
        message=(
            "New work is paused. Work already running can finish safely."
            if policy.pause_new_work
            else "New work can start when its permissions and resources are ready."
        ),
    )


def read(engine):
    return view(model_policy.read(engine))


def save(engine, body):
    with write_transaction(engine) as c:
        policy = model_policy.read_connection(c)
        if body.paused == bool(policy.pause_new_work) and body.revision in {
            policy.revision, policy.revision - 1
        }:
            return view(policy)
        if body.revision != policy.revision:
            raise HTTPException(409, "Work settings changed. Reload the saved status before changing it.")
        updated = policy.model_copy(update={
            "revision": policy.revision + 1, "pause_new_work": body.paused or None
        })
        c.execute(execution_policies.insert().values(
            revision=updated.revision,
            payload=updated.model_dump(mode='json'),
            saved_at=now(),
            actor="local-owner-work-control",
        ))
        return view(updated)


def task_paused(c, identity):
    from uuid import UUID
    return bool((model_policy.read_connection(c).task_pauses or {}).get(UUID(identity)))


def task_read(engine, identity):
    from workbench.intake import require
    from workbench.db import intakes
    with engine.connect() as c:
        require(c,intakes,identity)
        policy=model_policy.read_connection(c)
        from uuid import UUID
        paused=bool((policy.task_pauses or {}).get(UUID(identity)))
        return WorkControl(revision=policy.revision,paused=paused,message=(
            'This task is paused. Work already running can finish safely.' if paused else
            'This task can continue when its saved permissions and resources are ready.'))


def task_save(engine, identity, body):
    from uuid import UUID
    from workbench.intake import require
    from workbench.db import intakes
    with write_transaction(engine) as c:
        require(c,intakes,identity)
        policy=model_policy.read_connection(c)
        pauses=dict(policy.task_pauses or {})
        key=UUID(identity)
        if body.paused==bool(pauses.get(key)) and body.revision in {policy.revision,policy.revision-1}:
            pass
        else:
            if body.revision!=policy.revision:
                raise HTTPException(409,'Work settings changed. Reload before changing this task.')
            if body.paused:
                pauses[key]=True
            else:
                pauses.pop(key,None)
            updated=policy.model_copy(update=dict(revision=policy.revision+1,task_pauses=pauses or None))
            c.execute(execution_policies.insert().values(revision=updated.revision,payload=updated.model_dump(mode='json'),
                saved_at=now(),actor='local-owner-task-control'))
    return task_read(engine,identity)


def priority_order(column):
    """The existing worker selects the owner's highest-priority ready request."""
    from sqlalchemy import select,func,case
    from workbench.db import intakes
    from workbench.db import execution_policies
    next_task=select(execution_policies.c.payload['next_task'].as_string()).order_by(execution_policies.c.revision.desc()).limit(1).scalar_subquery()
    rank=func.coalesce(select(intakes.c.priority_override).where(intakes.c.id==column).correlate_except(intakes).scalar_subquery(),5)
    return case((column==next_task,0),else_=rank)


def work_next(engine, identity):
    from uuid import UUID
    from workbench.intake import require
    from workbench.db import intakes
    from workbench.task_cleanup import require_current
    with write_transaction(engine) as c:
        require(c,intakes,identity)
        require_current(c,identity)
        policy=model_policy.read_connection(c)
        if policy.next_task!=UUID(identity):
            updated=policy.model_copy(update=dict(revision=policy.revision+1,next_task=UUID(identity)))
            c.execute(execution_policies.insert().values(revision=updated.revision,payload=updated.model_dump(mode='json'),
                saved_at=now(),actor='local-owner-work-next'))
    return dict(message='This task will take priority for its next eligible operation. Running work can finish safely.')
