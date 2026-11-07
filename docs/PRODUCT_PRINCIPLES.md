# TwichUI Workbench — Product Direction and Goals

This earlier direction is subordinate to the exact owner-supplied root
requirements, philosophy and standing owner direction. Historical proposals and
delivery limits below do not override those documents or certify implementation.
Use [the current handoff](CONTINUATION.md) for demonstrated status.

Date: November 3, 2026
Status: Product-direction document. Requirements and proposed designs, not an implementation inventory.

## 1. Product vision

TwichUI Workbench is a local-first, user-operable application for continuously discovering, planning, and carrying out authorized improvements to TwichUI, a WoW Forever addon.

It should work autonomously where permitted, explain its activity in approachable language, bring meaningful decisions to the user, and preserve the evidence needed to trust its results.

Continuous operation does not mean continuous code changes. “Nothing worth doing” is a valid successful outcome. Never manufacture work to consume subscription capacity or produce a daily PR.

### Outcomes

- Continuous useful improvement, bug fixing, compatibility maintenance, and data updates.
- Less manual intervention, repeated investigation, and wasted model usage.
- Clear supervision and control without requiring software-engineering knowledge.
- Polished desktop and iPhone experiences.
- Explicit authorization, recoverable operations, and evidence-backed delivery.

## 2. Audience and experience

The owner is a UI/UX-leaning software engineer, but the product must not require engineering knowledge to use.

The interface should immediately answer:
1. Does anything need me?
2. What is happening now?
3. What is ready to review or publish?
4. What changed since I last looked?

Technical details remain accessible but secondary. “Apple-like” means clarity, predictable behavior, immediate feedback, sensible defaults, progressive disclosure, and careful visual craft—not imitation of Apple styling.

## 3. Guiding principles

1. Lead with meaning and the next useful action.
2. Show less first; make supporting evidence easy to reach.
3. Use icons, labeled chips, color, charts, and concise text together. Never rely on color or unexplained icons alone.
4. System records establish facts; AI explains and advises.
5. Acknowledge actions immediately and show their eventual outcome.
6. Keep saved, applied, checked, reviewed, published, and merged distinct.
7. Operate autonomously within explicit permissions; recommendations never create permissions.
8. Preserve functionality across desktop and phone, adapting composition rather than shrinking the desktop UI.
9. Show honest progress and recoverable failures.
10. Prefer useful work over activity and subscription consumption.
11. Require rendered evidence before claiming visual completion.
12. Keep one authoritative owner for every field and state transition.

## 4. Visual direction

Modern, clean application structure with restrained TwichUI character:
- Warm walnut/espresso surfaces.
- Aged bronze/gold and chestnut accents.
- Ivory text, readable contrast, subtle depth and texture.
- Consistent spacing, typography, component states, and semantic design tokens.
- Selected existing emblem/frame artwork where provenance and reuse are established.

Avoid generic flat charcoal/teal dashboards, excessive fantasy ornament, decorative gauges, flashing, fake percentages, and walls of engineering prose.

### Information hierarchy

- Glance: state, meaning, and primary action.
- Understand: brief explanation, recommendation, and next step.
- Inspect: diffs, checks, logs, model identity, provenance, and attempt history.

### Responsive and accessible behavior

- Desktop may combine list and detail panes; phone may use separate screens.
- Dense tables become readable summaries on narrow screens.
- Essential interactions must not require hover.
- Aim for generous 44 × 44 CSS-pixel touch targets.
- Support keyboard focus, text enlargement, reduced motion, and labeled states.
- Avoid page-level horizontal overflow; contain genuinely two-dimensional artifacts such as diffs.
- Make important chart values accessible without precise hover interaction.
- Test phone-sized layouts and actual mobile Safari. Automated viewport tests do not establish real-device acceptance.

## 5. Navigation and overview

Proposed destinations:
- Overview / Work
- Backlog
- Reviews / Approvals
- Models
- History
- Settings
- Secondary Details / Diagnostics

Final navigation may consolidate destinations to remain simple, especially on phones.

