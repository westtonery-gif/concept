# ADR-0097: A moment-picker Agent chooses the strongest moments of an episode

- **Status:** Proposed (the maintainer asked for this ADR on 2026-10-11 after naming the goal —
  "choosing the right moments is the key to a successful cut: laughter, tears, surprise, cult
  lines"; the open questions below are theirs to answer before code)
- **Date:** 2026-10-11
- **Deciders:** Maintainer (goal); Lead Architect (shape)
- **Amends:** ADR-0058 §4 ("v1 has no planner agent") — a planner returns, for one **new** mode
  only; the two existing modes and `plan_clips` are untouched.
- **Realises:** ADR-0069 §3 / `CLAUDE.md` queue task 25 (a planner that reads the transcript)
- **Serves:** `CLAUDE.md` queue task 25

## Context

Both modes today cut by **geometry**: `CHUNK` by length, `SCENE` by shot changes merged up to a
maximum (ADR-0058). Neither asks whether a stretch is *worth watching*. ADR-0069 measured that
neither pixels nor speech timing can say where a scene or a thread begins, and the 2026-09-22
re-run showed the cost: of 15 clips, QA flagged 14, almost all for boundaries; the maintainer
judged all 15 acceptable but a few were obviously stronger than others, and nothing ranks them.
What makes a short-form clip succeed is the moment itself — a punchline with its set-up, a
reversal, a cult line, an emotional peak that works from the first second without context.
Choosing that takes reading what is said, which is language work, not arithmetic.

## Decision

### 1. A new mode `MOMENTS`, additive

`ClipMode` gains `MOMENTS`; the board's `Mode` select gains `moments`. `CHUNK` and `SCENE` keep
their meaning, tests and `plan_clips`. Nothing silently falls back from one mode to another.

### 2. The role: `clip_moment_picker@v1`

An Agent on `clip_post_writer`'s template: Prompt `clip-moment-picker` v1 in the bundled store,
Schema `clip-moments@v1` with one text field `moments`. **No Skills, no Tools** — everything it
needs is in its input (ADR-0054 still has no consumer). One call per episode.

**Input** (canonical JSON): the series file name (it often says which series), the episode
duration, **the numbered transcript** — one line per speech span, `n | start_s | end_s | text` —
and the configured limits (minimum and maximum clip length, how many moments).

**Output**, one moment per line: `first_line-last_line | kind | why`. A moment is named by the
**transcript lines it spans, not by seconds** — so a boundary is a line edge **by construction**
and "no cut mid-line" (ADR-0069 §2) cannot be violated by a model that miscopies a time.
`kind` is a closed vocabulary (`laugh`, `surprise`, `tension`, `emotion`, `quote`); `why` is one
sentence. The model lists moments **strongest first**.

**What it is asked to prefer** (the Prompt's text, reviewed like code): a complete set-up and
payoff; a line or exchange that works from its first second with no prior context (a hook);
one thread per clip; a quotable or famous line; ending on the payoff, not before it. It may use
what it knows about the series to **rank**, but every moment must be backed by lines of this
transcript — knowledge reorders, it never adds a moment, and a claim that a scene is "popular" is
never put in `why` unless the transcript shows it.

### 3. A pure decoder judges the answer, and only it

`application/clip_moments.py` `decode_moments(text, transcript, limits) -> tuple[Moment, ...]`
(the shape of `decode_verdict` and `decode_storyboard`): every fault is **reported together**
and the answer is refused, never repaired by guessing. A moment is refused when its line range is
not inside the transcript, `first > last`, it overlaps another, its duration is outside
`[min, max]`, it touches a skip zone (ADR-0074), its `kind` is not in the vocabulary, or there are
more than the allowed count. One repair round is allowed (the model is shown the faults), as in
ADR-0087; a second refusal fails the step. A moment's start and end are the start of its first
line and the end of its last, so every accepted interval is already legal.

### 4. The plan is a recorded Task Output, not a pure function

`plan_clips` was a pure function of the footage, so a crashed invocation re-planned and got the
same clips (CLP-09). A model call is not pure. So for `MOMENTS` the validated moments are
**recorded as the Output of a `pick-moments` Task** — exactly how `index-footage` is recorded
(ADR-0068 §2) — and every later invocation reads them back instead of asking again. A new pure
`plan_from_moments(footage, moments)` turns them into `PlannedClip`s in **episode order** (the
index, the captions and the transcript are built exactly as `plan_clips` does). A Task found
`RUNNING` on resume is finished on its stored input; the call may repeat (at-least-once,
ADR-0026 §4), which costs cents and corrupts nothing.

### 5. Failure is loud

A failed or twice-refused picker call fails the **Run** with a stable reason
`MOMENT_PLAN_FAILED`; nothing is cut. It does **not** fall back to `SCENE` (a silent second
decision that is not ours, CLP-06's reason): the operator re-submits the card in the mode they
want. An empty answer is an error here, unlike an empty `SCENE` plan, because "no moment in a
22-minute episode" is not credible and hides a broken Prompt.

### 6. Clip QA and the gates do not change

Every moment becomes a clip with its own render, QA verdict and Human Review exactly as today.
The picker chooses what to *try*; `clip_qa_agent` and the human still decide what ships
(ADR-0018). `kind` and `why` live in the Task's Output — the maintainer's reader, for judging the
picker — and are **not** passed to QA (it should judge the cut, not the picker's pitch) nor to the
review page in v1.

### 7. Configuration, with documented defaults (CMP-02's reasoning)

`OMEMO_CLIP_MOMENT_MIN_MS` (default `20000`), `OMEMO_CLIP_MOMENT_COUNT` (default `12`);
the maximum is the existing `OMEMO_CLIP_MAX_MS`. The picker's provider/model/price/tokens/thinking
come from the usual per-role binding `CLIP_MOMENT_PICKER_V1`.

## Consequences

### Positive

- Clips are chosen for what happens in them, and ranked; the 14-of-15 boundary flags of the
  re-run stop being a function of geometry.
- Boundaries are legal by construction; the decoder is deterministic and testable without a model.
- Cost is one call per episode. The 22-minute episode's transcript is ~15k tokens (the QA re-run's
  232k input tokens were that transcript sent 15 times), so the picker is **roughly $0.05–0.15
  per episode** at the QA role's prices — to be measured, not assumed.
- Additive: no core change, no change to `Run`, `ContentDirector`, the QA role or the gates.

### Negative / Trade-offs

- The plan is no longer reproducible from the footage alone; it is reproducible from the **stored
  Run**. CLP-09's guarantee holds for `CHUNK`/`SCENE` only.
- Boundaries are line edges, not shot cuts; a clip may open mid-shot. ADR-0069 §2 already accepts
  that as legal, and a cut inside a shot is invisible, a cut inside a sentence is not.
- The model sees text, not pixels or audio: a visual gag with no dialogue, or a laugh in the
  music, is invisible to it. Whisper's line granularity bounds how fine a moment can be.
- "Popular scenes" come from the model's general knowledge of the series, which may be wrong or
  absent; it is ranking help, not evidence, and §2 forbids presenting it as evidence.

## Deferred (decided later, with numbers from real episodes)

- **Audio energy** (loudness peaks, laughter-like bursts) as an extra hint: an additive field on
  `IndexedFootage` and a port change; justified only if transcript-only picks prove to miss
  non-verbal peaks.
- **Real popularity signals** (the platform's "most replayed" curve, comment timestamps): no
  official API serves them for arbitrary videos; scraping breaks the platforms' terms. Not built.
- **Our own channel's results** (views, retention through upload-post) as a feedback loop that
  re-weights the Prompt: needs a dozen published clips first.
- **Posting strongest-first:** clips are numbered in episode order and `max_new_per_invocation`
  posts in that order; posting by rank is a separate, small change.
- **Cutting inside a long line** (a whisper segment of 25 s is one unit here).

## Open questions for the maintainer

1. The length window for a moment: `20–90 s` (short-form's sweet spot) or keep up to the 2-minute
   maximum?
2. How many moments per episode: 12 as proposed, or fewer and stronger?
3. Should `MOMENTS` become the default mode on new cards once it has run on one episode?
4. Is a first trial on episode 1 or episode 2 (both have QA verdicts to compare against)?

## Implementation order (each spec-before-code)

`CLIPPING_SPEC.md` §6/§10 and `CLIPPING_ACCEPTANCE.md` (`MPK` family: decoder faults, role
consistency, recorded-once, resume without a second call, `MOMENT_PLAN_FAILED`) → `ClipMode`,
board mapping → `decode_moments` + `plan_from_moments` (pure) → role, Prompt, Schema → the
`pick-moments` step in `ClipProduction._produce` → composition + `.env.example` → one live
episode.
