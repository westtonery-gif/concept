# ADR-0096: The canvas can be filled with a blurred copy of the picture instead of black bars

- **Status:** Accepted
- **Date:** 2026-10-10
- **Deciders:** Maintainer ("нужно чтобы видео выходили 9х16 — без чёрных квадратов в рилсе",
  asked which fill: blurred background chosen over a centre crop); Lead Architect
- **Amends:** ADR-0075 (the bars were kept for banners; that is now an option, not the only mode)

## Decision

`FfmpegClipRenderer(fill=)` takes `black` (default, ADR-0075 unchanged) or `blur`. With `blur`
the uncropped picture sits centred on a darkened, blurred copy of itself that fills the whole
canvas — a true 9:16 frame with no black bars and nothing cut off. The copy is blurred at 1/8
size and scaled up, so the cost is small. Captions are burnt before the split, so they stay on
the sharp picture. Configured by `OMEMO_CLIP_FILL` (`black` | `blur`).

## Consequences

- The default stays `black`, so no stored setup changes meaning; this machine's `.env` sets `blur`.
- A centre crop was rejected: it loses up to a third of the width of a cartoon frame.
- The headline/footer finish (ADR-0078/0079) still goes into the lower area; it now sits over the
  blurred fill rather than black, so check legibility on the first real clip.
- Banners (ADR-0075's reason for the bars) would need `black`; choose per channel.
- Tests: `FCR-11` (pixels: no black rows with and without music).
