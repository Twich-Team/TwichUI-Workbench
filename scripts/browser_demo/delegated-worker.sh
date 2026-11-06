#!/bin/bash
set -eu
umask 077
unit=$1
out=$2
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
exec .venv/bin/python -m workbench.worker
