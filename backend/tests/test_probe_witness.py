"""Deterministic NON-MODEL event sequences: no HTTP, server, probe or lease."""
import json
from types import SimpleNamespace

import pytest

from workbench.real_probe import ProbeWitness, cancel_probe, classify_probe
from workbench.real_primary import PrimaryHeld, PrimarySession


class Clock:
    now=0.
    def __call__(self):
        return self.now
    def advance(self, seconds):
        self.now += seconds


def status(w, clock, count=2, processing=True, task=7, epoch=None, started=None):
    w.observe(epoch or ['server',1],0,task,processing,count,clock() if started is None else started)


@pytest.mark.parametrize('order',['very_fast','delayed_status','output_first','stale_inflight_query'])
def test_actual_guard_waits_for_supported_fresh_witness_not_first_event(order):
    c=Clock()
    w=ProbeWitness(['server',1],60,c)
    if order in ('very_fast','stale_inflight_query'):
        status(w,c,count=0)
    c.advance(.01)
    w.output()
    if order=='stale_inflight_query':
        status(w,c,count=1,started=0.)  # Response arrived late but its query predates output.
    assert w.consider(23) is None
    c.advance(.8 if order=='delayed_status' else .01)
    status(w,c,count=2)
    decision=w.consider(23)
    assert decision['eligible'] and decision['witness']['query_started_at']>=decision['first_output_at']
    frozen=json.loads(json.dumps(decision))
    c.advance(.1)
    status(w,c,count=9)
    assert w.consider(23)==frozen  # Later cleanup/debug data never mutates the decision.


@pytest.mark.parametrize('case,reason',[('wrong_task','task_changed'),('wrong_epoch','epoch_changed'),
    ('missing','witness_deadline'),('already_idle','idle_before_cancel'),
    ('normal_finish','completed_before_cancel'),('fenced','fenced')])
def test_guard_is_inconclusive_on_missing_stale_or_terminal_identity(case,reason):
    c=Clock()
    w=ProbeWitness(['server',1],60,c)
    status(w,c,count=0)
    c.advance(.01)
    w.output()
    if case=='wrong_task':
        status(w,c,task=99)
    elif case=='wrong_epoch':
        status(w,c,epoch=['replacement',2])
    elif case=='already_idle':
        status(w,c,processing=False,count=0)
    elif case=='normal_finish':
        w.finished()
    elif case=='missing':
        c.advance(23)
    decision=w.consider(23,current=case!='fenced')
    assert decision['eligible'] is False and decision['reason']==reason
    c.advance(.1)
    status(w,c)
    assert w.consider(40)==decision  # No retroactive cleanup-as-pass.


def test_actual_guard_persists_before_delete_and_reconciles(tmp_path,monkeypatch):
    import workbench.real_primary as primary
    c=Clock()
    monkeypatch.setattr(primary,'time',SimpleNamespace(monotonic=c,sleep=c.advance))
    w=ProbeWitness(['server',1],60,c)
    w.output()
    c.advance(.1)
    status(w,c)
    state=tmp_path/'session'
    with PrimarySession(state,[],'NON-MODEL',lambda:True) as session:
        session.begin(streaming=True)
        class Native:
            def delete(self,nonce):
                disk=json.loads(state.read_text())
                assert disk['probe_decision']['eligible'] and disk['state']=='cancel_requested'
                assert disk['probe_decision']['witness']['observed_decoded']==2
            def observe(self,nonce):
                return dict(nonce=nonce,task_id=7,is_processing=False,producer_done=True,n_decoded=0)
        def factory(selected,end):
            assert selected['task']==7 and end<=5.1
            return Native()
        cancel_probe(w,session,factory,30,lambda:True)
        assert session.record['state']=='released'
        assert session.record['reconciled_at']-session.record['cancel_requested_at']<=5
    assert json.loads(state.read_text())['probe_decision']['witness']['observed_decoded']==2


@pytest.mark.parametrize('failure',['ambiguous','delete_failure','observe_failure','wrong_task','timeout'])
def test_cessation_failure_quarantines_no_replay(tmp_path,monkeypatch,failure):
    import workbench.real_primary as primary
    c=Clock()
    monkeypatch.setattr(primary,'time',SimpleNamespace(monotonic=c,sleep=c.advance))
    w=ProbeWitness(['server',1],60,c)
    w.output()
    c.advance(.1)
    status(w,c)
    state=tmp_path/'session'
    with PrimarySession(state,[],'NON-MODEL',lambda:True) as session:
        session.begin(streaming=True)
        class Native:
            def delete(self,nonce):
                if failure=='delete_failure':
                    raise OSError('scripted cleanup failure')
            def observe(self,nonce):
                if failure=='observe_failure':
                    raise OSError('scripted observation failure')
                return dict(nonce=nonce,task_id=99 if failure=='wrong_task' else 7,
                    is_processing=failure=='timeout',producer_done=failure!='ambiguous',n_decoded=2)
        with pytest.raises((PrimaryHeld,OSError)):
            cancel_probe(w,session,lambda row,end:Native(),30,lambda:True)
        assert session.record['state']=='quarantined'
    with pytest.raises(PrimaryHeld,match='quarantined'):
        with PrimarySession(state,[],'forbidden-replay',lambda:True):
            pass


