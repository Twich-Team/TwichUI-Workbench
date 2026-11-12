# Combined feature verification design review

November 12, 2026, 11:45 AM MST. Recommendation only; stop for owner review.

Following the Project Working Philosophy: preserve the two checked implementations,
reuse trustworthy evidence, and add independent observations only where the actual
request remains unproved. No model request, feature edit, application correction,
deployment, publication or acceptance transition was performed in this review.

The failed control has a precise cause: its left click targets the continent's
center, but its assertion expects Westfall's zoom. The feature remains unaccepted.
The recommended correction is to let the existing combined verifier compose
audited native checks, saved provenance and a small independent behavioral supplement,
instead of requiring a newly generated omnibus program to represent every clause.

## Acceptance authority and evidence

The original saved request, intake `5d62e6bc-1aa8-48f4-a3e1-4d05ade2d480`, says:

> When I close and reopen the TwichUI map during the same login, remember the zoom level I chose. Keep the existing first-open zoom and existing controls. Reset the remembered zoom when I reload or log out; do not add a setting or save it between sessions. Include checks for restoring the selected zoom and for preserving the default when no zoom has been chosen.

Comparison intake `8a11c7e1-b52c-43ff-a9c3-234a75a16bd4` repeats that request and
adds the owner's storage-first checkpoint, no persistent writes/new controls,
original/candidate differential, full regression suite and preserved history.
Its saved approved plan is `bc190018-0b88-4a02-b07f-53284c97f7ec`. Plan clause
numbers below are the actual indexed behavior/acceptance clauses, not implementation
claims. The original request remains the authority where a plan is incomplete.

Evidence inspected directly:

- Final read-only comparison backup: `combined-verification-20261112/final-comparison.sqlite3`.
  Parent job `8c063c44-393e-4b70-8499-3fd72d08cb67` retains two checked contributions
  and no combined acceptance.
- Storage execution `fe4b9dd7-cdd8-565c-812c-20b0ac0fce18`, candidate
  `2301e043d7e8f6d1b31e46e390ccf03130e54219c40bf8940d05abb116f98dec`,
  handoff commit `9e6864422c66414072193c3fc31ccd9fd3e60be6`.
- Capture/restore execution `2ed99020-a79c-550d-b4ec-6ec74b3228fb`, candidate
  `243fbe2fdea978b96852000a4690babd4416ca93fa9953168b8d4324b06225a8`,
  handoff commit `ba09e94fe3079b4f3a94795a1e1d00d6580bf450`.
- Both handoffs are clean, match their recorded commits and match all 1,116 paths
  in their respective native candidate manifests. Each saved result contains
  passing syntax, focused, fixed assertion-runner and 178/0 full-suite receipts.
  The original-with-new-tests differentials use the original base for step 1
  and the checked storage predecessor for step 2. They are distinct comparisons.
- Retained blind proposal `rehearsed-program.json`, compiled `rehearsed-oracle.lua`
  (SHA256 `545622ab57b0ab956297ee1fb65bed2ff71ca899f3157dc592f5fdf92a1710ae`),
  original diagnostics, correction history and compiler code. The proposal is
  unaccepted and has no recorded model review or candidate execution.

Evidence directories above are under `/home/rich/.local/share/TwichUI-Workbench/`.
New review receipts and original-only diagnostic copies are under
`verification-design-review-20261112/`. Earlier evidence is unchanged.

## Exact failed assertion and its role

Phase 3, line 31 of the retained program body, asserts:

```lua
ok=ok and Q.near(Q.zoom(),Q.mv.FitZoom(1436))
```

The preceding actions are:

```lua
Q.w:SetMapID(1436) -- Westfall, whole
Q.center(); Q.s:OnMouseDown('RightButton'); Q.s:OnMouseUp('RightButton')
-- right click frames Eastern Kingdoms, whole
Q.center(); Q.s:OnMouseDown('LeftButton'); Q.s:OnMouseUp('LeftButton')
```

`Q.center()` assigns screen coordinates `(600, 467)`. After the right click,
the view is centered on Eastern Kingdoms. At the subsequent left click,
`CursorWorld()` is `(0.7, 0.35)` and `MapWorld.Deepest()` returns `1415`
(Eastern Kingdoms). That point is outside Westfall. Actual zoom stays
`3.3333333333333`; `FitZoom(1436)` is `16.666666666667`.

