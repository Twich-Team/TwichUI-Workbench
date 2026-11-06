# Proposed real-model map-fade trial after offline acceptance

November 6, 2026, Mountain time. This is a new authorization request.
The previous offline window authorized only verification. No live trial
or archival is active.

Approve one package to preserve the named older browser files in a
verified local archive, remove only the matching originals after proving
they can be restored, and run one isolated map-fade test task with the
existing local Qwen model. Allow up to 20 minutes for archival and a
separate 105-minute trial window. In the first 33 minutes of the trial,
you’ll submit the prepared request, review the fresh plan, and separately
decide whether to allow the local run. Other local model users may have
to wait. The patch stays local for review. If the attempt stops, retain
the evidence, close the isolated services and verify normal model access.

## Bound candidate and proof

- Verified product checkpoint `17ad1ac4658b26dcb3eba3fa44891a53d8310c97`.
  The browser verification checkpoint
  `78095d16cb0d6f0d4fe5b3daece3cf2cf19955d4` changed only browser assertions.
  Application source, dependencies, migrations, guarded launcher code and
  visual baselines match the frozen candidate's tested inputs.
- All 107 Chromium cases passed across the two reconciled windows. All
  10 visual cases passed. All 12 desktop and phone snapshot captures
  matched their tracked reference PNGs byte for byte. See the
  [offline acceptance receipt](../.local/runs/offline-acceptance-finish-20261106/closeout.json),
  SHA-256 `97f2ce6cc778569a415bc970e1decd7a21a40b1ff169df2aa05496891c512be7`.
  This is evidence of the reviewed interface and controlled task path.
- Use only the private dormant
  `/home/rich/TwichUI-Workbench/.local/workflow-acceptance-20261106/browser-demonstration-manifest.json`,
  SHA-256 `507049cbc6f2a28c858fb2c3321f44d1a5593af32249548510d0506637f5b1ff`.
  Read-only preflight reverified all 318 inputs. The empty store and separate
  backup both have SHA-256
  `04906fe401b8c6309a617e33e2fde40d337b53bbc19ba2b69f29a15d4427d5ee`.
  Reverify the manifest, store identity and dormant policies before activation.
- UI artifact SHA-256 values are
  `f07eeeb48ad8a33c246e833741e021e30d1354c55d956441dc988b0dea35974e`
  for the HTML, `5a3340ad464e0c1a034ef5a70b68cb516dbe63eaa770ba380f50edfa75c178eb`
  for `index-4ozvO3nT.js`, and
  `0e1d437d6fd9140523d7bd4f1275bbd6ff37395c9baeaeab5975145273bdc427`
  for `index-BPx3MuYY.css`. These are the reviewed product assets.
- Preserve the sealed historical real-addon run at
  `artifacts/runs/browser-addon-staged.PPqj6w` and the original retry
  trace under `artifacts/runs/browser-EnQa9m`. The earlier real result
  does not supply a proposal, implementation, permission or result for
  this trial.

## One exact archive for capacity

Counted usage across the workspace and the dormant stage is 492,086,553
bytes under the 536,870,912-byte cap. Headroom is 44,784,359 bytes
(42.71 MiB). The 72 MiB live gate cannot pass at this count.

The only proposed archive ID is `workflow-verified-map-fade-20261106`.
Destination
`/home/rich/.local/share/TwichUI-Workbench/evidence-archives/workflow-verified-map-fade-20261106`.
Independent restore check destination
`/home/rich/.local/share/TwichUI-Workbench/evidence-archives/workflow-verified-map-fade-20261106-restore-check`.
Both were unused at preparation. Retain the durable workspace index at
`artifacts/archive-index/workflow-verified-map-fade-20261106.json`.

The 22 directories contain 197 generated files totaling 46,009,747 bytes.
Their inventory SHA-256 is
`cd863b13fc3a9a9d74431f7731d3353d0accff4a5cef8db10b44b2399c5bf9bd`,
computed from the canonical sorted compact JSON returned by
`scripts/evidence_archive.py:inventory` for the exact list below.
The inventory binds content, sizes, paths, modes and timestamps.
The listed roots are closed browser output. Ownership checks and the
same-user process and lsof scans found no archive target references.
Kernel restrictions applied to the existing systemd, PAM and SSH
session managers. The selection excludes tracked files, paths in retained
run seals, the original retry trace and this window's new evidence.