Overview priorities:
1. Needs you: genuine decisions and actionable blockers.
2. Current work: actual stage, elapsed time, waiting reason, and whether action is needed.
3. Ready for review/publication: result-oriented cards with direct actions.
4. Recent outcomes: useful changes and meaningful no-change results.

Stats and charts should answer operational questions, not dominate the page simply because telemetry exists.

## 6. State and feedback contract

Separate task state, attempt outcome, verification, review, publication, and evidence freshness.

Examples of distinct states:
- Working.
- Waiting automatically, with reason.
- Needs your decision.
- Not admitted, with qualification reason.
- Needs investigation.
- Attempt unsuccessful.
- Checks passed, awaiting review/publication.
- Published / merged.
- Unknown historical outcome.
- Stale live evidence.

After a decision is durably saved, remove it from the actionable “Needs you” count. Show “Decision saved — waiting to apply.” Later show applied, rejected, failed, or stale evidence explicitly. Saved is not applied.

Progress should use real stages, such as request saved → preparing changes → running checks → creating draft PR → draft ready. Show elapsed time and waiting state. Use percentages only with a meaningful measurable total.

Refresh, disconnect, response loss, and restart must recover existing operations rather than create duplicates.

## 7. AI explanations and recommendations

Use available AI to summarize what happened, what it means, the next useful step, and any action required.

Structured explanation content:
- Summary.
- Meaning.
- Recommendation when justified.
- Evidence basis.
- Uncertainty or missing information.

Only the relevant short portions appear initially. Technical evidence is expandable.

Rules:
- Generate bounded, cached explanations at meaningful transitions, not on every refresh.
- Bind explanations and recommendations to the relevant evidence revision.
- Deterministic records establish checks, permissions, and state.
- AI must not invent evidence, release holds, submit approvals, or grant execution permission.
- Recommendation controls may fill choices but never silently submit them.
- Use truthful deterministic fallback language if AI is unavailable.
- Explanation failure must not block core navigation or decisions.
- Paid fallback for explanations requires its own policy, not automatic escalation.

## 8. Model providers, routing, and capacity

Support local LLM, Codex, and Claude as execution providers, with provider-specific adapters.

The user can configure:
- Preferred provider and ordered fallback providers.
- Actual model per provider.
- Reasoning/effort where supported.
- Allocation, personal reserve, pacing, and extra-spending permission.
- Escalation conditions and behavior when capacity is unavailable.
- Additional attempt and concurrency safeguards.

Example intent: use the local LLM first, with Codex as fallback using up to a configured 75% allocation; Claude participates only if enabled.

An allocation ceiling is not a consumption target. Keep fallback-only behavior separate from an explicit strategy to use more eligible subscription capacity.

### Capacity policy

Support conservative, balanced, and maximize-useful-work strategies.

Distinguish:
- Workbench allocation: what automation may consume.
- Personal reserve: capacity to preserve for the user's own activity.
- Account consumption: observed total usage where available.
- Workbench attempt history: known automation activity.
- Predicted task consumption: estimate.

Use actual provider allowance/reset windows. Do not invent a daily subscription quota; daily pacing can be a separate policy. Enforce relevant simultaneous windows and retain simple attempt limits as conservative safeguards.

Verify supported machine-readable telemetry before promising precise quota control. Label readings as observed, estimated, stale, or unknown. Unknown is not zero usage. Do not scrape unsupported/private interfaces by default.

No automatic API billing, purchased-credit spending, authentication change, or overage without explicit permission. Do not assume SDK/API execution preserves subscription entitlements.

### Configuration semantics

- Offer valid model/effort combinations, not a universal effort list.
- Record requested and actual model/effort separately.
- Apply changed defaults to new attempts; do not silently mutate active execution.
- Show saved versus worker-effective settings.
- Explain unavailable models; substitute only under an approved policy.
- Task-specific overrides must remain within permissions and limits.

### Future adaptive routing

Start with explicit deterministic policy. Later let the local model recommend a provider/model/effort with rationale and uncertainty. Eventually permit bounded automatic selection within approved providers, models, effort ceilings, permissions, and capacity.

