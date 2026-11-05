# Execution-path checkpoint — November 5, 2026

Continue from the clean local commit containing this handoff. Its exact SHA and
check receipts are in `artifacts/runs/execution-path-20261105T200351Z/CHECKPOINT.json`.
Entry was `b59b90751c1d23e9da67051f4ef56cb63abe5ed4`. Its handoff and evidence
remain at `artifacts/runs/grounded-workflow-20261105T193102Z`. This session used
controlled responses and ordinary checks within the 60-minute authorization.

## Execution path and evidence

| Transition | Existing owner | What the evidence establishes |
| --- | --- | --- |
| Browser submission → durable request | `Intake.tsx` → `POST /api/intake` → `intake` writes the immutable original and planning job | Actual Chromium, API and SQLite on disposable stores. |
| Worker → selected local model → grounded proposal | `worker.tick` → `planning_worker` / `regression_preparation` → `model_policy.execution_policies` selection and `planning_provider.LocalProvider` / `planning_transport.LocalHTTP` | The actual adapter code and policy store passed controlled transport tests. The existing local endpoint previously produced structured generic planning output for WB-15. Its grounding and actual decomposition remained unaccepted. The grounded route has yet to reach real inference. |
| Proposal → human scope/expectation review | `grounded_scenarios` binds committed Git evidence, original input and feedback to the typed contract. `intake` / `planning_worker.apply_one` own the exact proposal and independent expectation confirmation | Actual browser confirmation, SQLite receipt and task materialization on both supported fixture shapes. Model output was controlled. |
| Applied review → certified offer | Single-task decomposition journals `behavioral_preparation` in `planning_jobs`. `trusted_preparation` uses `task_session.provision`, `Runtime`, `verification_profiles` and `task_budget.register_reviewed_offer` | Actual durable claims, Lua controls and canonical offer registration. Current grounded browser tests substitute confinement telemetry. |
| Offer → separate authorization | `RunApproval.tsx` → task `run-approval` API → `task_budget.approve_run` and the existing decision worker | Actual browser/API and distinct durable receipts in disposable stores. Certification alone creates no session, grant or request. Normal one-off admission requires the later applied `activate_once` decision, matching store capability and an unexpired offer/budget. |
| Authorization → provision → execution | `task_sessions` journals one-off ownership. `task_session` uses the existing delegated `provision_task.py` child and `chronicle_lease.py` controller. `real_execution` owns the grant, request, fence and attempt ledger. `Runtime` and `real_provider` own tools, model calls and checks | Ordinary browser ingress through this transition has controlled executor evidence. A supervised owner-ingress Chronicle run previously exercised the real lease, probe, model, execution and restoration. |
| Candidate → independent verification → durable result | `Runtime`, `real_confine`, `real_sandbox`, `verification_profiles`, `real_handoff` and `real_execution` own confinement, exact scope, fixed checks, freeze/seal and SQLite recovery | Historical real Chronicle and assisted guild candidates exercised those owners. The new ordinary-request route has yet to run implementation or candidate verification. |
| Verified result → draft PR | `task_session` waits for verified restoration. `real_publication` owns delivery state and the sealed result/current budget/usage checks. `PilotGitHub` stages the exact local commit, reconciles the branch and creates/confirms an open draft | Historical guild PR #6 exercised the real remote path using explicitly authorized Codex completion after three failed local attempts. Site session delivery has controlled evidence. Local Git staging and recovery also passed without network. |

## Change and remaining audit

The first adapter integration gap was model selection. The grounded worker used
whichever model the endpoint advertised and left the saved policy revision empty.
For real local grounded planning, the worker now reads the existing saved policy
inside the durable attempt claim. It requires an enabled preferred local model
and unspecified effort. The existing transport must observe the exact identifier
before generation, and the returned model must match it. The attempt keeps the
requested model and revision even on failure. The validated proposal keeps the
selection in its review-bound grounding. A preference save affects new claims.
Historical contracts omit the new optional field. Generic planning keeps its
existing adapter selection. Independent expectation review, preparation and the
execution recipe's fixed model and budget remain distinct.

- **Routing and capacity.** This binds one route and its attribution. Live
  capability/availability and capacity observations for `model_policy.evaluate`,
  account allowances, fallback and implementation-policy acknowledgment remain
  open. The fixed recipe uses `pilot-qwen38-27b`; it consumes the human-approved
  offer, including the recipe's limits, through the session and grant.
- **Real task and confinement.** On retained TwichUI base
  `f48b2e0bbdf966293695de8b1435b0e7e4372224`, the existing loader screen found no
  supported literal addon loader in 164 regular Lua tests plus the harness. Seven
  small representative paths also exceeded current grounded-context bounds.
  `tests/test_map_viewmath_tileat.lua` uses `loadfile(ROOT .. ...)` and fails these
  gates. This is local evidence about that base. No current production case was
  established. Pre-offer preparation calls the provisioner in the application
  worker and needs a dedicated delegated cgroup supplied by its owner. The post-budget
  controller already provisions in a delegated child. Actual pre-offer lifecycle,
  cancellation and cleanup on the installed worker remain unproved.
