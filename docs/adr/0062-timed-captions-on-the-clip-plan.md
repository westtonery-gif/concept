# ADR-0062: Timed captions on the clip plan and the render request

- **Status:** Accepted
- **Date:** 2026-09-21
- **Deciders:** Lead Architect / Domain Architect
- **Amends:** `CLIPPING_SPEC.md` §6 (`PlannedClip`) and §7 (`ClipRenderRequest`)
- **Serves:** `CLAUDE.md` queue task 21.6 (the ffmpeg-backed `ClipRenderer`)

## Context

`ClipRenderRequest` carries `subtitles: str` and `PlannedClip` carries `transcript: str` — both flat
text. Burning flat text can only put one static block on screen for the clip's whole duration,
which is worse than burning nothing: it covers the picture and never matches what is being said.

The timings exist. `IndexedFootage.speech` is a tuple of `SpeechSpan(start_ms, end_ms, text)`, and
`plan_clips` already selects the spans overlapping each clip — it simply joins their text and drops
the times.

Why the gap survived the spec and the tests: the only reader of that text so far is the **QA role**,
which wants exactly flat text because it judges meaning (ADR-0056 §2). The contract closed around
that reader. The other reader — a renderer that puts words on a picture — does not exist yet, so
nothing exercised the shape until it was time to build it. This is the ordinary cost of specifying
a port before its first implementation, and it is cheapest to fix now, while neither contract has a
second implementation.

## Decision

### 1. A `Caption` is a line with times **relative to the clip**

```
Caption(start_ms, end_ms, text)
```

`0` is the clip's first frame, not the episode's. The renderer is handed a clip and a list of lines
to draw on it, and never learns where that clip sat in the episode — one less place for an
off-by-an-episode error, and one less reason for the adapter to know about the index's shape.

### 2. `PlannedClip` gains `captions`; `transcript` stays

`plan_clips` already walks the overlapping spans, so it produces both from one pass: `captions` for
the renderer, `transcript` for QA. Keeping `transcript` is not duplication for its own sake — it has
a distinct reader that wants a different thing, and deriving it from `captions` at every call site
would push the same join into two places.

A line that straddles a clip boundary is **clipped in time, not dropped**: its words are partly
spoken on screen, so the caption is shown for the part that is, with its text intact. Dropping it
would silently lose dialogue at every cut, which is precisely where a viewer is most likely to
notice.

### 3. `ClipRenderRequest.subtitles: str` becomes `captions: tuple[Caption, ...]`

Empty is legitimate and means burn nothing — a clip with no speech is a normal clip, not an error.

How those captions become pixels is the adapter's business: an ffmpeg implementation will write its
own temporary subtitle file and burn it. That file format is **not** in the contract, because a
different renderer would use a different one.

## Consequences

### Positive

- Burnt-in captions can actually be correct, which is the point of burning them.
- Both contracts change while each still has exactly one implementation and one caller — the
  cheapest moment there will ever be.
- The renderer stays ignorant of the episode's timeline.

### Negative / Trade-offs

- `PlannedClip` now carries the same words twice, in two shapes. §2 is the reason; a reader who
  meets it without the reason will think it is redundant.
- Clipping a straddling line means a caption can show text whose beginning was cut off. Showing
  partial dialogue is the better failure, but it is still a failure mode a viewer can see.

## Alternatives considered

- **Keep flat text and burn a static overlay.** Rejected: it is the thing that looks like captions
  and is not, and it would have shipped before anyone watched a clip.
- **Hand the renderer the whole `IndexedFootage` plus the interval** and let it select and re-base.
  Rejected: it moves planning into an adapter and makes every renderer depend on the index's shape.
- **Put an SRT path in the request.** Rejected: it writes a file format into a contract, and forces
  a temporary file on implementations that do not need one.
- **Derive `transcript` from `captions` wherever QA needs it.** Rejected: the same join in two
  places, and QA's input shape would start depending on the renderer's.

## References

- `CLIPPING_SPEC.md` §6, §7; `CLIPPING_ACCEPTANCE.md` §4 (CLP)
- ADR-0053 §5, ADR-0056 §2 (QA reads flat text), ADR-0058 §2 (a clip is one contiguous interval)