Invalid recommendations fall back to configured policy. Recommendations cannot expand authorization.

## 9. Models page and analytics

Provide provider-level summaries with expandable per-model detail:
- Availability and current activity.
- Selected model and default effort.
- Allowance/reset information where available.
- Workbench allocation and pacing.
- Distinct tasks, attempts, verification outcomes, escalations, duration, and usage.
- Short explanation of routing/waiting decisions.

Separate tasks from attempts. One task may have local attempts and a Codex escalation; do not count those as multiple completed tasks.

Separate purpose: discovery, summarization, planning, implementation, review, and verification assistance.

Useful charts include consumption over time, allocation versus pace, activity by provider/model, escalation patterns, and outcomes. Do not imply provider percentages or token units are directly equivalent.

Every attempt should retain task/purpose, provider/account, requested/actual model and effort, routing reason, policy revision, timings, outcome, verification, and usage provenance.

## 10. Issue reporting

Initial form:
- Title.
- Description.
- Optional Lua error, in a multiline code-style field.

Persist the original report immediately and acknowledge receipt before model investigation.

The model can summarize, investigate relevant code, detect possible duplicates, propose severity/priority and acceptance criteria, and create/link an actionable task.

Keep original input separate from interpretation. A report is evidence to investigate, not proof of a diagnosis. Ask specific follow-up questions when necessary.

Treat descriptions, pasted errors, repository content, and upstream artifacts as untrusted data—not instructions. Avoid leaking player information, private paths, secrets, or raw private logs into public artifacts.

## 11. Feature request and approval lifecycle

Request → feasibility research → proposal and applicable mockups → approval of a specific revision → detailed subtasks → implementation → feature-level verification → draft PR.

The proposal should explain:
- Intended behavior and user value.
- Scope and exclusions.
- Feasibility, dependencies, risks, and uncertainty.
- User experience and representative in-game-context mockup for visual features.
- Acceptance criteria and broad delivery stages.

Detailed engineering decomposition may happen after approval. Routine in-scope decomposition needs no repeated approval; material changes to behavior, scope, permissions, or approved design require a revised proposal.

An approved proposal is not evidence of implementation and does not override execution safety policy.

### Publication gate

No feature PR until required subtasks are complete or explicitly removed by an approved scope revision, the integrated result passes feature-level checks, required artifacts exist, and publication policy permits it.

Large features may use independently useful milestone PRs only through an explicit approved delivery plan. Do not silently fragment delivery.

## 12. Hierarchical backlog and priorities

Show feature-level records and expandable subtasks, including progress counts, blockers, source, model/attempt history, and review state.

Parent/child hierarchy expresses ownership. Dependencies express prerequisites. Model them separately.

A count such as “3 of 5 tasks complete” is not automatically 60% of effort completed.

Automatically propose priorities, with bugs above ordinary features by default:
1. Safety, data loss, and seriously broken functionality.
2. Reproducible bugs and regressions.
3. Compatibility and relevant upstream-data updates.
4. Approved features.
5. Improvements and housekeeping.

Consider impact, urgency, confidence, age, and dependencies. Prioritize triage of unconfirmed reports without treating every report as a verified critical bug. Prevent indefinite starvation of lower-priority work.

Explain priorities in plain language. Allow user overrides/pins with explicit scope. Changes normally affect the next scheduling decision, not abrupt interruption of active work.

## 13. Verification and visual evidence

For visual addon features, retain:
- Approved mockup tied to proposal revision.
- Actual simulator rendering tied to implementation revision.
- Relevant scenario, viewport, scale, and verification results.

PRs should compare intended and implemented appearance where practical.

Keep unit/integration tests, simulator tests, screenshot inspection, browser tests, and real-client tests distinct. Screenshots do not prove interaction behavior. Simulator success does not establish live-game correctness.

Known simulator limits, including typography substitutions and unsupported APIs, remain explicit and must be freshly checked when relevant. Never change production semantics merely to satisfy a simulator limitation.

Missing required visual evidence produces a visible blocked state, not silent publication. Nonvisual work does not require decorative mockups.

