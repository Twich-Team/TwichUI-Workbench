# TwichUI Workbench — Web Visual Direction

Version: 1.1
Date: November 5, 2026
Status: Owner-selected visual baseline and candidate material assets; not proof of implementation, tested accessibility, or production asset readiness.

## 1. Purpose and authority

Make Workbench feel like a WoW-inspired companion to TwichUI: warm, crafted, approachable, and useful. The owner explicitly prefers the stronger game-addon character of the latest frameless render over a cleaner conventional web-dashboard interpretation. Do not flatten that character merely to follow modern web fashion.

Read alongside:
- PROJECT_WORKING_PHILOSOPHY.md — working principles.
- TWICHUI_WORKBENCH_REQUIREMENTS_v1_1.md — product capabilities, priorities, and permission boundaries.
- DASHBOARD_MOCKUP_VISUAL_DIRECTION_CLEAN.png — current visual baseline.
- DASHBOARD_ASSETS/ — candidate material textures.
- TwichUI_Style_and_Art_Direction.md — source addon direction, when available.

The word CLEAN in the reference filename identifies the frameless version; it does not instruct a flat or minimalist restyle. The source addon document is a supplemental reference, not a prerequisite that should block use of the current owner-selected concept.

This document supersedes version 1.0 where its emphasis on conventional web simplification conflicts with the owner's newer preference. It does not authorize implementation, new dependencies, paid work, publication, or network changes.

## 2. Document roles and precedence

The working philosophy governs how work is assigned and reported. The requirements govern capabilities, release priorities, and recorded owner decisions. This guide governs visual interpretation. The current render illustrates the intended appearance, not task state or implemented capabilities. Texture files supply materials, not finished controls or complete layouts.

Use explicit later owner decisions to resolve conflicts. Do not use an attractive mockup to override truthful progress, accessible controls, or approval boundaries. Record unresolved conflicts instead of silently guessing.

## 3. Selected appearance

A WoW-inspired companion application with clear, readable controls.

Preserve:
- Deep espresso backgrounds and warm dark-brown interiors.
- Chestnut leather title bars and section headers.
- Muted bronze edges and dividers.
- Shallow raised buttons with selective antique-gold emphasis.
- Warm ivory text, characterful major headings, and readable body text.
- The reference's sidebar, selected navigation treatment, section hierarchy, and restrained task-type colors.
- Tactile material character and modest depth rather than generic flat surfaces.

Remove from the app shell:
- The heavy decorative perimeter frame.
- Large ornamental corner fittings.

Retain the crafted top identity bar. Thin functional panel edges, subtle rounding, and header detail are appropriate. Frameless does not mean borderless or textureless.

Avoid bright gold everywhere, orange/red washes, neon, particles, fantasy scenes behind content, decorative gauges, excessive nested frames, and technical-console density. Do not substitute generic charcoal-and-teal styling.

## 4. Reference and asset inventory

Current reference:
- DASHBOARD_MOCKUP_VISUAL_DIRECTION_CLEAN.png

Candidate assets, using the actual uploaded folder spelling:
- DASHBOARD_ASSETS/BACKGROUND_LEATHER.png — deep espresso page background.
- DASHBOARD_ASSETS/HEADER_LEATHER.png — warmer chestnut title-bar and section-header material.
- DASHBOARD_ASSETS/PANEL_INTERIOR_LEATHER.png — intended quieter dark material for cards and sidebar.

The folder is DASHBOARD_ASSETS, not DASBOARD_ASSETS. Preserve filenames unless a deliberate migration also updates references.

The first two textures were discussed visually with the owner. The third is present in the uploaded inventory; its actual appearance has not been independently inspected in this document review. None of the three is certified here as seamless, optimized, or production-ready.

These are candidate source materials for the chosen design, not a mandate to use every file if browser inspection reveals redundant or unsuitable textures. Preserve the originals. Keep a simple asset record of origin, intended role, transformations, and permission/licensing information where applicable. Do not invent provenance or claim Blizzard supplied this artwork.

## 5. Preparing textures for actual use

Before calling an asset ready:
1. Inspect the original image, dimensions, format, and transparency.
2. Check repeated copies side by side and vertically for seams and conspicuous repeating marks.
3. Inspect its scale under real text and controls at desktop and phone sizes.
4. Check brightness, color consistency, readability, and unnecessary visual noise.
5. Prepare efficient web derivatives where useful, preserving source files and recording transformations.
6. Verify that missing images leave readable, usable fallback surfaces.

