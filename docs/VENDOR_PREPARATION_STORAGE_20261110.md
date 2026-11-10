# Approved vendor task — storage and preparation recovery

This records the successful preparation/storage milestone before the owner's first
actual vendor run. Its then-next approval action is historical. See
[current continuation](CONTINUATION.md) and [execution recovery](VENDOR_EXECUTION_READ_LOOP_20261110.md)
for the later consumed run and fresh decision. No historical result is reclassified.

Following the Project Working Philosophy and requirements A9/A10/A11, recover the
original approved task, preserving its scope decision and separate run permission.
No campaign cap, development allowance or engineering renewal applies.

## Established starting state

Task2dc5b8ba, proposal24, scope approval59b032e5 was applied00:43:50UTC. Its receipt
explicitly grants no execution or publication. Preparation job67feb54c failed once
before the first confined check or any model-assisted preparation/implementation.
Its retained root had150851 bytes of evidence and189852433 bytes of clean scratch,
well below the operation's64MiB/1GiB bounds. Current free space was4217MB, below the
unchanged4294967296-byte physical recovery reserve. Exact original free bytes were
not retained by the combined error; do not invent that historical measurement.

The old preparer admitted using only generic72MiB evidence room, then copied the
189MB source before checking storage. It offered no automatic settled storage
recovery. The combined error concealed the failed dimension. The ordinary path was
not stopped by the old512MiB cumulative allowance; that obsolete check also remained
in a separate legacy Runtime preflight branch and has now been removed there.

## Host usage and supported reclamation

Kernel layout reports a Samsung850 block device of250059350016 bytes, not an exact
256GB device. EFI1127219200 + boot2147483648 + LVM partition246782361600 bytes account
for nearly all device capacity. Root LV246780264448 bytes essentially fills that
partition. Root ext4 reports242307891200 bytes (~225.7GiB), with filesystem overhead
and boot partitions explaining the lower displayed capacity. No sizeable unallocated
region is indicated. Unprivileged LVM queries were denied; kernel geometry remains
available. No layout, reserved-block, partition or filesystem changes were made.

statvfs reports10983067648 bytes of free blocks unavailable to ordinary users.
This is separate from Workbench's4GiB reserve. Directory totals explain almost all
227GB used (small scan/metadata difference); no large unexplained deleted-open-file
consumer is indicated. Read-only du stayed on the root filesystem. /tmp and /dev/shm
are separate tmpfs mounts and were not added to root usage. Root inode space is ample.

Largest root consumers: Workbench70.29GB, cache50.88GB (Hugging Face49.30GB), models
49.27GB, pilot31.95GB (checkouts27.96GB), root swap8.59GB, plus system/other user data.
Workbench contains104 retained stages: after initial reclamation browser copies40.27GB,
Python environments11.33GB, node modules1.97GB; histories/results are much smaller.
These are nested categories, not extra totals to add to the parent. Model/download
weights, swap, personal data, pilot work and important backups were inspected only.

Verified sharing of three inactive byte/metadata-identical browser copies reclaimed
1244934144 actual available bytes:4216086528 ->5461020672. Every path and sealed byte
remains. Current and two recent complete copies keep independent inodes. Private
append-only records map both paths/hash/inodes; atomic replacement cannot leave a
missing binary. Different, changed, linked or unowned inputs refuse/are preserved.
Supported installed cleanup of the original clean disposable failed clone reclaimed
another192901120 bytes, reaching5652369408 before ongoing ordinary filesystem use.
Its source, manifest, claim, failure and exact history remain. No model cache deletion.

## Durable implementation and verification

`storage_resources` observes each operation's evidence, scratch, actual free bytes,
inodes and proposed clone growth. It excludes the workspace and symlinks from evidence
accounting; historical runs do not form a cumulative allowance. Low disk, inode,
scratch, evidence and accounting failures have distinct diagnostics. Resource helpers
are included in runtime binding. The4GiB physical reserve remains enforced.

`release_cache` performs bounded low-space retention of identical inactive browser
files in fixed private Workbench release paths. It preserves paths/seals, source,
history, credentials, results and recovery records, keeping three independent recent
copies. Shared-inode checks avoid repeated binary hashing after cleanup. Disposable
previews cannot manage real releases. No new root command or service scope.

