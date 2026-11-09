"""Campaign authority is finite, additive to ordinary task and provider gates."""
import copy
from datetime import datetime, timezone
import json
import sqlite3

import pytest
from sqlalchemy import text

from acceptance_policy import CAMPAIGN, OUTCOME, campaign_admission


def request():
    opened = datetime.now(timezone.utc).isoformat()
    return dict(action="submit", choice="bug-flight-paths", opened=opened, prior=[],
                campaign=dict(attempt=1, revision=0, opened_at=opened, prior_attempts=[]))


def test_standing_authority_removes_old_delivery_caps_but_keeps_prior_reconciliation(store):
    value = request()
    value['outcome'] = value.pop('campaign')
    value['outcome'].update(attempt=25, revision=18)
    for i in range(12):
        add_intake(store, value, str(i))
    with sqlite3.connect(store.url.database) as connection:
        campaign_admission(connection, value, dict(choice='bug-flight-paths', provider='local'), dict(outcome=OUTCOME))
    changed = copy.deepcopy(value)
    changed['outcome']['opened_at'] = 'changed'
    with sqlite3.connect(store.url.database) as connection, pytest.raises(ValueError, match='receipt'):
        campaign_admission(connection, changed, dict(choice='bug-flight-paths', provider='local'), dict(outcome=OUTCOME))


@pytest.mark.parametrize('historical_label', [1, 3])
def test_standing_codex_accounting_uses_saved_operation_bound_not_old_campaign_observation_ceiling(store, historical_label):
    from test_planning_limits import current
    value=request()
    value['outcome']=value.pop('campaign')
    value['outcome'].update(attempt=historical_label,revision=20,prior_attempts=['attempt'])
    value['prior']=['attempt']
    add_intake(store,value,'earlier')
    consent=dict(current(),investigation_version=10,max_local_requests=16,
        max_codex_requests=16,max_total_model_requests=16,codex_request_deadline_seconds=300)
    usage=dict(units='tokens',status='complete',requests_observed=16,max_request_total_tokens=150000,
        values=dict(prompt_tokens=1999900,completion_tokens=100,total_tokens=2000000))
    with store.begin() as c:
        c.execute(text('''INSERT INTO planning_jobs
          (id,intake_id,kind,revision,status,attempts,payload,message,created_at,finished_at)
          VALUES ('job','earlier','planning',1,'failed',1,:payload,'settled',:at,:at)'''),
          dict(payload=json.dumps(dict(local_planning_receipt=consent)),at=value['opened']))
        c.execute(text('''INSERT INTO model_attempts
          (id,job_id,task_id,source,backend,purpose,routing_reason,started_at,finished_at,outcome,usage)
          VALUES ('attempt','job','earlier','workbench','codex','planning','controlled',:at,:at,'failed',:usage)'''),
          dict(at=value['opened'],usage=json.dumps(usage)))
    with sqlite3.connect(store.url.database) as c:
        campaign_admission(c,value,dict(choice='bug-flight-paths',provider='codex'),dict(outcome=OUTCOME))
    usage['requests_observed']=17
    with store.begin() as c:
        c.execute(text('UPDATE model_attempts SET usage=:usage'),dict(usage=json.dumps(usage)))
    with sqlite3.connect(store.url.database) as c, pytest.raises(ValueError,match='observations'):
        campaign_admission(c,value,dict(choice='bug-flight-paths',provider='codex'),dict(outcome=OUTCOME))


def check(store, value, *, choice="bug-flight-paths", limits=None):
    with sqlite3.connect(store.url.database) as connection:
        campaign_admission(connection, value, dict(choice=choice, provider="local"),
                           dict(campaign=limits or CAMPAIGN))


def add_intake(store, value, identity, choice="bug-flight-paths"):
    payload = dict(kind="issue" if choice == "bug-flight-paths" else "feature",
                   local_planning="qwen-once" if choice == "bug-flight-paths" else choice)
    with store.begin() as connection:
        connection.execute(text("""INSERT INTO intakes VALUES (:id,:payload,'completed',1,:at,NULL)"""),
                           dict(id=identity, payload=json.dumps(payload), at=value["opened"]))


@pytest.mark.parametrize(("field", "outside"), [("attempt", 0), ("attempt", 9),
                                               ("revision", -1), ("revision", 4)])
def test_campaign_rejects_changed_limits_and_invalid_slots(store, field, outside):
    value = request()
    check(store, value)
    value["campaign"][field] = outside
    with pytest.raises(ValueError):
        check(store, value)
    with pytest.raises(ValueError):
        check(store, request(), limits={**CAMPAIGN, "attempts": 9})


def test_bug_journey_is_first_and_case_attempts_stay_bounded(store):
    value = request()
    with pytest.raises(ValueError, match="real bug journey"):
        check(store, value, choice="qwen-once")
    for i in range(3):
        add_intake(store, value, str(i))
    value["campaign"]["attempt"] = 4
    with pytest.raises(ValueError, match="allocation"):
        check(store, value)


@pytest.mark.parametrize("uncertainty", ["unfinished", "unknown", "usage"])
def test_campaign_cannot_replay_uncertain_or_unaccounted_prior_work(store, uncertainty):
    value = request()
    add_intake(store, value, "earlier")
    value["prior"] = ["attempt"]
    value["campaign"].update(attempt=2, prior_attempts=["attempt"])
    usage = dict(units="tokens", values=dict(completion_tokens=20))
    with store.begin() as connection:
        connection.execute(text("""INSERT INTO model_attempts
            (id,task_id,source,backend,purpose,routing_reason,started_at,finished_at,outcome,usage)
            VALUES ('attempt','earlier','workbench','local','planning','controlled',:at,:finished,:outcome,:usage)"""),
            dict(at=value["opened"],
                 finished=None if uncertainty == "unfinished" else value["opened"],
                 outcome="interrupted_unknown" if uncertainty == "unknown" else "completed",
                 usage=json.dumps(None if uncertainty == "usage" else usage)))
    with pytest.raises(ValueError):
        check(store, value)


def test_campaign_checks_prior_identity_and_accepts_accounted_settled_failure(store):
    value = request()
    add_intake(store, value, "earlier")
    with store.begin() as connection:
        connection.execute(text("""INSERT INTO model_attempts
            (id,task_id,source,backend,purpose,routing_reason,started_at,finished_at,outcome,usage)
            VALUES ('attempt','earlier','workbench','local','planning','controlled',:at,:at,'failed',:usage)"""),
            dict(at=value["opened"], usage=json.dumps(dict(units="tokens",
                                                          values=dict(completion_tokens=20)))))
    value["campaign"]["attempt"] = 2
    with pytest.raises(ValueError):
        check(store, value)
    value["prior"] = ["attempt"]
    value["campaign"]["prior_attempts"] = ["attempt"]
    check(store, value)
    reused = copy.deepcopy(value)
    reused['campaign']['attempt'] = 1
    with pytest.raises(ValueError, match='attempt number reused'):
        check(store, reused)
    altered = copy.deepcopy(value)
    altered["campaign"]["prior_attempts"].append("not-accounted")
    with pytest.raises(ValueError):
        check(store, altered)
