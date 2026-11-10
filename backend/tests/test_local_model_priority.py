"""Real cooperative locks, controlled owners, no model calls or real maintenance control."""
import json
import os
import signal
import subprocess
import sys
import time

import pytest

from workbench import local_model_priority as priority
from workbench import planning_provider
from test_everyday import installed  # noqa: F401


class LocalHost(priority.Host):
    def __init__(self, tmp_path):
        super().__init__(tmp_path/'pilot',tmp_path/'status')
        self.pilot.mkdir()
        (self.pilot/'queue').mkdir()
        for p in self.paths:
            p.touch(mode=0o600)
        self.expected = None
        self.idle_result = True
        self.interruptions = 0

    def maintenance(self, owner):
        return owner if owner == self.expected else None

    def interrupt(self, owner, *, force=False):
        assert owner == self.expected and self.identity(owner['pid']) == owner
        self.interruptions += 1
        os.killpg(owner['group'],signal.SIGKILL if force else signal.SIGTERM)  # This test's own child only.
        return True

    def idle(self):
        return self.idle_result


def holder(host, *, one=False):
    paths=host.paths[-1:] if one else host.paths
    code='''import fcntl,sys,time
files=[open(p,'rb') for p in sys.argv[1:]]
for f in files:fcntl.flock(f,fcntl.LOCK_EX)
print('ready',flush=True)
time.sleep(30)
'''
    proc=subprocess.Popen([sys.executable,'-c',code,*map(str,paths)],stdout=subprocess.PIPE,
        text=True,start_new_session=True)
    assert proc.stdout.readline().strip()=='ready'
    return proc


def contender(host):
    code='''import fcntl,sys
files=[open(p,'rb') for p in sys.argv[1:]]
try:
 for f in files:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
 print('acquired')
except BlockingIOError:print('blocked')
'''
    return subprocess.check_output([sys.executable,'-c',code,*map(str,host.paths)],text=True).strip()


def ready(reservation, ids):
    end=time.monotonic()+4
    while not reservation.step(ids):
        assert time.monotonic()<end
        time.sleep(.02)


def test_real_priority_handoff_excludes_reacquisition_between_model_requests(tmp_path,monkeypatch):
    host=LocalHost(tmp_path)
    child=holder(host)
    manager=priority.Reservation(host)
    try:
        host.expected=host.identity(child.pid)
        assert not manager.step(['saved-revision'])
        child.wait(timeout=3)
        ready(manager,['saved-revision'])
        assert host.interruptions==1 and contender(host)=='blocked'
        monkeypatch.setattr(priority,'_manager',manager)
        monkeypatch.setattr(planning_provider,'LOCAL_LOCKS',tuple(map(str,host.paths)))
        for _ in range(3):
            with planning_provider.local_slot():
                assert contender(host)=='blocked'
            assert contender(host)=='blocked'  # The whole operation still owns both locks.
        manager.close()
        assert contender(host)=='acquired'
        assert all(p.exists() for p in host.paths)  # No lock-file deletion/replacement.
    finally:
        manager.close()
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=3)


def test_unknown_owner_is_never_cancelled_or_mislabelled_as_maintenance(tmp_path):
    host=LocalHost(tmp_path)
    child=holder(host,one=True)
    manager=priority.Reservation(host)
    try:
        assert not manager.step(['saved-revision'])
        assert manager.state=='waiting' and host.interruptions==0 and child.poll() is None
        assert priority.Host.maintenance(host,host.identity(child.pid)) is None
    finally:
        manager.close()
        child.terminate()
        child.wait(timeout=3)


def test_stale_journal_does_not_establish_idle_or_provider_completion(tmp_path):
    host=LocalHost(tmp_path)
    host.save(dict(owner=dict(pid=999999999,start='old',boot='old'),state='working',
                   message='Old generation',intake_ids=['old-task']))
    host.idle_result=False
    manager=priority.Reservation(host)
    try:
        assert not manager.step(['saved-revision'])
        assert manager.state=='reconciling' and not manager.ready
        proof=json.loads(next(host.directory.glob('reconciled-reservation-*.json')).read_text())
        assert proof['provider_completion_inferred'] is False
        assert proof['previous']['state']=='working'
        host.idle_result=True
        assert manager.step(['saved-revision']) and manager.ready
    finally:
        manager.close()


def test_live_reservation_owner_journal_is_not_overwritten(tmp_path):
    host=LocalHost(tmp_path)
    first,second=priority.Reservation(host),priority.Reservation(host)
    try:
        assert first.step(['first'])
        before=(host.directory/'status.json').read_bytes()
        assert not second.step(['second']) and not second.ready
        assert (host.directory/'status.json').read_bytes()==before
        first.close()
        assert second.step(['second'])
    finally:
        first.close()
        second.close()


