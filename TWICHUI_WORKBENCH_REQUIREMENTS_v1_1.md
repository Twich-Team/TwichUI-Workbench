# TwichUI Workbench — Product Requirements and Roadmap

Version: 1.1 — owner requirements, revised November 5, 2026
Revision: Frameless visual baseline, candidate asset inventory, and standing local-model priority decision
Date: November 5, 2026
Owner: Rich

## Purpose and authority

Build a self-driven but user-operable application for continued development of TwichUI: continuous improvement, continuous fixes, and eventually around-the-clock operation within approved limits.

Follow PROJECT_WORKING_PHILOSOPHY.md for assignments, communication, autonomy, and safeguards. Rich is the product owner, not the coordinator of every repair.

This document describes required behavior and future plans, not implementation completion. Use docs/CONTINUATION.md and the latest verified reports for current status. The repository handoff was referenced in owner-provided reports but was not directly read during this document revision. Do not infer later execution or successful restoration from a proposed authorization.

Labels:
- Initial product: required for the first usable release, with any proposed acceptance details identified explicitly.
- Subsequent phase: intended capability, not a reason to delay proving the initial product.
- Open decision: unresolved detail; do not invent an answer.

Requirements are not blanket permission to spend, execute arbitrary tasks, publish changes, modify unrelated live services, access credentials, or broaden network exposure. The explicit standing local-model permission recorded in A11 is a limited exception for that service, not a removal of task approvals or limits.

Document roles:
- PROJECT_WORKING_PHILOSOPHY.md governs working methods and communication.
- This requirements document governs capabilities, release priorities, and recorded owner decisions.
- TWICHUI_WORKBENCH_WEB_VISUAL_DIRECTION.md governs visual interpretation and asset preparation.
- DASHBOARD_MOCKUP_VISUAL_DIRECTION_CLEAN.png is the current visual baseline, not proof of working capabilities.
- The latest verified handoff/results govern current implementation status.

# 1. Product experience and guiding principles

Rich can describe a bug, feature, inspection, test-coverage request, simulator task, or other supported work in ordinary language. Workbench explains its plan, obtains meaningful decisions, does the work, checks it, and presents an appropriate result.

Example: while playing, Rich notices a problem, opens Workbench, describes it, and can understand what happens next without supervising development.

This example does not limit the product to bug reports. Retain the full intended capability set.

- Use plain English in the app and in user-facing development reports.
- Keep technical detail in advanced views and supporting reports.
- Preserve the addon's general colors, artwork, and theme. Use the current frameless WoW-inspired visual baseline for the dashboard phase.
- Automate routine investigation, preparation, debugging, testing, recovery, and housekeeping.
- Ask Rich only for genuine decisions, unavailable access, changed scope, or consequential permission.
- Never equate an internal repair or passing tests with a completed product.
- Not all tasks produce code or a PR. Findings and test reports are valid results.
- Do not make Rich provide developer-style test packages or coordinate a chain of repair prompts.

# 2. Release structure

## Phase A — Initial minimum functional product

A supported ordinary request can reach a checked, reviewable finished result through the running application, with real model use where required and no developer hand-preparation for each task.

Codex must be available in this initial product as both an intentional development choice and a fallback option. It is not restricted to emergencies.

Proposed acceptance task: demonstrate at least one real TwichUI code-change task from browser intake through checks and an explicitly authorized draft PR. Synthetic fixtures alone do not establish real-addon readiness.

Exact breadth of task types required on day one remains to be agreed. Do not drop the broader capability vision because the first demonstrated path is narrow.

## Phase B — Gamer-friendly dashboard

Redesign the interface around clear current work, attention needed, backlog, and results. Preserve addon branding and the selected frameless WoW-inspired character. Basic clarity and honest progress are required in Phase A; the broader visual redesign follows the initial working product. Selecting artwork and preparing assets does not change this ordering.

## Phase C — Expanded automation and intelligent usage

Add proactive discovery, upstream monitoring, more sophisticated task grouping, usage-aware provider allocation, Claude, and dependable unattended operation.

