"""All-source accounting, missing data, UTC boundaries and actual recorded switches."""
from datetime import datetime,timezone
from uuid import uuid4
from sqlalchemy import select
from workbench.db import model_attempts,planning_jobs,write_transaction
from workbench import usage_view
from test_intake import submit

NOW=datetime(2026,11,11,12,tzinfo=timezone.utc)


def record(store,*,task='request',backend='local',model='reported-qwen',source='workbench',at='2026-11-10T20:00:00+00:00',tokens=20,status='complete',outcome='completed'):
    usage=None if tokens is None else dict(units='tokens',status=status,values=dict(total_tokens=tokens),requests_observed=0 if status=='not_started' else 1)
    with write_transaction(store) as c:
        c.execute(model_attempts.insert().values(id=str(uuid4()),task_id=task,purpose='planning',source=source,
            backend=backend,actual_model=model,requested_model='requested-other',routing_reason='Controlled fixture',
            started_at=at,outcome=outcome,usage=usage))


def test_totals_use_all_records_not_capped_detail_rows_and_preserve_unknowns(store):
    for _ in range(105):
        record(store)
    record(store,backend='codex',model=None,tokens=9,status='partial',outcome='failed')
    record(store,backend='codex',model=None,tokens=None,outcome='interrupted_unknown')
    record(store,tokens=0,status='not_started',outcome='unavailable')
    record(store,tokens=9999,backend='current-session')
    record(store,tokens=777,source='demo')
    record(store,tokens=888,source='unknown')
    record(store,tokens=999,at='2026-10-01T00:00:00Z')
    record(store,tokens=1000,at='invalid')
    record(store,tokens=1001,at='2026-11-12T00:00:00Z')
    value=usage_view.read(store,7,instant=NOW)
    assert value.operations==108 and value.tasks==1 and value.session_authored_operations==1
    assert value.reported_tokens==2109 and value.unreported_operations==1
    assert value.reported_operations==107 and value.partial_operations==1 and value.undated_operations==2
    assert sum(r.reported_tokens or 0 for r in value.timeline)==value.reported_tokens
    assert sum(r.operations for r in value.providers)==value.operations
    codex=next(r for r in value.providers if r.backend=='codex')
    assert codex.model=='Not reported' and codex.reported_tokens==9
    assert codex.other==2 and codex.partial_operations==1
    assert value.subscription_allowance==value.api_spending=='unavailable'
    assert 'requested-other' not in value.model_dump_json()


def test_empty_unknown_zero_and_utc_dates_are_distinct(store):
    empty=usage_view.read(store,7,instant=NOW)
    assert empty.operations==0 and empty.reported_tokens is None and empty.timeline==[]
    record(store,tokens=None,at='2026-11-05T02:00:00+02:00')
    record(store,tokens=1,at='2026-11-05T01:59:59+02:00')  # Previous UTC date, outside this window.
    value=usage_view.read(store,7,instant=NOW)
    assert value.operations==1 and value.reported_tokens is None and value.unreported_operations==1
    assert value.timeline[0].day=='2026-11-05'
    record(store,tokens=0,status='not_started')
    value=usage_view.read(store,7,instant=NOW)
    assert value.reported_tokens==0 and value.reported_operations==1
    assert len(value.timeline)==2


def test_switches_require_exact_persistent_pre_generation_receipt_and_do_not_write(client,headers,store):
    item,_=submit(client,headers)
    with write_transaction(store) as c:
        job=c.execute(select(planning_jobs)).mappings().one()
        payload={**job['payload'],'provider_switch':dict(from_provider='local',to_provider='codex',reason='local_unreachable',local_generation_started=False)}
        c.execute(planning_jobs.update().values(payload=payload,created_at='2026-11-10T00:00:00Z'))
    value=usage_view.read(store,7,instant=NOW)
    assert len(value.fallbacks)==1 and value.fallbacks[0].intake_id==item['id']
    assert value.operations==0  # A fallback record cannot manufacture provider activity.
    with write_transaction(store) as c:
        payload['provider_switch']['local_generation_started']=True
        c.execute(planning_jobs.update().values(payload=payload))
    assert not usage_view.read(store,7,instant=NOW).fallbacks


def test_unknown_or_malformed_accounting_is_never_a_token_claim():
    for usage in [None,{},dict(units='tokens',status='unknown',values=dict(total_tokens=99)),
                  dict(units='tokens',status='complete',values=dict(total_tokens=True)),
                  dict(units='tokens',status='complete',values=dict(total_tokens=-1)),
                  dict(units='tokens',status='not_started',requests_observed=1,values=dict(total_tokens=0))]:
        assert usage_view.token_value(usage)==(None,False)


def test_api_range_validation_and_empty_state_are_read_only(client,store):
    with store.connect() as c:
        before=[dict(r) for r in c.execute(select(model_attempts)).mappings()]
    value=client.get('/api/usage?days=30')
    assert value.status_code==200 and value.json()['reported_tokens'] is None
    assert value.json()['operations']==0 and value.json()['days']==30
    assert client.get('/api/usage?days=1').status_code==422
    with store.connect() as c:
        assert [dict(r) for r in c.execute(select(model_attempts)).mappings()]==before