def test_execution_controller_reservation_survives_inner_requests_and_releases_after_restoration(tmp_path,monkeypatch):
    host=LocalHost(tmp_path)
    monkeypatch.setattr(priority,'Host',lambda:host)
    monkeypatch.setattr(priority,'configured',lambda:True)
    monkeypatch.setattr(priority,'_manager',None)
    with pytest.raises(RuntimeError,match='controlled failure after restoration'):
        with priority.execution_reservation():
            assert all(priority.borrowing(p) for p in host.paths)
            assert contender(host)=='blocked'
            # The existing Lease stack exits this outer context only after its
            # authenticated restoration path, including failure handling.
            assert priority._manager.ready
            raise RuntimeError('controlled failure after restoration')
    assert priority._manager is None and contender(host)=='acquired'
    assert json.loads((host.directory/'status.json').read_text())['state']=='released'


def test_symlinked_or_replaced_locks_cannot_be_borrowed(tmp_path):
    host=LocalHost(tmp_path)
    manager=priority.Reservation(host)
    try:
        assert manager.step(['task'])
        p=host.paths[0]
        p.rename(p.with_suffix('.retained'))
        p.touch(mode=0o600)
        assert not manager.borrows(p)
        with pytest.raises(ValueError,match='ownership changed'):
            manager.step(['task'])
        p.unlink()
        p.symlink_to(p.with_suffix('.retained'))
        with pytest.raises(ValueError,match='path changed'):
            manager.borrows(p)
    finally:
        manager.close()


def fake_process(proc,pid,*,parent=1,group=None,start='42',cgroup='0::/system.slice/twichui-worker.service/main',argv=None):
    root=proc/str(pid)
    root.mkdir(parents=True)
    fields=['S',str(parent),str(group or pid),str(group or pid),*['0']*15,start,*['0']*4]
    (root/'stat').write_text(str(pid)+' (controlled process) '+' '.join(fields))
    (root/'cgroup').write_text(cgroup)
    (root/'cmdline').write_bytes(b'\0'.join(os.fsencode(a) for a in (argv or ['python','fixture'])))


@pytest.mark.parametrize('fault',[None,'stale_pid','different_program','different_cgroup','different_group','foreign_group_member'])
def test_only_verified_live_maintenance_group_can_be_interrupted(tmp_path,monkeypatch,fault):
    proc=tmp_path/'proc'
    (proc/'sys/kernel/random').mkdir(parents=True)
    (proc/'sys/kernel/random/boot_id').write_text('controlled-boot')
    pilot=tmp_path/'pilot'
    (pilot/'queue').mkdir(parents=True)
    (pilot/'queue/cycle-state.json').write_text(json.dumps(dict(running=dict(pid=44 if fault=='stale_pid' else 42))))
    fake_process(proc,42,group=84 if fault=='different_group' else 42,
        cgroup='0::/unrelated.service' if fault=='different_cgroup' else '0::/system.slice/twichui-worker.service/main',
        argv=['/usr/bin/python3','-I',str(pilot/('unrelated.py' if fault=='different_program' else 'cycle.py'))])
    fake_process(proc,43,parent=42,group=42)
    if fault=='foreign_group_member':
        fake_process(proc,44,group=42,cgroup='0::/unrelated.service')
    host=priority.Host(pilot,tmp_path/'status',proc)
    signals=[]
    monkeypatch.setattr(os,'killpg',lambda pid,sig:signals.append((pid,sig)))
    assert host.interrupt(host.identity(42)) == (fault is None)
    assert signals==([(42,signal.SIGTERM)] if fault is None else [])
    if fault is None:
        proof=json.loads(next(host.directory.glob('yield-*.json')).read_text())
        assert proof['maintenance_final_usage']=='unknown' and proof['maintenance_final_outcome']=='unknown'
        assert not proof['workbench_execution_authorized']


def test_snapshot_does_not_call_old_reservation_or_stale_cycle_marker_active(tmp_path,monkeypatch,store):
    host=LocalHost(tmp_path)
    host.save(dict(owner=dict(pid=999999999,start='old',boot='old'),state='working',message='old',intake_ids=['old']))
    (host.pilot/'queue/cycle-state.json').write_text(json.dumps(dict(running=dict(pid=999999999))))
    monkeypatch.setattr(priority,'Host',lambda:host)
    monkeypatch.setattr(planning_provider,'configured_mode',lambda:'everyday')
    view=priority.snapshot(store)
    assert view['state']=='idle' and not view['maintenance_active'] and not view['reservation_owner_alive']
    assert view['observed_at'] is None


