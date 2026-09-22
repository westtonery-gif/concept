# ADR-0078: The post's title as a headline in the top bar and branding in the bottom one, added at posting time

- **Status:** Accepted
- **Date:** 2026-09-22
- **Deciders:** Maintainer (asked for the author's own layer in the bars, 2026-09-22); Lead Architect
- **Amends:** ADR-0071 ("the reviewer approves the file that ships") for this one deterministic step

## Context

Platforms reward what the author adds (YouTube's reused-content rule, Instagram's 2024 originality
update). The maintainer's clips have two black bars (ADR-0075/0076). The cheapest real addition is
the author's own framing there: a headline over the picture and the channel's branding under it.
The headline is the post's title — which exists only after QA passes (ADR-0072) and may be edited
by the reviewer, so it is final only at the approval.

## Decision

1. **An additive port method.** `ClipFinisher.finish(FinishRequest(source_path, headline, footer,
   destination)) -> RenderedClip` in `adapters/clip_renderer.py`; `FfmpegClipRenderer` implements it
   with one ASS script on the canvas: the headline centred in the **top** bar, the footer (if any)
   centred in the **bottom** bar, both for the clip's whole length; audio copied, video re-encoded
   at the renderer's CRF. It needs a canvas — a source-frame render has no bars.
2. **At posting time.** When `Posting` carries a finisher, the clip is finished right before
   `submit` from the **approved** post text (`latest_post`), written next to the clip as
   `<clip>-post.mp4`, and that file is what is posted. Finishing is local and repeatable, so it runs
   again harmlessly if a crash repeats the step; the post itself stays at-most-once (ADR-0073 §3).
3. **Text on the picture.** Emoji and other characters outside the Basic Multilingual Plane are
   dropped from the burnt headline (Arial has none — they would render as boxes); the title on the
   platform keeps them. The footer comes from `OMEMO_CLIP_FOOTER`; unset means no footer.

## Consequences

- Every posted clip carries the author's framing; the reviewer still approved both the cut and the
  words that frame it, only not their composited pixels — a deterministic overlay of approved text.
- One more encode per posted clip (~1 minute at 4K).

## Alternatives considered

- **Burn the headline at render time.** The text does not exist yet then, and the reviewer may
  change it.
- **Transformations meant to defeat content matching** (mirroring, overlays of stripes, pitch
  shifts). Declined: they are circumvention, not authorship, and the platforms say they do not
  count as original.
