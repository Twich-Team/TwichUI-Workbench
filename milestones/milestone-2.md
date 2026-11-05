The human has explicitly approved Milestone 2 in docs/PLAN.md for this one run.
Read AGENTS.md, docs/EXECUTION.md, docs/PLAN.md, docs/PROGRESS.md,
docs/PRODUCT_PRINCIPLES.md, docs/DESIGN_SYSTEM.md and docs/ownership.md.
Preserve Phase 1 and all user changes. Implement only PLAN.md's bounded Milestone 2.
Continue across internal tasks without asking for routine plan approval. Record
small reversible decisions in PROGRESS.md. Do not start another milestone.

Use the already configured coding model at High effort, one agent and one attempt.
No recursive Codex/Claude invocation, subagents, agent teams, retries of the whole
run, new spending, paid fallback or changes to authentication. Respect the enclosing
runtime and artifact budget; finish with durable progress before reaching it.

Implement, test, inspect actual rendered desktop/phone screenshots and repair
in-scope failures. Do not weaken tests, remove gates or accept broken baselines.
After two substantive failed approaches to one blocker, stop that investigation
and record evidence plus the smallest next action. Continue independent in-scope
work where possible; stop for material scope/permission gaps.

No sudo, service replacement, port 8765 changes, live writes/data migrations,
model-provider executions for the product, GitHub writes, commits, pushes, PRs,
deployment, cutover, allowance/approval changes, private-artifact copying, or game/
WTF/SavedVariables changes. This run implements Workbench, not addon features.
A safely available read-only pilot observation is permitted within PLAN.md.

Run python3 scripts/wb.py verify. Inspect real screenshots with the image-view tool;
write inspected paths and remaining acceptance gaps into docs/PROGRESS.md. Never
claim real-device Safari or live-worker loading from Chromium fixture acceptance.
Return the required structured terminal result. READY_FOR_REVIEW requires passing
checks, actual desktop/phone screenshot inspection and no unresolved required
acceptance. Otherwise use BLOCKED, BUDGET_EXHAUSTED or INTERRUPTED truthfully.
