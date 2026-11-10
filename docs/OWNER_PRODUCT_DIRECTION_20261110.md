# Product direction addendum — November 10, 2026

The owner approved this direction for future TwichUI Workbench releases. It
supplements the unchanged [v1.1 requirements](../TWICHUI_WORKBENCH_REQUIREMENTS_v1_1.md)
and preserves their historical decisions and evidence. The current priority is
the ordinary real bug report through an independently checked result in the
installed app. This addendum does not change current permissions, provider
settings, active tasks, or publication holds. See the [roadmap](PLAN.md).

## Ordinary work and provider choice

- Normally take supported feature requests through investigation, planning,
  implementation, and independent verification automatically. Keep the plan,
  scope, changes, and checks visible. Ask only when intended behavior is
  materially ambiguous or an action exceeds existing permission.
- Keep the initial provider setup simple: local Qwen is primary, Codex is
  deliberately selectable for eligible work, and fallback is configurable.
- When Qwen genuinely struggles, Workbench should switch automatically to
  eligible Codex only under a preauthorized policy. Use evidence of a substantive
  obstacle or lack of progress. Confirm capacity, spending limits, disclosure
  permissions, and prior execution state before switching. Do not duplicate
  uncertain work or silently use paid API capacity. If those conditions cannot
  be established, preserve the work and explain why it cannot switch.

## Proactive work and review

- Discover, prioritize, and implement objectively low-risk addon improvements
  within configured scope. Use recorded evidence and explicit eligibility
  rules, such as an independently checkable test addition that changes no
  production behavior. Leave consequential behavior changes to the owner.
- Separate discovery from permission to implement. Offer continuous operation,
  idle-only operation, and scheduled working hours. Starting work must respect
  the chosen schedule, resource availability, and pause controls. Define and
  demonstrate what happens to in-flight work when paused before unattended use.
- In-app notifications are sufficient initially. Surface decisions, results,
  and failures needing attention; external delivery is a later option.
- Automatically prepare and submit qualifying draft PRs under standing
  publication authorization recorded for the applicable repository and scope.
  Require independent checks of the exact candidate, applicable safeguards, and
  reconciliation of uncertain submission before any retry. Group related
  low-risk changes when the combined result can be checked; keep urgent fixes
  separate. The owner is responsible for merging. Workbench must not merge,
  release, or bypass publication safeguards automatically.

## Smart Model Allocation (Phase C)

Manage **planned use**, **fallback reserve**, and **unallocated capacity**
independently for local Qwen, Codex subscriptions, and Claude subscriptions.
Use measured local resources and configured limits for Qwen. Subscription
policies use verified capacity and the applicable provider allowance windows.
For example, a Codex policy could allocate 50% to planned work, reserve 25% for
fallback, and leave 25% unallocated. After planned use is spent, Qwen becomes
primary while eligible fallback still has access to the approved reserve.
These figures are examples, not defaults. Unallocated capacity stays unused
unless policy explicitly assigns it.

Use official, supported sources for subscription usage and remaining
allowance where available. Retain the source, units, window, and freshness.
Show unavailable or stale data explicitly; do not invent balances, infer
subscription percentages from tokens, or rely on undocumented endpoints.
Keep three distinct records: Workbench-observed activity and tokens,
provider-reported subscription allowances, and separately billed API usage
and spending. Do not assume that any particular provider exposes all of this.

The eventual dashboard should have interactive, gamer-friendly charts for
provider activity, reliable remaining allowance, usage trends, successful work,
and fallback events. Keep daily status simple and put allocation controls and
diagnostics in advanced views. Follow the
[approved WoW-inspired visual direction](../TWICHUI_WORKBENCH_WEB_VISUAL_DIRECTION.md).

## Reconciliation with v1.1

- **A1–A3:** The default for supported features is autonomous delivery within
  permission. Material behavior ambiguity and uncovered consequences still
  require the appropriate decision.
- **A8:** The initial documented fallback is pre-generation. The later
  progress-triggered switch extends it only once safe settlement, supported
  access, and budget rules are demonstrated. The original vendor task stays
  Qwen-only, fallback Off, publication held.
- **A5, C2, C5:** The direction for automatic draft PRs is settled under
  standing publication authorization. Exact destinations, eligible upstream
  events, batch limits, and any needed grant remain implementation decisions
  or permissions; a product requirement is not an active publication grant.
- **A7, Phase B, C1:** The scheduling choices and initial in-app notification
  channel are settled. Exact pause behavior for a running task and any later
  external notification channels remain open.
- **C6:** The earlier 75% / 25% allocation was illustrative. The new direction
  requires three independently managed buckets per provider and honest usage
  reporting. Actual supported allowance access, capacity measurement, reserve
  release and borrowing, and model eligibility still need verification.
