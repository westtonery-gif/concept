# ADR-0083: Generated-video QA blocks visible defects and what a platform would reject — the rest are hints

- **Status:** Accepted
- **Date:** 2026-09-22
- **Deciders:** Maintainer ("критерии одобрены", 2026-09-22, on the proposal below); Lead Architect
- **Resolves:** ADR-0065 "Deferred" (QA criteria for generated video); `CLAUDE.md` queue task 23.3
- **Follows:** ADR-0070's shape (block only what must not ship; everything else is a hint)

## Context

The proposal put to the maintainer on 2026-09-22 and approved as written: block only defects —
artifacts, deformed bodies ("мутанты"), garbage text — and what a platform would not let through;
everything else comes back as hints, as clip QA already does (ADR-0070).

Two facts about the material:

- **The model has to see the video.** `ArtifactEvaluator.evaluate(content: str)` takes text. A
  video's defects are in its pixels.
- **A still frame cannot show motion.** Flicker, morphing and objects popping in are temporal; a set
  of stills shows some of them (an object present in one frame, missing in the next) and misses
  others. The human watches the video anyway (ADR-0018: QA `passed` **and** Approve).

## Decision

### 1. What blocks a video

`video-qa-agent` v1 returns `failed` or `flagged` only for:

1. **Visible defects** — deformed or extra limbs, hands, faces; melted or merged objects; an object
   that appears or vanishes between frames without cause; gibberish or garbled lettering. Clear →
   `failed`; unsure → `flagged`.
2. **What a platform would reject** — nudity or sexual content, graphic violence or gore, hate
   symbols, dangerous acts presented as instructions, a recognisable real person or a protected
   brand/logo. Clear → `failed`; unsure → `flagged`.

Everything else — weak match to the idea, dull composition, the ending not quite reached, style
remarks — comes back as `Подсказка:` flags on a `passed` verdict, which ADR-0034 already allows and
the review page shows (ADR-0070 §2). A vendor moderation refusal (`VideoJobState.REJECTED`) never
reaches QA: nothing was produced, the Run says so.

### 2. The model sees frames the department extracts

A deterministic step after `collect-video` extracts **one frame per second** of the video (8 for an
8-second clip) with `ffmpeg` — already installed and used by clipping — into local files. QA reads
those frames plus the idea and the three prompts (ADR-0082) as text context.

This needs QA to take images. Additively, beside `ContextualArtifactEvaluator` (ADR-0068):

```python
class VisualArtifactEvaluator(ArtifactEvaluator, Protocol):
    def evaluate_frames(
        self, content: str, context: str, frames: Sequence[str]
    ) -> EvaluationResult: ...
```

`frames` are local paths; the implementation calls `ImageAwareLLMClient.complete_with_images`
(ADR-0080 §2) and decodes through the unchanged `decode_verdict` (ADR-0034). The Schema is the
existing **`qa-verdict@v1`** — one verdict contract, as `clip_qa_agent@v1` reused it.

### 3. Sound is not judged in v1

Veo generates audio (ADR-0081 §1). QA cannot hear it; the human does. A soundtrack problem is a
reason to reject on the review page, not a QA criterion.

## Deferred

- **Judging motion directly** — a video-native model or more frames around suspected moments; revisit
  if humans keep rejecting videos QA passed for temporal defects.
- **Frame count as configuration** — fixed at 1/s until a reason appears.
- **Audio checks** — needs a speech/sound model; not asked for.

## Consequences

- The human queue gets only videos without obvious defects or policy problems, with hints attached.
- QA costs roughly eight image inputs per video — cents, measured by the Analytics Records.
- A temporal defect between sampled frames can pass QA; the human Approve is the backstop.

## Alternatives considered

- **Strict QA that also judges idea-fit and quality.** Rejected by the maintainer's choice, on the
  evidence of ADR-0070: a strict reading floods the queue with judgement calls a human makes faster.
- **Send the video file itself to a model.** Not available through this repo's Claude client; frames
  work today.

## References

- ADR-0018, ADR-0034, ADR-0068, ADR-0070, ADR-0080 §2, ADR-0081, ADR-0082
- `CLAUDE.md` queue task 23.3
