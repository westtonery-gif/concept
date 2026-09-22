# ADR-0077: A skip zone swallows the spoken lines that straddle it

- **Status:** Accepted
- **Date:** 2026-09-22
- **Deciders:** Maintainer ("убери из начала видео первые секунды о озвучке", 2026-09-22); Lead
  Architect
- **Amends:** ADR-0074 §4

## Context

The dub studio's voice-over ("Перевёл Женя Спицын, озвучил Индук") is spoken over the last seconds
of the theme and runs just past it. The fingerprint match ends where the shared music ends, so the
line straddles the zone's edge — episode 2: line 30.0–32.3 s, zone ends 30.9 s; episode 1:
157.3–160.4 s against 159.4 s — and the first clip after the titles opened on it (episode 2's clip 1,
already posted). The same happens at the end credits, with whisper's silence hallucinations
("Субтитры создавал…", "Редактор субтитров…") straddling the end zone.

## Decision

In `plan_clips`, before planning, each skip zone is **widened to cover every speech span that
overlaps it**; widened zones that now touch are merged. The clip after the titles then starts after
the straddling line, and a line cut in half by a zone edge — never wanted — cannot happen.

Pure and deterministic, on the recorded index (ADR-0068), so it applies to any re-plan; nothing in
the footage index changes.

## Consequences

- No voice-over credit or straddling hallucination at a clip's start or end.
- A real line of dialogue that happens to overlap a title edge is dropped with the titles — one
  line at the seam, instead of half of one.
