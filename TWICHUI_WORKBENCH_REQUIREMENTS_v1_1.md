# TwichUI Workbench — Product Requirements and Roadmap

Version: 1.1 — owner requirements, revised November 7, 2026
Revision: Standing autonomous delivery; superseded campaign caps removed
Owner: Rich

## Purpose and authority

Build a self-driven but user-operable application for continued development of TwichUI: continuous improvement, continuous fixes, and eventually dependable around-the-clock operation.

This is Rich's personal project. Prioritize a useful working product and substantial progress over enterprise-scale process, elaborate approval machinery, or excessive documentation. Rich is the product owner, not the coordinator of every repair.

Follow PROJECT_WORKING_PHILOSOPHY.md and STANDING_OWNER_DIRECTION_AUTONOMOUS_DELIVERY.md for working methods and standing engineering authority. The November 7 standing direction supersedes prior campaign clocks, attempt allocations, revision ceilings, cumulative local-testing caps, and routine renewal requirements. Do not recreate those caps in assignments or application controls.

Codex owns the agreed outcome through investigation, implementation, debugging, refactoring, necessary architecture changes and dependencies, testing, installation, and demonstration. A blocker is something to investigate and resolve. Try supported alternatives and continue rather than returning ordinary engineering decisions to Rich.

Distinguish product scope from implementation scope. Necessary connected engineering work is authorized without a fresh decision for each repair or component. A material change to the intended product outcome still requires a genuine product decision.

Maintain usage accounting. Respect actual service quotas, hardware capacity, billing arrangements, source-disclosure permissions, and system access. Choose practical per-operation timeouts, concurrency, retries, and monitoring independently; do not turn them into artificial assignment termination gates.

This document describes required behavior and future plans, not implementation completion. Use docs/CONTINUATION.md and the latest verified reports for current status. That repository handoff and the latest locally referenced campaign records were not directly read during this reference-document revision. No later execution, deployment, restoration, or acceptance result is certified here.

Labels:
- Initial product: required for the first usable release; proposed acceptance details remain explicitly identified.
- Subsequent phase: intended capability, not a reason to delay proving the initial product.
- Open decision: unresolved detail; do not invent an answer or block unrelated authorized work.

Development authority does not authorize new paid spending, unrelated tasks, unauthorized source disclosure, public exposure, publication, merging, or live game-addon changes. Apply existing valid standing permissions without requesting duplicate approval.

Document roles:
- PROJECT_WORKING_PHILOSOPHY.md governs working methods and communication.
- STANDING_OWNER_DIRECTION_AUTONOMOUS_DELIVERY.md records durable autonomous-delivery authority and supersession of campaign caps.
- This requirements document governs capabilities, priorities, and recorded owner decisions.
- TWICHUI_WORKBENCH_WEB_VISUAL_DIRECTION.md governs visual interpretation and asset preparation.
- DASHBOARD_MOCKUP_VISUAL_DIRECTION_CLEAN.png is the visual baseline, not proof of working capabilities.
- The latest verified handoff/results govern current implementation status.
- CODEX_MODEL_REFERENCE.md governs recommendations for the Codex development model and reasoning setting, not access entitlements for Workbench providers.

## Later explicit owner requirements — November 10, 2026

The original v1.1 requirements below remain intact. The owner subsequently added these authoritative requirements:

- [Global and per-task model/reasoning configuration](docs/OWNER_MODEL_CONFIGURATION_20261110.md): supported provider/model/effort for primary and fallback, optional task overrides, persisted resolved choices and requested-versus-actual reporting, with existing safeguards.
- [Expanded Feature Workshop](docs/OWNER_FEATURE_WORKSHOP_20261110.md): optional preserved references, actual supported generated visual proposals, reliable simulator before/after comparisons, coherent dependent subtasks under one parent feature, integrated checking and feature-level draft delivery.

The Feature Workshop requirements follow current Phase A/B work. Recording them does not authorize starting image generation, unapproved spending or a new feature orchestration framework during the active campaign. Neither addendum certifies implementation or changes an already-running task.

