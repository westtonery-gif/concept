# ADR-0082: The starting frame is generated too — an idea is the only input

- **Status:** Accepted
- **Date:** 2026-09-22
- **Deciders:** Maintainer ("фото же должны генерироваться промптом, по этому фото должен
  сгенерироваться промпт для анимации", 2026-09-22); Lead Architect
- **Amends:** ADR-0065 Context/§ answer 2 (a local folder holding a reference photo), ADR-0066 §1
  (the reference photo is the first frame), ADR-0080 §1 (the photo the prompt writer reads) and
  ADR-0081 "Deferred" (cropping a reference photo — no longer needed). All stay Accepted otherwise.
- **Serves:** `CLAUDE.md` queue task 23 (new subtask 23.11)

## Context

ADR-0065 through ADR-0081 assumed a human supplies a **reference photo** and the pipeline turns it
into an ending frame and a video. The maintainer corrected this on 2026-09-22: nobody supplies a
photo. The starting picture is generated from a prompt too, and the animation prompt is written by
looking at *that* generated picture. Together with ADR-0080 this makes the card's **idea** the one
input a human (or, later, an upstream agent) provides.

It also removes ADR-0081's aspect problem at the source: a generated frame is asked for in 9:16
directly, so no photo of an arbitrary shape ever reaches Veo.

## Decision

### 1. The pipeline, one Run per idea

| Step | What happens | Port / role |
|---|---|---|
| `write-scene` | idea → `start_prompt`: the **starting** state, with the camera fixed so the rest can keep it | `generation_scene_writer@v1` (text only) |
| `start-frame` | `start_prompt` → the starting frame, 9:16 | `TextToImageGenerator` (§2) |
| `write-prompts` | the starting frame (as an image) + idea → `image_prompt` (the ending) + `video_prompt` | `generation_prompt_writer@v1` (ADR-0080, unchanged) |
| `ending-frame` | starting frame + `image_prompt` → ending frame | `ImageGenerator` (ADR-0066, unchanged) |
| `submit-video` / `collect-video` | first → last frame + `video_prompt` → video | `VideoGenerator` (ADR-0066/0081) |
| QA → review | ADR-0083 → the human's Approve | |

Every step is a committed Task (ADR-0026 §2); a resume reuses what is committed. The two image
steps are repeatable (they overwrite their destination, ADR-0066 §2); only `submit-video` is not.

### 2. Text-to-image is an additive port, not a changed request

`ImageGenerationRequest.reference` stays required, so `ImageGenerator` keeps its contract. A second
Protocol is added in `adapters/image_generator.py`:

```python
@dataclass(frozen=True, slots=True)
class TextImageRequest:
    prompt: str  # non-blank
    aspect_ratio: str  # "9:16" or "16:9"
    destination: str  # local path


class TextToImageGenerator(Protocol):
    def generate_from_text(self, request: TextImageRequest, /) -> GeneratedImage: ...
```

`GeminiImageGenerator` implements both — the same `interactions` call without an input image, with
the requested ratio; a result off that ratio by more than 3% is refused, as today.

### 3. Two writer roles, not one

`generation_scene_writer@v1` — Prompt `generation-scene-writer`, Schema `generation-scene@v1` =
`("start_prompt",)`, text-only, no Skills, no Tools — writes the start. ADR-0080's
`generation_prompt_writer@v1` then **looks at the generated frame** before describing the end and the
motion, which is what the maintainer asked for: the animation prompt is written from the picture,
not from the idea alone. One role writing all three prompts blind was the cheaper alternative
(below).

### 4. Aspect ratio is configuration with a documented default

`OMEMO_GENERATION_ASPECT` = `9:16` (default) or `16:9`, the same kind of product choice as
`OMEMO_CLIP_CANVAS` (a length or a shape is safe to start somewhere; a property name is not — the
split task 21.6 recorded). The ending frame inherits it from the starting frame; Veo is asked for it.

## Cost of one video

Two writer calls (cents) + two images (`gemini-3.1-flash-image` 1K, 2 × $0.067) + Veo 3.1 Lite 8 s
720p ($0.40) + QA (ADR-0083) ≈ **$0.55–0.60**.

## Deferred

- **Showing the starting frame to a human** before the video is paid for — ADR-0066's deferred second
  gate, unchanged. The maintainer asked for one human action.
- **An automatic retry of a weak starting frame** (judge it, regenerate before paying for video).
  Wanted if QA keeps failing videos whose problem is visible in frame one.

## Consequences

- The card needs only an idea. No photo folder, no aspect problem, nothing for the human to prepare.
- One more paid image and one more model call per video than ADR-0081 counted.
- `ImageGenerator`'s "reference photo" wording now means "the generated starting frame"; the port is
  unchanged, its docstring is updated with the code.

## Alternatives considered

- **One writer for all three prompts, text only.** Cheaper, needs no image input — but the animation
  prompt would be written without seeing the picture it animates, which the maintainer explicitly
  did not want.
- **Make `reference` optional on `ImageGenerationRequest`.** Changes a validated contract two adapters
  and their tests rely on; the second Protocol costs the same and changes nothing.

## References

- ADR-0026 §2, ADR-0065, ADR-0066 §1/§2, ADR-0067 §4.1, ADR-0080, ADR-0081, ADR-0083
- `CLAUDE.md` queue task 23
