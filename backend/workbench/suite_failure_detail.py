"""Application-owned diagnosis of a fixed existing failed Lua test.

The complete-suite result stays failed. This only recovers assertion text hidden
by the committed suite runner's tail. No model command, new read path or shell.
"""
import json
import re

from workbench.real_workspace import PRELUDE, durable, sha, tree
from workbench.real_tool_contract import ToolDenied


def failed_tests(runtime, result):
    inventory=set(runtime.expected_suite())
    return list(dict.fromkeys(name for name in re.findall(
        r'^FAIL (test_[A-Za-z0-9_-]+\.lua)\s*$',result.get('out',''),re.MULTILINE)
        if name in inventory))[:2]


def observe(runtime, result, folder, permitted, *, workspace=None):
    from workbench.lua_failure_context import command
    workspace = workspace or runtime.workspace
    before = tree(workspace)
    details=[]
    for name in failed_tests(runtime,result):
        if not permitted():
            raise ToolDenied('candidate_denied','Permission changed before fixed failure diagnosis')
        argv = command(['lua5.1','-e',PRELUDE,name], 'tests/'+name)
        observed=runtime.check(argv,workspace/'tests',ro=[workspace],timeout=120)
        from workbench.real_workspace import require_safe_check
        require_safe_check(observed)
        durable(folder/('failure-detail-'+name+'.json'),json.dumps(observed,sort_keys=True).encode())
        source = (workspace/'tests'/name).read_bytes()
        locations = re.findall(re.escape(name)+r':([0-9]+):', observed.get('out',''))
        line = int(locations[0]) if locations else None
        excerpt = source.decode().splitlines()[max(0,line-5):line+4] if line else []
        details.append(dict(test=name,result=observed,base=runtime.base,
            source_sha256=sha(source),workspace_tree_sha256=sha(json.dumps(before,sort_keys=True).encode()),
            line=line,source_excerpt='\n'.join(excerpt)[:600]))
    if tree(workspace) != before or not permitted():
        raise ToolDenied('candidate_changed','Source or permission changed during failure diagnosis')
    return details


def previous_failure(runtime, previous, permitted):
    """Observe a settled failed candidate read-only; never restore it as input."""
    from pathlib import Path
    from workbench.repair_continuation import private_json
    from workbench.real_workspace import Runtime
    from workbench.visual_scope import digest
    old = Runtime(Path(previous['recovery_root']))
    if old.base != runtime.base or digest(tree(old.workspace)) != previous['original_tree_sha256']:
        raise ToolDenied('candidate_changed','Prior failed source differs from its settlement proof')
    folders = sorted((old.root/'working-checks').glob('[0-9][0-9]/outcome.json'))
    if folders and private_json(folders[-1]).get('failed_check_name') == 'complete-suite':
        checks = sorted(folders[-1].parent.glob('check-[0-9][0-9].json'))
        result = private_json(checks[-1])
    else:
        # A turn-limited attempt may never reach candidate checks. Observe its
        # exact approved focused test instead of losing the latest setup error.
        result = dict(out='FAIL '+old.recipe['focused_test'].split('/')[-1])
    details = observe(runtime,result,runtime.root/'prior-failure',permitted,workspace=old.workspace)
    durable(runtime.root/'prior-failure/observations.json',json.dumps(details,sort_keys=True).encode())
    return details
