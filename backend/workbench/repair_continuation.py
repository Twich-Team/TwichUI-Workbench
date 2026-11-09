"""Fresh run bindings for settled scoped repairs; never replay an old request.

Old decisions, usage, failures and model files remain immutable history. A normal
Run approval binds an exact frozen copy of known unfinished model-authored work.
Unknown execution, active work, checked results and changed provider/scope hold.
"""
import json
import os
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select

from workbench import real_execution as execution
from workbench.db import proposal_decisions, real_execution_grants as grants
from workbench.db import real_execution_requests as requests, task_sessions, real_publications
from workbench.visual_scope import digest


def private_json(path,limit=2<<20):
    path=Path(path)
    if (any(p.is_symlink() for p in (path,*path.parents)) or not path.is_file()
            or path.stat().st_uid!=os.getuid() or path.stat().st_size>limit):
        raise ValueError('Owned bounded continuation evidence required')
    return json.loads(path.read_text())


def settled(c,selected):
    """Prove the most recent use of this exact offer is a settled failed run."""
    saved=selected['snapshot']
    ref=saved['reviewed_task']['ref']
    budgets=c.execute(select(proposal_decisions).where(
        proposal_decisions.c.proposal_id==selected['proposal_id'],
        proposal_decisions.c.payload['run_budget']['offer_id'].as_string()==selected['id']
    ).order_by(proposal_decisions.c.created_at.desc())).mappings().all()
    if not budgets:
        return None
    budget=budgets[0]
    session=c.execute(select(task_sessions).where(task_sessions.c.id==budget['id'])).mappings().first()
    if not session or session['status']!='failed' or not session['finished_at']:
        return None
    histories=c.execute(select(requests,grants.c.snapshot.label('grant_snapshot'),grants.c.revoked_at)
        .join(grants,requests.c.grant_id==grants.c.id).where(
            grants.c.snapshot['workflow_task']['task']['id'].as_string()==ref['task_id'])
        .order_by(requests.c.created_at.desc())).mappings().all()
    if not histories or any(r['status']!='failed' or not r['finished_at'] or not r['revoked_at']
            or (r['result'] or {}).get('usage',{}).get('status')!='complete' for r in histories):
        return None
    row=histories[0]
    if row['grant_snapshot']['task_budget']['decision_id']!=budget['id']:
        return None
    if c.execute(select(real_publications.c.id).where(real_publications.c.task_id==ref['task_id'])).first():
        return None
    root=Path(row['grant_snapshot']['recovery']['root'])
    from workbench.real_workspace import Runtime,sha,tree
    runtime=Runtime(root)
    if not runtime.owns_recovery(dict(snapshot=row['grant_snapshot'])):
        raise ValueError('Failed repair recovery identity changed')
    restoration=private_json(root.parent/'lease-controller-result.json')['restoration']
    if restoration.get('restored') is not True:
        raise ValueError('Failed repair service restoration is not settled')
    # The long-running worker is not the provider. Check the actual per-run owner.
    owned=private_json(root/('ownership-'+row['id']+'.json'))
    if execution.alive(owned['owner']):
        raise ValueError('Prior repair process remains active')
    group=Path(owned['cgroup'])
    if not group.is_relative_to('/sys/fs/cgroup') or not group.name.startswith('workbench-real-'):
        raise ValueError('Prior repair execution boundary is unavailable')
    if group.exists() and any(p.read_text().strip() for p in group.rglob('cgroup.procs')):
        raise ValueError('Prior repair descendants remain active')
    if list((root/'edit-batches').glob('*/prepared.json')):
        for prepared in (root/'edit-batches').glob('*/prepared.json'):
            if not any((prepared.parent/n).exists() for n in ('applied.json','rolled-back.json')):
                raise ValueError('Interrupted local edit batch requires reconciliation first')
    if runtime.recipe!=saved['specification']:
        raise ValueError('Failed repair scope changed')
    changed,patch=runtime.scope()
    from workbench import provider_routes,task_execution
    route=provider_routes.run_selection(c,task_execution.snapshot(c,**ref))
    prior=row['grant_snapshot']['runtime'].get('codex_selection')
    if bool(route)!=bool(prior) or route and any(route.get(k)!=prior.get(k) for k in ('model','effort')):
        raise ValueError('A repair continuation cannot silently switch providers')
    usage=row['result']['usage']
    receipts=[]
    if prior:
        aggregate=private_json(root/('codex-usage-'+row['id']+'.json'))
        if (aggregate.get('fence')!=row['fence'] or aggregate.get('values')!=usage['values']
                or sha((root/('codex-usage-'+row['id']+'.json')).read_bytes())!=usage['provenance']['receipt_sha256']):
            raise ValueError('Completed provider usage provenance changed')
        for number in range(1,usage['requests_observed']+1):
            receipt=private_json(root/('codex-turn-'+row['id']+'-'+str(number)+'.json'))
            if not receipt.get('provider_receipt'):
                raise ValueError('Completed provider receipt identity missing')
            receipts.append(receipt)
        if len(receipts)!=aggregate['requests_observed']:
            raise ValueError('Provider receipt count changed')
    return dict(previous_offer_id=selected['id'],execution_id=row['id'],session_id=session['id'],
        root=str(root),base=runtime.base,tree=tree(runtime.workspace),patch_sha256=sha(patch.encode()),
        files={name:sha((runtime.workspace/name).read_bytes()) for name in changed},
        usage_sha256=digest(usage),receipts_sha256=digest(receipts),provider=prior,
        histories=[dict(execution_id=r['id'],model_attempt_id=r['fence'],
            session_id=r['grant_snapshot']['task_budget']['decision_id'],usage=r['result']['usage']) for r in histories])


