"""Bounded committed-code excerpts; compile for source ranges, never execute Lua."""
from functools import lru_cache
import math
import re
import subprocess

from workbench.grounded_scenarios import LITERALS

TEXT_BUDGET = 9000


def without_comments(text):
    return LITERALS.sub(lambda m: re.sub(r'[^\n]', ' ', m[0]) if m[0].startswith('--') else m[0], text)


@lru_cache(maxsize=64)
def compiled_shape(raw):
    # luac parses/compiles only (-p): no libraries, source execution or output
    # chunk. Input and time are bounded. Disassembly is private and discarded.
    if len(raw) > 131072 or raw.startswith(b'\x1b'):
        return (), None
    try:
        run = subprocess.run(['/usr/bin/luac5.1', '-p', '-l', '-'], input=raw,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=2,
            env={'PATH': '/usr/bin:/bin', 'LANG': 'C'})
    except (OSError, subprocess.TimeoutExpired):
        return (), None
    if run.returncode or len(run.stdout) > 8 << 20:
        return (), None
    ranges = tuple((int(a), int(b)) for a, b in re.findall(
        rb'^function <stdin:(\d+),(\d+)>', run.stdout, re.M))
    main = run.stdout.split(b'\nfunction <stdin:', 1)[0]
    returns = any(int(count) != 1 for count in re.findall(rb'\bRETURN\s+\d+\s+(\d+)\b', main))
    return ranges, returns


def function_ranges(raw):
    return compiled_shape(raw)[0]


def excerpts(files, inspected, terms, words):
    """Prefer actual definitions/call context over keyword-rich header comments.

    Every excerpt is an exact contiguous range of committed lines. A partial
    function is labelled as partial, rather than represented as its full body.
    Existing hashes/full file inspection remain in the authoritative packet.
    """
    frequency = {term: sum(term in words(without_comments('\n'.join(inspected[item['path']][2])))
                           for item in files) for term in terms}
    weight = {term: math.log(1+len(files)/(1+frequency[term])) for term in terms}
    candidates = {}
    functions = {}
    references = set()
    for item in files:
        path = item['path']
        _, raw, lines = inspected[path]
        code = without_comments('\n'.join(lines)).splitlines()
        spans = function_ranges(raw)
        if path.startswith(('!!!TwichUI/', 'TwichUI_Settings/')):
            item['top_level_returns_value'] = compiled_shape(raw)[1]
        rows = []
        for start, end in spans:
            if not 1 <= start <= end <= len(lines):
                continue
            body = '\n'.join(code[start-1:end])
            header = code[start-1]
            hits = words(body) & terms
            names = words(header) & terms
            score = (6*sum(weight[t] for t in sorted(names)) + 2*sum(weight[t] for t in sorted(hits))) / math.sqrt(1+(end-start)/40)
            rows.append((score, start, end, 'complete function'))
            match = re.search(r'function\s+([\w.:]+)', header)
            if match:
                functions[(path, start)] = match[1].split('.')[-1].split(':')[-1]
            if score:
                references.update(re.findall(r'\b([A-Za-z_]\w*)\s*\(', body))
        # Keep useful top-level calls, assignments and test assertions too.
        # Their code score excludes comments, but returned source keeps context.
        for start in range(0, len(lines), 12):
            body = '\n'.join(code[start:start+12])
            if body.strip():
                score = sum(weight[t] for t in sorted(words(body) & terms))
                rows.append((score, start+1, min(len(lines), start+18), 'code window'))
        candidates[path] = rows
    budgets = {}
    for item in files:
        budgets[item['path']] = (2400 if item.get('client_bootstrap') else
            350 if item.get('support_only') else 850 if item['path'].startswith('tests/') else 2400)
    scale = min(1, TEXT_BUDGET / max(1, sum(budgets.values())))
    for item in files:
        path = item['path']
        lines = inspected[path][2]
        budget = int(budgets[path]*scale)
        rows = candidates[path]
        # Follow literal calls in relevant functions within the inspected set.
        rows = [(score + (3 if functions.get((path, start)) in references else 0), start, end, kind)
                for score, start, end, kind in rows]
        rows.sort(key=lambda row: (-row[0], row[3] != 'complete function', row[1]))
        chosen, covered = [], set()

        def add(start, end, kind, allowance):
            nonlocal budget
            if start > end or set(range(start, end+1)) <= covered:
                return
            picked = []
            for line in lines[start-1:end]:
                proposed = '\n'.join([*picked, line])
                if len(proposed) > min(budget, allowance):
                    break
                picked.append(line)
            if not picked:
                return
            actual_end = start+len(picked)-1
            chosen.append(dict(line=start, text='\n'.join(picked),
                coverage=kind if actual_end == end else 'partial '+kind.removeprefix('complete '),
                through_line=actual_end, requested_through_line=end))
            covered.update(range(start, actual_end+1))
            budget -= len(chosen[-1]['text'])

        setup = item.get('client_bootstrap')
        if path.startswith(('!!!TwichUI/', 'TwichUI_Settings/')):
            first = next((i for i, line in enumerate(without_comments('\n'.join(lines)).splitlines(), 1)
                          if line.strip()), 1)
            add(first, min(len(lines), first+12), 'module prelude', min(500, budget//3))
        if setup:
            end = setup['through_line']
            # The exact committed full prefix remains independently compiled by
            # bug_evidence. Show its actual final loads, not opening comments.
            start = end
            allowance = budget*2//3
            while start > max(1, end-35) and len('\n'.join(lines[start-2:end])) <= allowance:
                start -= 1
            add(start, end, 'committed bootstrap tail', allowance)
        for score, start, end, kind in rows:
            if score <= 0 or len(chosen) >= 3:
                continue
            if len(chosen) == 0 or kind == 'complete function':
                add(start, end, kind, budget)
        if not chosen:
            start = next((i for i, line in enumerate(without_comments('\n'.join(lines)).splitlines(), 1)
                          if line.strip()), 1)
            add(start, min(len(lines), start+20), 'code prelude', budget)
        item['excerpts'] = chosen
        item['file_lines'] = len(lines)
