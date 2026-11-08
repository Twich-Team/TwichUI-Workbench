"""Complete terminal receipts survive a later local crash; no historical rewrite."""

import hashlib
import json
from uuid import uuid5, UUID

import pytest

from test_codex_quarantine import historical  # noqa: F401
from test_investigation_recovery import eligible  # noqa: F401
from workbench import investigation_recovery as recovery
from workbench.codex_journal import Journal
from workbench.codex_quarantine import ATTEMPT


@pytest.fixture
def completed(eligible, tmp_path, monkeypatch):  # noqa: F811
    c, _, payload = eligible
    caps = payload["recovery_capabilities"]
    stage = tmp_path / "installed"
    release = stage / "release"
    (release / "docs").mkdir(parents=True, mode=0o700)
    (release / "docs/OWNER_READONLY_RECOVERY_20261108.md").write_bytes(
        (recovery.ROOT / "docs/OWNER_READONLY_RECOVERY_20261108.md").read_bytes()
    )
    monkeypatch.setattr(recovery, "ROOT", release)
    monkeypatch.setattr(recovery, "capabilities", lambda: caps)
    provider = stage / "artifacts/codex-evidence"
    provider.mkdir(parents=True, mode=0o700)
    monkeypatch.setattr(recovery, "EVIDENCE", provider)
    sums = dict(prompt_tokens=0, completion_tokens=0, reasoning_tokens=0, total_tokens=0)
    paths = []
    for n in range(2):
        identity = (
            ATTEMPT if n == 0 else str(uuid5(UUID(ATTEMPT), "committed-source-turn-" + str(n)))
        )
        directory = release / "artifacts/runs" / ("planning-evidence-" + identity)
        directory.mkdir(mode=0o700, parents=True)
        actual = dict(
            prompt_tokens=100 + n, completion_tokens=3, reasoning_tokens=1, total_tokens=103 + n
        )
        text = json.dumps(dict(finding="controlled " + str(n)))
        journal = Journal(provider, b"controlled request")
        journal.record("isolation-verified", capabilities=caps)
        journal.record("turn-started", thread_id="thread" + str(n), turn_id="turn" + str(n))
        journal.record(
            "lifecycle-terminal",
            status="completed",
            thread_id="thread" + str(n),
            turn_id="turn" + str(n),
        )
        journal.record(
            "turn-finished",
            status="completed",
            usage=actual,
            output_excerpt=text,
            output_bytes=len(text.encode()),
            output_sha256=hashlib.sha256(text.encode()).hexdigest(),
        )
        journal.record("connection-closed", status="local-producer-closed")
        journal.close()
        content = dict(
            text=text,
            truncated=False,
            credential_redacted=False,
            original_sha256=hashlib.sha256(text.encode()).hexdigest(),
        )

        def write(path, value):
            path.write_text(json.dumps(value) + "\n")
            path.chmod(0o600)

        write(directory / "provider-content.json", content)
        events = [
            dict(
                phase="provider-result",
                provider="codex",
                generation_state="finished",
                accounting_state="complete",
                usage=dict(values=actual),
            )
        ]
        paths.append((directory / "events.jsonl", journal.path))
        if n == 0:
            events.append(
                dict(
                    phase="source-followup",
                    turn=2,
                    evidence_id=str(uuid5(UUID(ATTEMPT), "committed-source-turn-1")),
                )
            )
        write(directory / "events.jsonl", events[0])
        if n == 0:
            with (directory / "events.jsonl").open("a") as out:
                out.write(json.dumps(events[1]) + "\n")
        for k, v in actual.items():
            sums[k] += v
    parent = paths[0][0]
    with parent.open("a") as out:
        out.write(
            json.dumps(
                dict(
                    phase="payload-decoded",
                    generation_state="finished",
                    usage=dict(status="complete", requests_observed=2, values=sums),
                )
            )
            + "\n"
        )
    payload["diagnostics"] = dict(category="interrupted_unknown")
    c.execute(
        "UPDATE planning_jobs SET payload=? WHERE id=?", (json.dumps(payload), "historical-job")
    )
    c.commit()
    return c, paths, sums


def test_completed_receipts_are_recovered_without_rewriting_unknown_history(completed):
    c, _, sums = completed
    before = list(c.iterdump())
    receipt = recovery.record(c, ATTEMPT)
    assert receipt["final_outcome"] == "completed" and receipt["final_usage"] == sums
    assert receipt["requests_started"] == receipt["known_completed_requests"] == 2
    assert receipt["unknown_final_requests"] == 0 and not receipt["partial_output_reusable"]
    assert not receipt["successful_cancellation"]
    assert recovery.accounting(c, ATTEMPT) == receipt
    assert list(c.iterdump()) == before


@pytest.mark.parametrize(
    "fault", ["open", "terminal", "content", "aggregate", "lineage", "duplicate", "symlink"]
)
def test_missing_conflicting_or_partial_evidence_cannot_claim_completed_recovery(completed, fault):
    c, paths, _ = completed
    parent, provider = paths[0]
    if fault == "symlink":
        saved = parent.with_suffix(".saved")
        parent.rename(saved)
        parent.symlink_to(saved)
    elif fault == "content":
        p = parent.parent / "provider-content.json"
        d = json.loads(p.read_text())
        d["truncated"] = True
        p.write_text(json.dumps(d))
    elif fault == "duplicate":
        provider.with_name("duplicate.jsonl").write_bytes(provider.read_bytes())
        provider.with_name("duplicate.jsonl").chmod(0o600)
    else:
        p = parent if fault in {"aggregate", "lineage"} else provider
        events = list(map(json.loads, p.read_text().splitlines()))
        if fault == "open":
            events = [e for e in events if e["phase"] != "connection-closed"]
        if fault == "terminal":
            next(e for e in events if e["phase"] == "lifecycle-terminal")["status"] = "failed"
        if fault == "aggregate":
            events[-1]["usage"]["values"]["total_tokens"] += 1
        if fault == "lineage":
            next(e for e in events if e["phase"] == "source-followup")["evidence_id"] = "unbound"
        p.write_text("".join(json.dumps(e) + "\n" for e in events))
    with pytest.raises((ValueError, OSError)):
        recovery.record(c, ATTEMPT)
