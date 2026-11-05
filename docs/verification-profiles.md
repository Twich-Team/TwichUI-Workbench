# Offline Lua verification profiles

This is a verification prerequisite within unfinished M5. Reviewed offers consume
it through the existing bridge; certification grants no execution/publication authority.

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

Task-specific reasoning remains: map requested behavior to a fixture/operation,
propose concrete inputs and independently confirm expected observations, resolve
real-client requirements and verify current repository/destination facts. The
declarative compiler described below generates regression oracle/control code for
its supported shapes. No new model/provider/service/fallback/publication activation
was added. Existing fixed templates retain their meanings; no backlog recipe added.

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

Bug contracts and unsupported oracle shapes still require trusted preparation. A
first explicit budget after scope-only review can use the existing browser combined
reviewed-offer decision or trusted owner-instruction continuation. Neither profile
certification nor registration creates that budget or authorizes model execution.

## Generated regression scenario controls

The next reusable slice accepts bounded declarative examples as proposed data in
the existing typed contract. A request/base/test/feedback-bound internal structured
response seam is exercised with controlled model data only. There is no default
ordinary-request inference or automatic application preparer dispatch.

For one existing focused test, up to three examples describe a returned module
function or a harness client's exported operation, scalar arguments, bounded setup
and a scalar first return or client field plus a meaningful incorrect outcome. The browser exposes actual fixture,
operation, inputs, expected outcome and counterexample to reject and requires a separate expectation digest
in the human scope decision. This is independent semantic review, not certification.
Unclear mappings can still require a technical reviewer. Missing intent should be
clarified with the domain owner; it must not be inferred from current source alone.

`trusted_preparation.prepare_regression` compiles data into oracle bytes and
positive/vacuous/overly permissive wrong-behavior additions, builds the normal recipe,
and invokes the same provision/certification/registration owners. No task-specific
registration code or hand-authored passing solution is needed. The positive is a
generated control, not the accepted task implementation. The original control must
still fail adequacy, ruling out examples already covered by existing tests.

The oracle checks the proposed expectation against committed behavior and requires
normal tests to pass while an exact-input reviewed incorrect observation causes an assertion
failure. Every example must distinguish its own fault. The ordinary checks/suite
still pass on all controls, and infrastructure failures cannot count as behavior.
Wrong expectations, unsupported instrumentation/setup and unrelated errors yield
specific example-bound holds. Candidate globals cannot replace oracle verdict/exit
primitives; the existing OS/boundary checks remain mandatory and separate.

Choosing a meaningful incorrect outcome requires reasoning and independent review;
the compiler must not invent an arbitrary changed value and call its rejection
adequate. A same-length wrong-case counterexample rejects a length-only candidate
for case-normalization coverage.

No generated oracle is correct merely by being generated. Independent review of
what the examples mean and the unchanged executable control matrix are both needed.
Timing, complex reference/multiple-return expectations, unsupported globals/loaders
and real-game behavior require further supported evidence, not relabeled certification.
