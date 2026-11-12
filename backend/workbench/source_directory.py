"""Bounded committed-file navigation; names do not grant read or write authority."""
import json


def directory(base, permitted=None):
    from workbench import bug_source_planning as inspection
    from workbench import bug_investigation as bug, regression_preparation as source

    root, actual, _ = source.repository()
    if actual != base:
        raise ValueError('The committed project changed during file discovery.')
    bug.clean_base(root, base)
    allowed = None if permitted is None else set(permitted)
    roots = sorted(allowed) if allowed is not None else inspection.ROOTS
    # An empty external permission is empty, never a request for the whole tree.
    inventory = source.git(root, 'ls-tree', '-rz', '-l', base, '--', *roots, max_bytes=1 << 20) if roots else b''
    groups = {}
    for entry in inventory.split(b'\0'):
        if not entry:
            continue
        metadata, encoded_path = entry.split(b'\t', 1)
        mode, kind, _, size = metadata.decode().split()
        path = encoded_path.decode()
        if (mode not in {'100644', '100755'} or kind != 'blob' or int(size) > 131072
                or not inspection.INSPECT_PATH.fullmatch(path)
                or any(p.startswith('.') for p in path.split('/'))
                or {'lib', 'libs', 'vendor'} & {p.lower() for p in path.split('/')}
                or allowed is not None and path not in allowed):
            continue
        parent, name = path.rsplit('/', 1)
        groups.setdefault(parent, []).append(name)
    groups = {parent: sorted(names) for parent, names in sorted(groups.items())}
    count = sum(map(len, groups.values()))
    if len(json.dumps(groups, separators=(',', ':')).encode()) > 32768:
        return dict(base=base, complete=False, files_count=count, directories={},
                    note='The file directory exceeds one bounded view. Text search and explicit source reads remain available; no absence may be inferred.')
    return dict(base=base, complete=True, files_count=count, directories=groups,
        note='Read-only navigation metadata, not inspected code or repair permission. Join directory and filename with /. '
             'Read the relevant implementation and settings/UI pages, then their tests; the initial excerpts are only a starting selection. '
             'File discovery does not authorize editing, execution, publication or external disclosure.',
        disclosure_scope='Only the existing externally permitted paths.' if allowed is not None
                         else 'Eligible committed Lua, Python, test/generator scripts and PNG names. PNG reads return controlled metadata only. No source body is supplied here.')
