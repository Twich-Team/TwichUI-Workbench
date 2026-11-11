# Owner-supplied visual references

The November9 styling assignment supplied four JPEG attachments. Their original
bytes are preserved in `supplied-originals`; SHA-256 hashes and dimensions are in
`manifest.json`. In attachment order they depict the page leather, header leather,
panel leather, and selected frameless Dashboard/Backlog reference.

The named PNGs are lossless decoded copies of those supplied JPEGs. They are **not**
claimed to be recovered copies of the separately named original PNG uploads.
The owner subsequently supplied the complete version1.1 visual-direction text,
saved verbatim at the repository root as TWICHUI_WORKBENCH_WEB_VISUAL_DIRECTION.md.
The supplemental addon art-direction document was not found; the supplied guide
explicitly makes it optional. No different visual concept is substituted.

Runtime WebP derivatives live in `frontend/public/art/dashboard`. The512px source
tiles are mirrored into1024px patterns with matching edges, avoiding abrupt repeat
seams. CSS supplies functional edges/lighting and quiet reading surfaces. Original
reference artwork is not used as a screenshot background for interactive controls.

Provenance: supplied by Rich for this private Workbench assignment. No claim is
made about third-party authorship or a transferable license. No external font or
CDN is used. Existing branding and icon license records remain applicable.

Phase B adds `header_grain.webp`: the same supplied header source resized to768px,
mirrored to1536px, WebP quality88. It retains more grain at the768px CSS scale.
Source files are unchanged. Shared CSS supplies bevels and restrained light;
quieter existing panel/page tiles remain. Desktop/phone inspection and fallback
checks belong to the Phase B acceptance record.
