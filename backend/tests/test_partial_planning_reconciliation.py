"""Finished partial text is neither a complete plan nor uncertain execution."""

from contextlib import nullcontext
import copy
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from workbench import worker, partial_planning_reconciliation as settle
from workbench.db import model_attempts, planning_jobs
from workbench.planning_provider import LocalProvider
from test_bug_workflow import enable_bugs, report
from test_bug_source_planning import inspection
from test_everyday import installed  # noqa: F401


def partial(client, headers, store, monkeypatch, program=False):
    enable_bugs(monkeypatch)
    if program:
        import os
        from test_everyday import write_policy
        config=Path(os.environ['WORKBENCH_PREPARATION_CONFIG'])
        value=json.loads(config.read_text())
        value['bug_plan_review']='source-grounded-lua-v1'
        write_policy(config,value)
    calls = []

    def generate(self, packet, schema, expected_model):
        self.diagnostics = {"generation_state": "not_started"}
        calls.append(packet)
        index = len(calls)

        class Transport:
            seconds = 120

            def models(self, _):
                return expected_model

            def read(self, request, limit):
                return json.dumps(
                    dict(
                        id="controlled-partial-" + str(index),
                        model=expected_model,
                        usage=dict(prompt_tokens=40, completion_tokens=6144 if program and index==2 else 10,
                                   total_tokens=6184 if program and index==2 else 50),
                        choices=[
                            dict(
                                finish_reason="stop" if index == 1 else "length",
                                message=dict(
                                    content=json.dumps(dict(finding='Read current source.',draft=dict(inspection=inspection()['inspection'])) if program else inspection())
                                    if index == 1
                                    else '{"finding":'
                                ),
                            )
                        ],
                    )
                ).encode()

        return self._infer(
            Transport(), "inert", schema, json.dumps(packet), expected_model=expected_model
        )

    monkeypatch.setattr(LocalProvider, "generate", generate)
    identity = client.post("/api/intake", headers=headers, json=report()).json()["id"]
    assert worker.tick(store)
    with store.connect() as c:
        attempt = (
            c.execute(select(model_attempts).where(model_attempts.c.task_id == identity))
            .mappings()
            .one()
        )
        job = (
            c.execute(select(planning_jobs).where(planning_jobs.c.intake_id == identity))
            .mappings()
            .one()
        )
    assert (
        attempt["outcome"] == "incomplete_output" and job["status"] == "failed" and len(calls) == 2
    )
    monkeypatch.setattr("workbench.planning_provider.local_slot", lambda: nullcontext())
    monkeypatch.setattr(
        settle, "probe_local", lambda model, task, completion_limit=2600: dict(task=task, idle=True, completion_limit=completion_limit)
    )
    return dict(attempt), dict(job)


@pytest.mark.usefixtures("installed")
def test_all_usage_receipts_retained_despite_partial_text_and_receipt_does_not_approve_or_replay(
    client,
    headers,
    store,
    monkeypatch,
):
    attempt, job = partial(client, headers, store, monkeypatch)
    assert attempt["usage"]["values"] == dict(
        prompt_tokens=80, completion_tokens=20, total_tokens=100
    )
    assert attempt["usage"]["requests_observed"] == 2
    with store.connect() as c:
        with pytest.raises(ValueError):
            settle.accounting(c, attempt["id"])
    observed = settle.reconcile(store, attempt["id"], 12)
    assert observed["complete_usage_receipts"] == 2 and observed["complete_text_responses"] == 1
    assert observed["partial_text_responses"] == 1 and observed["unavailable_usage_requests"] == 0
    with store.connect() as c:
        assert (
            dict(
                c.execute(select(model_attempts).where(model_attempts.c.id == attempt["id"]))
                .mappings()
                .one()
            )
            == attempt
        )
        current = dict(
            c.execute(select(planning_jobs).where(planning_jobs.c.id == job["id"])).mappings().one()
        )
        payload = copy.deepcopy(current["payload"])
        payload.pop(settle.KEY)
        assert payload == job["payload"] and current["status"] == "failed"
        assert settle.accounting(c, attempt["id"]) == observed
        assert not c.exec_driver_sql("SELECT id FROM proposals").fetchall()
    assert settle.reconcile(store, attempt["id"], 12) == observed


