# Delivery campaign readiness

November 6, 2026 Mountain time. Following PROJECT_WORKING_PHILOSOPHY.md,
this records engineering and rehearsal evidence separately from delivery.
The bound [campaign request](DELIVERY_CAMPAIGN_APPROVAL_20261106.md)
requires owner approval before new generation, transmission or cutover.

## Starting point and historical evidence

Checkpoint `23825bd` and its closeout were verified. The running review
API and worker still serve `http://127.0.0.1:9487/` with
`index-CkG6v3Ki.js`. The original database retains 79 rows across 28
tables, schema `0012`, integrity and foreign-key checks, and logical
SHA-256 `4b889a9e8ea11f8683b5750934b73ab12a6293e58ccd72433a8c490a06202204`.
The retained failed-candidate database has 94 rows and logical SHA-256
`c77699675a78a61e930e5280c8ab6c0604ea089845a2fca830c60ab57b9a44c5`.
Fresh private copies were used for diagnosis.

The saved run decision, applied session and issued grant survived.
The selected implementation request did not commit. The trace identifies
`codex_task.py → real_execution.process_one → task_execution.dispatch_one
→ real_execution.submit → write_transaction → commit`.
SQLite returned a busy/locked error at commit. The existing rollback
removed the uncommitted request. The controller ended before an
implementation model turn or implementation-attempt row. One signed-in
`gpt-6.1-sol` planning turn remains valid live evidence.

The retained evidence cannot identify the historical conflicting
process or connection. No connection/lock-owner trace existed then.
That identity remains unknown. The kernel lock table cannot reconstruct
a released lock. This report does not attribute it to a particular
browser, API thread, watcher or supervisor.

The relevant store uses local ext4, DELETE journaling, normal locking,
FULL synchronization and a 150 ms busy timeout. A read-only measurement
of the real run-approval endpoint against the preserved store held its
read transaction for approximately 150 ms. Execution status took about
19 ms. Read-only validation can include local certificate hashes,
committed-source checks and readiness measurements. Provider calls,
long-running check suites, controller waiting and subprocess supervision
stay outside database transactions.

## Established reproduction and repair

A deterministic test uses the actual task execution-status function in
a separate process and the actual task-bound dispatch write. It pauses
the read transaction before releasing it and verifies that reader's
PID and database inode in `/proc/locks`. With the prior direct commit
behavior, the inserted implementation request rolls back. With the
repair, COMMIT waits for the same reader to release and succeeds without
repeating the insert. An extended reader hold reaches the bounded
failure path, rolls back, and preserves the same command ID for a safe
local resubmission. This identifies the reproducer's lock holder.
It does not establish the missing historical identity.