Before copying, repeat the closure and fingerprint checks and remeasure
both logical and physical space. Two copies total 92,019,494 logical
bytes. Reserve 128 MiB physical space for the copies and overhead and
require at least 4 GiB free after both. Archive filesystem free space
at preparation was 87,938,281,472 bytes. Use the existing local archive
operation without removal, verify the whole archive and all original-path
lookups, then independently restore to the named new private directory
and compare its full inventory. Repeat closure and inventory checks on
the originals. Only after all checks pass, use the exact same archive
operation with matching-original removal. Reverify the archive, restored
copy and all lookups. Stop on any mismatch, partial result, open reference
or expired archive window. Preserve all copied evidence and the index.

The projected headroom after verified removal is 90,794,106 bytes
(86.59 MiB). Allow up to 6 MiB for setup and receipts, leaving a projected
84,502,650 bytes (80.59 MiB). Remeasure actual space before activation
and before implementation. Require at least 72 MiB at both gates and
at least 4 GiB physical free on each filesystem the launcher checks.
Limit new trial evidence to 64 MiB, starting the owned stop by 60 MiB
to preserve 4 MiB for closeout. Keep scratch at or below 1 GiB.

| Exact workspace-relative directory | Files | Logical bytes |
| --- | ---: | ---: |
| `artifacts/runs/browser-9DbWWS/generated-expectations` | 10 | 811,419 |
| `artifacts/runs/browser-9DbWWS/screenshots` | 4 | 3,001,736 |
| `artifacts/runs/browser-BBlThc/task-to-pr` | 8 | 810,630 |
| `artifacts/runs/browser-EZsMjc/screenshots` | 6 | 1,181,273 |
| `artifacts/runs/browser-F3oHHl/results` | 3 | 2,045,036 |
| `artifacts/runs/browser-IGH8WQ/results` | 3 | 2,059,840 |
| `artifacts/runs/browser-UM92zU/results` | 3 | 1,927,451 |
| `artifacts/runs/browser-WRpnch/generated-expectations` | 10 | 886,087 |
| `artifacts/runs/browser-bOhZoS/automatic-scenarios` | 10 | 831,796 |
| `artifacts/runs/browser-bOhZoS/generated-expectations` | 10 | 886,453 |
| `artifacts/runs/browser-cocOG2/results` | 25 | 5,324,301 |
| `artifacts/runs/browser-cocOG2/screenshots` | 25 | 3,327,671 |
| `artifacts/runs/browser-fVuUA9/m5-implementation` | 16 | 2,670,697 |
| `artifacts/runs/browser-gGLSUC/m5-plain-language` | 12 | 1,029,022 |
| `artifacts/runs/browser-gkl0Ie/results` | 6 | 4,292,589 |
| `artifacts/runs/browser-gkl0Ie/screenshots` | 9 | 1,965,351 |
| `artifacts/runs/browser-kOhmUp/results` | 3 | 1,929,339 |
| `artifacts/runs/browser-pHjhTk/results` | 3 | 2,045,014 |
| `artifacts/runs/browser-qGGxBr/screenshots` | 12 | 1,240,501 |
| `artifacts/runs/browser-sU2xgt/results` | 3 | 1,852,434 |
| `artifacts/runs/browser-uGKz95/m5-plain-language` | 12 | 1,028,828 |
| `artifacts/runs/browser-uGKz95/results` | 4 | 4,862,279 |

## One isolated trial and its limits

1. Agree on owner availability before the clock starts. Require the
   isolated loopback port 9187 to be free, confirm owner access with the
   bounded access check, and announce the actual start, minute 33 cutoff
   and 105-minute deadline in Mountain time. Use only a new private
   `artifacts/runs/workflow-owner-acceptance-20261106-live` evidence
   directory. The everyday app on 9487, port 8765 and any existing
   preview keep their owners and scheduled closure. Leave at least
   50 minutes before lease admission, 45 before implementation and the
   final five for restoration. The later decision's 90-minute clock cannot
   extend the outer deadline. Hold if any requirement or cutoff is missed.