# 1. Product experience and guiding principles

Rich can describe a bug, feature, inspection, test-coverage request, simulator task, or other supported work in ordinary language. Workbench explains its plan, obtains meaningful decisions, does the work, checks it, and presents an appropriate result.

Example: while playing, Rich notices a problem, opens Workbench, describes it, and understands what happens next without supervising development. This example does not limit the full capability set to bug reports.

- Use plain English in the app and user-facing reports; keep technical details in advanced views and supporting reports.
- Preserve the addon's general colors, artwork, and theme; use the selected frameless WoW-inspired dashboard baseline.
- Automate routine investigation, preparation, debugging, testing, recovery, and housekeeping.
- Ask only for genuinely necessary product decisions, unavailable access, new paid spending, or consequential actions outside authorization.
- Attempt to unblock work independently before concluding owner action is needed.
- Never equate internal repairs or passing tests with a completed product.
- Findings, test reports, and simulations are valid outcomes; not all tasks produce code or a PR.
- Do not require developer-style test packages or a chain of owner-coordinated repair prompts.
- Use proportionate engineering practices for this personal project.

# 2. Release structure

## Phase A — Initial minimum functional product

A supported ordinary request reaches a checked, reviewable finished result through the running application, using real models where required and without per-task developer hand-preparation.

Codex must be available as both an intentional development choice and a fallback option, not only for emergencies.

Proposed acceptance task: demonstrate at least one real TwichUI code-change task from browser intake through checks and an explicitly authorized draft PR. Synthetic fixtures alone do not establish real-addon readiness. PR publication remains conditional on actual publication authority; do not infer that authority from this proposed acceptance wording or block otherwise authorized local development while waiting for publication.

Exact breadth of task types required on day one remains to be agreed. The first narrow demonstration must not erase the broader capability vision.

## Phase B — Gamer-friendly dashboard

Redesign around current work, attention needed, backlog, and results, preserving addon branding and the selected frameless WoW-inspired character. Basic clarity and truthful progress belong in Phase A; the broader visual redesign follows the first working product. Artwork selection and asset preparation do not change this ordering.

## Phase C — Expanded automation and intelligent usage

Add proactive discovery, upstream monitoring, sophisticated task grouping, usage-aware provider allocation, Claude, and dependable unattended operation.

# 3. Phase A requirements

## A1. Submit supported work naturally

Provide browser entry for ordinary task descriptions. Explain supported work and specific limitations. Ask targeted clarification questions when actual intended behavior cannot be inferred safely; do not require internal fixture names, source bindings, or verification terminology.

Help users express examples where needed. A shared Create a task entry point accommodates bugs, features, inspections, test coverage, simulator tests, and other supported work. Richer type-specific screens may follow.

Attachments and screenshots are desirable; initial-release necessity remains open.

## A2. Review and permission

Show what Workbench understood, proposed scope, expected behavior, checking strategy, meaningful protections, and any publication permission.

Bind approvals to actual proposals. A material change to intended behavior, consequential access, disclosure, spending, or publication requires the appropriate review. Necessary connected engineering changes within valid standing authority do not automatically constitute a new product decision.

A review of intended behavior is not permission for unapproved spending or publication. Apply valid standing permissions and normal authorized task decisions without repeatedly requesting the same covered consequence. Keep approvals understandable and combine foreseeable genuinely new requests.

## A3. Application-owned work

After required approvals, the application owns supported work through the finished result. Do not require per-task database edits, custom registration, hand-written known-good solutions, or manual preparation commands.

Investigate and fix routine failures, including missing source context, invalid provider output, diagnostics, and execution defects. Try appropriate supported alternatives and continue. Do not stop merely because an artificial campaign allocation was consumed.

Support app-owned source discovery, search, inspection, relevant dependency traversal, context assembly, and follow-up reads. Give providers sufficient relevant evidence without hardcoding the acceptance fix. Respect repository and source-disclosure permissions; distinguish investigative read access from approved write authority.

