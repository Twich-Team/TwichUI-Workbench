# Schema0014compatibility verified — November 11, 2026

The owner installed the separately reviewed0014compatibility helper. Its exact
SHA 72ceb24137afba5ebab832a4bc15d3784f49090f7c3e67998d4a800ac6b0e0a4,
root:root0755/link1, original session 78/cgroup and1794612571expiry were independently
verified through the existing approved executor. The first attempt refused a 0664
source input before backup/replacement; owner-controlled source permissions were
corrected to 0644, without relaxing the root check, and the owner completed the same
reviewed command. All four services and674records stayed unchanged by the update.
Do not rerun0013or0014upgrade scripts, replace the session binding, extend the grant
or use broader sudo. No further owner action is needed. Full private110install,
network/isolation/model checks and actual109rollback/110return subsequently passed.
[Exact completed helper action](WORKBENCH_ADMIN_SCHEMA0014.md) and
[cleanup acceptance](BACKLOG_CLEANUP_20261111.md) are current; earlier holds below
are retained historical checkpoints. Unrelated local administrative edits remain.

---

# Workbench administrator approval

**Decision requested.** Install the reviewed helper for the current Workbench repair and acceptance effort on `llm`. One interactive sudo authentication in the server terminal sets it up. The grant lasts up to seven days from bootstrap and is tied to the current Codex login session and boot. It survives routine retries and individual trial failures. It ends early on revocation, reboot, or loss of that session. Renewing or changing the grant requires the owner.

This grants the operations below for Workbench. Provider use, spending, release acceptance, publication and each browser plan/run decision keep their existing authorization. The bootstrap installs only the administrator mechanism. The restored loopback app is the current live deployment.

## What the helper can do

| Operation | Scope and guard |
| --- | --- |
| Install and update service definitions | Renders the six fixed roles `api`, `lan`, `worker`, `codex`, `review-api`, and `review-worker`. Candidate releases use a real Rich-owned `everyday-…` directory under the existing staging root. The review roles use the fixed sealed rollback build and original store. Configuration passed to the process is rendered in the protected helper. The helper never installs submitted unit text or a caller-supplied `EnvironmentFile`. |
| Manage services | Start, stop, restart, enable, disable, remove, or reconcile the named roles. The helper compares the entire unit to its template and checks the loaded file, aliases, UID and effective drop-ins. All services run as Rich. Only the exact Workbench boot link can change. A conflicting release prevents activation. Worker and connector stops require the relevant listeners closed, settled database state, and no active Workbench controller or recovery watcher. The exact legacy review files can be stopped, disabled or removed. To start them through the helper, first render the fixed rollback definitions. |
| Workbench configuration and isolation | Install or remove the single generated `PrivateNetwork=yes` worker drop-in while the worker is stopped. Run named TCP and sandbox probes as Rich, with bounded runtime. Cleanup checks a protected ownership record and the live transient unit identity before stopping it. A stopped definition can be reconciled after an interrupted system-manager reload. The helper reloads systemd for these file transitions and never restarts unrelated units. |
| Inspect host state | Read status, selected systemd properties, cgroups, effective BPF, and process identity for the named roles and read-only Qwen status. Read listening sockets for the known ports and connector path. Read bounded administrative journal fields and UFW/nftables state. Root output excludes service environments, arguments containing credentials, and raw application log messages. Rich can read ordinary application logs directly using existing access. |
| Home-LAN firewall | Add or remove one inbound IPv4 rule on `enp4s0` from `10.0.0.0/24` to `10.0.0.9` TCP 9487. Check active UFW, default-deny inbound, disabled routing, and no conflicting rule mentioning 9487. Start checks the LAN address and disabled forwarding. The helper tags its addition `twichui-workbench-admin` and records ownership before mutation. It preserves a matching pre-existing rule and can reconcile its own interrupted add or remove. The operator still inspects effective rules for broader ingress before cutover. |
| TLS, model restoration and evidence | TLS provisioning runs as Rich and validates the prepared CA, server IP, and key match. It writes only `ops/tls/server.crt` and `server.key` in the selected release, with private modes, preserving any different existing pair. The existing fenced Qwen lease and independent watchdog retain model restoration authority. The helper grants no direct model restart or credential-write action. History, archive restores, and application artifacts remain managed by their existing Rich-owned tools. |