# 3. Phase A requirements

## A1. Submit supported work naturally

Provide browser entry for ordinary task descriptions. Explain supported work and specific limitations. Ask targeted clarification questions instead of guessing behavior or producing incomplete work for approval.

Help users express examples where needed. Do not require them to know internal fixture names, source bindings, or verification terminology.

A shared Create a task entry point should accommodate bugs, features, inspections, test coverage, simulator tests, and other supported work. Richer type-specific screens can follow.

Attachments and screenshots are desirable; whether required at initial release remains open.

## A2. Review and permission

Show what Workbench understood, proposed scope, expected behavior, how it will check the outcome, relevant limits, and any publication permission.

Bind approval to the actual proposal. Material changes require renewed review. A review of intended behavior does not automatically authorize implementation, spending, or publication.

Keep necessary approvals understandable and combine foreseeable permission requests where possible without obscuring what is being approved. Apply valid standing permissions without seeking duplicate approval for the same covered consequence.

## A3. Application-owned work

After required approvals, the application owns supported work through the finished result. No per-task database editing, custom registration, hand-written known-good solution, or manual preparation command should be needed.

Solve routine failures within approved limits. Explain genuine holds clearly. Do not make the user relay another prompt after every ordinary repair.

## A4. Checks and honest outcomes

Check the requested behavior and relevant regressions. Do not rely solely on a model's claim of success.

Present what passed, failed, was not checked, and remains uncertain. Preserve meaningful independent review. Do not weaken checks to obtain a demonstration.

An invalid or failed task must not gain execution or publication authority.

## A5. Results and initial PR delivery

For changes: explain what changed, show code differences, show checks, and provide a reviewable draft PR when authorized.

For inspections: present findings and evidence, making clear whether any changes were made.

For tests and simulations: present scenarios, outcomes, failures, and limitations.

A PR is a proposed code change for review, not permission to merge or release automatically.

Use task-aware PR boundaries, not a universal daily publication schedule. Do not bundle unrelated work merely to meet a timetable. Advanced batching automation can follow the initial delivery path.

## A6. Tasks, backlog, and truthful progress

At minimum show submitted tasks, the active task, current stage, latest meaningful update, items needing the user, recent results, and held or failed work.

Suggested user-facing stages: Queued, Understanding, Needs your input, Needs approval, Working, Checking, Ready to review, Complete, Paused, Could not finish. Map these honestly to actual work.

Use meaningful activity indicators. Never invent progress percentages or keep spinning when work is waiting or failed.

After a decision is recorded but before it is applied, show “Decision made — waiting to apply,” or equally clear wording.

PR creation needs visible progress and a definite submitted or failed outcome. Dashboard counts must reflect real task data and clearly identified filter scope, not sample mockup figures.

## A7. Start and pause

Provide understandable start/pause controls. Proposed initial behavior: pause new work and explain what happens to already-running work. Whether to offer immediate cancellation or stopping at a safe point needs confirmation.

Pausing does not imply undoing completed changes. Permission to cancel Workbench-owned model generation for safe control does not settle the user-facing Pause behavior.

## A8. Codex in the initial product

Codex must be usable intentionally for suitable nontrivial tasks, not only after a local model fails. Local models remain useful when the paid allowance is exhausted, reserved, unavailable, or otherwise not selected.

Initial requirements:
- Configure available Codex access and model selection.
- Choose Codex deliberately for eligible tasks.
- Configure local use and bounded fallback behavior.
- Honor saved selections, spending/resource limits, and actual supported access.
- Explain which service is doing the work and why it was selected.
- Prevent endless retries or paid repetition of failures caused by missing information or broken application setup.

Rich's example is using a roughly $20 Codex offering's included allowance on worthwhile work rather than leaving it unused. This is a product intent, not a verified claim about a plan's price, credit schedule, API access, or availability.

Before implementation, verify the actual subscription/access arrangement and supported integration. Do not assume subscription allowances equal API credits, that they reset daily, or that unsupported automation is permitted.

