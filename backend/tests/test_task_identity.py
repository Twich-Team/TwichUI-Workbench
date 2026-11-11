"""Historical grouping preserves original authority and survives restart/restore."""
import copy
import sqlite3
from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import select
from workbench.db import intakes, planning_jobs, task_lineage, write_transaction, engine_for
from workbench.intake import digest
from workbench import task_identity, workspace_view
from test_intake import submit


def group(client, headers, store):
    ids = [submit(client, headers, title='Same title, distinct original ' + str(n))[0]['id'] for n in range(3)]
    with write_transaction(store) as c:
        c.execute(planning_jobs.update().values(status='failed'))
        c.execute(intakes.update().values(status='failed'))
        originals = [dict(r) for r in c.execute(select(intakes)).mappings()]
    manifest = dict(canonical_id=ids[0], title='One evidenced request', members=[dict(
        intake_id=i,payload_sha256=digest(next(r['payload'] for r in originals if r['id']==i)),
        relation='retry',evidence=[dict(source='campaign/dispatch.jsonl',sha256='a'*64)]) for i in ids[:2]])
    return ids, originals, manifest


def test_idempotency_restart_restore_reversal_and_original_deep_links(client, headers, store, tmp_path):
    ids, originals, manifest = group(client,headers,store)
    assert task_identity.reconcile(store, manifest)==2
    assert task_identity.reconcile(store, manifest)==2
    with store.connect() as c:
        assert [dict(r) for r in c.execute(select(intakes)).mappings()] == originals
        links = [dict(r) for r in c.execute(select(task_lineage)).mappings()]
        assert len(links)==2
        with sqlite3.connect(tmp_path/'restored.sqlite3') as backup:
            c.connection.driver_connection.backup(backup)
    assert client.get('/api/intake').json()['total']==2
    for i in ids[:2]:
        view=client.get('/api/intake/'+i).json()
        assert view['id']==i and view['task_history']['canonical_id']==ids[0]
        assert len(view['task_history']['members'])==2
    restored=engine_for(tmp_path/'restored.sqlite3')
    store.dispose()
    index=workspace_view.listing(restored)
    assert index['all_total']==2
    grouped=next(r for r in index['items'] if r['id']==ids[0])
    assert grouped['history_count']==grouped['history_failures']==2
    assert grouped['current_intake_id']==ids[1]
    assert workspace_view.listing(restored,search='distinct original 1')['total']==1
    assert workspace_view.listing(restored,search='distinct original 2')['total']==1
    assert task_identity.reverse(restored, manifest)==links
    assert task_identity.reverse(restored, manifest)==[]
    assert workspace_view.listing(restored)['all_total']==3
    with restored.connect() as c:
        assert [dict(r) for r in c.execute(select(intakes)).mappings()] == originals
    restored.dispose()


def test_conflicting_or_changed_member_rolls_back_entire_reconciliation(client, headers, store):
    ids, _, manifest=group(client,headers,store)
    wrong=copy.deepcopy(manifest)
    wrong['members'][-1]['payload_sha256']='b'*64
    with pytest.raises(ValueError,match='binding differs'):
        task_identity.reconcile(store,wrong)
    with store.connect() as c:
        assert not c.execute(select(task_lineage)).first()
    task_identity.reconcile(store,manifest)
    wrong=copy.deepcopy(manifest)
    wrong['title']='Guess from a title'
    with pytest.raises(ValueError,match='differs'):
        task_identity.reconcile(store,wrong)
    with pytest.raises(ValueError,match='entire original manifest'):
        task_identity.reverse(store,wrong)


def test_active_operation_cannot_be_reconciled_and_nonempty_downgrade_refuses(client,headers,store):
    ids,_,manifest=group(client,headers,store)
    with write_transaction(store) as c:
        c.execute(planning_jobs.update().where(planning_jobs.c.intake_id==ids[1]).values(status='running'))
    with pytest.raises(ValueError,match='Active planning'):
        task_identity.reconcile(store,manifest)
    with write_transaction(store) as c:
        c.execute(planning_jobs.update().values(status='failed'))
    task_identity.reconcile(store,manifest)
    cfg=Config('alembic.ini')
    with store.connect() as c:
        cfg.attributes['connection']=c
        with pytest.raises(RuntimeError,match='Export'):
            command.downgrade(cfg,'0012')
