"""Internal fixed-check capability. Never constructed from model/API arguments."""

from dataclasses import dataclass
from pathlib import Path
import os


@dataclass(frozen=True)
class VerificationProcess:
    command: tuple[str, ...]
    root: str
    complete_backend_suite: bool = False

    def validate(self, command, cwd, rw):
        root = Path(self.root)
        if (
            tuple(command) != self.command
            or not root.is_absolute()
            or root.is_symlink()
            or not root.is_dir()
            or root.stat().st_uid != os.getuid()
            or not root.parent.name.startswith("workbench-verify-")
            or not Path(cwd).resolve().is_relative_to(root.resolve())
            or any(not Path(p).resolve().is_relative_to(root.resolve()) for p in rw)
        ):
            raise ValueError("Exact owned verification command/copy required")
        if self.complete_backend_suite and self.command != (
            str(root / ".venv/bin/python"),
            "-m",
            "pytest",
            "-q",
        ):
            raise ValueError("Nested namespaces are restricted to the fixed complete backend suite")
