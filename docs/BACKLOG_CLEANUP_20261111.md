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

## Installed acceptance — November 11, 2026

Cleanup is installed in private preview at **1e848a7 / deployment110**. The exact
[hosted run43057614742](https://github.com/Twich-Team/TwichUI-Workbench/actions/runs/43057614742)
passed static/contracts/schema/build,2235 backend checks/11existing skips,58 frontend,
128 browser/3existing skips and12visual checks. Local totals above remain distinct.
No existing owner task was archived or deleted; both new cleanup tables are empty.
The schema marker is the sole pre-existing row change. All28original domain tables
are identical, and the upgraded store still contains674total rows.

The installed and returned110browser walks passed12desktop/phone/enlarged-text
geometry checks plus3actual vendor deletion reviews, all read-only, with unchanged
AI policy and no browser errors. The advanced installed walkthrough passed36more
geometry checks covering precise model/effort, PR links, technical evidence and
interactive usage controls. Disposable Undo acceptance also proved that a stale
Undo cannot override a newer restore/archive decision. One historical request group
remains protected because its recorded AI recovery is uncertain; its reason remains
visible, and all attempts are preserved.

Actual109rollback restored its exact schema 0013/29-table/674-row fingerprint.
Return/restart restored110with its exact schema 0014/31-table/674-row fingerprint.
Both stores remain retained. An independent installed-store restore was read by
both110and109code: all51original intakes, unchanged. Rollback refuses later owner
changes instead of replacing them with an older store. The old109strict320px/200%
maintenance notice check exposed its existing8px overflow;110fixes this and its
complete post-return size checks pass. The prior build's functional service,
private-network and data recovery checks passed; do not label its entire visual
walkthrough green.

The owner's0014helper copy initially refused group-write permission on its source,
before any backup/replacement. Source permissions were corrected without changing
its checks. The successful owner copy was independently verified as root:root0755,
SHA 72ceb24137afba5ebab832a4bc15d3784f49090f7c3e67998d4a800ac6b0e0a4,
with the same session 78grant and original expiry. All four services and all 674 rows
were unchanged by that compatibility update. No further owner action is needed.

Preflight also caught40bytecode caches generated by the previous-reader check;
only those added temporary files were preserved outside the old release and its
original seal was restored. No source or record changed and no seal was weakened.
All sealed-release reader commands now use disabled bytecode writes. A private
operation receipt's group-write mode was corrected, and the complete binding and
preflight rerun passed before cutover. These were verification-input repairs,
not application-test failures or relaxed checks.

Fresh read-only GitHub verification found vendor PR #10 merged, map PR #11 open/draft,
with both original heads unchanged. The application truthfully counts recorded
submissions; it does not turn historical publication receipts into live merge or
game-installation claims. The campaign performed no addon merge or publication.

Private receipts: ~/.local/share/TwichUI-Workbench/cleanup-20261111/outcome-1e848a7;
installed/returned browser evidence is in sibling installed110/returned110 and
installed110-cleanup/returned110-cleanup directories. Advanced evidence:
~/.local/share/TwichUI-Workbench/phase-b-20261111/cleanup110-controls.
The [legacy replacement direction](OWNER_LEGACY_REPLACEMENT_DIRECTION_20261111.md)
is recorded with initial dependencies and acceptance gates; live legacy owners
and the inference service remain unchanged. Feature Workshop remains separate.
