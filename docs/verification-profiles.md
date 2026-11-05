# Offline Lua verification profiles

This is a verification prerequisite within unfinished M5. It does not make ordinary
reviewed contracts executable and grants no execution or publication authority.

The trusted recipe format accepts `verification_profile` and `behavior` in addition
to the existing bounded contract, independent oracle hash and runtime fields.
The behavior object has an `id` (lowercase letters/digits/hyphens) and the exact
64-character `review_binding` digest supplied by trusted preparation. Existing
recipes without a profile retain their original positive/negative requirements.

| Profile | Prepared controls | Candidate acceptance |
| --- | --- | --- |
| `lua-regression-tests-v1` | Original fails adequacy; known-good additions pass; vacuous and wrong-behavior additions fail adequacy. Every control's ordinary syntax/focused checks and complete suite pass. | Existing test files only gain assertions; production/harness unchanged; independent approved-behavior adequacy oracle and all existing checks pass. |
| `lua-offline-bugfix-v1` | The approved-behavior reproducer fails on the original base while existing checks/suite pass. | Reproducer passes on the frozen fix; existing scope/regression/suite/artifact checks pass. No hand-written known-good implementation is required. |

The current runtime retains its green-baseline prerequisite. Bugs whose original
existing suite is already red need separate supported baseline analysis; this
profile does not waive or reclassify those failures. Offline results establish no
live-client behavior. Required real-client evidence remains a separate hold.

The four steps have distinct owners and meanings:

1. `select(category)` returns versioned requirements, `status=not_run` and execution
   authority false. Ordinary regression proposal preparation consumes this selection
   and persists it in the existing contract; historical contracts omit the new field.
   Bug-fix selection is available to trusted recipe preparation, not a new public
   bug intake path.
2. `prepare(runtime, control_patches)` uses a fresh isolated existing Runtime and
   task-specific reviewed oracle/control bytes. It invokes the existing fixed
   checks through `Runtime.check`, scopes every non-base control with `Runtime.scope`,
   freezes files/patches before checks, preserves raw results and returns evidence.
   It does not certify its own return value or call runtime activation/preflight.
3. `certify(runtime, bundle)` evaluates those frozen facts without running commands
   from the task or changing authority. It returns `certified`, `rejected`, or
   explicit `hold`. The existing `Runtime.record_controls(boundary)` records a
   create-only certificate only after profile certification and boundary evidence
   pass. `controls_valid()` recomputes current validity, including stored raw checks.
4. Existing human run-budget/grant/worker owners still authorize any execution.
   Scope approval, a selected profile, a textual proposal and a certified verifier
   cannot create that permission. Existing operational activation remains separate.

The independent oracle must emit exactly one final line of the form
`WB_BEHAVIOR <behavior-id> PASS` with exit 0 or `... FAIL` with exit 1. This line
must follow its actual approved-behavior assertions. An ordinary Lua error, unrelated
assertion, timeout, mismatched identity/exit status or duplicate/missing verdict
cannot certify reproduction. The marker is a transport protocol, not an automatic
semantic proof: trusted independent review must establish what the oracle measures,
and the regression control matrix must demonstrate its discrimination. Candidate
checks alone never certify their own adequacy.

Certification pins the recipe (including review/behavior), base and clean source,
base manifest, both oracle copies, profile requirements, suite inventory and checker
source hashes. It verifies every stored raw check, canonical/control-input patch,
control tree and frozen changed file. Changed inputs need fresh preparation in a
new evidence directory; old records remain historical. Candidate checks revalidate
before using cached feedback; final frozen manifests bind the certificate hash.
Frozen-artifact integrity remains distinct from current execution eligibility.

Task-specific work remains: determine approved behavior and scope; prepare and
independently review the oracle/reproducer; supply meaningful regression positive,
vacuous and wrong-behavior controls; resolve real-client requirements; and verify
current repository/destination facts. No automatic semantic-oracle generation or
new model, service, authorization, fallback or publication path is provided.

The next package must associate an actual current reviewed ordinary contract with
these independently certified bytes and existing offers. A caller-supplied digest
alone cannot establish that association. Existing fixed templates still serve their
old tasks; no new backlog-specific template was registered by this package.

## Consumption by reviewed offers

A trusted preparer can call `real_recipes.from_review(connection, ref, ...)` with
an actual applied scope-review/task reference and independently reviewed task data.
The recipe comes from the current typed contract, rather than a supported-task
registry. Prepare its oracle/controls with the existing Runtime/profile functions,
then call `task_budget.register_reviewed_offer(engine, ref, runtime)`. Registration
reconstructs the canonical Runtime, checks current certification, and stores its
association in the existing offer owner. Both profiles use the same functions.

The contract must have complete committed evidence for framework/read/write files,
the current clean configured checkout with HEAD equal to local main and reviewed
base, compatible exact fixed checks, default bounded resources and a draft PR to
Twich-Team/TwichUI/main. Remote freshness is still independently checked by the
publication owner; local main is not a claim about current GitHub main. Multiple
focused test commands or other runtime shapes currently hold rather than silently
skip reviewed checks. Client-dependent acceptance remains outside these offline
profiles and requires separately reviewed client evidence.

`task_session.preparation_template` consumes the current certified offer directly;
`task_session.provision` runs fresh profile preparation and existing boundary checks
before any separately authorized activation. Frozen original evidence remains a
required live reference, not disposable output. Changed review/request/task mapping,
source/base/origin, recipe/profile/checks/limits/destination or certified bytes
invalidates selection. Superseded evidence cannot regain execution eligibility by
losing the newer certificate. Historical outputs remain preserved.

Task-specific oracle/reproducer review and regression controls remain manual. Bug
contracts currently require trusted manual preparation before normal human review.
A first explicit budget after scope-only review can use the existing trusted
owner-instruction continuation ingress; there is no browser command for that step
yet. Neither these functions nor profile certification authorize model execution.
