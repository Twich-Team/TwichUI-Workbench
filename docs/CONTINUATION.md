# Development readiness checkpoint — November 5, 2026

The inspection diagnosis remains the baseline: preserve the modular monolith,
durable worker, current design and owners; complete the missing integration.
This session implements storage/source readiness only. It does not start the
verification-profile or contract-to-execution stage or authorize activation.

Exact final commit/tree, source-file hashes, generated-contract hashes, bundle
location/hash and recovery result are in
`artifacts/runs/development-readiness-20261105/CHECKPOINT.json`. That record is
written after the baseline commit; it avoids a self-referencing commit hash here.
The source baseline contains this checkpoint and the latest PROGRESS entry.
Runtime/private state, evidence, dependencies and generated contracts stay outside
Git. Regenerate contracts using `npm run contracts`; do not edit them by hand.

## What is ready

- Shared evidence policy/diagnostics now match the implemented 512 MiB limit.
- Archive `readiness-20261105-m5-generated` preserves all 425 files / 87,351,027
  bytes from ONLY the three explicitly authorized closed generated directories.
  The external manifest preserves original paths/hashes/metadata; the local receipt
  is `artifacts/archive-index/readiness-20261105-m5-generated.json`. Use the README
  commands to verify, look up or restore. The older Lua-preparation archive retains
  its own original ARCHIVE.json mapping; historical losses remain documented.
- All 6,439 entry-time evidence/reference hashes match, including the 425 archived
  files. No failure, candidate, approval, database or baseline was selected for
  deletion. Only verified originals in the three named directories were removed.
- Interrupted copy/removal, actual killed disposable processes, repeat execution,
  integrity failures, unsafe paths, restore conflicts and exact round trips passed.
  Core tests/checks passed; this storage-only session does not claim fresh browser,
  visual, real-client, real-model or unattended acceptance.

Counted headroom after archiving is about 114.6 MiB, measured precisely in the final
checkpoint. A 96 MiB engineering/evidence reservation fits with about 18.6 MiB
remaining. A 128 MiB reservation does **not** fit. Recheck status and declare the
next stage's actual bounded reservation before beginning; do not raise the cap,
erase evidence, or silently omit checks. All failure outputs must fit that budget.

## Next acceptance condition (requires a new authorized session)

Start from the exact recorded Git commit, check source status and counted headroom,
then implement the smallest reusable verification-profile and contract-to-execution
connection through the existing owners. No design restart or parallel execution
engine. Do not repeat the already delivered guild task just to obtain evidence.

Current boundary:

- `regression_preparation.py` reads committed repository evidence and produces typed
  proposed contracts on existing jobs/proposals. Syntax/focused/full-suite commands
  are proposed and unrun. It creates no executable offer, budget or permission.
- `supported_tasks.py` still requires a hash-addressed developer-supplied template
  and recipe; `real_recipes.py` requires an independently frozen oracle and named
  controls. Generic proposed contracts are not executable/certified recipes yet.
- Existing `task_budget.py`, `task_execution.py`, `real_execution.py`, worker,
  confined runtime/check/freezer/seal and real-publication owners must be reused.

Acceptance: at least two distinct ordinary regression-test requests can use the
same data-driven profile/preparation path without new per-task registration code.
Bind the reviewed contract, exact repository/base, selected paths, framework/suite
inventory, immutable oracle/control identities and independently certified checks
into the existing executable recipe/offer flow. Positive controls must pass;
original, vacuous and wrong-behavior candidates must fail for their intended
reasons. Stale/changed scope/base/check bundles and candidate-authored self-proof
must be rejected. Replay/recovery preserves the existing one-shot/idempotent
authority boundaries. A scope-only approval must still grant no execution.

Run appropriate disposable unit/integration checks plus the previously incomplete
full browser suite once its output reservation fits; inspect actual desktop/phone
screenshots for any UI change. Never substitute scoped or fake checks for required
acceptance. A checked contract is distinct from model execution and delivery.

Live activation, services/migrations, leases/probes, inference, publication, spending
and remote push remain separately unauthorized. The prior draft PR is one assisted
delivery after failed Qwen attempts; reliable unattended Qwen completion, general
task-to-PR operation and full M5 remain unaccepted. Keep accepted Models/Phase 1,
port 8765, closed historical records and documented evidence-loss limitations.
