"""Carry independently checked feature changes into the next isolated input.

The execution ledger and frozen completion remain authoritative. These are owned
source artifacts, not task state or permission. Nothing here dispatches a model,
publishes a branch, or certifies the combined feature.
"""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import subprocess

from sqlalchemy import select

from workbench import real_publication, task_execution
from workbench.db import real_execution_requests, real_execution_grants, planning_jobs
from workbench.publication_adapter import PilotGitHub
from workbench.real_workspace import durable
from workbench.trusted_preparation import owned, configuration
from workbench.visual_scope import digest


class Held(ValueError):
    pass


class LocalCheckedCommit(PilotGitHub):
    """Reuse exact local patch/commit reconciliation with credential-free Git.

    The remote publisher's credential and command helpers are never loaded.
    Network operations are refused even if a future caller invokes one by mistake.
    """
    real = False

    def _call(self, argv, cwd=None, raw=False):
        args = list(argv[1:])
        while len(args) >= 2 and args[0] == '-c':
            if args[1] not in {'protocol.allow=never','protocol.file.allow=always','core.hooksPath=/dev/null'}:
                raise Held('Unsupported local Git configuration.')
            args = args[2:]
        if (not args or args[0] not in {'clone','rev-parse','status','diff','checkout',
                'remote','apply','config','add','commit','show'}
                or (args[0] == 'config' and (len(args) != 4 or args[1] != '--local'
                    or args[2] not in {'user.name','user.email'}))
                or (args[0] == 'remote' and args[1:3] != ['set-url','origin'])):
            raise Held('Unsupported local checked-source command.')
        if (not self.permitted() or not argv or argv[0] != 'git'
                or any(v in {'push','fetch','pull','ls-remote','credential','submodule'} for v in argv)
                or ('clone' in argv and '--local' not in argv)):
            raise Held('Only current checked local source preparation is permitted.')
        result = subprocess.run(['git','-c','protocol.allow=never','-c','protocol.file.allow=always',
            '-c','core.hooksPath=/dev/null', *argv[1:]],
            cwd=cwd, env=dict(PATH='/usr/bin:/bin', LANG='C.UTF-8',
                GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_NOSYSTEM='1',
                GIT_TERMINAL_PROMPT='0', GIT_PROTOCOL_FROM_USER='0'),
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60)
        if result.returncode or len(result.stdout) > 4 << 20:
            raise Held('Local checked-source command failed or exceeded its output bound.')
        if self.location.exists():
            self._owned_checkout()
        return result.stdout if raw else result.stdout.decode().strip()


