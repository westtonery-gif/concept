# ADR-0076: Render quality — a 4K vertical canvas and CRF 18, so the picture is not shrunk

- **Status:** Accepted
- **Date:** 2026-09-22
- **Deciders:** Maintainer ("почему качество упало?", 2026-09-22); Lead Architect
- **Amends:** ADR-0075 §3 (the default canvas), ADR-0063 (encoder settings)

## Context

ADR-0075's default 1080×1920 canvas fits a 1920×1080 source into 1080×608 — a third of its
pixels — and the renderer used x264's defaults (CRF 23, bicubic scaling). The maintainer saw the
drop. Measured on the first episode (an 8–20 s segment): CRF 18 + lanczos at 1080 wide barely
helps, because the loss is the downscale itself; a **2160×3840** canvas holds the picture at
2160×1215 — no downscale, visually identical to the source — at about 9 s of encoding per 20 s of
video (preset `medium`) and ~12 MB per 20 s. Platforms re-encode every upload, and a
higher-resolution upload is what keeps the most detail through that.

## Decision

- `OMEMO_CLIP_CANVAS` defaults to **`2160x3840`**; 1080×1920 and `source` remain one line away.
- `FfmpegClipRenderer` encodes with **`-crf 18 -preset medium`** and scales with **lanczos**; both
  are constructor parameters (`crf`, `preset`).

## Consequences

- The picture keeps the source's detail; captions, burnt before scaling, are upscaled with it.
- A 2-minute clip is ~75 MB and ~1 minute of encoding; an episode of ~12 clips adds ~10 minutes.
- Instagram downscales anything above 1080 wide on its side; YouTube and TikTok accept 4K.