def test_live_model_activity_requires_actual_workbench_lock_owner(tmp_path,monkeypatch,store):
    from sqlalchemy import select
    from workbench.db import intakes,planning_jobs,model_attempts,write_transaction
    from uuid import uuid4
    host=LocalHost(tmp_path)
    monkeypatch.setattr(priority,'Host',lambda:host)
    monkeypatch.setattr(planning_provider,'configured_mode',lambda:'everyday')
    identity,job,attempt=map(lambda _:str(uuid4()),range(3))
    with write_transaction(store) as c:
        c.execute(intakes.insert().values(id=identity,payload=dict(kind='issue',local_planning='qwen-once'),status='investigating',revision=0,created_at=priority.now()))
        c.execute(planning_jobs.insert().values(id=job,intake_id=identity,kind='planning',revision=1,status='running',attempts=1,payload={},message='Old claim',created_at=priority.now()))
        c.execute(model_attempts.insert().values(id=attempt,job_id=job,task_id=identity,purpose='planning',source='workbench',backend='local',outcome='running',started_at=priority.now(),routing_reason='Controlled old claim'))
    view=priority.snapshot(store)
    assert view['state']=='reconciling' and not view['maintenance_active']
    assert identity in view['intake_ids'] and not view['reservation_owner_alive']
    with store.connect() as c:
        assert c.execute(select(model_attempts.c.outcome).where(model_attempts.c.id==attempt)).scalar_one()=='running'
        assert c.execute(select(model_attempts.c.finished_at).where(model_attempts.c.id==attempt)).scalar_one() is None


@pytest.mark.usefixtures('installed')
def test_saved_revision_resumes_exactly_once_without_resubmission_after_handoff_and_restart(
    tmp_path,client,headers,store,monkeypatch,
):
    from uuid import uuid4
    from sqlalchemy import select
    from workbench import worker
    from workbench.db import model_attempts,planning_jobs
    from test_scope_binding import unapproved

    item,calls=unapproved(client,headers,store,monkeypatch)
    url='/api/intake/'+item['id']
    p=item['proposals'][0]
    note='Also investigate delayed data and whether a refresh is needed.'
    command=dict(command_id=str(uuid4()),proposal_id=p['id'],binding=p['binding'],
        scope_revision=p['scope_revision'],action='changes',note=note)
    response=client.post(url+'/decisions',headers=headers,json=command)
    assert response.status_code==200,response.text
    assert worker.tick(store)  # Apply the owner decision exactly once.
    waiting=client.get(url).json()
    original_job=waiting['jobs'][-1]
    assert original_job['status']=='queued' and original_job['attempts']==0 and len(calls)==1
    host=LocalHost(tmp_path)
    manager=priority.Reservation(host)
    child=holder(host)
    host.expected=host.identity(child.pid)
    key=tmp_path/'inert-test-key'
    key.write_text('controlled-never-sent')
    monkeypatch.setenv('WORKBENCH_LOCAL_KEY',str(key))
    monkeypatch.setattr(priority,'Host',lambda:host)
    monkeypatch.setattr(priority,'configured',lambda:True)
    monkeypatch.setattr(priority,'_manager',manager)
    monkeypatch.setattr(planning_provider,'LOCAL_LOCKS',tuple(map(str,host.paths)))
    try:
        priority.reconcile(store)
        assert not worker.tick(store)
        assert len(calls)==1
        blocked=client.get(url).json()
        assert blocked['jobs'][-1]['id']==original_job['id'] and blocked['jobs'][-1]['attempts']==0
        assert blocked['local_model']['state']=='yielding'
        child.wait(timeout=3)
        priority.reconcile(store)
        assert worker.tick(store)
        assert len(calls)==2
        priority.reconcile(store)
        assert contender(host)=='acquired'
        assert not worker.tick(store)
        store.dispose()
        saved=client.get(url).json()
        assert saved['status']=='completed' and saved['revision']==2
        assert saved['jobs'][-1]['id']==original_job['id'] and saved['jobs'][-1]['attempts']==1
        assert saved['clarifications'][-1]['text']==note
        assert saved['original']==item['original'] and saved['human_scope']==item['human_scope']
        assert not saved['tasks']
        assert client.post(url+'/decisions',headers=headers,json=command).json()['status']=='applied'
        assert not worker.tick(store) and len(calls)==2
        with store.connect() as c:
            assert len(c.execute(select(model_attempts).where(model_attempts.c.task_id==item['id'])).all())==2
            assert len(c.execute(select(planning_jobs).where(planning_jobs.c.intake_id==item['id'])).all())==2
    finally:
        manager.close()
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=3)
