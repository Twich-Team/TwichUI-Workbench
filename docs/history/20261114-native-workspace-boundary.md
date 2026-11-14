# Native workspace isolation — revised boundary qualified

November 14, 2026, Mountain Time. Design and controlled qualification only.

**The revised boundary is supported by the installed Codex CLI and this host's
operating-system facilities.** Ordinary workspace writes can be permitted without
granting host access or accepting arbitrary changes into a candidate. The final
controlled fixture exercised native Codex tools, strict export of the same patch,
a frozen candidate and an independently authored credential-free verifier.

This supersedes the original per-file runtime-write requirement only under the
owner's later Native-Agent Isolation Boundary Correction. The original
[Checkpoint A failure](../NATIVE_AGENT_QUALIFICATION_20261114.md) remains valid for its
earlier contract; its evidence was preserved. No application adapter, installation,
real addon repair or PR publication was performed here.

## Six distinct boundaries

| Boundary | Contract |
|---|---|
| Host filesystem isolation | Model-controlled commands and file tools run with the pinned CLI's Linux bubblewrap/seccomp policy. Host data is absent from their filesystem view; runtime libraries and explicitly granted binaries are read-only exceptions. A working directory is not authority. |
| Source disclosure | A trusted preparer materializes only the frozen, authorized source inventory. Workspace write permission adds no right to read or upload other source. Private history, evidence and live stores are never copied into it. |
| Disposable workspace writes | Ordinary files may be created, modified and deleted in an independent disposable repository and its scratch area. Those actions do not confer candidate or publication authority. Git metadata and CLI/configuration control directories remain protected. |
| Credentials and live data | The trusted native controller authenticates separately. Its credential files and sockets are unavailable to shell, patch and image tools. No provider or publication credential may be inherited by tool environments. Private Workbench data remains outside the snapshot. |
| Candidate scope | After execution is settled and descendants are gone, the supervisor inventories all paths, including untracked and ignored paths and empty directories. It rejects forbidden changes and unsafe filesystem entries before creating any candidate. |
| Verification/publication | Verification receives an independent read-only copy of the accepted, hashed candidate and separate scratch space. It cannot read the dirty workspace or credentials. Only the existing trusted publisher may later consume that exact verified candidate under its separate authority. |

Post-execution scope validation is appropriate for disposable source changes.
It does not replace runtime protection of host files, credentials, network or
privileged operations. Semantic correctness still requires independent behavioral
checks; a path allowlist cannot establish that a code change is correct.

## Minimal implementation design

Use one native-agent-v1 path behind explicit new-task admission, with the existing
execution request and fence as owner. Add no scheduler, database migration or
historical-task conversion.

1. Freeze task/report identity, original commit and disclosure manifest, permitted
   candidate paths and operations, expected behavior, provider/model/effort,
   allowance semantics, resource limits, pinned CLI/configuration hashes, approved
   tools and the separate publication-authority reference.
2. Export approved Git blobs without running repository hooks or filters. Initialize
   independent Git metadata without hardlinks, alternates or host remotes. Reject
   unsafe input paths and reserved CLI/plugin/control content before launch.
3. Start the trusted CLI controller with a fresh private configuration directory and
   an explicit sanitized environment. Ignore inherited user configuration/rules;
   disable hooks, plugins, external MCP discovery, web search and agent delegation.
   Keep supported core tool execution enabled. Grant write access to disposable
   source/scratch directories, with protected control directories and no broad
   host-readable root. Provider transport remains trusted and separately restricted.
4. Put the controller and every tool/helper process in one owned resource-limited
   cgroup. Persist process/session/cgroup identity before consequential work.
   Cancellation, timeout or worker recovery must settle that exact execution,
   stop descendants and prove cleanup. Missing heartbeat is never permission to
   duplicate or export work. Bound supervisor memory and output/evidence bytes
   while reading streams, rather than checking size only after buffering them.
5. Once settled, scan by directory descriptors without following links or trusting
   Git status. Account for creations, deletions, modifications, type/mode changes,
   ignored files, empty directories and Git metadata. Reject symlinks, hardlinked
   regular files, shared objects/alternates, devices, FIFOs, sockets, privileged
   modes, path escapes and resource-budget violations.
6. Compare with the trusted original manifest. An unauthorized source change rejects
   the entire export. Temporary data belongs in explicitly designated scratch that
   is never exported; leftover unapproved files in source also reject. Export
   approved regular-file additions/changes/deletions into a new independent tree,
   preserving every other original source file. Never execute workspace code while
   performing this scan or trust an agent-supplied manifest.
7. Freeze bytes, permitted modes, inventory and candidate digest. Remove write access
   in the verification environment and revalidate hashes before and after checks.
   Agent-authored tests retain their provenance; they do not become independent
   simply because another process runs them. The publisher must verify the same
   candidate identity, current destination compatibility and existing remote intent.

These are responsibilities of the existing preparer, execution owner, verifier and
publisher. The private qualification programs are reference probes, not a second
execution framework or installed production adapter.

## Focused evidence