2. The owner opens `http://127.0.0.1:9187/demo/request` when the launcher
   announces it, reviews **Cover the map fade at a single threshold** and
   presses **Submit request** in the normal Workbench form. Use the frozen
   task JSON SHA-256
   `49a9e776a3039f39a2102088072680cc6878c6df0eef0d921421f8b36de5a2c8`
   and the clean isolated addon at
   `/home/rich/TwichUI-Workbench/.local/real-addon-readiness.uQHL9i/addon`
   on `main` at `1caf530212bbc6eaf37e41d668902a391aa936c1`.
   Read `!!!TwichUI/map/ViewMath.lua` and the existing focused test.
   The sole permitted change is additive assertions in
   `tests/test_map_viewmath_step.lua`. Verify the first result of
   `TwichUI.MapViewMath.Fade(2,2,2)` is `1` and reject `0`.
   Preserve the harness, production files, other tests, working checkout
   at `/home/rich/twichui-pilot/checkouts/base-1caf53021` and installed
   addon. Keep the historical patch out of model inputs.
3. After one fresh plan, the owner checks **The test examples match
   what I asked for** and selects **Approve plan** only if the proposal
   matches. After independent preparation and a new offer, the owner
   verifies **Keep the result local**, checks **I reviewed what this run
   allows**, and selects **Approve local run** only if the offer matches.
   Package authorization supplies neither browser decision. No scripted
   approval, saved proposal replay or implementation before the second
   decision.
4. Verify the installed model identity matches canonical SHA-256
   `9cc150102f21bab36eec4a5271c5ae5a163527045df481ec66ac055233a52340`
   and the sampler proof SHA-256
   `9b653ebae882119df3049ebce0c5c785196bf847b51955c4b95610d75edba765`.
   Use preferred local `pilot-qwen38-27b` only, with no fallback.
   Allow one planning generation, 120 seconds, 2,600 generated tokens,
   24,000 bytes each for request and response, fixed decoding and
   reasoning disabled. After both decisions and all readiness gates,
   allow one guarded exclusive lease up to 60 minutes, one cancellation
   probe up to 30 seconds with at most 128 counted input and 60 generated
   tokens, and one implementation attempt. Allow up to 20 implementation
   exchanges, 120 seconds each, 2,500 generated tokens per response
   (2,496 wire ceiling) and 24,000 generated tokens total. Aggregate
   ceiling is 22 generating requests and 26,660 generated tokens plus
   21 explicit input counts and bounded existing metadata, cancellation
   and restoration exchanges. Keep the 32,768-token context gate, at
   most two shared tool-input corrections, 20 minutes for implementation
   and 40 minutes through verification. Hold without a retry or extra probe.
5. Keep the existing check isolation and limits. Allow 6 GiB RAM,
   zero swap, 200% CPU, 64 tasks and 16 MiB per file for model-side checks,
   and 128 tasks and 128 MiB per file for trusted checks. Preserve the
   45,000-character read, 150-line addition, 65,536-byte patch, 8 MiB log,
   2 MiB stream, 64 KiB line and 256 KiB assembled-response ceilings.
   Require original, known-good, vacuous and wrong-behavior controls
   before the run offer and again in the fresh implementation workspace.
   Require Lua syntax, focused regression, independent behavior and
   all 167 original suite groups. Seal the patch, checks and accounting
   and use the independent terminal verifier. Keep zero publication
   and PR preparation records.

On every exit settle the owned task, cancel only its generation if needed,
and verify original model configuration and permissions, normal authenticated
access and temporary credential rejection through the existing controller
and watchdog. Close the isolated write API and worker, confirm owned cgroups
settled, revoke the matching grant and withdraw the matching temporary
capability. Recheck the protected listeners and checkouts. Retain any checked
patch locally for review. A read-only result view, if opened, closes by the
outer deadline. Hold for any uncertainty about ownership or restoration
and use only the guarded recovery path. Installing Workbench, applying
an addon patch, publication and any further trial each require separate
authority.
