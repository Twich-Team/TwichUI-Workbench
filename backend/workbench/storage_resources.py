"""Observed operation storage, distinct from historical campaign accounting."""

from pathlib import Path
import os
import shutil
import stat

RECOVERY = 4 << 30
EVIDENCE = 64 << 20
SCRATCH = 1 << 30


def regular_bytes(root, exclude=None):
    total = 0
    for p in Path(root).rglob("*"):
        if exclude and p.is_relative_to(exclude):
            continue
        try:
            info = p.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISREG(info.st_mode):
            total += info.st_size
    return total


class StorageHold(ValueError):
    def __init__(self, code, message, observation):
        super().__init__(message)
        self.code, self.observation = code, observation


def observe(root, workspace, growth=0):
    root, workspace = Path(root), Path(workspace)
    paths = [root, *([workspace] if workspace.exists() else [])]
    return dict(
        evidence_bytes=regular_bytes(root, workspace),
        scratch_bytes=regular_bytes(workspace),
        free_bytes=min(shutil.disk_usage(p).free for p in paths),
        free_inodes=min(os.statvfs(p).f_favail for p in paths),
        recovery_reserve_bytes=RECOVERY,
        proposed_growth_bytes=growth,
        evidence_limit_bytes=EVIDENCE,
        scratch_limit_bytes=SCRATCH,
        accounting="Regular files only; workspace excluded from evidence. Historical runs are not a cumulative allowance.",
    )


def require(root, workspace, growth=0):
    observation = observe(root, workspace, growth)
    if observation["free_bytes"] < RECOVERY + growth:
        free = observation["free_bytes"] / (1 << 30)
        raise StorageHold(
            "low_disk",
            f"Not enough free disk space for the working copy: {free:.2f} GiB available. "
            "Workbench keeps 4 GiB for safe recovery, plus room for this operation. Your approval is saved; "
            "verified storage cleanup and recovery are automatic.",
            observation,
        )
    if observation["free_inodes"] < 8192:
        raise StorageHold(
            "low_inodes",
            "The filesystem is short of file entries. Workbench is holding preparation "
            "and checking disposable storage; your approval is saved.",
            observation,
        )
    if observation["scratch_bytes"] > SCRATCH:
        raise StorageHold(
            "scratch_limit",
            "This operation’s working copy exceeds its 1 GiB safety limit. "
            "Workbench kept its evidence and must investigate its storage before continuing.",
            observation,
        )
    if observation["evidence_bytes"] > EVIDENCE:
        raise StorageHold(
            "evidence_limit",
            "This operation’s diagnostic files exceed its 64 MiB safety limit. "
            "Evidence is retained; this is not a cumulative task or development allowance.",
            observation,
        )
    return observation
