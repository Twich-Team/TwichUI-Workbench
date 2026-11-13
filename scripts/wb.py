"""Noninteractive project commands with existing host tool discovery."""
import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sys
from managed_storage import allocate, close, maintain as retain_managed
from project_env import ROOT, environment
from evidence_retention import maintain

RECOVERY = {'recover-preparation', 'run-preparation-recovery'}
TEST_CACHE = Path.home() / '.cache'


def test_environment():
    env = environment()
    if not env.get('TMPDIR'):
        # Keep paths short enough for real AF_UNIX connector tests. This is a
        # private per-checkout cache on the home filesystem, not global /tmp.
        scratch = TEST_CACHE / ('wbt-' + hashlib.sha256(str(ROOT).encode()).hexdigest()[:8])
        if TEST_CACHE.is_symlink() or scratch.is_symlink():
            raise RuntimeError('Tests require a private owned temporary directory.')
        scratch.mkdir(parents=True, exist_ok=True, mode=0o700)
        info = scratch.lstat()
        if (scratch.resolve() != scratch or not scratch.is_dir()
                or info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise RuntimeError('Tests require a private owned temporary directory.')
        unit = allocate(scratch, 'test-scratch')
        env['TMPDIR'] = str(unit)
        env['WORKBENCH_TEST_STORAGE_UNIT'] = str(unit)
    else:
        # A nested command owns neither its caller's unit nor explicit TMPDIR.
        env.pop('WORKBENCH_TEST_STORAGE_UNIT', None)
    info = os.statvfs(Path(env['TMPDIR']))
    if info.f_files and info.f_favail < 65536:
        raise RuntimeError('Test temporary storage is short of file entries. Preserve evidence and reclaim closed owned scratch before another run.')
    return env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['dev', 'check', 'test', 'test-e2e', 'test-visual', 'verify', 'retention',
                                           'prepare-offer', 'recover-preparation', 'run-preparation-recovery'])
    parser.add_argument('--disposable', action='store_true', help='Temporary fixture DB for dev')
    parser.add_argument("--built", action="store_true", help="Dev: serve built frontend through loopback API")
    parser.add_argument('--review', action='store_true', help='Dev: isolated built preview on allowed port 9187')
    parser.add_argument('--grep', help='Browser/visual tests only: select Playwright test titles while retaining evidence budgets')
    parser.add_argument('--database', help='Existing SQLite store for preparation or retention reference checks')
    parser.add_argument('--preview', action='store_true', help='Retention: show candidates without changing data')
    parser.add_argument('--pool', action='append', type=Path, help='Retention: explicit managed allocation pool')
    parser.add_argument('--intake-id')
    parser.add_argument('--task-id')
    parser.add_argument('--scope-approval-id')
    parser.add_argument('--evidence-root', help='Preparation only: existing independently certified Runtime root')
    parser.add_argument('--job-id', help='Recovery only: full ID of the original preparation job')
    parser.add_argument('--review-binding', help='Recovery authorization only: exact approved proposal binding')
    args = parser.parse_args()
    if args.command == 'retention':
        if any((args.disposable, args.built, args.review, args.grep, args.intake_id, args.task_id,
                args.scope_approval_id, args.evidence_root, args.job_id, args.review_binding)):
            parser.error('Retention accepts only --pool, --preview and optional --database')
        pools = args.pool or [TEST_CACHE / ('wbt-' + hashlib.sha256(str(ROOT).encode()).hexdigest()[:8])]
        import json
        print(json.dumps(retain_managed(pools, databases=[args.database] if args.database else [], preview=args.preview), indent=2))
        return 0
    if args.preview or args.pool:
        parser.error('--preview and --pool require retention')
    maintain()
    retain_managed([TEST_CACHE / ('wbt-' + hashlib.sha256(str(ROOT).encode()).hexdigest()[:8])])
    preparation_args = [args.database, args.intake_id, args.task_id, args.scope_approval_id, args.evidence_root]
    if args.command in RECOVERY:
        authorize = args.command == 'recover-preparation'
        if (not args.database or not args.job_id or authorize != (args.review_binding is not None)
                or any(preparation_args[1:]) or args.disposable or args.built or args.review or args.grep is not None):
            parser.error('Recovery requires --database and --job-id, and --review-binding only for recover-preparation')
        env = environment()
        env['WORKBENCH_DB'] = args.database
        env['PYTHONPATH'] += ':' + str(ROOT / 'scripts')
        command = [str(ROOT / '.venv/bin/python'), '-m', 'workbench.worker', '--preparation-recovery', args.job_id]
        if authorize:
            command += ['--authorize-review-binding', args.review_binding]
        result = subprocess.run(command, cwd=ROOT, env=env, check=False)
        maintain()
        return result.returncode
    if args.job_id is not None or args.review_binding is not None:
        parser.error('--job-id and --review-binding require a preparation recovery command')
    if args.command == 'prepare-offer':
        if not all(preparation_args) or args.disposable or args.built or args.review or args.grep:
            parser.error('prepare-offer requires --database, --intake-id, --task-id, --scope-approval-id and --evidence-root only')
        # Use the pinned project interpreter; no npm, checks, migration or activation.
        command = [str(ROOT / '.venv/bin/python'), '-m', 'workbench.trusted_preparation',
                   *preparation_args]
        result = subprocess.run(command, cwd=ROOT, env=environment(), check=False)
        maintain()
        return result.returncode
    if any(preparation_args):
        parser.error('Preparation arguments require prepare-offer')
    if args.grep is not None and (args.command not in {'test-e2e', 'test-visual'} or len(args.grep) > 240):
        parser.error('--grep requires test-e2e or test-visual and at most 240 characters')
    if args.review and (args.command != 'dev' or not args.built or not args.disposable):
        parser.error('--review requires dev --built --disposable')
    command = ['npm', 'test'] if args.command == 'test' else ['npm', 'run', args.command]
    if args.grep is not None:
        command += ['--', '--grep', args.grep]
    if args.disposable:
        if args.command != 'dev':
            parser.error('--disposable applies only to dev; tests are always disposable')
        command += ['--', '--disposable']
    if args.built:
        if args.command != "dev":
            parser.error("--built applies only to dev")
        if not args.disposable:
            parser.error("--built requires --disposable for isolated review")
        command += ["--built"]
    if args.review:
        command += ['--review']
    env = test_environment() if args.command in {'test', 'test-e2e', 'test-visual', 'verify'} else environment()
    result = subprocess.run(command, cwd=ROOT, env=env, check=False)
    if 'WORKBENCH_TEST_STORAGE_UNIT' in env:
        try:
            close(Path(env['WORKBENCH_TEST_STORAGE_UNIT']), result.returncode == 0)
        except (OSError, ValueError):
            # Failed closeout cannot turn scratch into disposable data.
            print('Test scratch retained: closeout could not safely certify it.', file=sys.stderr)
    maintain()
    return result.returncode


if __name__ == '__main__':
    sys.exit(main())