- **Recovery, retries and budgets.** Preparation claims reconcile complete evidence
  and hold partial output. Interrupted generation records unknown completion and
  has no automatic replay. The controller has finite leases and a one-attempt
  grant; bounded correction inside an attempt is already supported. Broader
  retries/fallback remain open. Planning (2,600 requested output tokens), the
  cancellation probe (60) and implementation (24,000) have separate ceilings.
  There is no shared planning-to-delivery reservation or unattended process that
  enforces the aggregate 512 MiB evidence cap across all stages.
- **Delivery and unattended operation.** The canonical delivery row journals write
  intents and reconciles uncertain outcomes with bounded reads. Historical real
  staging failed initially and was recovered on the same candidate and row, while
  its original private session marker remained held. Ordinary browser-to-real-draft
  delivery and unattended lifecycle consistency remain unproved. The browser run
  offer currently sends `publication="draft-pr"`. The same run-approval API
  accepts `publication="hold"` for a verification-only run.
- **Browser acceptance.** This session passed 186 focused backend tests, `wb check`
  and two targeted Chromium scenarios at 1440px and 390px. Inspected the desktop
  review, enlarged phone observation and phone run offer in `browser-ZKHwKe`.
  The historical 64/92 run and 28 uncovered cases remain separate, along with
  physical Safari and full current verification.

## Proposed next authorization

**Immediate, 30 minutes.** Install the checkpoint identified above on the existing
loopback review service at `127.0.0.1:9487`. Confirm fresh process UID/boot/start/argv
and ownership, current schema `0012`, a quiescent worker, no pending runnable work,
and sufficient headroom. Stage and hash-check backend/static assets and rehearse
restoration of a private SQLite backup and the served code/assets/config before
cutover. Use the existing owned supervisor/API/worker and serve assets index-last.
Allow only private backup/staging, owned restart and GET/Chromium checks of Work,
Models and existing delivered guild history. Preserve the DB inode, all rows,
schema, bind and environment. Keep the installed planner in demo mode and the
grounded and real runtime settings inactive. Reserve at most 8 MiB of counted
evidence and require 4 GiB physical space. No intake write, inference, lease, probe,
migration or publication in this stage. Stop before cutover for any failed gate.
On a post-cutover mismatch, stop only the identified replacement processes,
restore backed-up code/assets/config, start the prior build and environment with
the existing owned launcher, and verify the same store. Retain rollback evidence.

**Separate first real-adapter acceptance, 60 minutes.** Run the same checkpoint
with the normal API and worker on a fresh disposable store at `127.0.0.1:9187`.
The exact browser task is **Cover lowercase name normalization** in the existing
synthetic `tests/test_normalize.lua` fixture from `controlled_source` in
`test_grounded_workflow.py`. The expected scalar return for `"  ALICE "` is
`"alice"`. The control must reject `"ALICE"`. Label the repository and request
as fixtures. Pin its clean main SHA and packet, bind the private preparation file
to the disposable DB inode, and demonstrate actual owned cgroup/Landlock/seccomp
and private-loopback readiness before permitting any inference. Require 72 MiB
counted headroom (64 MiB preparation ceiling plus 8 MiB checks/handoff), 4 GiB
physical free space and 1 GiB maximum scratch. This stage requires more evidence
headroom than the current measurement.

Authorize `WORKBENCH_PLANNER=local` and `scenario_generation=true` only on the
disposable worker path. Permit exactly one local request using saved enabled preferred
`pilot-qwen38-27b` (Qwen3.8-27B), unspecified effort, fixed zero reasoning budget,
existing key/locks, one identity GET and one structured POST to
`127.0.0.1:8080`. Bound the exchange to 120 seconds, 24,000 bytes each way and
2,600 requested output tokens. After independent human review, permit only the
existing offline controls under the 40-minute Runtime clock, 6 GiB RAM, no swap,
200% CPU and 64 tasks per untrusted check (128 for trusted suite verification).
Stop at the certified offer and verify zero execution/delivery authority. Stop
earlier on lock/model mismatch, unknown completion, invalid mapping, failed
review/control, loss of confinement or exhausted limits. No retry, fallback,
lease, cancellation probe, implementation or remote write. Stop only owned
disposable processes and retain the store, receipt and outputs. The existing
8080 and 9487 configurations need no change for this stage.

A real-repository implementation demonstration stays held until a clean current
TwichUI base and exact task pass the existing packet and mapping gates. It would
also require the reviewed offer, explicit execution consent, current installed
sampler/cancellation proof and verified 8080 restoration, with one authorized
lease/probe/attempt. The existing run budget is 90 minutes total, 20 turns, 24,000
generated tokens, 2,500 per response, 20 minutes implementation and 40 minutes
through verification. It admits no fallback. A verification-only test can use
the existing API with `publication="hold"`, exercise local candidate and
delivery preview/staging, and test remote-state transitions with a controlled
publisher. Actual delivery needs distinct authorization for the exact current
candidate/base, one non-force maintenance-branch push and one open draft to
`Twich-Team/TwichUI:main` within the original 15-minute publication window,
verified restoration and usage, and private delivery activation. The existing
first-budget rule gives a verification-only decision no later upgrade path.

## Preservation

No installation, model inference, lease, probe, migration or external write was
performed. Entry counted 508,243,398 bytes with 28,627,514 bytes (27.30 MiB) free
under the unchanged 536,870,912-byte cap. The targeted checks fit without archival.
Final headroom is recorded with the checkpoint.
