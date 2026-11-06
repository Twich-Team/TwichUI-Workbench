# Proposed offline acceptance window for the reviewed task workflow

Prepared November 6, 2026, Mountain time. This record requests fresh
authorization. The previous archive and offline window closed on an evidence
hold and supplies no new execution authority.

Approve one 60-minute offline window to check the repaired retry test and
complete the browser and visual checks. Use up to 40 MiB of additional
evidence within the existing 512 MiB cap. Use fewer recordings and duplicate
captures, while keeping actual visual comparisons and useful failure
diagnostics. The work uses disposable stores and scripted model responses.
No new archive, higher storage cap, live-model trial, installation, or
publication is part of this request.

## Candidate and diagnosis

- Offline test checkpoint `cf014efe2c4999d500c60a32a545c54246749e74`, tree
  `d4e36a87083bdc90cb64fb451a733b545174c991`. Changes are limited to browser
  assertions and evidence capture. The product and guarded trial code match
  checkpoint `17ad1ac4658b26dcb3eba3fa44891a53d8310c97`. The previously prepared
  dormant manifest still hashes to
  `507049cbc6f2a28c858fb2c3321f44d1a5593af32249548510d0506637f5b1ff`
  and passed its 318-input read-only stage preflight. It is the identified
  future trial candidate, pending offline acceptance and separate approval.
- In the [failed Chromium case](../.local/runs/workflow-owner-acceptance-20261106-offline/chromiumFinal09.log),
  the retained Playwright network trace records HTTP 200 on the retry. It
  changes the same failed decomposition job to queued and retains attempt 1,
  the same applied approval and zero prepared tasks. Subsequent reads showed
  the same state for the seven-second expectation period. The request
  correctly returned to approved work awaiting preparation. The obsolete
  expectation described a new request waiting for its first planning job.
- Replayed the captured request and response in memory against the existing
  progress projection. It showed **Plan approved** and the preparation
  explanation. The repaired test requires the same queued job and approval,
  checks the visible progress, and then requires the same job to finish once
  and create prepared tasks. The second part needs a fresh browser run.
  TypeScript transpilation and a static comparison of the two capture
  configurations passed. A fresh browser or visual case has not run under
  this proposed window.

## Measured capacity

The counted regular files under the workspace and dormant stage total
446,384,285 bytes. This leaves 90,486,627 bytes (86.29 MiB) under the
536,870,912-byte cap. The table gives the workspace and stage breakdown.
Physical free space was 87,994,327,040 bytes at the workspace,
29,169,213,440 under `/tmp` and 32,407,216,128 under `/dev/shm`.

| Counted location | Logical bytes |
| --- | ---: |
| Workspace `artifacts` | 438,274,061 |
| Workspace `test-results` | 3,421,127 |
| Workspace `playwright-report` | 3,952,694 |
| Workspace `.local/runs` | 47,723 |
| Dormant stage `artifacts` (store, backup and policy) | 688,680 |

The prior archive recovered 125,356,114 logical bytes before new receipts.
Its 447 files and independent restore still match, as do the earlier
real-addon result's 222 sealed files. The 12 workspace archive-index files
account for 2,484 bytes, including this archive's 203-byte index. The old
window's 26 log and receipt files account for 46,139 bytes. These are
included in the table. The archive and restore are private local copies
under their authorized external destination. The external archive manifest
uses 144,167 bytes. Tracked visual reference PNGs use 1,921,002 bytes outside
the counted generated-output roots. No persistent copied fixtures or new HTML
report were found in the prior browser-run outputs.

The interrupted checks retained 35,736,318 bytes across their browser run
directories, plus their logs and receipts. The first diagnostic pass and
the sharded attempt each reached the guard while tests were writing. The
record establishes the threshold hit, while final file sizes reflect normal
Playwright cleanup of its temporary recordings and traces. The exact peak
was not sampled. The final sharded receipt measured 35,780,173 new bytes
against its phase baseline before closeout. Only 6,162,867 bytes of its
40 MiB allowance remained then. Sharding reduced the active set but did
not undo the accumulated evidence and repeated failure recordings.

