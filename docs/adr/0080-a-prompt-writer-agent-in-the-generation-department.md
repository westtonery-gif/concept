# ADR-0080: A prompt-writing Agent in the generation department — the human keeps only the final Approve

- **Status:** Accepted
- **Date:** 2026-09-22
- **Deciders:** Maintainer ("да добавляем нового агента", 2026-09-22); Lead Architect
- **Amends:** ADR-0065 §5 (v1 has no prompt-crafting agent) — reversed for v1; ADR-0066 §1 ("both
  prompts are human input in v1") accordingly. ADR-0065 and ADR-0066 stay Accepted otherwise.
- **Serves:** `CLAUDE.md` queue task 23 (new subtask 23.10, ahead of 23.9)

## Context

ADR-0065 §5 kept the prompt-crafting step out of v1 so the vendor-calling skeleton would be proven
first, "the same order clipping's own history took." On 2026-09-22 the maintainer reversed that:
the department is meant to be run by agents, with a human only reviewing the result — "мой
человеческий фактор все заруинит, просто сделаем human review." Writing two generation prompts by
hand for every video is exactly the human step they want gone, and ADR-0065's own "Negative" named
the cost this ADR removes: throughput bounded by how fast a human writes prompts.

Two facts shape the design:

- **The agent must see the photo.** An ending-frame prompt ("the same bunker, finished, same camera
  angle") cannot be written well from a sentence alone. Claude accepts image input, but this repo's
  `LLMClient.complete(*, system, user, fields, toolbox)` (ADR-0014/0028) carries text only.
- **Nothing about the skeleton changes.** The ports (ADR-0066), the vendor adapters (ADR-0067), the
  gate (ADR-0018) and the Run shape still hold; the agent adds one step in front of them.

## Decision

### 1. A producer role, `generation_prompt_writer@v1`

An ordinary producer on the Rin/Leo/`clip_post_writer` template (ADR-0072 §1): Agent, Prompt
`generation-prompt-writer` in the bundled store (ADR-0030), Schema `generation-prompts@v1` with
`required_fields = ("image_prompt", "video_prompt")`, no Skills, no Tools. Built by
`build_executor_map`, bound through `client_for_role` with its own required pricing, `max_tokens` and
thinking (ADR-0029/0052), every call an Analytics Record. Provider-agnostic: the model is the
binding's, never the code's.

- **Input:** the reference photo (as an image, §2) and the card's **idea** — one short line a human
  or an upstream agent writes, e.g. «таймлапс постройки бункера». The idea is required; a card with
  no idea is not ready (the board ADR, 23.4, names the property).
- **Output:** `image_prompt` — what the *ending* frame looks like, anchored to the photo (same
  framing, same place, the finished state); `video_prompt` — how the change unfolds between the two
  frames (ADR-0066 §1: the two steps are asked different things).

### 2. Image input is an additive client capability, not a changed port

`LLMClient` is not modified (`PROJECT.md` §4.11). A second Protocol is added beside it, the way
ADR-0068 added `ContextualArtifactEvaluator` beside `ArtifactEvaluator`:

```python
class ImageAwareLLMClient(LLMClient, Protocol):
    def complete_with_images(
        self,
        *,
        system: str,
        user: str,
        images: Sequence[str],
        fields: Sequence[str],
        toolbox: Toolbox,
    ) -> LLMCompletion: ...
```

`images` are **local paths** (ADR-0066 §4's rule: no URL crosses into the core). The Anthropic
implementation reads each file, checks its signature (PNG/JPEG/WebP, the set `GeminiImageGenerator`
already accepts), and sends it as a base64 image block before the text. A file that is missing,
unreadable, not an accepted image or over the provider's size limit is an `LLMError` **before** any
call. `FakeLLMClient` implements it too. The Composition Root refuses at build time a binding whose
client lacks the capability for a role that needs it — named, never a silent text-only fallback.

### 3. It runs as the first committed step of a generation Run

The department's application module (23.7) opens a Task at step **`write-prompts`** before the
ending-frame step, commits it before the call and finishes it with `start_task` / `finish_task`
(ADR-0026 §2). The validated Output is what the next two steps read. So a resume **reuses** the
committed prompts rather than paying for them again, and a `RUNNING` `write-prompts` Task is simply
re-run on its stored input — it is a model call, not a submission, so at-least-once (ADR-0026 §4)
costs cents and corrupts nothing. An `INVALID` Output fails the Run with ADR-0033's stable reason;
no image or video is paid for.

### 4. The human sees the prompts, and does not write them

The review page for the video (23.6) shows both prompts next to the result, so a reviewer who rejects
a video can say *why* in terms of what was asked. A reviewer's rejection reason is **not** fed back
to the prompt writer in v1 — a rejected video is not shipped, as a rejected clip is not (ADR-0059 §6);
an agent-driven retry loop is Deferred. Manual prompts on the card are **not** kept as an override:
two ways to supply the same input would be two paths to test and keep honest, and the maintainer
asked for exactly one.

## Deferred

- **Rework from the reviewer's reason** — feeding «Доработать» + the reason back into
  `write-prompts` and regenerating, ADR-0032's shape. Wanted once first generations show how often a
  better prompt, rather than another roll, would fix a video.
- **An upstream idea generator** — an agent that fills the idea itself (ranking topics, a content
  plan). This ADR only removes the prompt-writing step; where ideas come from stays a human or
  board concern until asked for.
- **Letting QA see the prompts** as its context (ADR-0068's port) — belongs to the QA criteria ADR
  (23.3).

## Consequences

### Positive

- A generation needs one human action: Approve or Reject the finished video (`PROJECT.md` §12).
- The agent sits on infrastructure already proven live: the Prompt store, per-role bindings,
  metrics, `SchemaBinding` validation.
- Image input lands as a reusable capability — the future video QA and a frame-checking step can use
  the same `complete_with_images`.

### Negative / Trade-offs

- The skeleton ADR-0065 §5 wanted proven first is now proven *together* with a model step, so a bad
  first video has one more suspect. Mitigated by §4: the prompts are on the review page.
- One more paid model call per video (small next to the video itself; measured, not assumed).
- A second client Protocol to keep in step with the first.

## Alternatives considered

- **Keep ADR-0065 §5 (human prompts).** Rejected by the maintainer: it is the human bottleneck they
  want removed.
- **Text-only agent (idea in, prompts out, no photo).** No port change, but the ending frame would be
  described blind; the whole point of the two-call pipeline (ADR-0066) is that the end state matches
  *this* photo.
- **Widen `LLMClient.complete` with an optional `images` argument.** Changes a signature every
  implementer and test double shares; the additive Protocol costs the same and changes nothing
  existing.
- **Manual prompts as an optional override.** Rejected in §4.

## References

- `PROJECT.md` §4 п.11, §12; ADR-0014, ADR-0026 §2/§4, ADR-0028, ADR-0029, ADR-0030, ADR-0033,
  ADR-0052, ADR-0059 §6, ADR-0065 §5, ADR-0066 §1/§4, ADR-0068 (the additive-Protocol precedent),
  ADR-0072 §1 (the producer-role template)
- `CLAUDE.md` queue task 23
