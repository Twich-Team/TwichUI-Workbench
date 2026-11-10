"""Bounded check diagnostics and read-only access to this attempt's saved logs."""

import json
import hashlib
import os
import re
from pathlib import Path

from workbench.real_tool_contract import ToolDenied, ToolInputError, ToolBudgetExceeded

LOG_PAGE_BYTES = 2048


class CheckFeedback:
    def __init__(self, root, paths):
        self.root, self.paths = Path(root), tuple(paths)
        self.logs = {}
        self.seen = set()
        self.read_chars = 0

    def record(self, result, check_id):
        # Only the trusted checker supplies IDs and content. Models cannot register logs.
        if not re.fullmatch(r"(?:working-[0-9]{2}-[0-9]{2}|fixed-[0-9]{3})", check_id):
            raise ToolDenied("check_log_denied", "Invalid trusted check identity")
        raw = result.get("out", "")
        if not isinstance(raw, str) or len(raw.encode()) > 65536:
            raise ToolDenied("check_log_denied", "Bounded saved check output required")
        from workbench.real_workspace import durable

        target = self.root / "check-logs" / (check_id + ".json")
        durable(
            target, json.dumps(dict(check_id=check_id, result=result), ensure_ascii=True).encode()
        )
        self.logs[check_id] = (target, hashlib.sha256(target.read_bytes()).hexdigest())
        lines = raw.splitlines()
        failures = [
            (i, line)
            for i, line in enumerate(lines)
            if re.search(
                r"FAILED|AssertionError|assertion failed|Error:|error:|lua5?\.1:|Traceback|expected|actual|WB_LOCAL_CONTEXT",
                line,
                re.I,
            )
        ]
        selected = set(range(max(0, len(lines) - 10), len(lines)))
        for i, _ in failures[-6:]:
            selected.update(range(max(0, i - 1), min(len(lines), i + 3)))
        excerpt = "\n".join(lines[i][:600] for i in sorted(selected))[-2400:]
        if any(line.startswith('WB_LOCAL_CONTEXT ') for line in lines):
            # Local observations supplement the actual error; never let a long
            # variable listing push its failure/stack completely out of view.
            first=next((i for i,line in enumerate(lines) if not line.startswith('WB_LOCAL_CONTEXT ')
                and re.search(r'\.lua:[0-9]+:',line)),0)
            head='\n'.join(lines[first:first+6])[:900]
            excerpt=head+'\n'+excerpt[-(2399-len(head)):]
        hint = None
        for _, line in reversed(failures):
            for path in self.paths:
                found = re.search(
                    r"(?<![\w/])" + re.escape(Path(path).name) + r":([0-9]{1,7})(?::|\b)", line
                )
                if found:
                    n = int(found[1])
                    hint = dict(path=path, start=max(1, n - 4), end=n + 4)
                    break
            if hint:
                break
        feedback = dict(
            status=result["status"],
            rc=result["rc"],
            check_id=check_id,
            diagnostic=excerpt,
            context_hint=hint,
            expected_actual="Included when emitted by the check; otherwise unavailable.",
            log=dict(
                tool="read_check_log", check_id=check_id, lines=len(lines), start=1, max_lines=80
            ),
            truncated=excerpt != "\n".join(lines),
        )
        while len(json.dumps(feedback, ensure_ascii=True).encode()) > 7000:
            feedback["diagnostic"] = feedback["diagnostic"][len(feedback["diagnostic"]) // 2 :]
            feedback["truncated"] = True
        return feedback

    def read(self, args):
        if not isinstance(args, dict) or set(args) - {"check_id", "start", "end", "column"}:
            raise ToolInputError(
                "check_log_input", "Use check_id, start, end and optional column only"
            )
        identity = args.get("check_id")
        if not isinstance(identity, str) or identity not in self.logs:
            raise ToolDenied(
                "check_log_denied", "Only a check_id returned in this attempt is readable"
            )
        start = args.get("start", 1)
        end = args.get("end", start + 39) if type(start) is int else None
        if (
            type(start) is not int
            or type(end) is not int
            or not 1 <= start <= end <= 2147483647
            or end - start >= 80
        ):
            raise ToolInputError(
                "check_log_input",
                "1-based inclusive lines; at most80, start defaults1, end=start+39; EOF clips",
            )
        path, expected_hash = self.logs[identity]
        if (
            path.is_symlink()
            or path.parent.is_symlink()
            or not path.is_file()
            or path.stat().st_size > 524288
            or path.stat().st_uid != os.getuid()
            or hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash
        ):
            raise ToolDenied("check_log_denied", "Saved check log integrity unavailable")
        lines = json.loads(path.read_text())["result"]["out"].splitlines(keepends=True)
        column = args.get("column", 0)
        if type(column) is not int or not 0 <= column <= 65536:
            raise ToolInputError("check_log_input", "column must be an integer from0 to65536")
        output = []
        cost = 0
        next_page = None
        keys = []
        replayed = True
        def page(parts, pointer, remaining=96000, repeated=False):
            return json.dumps(dict(check_id=identity, lines=parts, total_lines=len(lines),
                next_page=pointer, already_shown_or_empty=not parts,
                previously_shown=bool(parts) and repeated,
                note='Exact log text; rereads are free. Continue with next_page when present.',
                remaining_log_chars=remaining), ensure_ascii=True)

        for i in range(start - 1, min(end, len(lines))):
            offset = column if i == start - 1 else 0
            part = lines[i][offset : offset + 700]
            def pointer(length):
                if offset + length < len(lines[i]):
                    return dict(start=i + 1, column=offset + length)
                return dict(start=i + 2, column=0) if i + 1 < len(lines) else None

            # Bound the whole wire reply, including Unicode escaping and metadata.
            # A long line must produce a smaller progressing page, never an empty
            # page with the same cursor. No characters are discarded or invented.
            low, high = 0, len(part)
            while low < high:
                middle = (low + high + 1) // 2
                candidate = output + [dict(line=i + 1, column=offset, text=part[:middle])]
                if len(page(candidate, pointer(middle)).encode()) <= LOG_PAGE_BYTES:
                    low = middle
                else:
                    high = middle - 1
            if not low and part:
                next_page = dict(start=i + 1, column=offset)
                break
            part = part[:low]
            output.append(dict(line=i + 1, column=offset, text=part))
            # Page sizes vary with preceding lines and escaped text. Account for
            # the exact characters delivered, including overlapping rereads.
            delivered = [(identity, i, offset + j) for j in range(len(part))]
            new = sum(key not in self.seen for key in delivered)
            cost += new
            replayed = replayed and not new
            keys.extend(delivered)
            if offset + len(part) < len(lines[i]):
                next_page = dict(start=i + 1, column=offset + len(part))
                break
            next_page = dict(start=i + 2, column=0) if i + 1 < len(lines) else None
        if self.read_chars + cost > 96000:
            raise ToolBudgetExceeded("check_log_budget", "96000 check-log characters consumed")
        self.read_chars += cost
        self.seen.update(keys)
        return page(output, next_page, 96000 - self.read_chars, replayed)


class RepairFeedback:
    """Keep an unchanged rejected workspace from cycling through the same evidence."""

    def __init__(self, runtime):
        self.runtime = runtime
        self.content = self.writable_content()
        self.seen = set()
        self.edit_required = False
        self.rejected = {}

    def writable_content(self):
        return tuple(hashlib.sha256((self.runtime.workspace / path).read_bytes()).digest()
                     for path in self.runtime.paths)

    def edited(self):
        content = self.writable_content()
        if content == self.content:
            self.edit_required |= bool(getattr(self.runtime, "check_failure", None))
            return False
        self.content = content
        self.seen.clear()
        self.edit_required = False
        self.runtime.check_failure = None
        return True

    def observe(self, key, *, empty=False):
        if getattr(self.runtime, "check_failure", None) and (empty or key in self.seen):
            self.edit_required = True
        self.seen.add(key)

    def checked(self, name, feedback):
        if feedback["status"] == "fail":
            key = (self.writable_content(), name, feedback.get('diagnostic'), feedback.get('reason'))
            self.rejected[key] = self.rejected.get(key, 0) + 1
            if self.rejected[key] >= 3:
                raise ToolBudgetExceeded('rejected_workspace_cycle',
                    'Returned to the same previously rejected workspace and failure three times. '
                    'Saved edits and diagnostics require a changed strategy, not unchanged replay.')
            if self.rejected[key] == 2:
                feedback = {**feedback, 'previously_rejected_workspace': True,
                    'strategy_guidance': 'This exact workspace already failed this check, even if other edits occurred between checks. '
                        'Inspect the failing test setup and actual local observations; distinguish a defect in your new test from a production defect. '
                        'Do not cycle back to the same replacement. Existing original assertions must remain intact.'}
            self.runtime.check_failure = dict(
                name=feedback.get("failed_check_name", name),
                diagnostic=feedback.get("diagnostic") or feedback.get("reason", "Check failed."),
                context_hint=feedback.get("context_hint"),
            )
            feedback = {**feedback, "next_step": (
                "Use the failure location and previously read source to make a scoped replacement, "
                "then recheck. Read a focused source window if needed. Checking unchanged code "
                "or reopening an exhausted log cannot repair the failure."
            )}
        self.observe(("check", name, feedback["status"], feedback.get("diagnostic")))
        return feedback