@contextmanager
def locked(root):
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    owned(root)
    if root.stat().st_mode & 0o077:
        raise Held('Private checked-source storage is required.')
    fd = os.open(root/'assembly.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if os.fstat(fd).st_uid != os.getuid() or os.fstat(fd).st_nlink != 1:
            raise Held('Checked-source lock ownership changed.')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    except BlockingIOError:
        raise Held('This checked source is already being prepared.') from None
    finally:
        os.close(fd)


def _checked(c, identity, *, active=True):
    row = c.execute(select(real_execution_requests).where(real_execution_requests.c.id == identity)).mappings().first()
    if not row:
        raise Held('The checked execution is unavailable.')
    grant = c.execute(select(real_execution_grants).where(real_execution_grants.c.id == row['grant_id'])).mappings().first()
    if not grant:
        raise Held('The original execution grant is unavailable.')
    real_publication._checked(c, row, grant, active=active)
    linked = grant['snapshot']['workflow_task']
    if (not linked['original'].get('feature_planning')
            or not task_execution.current(c, linked)):
        raise Held('A current checked Feature Workshop task is required.')
    # This rereads completion/check/artifact integrity, not only database flags.
    result = real_publication._proof(row, grant)
    return row, grant, linked, result


def _root(policy, linked, execution_id, grant=None):
    if linked['approval']['payload'].get('feature_run'):
        prepared = (grant or {})['snapshot']['task_budget']['offer']['snapshot']['ready_preparation']['root']
        # The original immutable offer owns this location across later releases.
        # A new installation's output directory cannot relocate old evidence.
        return owned(prepared).parent/'feature-inputs'/linked['intake_id']/execution_id
    return Path(policy['output_root'])/'feature-inputs'/linked['intake_id']/execution_id


def _same_plan(value, linked):
    return (value['feature_id'] == linked['intake_id']
        and value['proposal_id'] == linked['task']['proposal_id']
        and value['plan_binding'] == linked['proposal_review']['binding'])


def validate_input(c, descriptor, linked, policy, *, depth=0, active=True):
    """Revalidate every predecessor, including later independent rejection."""
    if depth >= 16 or not isinstance(descriptor, dict):
        raise Held('Invalid or cyclic checked-source ancestry.')
    try:
        row, grant, parent, result = _checked(c, descriptor['execution_id'], active=active)
        root = _root(policy, parent, row['id'], grant)
        path = owned(root/'input.json')
        if path.stat().st_size > 65536 or path.stat().st_mode & 0o077:
            raise Held('Checked-source receipt exceeds its private bound.')
        value = json.loads(path.read_text())
        if (descriptor != dict(source=value['source'], commit=value['commit'],
                execution_id=value['execution_id'], receipt_sha256=digest(value))
                or not _same_plan(value, linked)
                or value['task_id'] != parent['task']['id']
                or value['candidate_id'] != result['candidate_id']
                or value['result_sha256'] != digest(result) or value['fence'] != row['fence']
                or value['integrated_verification'] != 'not_run'):
            raise Held('The checked source, plan or frozen result changed.')
        previous = grant['snapshot'].get('feature_input')
        if previous:
            validate_input(c, previous, linked, policy, depth=depth+1, active=active)
            if grant['snapshot']['contract']['source'] != previous['source'] or grant['snapshot']['contract']['base'] != previous['commit']:
                raise Held('A predecessor did not execute against its checked input.')
        elif grant['snapshot']['contract']['base'] != value['original_base']:
            raise Held('The first feature step did not use the original approved base.')
        if value['predecessor'] != previous:
            raise Held('Checked-source ancestry differs from its execution grant.')
        adapter = LocalCheckedCommit(root, value['assembly'], scratch_root=root)
        adapter.permitted = lambda: True  # Only fixed Git reads below; never prepare during validation.
        source = adapter._owned_checkout()
        if (str(source) != value['source']
                or adapter._call(['git','rev-parse','HEAD'],source) != value['commit']
                or adapter._call(['git','rev-parse','HEAD^'],source) != value['assembly']['base']
                or adapter._call(['git','status','--porcelain=v1','--untracked-files=all'],source)):
            raise Held('The prepared source no longer matches the exact checked patch.')
        changed = adapter._call(['git','diff','--name-only',value['assembly']['base'],'HEAD'],source).splitlines()
        if sorted(changed) != sorted(f['path'] for f in result['files']):
            raise Held('The checked-source paths changed.')
        for item in result['files']:
            expected = (Path(value['assembly']['evidence_root'])/'candidates'/result['candidate_id']/'files'/item['path']).read_bytes()
            if adapter._call(['git','show','HEAD:'+item['path']],source,raw=True) != expected:
                raise Held('The checked-source bytes differ from the frozen result.')
        return value
    except (KeyError, OSError, TypeError, real_publication.DeliveryHeld) as error:
        raise Held('Checked-source evidence is unavailable or changed.') from error


def prepare_input(engine, execution_id):
    """Worker-only bridge; no HTTP ingress, model output paths, or grant issuance."""
    policy = configuration(engine)
    with engine.connect() as c:
        row, grant, linked, result = _checked(c, execution_id)
        original_base = c.execute(select(planning_jobs.c.payload).where(
            planning_jobs.c.intake_id == linked['intake_id'], planning_jobs.c.kind == 'decomposition',
            planning_jobs.c.revision == linked['proposal_review']['revision'])).scalar_one()['feature_base']
        previous = grant['snapshot'].get('feature_input')
        if previous:
            validate_input(c, previous, linked, policy)
            if (grant['snapshot']['contract']['source'] != previous['source']
                    or grant['snapshot']['contract']['base'] != previous['commit']):
                raise Held('This task did not use its checked predecessor source.')
        elif grant['snapshot']['contract']['base'] != original_base:
            raise Held('An initial feature step must use its approved original base.')
    root = _root(policy, linked, row['id'], grant)
    with locked(root):
        def permitted():
            try:
                with engine.connect() as c:
                    current, actual_grant, actual, checked = _checked(c, execution_id)
                    if previous:
                        validate_input(c, previous, actual, policy)
                return current['fence'] == row['fence'] and actual_grant['binding'] == grant['binding'] and checked == result
            except (ValueError, OSError):
                return False
        snapshot = dict(execution_id=row['id'], repository='Twich-Team/TwichUI', base_branch='main',
            branch='maintenance/workbench-'+digest(dict(feature=linked['intake_id'], execution=row['id']))[:20],
            base=grant['snapshot']['contract']['base'], evidence_root=grant['snapshot']['recovery']['root'],
            candidate_id=result['candidate_id'], task_budget=grant['snapshot']['task_budget'],
            preview=dict(title=linked['task']['title'], task_id=linked['task']['id']))
        adapter = LocalCheckedCommit(root, snapshot, scratch_root=root)
        adapter.permitted = permitted
        commit = adapter.prepare(snapshot, result)
        if not permitted():
            raise Held('The feature or checked result changed during source preparation.')
        value = dict(version=1, feature_id=linked['intake_id'], task_id=linked['task']['id'],
            proposal_id=linked['task']['proposal_id'],
            plan_binding=linked['proposal_review']['binding'], original_base=original_base,
            execution_id=row['id'], fence=row['fence'], candidate_id=result['candidate_id'],
            result_sha256=digest(result), predecessor=previous, assembly=snapshot,
            source=str(adapter.workspace), commit=commit, integrated_verification='not_run')
        durable(root/'input.json', json.dumps(value, sort_keys=True).encode())
        (root/'input.json').chmod(0o600)
        return dict(source=value['source'], commit=value['commit'], execution_id=value['execution_id'],
            receipt_sha256=digest(value))


def prerequisite_tasks(c, descriptor, linked, policy, *, active=True):
    """Only tasks whose frozen changes are in this exact source ancestry qualify."""
    validate_input(c, descriptor, linked, policy, active=active)
    identities, visited = set(), set()
    while descriptor:
        if descriptor['execution_id'] in visited or len(visited) >= 16:
            raise Held('Cyclic checked-source ancestry.')
        visited.add(descriptor['execution_id'])
        row, grant, parent, _ = _checked(c, descriptor['execution_id'], active=active)
        value = json.loads(owned(_root(policy, parent, row['id'], grant)/'input.json').read_text())
        identities.add(value['task_id'])
        descriptor = value['predecessor']
    return identities
