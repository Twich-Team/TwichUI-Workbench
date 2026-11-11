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
            payload=updated.model_dump(),
            saved_at=now(),
            actor="local-owner-work-control",
        ))
        return view(updated)
