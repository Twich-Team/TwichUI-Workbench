# Saved revision and local-model priority recovery

Following the Project Working Philosophy and requirements A9/A11, recover the
owner's ORIGINAL vendor-item revision without resubmission or inferred approval.

## Confirmed incident

Task `2dc5b8ba-2d22-4c2c-9f90-3bd58d29d5fd` received a genuine Request changes
decision on proposal23. Exact text: “Also check to see if possibly the game
returns the item names slowly, and the display needs to be refreshed or similar.”
The decision was applied and job24 `522a9cf5-e729-47d6-aaf3-5ef7b84b42db`
remained durably queued, attempts0. It had not dispatched a model request.

Actual maintenance cycle422444 and its driver433514 held the existing cycle and
request locks. PID/start/cgroup/command/group and queue ownership matched; this
was live maintenance, not a stale reservation. Workbench only polled the locks;
it had no priority request/handoff. It also released locks between model requests,
allowing maintenance to reacquire ahead of later eligible work.

The dashboard counted only active addon work and omitted the queued request from
its main section. It therefore said No work running/Ready when you are while
the detail page repeated a waiting message. The detail message was also a generic
assumption that any contention meant maintenance; it had no owner observation.

## Actual recovery and resulting proposal

Preserved the original database/request/decision and maintenance-control bytes.
Supported opsctl pause prevented new maintenance admissions. A verified targeted
SIGTERM interrupted only the maintenance cycle's own process group. No lock was
deleted or completion invented. Its last generation outcome/usage remain unknown;
original maintenance interruption accounting and isolated working files remain.

The same job24 then resumed automatically through the installed application:
attempt `41cb2393-50cf-4bad-b3cf-1afa8bb3f88b`, one claim, three complete real-Qwen
responses,79105 tokens. Proposal24 `1e9427a8-69a3-5191-b21d-e6f95baa9a0a` completed
at23:53:31UTC. It addresses mixed known/late names, NI.Request/OnNames and the game
item-data event, refresh behavior, links and regressions. Exact runtime cause
remains uncertain; the isolated edit/test cycle must distinguish hypotheses.

No owner plan/run approval, vendor implementation, live-addon change or publication.
The exact revision, earlier scope, failures, decisions and usage remain preserved.
Qwen selected, fallback Off. This is a real revised plan, not a checked addon fix.

## Durable correction

`local_model_priority` separates trusted reservation ownership from provider
completion and usage. The worker reserves existing locks across an eligible local
planning operation, including source follow-ups. Local execution controllers keep
their reservation through authenticated restoration. Borrowing is limited to the
same actual process and unchanged lock inodes; no lock-file deletion or replacement.

Verified maintenance ownership permits bounded SIGTERM, then a revalidated bounded
forced stop if necessary. Unknown or foreign owners are held, not killed. A stale
journal does not establish activity, idleness, completion or zero usage. Actual
server readiness and retained ownership are checked before dispatch. A replacement
worker reconciles kernel ownership and records the old journal without altering
provider records. No arbitrary root or command authority was added.

Task and dashboard consume one authoritative model-state projection. Queued work
appears with its real title, reason and automatic-recovery explanation; counts
remain task counts. Maintenance appears separately only with a verified live
owner. Stale/missing ownership is reconciliation, not asserted maintenance.

## Verification and installed status

Controlled OS-process/lock tests cover genuine contention, ownership mismatch,
stale journals, reacquisition exclusion, duplicate dispatch, restart/reload and
preserved approval boundaries. Browser checks cover the same waiting explanation
on dashboard/detail at desktop and phone widths. These are not real-model passes.

Installation and final ORIGINAL-task browser/restart demonstration are in progress
at this checkpoint. Use the latest current handoff and bound private receipts for
actual installed identity, final verification, preservation, maintenance restoration
and genuine remaining owner review. A source checkpoint is not completion.

Private evidence:
`/home/rich/.local/share/TwichUI-Workbench/vendor-model-priority-recovery-20261109`.
No new spending, external disclosure, model replacement, public exposure, publication
or live-addon installation. Standing autonomy remains; no campaign caps/renewals.
