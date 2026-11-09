# Shared design system and provenance

## Active November9 styled-interface assignment

The owner now authorizes the full saved frameless WoW-inspired styling and everyday
interface delivery. This supersedes older statements below that prohibit broad
redesign or require the old Work/History/Models navigation. Use Dashboard, Backlog,
Results, Settings; preserve deep links and working execution/approval safeguards.
Use the supplied leather materials and reference in `DASHBOARD_ASSETS/README.md`.
No ornate perimeter frame; crafted headers, functional panel edges, raised controls,
warm ivory reading text. Current state must come from authoritative records, never
an LLM summary or a decorative spinner. Future capabilities stay discoverable with
honest availability. Styling does not authorize new cancellation semantics.

Use the root requirements and standing owner direction for current priorities
and engineering authority. The dated milestone statements below are historical;
they do not reimpose old campaign or routine engineering approval restrictions.
The broader styling phase remains after functional delivery.

Shared Tailwind theme tokens live in `frontend/src/styles.css`: espresso background,
walnut panels, aged bronze borders/actions, ivory text and muted olive for applied
outcomes. Georgia provides a local serif title face; system fonts cover body text.
No external fonts, imagery or runtime CDN are required.

Selected shadcn/ui Button and Dialog composition patterns are adapted to these
tokens, using Radix Slot/Dialog and class-variance-authority. Upstream MIT notice
is retained in `docs/licenses/shadcn-ui.txt`. Icons use the installed lucide-react
package and its bundled ISC license. The leaf is a generic icon, not a claim to
reuse the original TwichUI emblem. No pilot artwork, addon or game asset was copied.

Work, Backlog and History share the shell. Task and record details lead to the
existing route-addressable fixture review sheet where supported.
There are no placeholder Overview/Backlog/History/Settings controls. Native radio
groups require every choice explicitly, optional notes are bounded, focus stays
inside the sheet, Escape closes it, and focus returns to the review link. Status
text separates a saved pending command from an applied, stale or failed outcome.
Diagnostic hashes and IDs stay in secondary disclosures. Responsive layouts cover
320, 390, 768, 1024 and 1440 pixels without hiding the task's important states.

## Interaction rules for subsequent milestones

Use `PRODUCT_PRINCIPLES.md` as the product authority. Preserve the implemented
walnut/espresso/bronze/ivory tokens and components; no broad redesign is approved.
Lead with meaning and the next useful action, then a concise explanation, then
secondary evidence. Distinguish task, attempt, verification, review, publication
and freshness. Pending receipts are no longer actionable review requests.

Desktop may combine list/detail panes; phones use stacked pages or a full-width
sheet. Prefer 44px touch targets, visible keyboard focus, text enlargement,
reduced motion and controls usable without hover. Contain true two-dimensional
diffs instead of overflowing the page. Test text zoom and missing/long metadata.

Use real recorded stages/counts only. No placeholder settings, invented activity,
decorative charts or fabricated live facts. Local fixture mode must be clear.
No roadmap navigation entry should appear until its destination works.

Validate desktop and phone screenshots after meaningful visual changes; inspect
actual rendered pixels before updating a baseline. A passing comparison does not
establish that the initial design is good. Chromium viewport acceptance remains
separate from actual iPhone/mobile Safari acceptance. Real-device acceptance is
not claimed by this project yet. For visual addon features, approved in-game
mockup and implemented simulator evidence must bind to their respective revisions.

Milestone 2 uses rem font sizes, preserving the original default sizes while honoring
browser text enlargement. Open work is the default backlog view; all records and
flat search remain available. Per-source branch expansion survives record navigation.
Twelve Linux/Chromium regression images cover Work, review, Backlog, record details
and History at desktop/phone widths; actual device Safari remains a separate check.

## Milestone 2 acceptance iteration

Owner-approved workshop art is local in `frontend/public/art/workshop`; provenance
and unchanged source are retained there. The source already had alpha, so no white
color-key was used. `scripts/extract_workshop_art.py` preserves parchment/highlights.
Nine normalized assets; only Work/Backlog/History and decisions are integrated.
Navigation and status remain Lucide icons; the generic leaf is unchanged.
Operational headings replace editorial slogans. Compact cards and History entries
lead with recorded meaning; evidence/coverage is expandable. Stale/unavailable
source warnings remain visible. Tree borders, plain type names, state chips and
separate 44px expand/detail controls distinguish hierarchy without inferring order.

Refinement: neutral espresso canvas/walnut panels; bronze for selection, amber
review states, muted blue working, sage recorded verification/outcomes, dusty red
unsuccessful attempts, stone unknowns. Labels remain explicit; verified is not
user approval or publication. Distinct vector navigation icons supplement approved
heading art. Filter controls are keyboard-native checkboxes in a phone disclosure.

## Operating controls milestone

Original owner-supplied `twichui-launcher-source.png` now replaces the generic leaf
in desktop/phone navigation, unchanged. Its source is RGB with a baked checkerboard;
no transparency removal or new logo is fabricated. `frontend/public/art/branding/`
retains supplied provenance without a license claim. Favicon deferred because a
clean tiny unchanged treatment was not established. Existing workshop art remains.
Five working destinations include Settings and Monitoring; phone navigation wraps.
Record types sit beside titles; organizational rows show actual subitem counts,
without parent execution state. Shared semantic tones cover task/review/history.

Monitoring loads the existing pinned Apache ECharts 5.6.0 bundle locally with its
LICENSE, NOTICE and retained provenance. Percent utilization and GiB memory charts
are separate; explicit gaps, bounded reading tables, slider/touch exploration and
keyboard zoom/reset controls supplement tooltips. Refresh keeps absolute explored
windows and legend choices. Deliberate time-range changes reset exploration.

WB-16 adds the same human interface-scope selector to intake and proposal review:
“Changes the in-game interface”, “Does not change the interface”, “Not sure”
(default). The review identifies human scope separately from planner classification,
shows contradictions beside reference requirements, and creates a fresh revision
for scope edits. Stale reviews retain the old proposal and offer an explicit
“Open current proposal” action. Original forms, theme and held-execution copy
remain in place. Desktop/390px screenshot evidence lives under
`artifacts/screenshots/wb16-*.png`; no physical-device acceptance is implied.


Milestone 4 adds Models using the existing workshop art and Cpu navigation icon.
Provider cards separate saved inclusion, activation, availability, capability and
usage. Model IDs/provenance remain expandable. Native configuration controls and
explicit fallback ordering preserve the shared palette; synthetic routing cases
are visibly labeled. Activity uses accessible count meters with exact values and
separate task/attempt labels, dated job outcomes and source-specific observed token
counts only when records exist. Missing history has explanatory text. Model forms
stack on phone and at enlarged text; no new provider branding or generated logos.


Models refinement separates `/models` (saved overview / observed or explicit demo
activity) from `/models/configure` (one provider's model preferences, routing settings
and secondary preview). Cards lead with actual selected identifier, routing role,
inactive execution and recorded counts where available. Repetitive unknowns are
expandable; material failures/staleness remain visible. Manual model identifiers are
advanced; discovered choices and supported effort remain ordinary controls. The
same count-meter charts display explicitly synthetic activity without merging it
with observed provider totals. Unsaved notes and browser unload warnings preserve
configuration edits; phone layouts and 200% text were inspected without altering
existing visual baselines.
