"""Measure the actual nested JSON envelope; never trade away mandatory context."""

import copy
import json

import pytest

from workbench.codex_provider import encode_request, SECONDS, BUG_SECONDS
from workbench.planning_transport import PlanningUnavailable
from workbench.request_context import fit
from workbench.source_memory import retain, requested_visibility


def context():
    path = "tests/ordinary.lua"

    def window(line, text, coverage):
        return dict(
            path=path,
            line=line,
            through_line=line + len(text.splitlines()) - 1,
            text=text,
            coverage=coverage,
        )

    prelude = window(1, "local module = {}", "module prelude")
    requested = window(100, "return module.run()", "requested committed lines")
    declaration = window(
        99, "function module.run()", "committed enclosing declaration or registration"
    )
    bootstrap = window(20, "local client = createClient()", "complete committed test bootstrap")
    usage = window(80, 'controller:Begin("preview", true)', "committed public usage example")
    older = window(
        400, "\n".join('local s%d = "\\\\ quoted é"' % i for i in range(300)), "prior read"
    )
    packet = dict(
        base="committed",
        plan_review="source-grounded-lua-v1",
        files=[dict(path=path, excerpts=[prelude])],
        inspection_windows=[older],
    )
    current = [requested, declaration, bootstrap, usage]
    wire = dict(
        files=copy.deepcopy(packet["files"]),
        report="unchanged owner report",
        coverage=dict(write_paths=[path]),
        self_review=dict(decision="unapproved"),
    )
    retain(wire, packet, current, 128000, requested=[requested])
    return packet, current, requested, wire


def encoder(wire):
    return encode_request(
        "controlled",
        None,
        "Instructions\n" + json.dumps(wire, ensure_ascii=True),
        dict(description="large schema " + ('"\\' * 400)),
        BUG_SECONDS,
    )


def test_full_encoded_envelope_fits_preserving_requested_declarations_and_authority():
    packet, current, requested, wire = context()
    original = copy.deepcopy((packet, current, wire))
    limit = len(encoder(wire)) - 5000
    assert wire["source_memory"]["retained_characters"] < limit < len(encoder(wire))
    packed, facts = fit(wire, packet, current, [requested], encoder, limit)
    assert len(encoder(packed)) <= limit
    assert facts["before_bytes"] > facts["encoded_request_bytes"]
    assert facts["encoded_request_bytes"] == len(encoder(packed))
    assert facts["requested_source_preserved"]
    assert (packet, current, wire) == original
    for key in ("report", "coverage", "self_review"):
        assert packed[key] == wire[key]
    protected = [
        dict(w, coverage="requested committed lines")
        for w in [
            *current,
            dict(packet["files"][0]["excerpts"][0], path=packet["files"][0]["path"]),
        ]
    ]
    assert all(f["provided"] for f in requested_visibility(packed, protected))
    assert packed["source_memory"]["omitted_characters"] > 0


def test_already_fitting_request_is_unchanged():
    packet, current, requested, wire = context()
    packed, facts = fit(wire, packet, current, [requested], encoder, len(encoder(wire)))
    assert packed == wire and packed is not wire
    assert facts["before_bytes"] == facts["encoded_request_bytes"]


@pytest.mark.parametrize("cancelled", [False, True])
def test_impossible_or_cancelled_context_never_discards_mandatory_source(cancelled):
    packet, current, requested, wire = context()
    with pytest.raises(PlanningUnavailable) as held:
        fit(wire, packet, current, [requested], encoder, 1, lambda: cancelled)
    assert held.value.reason == ("cancelled" if cancelled else "context_budget")


@pytest.mark.parametrize("seconds", [SECONDS, BUG_SECONDS])
def test_shared_encoder_preserves_existing_protocol_bytes(seconds):
    prompt = 'untrusted "quotes" \\ and é\n'
    schema = dict(type="object", description="unchanged")
    legacy = dict(model="actual-selection", effort=None, prompt=prompt, schema=schema)
    if seconds != SECONDS:
        legacy["request_seconds"] = seconds
    assert (
        encode_request("actual-selection", None, prompt, schema, seconds)
        == json.dumps(legacy, ensure_ascii=True, allow_nan=False).encode()
    )


def test_larger_bounded_working_context_keeps_complementary_inspected_source_together():
    from workbench.codex_provider import MAX_BUG_INPUT

    packet, current, requested, wire = context()
    # Previously repeated reads need both halves together, not alternating eviction.
    extra = dict(
        path=packet["files"][0]["path"],
        line=1000,
        through_line=2799,
        text="\n".join('local value%d = "committed context"' % n for n in range(1800)),
    )
    packet["inspection_windows"].append(extra)
    retain(wire, packet, current, 262144, requested=[requested])
    packed, _ = fit(wire, packet, current, [requested], encoder, MAX_BUG_INPUT)
    assert packed["source_memory"]["omitted_characters"] == 0
    assert len(encoder(packed)) <= MAX_BUG_INPUT
    assert requested_visibility(packed, [dict(extra, coverage="requested committed lines")])[0][
        "provided"
    ]


