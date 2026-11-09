"""Continue exact read evidence without losing focus or manufacturing gap coverage."""

import copy
import json
from uuid import uuid4, uuid5, UUID

import pytest

from workbench import bug_source_planning as source
from workbench.investigation_memory import compact_packet, recent_focus
from workbench.source_memory import ranges


@pytest.fixture
def source_packet(monkeypatch):
    raw = ("\n".join(f"local line{n} = {n}" for n in range(1, 401)) + "\n").encode()
    path = "!!!TwichUI/ordinary.lua"
    item = dict(path=path, sha256="pinned", excerpts=[])
    calls = []

    def committed(p, base):
        assert p == path and base == "pinned-base"
        calls.append(p)
        return raw, copy.deepcopy(item)

    monkeypatch.setattr(source, "committed", committed)

    def window(start, end):
        return dict(
            path=path,
            sha256="pinned",
            line=start,
            through_line=end,
            text="\n".join(raw.decode().splitlines()[start - 1 : end]),
            coverage="prior committed read",
        )

    initial = dict(
        base="pinned-base",
        policy_sha256="policy",
        plan_review="source-grounded-intent-v1",
        files=[item],
    )
    cached = dict(
        copy.deepcopy(initial),
        inspection_windows=[window(1, 100), window(50, 150), window(151, 200), window(250, 300)],
    )
    return initial, cached, calls


def test_cache_compaction_preserves_union_and_gaps_without_mutating_receipts(source_packet):
    initial, cached, calls = source_packet
    before = copy.deepcopy(cached)
    compact = compact_packet(initial, cached)
    assert cached == before
    assert ranges(compact["inspection_windows"]) == ranges(cached["inspection_windows"])
    assert [(w["line"], w["through_line"]) for w in compact["inspection_windows"]] == [
        (1, 159),
        (160, 200),
        (250, 300),
    ]
    assert all("requested_through_line" not in w for w in compact["inspection_windows"])
    assert source.source_characters(compact) == source.source_characters(cached)
    # Each verification re-reads a file once, rather than once per overlapping window.
    assert len(calls) == 3


@pytest.mark.parametrize("change", ["text", "hash", "out_of_range", "base", "new_key"])
def test_corrupted_cache_remains_a_hold(source_packet, change):
    initial, cached, _ = source_packet
    if change == "text":
        cached["inspection_windows"][0]["text"] = "forged"
    elif change == "hash":
        cached["inspection_windows"][0]["sha256"] = "changed"
    elif change == "out_of_range":
        cached["inspection_windows"][0]["through_line"] = 999
    elif change == "base":
        cached["base"] = "changed"
    else:
        cached["approved"] = True
    with pytest.raises(ValueError):
        compact_packet(initial, cached)


@pytest.mark.parametrize("focus", [[{}], ["uninspected.lua"], ["!!!TwichUI/ordinary.lua"] * 2])
def test_invalid_attention_does_not_become_source_permission(source_packet, focus):
    initial, cached, _ = source_packet
    cached["inspection_focus"] = focus
    with pytest.raises(ValueError):
        source.verify_packet(initial, cached)


def test_recent_focus_retains_completed_ninth_turn_across_search_only_turn(tmp_path, source_packet):
    initial, cached, _ = source_packet
    packet = compact_packet(initial, cached)
    identity = str(uuid4())

    def journal(index, rows):
        turn = (
            identity if index == 0 else str(uuid5(UUID(identity), f"committed-source-turn-{index}"))
        )
        directory = tmp_path / "artifacts/runs" / ("planning-evidence-" + turn)
        directory.mkdir(parents=True, mode=0o700)
        path = directory / "events.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        path.chmod(0o600)

    complete = dict(
        phase="envelope",
        generation_state="finished",
        accounting_state="complete",
        completion="stop",
        model_matches=1,
    )
    journal(
        8,
        [
            complete,
            dict(
                phase="source-read-result",
                windows=[
                    dict(
                        path="!!!TwichUI/ordinary.lua", sha256="pinned", line=150, through_line=175
                    )
                ],
            ),
        ],
    )
    journal(9, [complete, dict(phase="source-read-result", windows=[])])
    assert recent_focus(identity, 10, packet, [tmp_path]) == ["!!!TwichUI/ordinary.lua"]
    # Content alone cannot establish completion.
    journal(
        7,
        [
            dict(
                phase="source-read-result",
                windows=[dict(path="forbidden.lua", sha256="other", line=1, through_line=1)],
            )
        ],
    )
    assert recent_focus(identity, 8, packet, [tmp_path]) == []


@pytest.mark.parametrize("allowed", [False, True])
def test_changed_installation_binding_needs_verified_coordinate_only_move(
    source_packet, monkeypatch, allowed
):
    from workbench.investigation_memory import relocated_packet
    from workbench.visual_scope import digest
    from workbench import trusted_preparation

    initial, cached, _ = source_packet
    policy = dict(current="private configured installation")
    before = copy.deepcopy(cached)
    observed = []

    def relocate(engine, sha, current):
        observed.append((engine, sha, current))
        return allowed

    monkeypatch.setattr(trusted_preparation, "relocated_policy", relocate)
    if not allowed:
        with pytest.raises(ValueError, match="installation coordinates"):
            relocated_packet(cached, policy, "existing engine")
    else:
        current = relocated_packet(cached, policy, "existing engine")
        assert current["policy_sha256"] == digest(policy)
        assert ranges(current["inspection_windows"]) == ranges(cached["inspection_windows"])
        assert current["files"] == cached["files"]
    assert cached == before
    assert observed == [("existing engine", cached["policy_sha256"], policy)]
