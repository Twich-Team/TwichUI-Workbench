"""Bug Report entry point: closed responses, unknown accounting and source formats."""
import copy
import json
import os
from pathlib import Path
import struct
from uuid import uuid4
import zlib

import pytest
from sqlalchemy import select

from workbench import worker, delivery_phase
from workbench.db import model_attempts, planning_jobs, intakes
from workbench.planning_provider import LocalProvider
from test_bug_workflow import enable_bugs, report
from test_everyday import installed, write_policy  # noqa: F401
from test_verification_profiles import git

TEST = 'tests/test_reminder_gallery.py'
TOOL = 'tools/make_reminder_gallery.py'
PNG = 'assets/reminder-gallery/reminder-lion-shield.png'


def png():
    def chunk(kind, data):
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    return (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,6,0,0,0))
            +chunk(b'IDAT',zlib.compress(b'\0\xff\0\0\xff'))+chunk(b'IEND',b''))


def setup(monkeypatch, tmp_path):
    root = enable_bugs(monkeypatch)
    for path, raw in [(TEST,b'assert expected_png == regenerated_png\n'),
                      (TOOL,b'import zlib\n# bounded generator fixture\n'), (PNG,png()),
                      ('tools/too-large.py',b'#'*131073)]:
        target = root/path
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(raw)
    (root/'tools/link.py').symlink_to('make_reminder_gallery.py')
    git(root,'add','.')
    git(root,'-c','user.name=Fixture','-c','user.email=fixture@invalid','commit','-qm','Committed Python and artwork')
    policy = Path(os.environ['WORKBENCH_PREPARATION_CONFIG'])
    write_policy(policy,{**json.loads(policy.read_text()),'bug_plan_review':'source-grounded-intent-v1'})
    release = tmp_path/'release'
    release.mkdir(mode=0o700)
    from workbench import planning_response
    monkeypatch.setattr(planning_response,'create_evidence',lambda attempt,schema,coverage=None,files=None:
                        planning_response.Evidence(release,attempt,schema,coverage,files))
    return root


def terminal(monkeypatch, *, usage=True, finish='length'):
    def generate(self, packet, schema, expected_model):
        self.diagnostics = dict(generation_state='not_started')
        class Transport:
            seconds = 120
            def models(self, *_):
                return expected_model
            def context_budget(self, *_):
                return dict(prompt_tokens=8000,context_tokens=32768)
            def read(self, request, limit):
                result = dict(id='chatcmpl-closed-test',model=expected_model,
                    choices=[dict(finish_reason=finish,message=dict(content='unfinished proposal'))])
                if usage:
                    result['usage'] = dict(prompt_tokens=40,completion_tokens=10,total_tokens=50)
                return json.dumps(result).encode()
        return self._infer(Transport(),'inert',schema,json.dumps(packet),expected_model=expected_model)
    monkeypatch.setattr(LocalProvider,'generate',generate)


