"""Noninteractive project commands with existing host tool discovery."""
import argparse
import subprocess
import sys
from project_env import ARTIFACT_LIMIT, ROOT, artifact_bytes, environment

RECOVERY = {'recover-preparation', 'run-preparation-recovery'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['dev', 'check', 'test', 'test-e2e', 'test-visual', 'verify',
                                           'prepare-offer', 'recover-preparation', 'run-preparation-recovery'])
    parser.add_argument('--disposable', action='store_true', help='Temporary fixture DB for dev')
    parser.add_argument("--built", action="store_true", help="Dev: serve built frontend through loopback API")
    parser.add_argument('--review', action='store_true', help='Dev: isolated built preview on allowed port 9187')
    parser.add_argument('--grep', help='Browser/visual tests only: select Playwright test titles while retaining evidence budgets')
    parser.add_argument('--database', help='Preparation only: existing local SQLite store')
    parser.add_argument('--intake-id')
    parser.add_argument('--task-id')
    parser.add_argument('--scope-approval-id')
    parser.add_argument('--evidence-root', help='Preparation only: existing independently certified Runtime root')
    parser.add_argument('--job-id', help='Recovery only: full ID of the original preparation job')
    parser.add_argument('--review-binding', help='Recovery authorization only: exact approved proposal binding')
    args = parser.parse_args()
    if artifact_bytes() >= ARTIFACT_LIMIT:
        print('BUDGET_EXHAUSTED: local evidence exceeds 512 MiB. Archive it explicitly before retrying.')
        return 4
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
        if artifact_bytes() > ARTIFACT_LIMIT:
            print('BUDGET_EXHAUSTED: evidence exceeded 512 MiB; no files were deleted.')
            return 4
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
        if artifact_bytes() > ARTIFACT_LIMIT:
            print('BUDGET_EXHAUSTED: evidence exceeded 512 MiB; no files were deleted.')
            return 4
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
    result = subprocess.run(command, cwd=ROOT, env=environment(), check=False)
    if artifact_bytes() > ARTIFACT_LIMIT:
        print('BUDGET_EXHAUSTED: evidence exceeded 512 MiB; no files were deleted.')
        return 4
    return result.returncode


if __name__ == '__main__':
    sys.exit(main())