Original `View.lua:585` onward selects the next deeper map in `ChainAt(pointer)`.
It correctly does nothing when no deeper map exists at the pointer. The original
test's click scenario (`tests/test_map_explore.lua:615` onward) calls `pointAt(wu,wv)`
again after every framing action. The generated control incorrectly substitutes
a fixed screen center for that changing world target.

This is a **preserved-behavior assertion**: phase 3 declares `expected=true` and
`incorrect=true`. It is neither a new zoom-memory requirement nor an intentional
negative control. Its false result is outside both reviewed results, so the
compiler correctly emits `outside-reviewed-results` and exits 2. Phase 2's false
result on original is the separate, intended missing-feature observation.

Read-only diagnostics on the exact frozen original establish:

| Diagnostic | Observed result |
| --- | --- |
| Unmodified retained oracle | Phase 1 expected; phase 2 incorrect; phase 3 outside-reviewed-results; exit 2 |
| Instrument each phase 3 predicate independently | Line 31 false; every other evaluated control predicate true, including Westfall markers |
| Place the second pointer over Westfall using the original test's coordinate calculation; keep expectations unchanged; stop after phase 3 | Phase 3 expected; exit 1 remains the intended missing zoom-memory feature on original |
| Unmodified original focused map test | Exit 0, all original map scenarios pass |

These are diagnostic observations, not accepted checks or a replacement program.
The targeted input correction has not been saved into the preparation journal.

## Environment defects distinguished from the failed assertion

The earlier `probe-original.py` and `probe-clock.py` execute in the surrounding
readiness checkout. At review, four disclosed files there differ from the proposal's hashes:
`tests/harness.lua`, `Core.lua`, `Persist.lua` and `Switches.lua`. This review initially
reproduced there, then detected the mismatch and repeated the diagnostics in the
actual frozen snapshot:

`/home/rich/TwichUI-Workbench/.local/real-addon-readiness.uQHL9i/addon/.git/workbench-source/snapshots/7fd7be8b5ab36870603518bfc458aef132299989`.

That snapshot is clean at the exact base, and all nine disclosed hashes match.
The same click assertion fails there. The earlier probes lack exact-source
qualification and cannot establish acceptance; the surrounding checkout's drift
is **not the cause of this control failure**. The combined Runtime already takes its original source
from the bound specification; the diagnostic shortcut must not replace it.

There is also a timing weakness in the proposal. Its `CLOCK=CLOCK+dt` writes to
the phase scope, while the committed client's `GetTime()` closes over bootstrap
globals. The diagnostic observes `CLOCK=1000.5` while `c.GetTime()` remains 1000.
Easing advances from the elapsed argument and passes; clock-dependent following
is not equivalently driven. Reuse the committed fixture's real timing mechanics
or explicitly validated equivalent inputs in any supplement. This review does
not recommend changing the general compiler's global-write semantics.

The earlier 16,032-character evidence limit was a separate compiler defect,
already corrected locally to accommodate the selected startup. The retained
57,290-byte oracle now compiles under the unchanged 65,536-byte final bound.
Neither that size defect nor a missing dependency explains line 31.

## Requirement coverage matrix

“Native” below means independently executed frozen tests, not independently authored
behavioral expectations. Both step oracles are exactly the generic
`xpcall(dofile("test_map_explore.lua"))` runner from `repair_cycle.oracle()`.
Their task-specific additions came from the implementation model. This is useful
evidence and valid step acceptance, but not a separate blind oracle for every
feature requirement. A green suite cannot fill the gaps below by implication.