@pytest.mark.usefixtures('installed')
@pytest.mark.parametrize('case',['length','unknown_usage','receipt_interrupted','active','disconnected'])
def test_saved_bug_continues_only_after_terminal_proof_and_never_duplicates(
    client, headers, store, monkeypatch, tmp_path, case,
):
    setup(monkeypatch,tmp_path)
    terminal(monkeypatch,usage=case!='unknown_usage',finish='stop' if case=='receipt_interrupted' else 'length')
    request = report()
    saved = client.post('/api/intake',headers=headers,json=request).json()
    assert worker.tick(store)
    saved = client.get('/api/intake/'+saved['id']).json()
    assert saved['status']=='failed'
    with store.begin() as c:
        attempt = dict(c.execute(select(model_attempts).where(model_attempts.c.task_id==saved['id'])).mappings().one())
        job = dict(c.execute(select(planning_jobs).where(planning_jobs.c.id==attempt['job_id'])).mappings().one())
        if case=='receipt_interrupted':
            # Crash after durable terminal envelope, before the ledger usage commit.
            c.execute(model_attempts.update().where(model_attempts.c.id==attempt['id']).values(usage=None))
        if case=='active':
            c.execute(planning_jobs.update().where(planning_jobs.c.id==job['id']).values(status='running',finished_at=None))
        if case=='disconnected':
            path=tmp_path/'release/artifacts/runs'/('planning-evidence-'+attempt['id'])/'events.jsonl'
            events=[json.loads(line) for line in path.read_text().splitlines()]
            events=[e for e in events if e['phase']!='envelope']
            events.append(dict(phase='connection-closed'))
            path.write_text(''.join(json.dumps(e)+'\n' for e in events))
        original_attempt = dict(c.execute(select(model_attempts).where(model_attempts.c.id==attempt['id'])).mappings().one())
        original_job = dict(c.execute(select(planning_jobs).where(planning_jobs.c.id==job['id'])).mappings().one())
    command=dict(command_id=str(uuid4()),revision=saved['revision'],text='Continue this saved investigation.',
                 continue_investigation=True,renew_operation=True)
    endpoint='/api/intake/'+saved['id']+'/clarification'
    result=client.post(endpoint,headers=headers,json=command)
    if case in {'active','disconnected'}:
        assert result.status_code==409
        with store.connect() as c:
            snapshot=delivery_phase.observe(c,saved['id'],[tmp_path])
        assert not delivery_phase.renewable(snapshot)
        assert snapshot['phases'][0]['provider_state']==('active' if case=='active' else 'unknown')
        return
    assert result.status_code==200, result.text
    assert client.post(endpoint,headers=headers,json=command).status_code==200
    assert client.post(endpoint,headers=headers,json={**command,'command_id':str(uuid4())}).status_code==409
    with store.connect() as c:
        jobs=c.execute(select(planning_jobs).where(planning_jobs.c.intake_id==saved['id'])).mappings().all()
        assert len(jobs)==2
        assert dict(jobs[0])==original_job
        assert dict(c.execute(select(model_attempts).where(model_attempts.c.id==attempt['id'])).mappings().one())==original_attempt
        continuation=jobs[1]['payload']['bug_followup']
        assert continuation['consumed_requests']==1
        assert continuation['prior_phases'][0]['usage']==original_attempt['usage']
        assert continuation.get('unknown_prior_usage',False)==(case in {'unknown_usage','receipt_interrupted'})
        assert continuation['known_prior_usage']['total_tokens']==(0 if case=='unknown_usage' else 50)
    from test_bug_source_planning import scripted
    calls=scripted(monkeypatch,[dict(finding='Source inspection remains read-only.',draft=dict(limitation='Review-only result.'))])
    assert worker.tick(store)
    assert not worker.tick(store)
    assert len(calls)==1
    current=client.get('/api/intake/'+saved['id']).json()
    with store.connect() as c:
        original=c.execute(select(intakes.c.payload).where(intakes.c.id==saved['id'])).scalar_one()
    assert original['description']==request['description'] and original['request_id']==request['request_id']
    assert not current['decisions'] and not current['tasks']


@pytest.mark.usefixtures('installed')
def test_bug_report_discovers_python_and_png_and_saves_only_a_review_plan(client,headers,store,monkeypatch,tmp_path):
    setup(monkeypatch,tmp_path)
    from test_bug_source_planning import scripted
    plan=dict(scope='Correct only PNG reproducibility comparison after confirming the encoding contract.',
              read_paths=[TEST,TOOL,PNG],writable_paths=[TEST],focused_test=TEST,
              criteria=[dict(id='artwork-preserved',description='Detect pixel, alpha and dimensions changes; preserve approved artwork and texture bytes.')],risks=[])
    answers=[dict(finding='Inspect the actual Python test, generator and binary metadata.',draft=dict(inspection=[
        dict(path=p,start=1,end=80) for p in (TEST,TOOL,PNG)])),
        dict(finding='The committed test compares encoded bytes; the PNG read supplied only metadata.',draft=dict(plan=plan))]
    calls=scripted(monkeypatch,answers)
    saved=client.post('/api/intake',headers=headers,json=report()).json()
    assert worker.tick(store)
    current=client.get('/api/intake/'+saved['id']).json()
    assert len(calls)==2 and len(current['proposals'])==1, current
    directory=calls[0]['source_directory']['directories']
    assert Path(TEST).name in directory['tests'] and Path(TOOL).name in directory['tools']
    assert Path(PNG).name in directory['assets/reminder-gallery']
    files={f['path']:f for f in calls[1]['files']}
    assert files[PNG]['content_kind']=='controlled-png-metadata-v1'
    assert 'binary bytes and embedded text withheld' in files[PNG]['excerpts'][0]['text']
    proposal=current['proposals'][0]
    assert proposal['task_contract']['bug_intent']['plan']['writable_paths']==[TEST]
    assert proposal['task_contract']['scope_missing'] and proposal['approval_blockers']
    assert not proposal['task_contract']['verification_commands']
    assert not current['tasks'] and not current['decisions']
    before=copy.deepcopy(proposal)
    command=dict(command_id=str(uuid4()),proposal_id=proposal['id'],binding=proposal['binding'],
        scope_revision=proposal['scope_revision'],action='approve',note='Must remain held',
        expectation_review=proposal['task_contract']['bug_intent']['review_digest'])
    assert client.post('/api/intake/'+saved['id']+'/decisions',headers=headers,json=command).status_code in {409,422}
    assert client.get('/api/intake/'+saved['id']).json()['proposals'][0]==before


