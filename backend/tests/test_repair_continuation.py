"""Known unfinished model work can move to a fresh bound copy, never the original."""
import json
import os
from test_everyday import installed  # noqa: F401
from pathlib import Path

import pytest

from workbench import repair_continuation as continuation
from workbench.real_workspace import durable,sha,tree
from workbench import verification_profiles as profiles
from test_verification_profiles import setup,PRODUCTION,TEST,BUGGY,CORRECT


def unfinished(tmp_path):
    runtime=setup(tmp_path,profiles.BUGFIX)
    runtime.paths=(PRODUCTION,TEST)
    frozen=tmp_path/'saved-unfinished'
    durable(frozen/'files'/PRODUCTION,CORRECT.encode())
    expected=tree(runtime.workspace)
    expected[PRODUCTION]=sha(CORRECT.encode())
    resume=dict(frozen_root=str(frozen),base=runtime.base,tree=expected,
        files={PRODUCTION:sha(CORRECT.encode())},execution_id='previous-known-failed-execution')
    durable(frozen/'resume.json',json.dumps(resume,sort_keys=True).encode())
    durable(runtime.root/'repair-resume.json',json.dumps(resume,sort_keys=True).encode())
    return runtime,resume


def test_resume_requires_fresh_permission_and_preserves_original_and_old_evidence(tmp_path):
    runtime,resume=unfinished(tmp_path)
    before=tree(runtime.workspace)
    with pytest.raises(ValueError):
        continuation.restore(runtime,lambda:False)
    assert tree(runtime.workspace)==before
    continuation.restore(runtime,lambda:True)
    assert tree(runtime.workspace)==resume['tree']
    assert (tmp_path/'source'/PRODUCTION).read_text()==BUGGY
    assert (Path(resume['frozen_root'])/'files'/PRODUCTION).read_text()==CORRECT
    with pytest.raises(ValueError,match='already activated'):
        continuation.restore(runtime,lambda:True)


@pytest.mark.parametrize('fault',['file','metadata','symlink','outside','base','extra-write'])
def test_changed_saved_work_or_binding_cannot_be_applied(tmp_path,fault):
    runtime,resume=unfinished(tmp_path)
    frozen=Path(resume['frozen_root'])
    if fault=='file':
        (frozen/'files'/PRODUCTION).write_text('changed')
    elif fault=='metadata':
        (frozen/'resume.json').write_text('{}')
    elif fault=='symlink':
        path=frozen/'files'/PRODUCTION
        path.unlink()
        path.symlink_to(tmp_path/'source'/PRODUCTION)
    else:
        if fault=='outside':
            resume['files']['../unapproved.lua']=sha(b'x')
        elif fault=='base':
            resume['base']='f'*40
        else:
            (runtime.workspace/TEST).write_text('-- unexplained\n')
        (frozen/'resume.json').write_text(json.dumps(resume,sort_keys=True))
        (runtime.root/'repair-resume.json').write_text(json.dumps(resume,sort_keys=True))
    with pytest.raises(ValueError):
        continuation.restore(runtime,lambda:True)
    assert not (runtime.root/'repair-resume-applied.json').exists()


