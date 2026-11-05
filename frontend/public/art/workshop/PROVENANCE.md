# Owner-supplied workshop artwork

Source: `Fantasy RPG Workshop Icon Sheet.png`, attached by the owner and approved
for this bounded Milestone 2 acceptance iteration on 2026-11-03. Preserved unchanged
as `source/approved-sheet.png`. No artist identity, license or rights beyond this
supplied provenance are asserted. This is not an official TwichUI logo.

Reproduce with the existing host Python/Pillow:
`python3 scripts/extract_workshop_art.py`.
The source is RGBA with an existing transparent background, not opaque white.
Extraction preserves its alpha, ivory parchment and internal highlights; no color
keying, redraw or regeneration. Nine equal-grid crops are alpha-trimmed, fitted
to 240px and centered on transparent 256px canvases. Inspect at 52–64px on walnut.
Only overview/backlog/history heading accents and the decisions summary are used.
The other five extracted assets do not imply implemented destinations.