@pytest.mark.usefixtures('installed')
def test_new_source_formats_retain_disclosure_and_committed_file_boundaries(monkeypatch,tmp_path):
    root=setup(monkeypatch,tmp_path)
    from workbench import bug_source_planning as source
    from workbench.source_inspection import InvestigationRead
    from workbench.planning_transport import PlanningUnavailable
    base=git(root,'rev-parse','HEAD').strip()
    packet=dict(base=base,files=[],policy_sha256='test-policy')
    initial=copy.deepcopy(packet)
    for path in (TEST,TOOL,PNG):
        with pytest.raises(PlanningUnavailable,match='disclosure'):
            source.inspect(packet,[InvestigationRead(path=path,start=1,end=2)],disclosure_paths=set())
        source.inspect(packet,[InvestigationRead(path=path,start=1,end=2)])
    source.verify_packet(initial,packet)
    assert source.search(base,'expected_png')[0]['path']==TEST
    for path in ('tools/absent.py','tests/absent.py'):
        errors=[]
        assert source.inspect(packet,[InvestigationRead(path=path,start=1,end=2)],errors=errors)==([],[],0)
        assert errors[0]['status']=='unavailable'
    for path in ('/etc/passwd','tools/../private.py','tools/.private.py','tools/vendor/private.py'):
        with pytest.raises(ValueError):
            source.committed(path,base)
    for path in ('tools/too-large.py','tools/link.py'):
        with pytest.raises(ValueError):
            source.committed(path,base)
    # Uncommitted, symlink and oversized regular files cannot become evidence.
    (root/'tools/uncommitted.py').write_text('secret')
    with pytest.raises(ValueError):
        source.committed('tools/uncommitted.py',base)


@pytest.mark.usefixtures('installed')
def test_continuation_preserves_prior_proposal_scope_decision_and_usage(client,headers,store,monkeypatch,tmp_path):
    setup(monkeypatch,tmp_path)
    from test_bug_source_planning import scripted
    from test_bug_intent_workflow import scope
    from workbench.db import proposals,proposal_decisions,intake_scopes
    scripted(monkeypatch,[dict(finding='A source-bound plan for review only.',draft=dict(plan=scope()))])
    saved=client.post('/api/intake',headers=headers,json=report()).json()
    assert worker.tick(store)
    url='/api/intake/'+saved['id']
    current=client.get(url).json()
    proposal=current['proposals'][0]
    command=dict(command_id=str(uuid4()),proposal_id=proposal['id'],binding=proposal['binding'],
        scope_revision=proposal['scope_revision'],action='changes',note='Inspect the Python failure before changing Lua.')
    assert client.post(url+'/decisions',headers=headers,json=command).status_code==200
    assert worker.tick(store)
    terminal(monkeypatch)
    assert worker.tick(store)
    current=client.get(url).json()
    assert current['status']=='failed'
    tables=[planning_jobs,model_attempts,proposals,proposal_decisions,intake_scopes]
    def history():
        with store.connect() as c:
            return {t.name:[dict(r) for r in c.execute(select(t).where(
                (t.c.task_id if t is model_attempts else t.c.intake_id)==saved['id'])).mappings()]
                for t in tables}
    before=history()
    command=dict(command_id=str(uuid4()),revision=current['revision'],text='Continue the same saved investigation.',
                 continue_investigation=True,renew_operation=True)
    renewed=client.post(url+'/clarification',headers=headers,json=command)
    assert renewed.status_code==200,renewed.text
    after=history()
    for table,rows in before.items():
        assert all(row in after[table] for row in rows)
    assert len(after['planning_jobs'])==len(before['planning_jobs'])+1
    assert after['model_attempts']==before['model_attempts']
    assert after['proposal_decisions']==before['proposal_decisions']
