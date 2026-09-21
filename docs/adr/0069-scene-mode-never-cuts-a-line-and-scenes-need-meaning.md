# ADR-0069: `SCENE` never cuts inside a spoken line — and real scenes need meaning, not pixels

- **Status:** Accepted
- **Date:** 2026-09-21
- **Deciders:** Lead Architect / Domain Architect (direction chosen by the maintainer, 2026-09-21)
- **Amends:** ADR-0058 §1 (what a `SCENE` boundary is); settles ADR-0064's deferred threshold
  question with a measurement rather than a number
- **Serves:** `CLAUDE.md` queue task 24.3

## Context

The first real episode (task 24; 22 minutes, 1080p, a cartoon) was cut in `SCENE` mode, and clip
QA flagged clips that start mid-sentence and clips that jump between storylines. ADR-0064 deferred
the `scdet` threshold "to the first real episode". Measured on that episode, with the transcript
from the anti-loop whisper settings (task 24.2):

| `scdet` threshold | detected cuts | note |
|---|---|---|
| 10 (default) | 337 | median gap 2.3 s — every shot change |
| 20 | 117 | still shots |
| 30 | 16 | arbitrary survivors, not scenes |

**245 of the 337 cuts fall inside a spoken line.** Merging those "scenes" up to the two-minute
maximum (ADR-0058 §1) turned `SCENE` into two-minute pieces whose edges were shot changes — often
mid-sentence, which is what QA saw.

Two deterministic repairs were measured and neither finds scenes:

- **A cut in a pause of at least N ms.** whisper.cpp's segments abut (one ends where the next
  starts), so pauses of 1–2 s at a cut are rare in dialogue: 57 / 52 / 48 boundaries at N = 1000 /
  1500 / 2000 ms, most of them bunched in one action sequence, leaving 447 s and 302 s stretches
  with none — which the chunk rule then cuts anyway.
- **A cut between two lines.** In dialogue a shot-reverse-shot changes the shot at nearly every
  change of speaker, so this finds the rhythm of the conversation, not its end.

Neither pixels nor speech timing say where a scene — let alone a storyline — begins. That takes
reading what is said.

## Decision

### 1. The threshold stays configuration, and stays 10

No threshold turns shot detection into scene detection (the table above), so none is chosen as a
fix. `OMEMO_SCENE_THRESHOLD` remains the knob it was; its default is not changed on this evidence.

### 2. In `SCENE`, no boundary lands inside a spoken line

- A detected cut **strictly inside** a speech span is not a scene boundary. A line's own start and
  end count as pauses.
- A scene still longer than the maximum is split by `CHUNK`'s rule (nudge to a pause within the
  tolerance); if the nudge cannot free the cut, `SCENE` moves it **back to the start of the line it
  falls in**, which keeps the piece under the chunk length. Only a single line longer than a whole
  chunk can defeat this, and then the cut stays where it was.
- CLP-06 is unchanged in meaning: no cut **detected** means no `SCENE` clips. Cuts detected but all
  mid-line mean one long scene, which is split — not an empty plan.
- `CHUNK` is unchanged (CLP-03 still holds: its cut may stay mid-line when no pause is near).

On the same episode this plans 15 clips with **no boundary inside a line**.

### 3. The merge rule is kept — and it is not the fix for interleaved storylines

Merging adjacent scenes up to the maximum still joins two threads the episode intercuts. That is
not a defect of the merge rule so much as of having no notion of a thread: with boundaries that
are shot changes, not merging would give two-second clips. Separating storylines needs a planner
that reads the transcript (the capability ADR-0057 described and ADR-0058 deferred), and it is
queued as its own task rather than approximated here.

## Consequences

### Positive

- The most visible defect of the first run — a clip opening or closing mid-sentence — cannot come
  from `SCENE`'s boundaries any more; CLP-13/14 pin it.
- The threshold question ADR-0064 left open is answered with numbers, and the answer is "a
  threshold is the wrong tool", which saves the next session from tuning it.

### Negative / Trade-offs

- `SCENE` is, on dialogue-heavy material, still closer to "chunks that respect lines and shots"
  than to scenes. Clips may still join two storylines; QA will keep flagging that, correctly, until
  the planner exists.
- The rule trusts whisper's span edges. A hallucinated span (task 24.2) could shift a boundary;
  the anti-loop settings make that rarer, not impossible.

## Alternatives considered

- **Tune the threshold.** Measured above; no value separates scenes from shots.
- **Require a pause of N ms at a cut.** Measured above; with abutting segments it finds almost no
  boundaries in dialogue. It was the maintainer's first choice and was withdrawn on this evidence.
- **Drop the merge rule.** With shot-level boundaries it would emit clips of a few seconds.
- **A planner agent now.** The right direction for storylines, and a larger decision (it restores
  an agent ADR-0058 removed); queued separately instead of folded into a fix.

## References

- ADR-0057 (storyline extraction), ADR-0058 §1 and §4 (scene cutting, no planner in v1),
  ADR-0064 (the local footage index, the deferred threshold)
- `CLAUDE.md` queue task 24; `CLIPPING_SPEC.md` §6; `CLIPPING_ACCEPTANCE.md` CLP-13, CLP-14