A genuine external boundary should explain what was attempted and precisely what owner action is needed, not request routine engineering renewal.

## A4. Checks and honest outcomes

Check requested behavior and relevant regressions independently of the generating model's claims. Present what passed, failed, was not checked, and remains uncertain.

Preserve meaningful independent review. Repair defective checks without weakening their purpose. Invalid or failed task output must not gain execution or publication authority.

Use actual relevant addon behavior in acceptance checks. Do not assign desired answers directly into fixtures or substitute added tests for the actual implementation fix. Clearly state simulator-versus-in-game limitations.

## A5. Results and initial PR delivery

For changes: explain what changed, show code differences and checks, and provide a reviewable draft PR when authorized.

For inspections: show findings and evidence, stating whether changes were made.

For tests and simulations: show scenarios, outcomes, failures, and limitations.

A PR proposes a change for review; it does not authorize merging or releasing. Use task-aware PR boundaries, not a universal daily schedule. Do not bundle unrelated work merely to meet a timetable. Advanced batching may follow the initial delivery path.

## A6. Tasks, backlog, and truthful progress

Show submitted tasks, active task, stage, latest meaningful update, items needing the user, recent results, and held or failed work.

Suggested stages: Queued, Understanding, Needs your input, Needs approval, Working, Checking, Ready to review, Complete, Paused, Could not finish. Map them honestly to actual work.

Use meaningful activity indicators; do not invent percentages or spin indefinitely while waiting or failed. Distinguish automatic recovery from work genuinely waiting for Rich.

After recording a decision but before applying it, show “Decision made — waiting to apply,” or equally clear wording.

Show PR-submission progress and a definite submitted or failed outcome. Counts must reflect real task data and identified filter scope, not mockup figures.

## A7. Start and pause

Provide understandable start/pause controls. Proposed initial behavior: pause new work and explain the treatment of running work. Whether immediate cancellation or stopping at a safe point is offered remains open.

Pause does not imply undoing completed changes. Permission to cancel Workbench-owned generation for safe control does not settle the user-facing Pause behavior.

## A8. Codex in the initial product

Codex must be intentionally usable for suitable nontrivial tasks, not only after local-model failure. Local models remain useful when included allowance is exhausted, reserved, unavailable, or not selected.

Initial requirements:
- Configure actual supported Codex access and model selection.
- Deliberately choose Codex for eligible tasks.
- Configure local use and supported pre-generation fallback behavior.
- Honor saved selections, actual service availability, billing safeguards, source-disclosure permissions, and system access.
- Explain which service is working and why.
- Diagnose missing information and broken setup before repeating requests.
- Prevent runaway activity and duplicate or uncertain external execution without reinstating arbitrary campaign attempt caps.

Rich's roughly $20 offering example expresses intent to use included capacity productively, not a verified price, allowance schedule, API entitlement, or availability claim.

Verify actual access and supported integration. Do not assume subscription allowances equal API credits, reset daily, or permit unsupported automation.

Basic deliberate Codex use belongs in Phase A. Automatic allowance monitoring and percentage-based allocation belong in Phase C unless separately promoted.

The Codex development model and reasoning setting are separate from Workbench's provider configuration. Consult CODEX_MODEL_REFERENCE.md for development recommendations.

## A9. Recovery and continuity

Reloads, restarts, duplicate clicks, and repeated decisions must not duplicate work, approvals, or publication. Preserve history and failures. Recover safely without recreating unchanged tasks or seeking duplicate approval; renew actual task bindings when necessary.

Use practical operation-level controls and usage accounting. Investigate settled failures before renewed attempts. Resolve uncertain external execution before replay; do not silently escalate consequential permissions or disclosure.

Previous cumulative campaign retry, time, allocation, and revision caps are superseded. Checkpoints are not stop points. Session interruption requires a resumable handoff, not renewed engineering authorization.

## A10. Storage, safety, and installation

Manage routine retention and cleanup automatically under documented preservation rules. Rich should not supervise cleanup between tasks.

