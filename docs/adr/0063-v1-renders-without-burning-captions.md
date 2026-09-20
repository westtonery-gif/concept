# ADR-0063: v1 renders clips without burning captions, and keeps them for when it can

- **Status:** Accepted
- **Date:** 2026-09-21
- **Deciders:** Lead Architect / Domain Architect
- **Amends:** `CLIPPING_SPEC.md` §7 ("вжечь субтитры")
- **Serves:** `CLAUDE.md` queue task 21.6 (the ffmpeg-backed `ClipRenderer`)

## Context

The department's render step was specified as "cut the interval, burn in the subtitles, write the
file", and ADR-0062 gave the request timed `Caption`s so that burning them could be correct.

The ffmpeg installed on the maintainer's machine (Homebrew, 9.0.2, arm64) **cannot draw text on a
picture at all**. Checked directly rather than inferred:

- `ffmpeg -h filter=subtitles` → `Unknown filter 'subtitles'`
- `ffmpeg -h filter=drawtext` → `Unknown filter 'drawtext'`
- its `configuration:` line carries no `libass`, no `libfreetype`, no `fontconfig`, and
  `brew deps ffmpeg` lists none of them.

Everything else works: cutting, re-encoding and measuring are all present. Only the overlay is
missing. Getting it would mean building ffmpeg from source through a third-party tap — the core
formula no longer takes options — which is the better part of an hour of compilation.

The maintainer chose to go without it (2026-09-21).

## Decision

### 1. `FfmpegClipRenderer` cuts, encodes and measures; it does not burn captions

It ignores `ClipRenderRequest.captions` and says so in its own docstring, so nobody reading the
adapter concludes the field is unused everywhere.

### 2. The captions are kept, not discarded

They stay on `PlannedClip` and in the clip Artifact's payload. Nothing about the contract changes,
because ADR-0062's contract was right for the wrong-seeming reason: it is exactly what makes this
reversible. The day an ffmpeg with `libass` is installed, burning captions is a change inside one
adapter — no port, no plan, no stored Artifact shape.

This is also why ADR-0062 was worth writing even though its capability is now deferred: had the
request still carried flat text, restoring burn-in later would have meant changing two contracts
and everything that had been stored under them.

### 3. What the department loses, stated plainly

Clips ship without captions. In v1 that costs little, because v1 publishes nothing — the maintainer
posts each approved clip by hand, and TikTok, Reels and Shorts all generate captions themselves.
It is a real loss all the same: a burnt-in caption is the department's own, styled, timed to the
cut, and present wherever the file goes. An auto-caption is the platform's.

### 4. QA is untouched

The QA role never saw the captions — it reads the flat transcript (ADR-0056 §2). Nothing about
this decision changes what a clip is judged on, which is what a viewer would understand from it,
not what is drawn on it.

## Deferred

- **Burning the captions**, once an ffmpeg with `libass` exists. The adapter gains a filter and a
  temporary subtitle file it writes itself; the format stays out of the contract (ADR-0062 §3).
- **Choosing the caption's look** — font, size, position, safe area — which only matters once
  something draws them.

## Consequences

### Positive

- The department runs on real files today instead of waiting on a source build.
- The reversal is confined to one adapter, by construction.

### Negative / Trade-offs

- A clip's captions exist in the trace but not on the screen, which reads as a bug until this ADR
  is found. The adapter's docstring points here.
- If the maintainer later posts at volume, adding burn-in means re-rendering clips already
  approved — the plan is stored, so it is mechanical, but it is not free.

## Alternatives considered

- **Build ffmpeg with libass through a third-party tap.** Available and rejected for now: an hour
  of compilation for a capability the platforms duplicate in v1.
- **Download a static ffmpeg build.** Rejected: a binary from an unvetted source, for the same
  capability.
- **Mux the captions as a soft subtitle track** instead of burning them. Rejected: TikTok, Reels
  and Shorts ignore embedded subtitle tracks, so it would look like a feature and do nothing.
- **Drop `Caption` from the contract until it can be drawn.** Rejected: that is ADR-0062's mistake
  in reverse, and it would make restoring burn-in a contract change again.

## References

- ADR-0062 (timed captions on the plan and the request), ADR-0053 §6 (v1 publishes nothing),
  ADR-0056 §2 (QA reads the flat transcript)
- `CLIPPING_SPEC.md` §7; `CLIPPING_ACCEPTANCE.md` implementation-state table