| Original requirement or owner constraint | Plan clauses | Verified evidence | Remaining feature acceptance check |
| --- | --- | --- | --- |
| Restore the latest chosen zoom after closing/reopening in one login | 1, 5 | Step 2 native lines 1035–1044 exercise both wheel directions and three reopens each | Run the already blind-authored phase 2 behavior on the final combined source; its original failure is established, candidate result is missing |
| Keep first-open and repeated no-choice default zoom | 0, 6 | Step 2 lines 1024–1030 check three map openings; original suite preserves default; blind phase 1 passes original | Execute that independent default control on the final combined source; retain initialization versus user-input distinction |
| Reload discards an actual remembered player choice | 2, 7 | Step 1 lines 1053–1063 prove fresh module-local storage; retained by step 2 | Fresh complete client/module initialization with SavedVariables copied after a real choice, then inspect displayed default and repeat reopening. Reloading only View.lua does not prove the whole interaction |
| Logout discards a captured choice | 2, 7 | Step 2 lines 1059–1062 check actual PLAYER_LOGOUT, empty storage and displayed default; step 1 covers storage clearing | Include actual selected-zoom/logout/default in the independent supplement; no invented PLAYER_RELOAD |
| No saving between sessions or persistent zoom writes | 4, 7 | Step 1 deep-compares TwichUIDB for direct storage/reset; final diff has module locals and no new persistence calls; step 2 checks DB identity and preexisting map preferences | Deep equality/serialization of the entire DB around actual control/reopen/logout actions. Step 2's one-way preference loop cannot detect new keys elsewhere; explicit smooth-setting changes must be outside snapshots or restored |
| No setting or new UI control | Owner constraint | Only View.lua and existing map test changed; settings sources unchanged; step 1 verifies no new frames from storage operations | Bind the audited combined diff/file union to acceptance. Existing settings defaults/suite plus that diff suffice; no second settings runtime needed |
| Preserve existing controls, easing and limits | 3, 8 | Original suite covers control behavior; step 2 explicitly checks remembered eased endpoint even if closed mid-animation | Independent checks must include memory after the existing non-wheel zoom actions (+/−, World/Back, map zoom clicks), and ordinary limits/easing controls. Existing blind phase 3 checks immediate controls but not reopening after each |
| Remember zoom without restoring an old pan or map | 3 | Step 2 lines 1054–1058 verify original map-change placement and reopened zoom | Assert actual new opening map AND center after changing map/pan; zoom equality alone does not establish either. Blind phase 2 contains a usable proposed map/center assertion |
| Preserve map content and restoration ordering, especially Westfall | 3, 8 | Original Westfall dungeon:36/dock:3/cave:101 assertion remains; both suites pass; diagnostic phase 3 marker predicate is true | Check content after restoration at a deliberately equivalent whole-Westfall view. Do not require every marker at zooms where normal visibility differs |
| First checkpoint is storage-only before integration | 4 | Step 1 frozen production diff is seven additions, no deletions, only View.lua; direct storage/reset assertions; step 2 consumes its checked handoff | Revalidate those original receipts and ancestry; no second storage implementation or scalar behavior phase can prove historical ordering |
| Original/candidate focused checks and complete addon suite | 9 | Each step has differential and 178/0; step 2 includes storage predecessor, but its differential baseline is that predecessor | One feature-level receipt must bind original base, exact final combined tree, focused/independent checks, controls and full inventory. Keep existing combined baseline/candidate suite execution; do not replace it with “two green steps” |
| Preserve original Qwen task, history, permissions and publication hold; one local review package | Owner constraint and delivery stage | Existing backup/parent records and checked source provenance retained; no combined result/package exists | App-owned identity/history/publication checks and a local package after acceptance. These are not Lua behavioral assertions |

Two setup insertions in step 2 need explicit review when reusing the native suite:
`tidy()` now fires PLAYER_LOGOUT, and another logout precedes a fresh cave/label
scenario. No old assertion was deleted. Those additions isolate prior remembered
choices, but alter scenario inputs. Keep that provenance visible and retain a
same-login integration check; do not call the entire candidate test file unchanged.

## What combined verification must prove

The final source must be exactly the approved composition of the two checked
contributions, with the original base unchanged. The actual requested behavior
must work together: select, reopen repeatedly, change choice, keep appropriate
opening placement, reset on logout/fresh initialization, and avoid persistence.
Original defaults, controls and content must remain correct at comparable states.

Each observation must state its role: preserved behavior passes original and
candidate; intended new behavior fails original for a semantic reason and passes
candidate; a deliberately broken or vacuous control is rejected. A fixture crash,
missing helper, bad input or unqualified original observation cannot serve as the
intended failure. A single failing differential cannot certify every requirement.

Acceptance also needs exact full-suite inventory, native receipts, source/contract
hashes, checked ancestry, current scope/permission and immutable failure history.
Step completion and feature completion remain separate. The local package follows
the feature receipt; publication and in-game proof remain distinct boundaries.

## Smallest recommended implementation correction

The current architecture does require a complete new generated program: preparation
calls the model for `Response` and `Review`; `feature_checks.compile()` requires
every clause index to occur in phases; `feature_verification.specification()`
requires that exact compiled proposal and ready review before local execution.
There is no evidence-composition path, even for already checked native behavior
or historical/full-suite obligations which Lua cannot itself establish. This
requirement is an implementation choice, not a requirement in the original request.

Make a narrow extension within these existing owners:

1. Permit an explicit composed acceptance contract. Every original requirement
   must name either an audited existing behavioral assertion, a supplemental
   independent observation, or an app-owned provenance/diff/suite control. Bind
   actual assertion source, roles, hashes and receipts; no checkbox-only coverage.
   Reused implementation-authored tests retain that authorship classification.
2. Reuse the reviewed existing suite/assertions and saved step receipts. Keep
   source/ancestry checks and the existing model-free combined Runtime, original
   controls, original/candidate execution, full suites, confinement, cancellation,
   integrity, retention and publication gates. The generated-complete-program
   path can remain available, but must not be the only entry.
3. Attach a small independent supplement for the uncovered rows. Reuse validated
   parts of the original-only blind proposal for default and wheel/reopen behavior;
   qualify their candidate results. Correct its click input explicitly if keeping
   that control, or assign the same behavior to the original native click test
   with a recorded rationale. Preserve the rejected program and its diagnostics.
   Have any new independent assertion author receive only original source,
   requirements and original-only feedback. This reviewer has seen the candidate
   and must not be represented as a blind new author.

For this feature the smallest supplement is three groups of readable observations:

- Existing no-choice and latest-choice reopen observations, with opening map/center
  checks and a small parameterized exercise of the other existing zoom controls.
- A real selected zoom followed by logout, and separately a fresh complete session
  with copied SavedVariables; check displayed defaults and repeated no-choice opens.
  Compare the entire saved DB around these actions.
- Whole-Westfall content after reopening at an equivalent chosen zoom; reuse the
  existing dungeon/dock/cave assertions and native preserved controls.

This changes the coverage composition rule, not the required evidence. Do not
merely bypass `covered == needed`, set `ready=true`, relabel old evidence as a new
combined run, or run only phase 2 and call the feature complete. No new provider,
planner framework, persistence layer, permission model or addon edit is needed
to implement this verification correction. Correcting line 31 alone repairs the
immediate control but does not close the coverage gaps.

## Tests required for that correction

1. Reproduce this exact original control failure and its world-target explanation
   through the actual 450-line bootstrap/compiler. Correct input must satisfy
   the same expectation; incorrect input must still fail. Assert exact original
   hashes; reject the historical surrounding checkout. Retain existing size bounds.
2. Exercise composed admission through the actual preparation/specification/runtime
   path using saved responses and disposable records. It must make zero provider
   calls when valid checks are supplied. Require every requirement to have real
   evidence; reject missing, duplicate/misattributed or irrelevant coverage.
3. Reject changed assertion/oracle/source/receipt hashes, wrong feature or ancestor,
   altered controls, substituted test commands, missing inventory and any original
   fixture error. A vacuous green suite or constant-pass supplement must not satisfy
   a changed-behavior obligation. Preserve an intentional wrong-expectation control.
4. Prove sensitivity to the identified gaps in disposable controls: missing restore,
   retaining choice after logout/reload, unintended persistence and wrong restored
   map/center/content must each be rejected by the corresponding observation.
5. Verify native assertion reuse does not hide the new logout setup or permit
   deletion/weakening of old assertions. Preserve a same-login integration case.
   Validate equivalent pointer and timing inputs, including fresh-session reset.
6. Retain existing Stop, expired/stale scope, fencing, interruption/recovery,
   no-duplicate-execution, immutable failure/accounting and publication-hold tests.
   A checked child, original-only diagnostic or partially complete supplement must
   never create feature completion or a package.
7. Run the corrected focused checks and complete addon inventory on the bound
   original and exact combined candidate through the existing native owner. Run
   affected Workbench verification/recovery tests; any later release still needs
   its exact complete release gate. This review is not that release verification.

## Remaining acceptance gaps and stop state

The independent candidate observations, whole-client reset/DB/control integration
checks and exact feature-level receipt are absent. The final local package is
absent. Actual game reload/logout and rendering remain unverified; offline
acceptance should say so. They need not be silently promoted to game proof or
used to demand unavailable game access for an explicitly offline result.

The checked implementations remain valuable, intact inputs. Their correctness is
not disproved by the malformed click control, and their passing suites do not
prove all missing behavior. No evidence currently justifies full feature acceptance.

Installed build remains `ade9295` / deployment 127 per the last verified receipts;
this review did not change services or the application. Existing corrections remain
uncommitted/uninstalled. Zero real Feature Workshop features have reached finished
delivery. Owner review of this recommendation is the next decision; no model call,
implementation continuation or deployment is queued by this report.
