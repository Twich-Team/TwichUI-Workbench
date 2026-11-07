# Workbench read-only Codex eligibility diagnosis

November 6, 2026 Mountain time. **Decision requested** — approve the
one-shot system-manager launch described below. The existing signed-in
read-only authorization covers the App Server methods. This request adds
only the named transient system-manager operation needed to give them
the connector's actual network restrictions. The installed administrator
grant stays unchanged. The original Workbench stays running on loopback.

## What the failed run establishes

The installed candidate and separate-device HTTPS passed. The previous
Codex-only acceptance reached a local `accounting` hold before a turn.
No plan or run was approved, and the other three cases stayed unstarted.
Rollback restored all 79 history rows, the original Qwen process and
authenticated access. The candidate LAN listener and firewall rule were
removed. The preserved receipt has a generic accounting category and
no account fields or RPC phase. [Closeout receipt](/home/rich/.local/share/TwichUI-Workbench/everyday-delegated-live-1fdee91/closeout.json)

| Check | Supported signal in the pinned CLI | Finding about the historical run |
| --- | --- | --- |
| Signed-in ChatGPT identity | `account/read` with `refreshToken=false`, `account.type` | The recorded code path reached accounting, which requires passing the ChatGPT-type check first. This is an inference from the bound code. |
| Eligible personal subscription | `account.planType` | Unknown. A missing or unfamiliar label and an explicitly excluded plan formerly shared a reason. |
| Selected model and effort | `model/list`, visible exact `model` entry and its `supportedReasoningEfforts` | Unknown whether reached. One catalog read confirms a listing only. A model's actual entitlement still needs an authorized turn. |
| Codex allowance bucket | `rateLimitsByLimitId.codex`, or the documented legacy `rateLimits` with `limitId=codex` | Unknown. A supplied null or invalid keyed Codex bucket could previously be masked by the legacy response. |
| Ordinary included usage | `ordinaryUsageAllowed` from `account/rateLimits/read` | Unknown. The pinned schema permits a boolean or null. It expressly says to leave a null result unavailable rather than infer eligibility from percentages or reset times. |
| Allowance windows and reserve | `primary` and `secondary`, integer `usedPercent` below 90, and current `resetsAt` for each | Unknown. The provider schema permits a null window or reset time. The current release policy still requires both usable windows. |
| Known exhausted or restricted usage | Explicit ordinary usage denial, reserve reached, `spendControlReached`, or `rateLimitReachedType` | The recorded hold category was accounting. The receipt establishes neither actual exhaustion nor the absence of a limit. |
| Additional credits and unlimited use | `credits.hasCredits`, `credits.unlimited`, and a zero `credits.balance` if supplied | Unknown. The credit object can be absent or null. The current policy requires affirmative no-credit and no-unlimited evidence. Earned reset credits are separate and are never redeemed. |
| Transport and resolver | Bounded initialize and RPC responses, fixed resolver, normal TLS, and numeric RPC error codes when reported | The recorded failure is consistent with a local eligibility interpretation check. It does not establish the precise live response or that the DNS repair resolved every transport issue. |
| Automatic reload | No field in this pinned account and rate-limit read contract establishes the setting | Off remains owner-reported, as does the zero additional-credit balance. Neither fact is independently verified here. |

