"""Application-owned reproduction capabilities in the existing confined runner.

A model selects a committed test, never shell text. Durable create-only intents
prevent replay; outputs remain observations, not diagnoses or verification claims.
"""
from pathlib import Path
from typing import Literal
import json
import re
import shutil
import subprocess
from types import SimpleNamespace
from contextlib import contextmanager
import tempfile

from pydantic import Field, model_validator
from sqlalchemy import select
from workbench.bug_evidence import Data
from workbench.visual_scope import digest


def durable(path, raw):
    from workbench.real_workspace import durable as save
    save(path, raw)
    path.chmod(0o600)


class Check(Data):
    capability: Literal['lua-test', 'python-test', 'repository-suite']
    path: str = Field(max_length=240)

    @model_validator(mode='after')
    def supported(self):
        expected = {'lua-test': r'tests/test_[A-Za-z0-9_-]+\.lua',
                    'python-test': r'tests/test_[A-Za-z0-9_-]+\.py',
                    'repository-suite': r'tests/run\.sh'}
        if not re.fullmatch(expected[self.capability], self.path):
            raise ValueError('Choose a supported committed test, never a command or arguments.')
        return self


def command(check):
    from workbench.real_workspace import PRELUDE
    if check.capability == 'lua-test':
        return ['lua5.1', '-e', PRELUDE, Path(check.path).name], 'tests'
    if check.capability == 'python-test':
        return ['python3', '-B', '-m', 'unittest', '-v', Path(check.path).stem], 'tests'
    return ['bash', 'tests/run.sh'], '.'


def classify(result):
    """Only observable failure classes; none proves a production-code diagnosis."""
    if result.get('status') == 'pass' and result.get('rc') == 0:
        return 'passed'
    output = result.get('out', '')
    if re.search(r'ModuleNotFoundError|No such file|not found|Permission denied', output):
        return 'environment-or-fixture-failure'
    if re.search(r"AssertionError|assertion failed|in function [\'\"]assert[\'\"]", output):
        return 'assertion-failure'
    return 'unclassified-failure'


@contextmanager
def original_copy(engine, root, base, evidence_root):
    """Shared original-only disposable source for application-owned checks."""
    from workbench.everyday import configuration
    from workbench.real_workspace import git
    from workbench.storage_resources import require
    temporary = Path(tempfile.mkdtemp(prefix='workbench-verify-', dir=configuration(engine).scratch))
    workspace = temporary/'workspace'
    try:
        require(evidence_root, workspace)
        subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'protocol.allow=never',
            '-c', 'protocol.file.allow=always', 'clone', '--local', '--no-hardlinks', '--no-recurse-submodules',
            '--quiet', str(root), str(workspace)], check=True, timeout=60,
            env={'PATH':'/usr/bin:/bin','GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null'})
        git(workspace, 'remote', 'remove', 'origin')
        git(workspace, 'config', 'core.hooksPath', '/dev/null')
        if git(workspace, 'rev-parse', 'HEAD').strip() != base:
            raise ValueError('Reproduction copy source changed.')
        require(evidence_root, workspace)
        yield workspace
    finally:
        shutil.rmtree(temporary)


