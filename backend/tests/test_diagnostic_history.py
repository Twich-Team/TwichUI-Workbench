"""Historical producer diagnostics cannot break browsing or change saved authority."""
import copy
import math
from uuid import uuid4

import pytest
from sqlalchemy import select

from workbench.db import intakes,planning_jobs
from workbench.intake import diagnostics_view


@pytest.mark.parametrize('raw', [None,[],dict(nested=dict(PRIVATE_SECRET='PRIVATE_SOURCE')),
                                dict(value=math.nan),dict(context_budget=dict(prompt_tokens=True,context_tokens=32768))])
def test_unknown_diagnostics_are_explicitly_unavailable_without_exposing_private_structure(raw):
    original=copy.deepcopy(raw)
    assert diagnostics_view(raw)==dict(diagnostics_unavailable=1)
    if raw is not None and not (isinstance(raw,dict) and 'value' in raw):
        assert raw==original


@pytest.mark.parametrize('legacy',[True,False])
def test_history_detail_and_listing_flatten_known_counts_without_migrating_or_authorizing(
    client,headers,store,legacy,
):
    item=client.post('/api/intake',headers=headers,json=dict(request_id=str(uuid4()),kind='issue',title='History remains readable',
        description='A saved request must remain visible after a local diagnostic failure.',
        lua_error='',visual_requirement='nonvisual')).json()
    counts=dict(context_budget=dict(prompt_tokens=7560,context_tokens=32768)) if legacy else dict(
        context_prompt_tokens=7560,context_capacity_tokens=32768)
    diagnostics=dict(category='inspection_limit',generation_state='finished',retained_failure=1,
                     unknown=dict(PRIVATE_SECRET='PRIVATE_SOURCE'),**counts)
    with store.begin() as c:
        [job]=c.execute(select(planning_jobs).where(planning_jobs.c.intake_id==item['id'])).mappings()
        payload={**job['payload'],'diagnostics':diagnostics}
        c.execute(planning_jobs.update().where(planning_jobs.c.id==job['id']).values(status='failed',payload=payload))
        c.execute(intakes.update().where(intakes.c.id==item['id']).values(status='failed'))
    detail=client.get('/api/intake/'+item['id'])
    listing=client.get('/api/intake')
    assert detail.status_code==listing.status_code==200
    found=next(row for row in listing.json()['items'] if row['id']==item['id'])
    for view in [detail.json(),found]:
        assert view['status']=='failed' and not view['tasks'] and not view['decisions']
        value=view['jobs'][0]['diagnostics']
        assert value==dict(category='inspection_limit',generation_state='finished',retained_failure=1,
            diagnostics_unavailable=1,context_prompt_tokens=7560,context_capacity_tokens=32768)
    assert 'PRIVATE_SOURCE' not in detail.text+listing.text
    with store.connect() as c:
        saved=c.execute(select(planning_jobs.c.payload).where(planning_jobs.c.id==job['id'])).scalar_one()
    assert saved==payload  # No migration, evidence deletion or authority mutation.
