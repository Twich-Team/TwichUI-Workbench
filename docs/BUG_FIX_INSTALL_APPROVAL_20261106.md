# Workbench installation and flight-path acceptance

November 6, 2026 Mountain time.

**Decision requested.** Approve the sealed `2446199` package for one reversible
installation, the named evidence archive if needed, four bounded provider
checks, and one real bug task using local Qwen. The real task is your report
that navigation assumes every flight path is available. The app must show
how it would check what the character knows, learn from a flight-master
visit, and refresh on the next visit. A passing result includes a checked
local patch and evidence that the original code reproduced the problem.

The proposal uses the approved private LAN and the existing Codex sign-in.
It extends the earlier browser delegation only to the named bug, within the
file and behavior checks below. Offline fixtures passed. Real generation,
the flight-path patch, and a successful cutover are still open. An
unsupported, ambiguous or failed bug task holds acceptance.

## Exact candidate and supporting record

| Item | Binding |
| --- | --- |
| Workbench commit and tree | `24461998cf0642a50b04189903c5a7c8b4236834` / `d597e6447407170307202fc324691adcd645c02c` |
| Private stage `S` | `/home/rich/TwichUI-Workbench/.local/m5-review/everyday-bugfix-2446199` |
| Regular-file seal | `ops/bundle.sha256`, SHA-256 `05db600a17a94a926ef0f99679ed90e07811aeb50e044e02cec993d238aaf1bd`, 4,506 files, 556,234,534 logical bytes |
| Symbolic links | `ops/symlinks.json`, SHA-256 `3fdc4a7ffbf1f7bf5bcc98ca7fcd757cc6d794a451be60aa791c495c918caee0`, 16 links |
| Browser policy | `ops/browser-policy.json`, SHA-256 `8deca24916ac815411d1f73a689e9a5ba4b9febf20f8089bd4eb3bd71fb19e43` |
| Inputs | `release/ops/acceptance-input.json` SHA-256 `a8f65253ef713b8e8373214dc3c09f0f622c9619079ec17dee99cd0149ad4d18` and `release/ops/bug-acceptance-input.json` SHA-256 `c8db86e07f4489f65256dde899f69be67baa6ec0180a280a5676620744bf43c0` |
| Browser, read-only policy and supervisor | `release/scripts/{browser_acceptance.mjs,acceptance_policy.py,live_acceptance.mjs}`, respectively `2730f6487af2562fe16157984fd1e6385cccb2880e1119124b6355bb9ed3f6b3`, `004995445faaa47fc621402e924afb37889aa1cc8a6910b5959e98b44eee9605`, and `dd9378b335949df0d61a2d1c4d3f682d0dd96aaa233ea1b473d521b69c236046` |
| Store-bound setup | `ops/configure.py`, SHA-256 `0f2f26b9708e6597fbc232a89ba9c20b7bad7beec9bd188c01f5e783939ddcdc`. Enables existing test additions and offline Lua bug repair. |
| Browser and source | `index-DCWzH1F9.js`, `index-CBKHT43W.css`. Node 24.22.1, Chromium 156.0.8078.4, Playwright 1.64.1. Clean addon base `1caf530212bbc6eaf37e41d668902a391aa936c1`, exact local research root in `ops/inputs.json`. |
| Connector and privilege | Pinned Codex CLI 0.166.0 and connector transport from the prior stage, with the revised eligibility policy. Verified administrator helper SHA-256 `1fd4a561188560242d5cb5df9bd583d8c922cfa4282e4bd1e46b8dca04bc3afb`. |

The previous records establish the rollback procedure, kernel filter and
browser trust architecture, and historical read-only Codex eligibility.
Their approvals are closed. The new seal binds the current app, stage
configuration, units, five exact inputs and decision code. Repeat the
current checks listed below before live activity. [Previous package](EVERYDAY_DELEGATED_INSTALL_APPROVAL_20261106.md) · [Eligibility result](CODEX_ELIGIBILITY_LIVE_RESULT_20261106.md) · [Administrator grant](WORKBENCH_ADMIN_APPROVAL.md)

## Preflight and installation

1. Verify the sealed inventory and symbolic links using
   `python3 S/release/ops/acceptance_seal.py verify S`, with the absolute
   value of `S` substituted. Verify the administrator helper hash, current
   grant in login session 78, and enough validity for the full window and
   rollback. The verified grant expires November 13, 2026 at 4:29:31 p.m.
   MST at the latest. Do not renew or widen the grant in this package.
