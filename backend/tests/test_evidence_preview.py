"""Long committed source must not break history or change review authority."""
import copy
from uuid import uuid4
from sqlalchemy import select
from workbench.evidence_preview import excerpt, references
from workbench.schemas import EvidenceReference
from workbench.db import planning_jobs


def test_preview_is_explicit_bounded_and_preserves_reference_identity():
    text='local function original()\n'+'-- committed source\n'*400
    item=dict(kind='addon',source_revision='a'*40,id='committed-source',label='source.lua',
              sha256='b'*64,excerpt=text)
    original=copy.deepcopy(item)
    [shown]=references([item])
    assert len(shown['excerpt'])<=2400 and 'Preview shortened' in shown['excerpt']
    EvidenceReference.model_validate(shown)
    assert {k:v for k,v in shown.items() if k!='excerpt'}=={k:v for k,v in item.items() if k!='excerpt'}
    assert item==original and excerpt('complete')=='complete'


def test_oversized_historical_preview_is_readable_without_mutating_its_payload(client,headers,store):
    identity=client.post('/api/intake',headers=headers,json=dict(request_id=str(uuid4()), title='Inspect a saved report',
         description='Inspect this saved report.',kind='issue',visual_requirement='nonvisual')).json()['id']
    item=dict(kind='addon',id='historical',label='committed source',sha256='a'*64,
              source_revision='b'*40,excerpt='retained original source\n'*300)
    with store.begin() as c:
        row=c.execute(select(planning_jobs).where(planning_jobs.c.intake_id==identity)).mappings().one()
        payload=copy.deepcopy(row['payload'])
        payload['evidence']=[item]
        c.execute(planning_jobs.update().where(planning_jobs.c.id==row['id']).values(payload=payload))
    for url in ['/api/intake','/api/intake/'+identity]:
        response=client.get(url)
        assert response.status_code==200,response.text
        view=response.json()
        view=view['items'][0] if 'items' in view else view
        assert len(view['evidence'][0]['excerpt'])<=2400
        assert 'Preview shortened' in view['evidence'][0]['excerpt']
    with store.connect() as c:
        after=c.execute(select(planning_jobs.c.payload).where(planning_jobs.c.id==row['id'])).scalar_one()
    assert after==payload and len(after['evidence'][0]['excerpt'])>2400