## 14. What's Training upstream updates

What's Training is an upstream WoW addon whose data TwichUI consumes during the GitHub deployment pipeline. This workflow concerns game training data, not AI model training.

Desired workflow:
1. Detect an upstream release.
2. Record its exact source version/revision.
3. Inspect relevant source data.
4. Run the same reproducible extraction/generation used by TwichUI's existing pipeline in isolation.
5. Compare generated TwichUI output.
6. Record “no update needed” if output is unchanged.
7. If changed, validate and prepare a focused draft PR under publication policy.

Reuse the existing transformation; avoid a divergent Workbench generator. Make generation runnable without publishing a TwichUI release.

Deduplicate source revisions and reconcile existing PRs. Record provenance, output diff, and validation. Show structured change counts only when reliably computed.

Preparing or merging a data PR does not independently distribute data to installed addons; delivery still depends on packaging/distribution policy.

## 15. Publication and operational controls

Support a deliberate PR pipeline, ready-to-publish diffs, “Create PR now,” category-informed grouping, notifications, and eventually conflict assistance.

- Create PR now requests eligible publication; it does not bypass checks or holds.
- Group by compatibility, dependencies, and review scope—not category alone.
- Conflict resolution is a new execution attempt followed by renewed verification.
- Pause normally stops admission of new work; cancellation of active work is a separate action.
- Acknowledge pause and control commands explicitly.
- Frequent inexpensive eligibility checks must not imply frequent expensive model calls.
- Notifications should be actionable, restrained, deduplicated, and updated when decisions are saved.

Broader discovery can precede broader execution permission. Existing automation boundaries remain in force until explicitly changed. Historical additive-test-only and draft-PR-only permissions are not silently expanded by this roadmap.

## 16. Authentication and deployment

Local-only use is acceptable initially. Future deployment should support the user's authentik SSO behind a reverse proxy.

Proposed initial integration: authentik forward authentication with an explicit trusted-proxy boundary. Confirm exact integration during deployment design.

- Explicit local-only versus SSO-protected modes.
- No automatic local-access fallback when SSO fails.
- Protect APIs and private artifacts as well as the frontend.
- Restrict backend bypass access and reject spoofed identity headers.
- Trust forwarded identity only through the configured proxy.
- Preserve CSRF and Host/Origin validation.
- Attribute submissions, approvals, settings changes, and publication requests to an actor.

A local owner identity is sufficient initially. Do not build a full multiuser role system without need, but avoid hard-coded anonymous ownership.

## 17. Architecture direction

Use an application-owned modular monolith with a separate durable worker:

Browser → API → application database / durable commands → worker/scheduler → provider, maintenance-engine, verification, and GitHub adapters.

System services may supervise API, worker, and local model processes. Product workflow belongs in application code and durable records, not scattered service scripts.

Long-running model, verification, and publication operations must not depend on HTTP request lifetimes or in-memory background callbacks. Persist progress and reconcile incomplete operations after restart.

Keep provider execution behind adapters; retain working CLI execution initially rather than changing authentication/billing semantics without investigation.

Maintain one authoritative owner per field/transition. Legacy worker files may remain authoritative behind a compatibility adapter during migration. No competing writable sources or blind bidirectional synchronization.

Single-host SQLite is the provisional baseline. Revisit database/deployment choices if actual multi-host or concurrency requirements justify it. Do not introduce distributed infrastructure speculatively.

The proposed stack remains React/TypeScript/Vite, FastAPI/Pydantic, SQLAlchemy/Alembic, separate Python worker, and focused unit/browser testing. This document does not establish that any component is installed.

## 18. Development and evidence standards

- Focused milestones, not one comprehensive rewrite.
- Current code and fresh results outrank old handoffs.
- Preserve user work, private data, game files, and runtime state.
- No destructive cleanup, commits/pushes, external submissions, deployments, or live transfers without authorization.
- Ordinary tests use sanitized fixtures and disposable databases, not real model calls or GitHub writes.
- Use reproducible noninteractive commands and retain useful failure artifacts.
- Inspect actual rendered screenshots before claiming visual completion.
- After two substantive failed approaches to a blocker, stop and report evidence and the smallest next action.

