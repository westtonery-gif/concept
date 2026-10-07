# ADR-0091: Cut as fast as the references, put faces in the frame, and let short shots be stills

- **Status:** Accepted
- **Date:** 2026-10-07
- **Deciders:** Maintainer ("видео скучное — цель таких мультиков абсурдность, бурная сильная
  реакция; наши герои не обладают мимикой; сравни с рефами", 2026-10-07); Lead Architect
- **Amends:** ADR-0089 §1–§2 (a shot may share a line; the world is a look, not a place),
  ADR-0090 §4 (a short window is not asked of Seedance)
- **Builds on:** ADR-0087 (the story writer), ADR-0089 (the storyboard), ADR-0090 (animation)

## Context

The first whole video (the lighthouse story, 100 s, ≈ $9) was judged boring. The maintainer asked for
a comparison with the references; it was **measured, not guessed**, against the three references
that came with view figures (9.7 M, 5 M, 1 M):

| | ours | 9.7 M | 5 M | 1 M |
|---|---|---|---|---|
| cuts a minute | 17 | 84 | 80 | 47 |
| average shot | 3.3 s | 0.7 s | 0.7 s | 1.3 s |
| motion (mean frame difference) | 3.6 | 11.6 | 7.9 | 7.2 |
| spoken words a second | 2.1 | 3.7 | 1.4 | 3.5 |

(The cut count is the number of large jumps between frames at 12 fps and may run high; the order of
magnitude is not in doubt.) Looking at the frames: the references are half **faces filling the
frame** with huge wet eyes and open mouths, and they change place every few seconds; ours were
small characters on one deck at one sunset, with a beard over one mouth. The stories differ too:
theirs have **someone to pity** and a villain who profits; ours had two people being petty.

## Decision

1. **A shot may share a line.** The storyboard's coverage rule is "every line is covered, in order,
   and a shot may begin on the line the last one ended on" (at most three shots on a line). That is
   how a reaction — the listener's face — gets its own picture. Shared lines split their time equally;
   `shot_windows` does the arithmetic on the real spoken lengths.
2. **Pace is a number in the task, and a floor in the decoder.** Told in a Prompt, "cut more" was
   ignored (twenty-six shots for twenty-six lines, both times). `storyboard_input(script,
   min_shots=…)` now states the figure, `decode_storyboard(…, shots_per_line=1.5)` refuses fewer, and
   the refusal says how to fix it.
3. **The storyboard's `world` is a look, not a place.** Each picture names its own place, so a
   story can move through three or four settings; at least half the shots are close-ups with a named
   strong emotion; the hero's mark gets its own insert shot.
4. **Faces are part of the character.** The story writer's Prompt (v3) requires big expressive eyes and
   a visible mouth, nothing covering the face, a large head, and a mark drawn large; stories need a
   sympathetic victim who suffers visibly, a profiteer, **escalating absurdity**, short reaction
   lines, and three or four places.
5. **A window under two seconds is not asked of Seedance.** Seedance clips last 2–12 s and cost
   $0.054 a second; the quick reaction shots the genre cuts every second are their picture with a
   fast punch-in made by ffmpeg, at no cost. Longer windows are animated as before.
6. **A cast of up to six**, since a story with a crowd of small roles needs them; at most three are
   in any one shot.

## Consequences

### Positive

- The tools now say what the references show, and the cost falls: only the long shots are paid for in
  seconds, the many short ones in pictures ($0.03 each).
- Every figure in this ADR can be re-measured on the next video and compared.

### Negative / Trade-offs

- **More pictures**: about 1.5 a line, so ~40 pictures for a 26-line story ($1.2).
- A punch-in is motion without life: the face does not change, only the frame moves. Whether that
  holds the eye is untested.
- Lips still do not follow speech (nothing here syncs them).
- The measurement is of three videos chosen for success.

## Deferred

1. Whether the new rules make a video the maintainer does not call boring — the next render decides.
2. **Sound accents** on reactions (gasp, thud, whoosh), which the references use and ours did not.
3. A face-fills-the-frame check for the pictures (a QA role).

## References

- `generation-refs/NOTES.md`; ADR-0087/0089/0090; the measurement above
