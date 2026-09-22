# ADR-0081: Veo 3.1 Lite behind `VideoGenerator` — the cheap default, Kling kept for later

- **Status:** Accepted
- **Date:** 2026-09-22
- **Deciders:** Maintainer ("добавляем дешевый адаптер — переходить расти будем по мере того как
  будет расти канал", 2026-09-22); Lead Architect
- **Amends:** ADR-0066 §4 ("on 2026-09-21 that is Kling 3.0 Standard, Pro or 4K") — the ending-frame
  requirement stands, the vendor list widens. ADR-0067 is untouched: `HiggsfieldVideoGenerator`
  stays as written.
- **Serves:** `CLAUDE.md` queue task 23 (new subtask 23.2b)

## Context

The department was drawn around Kling 3.0 Pro on Higgsfield (ADR-0066/0067). The maintainer is
funding the channel themselves and wants the cheapest model that is good enough now, moving up as
the channel grows. A price survey on 2026-09-22 put Kling 3.0 at roughly $0.09–0.18 per second
depending on tier and audio, against Seedance 2.0 Fast (~$0.02/s on ByteDance's own API) and Veo 3.1
Lite ($0.05/s at 720p).

The port has one hard requirement a cheap model must meet: **an ending frame** (ADR-0066 §1 —
`VideoGenerationRequest.last_frame` is required). Checked against vendor documentation on
2026-09-22, not assumed:

| Candidate | Ending frame | 9:16 | Lengths | Price | Verified from |
|---|---|---|---|---|---|
| **Veo 3.1 Lite** (`veo-3.1-lite-generate-preview`) | **yes** — `lastFrame`, "frame interpolation (first/last)" | yes | 4, 6, 8 s | **$0.05/s 720p, $0.08/s 1080p**, audio included | `ai.google.dev/gemini-api/docs/veo`, `…/docs/pricing` |
| Veo 3.1 Fast | yes | yes | 4, 6, 8 s | $0.10/s 720p, $0.12/s 1080p | same |
| Seedance 2.0 Fast (BytePlus ModelArk) | **not confirmed** — the tutorial page did not render its content; third-party pages do not say | — | — | ~$0.02–0.09/s, varies by reseller | not verifiable today |
| Kling 3.0 Std/Pro on Higgsfield | yes (`last_image_url`) | yes | 3–15 s | ~$0.09–0.17/s | ADR-0066 |

Veo 3.1 Lite is the cheapest candidate whose ending-frame support is confirmed from its vendor's
own page. It also runs on the **Gemini API key the department already requires** for the ending
frame (ADR-0067 §4.1), so it adds no account, no second billing relationship and no presigned-upload
machinery.

## Decision

### 1. A second `VideoGenerator` implementation: `VeoVideoGenerator`

In `infrastructure/`, stdlib `urllib`, no new dependency (ADR-0067's reasoning applies unchanged).
The port (ADR-0066 §3) is **not changed**: `submit` starts a Veo long-running operation
(`models/<model>:predictLongRunning` with `prompt`, the first frame as `image`, the ending frame as
`lastFrame`, both inline) and returns the operation name as the `job_id`; `collect` is one status
read of that operation and, when `done`, one download into `destination` with the file's own
measurements (`media_measure`). A safety-filtered result is `REJECTED`, an operation error `FAILED`
(ADR-0066 §3's distinction). The exact wire shape is read from Google's REST reference when the
adapter is written (23.2b), as ADR-0067 did for Higgsfield — this ADR fixes behaviour, not bytes.

- **Submission is non-repeatable, as for Higgsfield.** No idempotency key is documented for
  `predictLongRunning`, so ADR-0066 §3's rule holds: commit before `submit`, commit the `job_id`
  right after, never resubmit a `RUNNING` submit Task.
- **Collect promptly.** Google keeps a generated video for **two days** (vs. Higgsfield's seven).
  ADR-0066's polling within the invocation plus the next sweep is well inside that; a Run left
  uncollected past it fails with a stable reason rather than pretending the job is still pending.
- **The adapter refuses before any call** what the model cannot do: a `duration_s` outside the
  configured model's set (Lite: 4, 6, 8), and a first frame whose aspect is not within 3% of 9:16 or
  16:9 (the same tolerance `GeminiImageGenerator` applies); the request asks for the matching ratio.
- **Audio.** Veo 3.1 generates sound in every tier and the price includes it. v1 keeps what arrives;
  whether a generated soundtrack may ship is the QA criteria's question (23.3), as ADR-0066 deferred.
- **Errors** follow ADR-0066 §4: a `VideoGeneratorError` whose message carries no key and no URL.

### 2. Configuration chooses the vendor, explicitly and fail-closed

- `OMEMO_GEMINI_API_KEY` (already required for images) is reused.
- `OMEMO_VEO_VIDEO_MODEL` — required when Veo is chosen, from an allowlist of models that accept
  `lastFrame` (today `veo-3.1-lite-generate-preview`, `veo-3.1-fast-generate-preview`,
  `veo-3.1-generate-preview`). Moving up a tier is a one-variable change.
- `OMEMO_VEO_RESOLUTION` — required, `720p` or `1080p` (Lite has no 4K).
- `composition.build_video_generator(environ)` chooses by **presence**, the `build_review_desk`
  rule (ADR-0060): any `OMEMO_VEO_*` selects Veo, any `OMEMO_HIGGSFIELD_*` selects Higgsfield, a
  partly configured vendor fails naming its own missing variables, and **both present is an error**
  — two video vendors configured at once is a mistake to surface, not a preference to guess.

### 3. The growth path is configuration, not code

Start: **Veo 3.1 Lite, 720p**. When the channel justifies it: `OMEMO_VEO_RESOLUTION=1080p`
($0.08/s), then `OMEMO_VEO_VIDEO_MODEL=veo-3.1-fast-generate-preview`, then Kling through the
existing `HiggsfieldVideoGenerator` by swapping the variable set. Each step is a `.env` edit.

## Cost of one video, for scale

8-second vertical video, v1 pipeline: prompt writer (ADR-0080, cents) + ending frame
(`gemini-3.1-flash-image` 1K, $0.067) + Veo 3.1 Lite 720p (8 × $0.05 = $0.40) + QA ≈ **$0.50**.
The same through Kling 3.0 Pro on Higgsfield was roughly $0.95–1.45. Prices as published on
2026-09-22; the live test (`LIV`) records what is actually charged.

## Deferred

- **Seedance 2.0 Fast** — potentially cheaper still, but its ending-frame support could not be
  confirmed from ByteDance's own documentation today. A third implementation is additive once it is.
- **Cropping a reference photo to 9:16 or 16:9** as a deterministic step before generation, instead
  of refusing it (§1). Wanted if the photos in practice are 3:4 or 4:3.
- **Veo's `personGeneration` and negative prompts** — adapter configuration if the first
  generations need them.

## Consequences

### Positive

- Roughly half the cost per video of the Kling path, on the key the department already needs.
- Proves the ports' promise in ADR-0066 "Positive": a second video vendor is one module in
  `infrastructure/`, and no application or domain code learns its name.

### Negative / Trade-offs

- Two `VideoGenerator` adapters to keep tested, one of them (Higgsfield) not used until the channel
  grows.
- Lite caps a clip at 8 s (Kling: 15 s) and at 1080p; longer videos mean stitching, which v1 does not
  do.
- A "preview" model id may change or be retired by Google; the allowlist makes that a loud
  configuration error, not a silent downgrade.

## Alternatives considered

- **Stay on Kling via Higgsfield.** Proven shape, higher price; kept as the growth step (§3).
- **Seedance 2.0 Fast now.** Cheapest on paper; rejected for today only, because the one capability
  the port requires could not be verified.
- **Drop the ending frame to reach cheaper image-to-video models.** Reverses the maintainer's
  two-call choice (ADR-0066 §1) to save cents; not on the table.

## References

- ADR-0060 (selection by presence), ADR-0066 §1/§3/§4, ADR-0067 §4, ADR-0080
- Google: `ai.google.dev/gemini-api/docs/veo`, `ai.google.dev/gemini-api/docs/pricing` (read
  2026-09-22)
- `CLAUDE.md` queue task 23
