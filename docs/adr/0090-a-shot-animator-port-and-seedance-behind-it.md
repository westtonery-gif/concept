# ADR-0090: A `ShotAnimator` port — one picture brought to life — and Seedance behind it

- **Status:** Accepted
- **Date:** 2026-10-07
- **Deciders:** Maintainer ("го", 2026-10-07, after the quoted cost); Lead Architect
- **Builds on:** ADR-0084 (Seedance as the video vendor), ADR-0066 (the submit-once video job life
  cycle), ADR-0088/0089 (the pictures and the shot list that are to be animated)
- **Serves:** `CLAUDE.md` queue task 23 (23.2c — ADR-0084's Seedance side — and 23.16)

## Context

ADR-0084 chose Seedance and left it **unbuilt**; the one place Seedance had been called was a set of
one-off scripts lost with a temporary folder. `VideoGenerator` (ADR-0066) animates **from a first
frame to a last one**. A story shot is one picture and a sentence about what moves in it, so the
request is different: a frame, a prompt, a duration. The life cycle is not: Seedance is a task API
that answers a task id, is polled, and offers **no idempotency key** — exactly the case ADR-0066 §3
wrote the submit-once rule for.

What the live pilot and earlier probes established: the picture goes inline as a base64 data URI with
`role: first_frame`; settings are flags at the end of the text (`--resolution`, `--duration`,
`--ratio`, `--watermark false`, `--camerafixed`); durations are whole seconds, 2–12; the model makes
**no audio**; a 4-second 720p clip costs about $0.22 and came back 704×1248.

## Decision

### 1. A third video-ish port, `ShotAnimator`, reusing the job types

`adapters/shot_animator.py`: `submit(AnimationRequest(frame, prompt, duration_s, ratio="9:16")) ->
VideoJob`, `collect(VideoJob, destination) -> VideoJobResult`. `VideoJob`, `VideoJobState`,
`VideoJobResult`, `GeneratedVideo` and `VideoGeneratorError` are **reused**, so one poller serves
both ports. `VideoGenerator` is unchanged. Contract modules may not import `re`, so the ratio is
checked by hand.

### 2. `SeedanceShotAnimator`, stdlib `urllib`

`infrastructure/seedance_shot_animator.py`. `submit` is **one** `POST /api/v3/contents/generations/
tasks` and is **never retried**: a network failure there says "may or may not have been accepted
and is NOT submitted again", because a second POST is a second paid clip. A refusal carries the
vendor's words and never the key. `collect` is one status `GET`; `queued`/`running` →
`PENDING`; `succeeded` → download, measure the MP4 (size and length read from the file), write
atomically → `COMPLETED`; `failed`/`cancelled`/`expired` → `FAILED`, or `REJECTED` when the error
code names sensitive content; anything else is an error. Network failures on the read side are asked
again twice. The job id is percent-encoded into the path.

### 3. Configuration

`OMEMO_BYTEPLUS_API_KEY`, `OMEMO_SEEDANCE_VIDEO_MODEL` (must start `seedance-`) and
`OMEMO_SEEDANCE_RESOLUTION` (`480p`/`720p`/`1080p`) — all required, no defaults. The model in use is
`seedance-1-0-pro-250528`, the one activated and confirmed live (ADR-0084 §3). `composition.
build_shot_animator(environ)`.

### 4. A shot's length comes from the voice, and a job is remembered before it is awaited

A clip's window is taken from the **real spoken lines** — a shot starts where its first line starts
and ends where the next shot's first line starts — so the clips tile the voice track. Seedance takes
whole seconds, so a clip is asked for the next whole second and **trimmed** to its window; that
rounding is the price of the whole-second grid (thirty shots, 100 s of voice, 116 s asked for). The
entrypoint writes each job id to disk **before** it waits, so a crash or a re-run collects and never
submits again.

## Consequences

### Positive

- The department can now turn a storyboard into moving shots; every piece up to the silent film
  exists.
- The one irreversible, paid, non-idempotent call is the one that is never repeated by code.
- Whole-story cost is known before any money is spent (`demo_animate.py plan`).

### Negative / Trade-offs

- **Whole-second rounding costs about 16 %** ($6.26 against $5.40 for exactly 100 s).
- **No last frame**: a clip starts at the picture and Seedance decides where it ends, so a shot's
  ending is not controlled, and things can drift (the pilot's phone ended on the floor; a
  telescope passed through a head).
- No sound from the model: voice and music are separate tracks to assemble.
- Output is 704×1248, not 720×1280; the assembly scales it.

## Deferred

1. The other 29 clips, then the **join**, the voice track and the **word-by-word captions**.
2. **A QA reader of the clips** (limbs, faces, objects passing through each other) — until it exists
   the maintainer's eye is the gate.
3. A last frame per shot (the `VideoGenerator` shape) if endings drift too often.
4. A re-roll policy for a bad clip (a bad $0.2 shot is cheaper to redo than to live with).

## References

- ADR-0066 §3 (submit-once), ADR-0084 (vendor and the live confirmation), ADR-0088/0089
- `generation-tests/frames/…/clip-06.mp4` — the pilot clip
