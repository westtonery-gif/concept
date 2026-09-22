# ADR-0074: Opening titles and end credits are found by audio fingerprint, and no clip covers them

- **Status:** Accepted
- **Date:** 2026-09-22
- **Deciders:** Maintainer (found the problem, chose fingerprinting with a manual fallback,
  installed `chromaprint`, supplied a second episode, 2026-09-22); Lead Architect
- **Amends:** ADR-0064 (what the local footage index reports), ADR-0068 §2 (what the recorded
  index holds), ADR-0069 §2 (one more boundary rule in `plan_clips`)
- **Serves:** `CLAUDE.md` queue task 28

## Context

The first episode's clips included the opening titles and the end credits (task 24: clip 15 was
only credits; clip 3 opened on the theme). Nothing in the pipeline knew they existed.

The theme and the credits music **sound the same in every episode**, while the cold open before
them varies in length — so position alone cannot find them, and sound can. With `fpcalc`
(Chromaprint 1.6.1, `brew install chromaprint`) the raw fingerprints of the two available
episodes, aligned by the offsets their exact matches vote for, give (per-item Hamming distance
≤ 10 of 32 bits, runs with ≤ 2 s gaps):

| shared audio | episode 1 | episode 2 |
|---|---|---|
| opening theme | 127.7–159.3 s | 0.0–30.8 s |
| end theme | 1295.4–1313.9 s | 1247.5–1280.1 s |
| short recurring cues | 251–260 s (9 s), 827–840 s (13 s), 1211–1219 s (8 s) | mid-episode |

Two lessons are in that table. Short shared cues exist in the middle of an episode and are story,
not titles. And episode 2 has **38 s after its end theme** — cutting "from the credits to the end"
would drop a post-credits scene.

## Decision

### 1. The footage index reports the zones no clip may cover

`IndexedFootage` gains `skips: tuple[SkipZone, ...] = ()` — `SkipZone(start_ms, end_ms)`, ordered,
non-overlapping, inside the episode. Additive: every existing producer and value stays valid.

### 2. `LocalFootageIndex` finds them against sibling episodes

For the episode being indexed, `fpcalc -raw -json -length 0` fingerprints it and up to **four**
other video files in the **same folder** (sorted by name; the folder is the operator's
`OMEMO_EPISODE_ROOT`, where a series' episodes live together). A shared run is a skip zone when it
is **at least 15 s long** and lies **within the first or the last quarter** of the episode — which
keeps the mid-episode cues above out. Zones from different siblings are merged. A file of another
series simply shares nothing. With no sibling there are no zones — the manual fallback (a skip
field on the board card) is deferred until an episode needs it.

`fpcalc` missing is a `FootageIndexError`, like a missing `whisper-cli` (ADR-0064): the index is
misconfigured, not empty. The matching itself is a pure function in `infrastructure/
recurring_audio.py`, tested on synthetic fingerprints.

### 3. The recorded index carries them

`footage-index@v1`'s payload gains `skips` (ADR-0068 §2); a payload without it reads as no zones,
so an index recorded before this ADR still reads back.

### 4. `plan_clips` plans around them

The episode is split into the stretches **between** skip zones; each stretch is planned on its own
by the mode's rule (`CHUNK`: pieces nudged to pauses; `SCENE`: cuts in pauses, merged under the
maximum, never mid-line), so every zone edge is a clip boundary and no clip overlaps a zone.
A stretch shorter than **5 s** is dropped — a sliver between two zones is not a clip. With no zones
the plan is exactly what it was.

## Consequences

### Positive

- Titles and credits never reach a clip, a QA call or a reviewer; a post-credits scene still does.
- No configuration: the second episode in the folder is the evidence.

### Negative / Trade-offs

- The first episode of a new series, alone in its folder, gets no zones until a sibling arrives.
- A pilot's one-off ending song (episode 1's 1264–1296 s) is shared with nothing and is not caught;
  QA's "no content" criterion (ADR-0070) is still the net for that.
- Indexing runs `fpcalc` on up to five files — seconds each.

## Alternatives considered

- **Cut a fixed offset from the start and end.** The cold open's length varies and a post-credits
  scene follows the credits — both measured above.
- **Find titles in the transcript.** An instrumental theme has no words, and whisper hallucinates
  on music (task 24.2).
- **Skip ranges typed on the board card only.** Manual work per episode; kept as the deferred
  fallback.

## References

- ADR-0064, ADR-0068 §2, ADR-0069, ADR-0070; Chromaprint `fpcalc`; the Jellyfin Intro Skipper
  approach; `CLAUDE.md` queue tasks 24, 28
