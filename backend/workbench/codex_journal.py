"""Private bounded connector receipts; never a task approval or replay authority."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from uuid import uuid4

MAX_BYTES = 65536


class Journal:
    def __init__(self, root, request):
        root = Path(root)
        if root.resolve() != root:
            raise ValueError("Connector evidence path must be direct")
        root.mkdir(mode=0o700, exist_ok=True)
        info = root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("Connector evidence must be private and owned")
        self.identifier = str(uuid4())
        self.path = root / (self.identifier + ".jsonl")
        self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        self.used = self.count = 0
        self.record("request", request_sha256=hashlib.sha256(request).hexdigest())

    def record(self, phase, **values):
        allowed = {
            "request_sha256",
            "thread_id",
            "turn_id",
            "status",
            "usage",
            "partial",
            "output_bytes",
            "output_sha256",
            "output_excerpt",
            "reason",
            "acknowledged",
            "guard",
            "wire_bytes",
            "terminal_status",
            "capabilities",
        }
        if set(values) - allowed or not re.fullmatch(r"[a-z-]{1,40}", phase):
            raise ValueError("Unsupported connector evidence")
        for key in ("thread_id", "turn_id"):
            if key in values and not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", values[key]):
                raise ValueError("Unexpected provider identity")
        if "output_excerpt" in values:
            # This is model output only. Account/authentication RPCs and prompts
            # never enter this journal; the model has no credential-reading tool.
            excerpt = values["output_excerpt"][:8192]
            excerpt = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}", "<redacted-key>", excerpt)
            excerpt = re.sub(r"(?i)\b(Bearer)\s+[A-Za-z0-9._-]+", r"\1 <redacted>", excerpt)
            excerpt = re.sub(
                r"""(?i)(["']?(?:access_token|refresh_token|api_key|password)["']?\s*[:=]\s*)["']?[^\s,"'}]+""",
                r"\1<redacted>",
                excerpt,
            )
            # Bound encoded bytes too: JSON escapes can expand a Unicode prefix.
            low, high = 0, len(excerpt)
            while low < high:
                middle = (low + high + 1) // 2
                if len(json.dumps(excerpt[:middle], ensure_ascii=True).encode()) <= 8200:
                    low = middle
                else:
                    high = middle - 1
            excerpt = excerpt[:low]
            values["output_excerpt"] = excerpt
        raw = (
            json.dumps(
                dict(time=datetime.now(timezone.utc).isoformat(), phase=phase, **values),
                ensure_ascii=True,
                allow_nan=False,
            )
            + "\n"
        ).encode()
        if self.count >= 32 or self.used + len(raw) > MAX_BYTES:
            raise ValueError("Connector evidence bound reached")
        view = memoryview(raw)
        while view:
            written = os.write(self.fd, view)
            view = view[written:]
        os.fsync(self.fd)
        self.count += 1
        self.used += len(raw)

    def close(self):
        os.close(self.fd)

    def sessions(self):
        """Private per-request persistent rollouts; credentials never stored here."""
        path = self.path.parent / (self.identifier + '-sessions')
        path.mkdir(mode=0o700)
        return path