2. Require settled work, `enp4s0` at `10.0.0.9/24`, connected subnet
   `10.0.0.0/24`, gateway `10.0.0.1`, forwarding off, effective
   default-deny inbound, exact model and resolver identities, and intact
   rollback inputs. Preserve port 8765. At preflight the two review roles
   still served the original history. Its 28 tables and 79 rows passed
   read-only SQLite integrity, foreign key, schema `0012` and logical
   SHA-256 `4b889a9e8ea11f8683b5750934b73ab12a6293e58ccd72433a8c490a06202204`.
   Recheck current logical history and settle any new work. Do not discard
   any intervening rows.
3. Keep the 512 MiB counted ceiling, 96 MiB headroom before cutover, at
   most 6 MiB counted setup, 72 MiB before each planning and run
   admission, 64 MiB per run and 4 MiB settlement reserve. Require at
   least 4 GiB physical free and at most 1 GiB owned scratch. The stage
   forecast found 92,331,721 counted headroom bytes. If below 96 MiB,
   approve only archive `everyday-bugfix-2446199-browser-results` using
   `S/ops/conditional-archive.json` SHA-256
   `9cc8e652fc42474dd9b93093770b405be7676f0b83de221ab1deb173826476b5`.
   It binds 14 closed browser directories, 100 regular files and
   9,977,222 logical bytes, with source paths, hashes and metadata.
   Recheck the exact inventory, ownership, original-path references and
   absence of open handles. Reserve 32 MiB physical for the copy and
   independent restore. Copy to the existing private evidence-archive
   root, verify the bytes and metadata, restore separately, and verify
   original-path lookup before removing only matching originals.
   Projected headroom is 102,308,943 bytes. Hold if the approved archive
   or capacity checks cannot meet the thresholds. Preserve the three
   previous verified archives, both previously retained history copies
   and all other evidence.
4. Close the original loopback API and stop its settled worker through the
   named helper. Keep the original store and take an independently checked
   private backup and working copy at `S/artifacts/workbench.sqlite3`
   using the existing `everyday_store.py` checked-copy procedure. Confirm
   identical schema, integrity and logical fingerprint. Run the sealed
   `S/ops/configure.py` once against that working copy, then verify the
   private store-bound config, normal model key path, and
   `verify-active S`. The store remains on schema `0012`.
5. Install the four exact rendered service files from `S/ops` using the
   administrator helper for release `everyday-bugfix-2446199`.
   `workbench-everyday-{worker,codex,api,lan}.service` run as Rich.
   Match the installed root-owned bytes. Provision TLS using the helper's
   fixed operation and issue only the owned inbound rule for TCP 9487 on
   `enp4s0`, from `10.0.0.0/24` to `10.0.0.9`. Start the four roles
   initially disabled at boot. Keep Qwen authenticated on loopback and the
   Codex connector on its private Unix socket. Verify the effective kernel
   IP and bind restrictions, user, cgroups, peer and socket ownership,
   actual worker sandbox and cleanup using the existing named probes.
   The private Codex account review records the owner-reported zero extra
   credits and Automatic reload Off, the existing snapshot-risk decision
   and the exact new approval reference.
