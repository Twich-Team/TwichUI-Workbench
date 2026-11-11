# Backlog cleanup — November 11, 2026

Following the Project Working Philosophy, the overview milestone was completed
and installed first:38206c3/deployment 109. This connected increment adds task
organization without changing execution status, approvals, provider preferences,
publication receipts or original history.

## Implemented behavior

- Per-request actions in Backlog and task details; Archive, Restore and a deletion
  review. Archived requests have a searchable view and remain readable by direct
  links. Results has a current/archived selector. Dashboard and ordinary counts,
  search and outcome totals exclude archived requests. Usage retains all activity.
- Optional selection on the current Backlog page supports atomic bulk archive or
  restore. A mixed review explains ineligible tasks and offers an eligible-only
  review. Archive can be undone through revision-bound Restore.
- Archive covers the entire evidenced canonical request group. Original requests,
  scope, attempts, checks, results, PR links and lineage remain unchanged. External
  parent/dependency relationships prevent archival rather than orphaning work.
- Active or uncertain work, unapplied decisions, preparation/recovery, unsettled
  publication and unused live execution grants prevent Archive. Restore changes organization only;
  existing permissions still apply. Existing authoritative write/queue/publication gates
  require restoration before new work.
- Permanent deletion is deliberately limited to an archived, untouched, never-started
  saved-for-later idea with one original human scope and no work/evidence/links.
  Explicit confirmation removes that request and initial scope only. Minimal ID,
  original-input hash and cleanup receipts remain; retained backups are not erased.
  All worked requests retain Archive/Restore instead. No existing owner record is
  archived or deleted during verification or installation.
- Schema0014 adds two tables to the existing application database. No competing
  store or destructive historical migration. Command receipts and revision/context
  bindings are committed atomically under the existing SQLite write lock. Exact
  replay returns the original receipt; conflicting or stale submissions are refused.
  The browser retains a possibly-saved command across reload until its same receipt
  is recovered. Deleted task IDs cannot recreate their original request.

## Verification and delivery

Disposable tests cover persistence/restart, explicit deletion, tombstones, CSRF and
origin checks, concurrent requests, stale contexts, atomic bulk operations, grouped
history and dependency protection. A controlled completed publication verifies that
archival retains checks and the exact PR link, changes Results counts, and prevents
new publication. No external model or PR action is performed by those tests.

Actual browser acceptance covers desktop,390px phone,320px/200% text, keyboard
menus, confirmation, bulk cleanup, refresh, restoration and focus return. A clipped
menu was found in visual inspection and fixed without changing shared panel styles.

A fresh copy of the installed store migrates from29 to31tables, retaining all674rows
and all28original domain tables exactly; new cleanup tables are empty. Before hash:
2d6183ed01b5c0d7d1e7a6739200ab805044c83f004a8421de24b12e80fedf1a.
After copied migration:
2d312507ed6ded2f54616bf114649f71d312b276c007f1da0f9fc81a15716993.
Private evidence: ~/.local/share/TwichUI-Workbench/cleanup-20261111 and
.local/cleanup; browser captures under artifacts/runs.

Local verification passed: static/contracts/build/schema checks;2234backend tests
with8existing skips;58frontend tests;127browser checks with3existing skips;2final
cleanup/maintenance checks; and12visual checks. Final focused backend coverage
(131checks plus14cleanup/concurrency checks) covers changes completed during the
broader run, including the unchanged48administrator safeguards. Actual copied-store
review passed12page/size combinations and3existing-vendor deletion reviews with
zero writes/errors and unchanged AI policy. Current and previous109code each read
all51original requests from an independent schema0014restore unchanged.

The installed root helper currently accepts only schema 0012/0013. Its narrowly
reviewed schema 0014 compatibility update needs the owner's password-authorized
copy; the existing grant intentionally exposes no root file replacement. See
[exact compatibility action](WORKBENCH_ADMIN_SCHEMA0014.md). Keep private 109 active
until source/hosted checks pass and that compatibility is independently verified.
Do not change the grant/session/expiry, rerun the old0013 upgrade, stamp a false schema,
activate an unverified build, or use broader sudo from the autonomous executor.
