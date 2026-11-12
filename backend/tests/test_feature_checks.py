"""Original-source independent feature program; no candidate/model permissions."""

import copy
import subprocess

import pytest
from sqlalchemy import select

from workbench import feature_checks, regression_preparation
from workbench.db import planning_jobs, real_execution_requests
from workbench.real_workspace import PRELUDE
from test_feature_execution import missing_names_parent
from test_verification_profiles import PRODUCTION, TEST
from test_everyday import installed as installed


def program(packet):
    groups = {"absent": [], "blank": [], "normal": []}
    for clause in packet["indexed_clauses"]:
        text = clause["requirement"].lower()
        if "absent" in text:
            groups["absent"].append(clause["index"])
        if "blank" in text:
            groups["blank"].append(clause["index"])
        if "normal" in text:
            groups["normal"].append(clause["index"])
    return dict(
        description="Observe absent/blank handling and preserved normalization.",
        setup=dict(kind="module", source=PRODUCTION),
        read_paths=[PRODUCTION, TEST],
        focused_tests=[TEST],
        phases=[
            dict(
                description="Preserve ordinary normalization.",
                code="return subject('  ALICE  ')",
                expected="alice",
                incorrect="alice",
                clause_indices=groups["normal"],
            ),
            dict(
                description="Absent names return nil without throwing.",
                code="local ok, result = pcall(subject, nil); return ok and result == nil",
                expected=True,
                incorrect=False,
                clause_indices=groups["absent"],
            ),
            dict(
                description="Blank names use the default.",
                code="return subject('')",
                expected="anonymous",
                incorrect="",
                clause_indices=groups["blank"],
            ),
        ],
    )


def test_original_only_program_observes_real_missing_behavior_without_candidate_access(
    client,
    headers,
    store,
    installed,
    tmp_path,
):
    _, decision, _, _ = missing_names_parent(client, headers, store)
    with store.connect() as c:
        packet = feature_checks.packet(c, decision)
        assert set(packet) == {
            "version",
            "purpose",
            "original",
            "approved_plan",
            "indexed_clauses",
            "base",
            "files",
            "app_test_runtime",
            "evidence_boundary",
            "input_binding",
            "owner_replies",
            "human_scope",
            "coverage",
            "plan_review",
            "policy_sha256",
        }
        assert not c.execute(select(real_execution_requests)).first()
    compiled, oracle = feature_checks.compile(packet, program(packet))
    assert compiled["kind"] == "independent-feature-checks-v1"
    assert compiled["independently_executed"] is False
    assert "WB_BEHAVIOR feature-integration" in oracle and "bug-reproducer" not in oracle
    original, base, _ = regression_preparation.repository()
    before = (original / PRODUCTION).read_bytes()
    path = tmp_path / "independent-feature.lua"
    path.write_text(oracle)
    # Controlled unit runner: real Lua and original code, no confinement claim.
    observed = subprocess.run(
        ["lua5.1", "-e", PRELUDE, str(path)],
        cwd=original / "tests",
        capture_output=True,
        text=True,
        timeout=3,
    )
    assert observed.returncode == 1, observed.stdout + observed.stderr
    assert "WB_BEHAVIOR feature-integration FAIL" in observed.stdout
    assert "outside-reviewed-results" not in observed.stdout
    assert "WB_OBSERVATION feature-integration 1 expected" in observed.stdout
    assert (original / PRODUCTION).read_bytes() == before
    with store.connect() as c:
        job = (
            c.execute(select(planning_jobs).where(planning_jobs.c.kind == "feature_execution"))
            .mappings()
            .one()
        )
        assert not job["payload"].get("completed_steps")
        assert not c.execute(select(real_execution_requests)).first()


@pytest.mark.parametrize(
    "fault",
    ["uncovered", "invented", "bootstrap", "no-control", "no-difference", "syntax", "source"],
)
def test_model_program_cannot_change_original_source_or_omit_the_observation_contract(
    client,
    headers,
    store,
    installed,
    fault,
):
    _, decision, _, _ = missing_names_parent(client, headers, store)
    with store.connect() as c:
        packet = feature_checks.packet(c, decision)
    proposed = program(packet)
    if fault == "uncovered":
        proposed["phases"][0]["clause_indices"] = []
    elif fault == "invented":
        proposed["read_paths"].append("!!!TwichUI/invented.lua")
    elif fault == "bootstrap":
        proposed["setup"] = dict(kind="module", source=TEST)
    elif fault == "no-control":
        proposed["phases"][0]["incorrect"] = "other"
    elif fault == "no-difference":
        for phase in proposed["phases"]:
            phase["incorrect"] = phase["expected"]
    elif fault == "syntax":
        proposed["phases"][1]["code"] = "local = broken"
    else:
        packet = copy.deepcopy(packet)
        packet["files"][0]["sha256"] = "a" * 64
    with pytest.raises(ValueError):
        feature_checks.compile(packet, proposed)


@pytest.mark.parametrize("completion,allowed", [(6144, True), (6145, False)])
@pytest.mark.parametrize("review", [False, True])
def test_real_local_request_has_bounded_output_and_no_tools(completion, allowed, review):
    import json
    from workbench.planning_provider import LocalProvider, PlanningUnavailable

    schema = feature_checks.Review if review else feature_checks.Response
    proposed = (
        dict(program_binding="a" * 64, ready=False, issues=["Original setup needs inspection."])
        if review
        else dict(
            finding="Original setup needs inspection.",
            inspection=None,
            program=None,
            limitation="No inspected setup.",
        )
    )
    wire = dict(
        coverage=dict(reproducer_format="lua-phases-v1"),
        original=dict(description="An ordinary feature request."),
        files=[],
    )
    seen = []

    class Transport:
        seconds = 180

        def models(self, _):
            return "controlled-model"

        def context_budget(self, secret, body):
            assert body["max_tokens"] == 6144 and body["tools"] == []
            assert body["reasoning_budget_tokens"] == 3072
            seen.append(body)
            return dict(prompt_tokens=100, context_tokens=32768)

        def read(self, request, limit):
            body = json.loads(request.data)
            seen.append(body)
            assert body["max_tokens"] == 6144 and body["tools"] == []
            assert (
                "ORIGINAL" in body["messages"][0]["content"]
                or "original" in body["messages"][0]["content"]
            )
            return json.dumps(
                dict(
                    model="controlled-model",
                    usage=dict(
                        prompt_tokens=100,
                        completion_tokens=completion,
                        total_tokens=100 + completion,
                    ),
                    choices=[
                        dict(finish_reason="stop", message=dict(content=json.dumps(proposed)))
                    ],
                )
            ).encode()

    provider = LocalProvider()
    provider.diagnostics = dict(generation_state="not_started")
    if allowed:
        raw, model, basis = provider._infer(
            Transport(),
            "INERT-FIXTURE",
            schema,
            json.dumps(wire),
            expected_model="controlled-model",
        )
        assert raw == proposed and model == "controlled-model" and basis == "local"
        assert provider.diagnostics["request_completion_limit"] == 6144
    else:
        with pytest.raises(PlanningUnavailable) as failure:
            provider._infer(
                Transport(),
                "INERT-FIXTURE",
                schema,
                json.dumps(wire),
                expected_model="controlled-model",
            )
        assert failure.value.reason == "accounting"
    assert seen
