"""Isolated CI test partitions and the full, fail-closed release receipt.

Each partition runs serially on its own hosted runner. No shared fixture database,
ports or browser workers. Fast feedback never calls the acceptance command.
"""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {'checks', 'backend', 'browser', 'visual'}
BACKEND_SHARDS = 2
BROWSER_SHARDS = 4


def matrices():
    return {name: {'include': [dict(label=part, shard=f'{part}/{total}')
                              for part in range(1, total + 1)]}
            for name, total in [('backend', BACKEND_SHARDS), ('browser', BROWSER_SHARDS)]}


def backend_files(root):
    return sorted(p.relative_to(root).as_posix() for p in (root / 'backend/tests').rglob('*.py')
                  if p.name.startswith('test_') or p.name.endswith('_test.py'))


def partition(files, shard):
    match = re.fullmatch(r'([1-9][0-9]*)/([1-9][0-9]*)', shard)
    if not match:
        raise ValueError('Use a one-based shard such as1/2.')
    part, total = map(int, match.groups())
    if part > total or total > len(files):
        raise ValueError('Every shard must contain tests and belong to the full partition.')
    return files[part - 1::total]


def acceptance(commit, results, files):
    if not re.fullmatch('[a-f0-9]{40}', commit):
        raise ValueError('Exact commit required for full release acceptance.')
    if set(results) != REQUIRED or any(results[k].get('result') != 'success' for k in REQUIRED):
        raise ValueError('Every required test group must finish successfully; skipped is not acceptance.')
    return dict(version=1, workflow='Verify fixture application', commit=commit,
                full_release_acceptance=True, required_groups=sorted(REQUIRED),
                backend_shards=BACKEND_SHARDS, browser_shards=BROWSER_SHARDS, browser_workers_per_runner=1,
                backend_files=len(files), backend_inventory_sha256=sha256('\n'.join(files).encode()).hexdigest())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    backend = commands.add_parser('backend')
    backend.add_argument('--shard', required=True)
    backend.add_argument('--list', action='store_true')
    commands.add_parser('acceptance')
    commands.add_parser('matrix')
    args = parser.parse_args()
    files = backend_files(ROOT)
    if args.command == 'matrix':
        for name, matrix in matrices().items():
            print(name + '=' + json.dumps(matrix, separators=(',', ':')))
        return 0
    if args.command == 'backend':
        selected = partition(files, args.shard)
        if args.list:
            print(json.dumps(selected))
            return 0
        output = ROOT / 'artifacts/ci'
        output.mkdir(parents=True, exist_ok=True)
        label = args.shard.replace('/', '-of-')
        (output / f'backend-{label}-inventory.json').write_text(json.dumps(
            dict(all_files=files, selected=selected, shard=args.shard), indent=2) + '\n')
        return subprocess.call([sys.executable, '-m', 'pytest', *selected, '--durations=25',
                                '--junitxml=' + str(output / f'backend-{label}.xml')], cwd=ROOT)
    commit = os.environ['GITHUB_SHA']
    actual = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    if actual != commit:
        raise ValueError('Acceptance must bind the exact checked-out commit.')
    receipt = acceptance(commit, json.loads(os.environ['CI_RESULTS']), files)
    from release_bundle import verify
    application = ROOT / 'artifacts/ci/application.tar.gz'
    digest = sha256(application.read_bytes()).hexdigest()
    verify(application, digest, commit)
    receipt.update(application_sha256=digest, application_artifact='application.tar.gz')
    output = ROOT / 'artifacts/ci/full-release-acceptance.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
