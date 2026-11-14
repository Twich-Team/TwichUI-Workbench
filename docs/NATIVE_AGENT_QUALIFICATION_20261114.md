# Native coding-agent qualification — controlled failure

Last updated November 14, 2026, Mountain Time.

**Checkpoint A failed; native execution remains disabled.** The installed CLI could
not run the tested policy with individual writable files. A broader directory
policy executed, but a hostile probe created an unauthorized source file. The
attempted outer boundary with individual writable file mounts could not start
Codex's inner sandbox: namespace creation was denied. Following the owner's
explicit stopping rule, this assignment stops for review. B and C did not begin.

This establishes failure of the tested installed configurations, not impossibility
of every Linux isolation design. No more permissive policy, privileged workaround,
CLI replacement or repeated repair campaign was attempted.

## Authority and starting state

The owner supplied this assignment in attachment
`d54c16e7-83d4-4de5-93eb-3adba99edb7c/pasted-text-1.txt`. Checkpoint A requires actual
source, credential, network, resource and process isolation before native coding,
and a controlled stop if that cannot be established. The new assignment superseded
the earlier campaign's owner-review stop only within its specified scope.

Read the current root philosophy, requirements, standing direction, execution,
plan, progress, ownership and design guidance; campaign closeout; Smart allocation
logic; provider, runtime, confinement, verification and publication code. Read
Astra's November 14 Option B assessment in archived chat
`01a1da0d-343f-7db1-b92c-5f12ffd0d89f`, Assess execution architecture.

The delivery checkout was absent at entry. The saved branch and remote both pointed
to `4f496cea7bd07872f82bdbfb8a6337800ecdb174`. Recreated its committed source at the
original path using the existing branch. The original checkout on main and its
five modified documents and three untracked documents remain untouched. The missing
checkout's previously reported uncommitted admin-document edits and dependency
links were not present to inspect or recover. No existing copy or archive was
removed. Recreating committed source does not certify recovery of those files.

## Installed identities and preservation

- Running Workbench: `b6ed0dbd4e3a5659e9831e19d03d7e629a72aac2`.
- Immediate rollback: `c294d65ea8fa0adf30b87574e03eb46b89e18ede`.
- Rechecked all 764 manifest files in each release: zero mismatches.
- Four services remain active with their existing process identities and zero
  automatic restarts. No installation, restart or rollback occurred.
- A read-only SQLite transaction observed 1,018 records in 31 tables; integrity
  check returned ok. This matches the earlier record count. The new diagnostic
  fingerprint has its own algorithm; it is not claimed equal to the older hash.
- No task, permission, strategy, execution request, publication record, addon
  source, artwork, model service, port 8765, credential, billing setting or recovery
  copy was changed by this assignment. Existing scheduling remains with the worker.

## Exact CLI and capabilities

Installed Codex CLI: **0.166.0**. Binary SHA-256:
`b8a11fedf24b2dfb59a70c9e52b030f9a508dcd49cd2dd0d41956f4c785156b8`.
The matching official source tag resolves to
`6ff1e1ca3b30fc8b914720681fcf5fe62e95f856`.

Installed help exposes noninteractive exec, JSONL events, model selection,
configuration overrides, explicit session-ID resume, and user-config/rules
controls. The sandbox command takes -P, without a linux subcommand. These are
capability observations. Real resumption, native authorship, cancellation
settlement, per-inference usage and model/effort attribution remain unqualified.

No native-agent-v1 adapter or engine revision was implemented. Existing tool-free
adapters and historical execution contracts remain unchanged.

## Security probes and the blocker

Commands used disposable source and fake credential/database canaries. No real
credential contents were supplied to a probe or included in evidence. No model
was invoked. A baseline bubblewrap namespace command succeeded; that alone does
not establish the required native-agent boundary.

1. **Individual writable file:** source directory read-only, one existing file
   writable, minimal runtime reads, network disabled. Exit 101 before the command.
   Bubblewrap tried to create allowed.py/.git; Codex then panicked inspecting
   allowed.py/.aws because the parent is a file.
2. **Directory policy:** writable disposable directory, narrower read-only sibling
   source rules and denied .codex directory. Initial setup refusals concerned a
   nonexistent deny path and the CLI reexecution path. Creating the directory and
   explicitly allowing the pinned CLI binary let commands execute. Protected reads
   and existing protected-file writes were blocked. However, exclusive creation of
   unauthorized-addon.py **succeeded**. Protecting known siblings does not enforce
   an approved set of writable source files.
