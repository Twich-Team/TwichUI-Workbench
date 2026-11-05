"""NON-MODEL current-session authorship contract; no live run or external writes."""

import copy
import pytest
from workbench import real_execution, task_budget
from workbench.session_authorship import authorship_valid, usage_eligible
from test_supported_owner_ingress import owned_template as owned_template
from workbench import owner_task


def sample():
    contract = copy.deepcopy(real_execution.contract())
    contract.update(
        provider="current-session",
        model="codex-current-session",
        authoring="explicit-current-session",
    )
    path = contract["paths"][0]
    h = "b" * 64
    proof = dict(
        kind="explicit-current-session",
        author="Codex current session",
        owner_authorization_sha256="a" * 64,
        files={path: h},
    )
    result = dict(
        authorship=proof,
        manifest=dict(authorship=proof, tree={path: h}),
        inference_ran=False,
        usage=dict(
            status="unavailable",
            values=None,
            provenance=dict(method="current Codex session; no separately measurable token receipt"),
        ),
    )
    return contract, proof, result


def test_session_authorship_is_explicit_and_unknown_usage_is_not_zero():
    contract, proof, result = sample()
    assert task_budget.valid_contract(contract) and authorship_valid(contract, result, proof)
    assert usage_eligible(dict(contract=contract, runtime=dict(authorship=proof)), result)
    assert not authorship_valid(real_execution.contract(), result, proof)
    assert not usage_eligible(dict(contract=real_execution.contract()), result)
    assert result["usage"]["values"] is None
    assert not authorship_valid(contract, {**result, "inference_ran": True}, proof)
    assert not authorship_valid(contract, result, {**proof, "owner_authorization_sha256": "f" * 64})
    forged = copy.deepcopy(result)
    forged["manifest"]["tree"][contract["paths"][0]] = "c" * 64
    assert not authorship_valid(contract, forged, proof)


def test_owner_must_explicitly_select_session_authorship(store, owned_template, tmp_path, client):
    auth, h, identity, value = owned_template
    ref = owner_task.record_supported(store, auth, h, identity, value["recipe"]["contract"])
    recipe = copy.deepcopy(value["recipe"])
    recipe["contract"].update(
        provider="current-session",
        model="codex-current-session",
        authoring="explicit-current-session",
    )
    with pytest.raises(ValueError, match="scope, base, oracle"):
        owner_task.continue_supported(store, ref, auth, h, recipe, continuation_id="codex")
    new = owner_task.continue_supported(
        store, ref, auth, h, recipe, continuation_id="codex", session_authoring=True
    )
    assert new["task_id"] == ref["task_id"]
    data = client.get("/api/intake/" + ref["intake_id"])
    assert data.status_code == 200
    receipt = next(d for d in data.json()["decisions"] if d["id"] == new["approval_id"])
    assert (
        receipt["payload"]["execution_continuation"]["authoring_mode"] == "explicit-current-session"
    )


def test_explicit_unavailable_usage_keeps_models_activity_healthy_without_zero_inference(
    store, client
):
    from uuid import uuid4
    from workbench.db import model_attempts, write_transaction

    unknown = dict(
        status="unavailable",
        values=None,
        provenance=dict(method="NON-MODEL current-session accounting control"),
    )
    with write_transaction(store) as c:
        c.execute(
            model_attempts.insert().values(
                id=str(uuid4()),
                task_id="NON-MODEL",
                source="workbench",
                backend="current-session",
                purpose="implementation",
                actual_model="codex-current-session",
                routing_reason="NON-MODEL API compatibility control",
                started_at="2026-11-05T00:00:00+00:00",
                finished_at="2026-11-05T00:01:00+00:00",
                outcome="checked",
                usage=unknown,
            )
        )
        c.execute(
            model_attempts.insert().values(
                id=str(uuid4()),
                task_id="NON-MODEL-known",
                source="workbench",
                backend="local",
                purpose="implementation",
                actual_model="NON-MODEL",
                routing_reason="NON-MODEL known usage control",
                started_at="2026-11-04T00:00:00+00:00",
                outcome="checked",
                usage=dict(
                    status="complete",
                    values=dict(total_tokens=7),
                    units="tokens",
                    source="NON-MODEL controlled receipt",
                    observed_at="2026-11-04T00:01:00+00:00",
                ),
            )
        )
    response = client.get("/api/models/activity")
    assert response.status_code == 200
    data = response.json()
    assert data["rows"][0]["usage"] == unknown
    assert (
        data["attempts"] == 2
        and len(data["usage_points"]) == 1
        and data["usage_points"][0]["tokens"] == 7
    )