| Retained browser output | Logical bytes |
| --- | ---: |
| Authored images | 16,264,064 |
| Playwright trace ZIPs | 13,144,426 |
| Standalone failure and comparison PNGs | 590,937 |
| Videos | 2,718,093 |
| Copies of authored PNGs in attachments (21 exact duplicates) | 2,927,023 |
| Browser-run JSON, logs and metadata | 91,775 |

For example, the retry trace alone contains approximately 1.03 MB of
embedded screen frames, 1.03 MB of attached evidence and 1.73 MB of
resources. The interrupted stale-scope trace contains approximately
0.90 MB of embedded screen frames and 1.67 MB of resources. The old
five-to-six MiB of residual phase allowance could not hold the rest of
the browser and visual work with meaningful failure evidence.

## Proposed limits and execution

The fresh window starts only after approval and initial read-only preflight.
Recount both roots with `scripts/project_env.py:artifact_bytes`. At the
measured baseline the full 40 MiB allowance would still leave 48,543,587
bytes (46.29 MiB) under the unchanged aggregate cap. The future live gate
would need its own capacity measurement and authorization. This offline
window does not set aside or spend a live-run allowance.

Use `WORKBENCH_E2E_COMPACT=1` with the existing Playwright projects and the
existing toolchain. Run on ports 5380 and 9287 only if both are free. Keep
one worker and use sequential Chromium shards of at most three cases and
visual shards of at most two cases. Each shard uses fresh counted evidence
directories and its ordinary disposable fixture store. For example, the
full Chromium pass uses `npm run test-e2e -- --fully-parallel --workers=1 --shard=N/36`
with `N` from 1 through 36 in order. Check the enumerated case inventory
before using these shard counts. If the runner groups tests differently,
partition its enumerated list into those same maximum batch sizes while
running every case once. The compact configuration keeps the line reporter,
standalone failure PNGs, textual assertions and context, and retained
Playwright DOM and network traces. It omits video, embedded trace
screencasts, duplicate attachments and optional Chromium helper captures.
The key task form, failed retry, stale scope, proposal, permission and
checked-result captures remain available. The visual suite retains full
resolution PNG captures and its existing snapshot assertions. No evidence
path or counted root changes.

On the 38-case portion of the sharded pass, compact selection would
have kept 5,126,223 bytes of authored PNGs and omitted 6,456,369 bytes
of optional PNGs. This is an estimate using the saved files. The planned
allocation is 16 MiB for retained Chromium output including the focused
check, 8 MiB for visual captures and comparisons, 4 MiB for failures and
logs, 7 MiB for active trace overhead, and 5 MiB for margin and closeout.
The estimate is a start gate. Measure actual retained and peak use on
the focused batch. Recheck the projection before running the complete
projects. Hold if it cannot fit, including the margin.

1. In a clean environment with local model, runtime and publication
   settings absent, first run the exact mockup/decomposition retry case.
   Then verify response-loss recovery and both viewport variants of the
   stale-scope case, including the server rejection. Allow one bounded
   correction and focused recheck only for a demonstrated test or harness
   defect. A material product or authority change needs a separate decision.
2. Run the complete Chromium project with all assertions in force. Confirm
   the test inventory and receipts. Cover request submission, both browser
   decisions on scripted data, retry, replay, reload, interruption, failure,
   expiry, changed permissions and a checked result with supporting detail.
3. Compare the full visual project and inspect fresh desktop and phone
   views, including the permission and result screens, plus enlarged-text
   coverage. Review both expected Work changes against the accepted layout
   before updating just the two Work reference PNGs if warranted. If those
   references change, rerun all visual cases. Hold for unrelated visual
   differences or regressions. Keep the earlier expected and actual
   evidence. No comparison tolerance changes or skipped cases.
4. Poll the aggregate counted usage and physical space throughout the
   run using the existing evidence accounting. Include active output,
   logs and receipts. Stop only the owned test processes by 35 MiB of
   growth or when the physical floor of 4 GiB on any used filesystem is
   reached. Leave the last five MiB and at least the last minute for
   retaining diagnostics and confirming closure. Stop for an unexpected
   failure in a final pass, insufficient margin or an elapsed deadline.
   Verify both test ports are closed, preserve the diagnostics, and record
   actual test and visual counts, scope and remaining capacity.

If the full offline checks pass, prepare a separate real-model trial
request tied to the verified candidate. No trial clock, model request,
probe, lease, installation, or service change is authorized by this record.
