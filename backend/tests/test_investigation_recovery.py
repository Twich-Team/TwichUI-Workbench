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


@pytest.mark.parametrize("state", ["not_started", "uncertain", "finished"])
def test_zero_turn_accounting_requires_a_verified_nonstart_and_never_erases_unknown(
    eligible, state
):
    c, _, payload = eligible
    payload["diagnostics"]["generation_state"] = state
    c.execute(
        "UPDATE planning_jobs SET payload=? WHERE id=?", (json.dumps(payload), "historical-job")
    )
    c.execute(
        "UPDATE model_attempts SET outcome='failed',usage=? WHERE id=?",
        (
            json.dumps(
                dict(
                    units="tokens",
                    status="not_started",
                    requests_observed=0,
                    values=dict(
                        prompt_tokens=0, completion_tokens=0, reasoning_tokens=0, total_tokens=0
                    ),
                )
            ),
            ATTEMPT,
        ),
    )
    before = list(c.iterdump())
    if state == "not_started":
        assert recovery.no_generation_accounting(c, ATTEMPT)["requests_started"] == 0
    else:
        with pytest.raises(ValueError):
            recovery.no_generation_accounting(c, ATTEMPT)
    assert list(c.iterdump()) == before


def test_unknown_historical_usage_is_not_a_zero_dispatch(eligible):
    c, _, _ = eligible
    before = list(c.iterdump())
    with pytest.raises(ValueError):
        recovery.no_generation_accounting(c, ATTEMPT)
    assert list(c.iterdump()) == before


@pytest.mark.parametrize("fault", [None, "foreign_terminal", "missing_terminal"])
def test_confirmed_interruption_with_unknown_usage_is_separate_from_lost_execution(eligible, fault):
    c, journal, payload = eligible
    payload["diagnostics"].update(generation_state="partial", provider_terminal_confirmed=True)
    c.execute("UPDATE model_attempts SET outcome='incomplete_output' WHERE id=?", (ATTEMPT,))
    c.execute(
        "UPDATE planning_jobs SET payload=? WHERE id=?", (json.dumps(payload), "historical-job")
    )
    events = [
        json.loads(line)
        for line in journal.path.read_text().splitlines()
        if json.loads(line)["phase"] != "status-retrieval"
    ]
    for e in events:
        if e["phase"] == "interrupt-result":
            e.update(acknowledged=True, status="interrupted")
    if fault != "missing_terminal":
        events.insert(
            -1,
            dict(
                phase="lifecycle-terminal",
                thread_id="thread",
                turn_id="foreign" if fault == "foreign_terminal" else "turn",
                status="interrupted",
                usage=None,
            ),
        )
    journal.path.write_text("".join(json.dumps(e) + "\n" for e in events))
    before = list(c.iterdump())
    if fault:
        with pytest.raises(ValueError):
            recovery.record(c, ATTEMPT)
    else:
        result = recovery.record(c, ATTEMPT)
        assert result["final_outcome"] == "interrupted" and result["provider_terminal_confirmed"]
        assert result["successful_cancellation"] and result["final_usage"] is None
        assert not result["partial_output_reusable"]
    assert list(c.iterdump()) == before


@pytest.mark.parametrize('fault',[None,'missing_terminal','usage_mismatch','aggregate_short','prefix_mismatch'])
def test_confirmed_interruption_with_reported_usage_preserves_partial_output_hold(eligible,fault):
    c,journal,payload=eligible
    prefix=dict(prompt_tokens=20,completion_tokens=4,reasoning_tokens=1,total_tokens=24)
    final=dict(prompt_tokens=100,completion_tokens=5,reasoning_tokens=2,total_tokens=105)
    aggregate=dict(status='complete',units='tokens',requests_observed=2,
                   values={k:prefix[k]+final[k] for k in final})
    payload['diagnostics'].update(generation_state='partial',provider_terminal_confirmed=True,
        investigation_requests=2,known_usage=json.dumps(prefix))
    if fault=='prefix_mismatch':
        payload['diagnostics']['known_usage']=json.dumps(dict(prefix,total_tokens=25))
    if fault=='aggregate_short':
        aggregate['values']['total_tokens']=1
    c.execute("UPDATE model_attempts SET outcome='incomplete_output',usage=? WHERE id=?",(json.dumps(aggregate),ATTEMPT))
    c.execute('UPDATE planning_jobs SET payload=? WHERE id=?',(json.dumps(payload),'historical-job'))
    events=list(map(json.loads,journal.path.read_text().splitlines()))
    for e in events:
        if e['phase']=='turn-held':
            e['usage']=dict(final,total_tokens=106) if fault=='usage_mismatch' else final
        if e['phase']=='interrupt-result':
            e.update(acknowledged=True,status='interrupted')
    if fault!='missing_terminal':
        events.insert(-1,dict(phase='lifecycle-terminal',thread_id='thread',
            turn_id='turn',status='interrupted',usage=final))
    journal.path.write_text(''.join(json.dumps(e)+'\n' for e in events))
    before=list(c.iterdump())
    if fault:
        with pytest.raises(ValueError):
            recovery.record(c,ATTEMPT)
    else:
        result=recovery.record(c,ATTEMPT)
        assert result['requests_started']==2 and result['known_completed_requests']==1
        assert result['known_completed_values']==prefix and result['final_usage']==final
        assert result['unknown_final_requests']==0 and result['final_outcome']=='interrupted'
        assert result['successful_cancellation'] and not result['partial_output_reusable']
    assert list(c.iterdump())==before