def run(engine, attempt_id, packet, requested, should_stop, *, disclosure_paths=None):
    from workbench import bug_authority, regression_preparation as source, real_confine
    from workbench.db import model_attempts, planning_jobs
    from workbench.real_workspace import sha, tree, require_safe_check
    from workbench.trusted_preparation import configuration, owned
    check = Check.model_validate(requested)
    with engine.connect() as c:
        identity = c.execute(select(planning_jobs.c.intake_id).join(model_attempts,
            model_attempts.c.job_id == planning_jobs.c.id).where(model_attempts.c.id == attempt_id)).scalar_one()
        authority = bug_authority.current(c, identity)
    if packet['base'] != authority['base'] or (disclosure_paths is not None and check.path not in disclosure_paths):
        raise ValueError('Reproduction needs the frozen source and permitted test disclosure.')
    root, base, inventory = source.repository()
    if base != packet['base'] or check.path not in inventory:
        raise ValueError('The requested test is missing or committed source changed.')
    from workbench.bug_investigation import clean_base
    clean_base(root, base)
    policy = configuration(engine)
    key = digest(dict(attempt_id=attempt_id, base=base, check=check.model_dump(), policy_sha256=authority['policy_sha256']))
    folder = owned(policy['output_root']) / ('investigation-check-' + key)
    if folder.exists():
        return read(folder, base, key)  # An incomplete intent holds; no duplicate check.
    prior = packet.get('reproductions', [])
    if len(prior) >= 6:
        raise ValueError('The bounded reproduction capability limit was reached.')
    if should_stop():
        raise ValueError('Investigation stopped before reproduction.')
    folder.mkdir(mode=0o700)
    intent = dict(version=1, key=key, base=base, attempt_id=attempt_id, intake_id=identity,
        check=check.model_dump(), policy_sha256=authority['policy_sha256'])
    durable(folder/'intent.json', json.dumps(intent, sort_keys=True).encode())
    with original_copy(engine, root, base, folder) as workspace:
        before = tree(workspace)
        argv, cwd = command(check)
        from workbench.storage_resources import require
        require(folder, workspace)
        with engine.connect() as c:
            bug_authority.current(c, identity)
        if should_stop():
            raise ValueError('Investigation stopped before check dispatch.')
        from workbench.verification_process import VerificationProcess
        suite = check.capability == 'repository-suite'
        real_confine.USAGE_LOG = str(folder/'usage.jsonl')
        result = real_confine.run(argv, str(workspace/cwd), 600 if suite else 120,
            stop_event=SimpleNamespace(is_set=should_stop), ro=[str(workspace)], env={'PATH':'/usr/bin:/bin','HOME':'/nonexistent','LANG':'C.UTF-8',
                'PYTHONDONTWRITEBYTECODE':'1'}, private_loopback=suite,
            **(dict(verification=VerificationProcess(tuple(argv),str(workspace))) if suite else {}))
        result.update(command=argv, working_directory=str(workspace/cwd), network_mode='private-loopback' if suite else 'denied')
        require_safe_check(result)
        if tree(workspace) != before or should_stop():
            raise ValueError('Reproduction inputs changed or investigation stopped.')
        durable(folder/'result.json', json.dumps(result, sort_keys=True).encode())
        # Raw test output is retained privately. It can mention files outside a
        # provider's frozen disclosure, so the outgoing observation is data-only.
        summary = dict(version=1, key=key, base=base, check=check.model_dump(), observed=True,
            outcome=classify(result), status=result['status'], rc=result['rc'],
            result_sha256=sha((folder/'result.json').read_bytes()), intent_sha256=sha((folder/'intent.json').read_bytes()),
            evidence_root=str(folder), diagnosis_verified=False,
            note='Observed committed check outcome. A failure does not establish its cause; inspect source and distinguish hypotheses.')
        durable(folder/'summary.json', json.dumps(summary, sort_keys=True).encode())
        return summary


def read(folder, base, key):
    from workbench.real_workspace import sha
    from workbench.trusted_preparation import owned
    folder = owned(folder)
    value = json.loads(owned(folder/'summary.json').read_text())
    if (value['base'] != base or value['key'] != key or folder.name != 'investigation-check-' + key
            or value['result_sha256'] != sha(owned(folder/'result.json').read_bytes())
            or value['intent_sha256'] != sha(owned(folder/'intent.json').read_bytes())):
        raise ValueError('Reproduction evidence changed.')
    return value


def verify_observations(packet):
    """Revalidate private receipts, never trust a model's claim that a check ran."""
    if not packet.get('reproductions'):
        return
    from workbench.trusted_preparation import owned
    import os
    config_path = owned(os.environ['WORKBENCH_PREPARATION_CONFIG'])
    if config_path.stat().st_mode & 0o077 or config_path.stat().st_size > 8192:
        raise ValueError('Private preparation policy required')
    config = json.loads(config_path.read_text())
    root = Path(config['output_root'])
    for observed in packet.get('reproductions', []):
        folder = Path(observed['evidence_root'])
        if folder.parent != root or read(folder, packet['base'], observed['key']) != observed:
            raise ValueError('Reproduction receipt is stale or changed.')