Report separately: requested, reported implemented, repository-verified, unit/integration-tested, browser-tested, screenshot-inspected, simulator-tested, loaded, live-observed, real-client-tested, and published.

## 19. Shared guidance for Claude and Codex

Recommended documentation:
- AGENTS.md: short execution rules, commands, safety, and links.
- CLAUDE.md: thin entry point to the same shared guidance.
- docs/PRODUCT_PRINCIPLES.md: audience, product promise, autonomy, terminology, information hierarchy.
- docs/DESIGN_SYSTEM.md: tokens, components, responsive behavior, accessibility, visual references.
- Status/backlog document: changing implementation state and acceptance gaps.

Avoid duplicate product rules in competing agent files. Stable principles belong in shared guidance; changing feature state belongs in status records.

Implementation prompts should name an available model and reasoning effort appropriate to the task, verifying current labels when necessary. Do not make model-version names permanent architectural assumptions.

## 20. Delivery direction and immediate next step

The initial implementation remains bounded: executable architecture and one fixture-backed task → review → durable decision → worker processing → displayed outcome workflow.

Do not interrupt it to implement this roadmap. Review its report and evidence against the required extension points, then select independently useful milestones.

Suggested subsequent work, subject to baseline findings:
1. Consolidate shared product/design guidance and inspect the rendered baseline.
2. Establish hierarchical backlog, intake records, and revision-bound proposal contracts.
3. Add provider configuration, attempt accounting, and the Models page.
4. Add researched feature proposals, approvals, and controlled execution slices.
5. Add simulator evidence and feature-level publication gates.
6. Add upstream-data monitoring and adaptive capacity policies.

Enable SSO before broader network exposure; sequence it by deployment need rather than treating it as a late optional polish item.

### Decisions still requiring implementation-time verification

- Current repository contents, visibility, and milestone results.
- Actual single-host/concurrency requirements.
- Supported provider telemetry and model/effort discovery.
- Exact quota windows, shared-account usage visibility, and entitlement behavior.
- Existing What's Training source, extraction command, validation, and distribution path.
- Current simulator capabilities and available scenarios.
- Reverse proxy topology and authentik trust configuration.

## Source and status note

This document consolidates the owner's direction and the planning recommendations discussed on November 3, 2026. It is not independent inspection of a repository, service, account, simulator, or live game. Proposed mechanisms remain subject to focused implementation-time verification. It does not activate new permissions or change the scope of the implementation already running.

## 21. Proposed initial release — reconciliation, November 4, 2026

**Proposed for owner review; no new release scope or policy is accepted or active.**
This section supersedes §20's historical initial fixture target as a proposed
release promise. Implementation/acceptance facts remain in PROGRESS.md; ordered
packages remain in PLAN.md. Requirements below are stable IDs for that one plan,
not a separate backlog. Historical evidence establishes only the bounded behavior
it actually exercised. “Implemented” never means loaded on the preserved review.

Leave Workbench running to find worthwhile TwichUI work, plan eligible tasks,
implement changes in isolation, verify them, and prepare reviewable draft PRs.
The owner can request features, report issues, inspect work and pause it.

Initial release includes repository-wide discovery, issue/feature intake, bounded
subtasks/dependencies/priorities, real isolated local-first implementation, one
working Codex fallback with enforceable limits/bounded retries, meaningful checks
and required visual evidence, readable diffs/reliable draft delivery, manual and
approved scheduled publication, operational feedback/control, protected supported
proxy access, backup/restore/recovery/reproducible startup and demonstrated
unattended operation. Models presentation remains accepted and closed; runtime
integration must consume its contract without reopening its design.

Exclusions: automatic merge, game deployment, unapproved spending/provider
activation, speculative allowance guarantees, automatic category grouping,
automatic conflict repair, external notification delivery, Claude and the other
later iterations below. A sample prepared result proves none of the real delivery
requirements. Access foundations precede any broader exposure.