Record what storage is counted, retention rationale, and archival behavior. The former 512 MiB development allowance is not a permanent product requirement or a standing campaign stop threshold. Prevent disk exhaustion; preserve unique evidence, original history, recovery copies, and archive lookup records.

Protect credentials, data, unrelated work, and important evidence. Use proportionate checked installation and recovery procedures. Record candidate identity and verification before deployment; a new verified revision is not by itself a reason for owner approval.

Dashboard login is not required for initial local/private-network use. This does not remove model-service authentication, meaningful task approvals, isolation, or billing safeguards, and does not authorize public exposure.

## A11. Standing local-model priority and control

Owner decision, November 5, 2026: Workbench is the highest-priority user of the existing local model and may control that model for approved project work. Other model clients and maintenance may wait or be interrupted. Ordinary interruption of those clients does not require repeated permission.

For the existing service on port 8080, supported authority includes exclusive reservations, locks, idle/readiness checks, private temporary authentication changes, restarting twichui-llm.service when required, cancelling Workbench-owned generation, and guarded restoration and verification.

Preserve authentication, isolation, usage accounting, recovery protections, credentials, and important evidence. Record operational use in the handoff; recording permission is not evidence that an operation occurred.

The November 7 standing direction removes the prior delivery campaign's numerical call, retry, revision, and time allocations. Use the existing local service intelligently for connected delivery work, with practical operation controls and real resource monitoring rather than renewed campaign budgets.

This does not authorize new paid services, model downloads or replacement, unrelated service changes, public exposure, publication, merging, releases, or unsupported privilege escalation. Service control does not authorize unrelated product tasks.

Use guarded recovery and verify restoration after temporary exclusive reservations. If ownership or configuration changed unexpectedly, preserve evidence, inspect and seek a safe supported resolution rather than overwriting unrelated changes. Ask only if a concrete boundary cannot be resolved within existing authority.

Materially different service control outside supported mechanisms still requires any genuinely new access or consequential authorization. Combine foreseeable needs; do not weaken safeguards to avoid asking.

## Initial-product acceptance checklist

- [ ] Submit a supported task in the running browser app.
- [ ] Use actual intake information and configured real planning.
- [ ] Provide sufficient relevant source through app-owned investigation.
- [ ] Review the plan in understandable language and make necessary decisions.
- [ ] Prepare and perform work without per-task developer coordination.
- [ ] Demonstrate a real supported TwichUI change, not only a synthetic fixture.
- [ ] Run meaningful independent checks and state limitations.
- [ ] Show the result and code differences.
- [ ] Deliver an explicitly authorized draft PR for the change-producing acceptance task, subject to actual publication authorization.
- [ ] Demonstrate intentional Codex use through a supported integration.
- [ ] Verify configured fallback without uncertain replay, runaway activity, or unauthorized spending.
- [ ] Avoid duplicated work through reload/restart behavior and preserve results.
- [ ] Recover from ordinary failures autonomously; explain genuine external boundaries.
- [ ] Leave the verified working build installed and demonstrate the agreed request-to-result journey.

Completion does not imply every future capability or task category is available. This revision marks no implementation item complete.

# 4. Phase B — Gamer dashboard

## Visual direction and selected reference

Use actual TwichUI assets, colors, and theme alongside:
- DASHBOARD_MOCKUP_VISUAL_DIRECTION_CLEAN.png
- TWICHUI_WORKBENCH_WEB_VISUAL_DIRECTION.md

Rich prefers the render's stronger WoW-addon character over conventional clean-web styling. Preserve chestnut leather headers, warm dark panels, bronze edges, raised controls, selective gold, and readable ivory text. Remove the heavy decorative outer perimeter frame and ornate corner fittings; retain the crafted top identity bar and functional panel edges. Do not flatten the remaining interface in the name of simplification.

The reference shows Dashboard and Backlog side by side for comparison, not a requirement to display both simultaneously. Example names, figures, results, and statuses are illustrative, not acceptance evidence.