@pytest.mark.usefixtures('installed')
def test_normal_browser_run_continuation_binds_failed_work_preserves_usage_and_denies_active_or_unknown(
    client,headers,store,monkeypatch,
):
    from uuid import uuid4
    from sqlalchemy import select
    from workbench import planning_worker,task_budget
    from workbench.db import proposal_decisions,task_run_offers,task_sessions
    from workbench.db import real_execution_grants as grants,real_execution_requests as requests,write_transaction
    from test_bug_intent_workflow import prepared_scope
    from test_grounded_workflow import ControlledPreparer
    from workbench import task_session,task_execution
    from workbench.real_execution import now
    item,scope,proposal,_,endpoint,view=prepared_scope(client,headers,store,monkeypatch)
    first=dict(command_id=str(uuid4()),scope_approval_id=scope['command_id'],binding=view['binding'],
        run_budget=view['offer']['budget'],note='Controlled first scoped run')
    assert client.post(endpoint,headers=headers,json=first).status_code==200
    assert planning_worker.apply_one(store,command_id=first['command_id'])
    with store.connect() as c:
        selected=dict(c.execute(select(task_run_offers)).mappings().one())
        template=task_budget.preparation_inputs(c,selected)
    root=Path(selected['snapshot']['ready_preparation']['root']).parent/'failed-implementation/real-run'
    root.mkdir(parents=True)
    durable(root/'recipe.json',json.dumps(template['recipe']).encode())
    durable(root/'recipe-oracle.lua',template['oracle'].encode())
    runtime=ControlledPreparer(root)
    task_session.provision(runtime,template)
    (runtime.workspace/PRODUCTION).write_text(CORRECT)
    old_tree=tree(runtime.workspace)
    gid,rid,fence=str(uuid4()),str(uuid4()),str(uuid4())
    stamp=now()
    usage=dict(status='complete',units='tokens',values=dict(prompt_tokens=30,completion_tokens=10,total_tokens=40))
    durable(root.parent/'lease-controller-result.json',b'{"restoration":{"restored":true}}')
    durable(root/('ownership-'+rid+'.json'),json.dumps(dict(owner=dict(pid=999999999,boot='no-longer-running',start='0'),
        cgroup='/sys/fs/cgroup/workbench-real-controlled-absent')).encode())
    with write_transaction(store) as c:
        linked=task_execution.snapshot(c,item['id'],selected['snapshot']['reviewed_task']['ref']['task_id'],first['command_id'])
        snapshot=dict(workflow_task=linked,task_budget=dict(decision_id=first['command_id']),
            recovery=runtime.recovery_identity(),runtime=runtime.identity())
        c.execute(grants.insert().values(id=gid,snapshot=snapshot,binding='controlled-prior-grant',
            issued_at=stamp,expires_at=stamp,revoked_at=stamp))
        c.execute(requests.insert().values(id=rid,grant_id=gid,payload={},status='failed',fence=fence,
            message='Known local validation rejection',created_at=stamp,started_at=stamp,finished_at=stamp,
            result=dict(verification='unknown',usage=usage)))
        c.execute(task_sessions.insert().values(id=first['command_id'],task_id=linked['task']['id'],snapshot={},binding='controlled-session',
            status='failed',message='Settled failure',state={},created_at=stamp,finished_at=stamp))
    before=client.get(endpoint).json()
    assert before['available'] and before['receipt'] is None
    cycle=before['offer']['repair_cycle']
    assert cycle['verification']=='not_run' and cycle['continuation']['execution_ids']==[rid]
    assert before['offer']['id']!=view['offer']['id']
    # GET neither registers an offer nor changes saved source/history.
    assert client.get(endpoint).json()==before
    with store.connect() as c:
        assert len(c.execute(select(task_run_offers)).all())==1
    for status,changed_usage in [('running',usage),('checked',usage),('failed',{**usage,'status':'unknown'})]:
        with write_transaction(store) as c:
            c.execute(requests.update().where(requests.c.id==rid).values(status=status,result=dict(usage=changed_usage)))
        current=client.get(endpoint).json()
        assert current['offer']['id']==view['offer']['id'] and current['receipt']
    with write_transaction(store) as c:
        c.execute(requests.update().where(requests.c.id==rid).values(status='failed',result=dict(verification='unknown',usage=usage)))
    renewed=dict(command_id=str(uuid4()),scope_approval_id=scope['command_id'],binding=before['binding'],
        run_budget=before['offer']['budget'],note='Continue known unfinished repair after reviewing feedback')
    saved=client.post(endpoint,headers=headers,json=renewed)
    assert saved.status_code==200,saved.text
    assert client.post(endpoint,headers=headers,json=renewed).json()==saved.json()
    assert planning_worker.apply_one(store,command_id=renewed['command_id'])
    with store.connect() as c:
        old=c.execute(select(requests).where(requests.c.id==rid)).mappings().one()
        assert old['result']['usage']==usage and old['status']=='failed'
        assert c.execute(select(proposal_decisions.c.status).where(proposal_decisions.c.id==first['command_id'])).scalar()=='applied'
        selected=task_budget.offer(c,proposal['id'])
        template=task_budget.preparation_inputs(c,selected)
    assert template['repair_resume']['tree']==old_tree
    assert tree(runtime.workspace)==old_tree
    new=root.parent.parent/'fresh-continuation/real-run'
    new.mkdir(parents=True)
    durable(new/'recipe.json',json.dumps(template['recipe']).encode())
    durable(new/'recipe-oracle.lua',template['oracle'].encode())
    durable(new/'repair-resume.json',json.dumps(template['repair_resume'],sort_keys=True).encode())
    fresh=ControlledPreparer(new)
    task_session.provision(fresh,template)
    assert (fresh.workspace/PRODUCTION).read_text()==BUGGY
    continuation.restore(fresh,lambda:True)
    assert tree(fresh.workspace)==old_tree
    assert (Path(os.environ['WORKBENCH_RESEARCH_ROOT'])/PRODUCTION).read_text()==BUGGY
