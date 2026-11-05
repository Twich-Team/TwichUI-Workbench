"""Noninteractive project commands with existing host tool discovery."""
import argparse
import subprocess
import sys
from project_env import ARTIFACT_LIMIT, ROOT, artifact_bytes, environment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['dev', 'check', 'test', 'test-e2e', 'test-visual', 'verify'])
    parser.add_argument('--disposable', action='store_true', help='Temporary fixture DB for dev')
    parser.add_argument("--built", action="store_true", help="Dev: serve built frontend through loopback API")
    parser.add_argument('--review', action='store_true', help='Dev: isolated built preview on allowed port 9187')
    parser.add_argument('--grep', help='Browser/visual tests only: select Playwright test titles while retaining evidence budgets')
    args = parser.parse_args()
    if args.grep is not None and (args.command not in {'test-e2e', 'test-visual'} or len(args.grep) > 240):
        parser.error('--grep requires test-e2e or test-visual and at most 240 characters')
    if args.review and (args.command != 'dev' or not args.built or not args.disposable):
        parser.error('--review requires dev --built --disposable')
    if artifact_bytes() >= ARTIFACT_LIMIT:
        print('BUDGET_EXHAUSTED: local evidence exceeds 512 MiB. Archive it explicitly before retrying.')
        return 4
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
