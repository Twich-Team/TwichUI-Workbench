"""Fixed trusted one-session preparation child; no model, lease or publication."""

import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from workbench.task_session import provision  # noqa: E402
from workbench.real_workspace import Runtime  # noqa: E402


def main():
    root = Path(sys.argv[1]).resolve()
    if not (
        (root.parent == ROOT / "artifacts/runs" and root.name.startswith("task-lease-"))
        or (
            root.parent.parent == ROOT / "artifacts/runs"
            and root.parent.name.startswith("task-pr-completion-")
            and root.name.startswith("authored-")
        )
    ):
        raise ValueError("Owned task run root required")
    cg = Path("/sys/fs/cgroup" + Path("/proc/self/cgroup").read_text().strip().split("::", 1)[1])
    if not cg.name.startswith("workbench-real-prepare-"):
        raise ValueError("Dedicated owned preparation scope required")
    (cg / "main").mkdir()
    (cg / "main/cgroup.procs").write_text(str(os.getpid()))
    (cg / "cgroup.subtree_control").write_text("+memory +pids +cpu")
    os.environ["WORKBENCH_CG_ROOT"] = str(cg)
    from workbench import real_execution
    from workbench.real_workspace import durable, sha

    durable(
        root / "preparation-owner.json",
        json.dumps(
            dict(
                owner=real_execution.owner_identity(),
                recipe_sha256=sha((root / "real-run/recipe.json").read_bytes()),
            ),
            sort_keys=True,
        ).encode(),
    )
    provision(Runtime(root / "real-run"), json.loads((root / "template.json").read_text()))


if __name__ == "__main__":
    main()
