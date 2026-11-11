"""The schema extension keeps every existing privileged settlement refusal."""
from pathlib import Path
import sqlite3
import os
import pytest
from ops import workbench_admin as admin


def test_schema13_is_organizational_and_unknown_or_unsettled_stores_remain_refused(store, tmp_path, monkeypatch):
    database=Path(store.url.database)
    stage=tmp_path/'stage'
    (stage/'artifacts').mkdir(parents=True,mode=0o700)
    dest=stage/'artifacts/workbench.sqlite3'
    with sqlite3.connect(database) as source, sqlite3.connect(dest) as target:
        source.backup(target)
    dest.chmod(0o600)
    monkeypatch.setattr(admin,'rich_stage',lambda _:stage)
    # The installed helper targets Rich; this disposable fixture belongs to the
    # actual test runner (GitHub uses a different UID). Keep ownership checks real.
    monkeypatch.setattr(admin,'UID',os.getuid()+1)
    with pytest.raises(admin.Refused,match='path or mode'):
        admin.settled_check('everyday-test',False)
    monkeypatch.setattr(admin,'UID',os.getuid())
    admin.settled_check('everyday-test',False)
    with sqlite3.connect(dest) as c:
        c.execute("UPDATE alembic_version SET version_num='unknown'")
    with pytest.raises(admin.Refused,match='outside this grant'):
        admin.settled_check('everyday-test',False)
    with sqlite3.connect(dest) as c:
        c.execute("UPDATE alembic_version SET version_num='0013'")
        c.execute("UPDATE commands SET status='pending'")
        # Seed has no commands; add a durable planning operation instead.
        c.execute("INSERT INTO intakes VALUES ('held','{}','queued',0,'2026-11-11',NULL)")
        c.execute("INSERT INTO planning_jobs VALUES ('pending','held','planning',1,'queued',0,NULL,NULL,'{}','Queued',NULL,'2026-11-11',NULL)")
    with pytest.raises(admin.Refused,match='unsettled'):
        admin.settled_check('everyday-test',False)
    with sqlite3.connect(dest) as c:
        c.execute("UPDATE planning_jobs SET status='failed'")
    admin.settled_check('everyday-test',False)
    with sqlite3.connect(dest) as c:
        c.execute("UPDATE alembic_version SET version_num='0012'")
    admin.settled_check('everyday-test',False)
