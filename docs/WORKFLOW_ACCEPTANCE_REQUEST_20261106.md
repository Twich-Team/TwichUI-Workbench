# Approval request for the simpler task workflow

Prepared November 6, 2026, Mountain time. **Proposed. Awaiting fresh owner authorization.**

Approve one package to preserve the named old browser evidence in a verified local archive, finish the offline browser and visual checks, and repeat the map-fade test once through the simpler interface using the existing local Qwen model. The real trial uses a fresh isolated copy and still requires your separate decisions on the actual plan and the local-only run. During the trial, other users of the local model may wait. At the end, confirm normal model access and close the isolated execution services. Installation, addon installation, and publication each require separate authority.

Approval refers to this record, including the exact inventory and fingerprints below. The offline preparation has a 45-minute limit. The isolated live trial has a separate 105-minute window agreed with the owner before it starts. Time waiting for this approval or for an agreed start consumes neither window.

## 1. Bound source, evidence and evidence space

- Workbench candidate `a943707905ed67344e4276a82527a3dbb4066247`. Git tree `75e5195da0f916d01c5f96a3f0a1af054dc662a6`. The owner preview serves these product assets. Its separate demonstration server and synthetic input are excluded from live staging.
- Built HTML SHA-256 `d34b8e271e75e680ff6b915132c68705aaae6a68f400c679a2eb9200b5d0437b`. JavaScript `index-CcF64LiB.js` SHA-256 `4e3f79b8dd8b80f4e2aaedc4b50fa323529f09c2b7f88de99298aede2fe7037c`. CSS `index-v1R3EEkW.css` SHA-256 `3192580502207e78fbd5bfbdaf7048f427d4461d157bb4adcd09c16cf985e9c3`.
- The previous real-addon run remains historical. Its [222-file seal](../artifacts/runs/browser-addon-staged.PPqj6w/SHA256.json) was reverified. Seal file SHA-256 `f16e734548c0fc74ad62754af00aa019c04d59040d27305137c5119736262dd7`. The request below borrows the exact task definition and no prior decision, store, offer, model budget, or runtime capability.
- Recount all applicable roots with `artifact_bytes` before work. Keep the existing 512 MiB aggregate cap across the workspace, any new stage, and other declared counted roots. Reserve 40 MiB for the whole offline check phase, 6 MiB for new counted setup and archive receipts, and require 72 MiB free for the live gate after the new store and backup are in place, again before implementation. Limit live run evidence growth to 64 MiB, scratch to 1 GiB, and require at least 4 GiB physical free across the filesystems the guarded launcher checks. A storage or time hold preserves partial output and grants no retry.
- Use only the exact archive below. Before copying, recheck the same-user ownership, closure and absence of open files, current directories or mappings. Recompute the fingerprint. Hold on any difference, unexpected source, space change affecting the reserve, or active writer. The inventory was compared against the path entries in 16 retained run seals. Direct historical links into the listed folders can be recovered with the archive index.

### Exact closed archive

Archive ID `workflow-owner-acceptance-20261106`.

Destination `/home/rich/.local/share/TwichUI-Workbench/evidence-archives/workflow-owner-acceptance-20261106`. The destination was unused at preparation. Archive data stays private and local. The external filesystem had 88,191,696,896 free bytes. Allow space for the archive and a separate restored copy. Require the same 4 GiB physical floor after both.

Inventory SHA-256 `72197a9b68e48eb4af95f5ae5ce3702fa1af88e62a98c2e30cf4c9372624ed46`. This hashes UTF-8 `json.dumps(entries, sort_keys=True, separators=(",", ":"))` from `scripts/evidence_archive.py`'s `inventory` over the exact roots. The inventory binds paths, file digests, sizes, modes, modification times and directories. Files belong to the current owner. The local process reference check found zero matches.

Entry count was 535,213,615 bytes with 1,657,297 bytes free under the cap. Copy and verified removal of the 447 files totaling 125,356,114 bytes projects 127,013,411 bytes of free counted space before receipts and new output. That is 121.13 MiB. After the 40 MiB offline allowance and 6 MiB setup and receipt allowance, projected live headroom is 75.13 MiB. Use actual measured space at every gate.

Use `scripts/evidence_archive.py archive` with these roots and without removal first. Verify every archived file and metadata, verify original-path lookups, then restore into a fresh mode-0700 directory outside counted roots and compare the entire restored inventory to the bound inventory. The archive and successful restore are prerequisites to repeating the same archive operation with `--remove-originals`. That flag may remove only matching workspace copies after the archive index is durable. Reverify archive contents and original-path lookups after removal and retain the index. A failed or partial copy or restore stops this phase. No other directories, databases, receipts, real runs, patches, checked results, credentials or Git screenshot references are approved archive targets.