Release acceptance requires evidence for the complete real authorized isolated
workflow, including deliberate no-work/wait/pause outcomes, bounded fallback and
retries, current approval/checks, real diff integrity, uncertain remote-write
reconciliation without duplication, policy-governed manual/scheduled draft PRs,
protected API/artifacts, restore/restart drills and unattended observation. The
owner must separately accept each promised outcome. Local mocks and Chromium
phone viewports cannot substitute for provider/host/physical-client evidence.

### Requirement traceability

R1/P1–P8 refer to the ordered initial-release packages in PLAN.md; L1–L7 are the
proposed later order there. Evidence paths are repository-relative. Each acceptance
condition is future owner review unless explicitly recorded accepted in PROGRESS.

| ID | Requirement / release iteration | Existing evidence (limited) | Remaining work | Acceptance condition |
| --- | --- | --- | --- | --- |
| REL-01 | Diagnose reverse-proxy failure; R1/P2 | WB-08, loopback review only | Establish topology; inspect proxy/auth/backend/Host/Origin/forwarding and reachability; firewall is only a hypothesis | Reproduce failing route, demonstrate diagnosed fix through supported protected route without backend bypass |
| REL-02 | Discover worthwhile repository-wide work beyond unit tests; R1/P3 | Existing fixture/catalog projections; no broad discovery acceptance | Evidence-led code, compatibility, reliability, performance and usability discovery using policy below | Retained representative findings and valid no-work cycle with evidence and dispositions |
| REL-03 | Larger-task decomposition/dependencies; R1/P4 | M3 hierarchy/dependency records and demo worker; WB-15 real grounding unaccepted, 2/2 approaches exhausted | Grounded bounded subtasks and integrated feature completion; preserve exhausted investigation | Real approved scope decomposes with explicit prerequisites and end-to-end acceptance |
| REL-04 | Upstream UI/reference changes and addon impact; L1 | §14 describes prior upstream data intent, no monitoring proof | Clarify repositories, revisions and impact analysis; dedupe/no-change handling | Known source update yields evidenced impact or no change, without unsupported implementation authority |
| REL-05 | Working Codex fallback / paid fallback initially Codex; R1/P5 | Accepted Models policy/installed metadata, no runtime activation; WB-19 | Supported real execution adapter, bounded routing and attribution under separately approved policy | Local failure invokes permitted Codex once within limits, records actual model/outcome, no billing conversion |
| REL-06 | Tasks view; R1/P1/P4 | Catalog.tsx / intake tasks, M2/M3 browser evidence | Integrate actual execution/verification/delivery states without parallel task records | Owner can find task, scope, dependencies, current stage and result |
| REL-07 | Responsive worker eligibility checks; R1/P3/P8 | Existing worker short polling and deterministic gates | Measure real wake latency, preserve cheap checks separate from model calls and fairness | Eligible change picked up within agreed bound without costly polling/inference churn |
| REL-08 | Code diffs for ready-to-publish work; R1/P1/P6 | candidate_review.py, synthetic comparison only in this package | Real Git base/tree/artifact provenance and safe supported text/binary/rename/delete handling | Exact ready candidate produces readable bounded real diff; unavailable inputs fail visibly |
| REL-09 | PR pipeline and reliable draft delivery; R1/P1/P7 | pr_preparation.py / 0009 deterministic fake only | Real host adapter, configured target/base, remote reconciliation, delivery holds | One logical real draft per intended operation, with durable URL/outcome and safe uncertain retry |
| REL-10 | Decision saved → awaiting application → confirmed/error; R1/P4/P8 | Existing commands/applications and intake decision UI/worker tests | Confirm all actual runtime controls use durable feedback and freshness | Saved action clears needs-you; application/error/stale is observable after restart |
| REL-11 | Honest loading/progress, especially PR creation; R1/P1/P7/P8 | Existing pending states; sample waiting/preparing/reconciling/history | Real stage/timing/wait reasons and disconnect recovery | No false completion/percentages; saved, working, uncertain and complete are distinguishable |
| REL-12 | Automatic PR when “what is training” changes; L7, unresolved | §14 records earlier What's Training addon interpretation | Owner clarification: training addon data, reference repo or other meaning; combine L1 if same | Agreed source/meaning/transform and policy yield deduped checked draft or no-change result |
| REL-13 | Workbench in its own Git repository; R1/P2 | Local .git and configured origin; prior remote existence inspection in ownership.md; no local commits at reconciliation | Separately verify intended committed contents/remote synchronization when authorized; no push tonight | Own repository exists AND separately evidenced intended remote synchronization; configuration alone is insufficient |
| REL-14 | Backlog and priorities; R1/P3/P4 | Catalog/backlog and intake priorities, M2/M3 evidence | Grounded findings, dependencies, explicit user priorities and eligible scheduling | Owner can inspect/reorder work with reasons; large/uncertain work becomes proposal/investigation |
| REL-15 | Request a Feature; R1/P4 | Intake.tsx, original persistence/proposals/human scope, M3 accepted with limits | Grounded real research/decomposition and integrated delivery | Original request persists before work; revision-bound approval leads to verified feature-level delivery |
| REL-16 | Issue reporting; R1/P4 | Intake original title/description/Lua error and tests | Reproduction/duplicate triage and reliable task linkage | Original issue preserved; diagnosis separately evidenced and actionable or explicitly uncertain |
| REL-17 | Configurable usage limits and bounded retries; R1/P5 | Models preview/attempt limits, WB-17 unknown allowance | Enforce attempt/time/concurrency/output ceilings and bounded retries at actual fallback boundary | Oversized/exhausted work waits/stops; attempts cannot exceed configured enforceable limits |
| REL-18 | Eventually allowance-aware routing / cross-provider optimization; L6 | WB-17/18: allowance/capability unknown; estimates separate | Supported telemetry, windows/reserve/unknown handling, reliable provider comparisons | Demonstrate limits using supported observations; unknown never treated as free/zero usage |
| REL-19 | Start/pause/resume; R1/P8 | Existing display/control boundary, no new execution authority | Application-owned scheduler controls and worker-effective acknowledgement | Pause stops new admission; running work/cancellation distinct; resume safely reconciles |
| REL-20 | Basic statistics R1/P8; richer statistics L4 | Accepted Monitoring/Models UI, known pilot telemetry gaps WB-06/07 | Reliable actual task/attempt/outcome/wait totals; richer trends later | Counts reconcile to durable operations and disclose unknown/stale readings |
| REL-21 | In-app notifications R1/P8; external L4 | Local outcomes/status only; WB-14 external unverified | Actionable deduped in-app events; authorized external transport later | Needs-attention/completion visible, updated after action; external delivery separately demonstrated |
| REL-22 | Create PR now; R1/P7 | Explicit Prepare sample PR only | Real manual action through same eligibility/host reconciliation as schedule | Creates eligible draft once; never bypasses approval, verification or holds |
| REL-23 | Merge-conflict repair; L3 | No real repair evidence | Bounded isolated new attempt under approved scope, renewed checks and conflict inspection | Repair cannot force-push/merge or reuse stale checks; stop when bound exhausted |
| REL-24 | Category metadata R1/P1; category-based PR grouping L2 | New request category; earlier catalog labels not destructively rewritten | Agree compatible grouping by dependency/review scope as well as category | Preview records primary category separately from risk/tags; later grouping preserves scope gates |
| REL-25 | Meaningful verification and visual evidence; R1/P6 | Original controlled verifier accepted; candidate label checks unaccepted; WB-09 physical Safari unverified | Real behavior/integration checks; visual evidence appropriate to scope | Required checks current at exact candidate; approved design, implementation, simulator and physical client are distinct |
| REL-26 | Real isolated local-first implementation; R1/P5 | m5-implementation inert JSON sample/atomic attempt only, unaccepted | Safe checkout/base contract, real executor, bounded artifact ownership/recovery | Approved eligible scope changes isolated files with attributable candidate, no live game access |
| REL-27 | Manual/scheduled approved publication policy; R1/P7 | Real publication remains disabled; no scheduler tonight | Disabled/manual/scheduled, schedule/timezone, backlog/batch limits, eligibility, open-PR update policy | Manual and scheduled actions share gates, reconcile remote uncertainty and obey approved limits |
| REL-28 | Access protection; R1/P2 | Exact loopback Host/Origin/CSRF guards, §16 proposed authentik | Supported trusted proxy/SSO, protected API/artifacts and spoof/bypass prevention | Protected route works; unauthenticated and forged forwarded identity/bypass fail closed |
| REL-29 | Backup/restore; R1/P2 | Disposable upgrade/restore tests and preserved review backups | Document and drill owned full-store/evidence backup/restore with retention/permissions | Restore retains commands/results/artifacts and does not duplicate interrupted effects |
| REL-30 | Recovery; R1/P2/P5/P7/P8 | Existing command atomicity, candidate crash tests, new fake ledger recovery | Real filesystem/provider/host boundaries and active operation recovery | Restart at each real boundary yields one correct reconciled outcome or explicit hold |
| REL-31 | Reproducible startup; R1/P2 | README locked setup, wb wrapper and port guards | Supported-host repeatable startup, ownership/config schema and documented prerequisites | Fresh authorized setup and restart reproduce supported protected service without port replacement |
| REL-32 | Demonstrated unattended operation; R1/P8 | Short disposable workflows only; WB-13 not a task-survival guarantee | Agreed observation period, restart/failure/no-work/pause drills with limits | Retained unattended run demonstrates useful work or reasoned waits without manual hidden intervention |
| REL-33 | No automatic merge or game deployment; all releases | Existing prohibitions, all simulated responses deny permissions | Preserve explicit separate boundaries in real adapters | No enabled path merges/deploys by preparation, schedule, category, approval or verification implication |
| REL-34 | iPhone/accessibility and physical Safari; R1/P6/P8 | Chromium 390px/200%/keyboard evidence; WB-09 open | Inspect real supported client where required | Real-device/browser limitations explicitly accepted; viewport tests remain accurately labeled |
| REL-35 | Worthwhile-work policy; R1/P3, proposed only | Product no-manufactured-work principle; policy below | Review and bind findings/dispositions to revision and authorization stages | Representative qualifying/excluded cases and no-work cycle satisfy approved policy |
| REL-36 | Claude/second fallback; L5 | Models configuration preview only; WB-18 unknown capabilities | Supported adapter, model/effort capabilities, limits and explicit activation | Separately enabled fallback works within limits without silent substitution/spending |