@pytest.mark.usefixtures("installed")
def test_legacy_null_usage_field_preserved_when_actual_complete_journals_exist(
    client, headers, store, monkeypatch
):
    attempt, job = partial(client, headers, store, monkeypatch)
    payload = copy.deepcopy(job["payload"])
    payload["diagnostics"]["known_usage"] = json.dumps(attempt["usage"]["values"])
    with store.begin() as c:
        c.execute(
            model_attempts.update().where(model_attempts.c.id == attempt["id"]).values(usage=None)
        )
        c.execute(
            planning_jobs.update().where(planning_jobs.c.id == job["id"]).values(payload=payload)
        )
    result = settle.reconcile(store, attempt["id"], 12)
    assert result["values"]["completion_tokens"] == 20
    with store.connect() as c:
        assert (
            c.execute(
                select(model_attempts.c.usage).where(model_attempts.c.id == attempt["id"])
            ).scalar_one()
            is None
        )


@pytest.mark.parametrize(
    "fault",
    ["codex", "implementation", "unavailable", "running", "claim", "receipt", "usage", "outcome"],
)
@pytest.mark.usefixtures("installed")
def test_unrelated_unknown_or_unsettled_state_cannot_be_admitted(
    client, headers, store, monkeypatch, fault
):
    attempt, job = partial(client, headers, store, monkeypatch)
    payload = copy.deepcopy(job["payload"])
    with store.begin() as c:
        if fault == "codex":
            c.execute(
                model_attempts.update()
                .where(model_attempts.c.id == attempt["id"])
                .values(backend="codex")
            )
        elif fault == "implementation":
            c.execute(
                model_attempts.update()
                .where(model_attempts.c.id == attempt["id"])
                .values(purpose="implementation")
            )
        elif fault == "outcome":
            c.execute(
                model_attempts.update()
                .where(model_attempts.c.id == attempt["id"])
                .values(outcome="interrupted_unknown")
            )
        elif fault == "running":
            c.execute(
                planning_jobs.update()
                .where(planning_jobs.c.id == job["id"])
                .values(status="running")
            )
        else:
            if fault == "unavailable":
                payload["diagnostics"]["accounting_state"] = "unavailable"
            elif fault == "claim":
                payload["scenario_generation_claim"] = {}
            elif fault == "receipt":
                payload["local_planning_receipt"]["max_local_requests"] = 9
            else:
                payload["diagnostics"]["known_usage"] = json.dumps(
                    dict(prompt_tokens=1, completion_tokens=1, total_tokens=2)
                )
            c.execute(
                planning_jobs.update()
                .where(planning_jobs.c.id == job["id"])
                .values(payload=payload)
            )
    with pytest.raises(ValueError):
        settle.reconcile(store, attempt["id"], 12)


@pytest.mark.usefixtures("installed")
def test_concurrent_state_change_during_probe_prevents_settlement(
    client, headers, store, monkeypatch
):
    attempt, job = partial(client, headers, store, monkeypatch)

    def probe(model, task):
        with store.begin() as c:
            c.execute(
                planning_jobs.update()
                .where(planning_jobs.c.id == job["id"])
                .values(message="Changed concurrently")
            )
        return dict(task=task, idle=True, completion_limit=2600)

    monkeypatch.setattr(settle, "probe_local", probe)
    with pytest.raises(ValueError, match="changed"):
        settle.reconcile(store, attempt["id"], 12)
    with store.connect() as c:
        payload = c.execute(
            select(planning_jobs.c.payload).where(planning_jobs.c.id == job["id"])
        ).scalar_one()
        assert settle.KEY not in payload


@pytest.mark.usefixtures('installed')
def test_program_partial_keeps_actual_larger_bound_and_does_not_approve(client,headers,store,monkeypatch):
    original,job=partial(client,headers,store,monkeypatch,program=True)
    result=settle.reconcile(store,original['id'],40232)
    assert result['values']['completion_tokens']==6154
    assert result['requests_started']==2 and result['partial_text_responses']==1
    with store.connect() as c:
        assert dict(c.execute(select(model_attempts).where(model_attempts.c.id==original['id'])).mappings().one())==original
        assert settle.accounting(c,original['id'])==result
        payload=c.execute(select(planning_jobs.c.payload).where(planning_jobs.c.id==job['id'])).scalar_one()
    assert payload[settle.KEY]['snapshot']['completion_limit']==6144
    assert payload['local_planning_receipt']['max_local_completion_tokens']==6144