CLI **0.166.0**, unchanged from the previous stop. Binary SHA-256:
`b8a11fedf24b2dfb59a70c9e52b030f9a508dcd49cd2dd0d41956f4c785156b8`.
Reviewed Astra's Option B assessment in archived chat
`01a1da0d-343f-7db1-b92c-5f12ffd0d89f`.
Host: Linux 7.0.0-38-generic x86_64, systemd 259.5 and system bubblewrap 0.11.1.
Hashes for the CLI, core execution helpers and bundled/system bubblewrap are retained.

The profile uses explicit source/scratch grants plus the documented minimal runtime
read policy, rather than broad filesystem reads. Linux enforcement and separate
service traffic are described in the
[official permission-profile documentation](https://learn.chatgpt.com/docs/permissions).
The core Code Mode host was enabled; its isolate exposed no process, require or
fetch global. Its discovered nested tools contained no web or external app tools.
MCP resource and template lists were empty.

| Check | Observed result |
|---|---|
| Native initialization and ordinary writes | 29 runtime assertions passed, including temporary file create/modify/delete |
| Private reads and outside writes | Credential/database/history canaries and actual host credential paths were blocked; outside write, traversal and symlink escape attempts were blocked |
| Network and privilege | External TCP, live host loopback and Unix socket connections blocked; no new privileges, zero effective capabilities, mount, host-process access and cgroup-write escapes refused |
| Native file-tool protection | Real CLI shell, patch and image handlers refused private paths; approved source patch succeeded |
| Native escalation/configuration | Explicit escalation was refused under approval never; an outside-working-directory write did not occur; hostile inherited MCP configuration was ignored |
| Resource controls | Actual cgroup readback: 1 GiB memory, zero swap, 128 tasks, one CPU; 1 MiB per-file limit was enforced; runtime limits applied |
| Descendant settlement | Cancellation and timeout each removed a tree containing six processes, including a detached double-forked child ignoring termination; cgroup empty and heartbeat stopped |
| Candidate rejection | 18 hostile cases rejected with no candidate directory created; two authorized cases accepted |
| Frozen verification | Independent verifier read accepted value 2 while the working copy held 777; source and credential reads and candidate writes were blocked; frozen hashes stayed unchanged |
| Integrated native fixture | The exact native-tool patch was exported, frozen and independently verified, starting from a clean independent fixture commit |

The 18 rejected cases cover unapproved modifications, additions, ignored additions,
deletions and empty directories; symlinks, hardlinks, Git alternates/configuration
and shared Git objects; FIFO, privileged/executable modes and file-to-directory
replacement; oversized files, entry count, aggregate bytes and depth limits.
The reference scanner caps entries at 4,096, aggregate content at 16 MiB, individual
files at 1 MiB, depth at 16 and scan time at ten seconds. It enumerates incrementally
and rejects excess rather than silently skipping paths.

All writable fixture areas were on tmpfs. Additional storage allocation was bounded
by the cgroup memory ceiling as well as the file-size limit. CPU, memory and task
ceilings were read back, not stress-tested to exhaustion. Production limits and
source-size allowances must be frozen for the real workload; these fixture values
are not proposed as universal application defaults.

## Scope and limits of this result

The final native integration used five canned HTTP responses from a local mock.
Across all native mock setup and qualification runs there were 27 local synthetic
HTTP requests, **zero real provider requests and zero real model tokens for this
qualification**, excluding development-chat usage.
Mock usage numbers are fabricated fixture data and excluded from actual usage.
No real provider credential was loaded or exported, and no addon source was used.

The subscription-only authentication, included-allowance checks, Smart allocation
semantics, unknown-usage accounting and no-API-billing safeguards remain unchanged.
Their qualification for a real unattended integration is still separate work.
The mock's unauthenticated local endpoint is test-only and cannot serve as a
production authentication configuration.

Initial diagnostic setup failures are retained: an empty shell-environment policy
broke sandbox initialization; the final launcher instead supplied a sanitized
environment and kept the CLI's internal environment handling. Early mock tests
disabled the required Code Mode host or expected an older tool-declaration format.
Final tests used the installed native interface. No host security setting,
capability restriction, Workbench code or verifier logic was relaxed.

This establishes the tested filesystem/tool/candidate boundary on this host.
It does not certify real provider capacity, real coding quality, worker restart
reconciliation, native accounting, complete addon regressions or publication.
The reference exporter and verifier are isolated qualification code, not installed
Workbench capabilities. No broad migration or real proof is authorized by this report.

Running build b6ed0db and rollback c294d65 each retained all 764 manifest files.
All four services kept their existing PIDs and zero restarts. Read-only history
inspection found 1,018 records in 31 tables with integrity ok. The original main
checkout's unrelated edits and the earlier failure evidence remain unchanged.

**Decision:** accept the revised isolation contract as the minimal implementation
design. No missing OS primitive was demonstrated for this boundary. Stop this
assignment after design and evidence; any later adapter/provider qualification and
one real frozen addon proof are separate work.

Private evidence:
`/home/rich/.local/share/TwichUI-Workbench/native-boundary-reassessment-20261114/`.
Final runtime/export/cleanup/verifier matrix: run-05. Integrated native chain:
native-mock-07. Supporting trials, programs, inventories, native events, synthetic
transport receipts, accepted candidate and preservation checks are retained.