### Proposed worthwhile-work policy — document only, inactive

Every finding records the problem/request and source evidence, exact affected
revision/paths, expected benefit, scope/risk/dependencies/verification, duplicate or
related-task assessment, and disposition with reason. Category confers no authority.
Discovery, implementation authorization, verification and publication permission
remain separate stages.

Qualify reproducible bugs/concrete expected-behavior violations, demonstrated
compatibility problems, specific reliability failures, measured performance issues,
reported/reproducible usability problems, user-requested features, and meaningful
test gaps tied to behavior or regression risk. Exclude style churn, speculative
optimization, broad refactors without demonstrated need, test-count inflation,
rejected ideas repeated without new evidence and work created merely to stay busy.

Proposed priority, superseding §12's older proposed order for this release:
(1) serious confirmed failures/compatibility blockers; (2) explicit owner priorities
and approved features; (3) bugs/reliability; (4) demonstrated performance/usability;
(5) targeted tests/maintenance. Explain evidence, impact, verification confidence,
effort, dependencies and risk without unexplained numerical scores. Large work
becomes proposals/subtasks; uncertainty becomes investigation. “No worthwhile
eligible work” is a legitimate cycle outcome. No discovery/scheduler policy is
activated by this reconciliation.

Primary categories proposed: Bug fix, Compatibility, Reliability, Performance,
Usability / visual, Feature, Tests, Maintenance. Store a primary category separately
from risk and optional tags; preserve historical classifications. Tonight these are
sample-preparation request metadata only; automatic grouping remains later.
