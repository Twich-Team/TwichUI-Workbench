# Why the map-fade approval was not recorded

November 6, 2026, Mountain time. Reviewed the actual checkout at
`42f8b88` and the closed trial evidence before editing. The old trial
launched the reviewed `17ad1ac` candidate. Its database was inspected
read-only, and its sealed receipt and all 41 referenced files matched.
The everyday app, port 8765 and model listener retained their owners.
Port 9187 was free.

## Reconstructed timeline

All times below are MST. Subsecond order comes from saved UTC timestamps.

| Time | What the evidence establishes |
| --- | --- |
| 11:12:45 a.m. | The owner had confirmed access and availability before the 105-minute clock started. |
| 11:15:14 a.m. | Workbench received and saved the owner's request through the form. |
| 11:15:36 a.m. | Workbench saved the Qwen proposal. The supervisor opened proposal review at 11:15:37. The chat tool dispatched the link at 11:16:24. |
| 11:21:44 a.m. | The owner's plan approval was saved at 11:21:44.069 and applied at 11:21:44.432. Preparation was queued at 11:21:44.598 and had written its recipe by 11:21:44.827. |
| 11:37:58 a.m. | The independent preparation and controls finished. The certified offer was recorded at 11:37:58.626. |
| 11:37:59 a.m. | The supervisor read and verified the offer and opened the second browser decision at 11:37:59.390. |
| 11:38:15 a.m. | The chat tool dispatched the ready message. A reminder followed at 11:43:00. There is no receipt showing when either message was seen. |
| 11:45:45 a.m. | Browser writes reached the fixed cutoff at 11:45:45.304. The phase became explicitly read-only at 11:45:45.738, and the launcher closed at 11:45:46.399. |

Sources are the [closed trial receipt](../artifacts/runs/workflow-owner-acceptance-20261106-live/closeout-safeguards.json),
[API access log](../artifacts/runs/workflow-owner-acceptance-20261106-live/api.log),
[closed SQLite store](../.local/workflow-acceptance-20261106/artifacts/runs/browser-demo-store/review.sqlite3),
and timestamps in the local chat record. The HTTP log has request order
and status codes. It has no per-request timestamps, focus or click events,
rendered DOM, client errors, or notification read receipts.

## What the records can and cannot distinguish

- The plan was approved and preparation took about 16 minutes 14
  seconds. The offer opened with 7 minutes 46 seconds left. From the ready
  message dispatch, 7 minutes 29 seconds remained. That was a short
  scheduled review window after a long wait. The records do not establish
  how much of it the owner actually had.
- The log shows the owner browser's task reads during preparation. Those
  reads precede the supervisor's final offer check. The final
  `run-approval` GET is the supervisor's read in the sequence
  `run-offer`, intake, then task offer. There is no recorded browser fetch
  of the certified offer, DOM render, or return to the page after that
  sequence. Earlier HTTP 200 reads only prove the endpoint replied.
- The only application writes in the access log were model-policy setup,
  the submitted request, and the plan decision. There was no
  `POST .../run-approval` with any HTTP status and no second approval
  in the closed database or ingress receipts. No rejected, failed, or
  delayed approval write is evidenced. The browser could have been
  inactive, navigated away, or unable to reach the service before sending
  a request. There is no client evidence to tell which, or whether any
  click was attempted. Confirmed availability at the start does not
  establish availability when the second decision opened.
- The old ready message led to the top of the generic task page and
  depended on a separate refresh or navigation. There was no dedicated
  link to the second decision or trial cutoff on that path. This is a
  demonstrated handoff weakness. The precise reason the browser did not
  fetch the ready offer remains unknown.

## Repair and verification

The guarded trial has a read-only local-run status page with the actual
Mountain-time cutoff. It shows waiting, ready, saved awaiting application,
or closed based on the launcher and durable receipt. Its ready link
focuses the existing approval region on the normal Workbench task page.
The form continues to own the separate approval and its current offer
binding. The write gate rechecks the cutoff after frozen-input validation.

A new opt-in minimum review interval stops preparation from opening a
late offer. The proposed run sets a 120-minute outer window, a minute-48
browser cutoff and a minute-33 latest offer. A late offer holds with no
implementation. The 72 minutes reserved after browser decisions are
preserved. Direct notification and confirmation that the offer is visible
will establish a better handoff without replacing browser consent.

The change is limited to the trial launcher and the approval region's
link and focus behavior. `wb check` passed. The 45 focused backend tests
passed, and the 17 browser-stage tests passed again after adding the
cutoff-boundary assertion. Chromium passed the two connected certified
offer cases at desktop and phone widths. Both checked focus, scope,
confirmation and saved approval after reload. The saved approval did
not offer a duplicate action after the offer became stale. Desktop and
phone approval captures were inspected. The [staged synthetic rehearsal](../artifacts/runs/workflow-map-fade-review-rehearsal-20261106/terminal-session.json)
used the revised timing, the new status link, separate browser decisions,
and a duplicate click. It recorded one run, one checked simulated result,
and zero publication. The worker and API closed and its capability was
withdrawn. An initial rehearsal attempt stopped before starting its
services because its output path was outside the launcher's allowed run
tree. Its evidence remains in the preparation record. The corrected
rehearsal used a new counted run directory.

The old 107 Chromium and 10 visual passes remain evidence for unchanged
behavior and visual baselines. The changed local approval path was
rechecked as above. No other full browser or visual suite was rerun for
this checkpoint. No model service request, probe, lease, installation,
or change to the everyday app occurred.
