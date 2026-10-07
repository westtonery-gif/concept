# ADR-0088: A `FrameGenerator` port — pictures from words and references — and Seedream behind it

- **Status:** Accepted
- **Date:** 2026-10-07
- **Deciders:** Maintainer ("го начинай кадры", 2026-10-07); Lead Architect
- **Builds on:** ADR-0084 (Seedream + Seedance as the generation vendor), ADR-0066/0067 (the first
  image port and the Gemini adapter), ADR-0087 (the story script that has to be drawn)
- **Serves:** `CLAUDE.md` queue task 23 (23.11b — ADR-0084's Seedream image side — and 23.14)

## Context

ADR-0084 moved the department to BytePlus and left Seedream **unbuilt**: `SeedreamImageGenerator`
and `SeedanceVideoGenerator` were queue items, and the only code that had ever called Seedream was
a handful of one-off scripts that were lost with a temporary folder. Meanwhile the department's
only image port, `ImageGenerator`, is shaped for ADR-0066's pipeline: **one reference photo** in,
one ending frame out.

A voiced story needs a different shape. Its characters come from **words alone** (a character
sheet), and every shot after that is words **plus the sheets of whoever is in the shot**, so that a
character stays recognisably the same across thirty pictures. That is zero or more references and
an explicit size, which the existing port does not express and should not be bent to.

What was already established live against Seedream (2026-09-26/27, recorded in ADR-0084 and the
session notes): `size` as an explicit `WIDTHxHEIGHT` returns exactly that size; `image` takes a
list of base64 data URIs and **honours** them; two references at once are accepted; `watermark:
false` removes the "AI generated" mark; the answer is a short-lived URL; $0.03 a picture. Today's
character sheets confirmed the model draws original 3D-cartoon characters from a paragraph of text.

## Decision

### 1. A second image port, `FrameGenerator`

`adapters/frame_generator.py`: `generate(FrameRequest(prompt, destination, width, height,
references=())) -> GeneratedImage`. Paths in, a path out, the adapter's own measurements back
(`GeneratedImage` and `ImageGeneratorError` are reused, not duplicated). Synchronous, paid,
repeatable, a Workflow step and never a Tool (ADR-0054 §2). `ImageGenerator` is **unchanged**: it
stays the port of ADR-0066's photo-to-ending-frame pipeline, and nothing here removes it.

### 2. `SeedreamFrameGenerator`, stdlib `urllib`

`infrastructure/seedream_frame_generator.py`, no new dependency. One `POST /api/v3/images/
generations` with `Authorization: Bearer`, then one download of the answer's URL, which is written
atomically and never returned. References go inline as base64 data URIs with their own measured
media type (so nothing is hosted); more than ten, a missing file or a non-image are refused **before**
any request. The answer's size is measured from its bytes and an answer more than 3 % off the
ratio asked for is a failure (ADR-0067 §3's rule). A network failure — including a body cut short —
is asked again twice; a refusal from the vendor is final and carries its own words, never the key.

### 3. Configuration

`OMEMO_BYTEPLUS_API_KEY` (already set for the video probes) and `OMEMO_SEEDREAM_IMAGE_MODEL`, both
required, no defaults. The model id must start with `seedream-`; which one is the maintainer's
choice, and `seedream-4-0-250828` is the one activated in the Ark console and used for the sheets.
`composition.build_frame_generator(environ)` builds it and names what is missing.

### 4. What the character sheets proved, and what they did not

Four characters written by `story_writer@v1` as text only — a barnacled buoy-shaped captain with a
number on his hat, a gull-headed man in a yellow raincoat, a gull on a loaf of bread, a woman
shaped like a lighthouse bell — came out as four clean, distinct, original characters in one
consistent film style, for $0.12. **What this does not prove:** that a character keeps the same
face and costume across thirty shots when the sheets are passed as references. That is the next
test and it is what decides whether a story can be drawn at all.

## Consequences

### Positive

- The department can draw. One module and one port; the video side (Seedance) is the other half.
- References are inline, so the Seedream side has no hosting and no URL ever crosses the core.
- The same adapter serves character sheets and shots; there is no second code path to keep honest.

### Negative / Trade-offs

- **A second image port beside the first.** Two ports with `generate` is a smell; they are kept
  apart because their requests differ in what is *required* (one photo vs. none or many). If
  ADR-0066's pipeline is dropped for good, `ImageGenerator` can go and this one stays.
- **Everything rides on one vendor and one model id** (ADR-0084's concentration risk, unchanged).
- Pictures cost money every time: a 30-shot story is about $0.90 of frames before any video, and a
  re-draw of one shot is $0.03.
- A model refusing a prompt (moderation) comes back as "no image" with the vendor's words; nothing
  retries it.

## Deferred

1. **The storyboard step**: from a story script to a list of shots — which lines each covers, who is
   in it, the setting, the camera — and the prompt for each. It is a model's job (a new role) and
   its own ADR.
2. **Consistency across shots** — measured, not assumed (§4).
3. **`SeedanceVideoGenerator`** and the assembly of clips, voice and captions into the video.
4. A **Fake/in-memory** `FrameGenerator` for department tests, when the department has an
   application module that needs one.

## References

- ADR-0084 (the vendor), ADR-0067 (the aspect rule), ADR-0066 (the other image port), ADR-0054
- `generation-tests/frames/…/cast.jpeg` — the four sheets
