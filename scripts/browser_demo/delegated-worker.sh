#!/bin/bash
set -eu
umask 077
unit=$1
out=$2
if [ "${3:-}" = "everyday" ]; then
    # Only this startup's disposable synthetic preflight lives here.
    out=$(mktemp -d "$TMPDIR/workbench-startup.XXXXXXXX")
fi
IFS= read -r current < /proc/self/cgroup
cg="/sys/fs/cgroup${current#*::}"
test "${cg##*/}" = "$unit.service"
test -O "$cg"
mkdir "$cg/main"
printf '%s\n' "$$" > "$cg/main/cgroup.procs"
printf '%s\n' '+memory +pids +cpu' > "$cg/cgroup.subtree_control"
export WORKBENCH_CG_ROOT="$cg"
mkdir "$out/worker-preflight"
PYTHONPATH="$PWD/backend:$PWD/scripts" .venv/bin/python scripts/browser_demo/guarded_runtime_preflight.py "$out/worker-preflight"
printf '%s\n' "$cg" > "$out/worker-ready.txt"
if [ "${3:-}" = "preflight-only" ]; then
    exit 0
fi
if [ "${3:-}" = "offline" ]; then
    exec .venv/bin/python -m browser_demo.offline_app
fi
if [ "${3:-}" = "everyday" ]; then
    rm -- "$out/worker-preflight/read-denial-marker" "$out/worker-preflight/runtime-results.json" "$out/worker-ready.txt"
    # The synthetic Runtime also records its bounded command telemetry.
    if [ -f "$out/worker-preflight/usage.jsonl" ]; then
        rm -- "$out/worker-preflight/usage.jsonl"
    fi
    rmdir -- "$out/worker-preflight" "$out"
fi
exec .venv/bin/python -m workbench.worker
