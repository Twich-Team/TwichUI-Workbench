"""Bounded check diagnostics and read-only access to this attempt's saved logs."""

import json
import hashlib
import os
import re
from pathlib import Path

from workbench.real_tool_contract import ToolDenied, ToolInputError, ToolBudgetExceeded


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
                r"FAILED|AssertionError|assertion failed|Error:|error:|lua5?\.1:|Traceback|expected|actual",
                line,
                re.I,
            )
        ]
        selected = set(range(max(0, len(lines) - 10), len(lines)))
        for i, _ in failures[-6:]:
            selected.update(range(max(0, i - 1), min(len(lines), i + 3)))
        excerpt = "\n".join(lines[i][:600] for i in sorted(selected))[-2400:]
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
            truncated=len(excerpt) < len(raw),
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
        for i in range(start - 1, min(end, len(lines))):
            offset = column if i == start - 1 else 0
            key = (identity, i, offset)
            if key in self.seen:
                continue
            part = lines[i][offset : offset + 700]
            candidate = output + [dict(line=i + 1, column=offset, text=part)]
            if len(json.dumps(candidate, ensure_ascii=True).encode()) > 5800:
                next_page = dict(start=i + 1, column=offset)
                break
            output = candidate
            cost += len(part)
            keys.append(key)
            if offset + len(part) < len(lines[i]):
                next_page = dict(start=i + 1, column=offset + len(part))
                break
            next_page = dict(start=i + 2, column=0) if i + 1 < len(lines) else None
        if self.read_chars + cost > 96000:
            raise ToolBudgetExceeded("check_log_budget", "96000 check-log characters consumed")
        self.read_chars += cost
        self.seen.update(keys)
        return json.dumps(
            dict(
                check_id=identity,
                lines=output,
                total_lines=len(lines),
                next_page=next_page,
                already_shown_or_empty=not output,
                note="Previously returned log segments are omitted; use earlier messages. Continue with next_page when present.",
                remaining_log_chars=96000 - self.read_chars,
            ),
            ensure_ascii=True,
        )