Basic deliberate Codex use belongs in Phase A. Automatic allowance monitoring and percentage-based allocation belong in Phase C unless separately promoted.

## A9. Recovery and continuity

Reloads, restarts, duplicate clicks, and repeated decisions must not duplicate work, approvals, or publication.

Keep task history and previous failures. Support safe recovery without recreating unchanged tasks or seeking duplicate approval. Renew approval when the actual binding changes.

Bound retry counts and total usage across attempts. Do not silently escalate scope or permissions.

## A10. Storage, safety, and installation

Manage routine evidence retention automatically under documented preservation rules. Rich should not repeatedly supervise cleanup between tasks.

Establish what storage is counted, why, and how long evidence is retained. The current 512 MiB development allowance is not automatically a permanent product requirement.

Protect credentials, user data, unrelated work, and important evidence. Use checked installation and rollback procedures.

Dashboard login is not required for initial local/private-network use. This does not remove model-service authentication, approval requirements, limits, or isolation, and does not authorize public exposure.

## A11. Standing local-model priority and control

Owner decision, November 5, 2026: Workbench is the highest-priority user of the existing local model and may have full control of that model for approved project work. Other model clients and maintenance work may wait or be interrupted. Ordinary interruption of those clients is not a reason to request the same permission repeatedly.

For the existing model service on port 8080, this covers supported exclusive reservations, required locks, idle/readiness checks, private temporary authentication changes, restarting twichui-llm.service when required, cancelling Workbench-owned generation, and guarded restoration and verification. Preserve authentication, isolation, usage accounting, recovery protections, credentials, and important evidence.

This standing permission concerns model-service control. It does not by itself authorize a new code task, unlimited calls or retries, extended task budgets, paid services, model downloads or replacement, changes to unrelated services, public exposure, or publication/merging/releases. Record operational use in the repository handoff; recording this decision here is not proof that Codex has applied it or that a run has occurred.

Respect each approved task's time, request, token, storage, and retry limits. A one-run exception does not silently become a permanent resource policy. Use the existing guarded recovery path and verify restoration at the end of temporary exclusive reservations. If ownership or configuration changed unexpectedly, preserve recovery evidence and stop for inspection rather than overwriting unrelated changes.

If future work needs materially different service control or standing scheduling behavior beyond the supported mechanism, explain the consequence and obtain any genuinely new permission together. Do not weaken safeguards merely to avoid asking.

## Initial-product acceptance checklist

- [ ] Submit a supported task in the running browser app.
- [ ] Use actual intake information and configured real planning.
- [ ] Review the plan in understandable language and make necessary decisions.
- [ ] Prepare and perform the work without per-task developer coordination.
- [ ] Demonstrate a real supported TwichUI change, not only a synthetic fixture.
- [ ] Run meaningful checks and report their limitations.
- [ ] Show the result and code differences.
- [ ] Deliver an explicitly authorized draft PR for the change-producing acceptance task.
- [ ] Demonstrate intentional Codex use through a supported integration.
- [ ] Verify configured fallback behavior without unbounded retries or spending.
- [ ] Avoid duplicated work through ordinary reload/restart behavior.
- [ ] Explain failures and offer supported next actions.

Completion does not imply every future capability or task category is available. This revision marks no implementation item complete.

# 4. Phase B — Gamer dashboard

## Visual direction and selected reference

Use actual TwichUI assets, colors, and theme alongside the latest owner-selected reference:
- DASHBOARD_MOCKUP_VISUAL_DIRECTION_CLEAN.png
- TWICHUI_WORKBENCH_WEB_VISUAL_DIRECTION.md

The owner prefers the render's stronger WoW-addon character over conventional clean-web styling. Preserve chestnut leather headers, warm dark panels, bronze edges, raised controls, selective gold, and readable ivory text. Remove the heavy decorative outer perimeter frame and ornate corner fittings; retain the crafted top identity bar and functional panel edges. Do not flatten the rest of the interface in the name of simplification.