def preview(c,selected):
    if not selected['snapshot']['specification'].get('repair_cycle'):
        return selected
    proof=settled(c,selected)
    if proof is None:
        return selected
    root=Path(proof['root'])/'repair-resumes'/digest(proof)
    resume=dict(**proof,frozen_root=str(root))
    snapshot={**selected['snapshot'],'repair_resume':resume}
    fingerprint=digest(snapshot)
    return dict(id=str(uuid5(NAMESPACE_URL,'workbench-repair:'+fingerprint)),
        proposal_id=selected['proposal_id'],fingerprint=fingerprint,snapshot=snapshot,
        created_at=selected['created_at'])


def freeze(c,selected):
    """POST only. The exact preview must still match before saving any decision."""
    resume=selected['snapshot'].get('repair_resume')
    if not resume:
        return
    from workbench.db import task_run_offers
    if c.execute(select(task_run_offers.c.id).where(task_run_offers.c.id==selected['id'])).first():
        validate_frozen(resume)
        return
    prior=c.execute(select(task_run_offers).where(task_run_offers.c.id==resume['previous_offer_id'])).mappings().one()
    if preview(c,dict(prior))!=selected:
        raise ValueError('Unfinished repair or provider settlement changed; review again')
    from workbench.real_workspace import Runtime,durable
    runtime=Runtime(resume['root'])
    root=Path(resume['frozen_root'])
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    for name in resume['files']:
        durable(root/'files'/name,(runtime.workspace/name).read_bytes())
    durable(root/'resume.json',json.dumps(resume,sort_keys=True).encode())
    validate_frozen(resume)
    c.execute(task_run_offers.insert().values(**{**selected,'created_at':execution.now()}))


def validate_frozen(resume):
    from workbench.real_workspace import sha
    root=Path(resume['frozen_root'])
    if private_json(root/'resume.json')!=resume:
        raise ValueError('Saved unfinished repair binding changed')
    for name,expected in resume['files'].items():
        if name.startswith('/') or '..' in Path(name).parts:
            raise ValueError('Invalid continuation path')
        path=root/'files'/name
        if (any(p.is_symlink() for p in (path,*path.parents)) or not path.is_file()
                or path.stat().st_uid!=os.getuid() or path.stat().st_size>65536 or sha(path.read_bytes())!=expected):
            raise ValueError('Saved unfinished repair file changed')


def restore(runtime,permitted):
    """Only a fresh granted run can copy the already disclosed unfinished work."""
    path=runtime.root/'repair-resume.json'
    if not path.is_file():
        return
    resume=private_json(path)
    validate_frozen(resume)
    if runtime.base!=resume['base'] or not set(resume['files']).issubset(runtime.paths):
        raise ValueError('Continuation scope changed')
    from workbench.repair_edits import commit
    from workbench.real_workspace import durable,tree
    marker=runtime.root/'repair-resume-applied.json'
    if marker.exists():
        raise ValueError('Saved repair was already activated; reconcile instead of replaying')
    if tree(runtime.workspace)!=json.loads(runtime.initial.read_text()):
        raise ValueError('A continuation requires a fresh unchanged isolated copy')
    planned={}
    for name in resume['files']:
        old=(runtime.workspace/name).read_text()
        new=(Path(resume['frozen_root'])/'files'/name).read_text()
        if old!=new:
            planned[name]=new
    if planned:
        commit(runtime,planned,permitted)
    if tree(runtime.workspace)!=resume['tree']:
        raise ValueError('Restored repair differs from the approved unfinished work')
    durable(marker,json.dumps(dict(tree=resume['tree'],previous_execution=resume['execution_id']),sort_keys=True).encode())