6. Verify actual installed assets and matching history on
   [loopback](http://127.0.0.1:9487/) and
   [home LAN HTTPS](https://10.0.0.9:9487/). Verify the TLS chain against the
   same server CA and test foreign Host, Origin and missing-CSRF rejection.
   Reuse the separate-device page load without a certificate warning from
   the previous installation when the subnet, interface, address,
   certificate trust and reachability evidence match. Recheck the newly
   installed listener, routes and scoped firewall rule. Ask for a fresh
   separate-device check only if a relevant condition changed or the
   verification cannot establish access. Devices trust the same server CA
   once if needed. Trusted-subnet operator access uses HTTPS without
   client certificates or dashboard login. The approved model and private
   execution endpoints remain outside LAN ingress. [Server CA](/home/rich/.local/share/TwichUI-Workbench/everyday-install-20261106-tls/workbench-home-lan-ca.crt)

## Live acceptance and source permission

The maximum is **ten hours**, with 30 minutes for installation checks,
four blocks of up to 105 minutes for provider cases, up to 120 minutes
for the bug, and 30 minutes for closeout or restoration. Begin only after
the exact approval, valid grant, sufficient capacity and settlement.
The provider cases retain the previous four exact task inputs and
outage controls.

| Order | Case | Expected route |
| --- | --- | --- |
| 1 | `codex-once` | Existing signed-in `gpt-6.1-sol`, fallback Off |
| 2 | `qwen-once` | Local `pilot-qwen38-27b`, fallback Off |
| 3 | `codex-with-local-fallback` | Named connector stopped through the ingress fence, one switch before generation, connector restored, local Qwen run |
| 4 | `qwen-with-codex-fallback` | Named worker in the fixed private network for the local readiness refusal, one switch before generation, normal namespace restored, Codex run |
| 5 | `bug-flight-paths` | Local Qwen alone, browser report from the owner. No prepared reproducer, injected model answer or prewritten patch. |

For the flight-path task, permit Qwen to receive the exact report and the
bounded excerpts of the nine committed files selected in
`browser-policy.json`. They include navigation source and existing test
context. The model may propose read access only to that envelope and
changes to at most three existing files from the narrower writable set
in the policy. The exact set is `!!!TwichUI/nav/{Journey,Nav,Travel}.lua`
and `tests/{test_nav,test_nav_journey,test_nav_unlock}.lua`. Workbench
also lists the three read-only navigation setup modules from the
committed load manifest. It can freeze an existing focused test's
bounded setup prefix and run it in the same confined verifier on the
original and candidate. The setup bytes come from the original commit
and are not sent to the implementation model. No external source
transmission for this bug trial. Only approved in-scope read paths, up to 45,000 source
characters, fixed checks, 150 changed lines and a 65,536-byte patch may
reach the Qwen run. The app must itself propose the repair and a reviewed
original-versus-candidate reproducer for initial eligibility and both
visits. Require relevant existing regressions and the exact complete
suite. Record any limit on offline game simulation. A report the app
cannot reproduce or safely fix is an honest hold.

For the four provider cases retain the prior Codex disclosure envelope.
It allows only sealed titles, input examples, task scope, relevant
callable signatures, paths and hashes from the pinned map module, runner,
harness and test, and committed test lines 1–120 for implementation.
A separate bug task choosing Codex in the installed app would require
the task-specific consent shown by Workbench for its selected committed
addon and test excerpts. This acceptance package allows no Codex bug
trial and no broader external disclosure. No host files, live game,
installed addon, unrelated work, sign-in data or secrets enter task data.

**Enforced local limits.** At most 66 Qwen generating requests and 79,980
Qwen generated tokens across three local planning claims and three
leases, each with one cancellation probe and one implementation attempt.
Planning keeps the 120-second clock, 2,600 requested generated tokens
and 24,000-byte request and response cap. Each lease keeps the prior
60-minute limit including restoration, with at most one 30-second
60-token cancellation probe, 20 implementation requests, 2,496
requested generated tokens per request and 24,000 total implementation
tokens. The 30-minute task planning consent stays separate from the run
approval and shared attempt deadline. The live flight-path case allows
one investigation call and one implementation attempt. The broader
app supports a separate explicitly consented same-provider follow-up,
which stays outside this live trial. If the bug needs clarification,
the case holds before its scope decision.

**Codex bounds.** At most four App Server turns, one planning and one
implementation each for cases 1 and 4. Each operation may make one account
read, one model catalog read and two allowance reads. Cap the App Server
at 120 seconds and five seconds local response/cleanup, the packet and
output at 24,000 bytes, stream at 2 MiB and line at 256 KiB. The tested
regression implementation takes one exact replacement up to 8 KiB.
The 32,768-token observed threshold is a hold check. It is unavailable
as a hard provider-side ceiling. The CLI may retry transport internally
before Workbench can observe it. Hold on an observed retry, without
requesting a replacement turn. The existing policy permits a maximum of
one eligible pre-generation planning switch within the original claim.
Execution stays with the provider that produced the reviewed plan.
Generated-failed, partial, active or uncertain work cannot switch or replay.

Before task source can leave the connector, require the actual signed-in
account, eligible plan, exact model and allowance gates. Both full
pre-turn allowance reads must affirm included usage, with valid primary
window below 90% reserve. Validate any supplied secondary window. The
owner-approved exception allows explicit secondary null only if every
other check passes. Denial, failed reads, ambiguity, exhaustion and
contrary credit or account evidence hold. This rechecks eligibility
inside the first installed case before source. No separate live
diagnostic or login is requested. Use included subscription allowance
only. The owner-reported account settings and accepted snapshot
uncertainty still apply. The snapshots cannot lock billing for a turn.
API billing, additional-credit use, purchase, Automatic reload, reset
redemption and upgrades stay outside this package.

## Delegated browser actions and stop conditions

Bind the private `S/artifacts/acceptance/window.json` with fields
`approval_reference`, `commit`, `seal`, `boot`, `opened_at` and
`normal_model_key_sha256`. Populate `preflight.kernel_filters`,
`preflight.history_preserved`, `preflight.lan_tls` and
`preflight.remote_lan_access` only from the corresponding verified
receipts, including the scoped reuse condition above. Keep private
fingerprints in the operator receipt. Invoke the sealed driver once per
case, in order, with the exact approval checkpoint. The following uses
the absolute stage and research paths from the bindings.

```bash
/usr/bin/env -i HOME=/home/rich PATH=/usr/bin:/bin LANG=C.UTF-8 \
  PYTHONDONTWRITEBYTECODE=1 \
  WORKBENCH_RESEARCH_ROOT="$RESEARCH" PYTHONPATH="$S/release/backend:$S/release/scripts" \
  LD_LIBRARY_PATH="$S/browser/lib" \
  "$S/browser/node" "$S/release/scripts/live_acceptance.mjs" "$CASE" "$APPROVAL_REFERENCE"
```

Earlier case receipts must be settled and checked. Keep unrelated submissions and settings changes
outside the window.

Extend the existing four-case delegation to the exact flight-path report.
The same browser driver gates same-origin writes, submits ordinary intake
and waits for the exact plan. The operator may independently read the
generated plan and map the three owner criteria to its observations.
Save only that review in private create-only `bug-plan-review.json`,
bound to the approval, seal, intake, plan binding and expectation digest.
Each criterion needs a distinct reviewed observation index and reason.
The driver holds for a missing, changed, ambiguous or out-of-scope plan.
This review supplies no fixture, test code, repair or model answer.
The driver uses ordinary browser controls for the separate scope and run
decisions and records the owner delegation, plan, offer and budget
bindings. It verifies the prepared original reproduction, exact source
and write scopes, route and budget before approving the run. No further
owner browser actions are routine under this approval. No publication
permission is available for any case.

Stop on failed preflight, missing or mismatched review, stale policy,
incorrect source or scope, unexpected dialog or duplicate action,
incomplete accounting, partial or uncertain provider work, failed
verification, restoration hold, or exhausted limits. No substitute tasks,
extra attempts or automatic replay. Close LAN ingress and remove only
the package-owned rule on failure. Use the normal stop controls for
owned work where applicable. Preserve execution, watcher and recovery
evidence until settled. If restoration is uncertain, keep the watcher and
hold further transitions. After settlement close loopback ingress, stop,
disable and remove only the candidate roles and owned drop-in, and restore
the fixed review roles against the original store. Verify original assets,
history fingerprint, listener, firewall baseline, socket absence, model
and credential restoration. Preserve diagnostics, both stores and verified
archives. Remove only owned temporary material after validation. Follow the
existing interactive owner recovery procedure if the scoped grant ends.

On success, verify five distinct checked results with no publication.
Verify the three observations of the flight-path report and review the
actual local patch. Confirm normal Qwen restoration, unchanged sign-in,
fallback Off, removal of the temporary drop-in and no unsettled work.
Verify the settled history and checked result after one controlled
restart with no duplicate task, provider or delivery work. Recheck both
listeners and effective filters, and enable only the four approved
candidate roles. Keep the original checked backup and fixed rollback
release.

## What this package would establish

After successful acceptance the owner can report supported offline Lua
bugs in ordinary language, approve the proposed repair, and review a
checked local patch. The same app can add supported regression tests,
choose local Qwen or signed-in Codex for eligible work, and explicitly
allow one safe planning switch in either direction. The browser records
and verification results persist. The supported scope is existing
committed Lua files that the bounded harness can reproduce and check.
Arbitrary addon defects, actual in-game acceptance, broader features,
inspections, simulator tasks, draft PR delivery and autonomous maintenance
remain separate requirements. The visual redesign and full requirements
audit follow functional acceptance.

The full local suites and the final targeted checks distinguish controlled
providers from installed acceptance. The reusable module and client
fixtures exercised actual Lua before and after the repair, including
successive observations, a failed reproduction hold, separate approvals
and recovery from stored history. A native, offline smoke check exercised
the real addon's client-harness loader with the confined Lua interpreter.
It found an absent harness operation and a dependency on the addon's
committed client setup. The final candidate runs the bounded setup
inside the confined interpreter and the real navigation fixture
started successfully. This proves the fixture can start. The live
case still needs an app-proposed before-and-after behavior check.
The earlier seals are superseded. A controlled Codex path produced a
two-file change checked by the same verifier. The real flight-path report
was used only to verify local committed-source selection and seal the
request. Its plan, reproduction and repair remain for Workbench.


The most recent full run before the final fixture-loader adjustment
passed 1,123 backend tests with two opt-in tests skipped and 25 frontend
tests. On the final source, build, schema, lint, the five focused bug
checks, the confined committed navigation setup smoke, and the bug
Chromium journey passed. The four unchanged regression inputs and
contracts passed the separate controlled browser rehearsal. The installed
helper's 48 offline tests passed and a disposable checked copy verified
the setup script. [Desktop plan](/home/rich/.codex/worktrees/everyday-workbench/TwichUI-Workbench/artifacts/runs/browser-r4tJyR/screenshots/bug-plan-1440.png) · [Phone result](/home/rich/.codex/worktrees/everyday-workbench/TwichUI-Workbench/artifacts/runs/browser-r4tJyR/screenshots/bug-result-390.png) · [Native fixture smoke](/home/rich/.local/share/TwichUI-Workbench/bugfix-package-proof.eROk9R/pinned-harness-bootstrap.txt)