Make it calm, approachable, readable, and usable on desktop and phone with enlarged text. Status must not rely only on color. Avoid console density and decorative clutter; intentional texture and depth are not automatically clutter.

## Candidate material assets

Uploaded sources:
- DASHBOARD_ASSETS/BACKGROUND_LEATHER.png
- DASHBOARD_ASSETS/HEADER_LEATHER.png
- DASHBOARD_ASSETS/PANEL_INTERIOR_LEATHER.png

Use the actual folder spelling DASHBOARD_ASSETS. Intended roles: page background, warmer header, quieter panel/sidebar interior. These are textures, not prebuilt controls or layouts. Presence does not prove seamless tiling, optimized size, readability, or browser suitability. The third asset was inventoried but not independently visually inspected in the November 5 revision; this revision adds no visual verification.

Preserve originals. Inspect seams, scale, contrast, noise, and real-page rendering. Prepare web derivatives where useful, retaining provenance/permission information. Keep text and controls as actual web elements. Use reusable web styling for edges, shadows, controls, and states. Keep dense reading/code surfaces smooth or nearly smooth, with readable fallbacks if artwork fails.

## Dashboard

Answer immediately: What is working now? Does it need me? What finished?

Sections:
- Working now: task title, understandable stage, meaningful update, activity.
- Needs your attention: specific necessary question/approval and clear action.
- Recently completed: concise outcomes and links to results.
- At a glance: useful queued, active, waiting-for-user, and completed statistics.

Diagnostics belong in an advanced view. An Understand → Work → Check → Review summary may help but must not hide waiting, failure, or pause states or imply every task changes code.

## Backlog and navigation

Baseline navigation: Dashboard, Backlog, Results, Settings. Keep a prominent shared Create a task action usable across screen sizes. Design labels do not certify implemented routes or capabilities.

Show task titles, types, statuses, supported priority, and active-work markers. Distinguish queued work from work waiting for Rich. Keep completed, held, and failed results accessible.

Use reference search and All tasks / Needs you / Completed filters as starting direction, verified against actual behavior. Consider sorting, grouping, and reprioritizing through implementation without inventing backend capabilities.

## Reviews and notifications

Explain behavior, show differences before authorized publication, and treat findings and test reports as first-class outcomes.

Notify for meaningful decisions, completion, and failures genuinely needing attention, not every internal step. External channels and urgency preferences remain open.

## Visual acceptance boundaries

Check populated, empty, waiting, error, and recovery states; desktop/phone layouts; enlarged text; keyboard focus; controls; reduced motion; readable textured surfaces.

Distinguish concept approval, asset preparation, implementation, installation, browser verification, and demonstrated task completion. None substitutes for another.

# 5. Phase C — Expanded features

## C1. Continuous improvement, self-inspection, and larger tasks

Identify useful work beyond unit tests: bugs, code health, coverage gaps, simulator scenarios, compatibility work, and feature opportunities. Suggest understandable backlog items and avoid duplicates. Discovery alone does not authorize unrelated changes or publication.

Break larger goals into coherent related tasks and show overall progress. Respect actual capacity, billing, and consequential permissions without reinstating artificial delivery campaign caps. Rich should not coordinate subtasks or approve arbitrary fragments of the same authorized coherent change.

Around-the-clock operation requires dependable recovery, pause controls, resource monitoring, and reporting. It is a goal, not a certified present capability.

## C2. What's Training data updates

What's Training is a separate addon, unrelated to TwichUI ownership. TwichUI uses data from it.

Desired workflow:
1. Detect an update at the configured authoritative source.
2. Compare relevant upstream data with TwichUI's retained data.
3. Determine whether TwichUI needs an update.
4. If needed, update relevant data, run checks, and create a PR under configured authorization.
5. Otherwise report no change without creating an empty PR.

Preserve upstream source/version and explain changes. Do not modify or assume ownership of the upstream addon. Review data/code permissions before copying.