The reference shows Dashboard and Backlog as separate pages placed side by side for comparison. The app is not required to display both together. Example task names, numbers, results, and statuses are illustrative, not current work or acceptance evidence.

Make it calm, approachable, readable, and usable on desktop and phone with enlarged text. Status must not rely on color alone. Avoid engineering-console density and decorative gaming clutter. Meaningful addon character is intentional; clutter is not a synonym for all texture or depth.

## Candidate material assets

Uploaded source files:
- DASHBOARD_ASSETS/BACKGROUND_LEATHER.png
- DASHBOARD_ASSETS/HEADER_LEATHER.png
- DASHBOARD_ASSETS/PANEL_INTERIOR_LEATHER.png

Use the actual folder spelling DASHBOARD_ASSETS. Intended roles are page background, warmer header leather, and quieter panel/sidebar interior respectively. These are candidate textures, not prebuilt layouts or controls. Presence does not establish seamless tiling, optimal file size, readability, or browser suitability. The third asset was inventoried but not independently visually inspected during this revision.

Preserve originals; inspect seams, scale, contrast, visual noise, and real page rendering before claiming readiness. Prepare web derivatives where useful and keep provenance/permission information. Keep text and controls as real web elements, not embedded artwork. Build reusable edges, shadows, controls, and states with web styling where appropriate. Keep dense reading and code surfaces smooth or nearly smooth. Provide readable fallback surfaces if artwork fails to load.

## Dashboard

Answer immediately:
1. What is working now?
2. Does it need me?
3. What finished?

Sections:
- Working now: task title, understandable stage, meaningful update, activity.
- Needs your attention: specific question/approval and a clear action.
- Recently completed: concise outcomes and links to results.
- At a glance: a few useful statistics such as queued, active, waiting for the user, and completed.

Detailed diagnostics remain available in an advanced view. A compact Understand → Work → Check → Review sequence may summarize active work, but must not hide waiting, failure, or pause states or imply every task changes code.

## Backlog and navigation

Use Dashboard, Backlog, Results, and Settings as the current navigation baseline. Keep a prominent shared Create a task action reachable across screen sizes. These design labels do not certify existing routes or features.

Show titles, task types, statuses, priority where supported, and an activity marker on current work. Distinguish waiting in the queue from waiting for Rich. Keep completed, held, and failed results easy to revisit.

The reference's search and All tasks / Needs you / Completed filters provide a starting direction. Verify them against real behavior. Candidate refinements include sorting, grouping, and reprioritizing; confirm useful controls during implementation without inventing backend capability.

## Reviews and notifications

Explain behavior rather than internal mechanisms. Show differences for changes ready to publish. Treat findings and test reports as first-class outcomes.

Notify about meaningful decisions, completion, and failures needing attention. External channels and urgency preferences remain open. Avoid notifications for every internal step.

## Visual acceptance boundaries

Review populated, empty, waiting, error, and recovery screens. Verify desktop and phone layouts, enlarged text, keyboard focus, controls, reduced motion, and readable textured surfaces. Distinguish concept approval, prepared assets, implemented screens, installed screens, browser verification, and demonstrated task completion. None substitutes for another.

# 5. Phase C — Expanded features

## C1. Continuous improvement, self-inspection, and larger tasks

Identify useful work beyond unit tests: bugs, code-health issues, coverage gaps, simulator scenarios, compatibility work, and feature opportunities.

Create understandable backlog suggestions and avoid duplicates. Discovery does not itself authorize changes or publication.

Break larger goals into related tasks, show overall progress, and respect combined budgets. Rich should not coordinate every subtask or approve arbitrary fragments of the same coherent change.

Around-the-clock operation requires proven limits, recovery, pause controls, and reporting; it is a goal, not present completion.

## C2. What's Training data updates

What's Training is a separate addon, unrelated to TwichUI ownership. TwichUI uses data from it.

Desired workflow:
1. Detect an update to the configured What's Training source.
2. Compare relevant upstream data with TwichUI's retained data.
3. Determine whether TwichUI actually needs an update.
4. If so, update the relevant data, run applicable checks, and create a PR under the configured authorization.
5. If no change is necessary, report that result without creating an empty PR.

