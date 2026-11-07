"""Operator policy denials expose only fixed reasons; malformed data stays private."""
import ast
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT=Path(__file__).resolve().parents[2]


def test_all_public_policy_failure_reasons_are_literal_application_text():
    tree=ast.parse((ROOT/'scripts/acceptance_policy.py').read_text())
    calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)
           and n.func.id=='require' and len(n.args)>1]
    assert calls
    assert all(isinstance(c.args[1],ast.Constant) and isinstance(c.args[1].value,str) for c in calls)


@pytest.mark.parametrize('fault',['policy','malformed'])
def test_policy_cli_returns_bounded_structured_denial_without_input_or_source(tmp_path,fault):
    from acceptance_policy import OUTCOME
    path=tmp_path/'policy.json'
    path.write_text(json.dumps(dict(outcome=OUTCOME,cases=[dict(choice='bug-flight-paths',
        request=dict(kind='issue',PRIVATE_SECRET='PRIVATE_SOURCE'),criteria=[])])))
    request=json.dumps(dict(choice='bug-flight-paths',action='submit',PRIVATE_SECRET='PRIVATE_SOURCE'))
    result=subprocess.run([sys.executable,str(ROOT/'scripts/acceptance_policy.py'),'admit','--policy',str(path)],
        input='{' if fault=='malformed' else request,capture_output=True,text=True,timeout=10)
    assert result.returncode==2 and not result.stdout
    reason=json.loads(result.stderr)
    assert reason==dict(held=True,reason='pinned bug request changed' if fault=='policy' else
                       'Acceptance policy input or state is unavailable.')
    assert len(result.stderr)<256 and 'PRIVATE' not in result.stderr and 'Traceback' not in result.stderr