Open setup: authoritative URL, update signal, TwichUI data locations, checking strategy, and any standing authorization for per-event PR creation.

This replaces the ambiguous note “Automatic PR when update to what's training occurs.” It does not refer to model training.

## C3. WoW UI/API change monitoring

Owner-designated source: https://github.com/Gethe/wow-ui-source
Requested branch: forever

Monitor relevant game-related changes, assess TwichUI impact, and propose or perform authorized compatibility work with checks and reviewable PRs.

The earlier record identifies this as a WoW UI-source mirror, not a Blizzard-owned repository, based on a November 5 README check. That earlier observation is carried forward, not newly verified here.

Branch verification remains open: an earlier landing page displayed live. Do not silently replace forever with live. Confirm remote ref and intended game version before enabling monitoring.

Record upstream revision and connect relevant changes to affected TwichUI behavior. Avoid tasks for unrelated upstream edits.

## C4. Reference addons

Allow downloading approved addons for reference and comparison. Record source/version, treat material as untrusted, and do not automatically execute it or incorporate code/artwork. Clarify approved sources and incorporation permissions.

## C5. Task-aware PR grouping and timing

No fixed “one PR a day” rule. Group and publish according to work:
- A checked reported bug may receive its own PR promptly when authorized.
- Autonomous test/simulator changes may accumulate into a coherent batch.
- Feature PRs include related implementation, subtasks, and checks only.

Keep traceability and check combined batches. Do not mix unrelated changes just because they finished together. Report-only test/simulator work need not produce PRs.

Capabilities: Create PR now for eligible checked work; show included tasks, differences, and checks; reconcile interrupted submission without duplicates; resolve in-scope merge conflicts safely and rerun checks; explain waiting/readiness.

Open policy: maximum batch age/size, urgent-bug handling, overrides, automatic PR authority. No automatic merge or release is implied.

## C6. Usage-aware provider allocation

Paid services are planned work resources, not only failover.

Use included allowance deliberately for appropriate nontrivial work; use local models for other work or after planned allowance is spent; reserve configurable paid capacity for fallback; monitor supported allowance interfaces; adapt to verified daily, weekly, rolling, or other actual windows.

Rich's 75% planned / 25% fallback example is configurable illustration, not a fixed default. Provider allocation policy must not recreate the superseded delivery campaign caps.

Show planned allowance, reserve, use, remaining capacity, and reset only when supported by reliable data. Distinguish allowance availability from task estimates. Verify interfaces and permitted automation; do not invent balances. If live data is unavailable, explain the limitation and provide an honest supported alternative.

Clarify reserve release near reset, overage treatment, priorities, and reserve borrowing. No new paid overage is authorized by standing engineering autonomy.

Claude should eventually support equivalent deliberate-use/reserve behavior but remains later-phase. Initial Codex availability must not wait for Claude.

## C7. Responsiveness and organization

Check eligibility often enough that ready work is not forgotten while respecting actual capacity. Explain why work cannot start. Choose practical targets from operation rather than blindly increasing polling.

Retain the request for a separate Workbench/dashboard repository. Clarify frontend-only versus all Workbench before moving code. Preserve history, deployment, and recovery continuity.

# 6. Suspected private-network access issue

Owner report: a possible firewall rule prevents reverse-proxy access.

This is an unverified diagnosis. Inspect listeners, proxy, routing, and firewall before changing rules. Do not remove protections or expose services publicly for a successful test.

Confirm intended private address and whether proxy access is required for first release from current decisions and verified configuration; ask only if genuinely unresolved.

# 7. Coverage of original Things notes