A requested seamless texture is not proof of seamless output. Do not mark these checks complete without doing them. Asset preparation can correct routine issues within the authorized visual assignment; do not require the owner to supervise each crop or export.

Use the textures as decorative material layers. Build text, controls, layout, statuses, borders, and interaction states as actual web elements. Do not turn a blank full-screen render into a fixed-size dashboard background with invisible click targets.

Prefer shared web styling for rims, bevels, corners, shadows, and button states. Add custom decorative art only where it improves fidelity. Do not stretch a complete panel image to fit arbitrary content.

## 6. Palette and surfaces

Exact web color values are not approved by this guide. Derive a coherent palette from the selected render and assets, then verify it in the browser. Do not claim guessed values were extracted.

Roles:
- Page: quiet deep espresso.
- Panels/sidebar: dark warm brown, distinct enough from the page to define structure.
- Headers: chestnut leather, warmer than panel interiors.
- Structure: muted aged bronze.
- Primary emphasis: selective antique gold.
- Primary text: warm ivory.
- Supporting text: readable warm neutral, not faded into the texture.
- Semantic states: distinct success, warning, failure, and activity treatments with labels and/or icons.

Use material detail in the header, section bars, sidebar, and selected controls. Keep task-card interiors quieter. The owner-selected concept permits subtle texture behind routine content; the earlier blanket preference for predominantly untextured reading surfaces is superseded. Dense reading, forms, code, logs, and comparisons should remain smooth or nearly smooth.

## 7. Typography and spacing

Preserve the reference's characterful app identity and major headings. Use readable body and control typography. Do not force every label into an ornate fantasy face. Reserve monospace for code and advanced details.

Keep hierarchy clear: page purpose, current task, meaningful stage, next action, supporting detail. Use comfortable controls and spacing. Important explanations must remain readable with enlarged text. Font families, licenses, exact sizes, spacing, and radii remain implementation choices to verify, not extracted approved standards.

## 8. Navigation

Use the reference's starting structure:
- Dashboard.
- Backlog.
- Results.
- Settings.

Keep Create a task prominent and reachable in short windows and mobile layouts; it need not remain literally at the sidebar bottom on every screen. These labels are the current design baseline, not evidence that each route already works.

Keep all intended work discoverable: bugs, features, inspections, coverage, simulator work, data updates, and other supported tasks. Do not replace the shared entry point with a bug-only workflow. Keep advanced diagnostics available without using internal system names as primary navigation.

## 9. Dashboard

Answer immediately:
1. What is working now?
2. Does it need me?
3. What finished?

Working now: task name, type where useful, actual stage, useful latest update, and activity only when active. The reference's Understand → Work → Check → Review sequence is a compact summary, not a substitute for explicit queued, blocked, waiting, failed, or paused states. Do not imply every task changes code. Never invent a percentage or reliable completion time.

Needs your attention: show the actual decision and an obvious action such as Review plan or Answer question. After a recorded decision, show Decision made — waiting to apply when appropriate, rather than an unanswered approval alert.

At a glance: a few real counts such as working, queued, and needing the owner. Explain filter scope when necessary. Counts and lists must agree; mockup numbers are illustrative, not acceptance data.

Recently completed: show meaningful outcomes. Inspections can produce findings without code changes; tests and simulations produce reports; changes can be ready for review without being published. Do not equate passed checks with publication or final acceptance.

Keep token counters, CPU graphs, detailed capacity, and service diagnostics in a secondary view unless a real issue requires attention.

## 10. Backlog

Use spacious rows or cards with task name, type, status, and useful priority/grouping. Give active work a subtle highlight and activity indicator. Distinguish Queued, Needs your input, and Needs approval. Retain completed, held, and failed work.

The reference's search and All tasks / Needs you / Completed filters establish a useful starting layout. Verify behavior against real data; sorting, grouping, and reprioritizing should follow supported capabilities rather than implying unbuilt features.

Status cannot depend on color alone. Task-type color should not compete with required decisions. Group related test improvements when appropriate; urgent fixes may be reviewed separately; features should keep related work together. Do not impose a daily PR schedule.

## 11. Task entry, reviews, and results

Task entry starts with What would you like Workbench to do? Offer optional type guidance and conversational clarification, not internal preparation choices.

Review explains understood scope, intended behavior, checks, relevant limits, and requested permission. Make consequential boundaries visible without requiring engineering knowledge. Do not show approval as recorded before it is recorded, or treat plan review as execution/publication permission.

