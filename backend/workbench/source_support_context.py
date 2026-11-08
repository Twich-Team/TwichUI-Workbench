"""Discover literal committed test support; read context never becomes write scope."""
import re

from workbench.grounded_scenarios import LITERALS

IMPORT = re.compile(r'''^[ \t]*dofile\s*\(\s*TESTS\s*\.\.\s*(["'])([A-Za-z0-9_./-]+\.lua)\1\s*\)''', re.M)
MAX_FILES = 3
MAX_BYTES = 24576


def imports(text):
    # Keep quoted loader arguments, remove comments/long strings. Anchoring
    # prevents a quoted string mentioning dofile from becoming a real import.
    screened = LITERALS.sub(lambda m: re.sub(r'[^\n]', ' ', m[0])
                           if m[0].startswith(('--', '[')) else m[0], text)
    protected = [(m.start(),m.end()) for m in LITERALS.finditer(text)]
    result = []
    for match in IMPORT.finditer(screened):
        if any(start <= match.start() < end for start,end in protected):
            continue
        path = 'tests/' + match[2]
        if any(part.startswith('.') or not part for part in path.split('/')):
            continue
        if path not in result:
            result.append(path)
    return result


def add(files, inspected, base):
    """Only literal dependencies of selected committed tests, bounded and inert."""
    from workbench.bug_source_planning import committed

    seen = {f['path'] for f in files}
    candidates = {}
    for item in files:
        if item['path'].startswith('tests/test_'):
            text = '\n'.join(inspected[item['path']][2])
            for path in imports(text):
                if path not in seen:
                    candidates.setdefault(path, []).append(item['path'])
    added, used = [], 0
    for path, parents in sorted(candidates.items()):
        if len(added) == MAX_FILES or len(files) + len(added) == 12:
            break
        try:
            raw, item = committed(path, base)
        except ValueError:
            continue  # No invented source or dynamic/absent dependency claim.
        if len(raw) > 16384 or used + len(raw) > MAX_BYTES:
            continue
        used += len(raw)
        lines = raw.decode().splitlines()
        excerpt = []
        for line in lines[:24]:
            if len('\n'.join([*excerpt, line])) > 1600:
                break
            excerpt.append(line)
        item['excerpts'] = [dict(line=1, through_line=len(excerpt), text='\n'.join(excerpt),
                                 coverage='committed test-support prelude')] if excerpt else []
        item['support_dependency'] = dict(imported_by=parents, read_only=True,
            note='Literal committed test dependency. Inspect needed definitions; this does not authorize an edit or prove runtime accessibility.')
        added.append(item)
    files.extend(added)
    return files
