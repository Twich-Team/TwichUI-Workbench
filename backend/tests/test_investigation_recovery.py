"""Standing policy applies to proven capabilities, never just absence of writes."""

import json

import pytest

from test_codex_quarantine import historical  # noqa: F401
from workbench import investigation_recovery as recovery
from workbench.codex_journal import Journal
from workbench.codex_quarantine import ATTEMPT
from workbench.planning_reconciliation import rows


@pytest.fixture
def eligible(historical, tmp_path, monkeypatch):  # noqa: F811
    c, _, _, _ = historical
    receipts = tmp_path / "receipts"
    receipts.mkdir(mode=0o700)
    evidence = tmp_path / "provider"
    monkeypatch.setattr(recovery, "RECEIPTS", receipts)
    monkeypatch.setattr(recovery, "EVIDENCE", evidence)
    journal = Journal(evidence, b"private prompt not retained")
    caps = recovery.capabilities()
    journal.record("isolation-verified", capabilities=caps)
    journal.record("turn-started", thread_id="thread", turn_id="turn")
    journal.record("interrupt-result", acknowledged=False, status=None)
    journal.record("status-retrieval", status="unavailable")
    journal.record("turn-held", status="uncertain", partial=True)
    journal.record("connection-closed", status="local-producer-closed")
    journal.close()
    raw = rows(c, "SELECT payload FROM planning_jobs WHERE id=?", ("historical-job",))[0]["payload"]
    payload = json.loads(raw)
    payload["recovery_capabilities"] = caps
    payload["diagnostics"]["provider_receipt"] = journal.identifier
    c.execute(
        "UPDATE planning_jobs SET payload=? WHERE id=?", (json.dumps(payload), "historical-job")
    )
    c.commit()
    return c, journal, payload


def test_standing_receipt_preserves_history_unknown_usage_and_no_execution(eligible):
    c, journal, payload = eligible
    before = list(c.iterdump())
    receipt = recovery.record(c, ATTEMPT)
    assert receipt["final_outcome"] == "unknown" and receipt["final_usage"] is None
    assert not receipt["successful_cancellation"] and not receipt["partial_output_reusable"]
    assert receipt["requests_started"] == 2 and receipt["known_completed_requests"] == 1
    assert recovery.record(c, ATTEMPT) == recovery.accounting(c, ATTEMPT) == receipt
    assert list(c.iterdump()) == before
    assert not c.execute("SELECT 1 FROM proposals").fetchone()


@pytest.mark.parametrize(
    "fault",
    [
        "source_writes",
        "execution_dispatch",
        "external_action_tools",
        "missing_caps",
        "missing_recovery",
        "open_connection",
        "altered_journal",
        "running_job",
        "implementation",
        "changed_history",
        "public_receipt",
        "symlink",
    ],
)
def test_consequential_or_unverified_operations_stay_held(eligible, fault):
    c, journal, payload = eligible
    if fault in {"source_writes", "execution_dispatch", "external_action_tools"}:
        payload["recovery_capabilities"][fault] = True
    elif fault == "missing_caps":
        payload.pop("recovery_capabilities")
    elif fault in {"missing_recovery", "open_connection", "altered_journal"}:
        rows = [json.loads(x) for x in journal.path.read_text().splitlines()]
        phase = (
            "status-retrieval"
            if fault == "missing_recovery"
            else "connection-closed"
            if fault == "open_connection"
            else "isolation-verified"
        )
        journal.path.write_text("".join(json.dumps(x) + "\n" for x in rows if x["phase"] != phase))
    elif fault == "running_job":
        c.execute("UPDATE planning_jobs SET status='running',lease='old' WHERE id='historical-job'")
    elif fault == "implementation":
        c.execute("UPDATE model_attempts SET purpose='implementation' WHERE id=?", (ATTEMPT,))
    elif fault in {"changed_history", "public_receipt", "symlink"}:
        recovery.record(c, ATTEMPT)
        p = recovery.path_for(ATTEMPT)
        if fault == "changed_history":
            c.execute("UPDATE planning_jobs SET message='new' WHERE id='historical-job'")
        elif fault == "public_receipt":
            p.chmod(0o644)
        else:
            target = p.with_suffix(".saved")
            p.rename(target)
            p.symlink_to(target)
    if fault in {"source_writes", "execution_dispatch", "external_action_tools", "missing_caps"}:
        c.execute(
            "UPDATE planning_jobs SET payload=? WHERE id=?", (json.dumps(payload), "historical-job")
        )
    with pytest.raises((ValueError, OSError)):
        recovery.accounting(c, ATTEMPT) if fault in {
            "changed_history",
            "public_receipt",
            "symlink",
        } else recovery.record(c, ATTEMPT)


def test_restart_reads_immutable_disposition_and_changed_late_state_cannot_gain_authority(eligible):
    c, _, _ = eligible
    receipt = recovery.record(c, ATTEMPT)
    # New reader reconstructs from disk; no in-memory approval/partial output.
    assert recovery.accounting(c, ATTEMPT) == receipt
    c.execute("UPDATE planning_jobs SET status='completed' WHERE id='historical-job'")
    with pytest.raises(ValueError, match="fenced"):
        recovery.accounting(c, ATTEMPT)


def test_worker_recovery_and_normal_admission_need_no_repeated_owner_exception(eligible, store):
    from acceptance_policy import campaign_admission, OUTCOME

    c, _, _ = eligible
    before = list(c.iterdump())
    assert recovery.recover_one(store)
    assert not recovery.recover_one(store)
    at = c.execute("SELECT started_at FROM model_attempts WHERE id=?", (ATTEMPT,)).fetchone()[0]
    request = dict(
        action="submit",
        choice="bug-flight-paths",
        opened=at,
        prior=[ATTEMPT],
        outcome=dict(attempt=200, revision=100, opened_at=at, prior_attempts=[ATTEMPT]),
    )
    campaign_admission(
        c, request, dict(choice="bug-flight-paths", provider="codex"), dict(outcome=OUTCOME)
    )
    assert list(c.iterdump()) == before