Results lead with outcome, then checks and limitations, then optional evidence. Treat findings, test reports, simulator results, and code changes as distinct valid results. Use readable conventional code comparisons with meaningful added/removed indicators and little material ornament.

## 12. Controls and honest states

Use shallow raised chestnut/espresso buttons, modest bronze edges, and selective gold primary actions. Secondary controls should be quieter. Destructive actions need explicit wording and appropriate warning.

Verify resting, hover, pressed, keyboard focus, disabled, submitting, success, and failure states. Prevent duplicate actions. Never remove visible focus to imitate a screenshot. Do not depend on hover for essential information.

Use subtle animation only for actual activity. Queued, paused, failed, and waiting tasks must not look active. Respect reduced motion. Show useful updates or waiting reasons, not perpetual spinners. Connection loss means the display may be stale; it does not prove the task stopped. PR submission needs definite submitted or failed outcomes backed by application state.

## 13. Icons and additional artwork

Use a coherent icon family with readable labels. The reference's small object-like icons are direction, not permission to copy game assets or another product's branding.

Existing addon artwork may be reused when approved, suitable, and available. The source document's named references, including TWICHUI_APPROVED_STYLE_2026-10-31.png and guild-bank treatments, are not claimed to have been inspected in this review. They supplement rather than displace the newer owner-selected web render.

Standalone icons may use transparency where useful. Keep texture images fully filled. Do not embed ordinary interface text in artwork. Verify licensing and actual-size legibility. Avoid decorating every task with illustrations.

## 14. Responsive and accessible presentation

The side-by-side render depicts two separate pages, not a requirement to show both pages together in the running app.

Support desktop, phone, enlarged text, keyboard use, and reduced motion. Reflow sections and task rows without ordinary horizontal scrolling. Keep current work and decisions prominent. Adapt navigation and task creation access appropriately. Code comparisons may use a separate suitable viewing treatment.

Check contrast on actual textured surfaces, focus, labels, touch targets, and associated form instructions/errors. Do not steal focus during background updates or announce every internal event urgently. These are requirements to verify, not a claim of current compliance.

## 15. Empty, waiting, failure, and recovery screens

Design more than populated success screens:
- No tasks: explain how to start.
- Idle versus paused versus blocked.
- Waiting for a model or resource, with a reason.
- Clarification and approval requests.
- Recorded decisions awaiting application.
- Interrupted work, retained evidence, and supported recovery.
- Failed checks without a completed badge.
- Lost connection or stale display.
- Unavailable usage information without invented balances.

Use plain English; put internal failure identifiers in expandable details.

## 16. Delivery boundaries and verification

The initial working product remains first priority. Asset preparation does not promote the broad visual redesign ahead of proving useful execution.

When authorized, own a coherent dashboard outcome: inspect current app and assets, build shared surfaces and real flows, check desktop/phone and interaction states, correct routine defects, and report what was actually installed and demonstrated. Preserve approval, authentication, model-control, and publication safeguards.

Distinguish visual concept, prepared assets, implemented screens, installed screens, browser-tested states, and demonstrated end-to-end work. A screenshot proves none of the later stages by itself.

## 17. Acceptance checklist

- Recognizably related to the selected TwichUI render, not merely brown.
- No heavy decorative perimeter frame or ornate corner fittings.
- Crafted header, tactile materials, raised buttons, and restrained bronze preserved.
- Real text and controls, not a flattened screenshot.
- Textures checked for seams, scale, readability, and web delivery.
- Current work, decisions, and results obvious.
- Real counts, statuses, activity, and result types.
- All intended work types remain discoverable.
- Clear focus, usable controls, phone layout, enlarged text, and reduced motion.
- Smooth readable code/detail surfaces.
- Honest empty, waiting, failed, and recovery states.
- Permission boundaries unchanged.
- Progress claims supported by actual verification.

## 18. Decisions still open

Exact palette values, typography/fonts and licenses, responsive dimensions, asset derivatives, detailed list controls, and notification preferences remain to be verified or decided as needed. A future light theme is not currently required.

The general amount of outer framing is no longer open: use the selected frameless direction. Do not repeatedly reopen the owner's preference for stronger WoW-addon character.

## Revision record

Version 1.1 records the November 5 owner preference for the frameless, strongly WoW-inspired render, inventories uploaded material candidates, replaces conflicting cleaner-web guidance, and separates artwork selection from implementation and asset verification.