3. **Outer individual-file mounts:** separate bubblewrap namespace, read-only
   source, one writable file mount, fake controller credentials outside the
   tool-readable scope. The first setup lacked bundled runtime resources.
   Supplying those resources and a writable disposable controller directory reached
   Codex's inner sandbox, which exited 1: "No permissions to create a new namespace".
   The diagnostic suggests a kernel/namespace restriction but does not prove its
   exact cause. No host security setting or capability restriction was relaxed.

| Required boundary | Actual evidence | Qualification |
|---|---|---|
| Unauthorized reads | Private host path and private canaries could not be opened | Limited command-level pass |
| Provider credentials | Fake controller credential open blocked; no real credential read attempted | Limited canary evidence |
| Workbench database | Fake database outside allowed paths blocked; live database not exposed | Limited canary evidence |
| Unauthorized writes | Protected existing source blocked; new unauthorized source creation succeeded | **Failed** |
| Symlink/filesystem escape | Symlink read/write to hidden controller blocked; host parent PID absent | Limited command-level pass |
| Network escape | External TCP and host loopback blocked with permission errors | Limited command-level pass |
| Resources/termination | Separate user-unit readback confirmed 1 GiB memory, zero swap, 64 processes, one CPU; runtime limit configured | Native lifecycle, total storage and hostile descendant termination unqualified |
| Configuration/hooks/extensions | Tool-side .codex/config.toml creation blocked | Full native startup/extension matrix not run |

Opening /proc/1/environ initially succeeded. Follow-up showed only sandbox PIDs
1 and 2, blocked init memory, no host parent PID, and no credential through init's
root. That open result is not evidence of host credential exposure. Partial passes
do not qualify the failed overall configuration.

A worktree, broader source writes followed by diff rejection, or instructions in
a prompt do not replace the missing enforced boundary.

## Subscription and accounting

Installed login status reports ChatGPT login. Official documentation describes
[saved authentication in noninteractive execution](https://learn.chatgpt.com/docs/non-interactive-mode)
and [subscription sign-in](https://learn.chatgpt.com/docs/auth). Those establish
available mechanisms, not completed qualification of this unattended integration
or current included allowance.

The [advanced account-auth automation guide](https://learn.chatgpt.com/docs/auth/ci-cd-auth)
warns against its auth-cache runner workflow for public/open-source repositories.
GitHub reports the addon is public. That guide also targets runners where login
cannot run; distinguish the intended local signed-in workflow before drawing a
terms conclusion. This report does not claim ordinary local Codex use on a public
repository is prohibited. Subscription qualification remains **open**. No real
unattended request, auth-cache export, allowance probe, API key, purchase, extra
credits or pay-as-you-go fallback was used.

Smart request-start allocation and strict-ceiling holds are unchanged. Native
sessions may contain several inference requests; a final-turn usage event does
not establish exactly one provider request. New native accounting and interruption
reconciliation were not implemented or claimed.

## Checkpoint D and owner review

| Requested outcome | Result |
|---|---|
| Adapter/integration tests | No new adapter; not run |
| Controlled provider tests | None |
| Real native coding requests/tokens | **0 / 0**, excluding this development chat |
| First substantive addon edit | Not reached |
| Patch and independent acceptance | None; not started |
| Complete applicable addon suite | Not run; no historical count substituted |
| Draft PR | None; zero publication attempts |
| Owner interventions after assignment | None |
| Engineering changes during frozen proof | Not applicable: C never began |

**No-go for native rollout.** Owner review should decide whether a separate
follow-up will qualify a supported CLI/host combination with exact source writes
and credential separation, and resolve subscription support. This report does not
authorize weakening confinement or starting that follow-up. The next complete
delivery outcome remains one securely executed native repair, independently
verified and published as exactly one authorized draft PR.

Private evidence:
`/home/rich/.local/share/TwichUI-Workbench/native-agent-qualification-20261114/`.
It retains CLI help/identity, policies, refusals and limited passes, unauthorized
creation and namespace receipts, resource readback, installed/rollback checks,
read-only history summary, original checkout status/diff hash, owner-goal hash,
official documentation snapshots, final probe files and an inventory. No production
credentials are included. Earlier disposable probe programs were overwritten as
probing progressed; their commands remain in this chat's tool history. The final
probe files are not an exact source archive of every earlier command.