def test_failed_guard_is_saved_before_cleanup_and_cannot_be_replaced(tmp_path):
    c=Clock()
    w=ProbeWitness(['server',1],60,c)
    w.output()
    c.advance(23)
    state=tmp_path/'session'
    with PrimarySession(state,[],'NON-MODEL',lambda:True) as session:
        session.begin(streaming=True)
        def forbidden(*args):
            pytest.fail('Failed guard must never invoke probe cancellation-as-success')
        with pytest.raises(PrimaryHeld,match='witness_deadline'):
            cancel_probe(w,session,forbidden,30,lambda:True)
        assert not json.loads(state.read_text())['probe_decision']['eligible']
        with pytest.raises(PrimaryHeld,match='replaced'):
            session.capture_probe_decision({'eligible':True})
    assert json.loads(state.read_text())['state']=='quarantined'


def test_completion_race_releases_lane_but_cannot_pass_capability_audit(tmp_path,monkeypatch):
    import workbench.real_primary as primary
    c=Clock()
    monkeypatch.setattr(primary,'time',SimpleNamespace(monotonic=c,sleep=c.advance))
    w=ProbeWitness(['server',1],60,c)
    w.output()
    c.advance(.1)
    status(w,c)
    with PrimarySession(tmp_path/'session',[],'NON-MODEL',lambda:True) as session:
        session.begin(streaming=True)
        class Native:
            def delete(self,nonce):
                pass  # Simulated native normal completion wins the race immediately before DELETE.
            def observe(self,nonce):
                return dict(nonce=nonce,task_id=7,is_processing=False,producer_done=True,n_decoded=0)
        cancel_probe(w,session,lambda row,end:Native(),30,lambda:True)
        assert session.record['state']=='released'
    event=dict(probe_decision={'eligible':True},canceled=True,completed=False,fence_state='released')
    audit=dict(journal_readable=True,owned_stop_logged=True,owned_cancel_queued_logged=True,cancel_precedes_stop=True,
               normal_completion_timing_logged=False,error_or_loss_observed=False)
    assert classify_probe(event,audit,.5)
    assert not classify_probe(event,{**audit,'normal_completion_timing_logged':True},.5)
    assert not classify_probe(event,{**audit,'error_or_loss_observed':True},.5)
    assert not classify_probe(event,{**audit,'journal_readable':False},.5)
    assert not classify_probe(event,{},.5)
    assert not classify_probe({**event,'probe_decision':{'eligible':False}},audit,.5)
    assert not classify_probe(event,audit,30.01)


@pytest.mark.parametrize('cleanup_fails',[False,True])
def test_actual_stream_output_before_status_uses_frozen_guard(tmp_path,monkeypatch,cleanup_fails):
    import io
    import threading
    import time
    import workbench.real_stream as stream
    from workbench.real_stream import NamedStream
    output=threading.Event()
    events=[]
    statuses=[]
    state=tmp_path/'stream-session'
    class Response(io.BytesIO):
        def readline(self,n=-1):
            output.set()
            return super().readline(n)
    class Client:
        def __init__(self,*args):
            self.ends_at=time.monotonic()+120
            self.opener=self
        def __enter__(self):
            return self
        def __exit__(self,*args):
            pass
        def open(self,*args,**kwargs):
            return Response(b'data: {"model":"scripted","choices":[{"delta":{"content":"fixture"}}]}\n\n')
        def read(self,*args,**kwargs):
            assert output.wait(1)
            count=0 if not statuses else 1 if len(statuses)==1 else 9
            statuses.append(count)
            return json.dumps([dict(id=0,id_task=7,is_processing=True,next_token=[{'n_decoded':count}])]).encode()
    class Native:
        def __init__(self,*args,**kwargs):
            pass
        def delete(self,nonce):
            selected=json.loads(state.read_text())['probe_decision']
            assert selected['eligible'] and selected['witness']['observed_decoded']==1
            if cleanup_fails:
                raise OSError('scripted cleanup failure')
        def observe(self,nonce):
            return dict(nonce=nonce,task_id=7,is_processing=False,producer_done=True,n_decoded=0)
    monkeypatch.setattr(stream,'LocalHTTP',Client)
    monkeypatch.setattr(stream,'NativeCancellation',Native)
    client=NamedStream('NO-REAL-CREDENTIAL',state,lambda:['server',1],lambda:True,events.append)
    if cleanup_fails:
        with pytest.raises(OSError):
            client.request({'max_tokens':60},lambda:True,time.monotonic()+30,probe=True)
        assert events[0]['fence_state']=='quarantined'
    else:
        client.request({'max_tokens':60},lambda:True,time.monotonic()+30,probe=True)
        assert events[0]['canceled'] and events[0]['fence_state']=='released'
    decision=events[0]['probe_decision']
    assert decision['witness']['observed_decoded']==1 and decision['eligible']
    assert statuses[:2]==[0,1] and not events[0]['completed']
    assert events[0]['usage'] is None  # Idle0 is never treated as exact usage.