SQLite documents that a reader-blocked busy COMMIT leaves its
transaction active and permits another COMMIT on that transaction.
[SQLite transaction documentation](https://www.sqlite.org/lang_transaction.html)

The final implementation retries only the SQLite COMMIT of the same
active transaction inside SQLAlchemy's dialect operation, for at most
one second in addition to individual 150 ms connection waits. The first
implementation issued COMMIT above SQLAlchemy's lifecycle. The complete
suite's fault-injection tests exposed that defect. It was corrected,
and the submission/result rollback tests passed again.

BEGIN still has its prior bounded acquisition handling. No transaction
body or provider request is replayed by the commit repair. DELETE mode,
FULL synchronization and the connection timeout are retained. The Codex
controller can wait up to ten seconds on local database contention
within its original deadline. Reentry uses the same durable request and
the existing recovery-first fence. A committed running fence cannot
become a new pending request.

Slow transaction and busy diagnostics record process ID, opaque
connection identity, elapsed time, application call site and matching
kernel lock-owner PIDs. They omit SQL, bound parameters and task,
account or credential contents. Missing lock evidence stays unknown.
Delegated children now inherit explicit bytecode suppression, avoiding
the 38 extra runtime imports found during the earlier rollback.

The adjacent dispatch, command, grant, completion/recovery and API error
paths were reviewed. Real process-kill tests cover pending dispatch,
the committed execution fence and a simulated irreversible provider
submission. Pending local work remains executable with the same ID.
Fenced interrupted work is retained as interrupted/unknown and cannot
submit again. A separate exclusive SQLite connection can be acquired
inside the scripted provider callback, proving no database transaction
spans that callback. This establishes local fencing and conservative
recovery, not exactly-once external execution.

## Production-like rehearsals

The four existing provider cases now use separate API and worker
processes and DELETE journaling. The former opt-in used threads and
enabled WAL only for that fixture. Two browser contexts exercise the
normal application, routine status polling, repeat command IDs and
changed/stale decision rejection. Controlled provider outcomes exercise
both safe pre-generation switches. Focused Lua and behavioral controls
are real. The unchanged 167-group suite and kernel metadata are
controlled in this particular fast routing fixture.

A separate two-bug rehearsal uses ordinary browser reports and the
application's source discovery, typed plan, independent original
reproduction, plan approval, separate run approval, repair, checks,
result and history. The two committed synthetic Lua bugs have different
files and fixture shapes. The same installation configuration is reused.
Both API and worker restart before the second report. Two browser
clients observe each task.

The native variant executes actual confined Lua checks and complete
fixture suites. Its worker has a delegated cgroup with 6 GiB memory,
zero swap, 128 tasks and 200% CPU. The bounded outer test unit also
contains browsers and the API, with a 512-task outer limit. Untrusted
commands retain the actual existing sandbox. The initial native fixture
used an unrecognized cgroup name and correctly held cleanup. Renaming
the owned fixture cgroup to the existing accepted task namespace fixed
the harness. No production cleanup check was relaxed.

Providers and service-restoration observations in these rehearsals are
controlled. The synthetic fixture implementations are test data. They
provide no flight-path answer, reproduction or patch. The actual pinned
addon is unchanged. The previous real 167-group baseline and navigation
bootstrap evidence remain applicable to the unchanged addon base and
checker/loader code. Real Qwen, signed-in Codex implementation, actual
switches, actual model restoration and the flight-path result still
require the proposed campaign.

## Final verification register

The final source is `cbddecff94aefd42830604b40cf22225500a7147`.
The initial stage's file/link seals and the installed helper's
unprivileged stage validation passed. All 336 tracked files were
compared with the commit. The two acceptance-input files differ only
by the documented task-title suffix. Application assets match the
sealed distribution.

| Check | Final evidence |
| --- | --- |
| Build, contracts, types, lint and disposable schema | Passed through `wb.py check`. Schema remains `0012`. |
| Default backend suite | 1,144 passed and three separately covered opt-ins. |
| Frontend unit suite | All 25 tests passed. |
| Administrator helper suite | All 48 tests passed. |
| Pinned-addon routing opt-in | All four journeys passed in separate processes, two browser contexts, repeated/stale decisions, actual focused Lua and controlled provider outcomes. |
| Ordinary bug process opt-in | Two distinct fixture bugs passed across full API/worker restarts, including native sandboxed checks and complete fixture Lua suites. |
| Native corrective-readiness opt-in | Passed with actual confinement and scripted HTTP, including a failed check, bounded diagnostic retrieval, a correction and independent rechecking. Relevant helper/test bytes match the final candidate. |
| Complete Chromium suite | 108 passed, with two everyday opt-ins exercised separately. The final rerun used compact evidence and tracing Off within the existing development ceiling. |
| Everyday regression browser opt-in | Passed both ordinary tasks in one history with its dedicated everyday fixture. |
| Ordinary bug browser opt-in | Passed with the required everyday and bug fixture flags, including saved patch and original-versus-candidate evidence. |
| Visual suite | All 10 passed. Actual 1440-pixel and 390-pixel bug plan/result screenshots were inspected. |
| Store-bound installation setup | Checked 79-row disposable copy passed configure, both supported classes and the proposed 1 GiB policy. |

The default backend skips require explicit source, delegated-cgroup or
multi-process browser setup. Each ran separately as described above.
The native private-loopback test ran in the default suite. The earlier
navigation bootstrap and pinned-addon 167-group baseline are reused
with verified unchanged source/checker inputs. Their scope remains
offline setup and baseline evidence.

The first bug-browser invocation omitted the everyday fixture flag
and held at model selection before a planning submission. The correct
combined flags passed. The interrupted browser rerun and its owned
test-server cleanup are retained separately from the successful final
run. Its peak counted evidence was 511,638,817 bytes, below the
536,870,912-byte development ceiling. Browser tracing was disabled in
the final run to bound capture growth. This limits retained trace
diagnostics, while results, source, screenshots and process/native logs
remain available.

Private logs and bounded receipts are kept under
`/home/rich/.local/share/TwichUI-Workbench/campaign-rehearsal.AoPlSkKQ`.
Earlier failed and interrupted verification outputs remain preserved.
No archived originals were pruned in this assignment.

## Capacity, installation and remaining decision

The new stage proposes a private, database-bound 1 GiB evidence policy.
The installed app and development checkout have not been switched to it.
Every existing accounting root is retained. The initial 27 roots can
grow to 33 across three revisions, within the repaired 40-root bound.
The explicit upper capacity forecast reserves eight 64 MiB runs, four
6 MiB setups and 4 MiB of operator/decision receipts. Current counted
evidence is 504,755,170 bytes. The initial 1 GiB projection alone leaves
insufficient space for the full worst-case allocation and final
admission threshold.

The package therefore binds one proposed archive of two closed browser
output directories, 412 regular files and 48,174,500 logical bytes.
Its exact manifest and name are in the approval record. Ownership,
inventory and absence of open handles passed the read-only preparation
check. No archive copy or pruning occurred. After that archive and a
64 KiB index allowance, projected headroom is 617,095,618 bytes.
The full allocation leaves 50,864,578 bytes for settlement and
117,973,442 bytes before the last run, above the 4 MiB and 72 MiB
gates respectively. Minimum observed physical free, including shared
memory, is 32,407,216,128 bytes.

These projections do not waive any live measurement. The four earlier
verified archives, their indexes and independent restores are retained.
Archive authority is limited to the two exact directories in the new
manifest. Physical free space, scratch, task-wide output and admission
reserves remain separate gates.

The installed administrator helper and session-78 status were rechecked.
Its hash matches the approved mechanism. No grant renewal, expanded
operation, firewall/service cutover, provider generation, task source
transmission, addon installation or publication occurred. The temporary
offline test units are owned, bounded and collected.

The owner decision is the exact combined campaign, including its
revision mechanism, live/source/browser bounds, 1 GiB installation
allowance, named service/network changes, checked history and rollback.
Successful preparation is not delivery. The required real bug and
initial-product gaps remain open until their actual acceptance gates
are demonstrated.
