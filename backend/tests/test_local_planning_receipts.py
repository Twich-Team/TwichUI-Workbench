"""Local terminal facts remain distinct from content acceptance and late preflight failure."""

import hashlib
import json
from uuid import uuid4, uuid5, UUID
import pytest
from workbench.provider_receipts import completed_local_turns


@pytest.mark.parametrize("fault", [None, "partial", "model", "sum", "late_wire", "duplicate"])
def test_local_completed_prefix_requires_every_terminal_envelope(tmp_path, fault):
    identity = str(uuid4())
    root = tmp_path / "stage"
    root.mkdir(mode=0o700)
    for index in range(3):
        phase = (
            identity
            if index == 0
            else str(uuid5(UUID(identity), "committed-source-turn-" + str(index)))
        )
        p = root / "release/artifacts/runs" / ("planning-evidence-" + phase) / "events.jsonl"
        p.parent.mkdir(parents=True, mode=0o700)
        values = dict(prompt_tokens=3, completion_tokens=2, total_tokens=5)
        if index < 2:
            e = dict(
                phase="envelope",
                generation_state="finished",
                completion="stop",
                accounting_state="complete",
                model_matches=1,
                response_identity=str(uuid4()),
                usage=dict(values=values),
            )
            if index == 1 and fault == "partial":
                e["completion"] = "length"
            if index == 1 and fault == "model":
                e["model_matches"] = 0
            rows = [e, e] if index == 1 and fault == "duplicate" else [e]
        else:
            rows = [dict(phase="request-rejected", generation_state="not_started")]
            if fault == "late_wire":
                rows.append(dict(phase="wire-received"))
        p.write_text("".join(json.dumps(r) + "\n" for r in rows))
        p.chmod(0o600)
    usage = dict(
        status="complete",
        requests_observed=2,
        values=dict(prompt_tokens=6, completion_tokens=4, total_tokens=10),
    )
    if fault == "sum":
        usage["values"]["total_tokens"] = 11
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*.jsonl")}
    if fault:
        with pytest.raises(ValueError):
            completed_local_turns(
                identity,
                usage,
                [root],
                "saved-model",
                dict(generation_state="finished", stopped_request_generation_state="not_started"),
            )
    else:
        proof = completed_local_turns(
            identity,
            usage,
            [root],
            "saved-model",
            dict(generation_state="finished", stopped_request_generation_state="not_started"),
        )
        assert proof["provider_outcome"] == "completed" and proof["actual_model"] == "saved-model"
        assert proof["database_records_unchanged"]
    assert before == {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*.jsonl")
    }


def test_intent_working_packet_is_packed_before_wire_bound(monkeypatch):
    from workbench.planning_provider import LocalProvider, PlanningUnavailable
    from workbench.bug_intent import Response

    packet = dict(
        coverage=dict(scope_plan_format="readable-intent-v1"),
        files=[dict(excerpts=[dict(text="source " * 16000)])],
    )
    monkeypatch.setenv("WORKBENCH_LOCAL_KEY", "unused-controlled")
    seen = []

    def packed(self, schema, encoded, key, model):
        seen.append(len(encoded))
        return {}, model, "local"

    monkeypatch.setattr(LocalProvider, "_request", packed)
    result = LocalProvider().generate(packet, Response, expected_model="saved")
    assert result[1] == "saved" and seen[0] > 98304
    packet["coverage"] = {}
    with pytest.raises(PlanningUnavailable, match="input budget"):
        LocalProvider().generate(packet, Response, expected_model="saved")


@pytest.mark.parametrize("fault", [None, "uncertain", "missing", "duplicate", "wire", "boolean"])
def test_explicit_local_non_dispatch_is_not_inferred_from_empty_text(tmp_path, fault):
    from workbench.provider_receipts import not_started_local_turn

    identity = str(uuid4())
    root = tmp_path / "stage"
    p = root / "release/artifacts/runs" / ("planning-evidence-" + identity) / "events.jsonl"
    p.parent.mkdir(parents=True, mode=0o700)
    e = dict(phase="request-stopped", category="input_budget", generation_state="not_started")
    rows = [] if fault == "missing" else [e, e] if fault == "duplicate" else [e]
    if fault == "wire":
        rows.append(dict(phase="wire-received"))
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    p.chmod(0o600)
    d = dict(
        generation_state="uncertain" if fault == "uncertain" else "not_started",
        investigation_requests=False if fault == "boolean" else 0,
        stopped_request_generation_state="not_started",
        stopped_request_category="input_budget",
    )
    if fault:
        with pytest.raises(ValueError):
            not_started_local_turn(identity, [root], d)
    else:
        proof = not_started_local_turn(identity, [root], d)
        assert (
            proof["requests"] == 0
            and proof["usage_observation"] is None
            and proof["database_records_unchanged"]
        )