| Idea | Requirement location |
|---|---|
| Download reference addons | C4 |
| Suspected reverse-proxy firewall issue | Section 6 |
| Identify work beyond unit tests | C1 |
| Plan larger tasks into subtasks | C1 |
| Watch UI repo updates | C3; forever requires verification |
| Codex fallback and intentional use | A8 |
| Tasks view | A6 and Phase B |
| More frequent eligibility checks | C7 |
| Ready-to-publish code differences | A5 and Phase B |
| PR pipeline | A5 and C5 |
| Decision made, waiting to apply | A6 |
| Activity/progress, especially PR creation | A6 and Phase B |
| What's Training update PRs | C2 |
| Dashboard's own repository | C7 |
| Backlog view | A6 and Phase B |
| Features and bug reporting | A1 and Phase B |
| Paid model alternatives | A8 and C6 |
| Smart fallback usage | C6 |
| Claude usage | C6; later |
| Start/pause | A7 |
| Statistics and notifications | Phase B |
| Create PR now | C5 |
| Merge-conflict resolution | C5 |
| Category-based PRs | C5; task-aware, not daily scheduling |

# 8. Remaining decisions

Do not block unrelated authorized work on these questions. Check the latest verified decisions first; do not ask again if already settled.

1. Authoritative What's Training update source.
2. Intended forever ref and tracked WoW version.
3. Actual Codex access/integration and supported usage information.
4. Preferential Codex task selection and reserve borrowing policy.
5. Batch age/size and urgent-work policy for test/simulator PRs.
6. Upstream events authorized to create PRs automatically.
7. Separate dashboard repository versus all Workbench.
8. Pause handling of running work.
9. External notification channels.
10. Day-one task breadth and private-network access requirements.

The general visual character and outer-frame treatment are settled: use the selected frameless WoW-inspired concept. Exact web colors, fonts, responsive construction, and asset preparation are implementation work. Ordinary interruption of other local-model clients is covered by A11, not a recurring permission question.

# 9. Assignment and reporting rules

Before each copyable Codex prompt provide a compact introduction:
- Plain-English outcome, why it matters, and what Rich can see/do afterward.
- New session or continue, with a context-based reason.
- Development model recommendation from CODEX_MODEL_REFERENCE.md, stating uncertainty rather than inventing availability.
- Actual reasoning setting and short reason.
- Standing autonomous-delivery authority, genuinely new permission if any, and meaningful preserved protections.

Distinguish the development model from Workbench providers. Give one complete copyable prompt.

Require reading philosophy, standing direction, requirements and handoff; verifying actual state; owning a meaningful outcome; investigating and repairing ordinary blockers; making necessary connected changes; preserving unrelated work; checkpointing; and maintaining continuity while continuing work.

Do not introduce arbitrary campaign bounds, one-attempt exceptions, or routine renewal gates. Reconcile conflicting saved documents and controls with the November 7 standing direction while preserving unrelated protections and historical evidence.

Ask the owner only after reasonable investigation of supported alternatives, for an unavoidable product decision, unavailable access, new paid spending, or consequential action outside authority. A failed attempt, added component, broader implementation, or new revision is not by itself such a boundary.

Report plainly what the app can do, what actually worked, what remains and is being done about it, and whether a genuine decision is needed. Keep updates concise. Reports and checkpoints do not terminate work.

Track: Describe a task → review the plan → do the work → check it → present the result.

Distinguish built, simulated, real-model-tested, installed, and demonstrated end to end. Do not invent percentages or reliable completion dates. Do not let redesign delay the initial useful product or let a narrow demonstration erase the broader vision.

# 10. Revision record

November 5, 2026: recorded frameless WoW-inspired visual direction and candidate textures; aligned visual authority; recorded standing control of the existing local model. Retained initial-product priority, broader roadmap, open decisions, and safeguards.

November 7, 2026: incorporated standing autonomous-delivery authority for this personal project; superseded numerical delivery campaign clocks, attempt allocations, revision ceilings, cumulative testing caps, and routine renewals. Distinguished implementation scope from product scope; authorized connected repairs and independent unblocking; retained actual billing, access, disclosure, network, verification, recovery, and publication protections. Updated source-context and continuity expectations without certifying current implementation results. Reconciled retry and local-model-control wording with the new direction.

Filename remains TWICHUI_WORKBENCH_REQUIREMENTS_v1_1.md to preserve existing references. This document revision marks no implementation milestone complete.
