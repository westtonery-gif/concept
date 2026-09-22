# ADR-0079: The headline moves to the bottom bar; the top bar is kept for banners

- **Status:** Accepted
- **Date:** 2026-09-22
- **Deciders:** Maintainer ("заголовок снизу оставь, сверху будет баннерная реклама", 2026-09-22)
- **Amends:** ADR-0078 §1 (where the headline goes)

## Decision

`FfmpegClipRenderer.finish` draws the headline in the **bottom** bar (centred at two fifths of its
height) and the footer, when set, below it near the bottom edge. The **top bar is left empty** — it
is the banner slot (ADR-0075). Nothing else changes.
