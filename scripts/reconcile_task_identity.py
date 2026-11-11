"""Apply reviewed provenance links to an existing store, or export/reverse them.

No schema initialization, provider work, source editing or execution authority.
Source receipt hashes must match before applying a historical manifest.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from workbench import task_identity
from workbench.db import engine_for


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['apply', 'reverse'])
    parser.add_argument('--database', required=True, type=Path)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--evidence-root', required=True, type=Path)
    args = parser.parse_args()
    if not args.database.is_file() or args.database.is_symlink():
        parser.error('Use an existing owned database.')
    manifest = json.loads(args.manifest.read_text())
    for member in manifest['members']:
        for reference in member['evidence']:
            path = args.evidence_root / reference['source']
            if (path.resolve() != path or not path.is_relative_to(args.evidence_root)
                    or not path.is_file() or path.stat().st_size > 4 << 20
                    or sha256(path.read_bytes()).hexdigest() != reference['sha256']):
                raise ValueError('The original source receipt is unavailable or changed.')
    engine = engine_for(args.database)
    if args.action == 'apply':
        print(json.dumps(dict(linked=task_identity.reconcile(engine, manifest), canonical_id=manifest['canonical_id'])))
    else:
        print(json.dumps(dict(reversed_links=task_identity.reverse(engine, manifest))))
    engine.dispose()


if __name__ == '__main__':
    main()
