"""Preview a whole scoped edit batch; commit or restore all of its local files."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
from uuid import uuid4

from workbench.real_tool_contract import ToolDenied,ToolInputError


class RepairConstraint(ToolInputError):
    def __init__(self,code,message,**counts):
        super().__init__(code,message)
        self.counts=counts

    def as_result(self,*_,**__):
        return dict(ok=False,error=dict(code=self.code,message=str(self),correctable=True,**self.counts))


def target(runtime,name):
    if name not in runtime.paths:
        raise ToolDenied('path_denied','This file is outside the approved write scope')
    path=runtime.workspace/name
    if (not path.is_file() or path.stat().st_uid!=os.getuid() or path.stat().st_nlink!=1
            or not path.resolve().is_relative_to(runtime.workspace.resolve())
            or any(p.is_symlink() for p in [path,*path.parents] if p==runtime.workspace or runtime.workspace in p.parents)):
        raise ToolDenied('file_denied','Owned existing regular scoped files are required')
    return path


def preview(runtime,planned):
    """Count final changes against the original base, not just this model turn."""
    from workbench.real_workspace import git
    from workbench.real_execution import LIMITS
    count=0
    with tempfile.TemporaryDirectory(prefix='edit-preview-',dir=runtime.root) as temporary:
        folder=Path(temporary)
        for index,name in enumerate(runtime.paths):
            path=target(runtime,name)
            before=git(runtime.workspace,'show',runtime.base+':'+name)
            after=planned.get(name,path.read_text())
            left,right=folder/(str(index)+'-base'),folder/(str(index)+'-candidate')
            left.write_text(before)
            right.write_text(after)
            result=subprocess.run(['git','--no-pager','diff','--no-ext-diff','--no-textconv',
                '--numstat','--no-index','--',str(left),str(right)],capture_output=True,text=True,timeout=10)
            if result.returncode not in (0,1):
                raise ValueError('Edit preview could not establish the whole candidate scope')
            if not result.stdout.strip():
                continue
            added,removed,_=result.stdout.strip().split('\t',2)
            if not added.isdecimal() or not removed.isdecimal():
                raise ToolDenied('binary_edit','Binary edits are unavailable')
            count+=int(added)+int(removed)
            if name.startswith('tests/') and int(removed):
                raise RepairConstraint('test-preservation','Existing tests may gain assertions; existing lines cannot be removed.')
    if count>LIMITS['changed_lines']:
        raise RepairConstraint('change-budget','The entire repair and regression tests must fit the approved changed-line limit. Revise the proposal; do not reduce required behavior coverage.',
            changed_lines=count,allowed_changed_lines=LIMITS['changed_lines'])
    return count


def replace_batch(runtime,edits,permitted):
    if not permitted():
        raise ToolDenied('grant_changed','Current run permission is required')
    reconcile(runtime,permitted)
    if not isinstance(edits,list) or not 1<=len(edits)<=6:
        raise ToolDenied('edit_shape','One to six exact scoped replacements are required')
    planned={}
    for edit in edits:
        if not isinstance(edit,dict) or set(edit)!={'path','old','new'}:
            raise ToolDenied('edit_shape','Exact scoped replacements are required')
        name,old,new=(edit[k] for k in ('path','old','new'))
        path=target(runtime,name)
        if not isinstance(old,str) or not old or not isinstance(new,str) or len(old.encode())+len(new.encode())>8192:
            raise ToolDenied('edit_size','Bounded exact replacement strings are required')
        current=planned.setdefault(name,path.read_text())
        if current.count(old)!=1:
            raise RepairConstraint('exact-match','No edits were applied. Each old string must match exactly once in current source.')
        planned[name]=current.replace(old,new,1)
        if len(planned[name].encode())>65536:
            raise RepairConstraint('file-budget','The complete proposed file exceeds the approved file-output bound.')
    return commit(runtime,planned,permitted)


def commit(runtime,planned,permitted):
    """Internal whole-file transaction, also used for hash-bound saved repairs."""
    from workbench.real_workspace import durable,sha,tree
    if not permitted() or not planned or not set(planned).issubset(runtime.paths):
        raise ToolDenied('grant_changed','Current scoped run permission is required')
    if any(not isinstance(v,str) or len(v.encode())>65536 for v in planned.values()):
        raise RepairConstraint('file-budget','The complete file exceeds the approved output bound.')
    count=preview(runtime,planned)
    folder=runtime.root/'edit-batches'/str(uuid4())
    before={name:target(runtime,name).read_bytes() for name in planned}
    initial=tree(runtime.workspace)
    for name,raw in before.items():
        durable(folder/'before'/name,raw)
    durable(folder/'prepared.json',json.dumps(dict(request=getattr(runtime,'_request_id',None),
        fence=getattr(runtime,'_fence',None),base=runtime.base,tree=initial,
        before={p:sha(v) for p,v in before.items()},after={p:sha(v.encode()) for p,v in planned.items()},
        changed_lines=count),sort_keys=True).encode())
    try:
        runtime.enforce_storage()
        for name,content in planned.items():
            if not permitted():
                raise ToolDenied('grant_changed','Run permission changed during the edit batch')
            target(runtime,name).write_text(content)
        runtime.scope()
        if not permitted():
            raise ToolDenied('grant_changed','Run permission changed before the edit batch completed')
        durable(folder/'applied.json',json.dumps(dict(tree=tree(runtime.workspace)),sort_keys=True).encode())
    except BaseException:
        for name,raw in before.items():
            target(runtime,name).write_bytes(raw)
        if tree(runtime.workspace)!=initial:
            raise ValueError('Edit batch restoration is uncertain; retain the workspace and hold') from None
        durable(folder/'rolled-back.json',b'{"restored":true}')
        raise
    return 'Scoped edit batch saved in the isolated workspace.'


def reconcile(runtime,permitted):
    """A crash cannot turn half a batch into unexplained approved work.

    Only restore an unfinished transaction whose complete tree consists of its
    exact before/after files. Any other modification remains a recovery hold.
    """
    from workbench.real_workspace import durable,sha,tree
    for folder in sorted((runtime.root/'edit-batches').glob('*')):
        if (folder/'applied.json').exists() or (folder/'rolled-back.json').exists():
            continue
        if folder.is_symlink() or not (folder/'prepared.json').is_file():
            raise ValueError('Incomplete edit journal; retain evidence and hold')
        record=json.loads((folder/'prepared.json').read_text())
        current=tree(runtime.workspace)
        if record['base']!=runtime.base or set(record['before'])!=set(record['after']):
            raise ValueError('Edit recovery binding changed')
        expected=dict(record['tree'])
        for name,before in record['before'].items():
            target(runtime,name)
            if current.get(name) not in {before,record['after'][name]}:
                raise ValueError('Uncertain local edit contents; retain evidence and hold')
            raw=(folder/'before'/name).read_bytes()
            if sha(raw)!=before:
                raise ValueError('Original local edit bytes changed')
            expected[name]=current[name]
        if current!=expected or not permitted():
            raise ValueError('Edit recovery scope or permission changed')
        for name in record['before']:
            target(runtime,name).write_bytes((folder/'before'/name).read_bytes())
        if tree(runtime.workspace)!=record['tree']:
            raise ValueError('Edit recovery restoration is uncertain')
        durable(folder/'rolled-back.json',b'{"restored":true}')