| Exact workspace-relative directory | Files | Logical bytes |
| --- | ---: | ---: |
| `artifacts/runs/browser-ByxnuE/report` | 34 | 13,593,299 |
| `artifacts/runs/browser-RSBK4X/report` | 33 | 8,698,776 |
| `artifacts/runs/browser-1KtJZN/results` | 4 | 7,476,451 |
| `artifacts/runs/browser-tx0MW7/report` | 26 | 6,780,037 |
| `artifacts/runs/browser-qaOHNI/report` | 11 | 6,323,153 |
| `artifacts/runs/browser-9DbWWS/results` | 8 | 6,208,421 |
| `artifacts/runs/browser-4gCfEV/results` | 7 | 4,841,753 |
| `artifacts/runs/browser-O2I9fd/results` | 3 | 4,061,846 |
| `artifacts/runs/browser-Ur8hlr/results` | 8 | 3,857,720 |
| `artifacts/runs/browser-xnOyfM/results` | 8 | 3,345,159 |
| `artifacts/runs/browser-P0vj4h/results` | 4 | 3,257,106 |
| `artifacts/runs/browser-KS88Yn/results` | 4 | 3,248,168 |
| `artifacts/runs/browser-8SOVWY/results` | 4 | 2,768,785 |
| `artifacts/runs/browser-lZ66Zi/results` | 5 | 2,722,014 |
| `artifacts/runs/browser-JX8oN5/results` | 4 | 2,456,130 |
| `artifacts/runs/browser-U64k1y/report` | 2 | 831,046 |
| `artifacts/runs/browser-zk0JAp/results` | 1 | 293,678 |
| `artifacts/runs/m5-unattended-8ragc598/actual-screenshots` | 32 | 3,875,413 |
| `artifacts/runs/m5-unattended-8ragc598/actual-final/actual-screenshots` | 32 | 3,860,139 |
| `artifacts/runs/review-9487-recovery-ij3um267/control/actual-screenshots` | 32 | 3,855,113 |
| `artifacts/runs/browser-7trH8t/screenshots` | 21 | 3,421,129 |
| `artifacts/runs/browser-r2Vnbs/screenshots` | 6 | 3,313,306 |
| `artifacts/runs/browser-4d7aou/screenshots` | 4 | 3,064,915 |
| `artifacts/runs/browser-XtNDXm/screenshots` | 5 | 2,770,618 |
| `artifacts/runs/browser-dTJ9hw/screenshots` | 4 | 2,689,100 |
| `artifacts/runs/browser-RNtVpX/screenshots` | 3 | 2,546,614 |
| `artifacts/runs/browser-JGKJXq/screenshots` | 4 | 2,497,485 |
| `artifacts/runs/browser-BBlThc/screenshots` | 8 | 2,067,304 |
| `artifacts/runs/diff-presentation-8xweaejo/validation-2/screenshots` | 11 | 1,976,522 |
| `artifacts/runs/m5-unattended-8ragc598/verified-complete/screenshots` | 36 | 1,831,461 |
| `artifacts/runs/review-9487-task-bridge-lqodmz63/screenshots` | 21 | 1,624,269 |
| `artifacts/runs/m5-task-execution-1cf7wj_m/verified-ready/screenshots` | 30 | 1,353,227 |
| `artifacts/runs/browser-sXxiR1/screenshots` | 2 | 1,322,004 |
| `artifacts/runs/m5-result-handoff-nxxlbnkc/verified-safe/screenshots` | 10 | 841,424 |
| `artifacts/runs/m5-result-handoff-nxxlbnkc/verified-final/screenshots` | 10 | 841,349 |
| `artifacts/runs/m5-result-handoff-nxxlbnkc/verified-owner/screenshots` | 10 | 841,180 |

## 2. Offline verification before any real request

Use disposable stores, the existing offline transport fixtures and the configured loopback Chromium suite on ports 5380 and 9287. Use a clean environment with no live model, runtime, or publication configuration. Run the complete Chromium project, then the full visual project, including the updated Work snapshots and unchanged legacy references. The existing browser configuration allocates fresh evidence directories. Use the line reporter to avoid duplicate HTML attachments and preserve normal failure traces, videos and screenshots. A first diagnostic pass may stop at two failures per project. Allow one bounded correction pass for test-only locator, fixture or harness alignment and one final full run of any affected project within the same 45-minute and 40 MiB ceilings. Monitor counted output and stop owned test processes before the reserved live capacity would be consumed. A final pass must run every case with its assertions in force. Material product or safety changes, repeat blockers, visual differences beyond the inspected Work change, or failure to finish hold the live phase.

The backend and frontend automated suites and the production build already passed. Focused browser cases already demonstrated the connected flow with scripted responses. The initial full browser attempt was interrupted, and the last visual comparison stopped at the intentional Work changes. Inspect the final desktop and phone comparisons. Keep the earlier captures and test failures in the archive. A changed reference can only follow an inspected intended result. A passed snapshot compares pixels and does not establish physical-device or in-game behavior.

## 3. One live, local-only map-fade trial