@pytest.mark.parametrize("limit", [65536, 1800])
def test_local_exact_packer_preserves_fresh_source_and_stops_impossible_request(limit):
    from workbench.planning_provider import LocalProvider
    from workbench.bug_investigation_stage import InvestigationResponse

    packet, current, requested, wire = context()
    packet["plan_review"] = "source-grounded-intent-v1"
    packet["inspection_windows"] *= 120
    retain(wire, packet, current, 128000, requested=[requested])
    # Full history remains in the packet; the model gets compact provenance.
    assert wire["source_memory"]["read_ranges"] == {packet["files"][0]["path"]: [[400, 699]]}
    original = copy.deepcopy((packet, wire))
    calls = []

    class Captured(Exception):
        pass

    class Transport:
        seconds = 300

        def models(self, _):
            return "saved-local"

        def context_budget(self, _, body):
            return dict(prompt_tokens=15000, context_tokens=32768)

        def read(self, request, bound):
            outgoing = json.loads(json.loads(request.data)["messages"][1]["content"])
            assert len(request.data) <= 65536
            assert outgoing["report"] == wire["report"]
            assert outgoing["coverage"] == wire["coverage"]
            assert requested_visibility(outgoing, [requested])[0]["provided"]
            calls.append(1)
            raise Captured()

    provider = LocalProvider()
    provider.request_packer = lambda outgoing, encode, actual_limit, **constraints: fit(
        outgoing, packet, current, [requested], encode, min(actual_limit, limit), **constraints
    )
    with pytest.raises(Captured if limit == 65536 else PlanningUnavailable):
        provider._infer(
            Transport(),
            "inert",
            InvestigationResponse,
            json.dumps(wire),
            expected_model="saved-local",
        )
    assert calls == ([1] if limit == 65536 else [])
    assert (packet, wire) == original


def test_token_limit_trims_optional_history_even_when_serialized_bytes_fit():
    packet, current, requested, wire = context()
    initial = copy.deepcopy((packet, wire))
    observed = []

    def measure(value):
        count = len(json.dumps(value)) // 3
        observed.append(count)
        return count, observed[0] - 1000

    packed, facts = fit(
        wire, packet, current, [requested], encoder, len(encoder(wire)) + 10000, measure=measure
    )
    assert len(observed) > 1 and observed[-1] <= observed[0] - 1000
    assert (
        facts["prompt_tokens"] == observed[-1] and facts["prompt_token_limit"] == observed[0] - 1000
    )
    assert packed["report"] == wire["report"] and packed["coverage"] == wire["coverage"]
    assert requested_visibility(packed, [requested])[0]["provided"]
    assert (packet, wire) == initial


def test_token_limit_cannot_silently_discard_required_reads_or_approval_data():
    packet, current, requested, wire = context()
    with pytest.raises(PlanningUnavailable) as held:
        fit(
            wire,
            packet,
            current,
            [requested],
            encoder,
            len(encoder(wire)) + 10000,
            measure=lambda value: (30000, 1),
        )
    assert held.value.reason == "context_budget"


@pytest.mark.parametrize("invalid", [None, True, -1])
def test_local_packer_holds_on_unusable_tokenizer_accounting(invalid):
    from workbench.planning_provider import LocalProvider
    from workbench.bug_investigation_stage import InvestigationResponse

    packet, current, requested, wire = context()
    wire["navigation"] = "read-only names " * 3000

    class Transport:
        seconds = 300

        def models(self, _):
            return "saved-local"

        def context_budget(self, _, body):
            return dict(prompt_tokens=invalid, context_tokens=32768)

        def read(self, *args):
            raise AssertionError("Unaccounted model request")

    provider = LocalProvider()
    provider.request_packer = lambda outgoing, encode, limit, **constraints: fit(
        outgoing, packet, current, [requested], encode, limit, **constraints
    )
    with pytest.raises(PlanningUnavailable) as held:
        provider._infer(
            Transport(),
            "inert",
            InvestigationResponse,
            json.dumps(wire),
            expected_model="saved-local",
        )
    assert held.value.reason == "context_budget"


def test_local_packer_recounts_exact_envelope_and_sends_only_after_optional_history_fits():
    from workbench.planning_provider import LocalProvider
    from workbench.bug_investigation_stage import InvestigationResponse

    packet, current, requested, wire = context()
    wire["navigation"] = "read-only names " * 3000
    original = copy.deepcopy((packet, wire))
    counts = []
    calls = []

    class Captured(Exception):
        pass

    class Transport:
        seconds = 300

        def models(self, _):
            return "saved-local"

        def context_budget(self, _, body):
            outgoing = json.loads(body["messages"][1]["content"])
            count = 20000 + outgoing["source_memory"]["retained_characters"]
            counts.append(count)
            return dict(prompt_tokens=count, context_tokens=32768)

        def read(self, request, limit):
            body = json.loads(request.data)
            outgoing = json.loads(body["messages"][1]["content"])
            assert counts[0] > 22000 and counts[-1] <= 22000
            assert requested_visibility(outgoing, [requested])[0]["provided"]
            assert outgoing["report"] == wire["report"] and outgoing["coverage"] == wire["coverage"]
            assert outgoing["navigation"] == wire["navigation"] and body["model"] == "saved-local"
            calls.append(1)
            raise Captured()

    provider = LocalProvider()
    provider.request_packer = lambda outgoing, encode, limit, **constraints: fit(
        outgoing, packet, current, [requested], encode, limit, **constraints
    )
    with pytest.raises(Captured):
        provider._infer(
            Transport(),
            "inert",
            InvestigationResponse,
            json.dumps(wire),
            expected_model="saved-local",
        )
    assert calls == [1] and len(counts) > 1 and (packet, wire) == original
