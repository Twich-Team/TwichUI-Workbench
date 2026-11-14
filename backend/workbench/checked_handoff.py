"""Versioned native checked-result bridge; no execution or publication authority.

An explicit operator qualification reads the original completion under its sealed
runtime, without writes or network. Consumption pins that provenance and every
authoritative evidence byte. Current authorization stays with real_publication.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import UUID, uuid4

from workbench.real_workspace import durable, sha
from workbench.visual_scope import digest

ROOT = Path(__file__).resolve().parents[2]
VERSION = 'native-checked-result-v1'


def private(path, *, directory=False):
    path = Path(path)
    if (path.resolve() != path or path.is_symlink()
            or not (path.is_dir() if directory else path.is_file())
            or path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077):
        raise ValueError('Owned private checked-result artifact required')
    return path


def receipt_path(identity):
    return ROOT.parent / 'artifacts/checked-results' / (str(UUID(identity)) + '.json')


def source_seal(release, expected):
    """Check ALL historical helpers before importing any historical code."""
    release = Path(release)
    if release.resolve() != release or not release.is_dir():
        raise ValueError('Original sealed runtime required')
    helpers = release / 'backend/workbench'
    if (any(p.name not in {'workbench', 'tests', '__pycache__'} for p in helpers.parent.iterdir())
            or any(p.name not in {*expected['helper_sha256'], '__pycache__', 'real_checks'} for p in helpers.iterdir())):
        raise ValueError('Unsealed historical import artifact')
    if {p.name for p in helpers.glob('*.py')} != set(expected['helper_sha256']):
        raise ValueError('Historical verification source inventory changed')
    for base, entries in ((helpers, expected['helper_sha256']),
                          (release / 'scripts', expected['connector_sha256'])):
        for name, checksum in entries.items():
            path = base / name
            if (Path(name).name != name or path.resolve() != path
                    or not path.is_file() or sha(path.read_bytes()) != checksum):
                raise ValueError('Historical verification source seal changed')


def evidence_seal(runtime, row, grant):
    result = row['result']
    candidate = result['candidate_id']
    if len(candidate) != 64 or any(c not in '0123456789abcdef' for c in candidate):
        raise ValueError('Invalid checked candidate')
    names = ['native-task.json', 'base-manifest.json', 'native-baseline.json',
             'workspace-location.json', 'native-provider-result.json',
             'completion-' + str(UUID(row['id'])) + '.json',
             *['native-check-' + str(i) + '.json' for i in range(3)]]
    frozen = 'candidates/' + candidate + '/'
    names += [frozen + name for name in ('manifest.json', 'result.json', 'changes.patch')]
    names += [frozen + 'files/' + e['path'] for e in result['files']]
    seals = {}
    for name in names:
        path = runtime.root / name
        if (path.resolve() != path or not path.is_relative_to(runtime.root)
                or not path.is_file() or path.stat().st_size > 4 << 20):
            raise ValueError('Historical verification artifact missing or unsafe')
        seals[name] = sha(path.read_bytes())
    if not runtime.owns_recovery(grant) or not runtime.verify_frozen(result):
        raise ValueError('Historical candidate integrity failed')
    return seals


def binding(row, grant):
    return dict(execution_id=row['id'], task_id=grant['snapshot']['native_task']['task_id'],
                fence=row['fence'], grant_id=grant['id'], grant_binding=grant['binding'],
                candidate_id=row['result']['candidate_id'], result_digest=digest(row['result']),
                runtime=grant['snapshot']['runtime'], recovery=grant['snapshot']['recovery'],
                historical_intake_id=grant['snapshot']['native_task'].get('historical_intake_id'))


def history(c, grant):
    """Retain original linked attempts as provenance, never promote their outcome."""
    from sqlalchemy import select
    from workbench.db import intakes, real_execution_grants, real_execution_requests
    linked = grant['snapshot']['native_task'].get('historical_intake_id')
    records, seen = [], set()
    while linked:
        if linked in seen or len(seen) >= 32:
            raise ValueError('Invalid historical native task chain')
        seen.add(linked)
        task = c.execute(select(intakes).where(intakes.c.id == linked)).mappings().one()
        attempts = c.execute(select(real_execution_requests).join(real_execution_grants,
            real_execution_requests.c.grant_id == real_execution_grants.c.id).where(
                real_execution_grants.c.snapshot['native_task']['task_id'].as_string() == linked)).mappings().all()
        records.append(dict(task_id=linked, task_digest=digest(dict(task)),
            attempts=[dict(execution_id=r['id'], status=r['status'],
                           record_digest=digest(dict(r))) for r in attempts]))
        linked = (task['payload'].get('native_task') or {}).get('historical_intake_id')
    return records


def historical_completion(release, row, grant):
    """Only sealed historical code runs; readonly mount and no network/credentials.

    -I and -B prevent user imports and stale bytecode. The frozen Runtime performs
    its original full identity equality and immutable-completion checks unchanged.
    """
    source_seal(release, grant['snapshot']['runtime'])
    program = '''import json,sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from workbench.native_agent import Runtime
row,grant=json.load(sys.stdin)
r=Runtime(Path(grant['snapshot']['recovery']['root']))
assert r.identity()==grant['snapshot']['runtime'], 'Historical runtime identity changed'
completed=r.recover_completion(row,grant)
assert completed and completed['result']==row['result'], 'Original completion integrity failed'
print(json.dumps(completed,sort_keys=True))
'''
    process = subprocess.run(['/usr/bin/bwrap', '--die-with-parent', '--unshare-net',
        '--ro-bind', '/', '/', '--dev', '/dev', '--proc', '/proc', '--',
        sys.executable, '-I', '-B', '-X', 'pycache_prefix=/nonexistent/checked-result-bytecode',
        '-c', program, str(Path(release) / 'backend')],
        input=json.dumps([dict(row), dict(grant)]), text=True, capture_output=True,
        timeout=30, cwd='/', env={'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent',
                                  'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1'})
    if process.returncode or len(process.stdout) > 4 << 20:
        raise ValueError('Original frozen runtime refused verification evidence: ' + process.stderr[-1000:])
    source_seal(release, grant['snapshot']['runtime'])
    completed = json.loads(process.stdout)
    if completed['result'] != row['result']:
        raise ValueError('Original checked result changed')
    return completed


def qualify(engine, identity, release, owner_instruction):
    from workbench import native_agent, real_publication
    with engine.connect() as c:
        row, grant = real_publication._load(c, identity)
        if not native_agent.native(grant['snapshot']):
            raise ValueError('Native checked result required')
        # Qualification is read-only and can inspect a paused historical task.
        # Actual current authority (including pause/revocation) gates publication.
        real_publication._checked(c, row, grant, active=False)
        provenance = history(c, grant)
    runtime = native_agent.Runtime(Path(grant['snapshot']['recovery']['root']))
    before = evidence_seal(runtime, row, grant)
    completed = historical_completion(release, row, grant)
    if evidence_seal(runtime, row, grant) != before:
        raise ValueError('Historical evidence changed during qualification')
    receipt = dict(version=VERSION, binding=binding(row, grant),
        historical_release=str(Path(release)), evidence=before,
        historical_attempts=provenance,
        completed_at=completed['completed_at'],
        qualification_authority_sha256=sha(owner_instruction.encode()))
    target = receipt_path(identity)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    private(target.parent, directory=True)
    data = json.dumps(receipt, sort_keys=True).encode()
    staging = target.with_suffix('.' + uuid4().hex + '.pending')
    durable(staging, data)
    staging.chmod(0o600)
    try:
        os.link(staging, target)
    except FileExistsError:
        if private(target).read_bytes() != data:
            raise ValueError('Conflicting immutable checked-result receipt') from None
    finally:
        staging.unlink()
    descriptor = os.open(target.parent, os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return receipt


def load(runtime, row, grant):
    target = private(receipt_path(row['id']))
    private(target.parent, directory=True)
    if target.stat().st_size > 1 << 20:
        raise ValueError('Checked-result receipt exceeds bound')
    receipt = json.loads(target.read_text())
    if (set(receipt) != {'version', 'binding', 'historical_release', 'evidence',
                         'completed_at', 'qualification_authority_sha256', 'historical_attempts'}
            or receipt['version'] != VERSION or receipt['binding'] != binding(row, grant)
            or digest(grant['snapshot']) != grant['binding']):
        raise ValueError('Checked-result receipt identity changed')
    source_seal(receipt['historical_release'], grant['snapshot']['runtime'])
    if receipt['evidence'] != evidence_seal(runtime, row, grant):
        raise ValueError('Historical verification evidence changed')
    return dict(result=row['result'], completed_at=receipt['completed_at'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execution', required=True)
    parser.add_argument('--historical-release', type=Path, required=True)
    parser.add_argument('--owner-instruction', type=Path, required=True)
    args = parser.parse_args()
    from workbench.db import engine_for
    receipt = qualify(engine_for(), args.execution, args.historical_release,
                      args.owner_instruction.read_text())
    print(json.dumps(receipt, sort_keys=True))


if __name__ == '__main__':
    main()