`preparation_resources` uses the existing job journal: verify the same applied scope,
no offer/session/model attempt, clean copy and absent/settled check activity; preserve
old claim/result/usage; then queue a fresh offline round under that approval. No model,
execution or publication authority. Unknown effects, altered copies/source/scope or
contrary accounting hold. New-clone admission accounts for actual source size. Automatic
recovery is reflected consistently in task, dashboard and acceptance-driver waiting.
Run consent remains a separate meaningful browser action.

Controlled resource, retention, duplicate/restart, scope/API and browser checks cover
the connected path. Two opt-in separate-process real-Lua/scripted-provider journeys
exercise injected storage collisions and normal browser recovery, then isolated edits
and independent checks. They are controlled evidence, not new real-provider passes.
Prior real provider/checked results must be preserved rather than replayed.

## Installed and demonstrated outcome

Candidate `9ac94592fd8163db6d4444dbf6b0816d055182a8`, tree
`1dea90f1feb79ab36cb38c0d4e5f302373d70ae8`, deployment86, seal
`9ebaae930a4e71e22311866d93b48e47c109037f6696e972d781ff4d60a8ff32` is installed.
The same saved preparation job completed through its automatic second offline
round under the same actual approval. Existing addon tests and isolation pass.
One canonical separate run offer is available; no new approval/model/run grant.
Vendor repair verification remains `not_run`. Owner must review/approve that run
if desired; do not infer execution permission from this repair or scope approval.

Additional low-space housekeeping brought total browser coalescing to13 inactive
distributions/3900 files/5384372370 logical duplicate bytes. Every path/seal remains.
Measured reclamation before the last two automatic coalesces was4757688320 bytes,
including supported cleanup of the failed clean clone. The final journals include
later coalesces. Physical free bytes after installation, backups and ordinary use:
8699461632 versus4217131008 initially (~3.93→8.10GiB). Do not equate logical bytes
with exact physical savings. Soft8GiB housekeeping target; hard4GiB reserve unchanged.

Connected backend319, final transition114, final storage10, frontend35, browser5
and two opt-in separate-process storage-fault journeys pass; static/contracts/build/
schema pass. Those are controlled providers, not fresh real-model acceptance.
Unchanged opt-ins not repeated: historical pinned four-provider browser rehearsal,
guild-roster readiness and four other legacy process modes. Prior evidence retained;
affected two modes exercised with resource faults, scope and fresh run boundaries.
Existing bundle-size warning and initial corrected fixture/preflight issues retained.

Installed original desktop/phone/reload and ingress-first four-service restart show
the run decision unchecked/unsubmitted and no stale preparation hold. Task/dashboard
agree. History comparison preserves461 old rows; only same preparation job and its
producer diagnostic projection change, one run offer added. Final464-row fingerprint
`8a9ff74e13f87da7e081330bb2aaa3ff8b37f04da615e6e21ccedb6ca5b179d1` matches post-recovery,
post-restart, backup and independent restore. Six earlier real checked results remain
canonically valid/readable without replay. Original source/history/failures/approvals,
Qwen/fallbackOff/policy18 and historical unknowns unchanged. No new provider calls.

Normal model PID1429966/auth/epoch and unpaused maintenance unchanged. Four services
active/enabled as Rich; helper and LAN/TLS/browser/internal-endpoint protections
verified. Prior physical-device evidence reused under unchanged routes/address/CA;
no new device test claimed. Previous installedef7e23e/691edf7/c5873b1 recovery retained.
No new access or storage decision needed. The meaningful separate run approval is
the remaining owner action, not an engineering renewal. Vendor bug still unfixed.

Private hash-bound closeout: `final-preparation-storage-closeout.json` in the evidence
directory below. Exact installation/test/rollback records in
`/home/rich/.local/share/TwichUI-Workbench/delivery-campaign-7b5963a/outcome-9ac9459`.
Current [handoff](VENDOR_CURRENT_HANDOFF.md) and [continuation](CONTINUATION.md) supersede
earlier pending-installation and awaiting-plan-review descriptions.

Private evidence: `/home/rich/.local/share/TwichUI-Workbench/vendor-preparation-storage-recovery-20261110`.
No spending, disclosure expansion, public exposure, publication or live-addon changes.
