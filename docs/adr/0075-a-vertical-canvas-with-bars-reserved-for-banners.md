# ADR-0075: Clips are rendered on a vertical canvas, with the bars above and below kept for banners

- **Status:** Accepted
- **Date:** 2026-09-22
- **Deciders:** Maintainer ("формат тот же, но добавить чёрные блоки сверху и снизу для показа
  баннерной рекламы", 2026-09-22); Lead Architect
- **Amends:** ADR-0058 / ADR-0056 ("v1 does not reframe; the platform letterboxes it")
- **Serves:** `CLAUDE.md` queue task 33

## Context

A 16:9 clip uploaded to a 9:16 feed is letterboxed **by the platform** (ADR-0056 Context). The
maintainer wants that letterbox to be **ours**: the same 16:9 picture, not cropped, with black bars
above and below that the department owns — the place where banner advertising will go. What the
banners are, who they are for, and how they are labelled are separate decisions (advertiser,
licence terms, Russian ad-marking law); this ADR only makes the room for them.

## Decision

1. `FfmpegClipRenderer` takes an optional **canvas** `(width, height)`. With one, the cut picture
   (captions already burnt, ADR-0071) is scaled to fit the canvas **without cropping** and padded
   with black, centred: `scale=W:H:force_original_aspect_ratio=decrease,pad=W:H:(ow-iw)/2:(oh-ih)/2`.
   For a 1920×1080 source on 1080×1920 that is a 1080×608 picture with **656 px of black above and
   below**. Without a canvas the render is what it was.
2. Captions stay **on the picture**, not in the bars, so both bars are entirely free for banners.
3. `OMEMO_CLIP_CANVAS` configures it: `WIDTHxHEIGHT`, default **`1080x1920`**; `source` keeps the
   source frame. A product choice with a safe default, like the clip lengths (ADR-0040's exception
   recorded in `CMP-02`).
4. The format check is unchanged: `RenderedClip` measures the real file, and aspect ratio was never
   a violation (RND-04).

## Consequences

- The platform no longer letterboxes: the file already is 9:16, and the bars are ours to fill.
- Captions become smaller relative to the screen (the picture is a third of it); the caption size is
  a renderer parameter if they read too small.
- Banners themselves — an overlay into the bars, per campaign, with whatever disclosure the
  platforms and the law require — are the next decision, not this one.

## Alternatives considered

- **Crop to 9:16.** Loses two thirds of a 16:9 frame and needs a subject tracker; the maintainer
  asked for the same picture.
- **Blurred copy of the picture as the background.** A common look, but the bars are meant for
  banners, and black is what was asked for.

## References

- ADR-0056, ADR-0058, ADR-0063, ADR-0071; `CLAUDE.md` queue task 33
