"""Trusted bounded package allocations; execution/accounting remain in the worker.

Each reserved cycle can start at most one existing controller/probe/attempt. Full
24,000-token capacity is reserved even if unused; actual usage remains the existing
ledger's responsibility. Append-only reservations never reset after failure.
"""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import time


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Package:
    def __init__(self, root, expected_hash=None):
        self.root = Path(root).resolve()
        self.spec = self.root / "BUDGET_BASELINE.json"
        if self.spec.is_symlink() or self.spec.stat().st_uid != os.getuid():
            raise ValueError("Owned package baseline required")
        self.hash = sha(self.spec)
        if expected_hash and self.hash != expected_hash:
            raise ValueError("Package baseline changed")
        self.budget = json.loads(self.spec.read_text())
        b = self.budget
        from workbench.real_execution import LIMITS

        profile = (
            b["deadline"] - b["started_at"],
            b["max_cycles"],
            b["max_probes"],
            b["max_attempts"],
            b["max_generated_tokens"],
        )
        if not (
            (profile
            in (
                (14400, 3, 3, 3, 72000),
                (7200, 2, 2, 2, 48000),
                (5400, 1, 1, 1, 24000),
                (7200, 1, 1, 1, 24000),
                (10800, 1, 1, 1, 24000),
                (21600, 3, 3, 3, 100000),
            ) or (0 < profile[0] <= 10800 and profile[1:] == (1, 1, 1, 24000)))
            and b["reserved_tokens_per_cycle"] == b["original_attempt_token_ceiling"] == 24000
            and b["original_turns"] == 20
        ):
            raise ValueError("Unexpected package ceilings")
        if (
            LIMITS["total_tokens"] != 24000
            or LIMITS["turns"] != 20
            or LIMITS["request_tokens"] != 2500
        ):
            raise ValueError("Per-attempt ceilings differ from package allocation")

    def require_time(self, seconds):
        if self.budget["deadline"] - time.time() < seconds:
            raise ValueError("Overall package time ceiling/reserve reached")

    def reservations(self):
        path = self.root / "cycles.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def enforce_storage(self, extra=None):
        roots = {self.root, *(Path(r["root"]) for r in self.reservations())}
        if extra is not None:
            roots.add(Path(extra))
        package_bytes = sum(
            p.stat().st_size for root in roots for p in root.rglob("*") if p.is_file()
        )
        real_bytes = sum(
            p.stat().st_size
            for root in roots
            if root != self.root
            for p in (root / "real-run").rglob("*")
            if p.is_file()
        )
        ceiling = self.budget.get("evidence_ceiling_bytes", 128 << 20)
        if type(ceiling) is not int or not 64 << 20 <= ceiling <= 128 << 20:
            raise ValueError("Evidence ceiling may only narrow the existing reservation")
        if package_bytes > ceiling or real_bytes > 64 << 20:
            raise ValueError("Overall package evidence storage ceiling reached; preserve files")
        return dict(package_bytes=package_bytes, real_run_bytes=real_bytes)

    def remaining_evidence_reservation(self):
        """Reserve the unspent part, without counting owned retained bytes twice."""
        used = self.enforce_storage()["package_bytes"]
        return max(0, self.budget.get("evidence_ceiling_bytes", 128 << 20) - used)

    def reserve(self, cycle):
        cycle = Path(cycle).resolve()
        if (
            cycle.parent != self.root.parent
            or not cycle.name.startswith(
                ("chronicle-lease-" + self.root.name + "-", "task-lease-" + self.root.name + "-")
            )
            or not cycle.is_dir()
            or cycle.stat().st_uid != os.getuid()
        ):
            raise ValueError("Owned isolated package cycle required")
        self.require_time(3000)  # full run, setup and restoration must fit
        self.enforce_storage(cycle)
        fd = os.open(self.root / "budget.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "r+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            rows = self.reservations()
            if len(rows) >= self.budget["max_cycles"] or any(r["root"] == str(cycle) for r in rows):
                raise ValueError("Package cycle capacity exhausted or already reserved")
            for prior in rows:
                result = Path(prior["root"]) / "lease-controller-result.json"
                if not result.is_file() or not json.loads(result.read_text()).get(
                    "restoration", {}
                ).get("restored"):
                    raise ValueError("Prior operation/restoration must be reconciled first")
            row = dict(
                index=len(rows) + 1,
                root=str(cycle),
                baseline_sha256=self.hash,
                reserved_at=time.time(),
                generated_capacity=24000,
                note="Capacity reservation, not proof a probe or attempt actually started",
            )
            fd = os.open(
                self.root / "cycles.jsonl",
                os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW,
                0o600,
            )
            with os.fdopen(fd, "a") as stream:
                stream.write(json.dumps(row, sort_keys=True) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            link = dict(package=str(self.root), baseline_sha256=self.hash, index=row["index"])
            with (cycle / "package-link.json").open("x") as stream:
                json.dump(link, stream)
            return row

    def verify(self, cycle):
        cycle = Path(cycle).resolve()
        rows = [r for r in self.reservations() if r["root"] == str(cycle)]
        if len(rows) != 1 or rows[0]["baseline_sha256"] != self.hash:
            raise ValueError("Current immutable package allocation required")
        return dict(
            package=str(self.root),
            baseline_sha256=self.hash,
            cycle=rows[0]["index"],
            deadline=self.budget["deadline"],
            generated_capacity=24000,
        )

    def clamp_lease(self, lease):
        from workbench.real_lease import atomic

        path = lease.private / "state.json"
        state = json.loads(path.read_text())
        state["deadline"] = min(state["deadline"], self.budget["deadline"])
        state["watchdog_deadline"] = state["deadline"] - 300
        atomic(path, json.dumps(state).encode())
        lease.metadata["deadline"] = state["deadline"]


def package_for(cycle):
    path = Path(cycle) / "package-link.json"
    if not path.exists():
        if Path(cycle).name.startswith("chronicle-lease-chronicle-completion-"):
            raise ValueError("Completion package cycle requires a reserved budget link")
        return None
    if path.is_symlink() or path.stat().st_uid != os.getuid():
        raise ValueError("Owned package link required")
    link = json.loads(path.read_text())
    package = Package(link["package"], link["baseline_sha256"])
    identity = package.verify(cycle)
    if identity["cycle"] != link["index"]:
        raise ValueError("Package cycle identity mismatch")
    return package
