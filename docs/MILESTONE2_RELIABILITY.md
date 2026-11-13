# Milestone 2: task isolation and complete installation packages

Owner assignment: November 12, 2026, Mountain time. Scope is limited to the first
two corrections in the independent Astra architecture audit. Milestones 3/4,
automatic discovery, gallery continuation and map publication remain excluded.

The installed starting point is 0c3cecb/everyday-outcome-0c3cecb-m1. All 938 rows,
31 tables and schema0014 match logical fingerprint
e744e2bec0e9814636101151b6f18c917b97c374ee504eed1a7eb7f179c05570.

## Corrections and ownership

The worker previously let one operation's exception escape its shared scheduling
loop. Background feature delivery also used action eligibility while reading
historical contributions. Its fixed short-circuit stage order could starve later
stages. The existing worker now rotates its starting stage after successful work;
each stage retains its existing oldest-first claims, transactions and fences.
Canonical archived/deleted requests are excluded before planning/feature selection.
Historical feature readers validate original evidence without asking to execute.
Action entry points retain their default current-task checks.

worker_operation contains an exception only after a durable task journal has been
identified. It records failure/hold in that journal, preserves successful/terminal
outcomes and owner cancellation, and keeps claims, provider records, grants and
unknown usage. Unexpected failures never authorize replay. The existing recovery
owners remain authoritative; feature automatic renewal excludes the generic
unexpected-failure hold. Database failures, shared capacity failures and failures
without an identified task still propagate. The outer worker retains SQLite busy
recovery and its existing exclusive-owner/model safeguards. No parallel queue or
recovery state machine was added.

Hosted checks previously accepted source/frontend while a separately assembled
private installer omitted a manifest. release_bundle now packages exact committed
source (including the installer and locked dependency declarations), the checked
frontend, and a complete application hash inventory. Final full hosted acceptance
binds the artifact digest. Installation compares every accepted byte with the
staged application; it neither rebuilds nor repairs accepted application contents.
Installed Python dependencies/browser/reference inputs remain bound by the existing
private stage seal and external dependency pins; credentials never enter hosted
artifacts.

installation_preflight extends the existing sealed installation mechanism. Its
private envelope inventories installer/rollback scripts, configuration, runtime,
original rejection manifest, copied history, external executables and historical
evidence roots. Required configuration cannot be omitted from the inventory.
Configuration source/schema, application digest/revision, original draft authority,
database identity, source/evidence bytes and rollback prerequisites all validate
before activation. Durable installation intent and an exclusive installer lock
precede service changes. An interrupted installer requires explicit verified
rollback; an attempted activation is never automatically repeated. The existing
private service scripts still own actual activation/restoration. Cleanup refuses
any overlap with referenced evidence and requires verified retention for disposable
installation scratch. Existing historical paths are retained; no evidence migration.

## Initial verification (installation pending)

- Static/contracts/frontend build/schema checks pass.
- 34 boundary/CI/installer tests pass. Actual worker ticks prove contained expected
  errors, no uncertain replay, independent queued completion, fairness, and global
  database refusal. Installer subprocesses prove missing manifest, wrong digest,
  wrong revision, missing/unlisted configuration, incompatible schema, changed
  application bytes and incomplete rollback refuse before service operations.
  A killed installer retains durable intent and supports the rollback entry point.
- Expanded archive regression passes: historical inspection works, new action
  still refuses archive409, malformed archived descriptor cannot reach execution,
  two worker ticks reach unrelated planning and all retained rows remain exact.
- Earlier feature-delivery/pause regressions: 17 pass. Intake: 37 pass.
- Full local suites, exact hosted acceptance, saved-store rehearsal, installation,
  sustained health/restart/browser checks and final comparison remain in progress.

Private evidence is under the Workbench user-data directory in
milestone2-20261112 and m2-*.log. No new Workbench model request, addon execution,
gallery continuation or map publication is part of this assignment. Passing this
milestone does not establish complete unattended reliability.

## Saved-store rehearsal and recovery consolidation

The first hosted artifact (47ee725) was staged but not activated. Its saved-store
rehearsal found one additional action gate inside composed native-evidence reads.
That reader now uses historical validation too. The corrected test copy completes
14 production worker ticks: all 938 rows remain exact, 13 ticks reach unrelated
planning, one passive quarantine receipt is retained, and the transport guard
allows zero model requests. The original archive still refuses new action409.
The composed-evidence regression now also archives the real completed fixture and
checks historical display plus action refusal.

The old crash test intentionally expected RuntimeError to exit the shared worker.
Its replacement requires containment and preserves all former no-replay, ledger,
unknown-usage and task-count assertions. Unexpected feature-scope/check failures
now use the same hold_interrupted disposition as dead-owner recovery. No second
recovery interpretation is introduced. Six recovery/composition checks, all 51
worker/installer/CI/delivery/pause checks and static checks pass. The final package
also pins the retained release's interpreter/seal and invokes the exact pinned
shell; rollback verification does not depend on candidate application files.
A fresh full hosted run is required for this revised candidate before activation.