Preserve the upstream source/version and explain the data changes. Do not modify the upstream addon or assume ownership of it. Review applicable data/code permissions before copying material.

Open setup details: authoritative source URL, version/update signal, relevant TwichUI data locations, check strategy, and whether ongoing policy may authorize PR creation without a fresh per-event decision.

This clarification replaces the ambiguous note “Automatic PR when update to what's training occurs.” It does not refer to model training.

## C3. WoW UI/API change monitoring

Owner-designated source: https://github.com/Gethe/wow-ui-source
Requested branch: forever

Monitor relevant changes alongside game updates, assess their effect on TwichUI, and propose or perform authorized compatibility updates with checks and reviewable PRs.

The prior requirements record describes the repository as a mirror of World of Warcraft's UI source, not a Blizzard-owned repository, based on a README check dated November 5, 2026. That external check is carried forward here, not newly verified during this document revision.

Branch verification remains open: the earlier fetched repository landing page displayed live. Do not silently replace Rich's requested forever branch with live. Confirm the exact remote branch/ref and intended game version before enabling monitoring.

Record the upstream revision and explain which change affects which part of TwichUI. Avoid creating work from every unrelated upstream edit.

## C4. Reference addons

Allow downloading approved addons for reference and comparison. Record source/version, treat downloaded material as untrusted, and do not automatically execute it or incorporate its code/artwork.

Clarify approved sources and incorporation permissions before use.

## C5. Task-aware PR grouping and timing

Rich does not want a fixed rule such as “one PR a day at a set time.” Group and publish according to the nature of the work.

Required examples:
- A reported bug fix may receive its own PR as soon as it is checked and ready.
- Autonomous test additions or simulator-related changes may accumulate into a coherent batch before one PR.
- A feature PR includes only work related to that feature, including its relevant subtasks and checks.

Do not mix unrelated changes merely because they completed together. Keep task traceability and check the combined result of a batch.

Test/simulator tasks producing only reports need not produce a PR.

Additional capabilities:
- Create PR now for an eligible, checked batch/change.
- Show included tasks, code differences, and check results.
- Reconcile interrupted submissions without duplicate PRs.
- Resolve merge conflicts safely within scope and rerun relevant checks.
- Show why a change is waiting or ready.

Open policy details: maximum batch age/size, urgent-bug handling, user override behavior, and authorization for automatic PR creation. No automatic merge or release is implied.

## C6. Usage-aware provider allocation

Paid services are planned work resources, not only failover services.

Desired behavior:
- Use paid allowance intentionally on appropriate nontrivial work.
- Use local models for other work and after the configured planned allowance is spent.
- Reserve a configurable share of paid capacity for fallback when local work cannot complete.
- Monitor usage and allowance windows through supported interfaces where available.
- Adapt to verified daily, weekly, rolling, or other limits rather than assuming one schedule.

Owner example: allocate 75% for planned work and reserve 25% for local-model failover. These are configurable example percentages, not fixed defaults.

Show planned allowance, reserve, use, remaining capacity, and reset information only when supported by reliable data. Distinguish allowance availability from task spending estimates.

Do not promise that any particular usage API exists. Verify available interfaces, accuracy, and permitted automation. If live usage cannot be obtained, make the limitation explicit and offer a bounded, honest alternative rather than inventing balances.

Clarify reserve release near a reset, treatment of paid overages, task-priority rules, and whether planned work may borrow reserve with approval.

Claude should eventually support the same deliberate-use and reserve behavior, but Claude integration is a later feature. Initial Codex availability must not wait for Claude.

## C7. Responsiveness and organization

Check work eligibility often enough that ready tasks do not appear forgotten, while respecting capacity and resource use. Show why work cannot start. Choose practical targets from actual operation rather than blindly increasing polling.

Record the request for Workbench/dashboard to have its own repository. Clarify whether this means frontend only or all of Workbench before moving code. Preserve history, deployment, and recovery continuity.