Generated definitions retain the loopback and home-LAN listeners, local Qwen boundary, systemd filters, trusted-subnet application checks and Codex connector egress policy. `User=rich` and `Group=rich` apply to the single generated `ExecStart`. The renderer supplies no privilege override prefixes or additional hooks. Owner-writable application code, probes and certificate parsing run as Rich. This is not an isolation boundary between mutually hostile processes already inside the same trusted Rich login session. The Workbench system services, delegated task scopes, and different login sessions have a different cgroup or audit session and cannot use the grant. The untrusted task sandbox remains separate. [systemd service directives](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html) · [systemd resource control](https://www.freedesktop.org/software/systemd/man/258/systemd.resource-control.html)

## Exact bootstrap and smoke checks

| Reviewed file | SHA-256 |
| --- | --- |
| [Root helper](../ops/workbench_admin.py) | `1fd4a561188560242d5cb5df9bd583d8c922cfa4282e4bd1e46b8dca04bc3afb` |
| [Bootstrap script](../ops/workbench_admin_bootstrap.sh) | `ec23ec031f4e7d53fda8d8791789d93586e566966188ae337b5d1aef89369387` |

After approval, run the following in the server terminal as Rich. Enter the sudo password only in that terminal.

```bash
cd /home/rich/.codex/worktrees/everyday-workbench/TwichUI-Workbench
printf '%s\n' \
  '1fd4a561188560242d5cb5df9bd583d8c922cfa4282e4bd1e46b8dca04bc3afb  ops/workbench_admin.py' \
  'ec23ec031f4e7d53fda8d8791789d93586e566966188ae337b5d1aef89369387  ops/workbench_admin_bootstrap.sh' \
  | /usr/bin/sha256sum -c - && /usr/bin/env -i HOME=/home/rich PATH=/usr/bin:/usr/sbin:/bin /bin/bash ops/workbench_admin_bootstrap.sh
```

The script runs as Rich. Fixed system tools perform the privileged copies and verification. It installs the helper at `/usr/local/sbin/twichui-workbench-admin`, the root-private grant at `/etc/twichui-workbench-admin/grant.json`, and a root-private state directory at `/var/lib/twichui-workbench-admin`. It checks the SHA-256 of each protected copy before publishing the sudoers rule. It refuses existing destination paths. The observed scope is `/user.slice/user-1000.slice/session-78.scope`, audit session `78`, login UID `1000`. The kernel cgroup is root-owned. The protected grant records that scope, the current boot ID and a maximum seven-day expiry.

The sole added sudoers entry is:

```sudoers
rich llm=(root) NOPASSWD:NOSETENV: /usr/local/sbin/twichui-workbench-admin ""
```

The installed `sudo-rs` 0.2.13 parser accepts the exact no-argument syntax and rejects digest rules. Verification therefore pins bytes at bootstrap and relies on root ownership afterward. The helper separately refuses unexpected CLI arguments and checks its protected grant on every normal invocation. It reads one bounded JSON object, invokes fixed executable paths with literal argument lists and a clean environment, and serializes operations. It exposes no generic root command, file write, service name, or network rule. [Ubuntu sudo-rs manual](https://manpages.ubuntu.com/manpages/resolute/man5/sudoers-rs.5.html)

Before any service or rule operation, verify the installed mode and hash, a successful `status` from the approved Codex scope, and refusal from a different user scope. With the interactive sudo timestamp cleared, verify that an added CLI argument, the owner-recovery flag, and an unrelated root command are denied by `sudo -n`. Verify that an invalid JSON operation is refused and recorded. If any smoke check differs, revoke the new grant and retain the receipts. Existing independent sudo grants, including the Qwen lease's fixed service operations, are outside the new grant.

## Operator requests and recovery

Use a JSON object through the installed helper. For example:

```bash
printf '%s\n' '{"op":"status"}' | sudo -n /usr/local/sbin/twichui-workbench-admin
printf '%s\n' '{"op":"inspect","target":"logs","role":"codex","lines":50}' | sudo -n /usr/local/sbin/twichui-workbench-admin
printf '%s\n' '{"op":"service","action":"write","role":"worker","release":"everyday-system-20261106"}' | sudo -n /usr/local/sbin/twichui-workbench-admin
```

Validated service actions are `write`, `sync`, `start`, `stop`, `restart`, `enable`, `disable`, and `remove`. `drop-in` takes `on` or `off`. `firewall` takes `add` or `remove`. `probe` and `probe-clean` take `worker`, `lan`, `codex`, `isolated`, or `guard`. The guard probe also needs a release identifier. `inspect` supports `unit`, `logs`, `process`, `filters`, `firewall`, and `sockets`. `audit` and journal inspection accept 1–200 lines. Run the fixed TLS operation as Rich with `/usr/local/sbin/twichui-workbench-admin --tls-provision RELEASE`. Normal requests are capped at 4 KiB and five seconds to read. Subcommand output is bounded. A running transition may finish after expiry within its command deadline. After an interrupted call, inspect the loaded unit, pending jobs and protected records before retrying.

For a planned switch or rollback, close LAN access first. Use the local UI to stop owned work if needed and wait for the fenced Qwen watcher. Preserve the candidate store, original store, archives and recovery evidence. Close loopback ingress before stopping worker or connector. Remove only the owned rule and drop-in, then stop, disable and remove candidate definitions. Render `review-api` and `review-worker` with `everyday-system-20261106`, start those roles and verify old loopback assets, history and model identity. The helper's named probes clean their own temporary runtime; ordinary scratch and archive cleanup stays under the existing unprivileged tools. The Qwen watchdog continues under Rich's user manager if the administrator grant ends. The existing watchdog can be relaunched as Rich from the exact owned lease receipt with the matching release's `python -m workbench.real_lease LEASE_DIRECTORY` after confirming its original watcher is absent. The watchdog keeps its original identity, timeout and restoration checks. No lease path or backup is supplied to a root process.

The helper records each validated request and its start, completion or refusal in `/var/lib/twichui-workbench-admin/audit.jsonl`. Requests contain no credentials, application output or source. A missing completion is an uncertain transition. A full or inaccessible audit file holds operations. The owner can move an exhausted log to another protected filename using ordinary interactive sudo and keep the original for recovery.

Use `{"op":"revoke"}` to end the grant early. It leaves current services and evidence in place. The owner can remove only `/etc/sudoers.d/twichui-workbench-admin` with interactive sudo and validate the remaining sudoers file. Expiry, reboot or a different session holds normal helper use. If recovery is necessary afterward, the owner can clear the sudo timestamp and invoke the fixed helper using interactive sudo with `--owner-recovery` and the same JSON input. That mode accepts only inspection, revocation, owned cleanup, and restoration of the fixed review roles. It cannot open LAN, start candidate services, or renew the grant. Keep the root-owned helper and evidence through recovery. If bootstrap stops early, inspect the exact listed files and remove only the matching partial installation before retrying.

## Verification before approval

48 offline tests passed, including argument and path rejection, exact unit rendering and systemd syntax, root-file and symlink checks, scope and expiry, release conflicts, ingress and active-work fences, protected probe identity, fixed TLS destinations, recovery restrictions, and owned firewall retries. The host parser accepted the proposed sudoers entry. The prepared certificate still verifies for `10.0.0.9` and matches its key. The active rollback definitions were matched against their pinned root-owned bytes. Live sudo authorization and actual helper-driven system changes await the post-approval smoke check. The helper and policy are absent from their privileged destinations.