@pytest.mark.parametrize('kind',['cancel','natural','error','suppressed','missing','no_cancel_enqueue','late_cancel_enqueue'])
def test_actual_audit_parser_fails_closed_on_completion_or_loss(monkeypatch,kind):
    import runpy
    import time
    from pathlib import Path
    script=runpy.run_path(str(Path(__file__).resolve().parents[2]/'scripts/chronicle_lease.py'))
    messages=['W srv stop: cancel task, id_task = 7','I slot release: id 0 | task 7 | stop processing: n_tokens = 65']
    if kind=='no_cancel_enqueue':
        messages=messages[1:]
    if kind=='late_cancel_enqueue':
        messages.reverse()
    if kind=='natural':
        messages.insert(0,'I slot timings: id 0 | task 7 | eval time = 20 ms')
    if kind=='error':
        messages.insert(0,'E srv: Compute error.')
    if kind=='suppressed':
        messages.insert(0,'Suppressed 4 messages from source')
    if kind=='missing':
        messages=[]
    raw=b'\n'.join(json.dumps({'_PID':'123','MESSAGE':m}).encode() for m in messages)
    monkeypatch.setattr(script['subprocess'],'run',lambda *a,**k:SimpleNamespace(stdout=raw,stderr=b'',returncode=0))
    audit=script['journal_proof'](123,7,1,time.monotonic()+2)
    event=dict(probe_decision={'eligible':True},canceled=True,completed=False,fence_state='released')
    assert classify_probe(event,audit,.5)==(kind=='cancel')


def test_budget_epoch_fence_does_not_invoke_systemctl_or_adopt_replacement(tmp_path,monkeypatch):
    import os
    import workbench.real_lease as lease
    owner=lease.Lease(tmp_path)
    owner.server_epoch=[17,'123']
    birth=['123']
    class File:
        def __init__(self,name):
            self.name=name
        def read_text(self):
            if self.name=='cgroup':
                return '0::/system.slice/twichui-llm.service'
            return '17 (llama-server) '+' '.join(['S']+['0']*18+[birth[0]])
    class Proc:
        def __truediv__(self,name):
            return File(name)
        def stat(self):
            return SimpleNamespace(st_uid=os.getuid())
    class Root:
        def __truediv__(self,pid):
            assert pid=='17'
            return Proc()
    monkeypatch.setattr(lease,'Path',lambda p:Root())
    monkeypatch.setattr(lease,'system',lambda *a:pytest.fail('No subprocess within cancellation deadline'))
    assert owner.current_epoch()==[17,'123']
    birth[0]='456'
    assert owner.current_epoch() is None and owner.server_epoch==[17,'123']


def test_late_idle_reply_cannot_release_lane(tmp_path,monkeypatch):
    import workbench.real_primary as primary
    c=Clock()
    monkeypatch.setattr(primary,'time',SimpleNamespace(monotonic=c,sleep=c.advance))
    path=tmp_path/'session'
    with PrimarySession(path,[],'NON-MODEL',lambda:True) as session:
        session.begin(streaming=True)
        session.bind_observed_task(7)
        def late(nonce):
            c.advance(6)
            return dict(nonce=nonce,task_id=7,is_processing=False,producer_done=True,n_decoded=0)
        with pytest.raises(PrimaryHeld,match='deadline'):
            session.cancel_and_reconcile(lambda n:None,late)
        assert session.record['state']=='quarantined'


@pytest.mark.parametrize('case',['ready','missing_info','loss','unreadable'])
def test_audit_readiness_is_read_only_and_requires_visible_info(monkeypatch,case):
    import runpy
    from pathlib import Path
    script=runpy.run_path(str(Path(__file__).resolve().parents[2]/'scripts/chronicle_lease.py'))
    messages=[] if case=='missing_info' else ['1.00 I srv listening']
    if case=='loss':
        messages.append('Suppressed 3 messages')
    raw=b'\n'.join(json.dumps({'_PID':'123','MESSAGE':m}).encode() for m in messages)
    def run(argv,**kwargs):
        assert argv[0]=='journalctl'  # No generation/lifecycle operation in this gate.
        return SimpleNamespace(stdout=raw,stderr=b'' if case!='unreadable' else b'denied',returncode=0)
    monkeypatch.setattr(script['subprocess'],'run',run)
    assert script['audit_ready'](123)['passed']==(case=='ready')