1. Use the existing frozen-stage builder from the approved candidate to create one new private stage and one empty inode-bound SQLite store and verified backup. Use the existing isolated clean addon checkout on `main` at `1caf530212bbc6eaf37e41d668902a391aa936c1`. Preserve the working addon checkout and the earlier staged review and patch. The isolated source, focused test, test runner and harness Git blob IDs respectively are `868b64c9e7447de8cf5af848597a46f9f87c48ee`, `d8f88e3d4c69e3288bd15475ebd8034ac55c3dd9`, `54546b70b1501e4cb17ce2ef1cc826cf61aa72e0` and `6fe668e9268d9f6547f2a01a6b5eb03cc04a4b25`. No templates are required. Use the current verified sampler proof bytes, SHA-256 `9b653ebae882119df3049ebce0c5c785196bf847b51955c4b95610d75edba765`. The builder computes the new manifest from the approved inputs, new absolute paths and new store identity. Record that new fingerprint and read it back independently before preflight and activation. The existing model installation identity must match the earlier verified manifest object, whose canonical JSON SHA-256 is `9cc150102f21bab36eec4a5271c5ae5a163527045df481ec66ac055233a52340`. Use the private credential reference only in the isolated launcher configuration. The earlier manifest SHA-256 `aa174f49950c08e5c112943dd87db42317b05eedb3e4072050febba2c7e307f3` is only the reference for extracting the task, never a live fingerprint for this package.
2. Require 9187 to be free through the existing read-only preview's own expiry. Hold for any listener still present. Confirm owner access over loopback using the existing bounded access check. Leave 9188, 9487 and 8765 under their current owners. Record and announce actual start and cutoff in Mountain time. The 105-minute outer clock starts before live setup. Both owner decisions must arrive by minute 33, leaving 72 minutes for the run and closeout. Require at least 50 minutes before lease admission, 45 before implementation, and the last five minutes for restoration. The later decision's 90-minute clock cannot extend the outer deadline.
3. Submit **Cover the map fade at a single threshold** using the actual browser task form. Prefill the exact test example through the existing intake driver. Pin the canonical task JSON SHA-256 `49a9e776a3039f39a2102088072680cc6878c6df0eef0d921421f8b36de5a2c8` from the earlier manifest's `task` field, using sorted keys and compact separators. Read `!!!TwichUI/map/ViewMath.lua` and the existing test. The sole permitted change is additive assertions in `tests/test_map_viewmath_step.lua`. Confirm the first result of `TwichUI.MapViewMath.Fade(2,2,2)` is `1` and reject `0`. Preserve existing tests, production code, harness and runner. Never feed the historical patch to the model. The owner reviews the actual plan and independently confirms the example, then separately reviews the certified offer and selects **Local implementation only**. The authorization for this package supplies neither browser decision.
4. Revalidate dormant policies, current model identity, confined worker and isolated check boundaries before use. Configure only preferred local `pilot-qwen38-27b` with fallback denied. Use the existing locks and authentication. Allow one planning generation, 120 seconds, 2,600 generated tokens and 24,000 bytes each for request and response, with fixed decoding and reasoning disabled. After the two decisions and the readiness checks, allow one controller-owned exclusive lease up to 60 minutes, one cancellation probe up to 30 seconds with at most 128 counted input and 60 generated tokens, and one implementation attempt. Allow up to 20 implementation exchanges, 120 seconds each, up to 2,500 generated tokens per response with the existing 2,496 wire ceiling, and 24,000 generated tokens total. Aggregate ceiling is 22 generating requests and 26,660 generated tokens plus 21 explicit input counts and the existing bounded metadata, cancellation and restoration exchanges. Require the 32,768-token context gate, at most two shared tool-input corrections, 20 minutes for implementation and 40 minutes through verification. No retry, fallback, second lease or extra probe.
5. Preserve the existing per-check resource boundaries. Use 6 GiB RAM, zero swap, 200% CPU and 64 tasks and 16 MiB per file for model-side checks, and 128 tasks and 128 MiB per file for trusted checks. Keep the established 45,000-character read, 150-line addition, 65,536-byte patch, 8 MiB log, 2 MiB stream, 64 KiB line and 256 KiB assembled-response ceilings. Require the original, known-good, vacuous and wrong-behavior controls before the run offer and again in the fresh implementation workspace. Require Lua syntax, focused regression, independent behavior and all 167 original suite groups. Seal the patch, candidate, check output and complete accounting and run the independent terminal verifier. Keep zero publication and PR preparation records. Preserve partial evidence on a hold.

## 4. Closeout and restoration

On success, refusal, timeout or failure, use the existing controller and watchdog. A stop targets only this task's owned generation. Verify the original model configuration and permissions, normal authenticated access, and rejection of the temporary credential through the bounded existing restoration checks. The standing model-service decision covers the necessary locks, temporary credential, named service grants, Workbench-owned cancellation and guarded restoration for this one separately approved task. It does not authorize a new model, broader access or spending.

Close the isolated write API and worker, settle their execution sessions and owned delegated cgroups, verify grant revocation and withdraw only the matching temporary capability. Recheck preserved listeners and the two addon checkouts. Require an independently verified terminal receipt before calling the service restored. If ownership or restoration is uncertain, stop new work, retain recovery evidence and use only the matching guarded recovery path. Never report restoration on a timed-out or unknown check. Any exact-result review uses a read-only view and ends by the outer deadline. The everyday app remains on its current build. The verified patch is a review result, with installation and publication outside this package.