The pinned CLI's generated schema is retained privately as offline
protocol evidence. The official docs describe the fallback quota view,
nullable windows, optional credit details and the model catalog.
[Official App Server documentation](https://learn.chatgpt.com/docs/app-server) · [Official model-catalog guidance](https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server)

## Local repair and verification

Checkpoint `969adfab3c4314e2978d5d983b2daed02f07721e`, tree
`f8a7894525c07076b8ca4c90dbbe1dc3054a3d35`, separates absent, null,
ambiguous and contrary evidence. It records the check name and phase
through the existing connector and planning receipts. It corrects the
keyed-bucket precedence and rejects malformed catalog or quota evidence
without conflating it with account exhaustion. A known hold stops before
a task prompt. The paid-account, ordinary-usage, credit, reserve and
two-window checks remain in force.

The real pinned CLI, using a private network namespace, synthetic
credentials and injected bind denial, passed an eligible fixture. Two
separate fixtures reproduced the generic historical category and now
report `ordinary_usage=null` and `secondary_window=null`, respectively.
The fixtures establish that both are possible. The historical record
cannot distinguish them. [Eligible fixture](/home/rich/.local/share/TwichUI-Workbench/codex-eligibility-preparation.4OLTtjC0/fixture-eligible.txt) · [Account-association fixture](/home/rich/.local/share/TwichUI-Workbench/codex-eligibility-preparation.4OLTtjC0/fixture-missing-association.txt) · [Secondary-window fixture](/home/rich/.local/share/TwichUI-Workbench/codex-eligibility-preparation.4OLTtjC0/fixture-missing-secondary.txt)

The focused provider and everyday suites passed 83 tests. The separate
four-case Chromium rehearsal passed again with controlled responses.
The harness is unchanged and uses the ordinary plan and run controls,
the full bound scope and offer, the exact case and provider, independent
checks and restoration. It rejects changed scope, commands, files,
provider or budgets, unknown dialogs, and partial or uncertain work.
The owner-delegated notes remain in the ordinary approval receipts.
All 48 administrator tests and the project build check passed. The staged
launcher refused an unopened window, and the driver refused a direct
launch outside its named cgroup. The systemd unit syntax check passed.

## Why a separate launch is necessary

The installed `codex` probe can run only its fixed TCP/bind check.
The configurable `guard` probe is private-network and localhost-only.
The production connector role cannot start alongside the restored review
roles under the verified grant. The user manager also allowed a real
loopback bind despite being configured with `SocketBindDeny=any` in an
offline transient. That unit was collected without any signed-in reads.
[Grant operation scope](WORKBENCH_ADMIN_APPROVAL.md) · [User-manager proof](/home/rich/.local/share/TwichUI-Workbench/codex-eligibility-preparation.4OLTtjC0/user-manager-boundary.txt)

## Exact missing operation

Approve an interactive owner sudo launch of the reviewed
`/usr/bin/systemd-run` command in the staged launcher below. It starts
only `workbench-codex-eligibility-1.service`, with an optional
`workbench-codex-eligibility-2.service` under the same window if the first
settles and a specific, recorded verification purpose warrants a second.
The shell runs as Rich. Only the transient service creation needs the
owner's existing interactive sudo. The service and its child run as Rich.
The operation adds no sudo rule or protected helper code.

| Binding | Exact value |
| --- | --- |
| Private operator directory | `/home/rich/.local/share/TwichUI-Workbench/codex-eligibility-preparation.4OLTtjC0/operator` |
| Six-file manifest `SHA256SUMS` | `8dc2e0071fc139eb63c1c2deaf962923d0ab57d1d3a8bb8d63f206b1ab442e7f` |
| `run-probe.sh` | `59d3c1fa3fb94b078f2f3773cfcb297172d62bdd2b282306c403c5b31310c233` |
| Existing Codex CLI 0.166.0 | `b8a11fedf24b2dfb59a70c9e52b030f9a508dcd49cd2dd0d41956f4c785156b8` |

The staged script checks the manifest, the private window receipt and the
exact transient unit name before invoking sudo. Inside the service, the
driver checks the UID and cgroup, no-new-privileges, zero effective and permitted capabilities,
the identical connector IP rules and bind deny, and the time limit.
It tests that actual IPv4 and IPv6 binds and the two known private
destinations are blocked before starting the CLI. It uses the existing
fixed systemd-resolved Unix lookup for `chatgpt.com`, the same public IPv4
mapping, normal TLS, the same cleared CLI environment and Bubblewrap
mounts, and the original signed-in file mounted read-only. No listener
or app store is used. The service is auto-collected, with the same
512 MiB memory, zero swap, one-CPU and 64-task envelope as the connector.

Before opening the window, recheck the original and failed-candidate
stores, absence of controllers and uncertain prior operations, current
grant and service state, sealed files, at least 72 MiB evidence headroom
and 4 GiB physical free. The measured headroom is 98,576,384 bytes and
the physical floor is 32,407,216,128 bytes. Set the private window receipt
only after coordination and approval. It binds the boot ID, start time
and the settled/capacity checks. Open one ten-minute window for setup,
diagnostic operations and cleanup.

The driver accepts only initialization, one account read, one model
catalog read and up to two allowance reads per probe. The pinned CLI may
read earned-reset metadata as part of an allowance RPC. The driver
cannot issue a thread, turn, cancellation or reset RPC. It sends no task
text, source or local evidence. The application deadline is under 120
seconds and systemd stops the unit at 120 seconds with up to five seconds
for cleanup. Require collection of the exact unit and removal of its
private runtime directory. A missing result, transport interruption,
uncertain cleanup or an unexpectedly open boundary closes the window
without a second attempt. No new login, refresh, API billing, purchase
or account-setting change is authorized. The console and receipts contain
only bounded method and check statuses, with no raw account responses.

After approval and preflight, the owner runs the following in their
server terminal and enters the sudo password there.

```bash
cd /home/rich/.local/share/TwichUI-Workbench/codex-eligibility-preparation.4OLTtjC0/operator
printf '%s\n' \
  '8dc2e0071fc139eb63c1c2deaf962923d0ab57d1d3a8bb8d63f206b1ab442e7f  SHA256SUMS' \
  '59d3c1fa3fb94b078f2f3773cfcb297172d62bdd2b282306c403c5b31310c233  run-probe.sh' \
  | /usr/bin/sha256sum -c - && /usr/bin/bash ./run-probe.sh 1
```

The unchanged launcher may use slot 2 only after a settled first receipt
and a private record of the specific follow-up purpose. This request
does not authorize executing a different driver or relaxing a policy
check. Owner-reported settings and the already accepted snapshot
uncertainty remain in effect.

## Next installation gate

The live eligibility probe has yet to run. The exact account or quota
check behind the historical failure is still unresolved. A newly sealed
installation package must wait for an eligible result in equivalent
confinement or a specific policy/access decision supported by observed
evidence. No such account policy decision is being requested now.
Generation acceptance, A8, general addon fixes and broader product
capabilities remain open. Visual styling follows functional acceptance.
