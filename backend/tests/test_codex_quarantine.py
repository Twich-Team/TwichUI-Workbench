"""Specific owner exception retains unknown history; every other uncertainty holds."""

from datetime import datetime, timezone
import json
import sqlite3

import pytest
from workbench import codex_quarantine as q
from workbench.db import intakes, planning_jobs, model_attempts
from workbench.planning_reconciliation import rows
from workbench.visual_scope import digest
from acceptance_policy import campaign_admission, OUTCOME


@pytest.fixture
def historical(store, tmp_path, monkeypatch):
    at = datetime.now(timezone.utc).isoformat()
    payload = dict(
        scenario_generation_claim=dict(pid=99999999, boot="old-boot", start="1"),
        diagnostics=dict(
            generation_state="uncertain",
            investigation_requests=1,
            known_usage=json.dumps(
                dict(
                    prompt_tokens=31352,
                    completion_tokens=157,
                    reasoning_tokens=19,
                    total_tokens=31509,
                )
            ),
        ),
    )
    with store.begin() as c:
        c.execute(
            intakes.insert().values(
                id="historical",
                payload=dict(kind="issue", local_planning="codex-once"),
                status="failed",
                revision=1,
                created_at=at,
            )
        )
        c.execute(
            planning_jobs.insert().values(
                id="historical-job",
                intake_id="historical",
                kind="planning",
                revision=1,
                status="failed",
                attempts=1,
                payload=payload,
                message="Original uncertain output.",
                created_at=at,
                finished_at=at,
            )
        )
        c.execute(
            model_attempts.insert().values(
                id=q.ATTEMPT,
                job_id="historical-job",
                task_id="historical",
                source="workbench",
                backend="codex",
                purpose="planning",
                routing_reason="Explicit Codex.",
                started_at=at,
                finished_at=at,
                outcome="interrupted_unknown",
                usage=None,
            )
        )

    def snapshot(c):
        return dict(
            attempt=rows(c, "SELECT * FROM model_attempts WHERE id=?", (q.ATTEMPT,))[0],
            job=rows(c, "SELECT * FROM planning_jobs WHERE id=?", ("historical-job",))[0],
            intake=rows(c, "SELECT * FROM intakes WHERE id=?", ("historical",))[0],
        )

    connection = sqlite3.connect(store.url.database)
    prior = snapshot(connection)
    monkeypatch.setattr(q, "BINDING", digest(prior))
    monkeypatch.setattr(q, "RECEIPTS", tmp_path)
    monkeypatch.setattr(q, "local_generation_absent", lambda: True)
    yield connection, prior, snapshot, at
    connection.close()


def test_specific_quarantine_unblocks_fresh_admission_without_modifying_any_history(historical):
    c, prior, snapshot, at = historical
    request = dict(
        action="submit",
        choice="bug-flight-paths",
        opened=at,
        prior=[q.ATTEMPT],
        outcome=dict(attempt=200, revision=100, opened_at=at, prior_attempts=[q.ATTEMPT]),
    )

    def admit():
        campaign_admission(
            c, request, dict(choice="bug-flight-paths", provider="codex"), dict(outcome=OUTCOME)
        )

    with pytest.raises(ValueError, match="uncertain"):
        admit()
    receipt = q.record(c)
    assert receipt["final_outcome"] == "unknown" and receipt["final_usage"] is None
    assert receipt["quarantined_turn"] == 2 and not receipt["successful_cancellation"]
    assert not receipt["partial_output_reusable"] and receipt["requests_started"] == 2
    assert q.record(c) == q.accounting(c, q.ATTEMPT) == receipt
    assert snapshot(c) == prior
    admit()
    assert snapshot(c) == prior


@pytest.mark.parametrize(
    "fault",
    [
        "other_id",
        "changed_history",
        "live_producer",
        "changed_authority",
        "changed_receipt",
        "symlink",
        "public_file",
        "truncated",
        "missing_receipt",
        "local_active",
    ],
)
def test_quarantine_denies_missing_changed_or_unapproved_authority(historical, monkeypatch, fault):
    c, prior, snapshot, at = historical
    if fault == "other_id":
        with pytest.raises(ValueError):
            q.accounting(c, "00000000-0000-0000-0000-000000000000")
        return
    if fault == "local_active":
        monkeypatch.setattr(q, "local_generation_absent", lambda: False)
        with pytest.raises(ValueError):
            q.record(c)
        assert snapshot(c) == prior
        return
    path = q.receipt_path(q.ATTEMPT)
    if fault != "missing_receipt":
        q.record(c)
    if fault == "changed_history":
        c.execute("UPDATE planning_jobs SET message='Changed' WHERE id='historical-job'")
    if fault == "live_producer":
        monkeypatch.setattr(q, "alive", lambda _: True)
    if fault == "changed_authority":
        monkeypatch.setattr(q, "AUTHORITY_SHA256", "0" * 64)
    if fault == "changed_receipt":
        data = json.loads(path.read_text())
        data["successful_cancellation"] = True
        path.write_text(json.dumps(data))
    if fault == "symlink":
        other = path.with_suffix(".original")
        path.rename(other)
        path.symlink_to(other)
    if fault == "public_file":
        path.chmod(0o644)
    if fault == "truncated":
        path.write_text("{")
    with pytest.raises((ValueError, OSError)):
        q.accounting(c, q.ATTEMPT)
    if fault != "changed_history":
        assert snapshot(c) == prior
