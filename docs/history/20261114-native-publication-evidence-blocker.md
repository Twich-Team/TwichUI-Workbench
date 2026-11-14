# Native draft delivery — new evidence-reuse blocker, November 14, 2026

## Outcome and stop

No addon draft PR was created. Installed build remains
`c4b9dd8412dbcc1d7df2f4e6daa1565b8b597142`. No application correction was committed,
pushed or installed. The proposed selector correction exists only in a private
isolated source copy. No native coding session, verification-program generation,
addon source modification, provider request or remote write was started.

The current owner assignment authorizes the focused publication selection correction
and one separate linked delivery operation, superseding the previous owner-review
stop for that scope. It also explicitly requires stopping at one newly demonstrated
blocker needing an engineering or owner decision. The isolated trusted-publisher
proof below demonstrates that blocker before an unnecessary release/install cycle.
The original frozen proof remains a controlled failure, unchanged and unaccepted as
an end-to-end result. Zero native addon tasks have completed request to draft.

Following the Project Working Philosophy, passing source/check stages is not
represented as completing delivery. No infrastructure milestone substitutes for
publishing the preserved repair.

## Actual state confirmed

The running API, worker, connector and private LAN service all point at
`/home/rich/TwichUI-Workbench/.local/m5-review/everyday-outcome-c4b9dd8/release`.
Each remains active with its original PID and zero restarts. Development remains
on `codex/native-agent-v1`; preexisting untracked environment links are untouched.

The live execution, grant and canonical publication row exactly match the saved
November 14 receipts. The execution is checked, its task is paused, publication is
pending with empty state, and no prepared commit or remote intent exists. No
production task controls, grants, approvals, results or publication rows were edited.

Preserved source identities:

- Native task: `6104b4a6-e3e6-4e29-9725-6bc552f15db9`.
- Execution: `36792197-7825-5f84-bb0f-d2db73471df1`.
- Grant: `54898b99-d87d-4177-8fd6-0def1dc0ddd1`.
- Addon base: `d09e6dcb29a4f9e3e3dbdba3fb7632c80043c810`.
- Candidate: `3049e5ee932eae98d190aac339d8b41e56055821e7d007d74f693d169645aa58`.
- Patch SHA256: `9222071a4877756324defb330a0b85890b6c71e01aabb4df56cbb838aa511eca`.
- Only changed addon file: `tests/test_reminder_gallery.py`, 94 added/deleted lines.
- Original checks: 10 focused tests, 87 independent controls, 196 regression files.

Those completed addon tests were reused as frozen receipts, not repeated. Their
passing result and exact hashes remain intact; no claim of fresh addon testing is
made. Current destination qualification and publication were not reached.

## Selection diagnosis and isolated correction

`draft_delivery.queue_automatic` chooses the native task ID, falling back to the
legacy workflow intake ID, for worker eligibility. `draft_delivery.advance` uses
only the legacy workflow intake ID. This real native grant has no `workflow_task`.
With excluded/paused tasks present, SQL NULL fails the advancement predicate. The
original recorded before-pause predicate proof matched zero rows, while native
identity matched one. Queue admission and advancement disagree.

The isolated copy changes only the advance predicate to the same existing
`coalesce(native_task.task_id, workflow_task.intake_id)` used by queue admission.
It does not fabricate a legacy task, certificate or result. This correction was
not applied to the development checkout or installed release.

The worker normally selects that canonical row and sends its execution ID through
the private connector. `draft_delivery.handle` calls the existing publication owner;
`real_publication.process_one` revalidates `_saved_inputs` and `_proof` before the
trusted `PilotGitHub` adapter may prepare or push anything. Thus changing selection
alone cannot make an invalid or incompatible saved result publish.

## Newly demonstrated mandatory gate failure

The same read-only probe ran in two fresh Python processes using the installed
interpreter: once with installed source, once with the isolated selector correction.
It used exact saved execution/grant JSON, first compared against the live database.
It invoked real native frozen-candidate validation, receipt validation, immutable
completion recovery and `real_publication._proof`. No checks or candidate code ran.

| Actual gate | Installed source | Only selector corrected |
| --- | --- | --- |
| Exact frozen candidate bytes | pass | pass |
| Original recovery directory identity | pass | pass |
| Runtime equals frozen grant identity | pass | fail |
| Saved independent check receipts accepted | pass | fail |
| Immutable completion recovered | yes | no |
| Trusted publisher accepts original proof | yes | no |

The only runtime identity difference is `helper_sha256.draft_delivery.py`:

- Frozen/installed: `76c157cf6148a23e4cb756444305c3c28b65926258423989862415ccb50f7bb9`.
- Isolated correction: `6421e687083d8bedbc49896c5f99b358377624f48784058c7c96d6624ea1eb42`.

`native_agent.Runtime.identity()` hashes every backend Python helper.
`Runtime.verify_checks()` requires that complete identity to equal the original
grant's runtime. `real_handoff.load()` requires those checks before returning the
original completion, and `real_publication._proof()` refuses the missing completion:

> The frozen candidate or independent completion evidence failed integrity checks.

The baseline recovered result digest is
`a63847c30cf9c92b89203cc4b10d057c2b936ea00b2dd64414036359ff1b74e9`.
The corrected copy preserves candidate bytes but fails solely on the publisher
helper change. A separate publication approval cannot repair this: it is checked
only after `_saved_inputs` has already required `_proof`. This is a mandatory
acceptance refusal, not a provider allowance or candidate correctness failure.

A further engineering decision is needed on a bounded linked delivery handoff that
validates original evidence under its frozen runtime while using the corrected
publisher. It must preserve the original grant, candidate, receipts, historical
outcome, current authorization/revocation/pause checks, branch qualification and
canonical publication idempotency. No hash exception, rewritten grant, fabricated
certificate, new coding run or direct GitHub bypass was implemented.

## Preservation and verification limits

The actual 1,039-row, 31-table, schema0014 store is unchanged. Its logical SHA256 is
`4375a2c31a62fd898753138a9533959d95c51c8c9f7225975010b9e20d999556`.
A separate restored snapshot matches. Every original 1,025-row and 1,031-row preproof
snapshot row remains exact. Original execution, grant and pending publication match
their saved receipts. Existing authentication, global model strategy, installed
source, rollback and unrelated local work were not modified.

No full Workbench local/hosted suite was launched, no release commit was made, and
no installation was attempted after the isolated mandatory gate failed. No native/
legacy publication regression suite or fresh destination check is claimed for the
private proposed correction. The accepted c4b9dd8 build retains its earlier full
local and hosted verification, recorded in the original correction report.

One closeout caller initially compared a string task ID with typed UUID pause keys;
it stopped at that assertion. Normalizing those read-only keys confirmed the task
was still paused. No product source, task control or proof state was changed by the
caller diagnostic.

Private receipts and isolated source:
`/home/rich/.local/share/TwichUI-Workbench/native-draft-delivery-20261114/`.
Files include `actual-state-confirmation.json`, `installed-baseline.json`,
`isolated-selection-correction.json`, `proposed-selection-correction.patch`,
`actual-before.sqlite3`, `independent-after-inspection.sqlite3`, and
`preservation-closeout.json`. Original native proof evidence stays at its original
`native-gallery-correction-20261114/manual-override/` location without modification.

Stop here under the current assignment's new-blocker rule. Do not start a broader
engineering recovery, new native proof, check regeneration, merge or addon release.