# 6. Suspected private-network access issue

Owner report: a possible firewall rule prevents the reverse proxy from working with Workbench.

This is an unverified diagnosis. Investigate listener, proxy, routing, and firewall configuration before changing rules. Do not remove protection or expose services publicly to obtain a successful test.

Confirm the intended private address and whether proxy access is required for the first release.

# 7. Coverage of original Things notes

| Idea | Requirement location |
|---|---|
| Download reference addons | C4 |
| Suspected reverse-proxy firewall issue | Section 6 |
| Identify work beyond unit tests | C1 |
| Plan larger tasks into subtasks | C1 |
| Watch UI repo updates | C3; requested forever branch requires verification |
| Codex fallback | A8; initial product, also intentional primary use |
| Tasks view | A6 and Phase B |
| More frequent eligibility checks | C7 |
| Ready-to-publish code differences | A5 and Phase B |
| PR pipeline | A5 and C5 |
| Decision made, awaiting apply | A6 |
| Activity/progress, especially PR creation | A6 and Phase B |
| What's Training update PRs | C2 |
| Dashboard's own repository | C7 |
| Backlog view | A6 and Phase B |
| Feature requests and issue reporting | A1 and Phase B |
| Paid model alternatives | A8 and C6 |
| Smart fallback usage | C6 |
| Claude usage | C6; later |
| Start/pause | A7 |
| Stats and notifications | Phase B |
| Create PR now | C5 |
| Merge-conflict resolution | C5 |
| Category-based PRs | C5; task-aware grouping, not daily scheduling |

# 8. Remaining decisions

Do not block unrelated safe work on these questions.

1. What is the authoritative What's Training update source?
2. Does forever exist as the intended upstream branch/ref, and which WoW version should it track?
3. Which exact Codex account/access arrangement will be used, and what supported integration and usage information does it offer?
4. Which nontrivial tasks should preferentially use Codex, and can reserve ever be borrowed?
5. What batch age/size and urgent-work rules should apply to test/simulator PRs?
6. Which upstream events may automatically create PRs under standing approval?
7. Does a separate repository mean the dashboard alone or all Workbench?
8. How should Pause handle work already running?
9. Which external notification channels, if any, are wanted?
10. Which task types and private-network access must be available at initial release?

The general visual character and outer-frame treatment are no longer open questions: use the selected frameless WoW-inspired concept. Exact web colors, fonts, asset preparation, and responsive construction remain implementation/design work. Ordinary interruption of other local-model clients is covered by A11, not a recurring permission question.

# 9. Assignment and reporting rules

Before every copyable Codex prompt provide:
- Plain-English brief: outcome, why it matters, what Rich can see/do afterward.
- New session or continue, with a reason based on actual context.
- Recommended development model; state uncertainty instead of inventing a version.
- Reasoning effort and why.
- New permissions and important preserved boundaries.

Distinguish the model doing development in Codex from the models Workbench itself calls.

Require reading the philosophy and handoff, verifying the current state, owning a meaningful outcome, handling routine problems and eligible housekeeping, preserving unrelated work, keeping appropriate checkpoints, and updating continuity.

Report plainly: what the app can now do, what actually worked, what remains, and whether Rich needs to decide anything.

Track progress against:

Describe a task → review the plan → do the work → check it → present the result.

Do not invent percentages or reliable completion dates. Distinguish built, simulated, real-model-tested, installed, and demonstrated end to end.

Do not let redesign or future expansion postpone proving the initial working product. Do not let the first narrow demonstration erase the larger vision.

# 10. Revision record

November 5, 2026: recorded the owner's frameless WoW-inspired visual preference and uploaded candidate textures; aligned authority with the updated visual guide; recorded standing control of the existing local model without expanding task budgets or publication rights. Retained initial-product priority, the broader roadmap, open decisions, and safeguards. Filename remains TWICHUI_WORKBENCH_REQUIREMENTS_v1_1.md to preserve existing project references. No current implementation milestone is newly certified by this revision.
