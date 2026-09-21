# ADR-0067: The generation vendor adapters — Gemini and Higgsfield over stdlib `urllib`

- **Status:** Accepted
- **Date:** 2026-09-21
- **Deciders:** Lead Architect / Domain Architect
- **Realises:** ADR-0066 (the two ports) in `infrastructure/`; ADR-0065 "Deferred" — auth and env vars
- **Serves:** `CLAUDE.md` queue task 23.2

## Context

ADR-0066 fixed what `ImageGenerator` and `VideoGenerator` promise and left two things to this
subtask: which client each adapter speaks, and how it is configured. The repo has made the client
choice three times already — `notion-client` rejected for stdlib `urllib` (ADR-0040), `cryptography`
accepted as the one dependency stdlib genuinely cannot replace (ADR-0043), and two local binaries
instead of a vendor (ADR-0064). The same test applies: does the SDK do something we cannot do
ourselves in a few lines, and what does it drag in?

Checked on 2026-09-21 (PyPI metadata):

- **`higgsfield-client` 0.2.0** depends on `httpx`. What it adds over the REST API is `subscribe`
  (submit + poll in one blocking call — exactly the shape ADR-0066 §3 rejected), upload helpers, and
  retries. The API it wraps is four routes: `POST /files/generate-upload-url`, a presigned `PUT`,
  `POST /<model>`, `GET /requests/{id}/status`.
- **`google-genai` 2.24.0** depends on `anyio`, `google-auth[requests]`, `httpx`, `pydantic`,
  `requests`, `tenacity`, `websockets`, `distro`, `sniffio` and `typing-extensions`. The call we need
  is one `POST /v1beta/interactions` with an API-key header.

The adapters also owe the ports **their own measurements** of the files they write (ADR-0066 §2/§3,
the `RenderedClip` rule of ADR-0056 §1). The clip renderer reads back with `ffprobe`; the generation
department need not depend on ffmpeg being installed, and the formats involved are few: PNG, JPEG
and WebP for images, MP4 for Kling's output.

## Decision

### 1. Both adapters speak REST through stdlib `urllib`; no new dependency

`infrastructure/gemini_image_generator.py` and `infrastructure/higgsfield_video_generator.py` use
`urllib.request`, `json` and `base64` — the Notion adapters' pattern, including the constructor's
`api_url` argument that lets the tests point them at a local server. Neither SDK is added:
Higgsfield's would bring `httpx` to wrap four routes and its headline helper is the shape we
refused; Google's would bring ten packages for one `POST`. **`pyproject.toml` is unchanged.** If a
vendor's REST surface ever grows past what a small module can hold honestly, that is the moment to
revisit, by ADR.

Gemini is called through the **Interactions API** (`POST /v1beta/interactions`), which is what
Google's image-generation page documents as of its 2026-09-17 revision: the photo inline as
`{"type": "image", "mime_type", "data"}` beside the prompt, `response_format` of type `image` with
`aspect_ratio` and `image_size`, and **`store: false`** — the reference photo is the operator's
material and there is no reason to leave it on Google's side. The image comes back inline in
`steps[].content[]` of the `model_output` step.

### 2. Configuration — required variables, no defaults, fail closed

| Variable | Meaning |
|---|---|
| `OMEMO_GEMINI_API_KEY` | the Gemini API key (never in `repr`, a message or a log) |
| `OMEMO_GEMINI_IMAGE_MODEL` | the image model id, e.g. `gemini-3.1-flash-image` |
| `OMEMO_GEMINI_IMAGE_SIZE` | `512`, `1K`, `2K` or `4K` |
| `OMEMO_HIGGSFIELD_API_KEY_ID` / `OMEMO_HIGGSFIELD_API_KEY_SECRET` | the key pair, sent as `Authorization: Key <id>:<secret>` |
| `OMEMO_HIGGSFIELD_VIDEO_MODEL` | the endpoint id, e.g. `kling-video/v3.0/pro/image-to-video` |
| `OMEMO_HIGGSFIELD_SOUND` | `on` or `off` |

ADR-0040's rule, unchanged: every one is required, a missing one names itself and never its value,
and no model or size is defaulted — a model is exactly the kind of thing `PROJECT.md` says lives in
configuration. The `OMEMO_` prefix keeps them apart from the vendor SDKs' own `HF_KEY`/`GEMINI_API_KEY`
conventions, so a stray shell variable for some other tool never silently configures the factory.

**The video model is checked against an allowlist of endpoints whose documented schema takes
`last_image_url`**: `kling-video/v3.0/{std,pro,4k}/image-to-video`. A model outside it is refused at
configuration, because the two-call pipeline (ADR-0066 §1) cannot work without an ending frame and
the vendor would otherwise accept the request and ignore the field. Widening the list is a one-line
change made after reading the new model's page, not a guess.

### 3. The frame keeps the photo's shape

Gemini takes an aspect ratio from a closed list; a phone photo is rarely exactly on it. The adapter
measures the photo, asks for the **nearest listed ratio** (compared on a log scale, so 2:1 and 1:2
are equally far from 1:1), and checks the answer's own measured ratio against that request within
3%. An answer off it is `ImageGeneratorError` and nothing is written: an ending frame of another
shape would make Kling animate between two differently framed pictures, and that is better stopped
here than discovered by a human looking at a paid-for video. The extreme ratios Gemini also lists
(1:8, 8:1, 1:4, 4:1) are left out of matching — no reference photo for this department is shaped
like a banner.

A reference photo larger than 15 MB is refused before any call: inline payloads are bounded and the
Files API is not used in v1.

### 4. Measurements are read from the produced bytes, in stdlib

`infrastructure/media_measure.py` reads width and height from PNG (`IHDR`), JPEG (the first `SOFn`
frame header) and WebP (`VP8X`/`VP8L`/`VP8 `), and duration plus the **video** track's size from an
MP4's `moov` (`mvhd`; the `trak` whose `hdlr` is `vide` — the audio track Kling adds by default is
skipped). Anything it cannot read is `MediaMeasureError`, which the adapters turn into their port's
error: an unmeasurable file is a failed step, never a guessed number. The same module sniffs the
image type for Higgsfield's upload `content_type` and Gemini's `mime_type`, so neither is taken from
a file extension.

### 5. What each adapter does — and refuses to do — at the wire

- **No retries inside either adapter.** A Higgsfield submission must never be repeated (ADR-0066 §3);
  a status check that fails is simply asked again by the Workflow's next `collect`; a Gemini call
  that fails is the Workflow's step failing. Retrying below the port would hide exactly the
  ambiguity ADR-0066 wanted visible.
- **Credentials go only to their own API.** The presigned `PUT` and the video download carry no
  Higgsfield credentials (the vendor's own instruction for storage); the tests check both.
- **No URL leaves the adapter.** Upload URLs, public input URLs and the output URL are used and
  dropped; they appear in no return value, message or log. A job id must look like a Higgsfield
  request id (a UUID) before it is put in a path, so an id that came back from storage cannot steer
  a request elsewhere; a URL the vendor hands back must be `http(s)`.
- **Files are written atomically** — to a temporary file beside the destination, then renamed — so a
  crash never leaves a half-written frame or video where the next step would read it. Nothing is
  written unless the bytes measured as what was asked for.
- **Errors say what went wrong, not what was sent.** Higgsfield's documented status codes are named
  (`401` invalid credentials, `403` insufficient credits, `423` model blocked, …); Google's error
  message is quoted, truncated. A model that answered with text instead of an image is quoted too,
  truncated, because "it said: I can't make that image" is what an operator needs to read.

### 6. Tested against local servers; live calls are opt-in and cost money

The adapters' real `urllib` code is exercised over real sockets against local servers that play
Gemini, and Higgsfield with its storage and CDN (`GIG`, `HVG`), on real minimal PNG/JPEG/WebP/MP4
bytes; `MED-05` also reads an MP4 real ffmpeg encoded, when ffmpeg is present. That proves our side
of the wire. **It does not prove the vendors' side** — that the documented shapes are what the
services actually send. `tests/test_live_generation.py` (`LIV`) calls both for real on a real photo
and runs only with `OMEMO_LIVE_GENERATION=1` and the keys set, because it spends the operator's
money; otherwise it skips and says why. Until it has run once, the department is **not** proven
against the real vendors, and `GENERATION_ACCEPTANCE.md`'s state table says so.

## Deferred

- **Retries with backoff for status checks inside `collect`.** Not needed while the Workflow polls;
  revisit if a transient `5xx` turns out to be common.
- **The Files API** for photos over 15 MB, and Gemini's `delivery: uri`.
- **Cost estimation** before submitting (Higgsfield's `/estimate/...`) — ADR-0066 Deferred.
- **Other video models.** Seedance and Wan also take first and last frames on Higgsfield; their
  parameter names differ, so each would be read and added to the allowlist deliberately.

## Consequences

### Positive

- No new dependency; the two vendors' surfaces are four and one routes, readable in one module each.
- The ports' "measurements come from the file" rule holds without ffmpeg on the machine.
- A misconfiguration that would silently break the pipeline (a model without an ending frame, a
  lower-case `2k`) fails at startup with its variable named.

### Negative / Trade-offs

- We own the wire format. If Google or Higgsfield change a shape, the adapter breaks where an SDK
  might have absorbed it; the live test is what notices.
- The Interactions API is the one Google documents today for image generation; it is newer than the
  older `generateContent` route, and a change to it is ours to follow.
- A hand-written MP4/JPEG header reader is code to maintain. It reads four header fields and nothing
  else, and fails closed on anything unexpected.

## Alternatives considered

- **The official SDKs.** Rejected for the reasons above: `higgsfield-client` pulls `httpx` and its
  convenience is the blocking shape ADR-0066 refused; `google-genai` pulls ten packages for one call.
- **`ffprobe` for measurements**, as the clip renderer does. Rejected for this department: it would
  make ffmpeg a runtime requirement of a department that otherwise needs none.
- **Trust the vendor's reported dimensions/duration.** Rejected: the ports promise measurements of
  the file written, and Higgsfield reports none anyway.
- **Default the model or size.** Rejected by ADR-0040's rule and `PROJECT.md`'s "never hardcode a
  model".

## References

- ADR-0023 (ports by role), ADR-0040 (fail-closed env configuration, `urllib`), ADR-0043 (the one
  justified dependency), ADR-0056 §1 (measurements from the adapter), ADR-0064 (choosing no vendor
  surface when one is not needed), ADR-0066 (the ports and the two-call pipeline)
- `GENERATION_SPEC.md` §2–§4, `GENERATION_ACCEPTANCE.md` (GNP, MED, GIG, HVG, LIV)
- Higgsfield: `docs.higgsfield.ai/docs/concepts/{requests,polling,file-uploads,errors}.md`,
  `api-reference/requests/get-request-status.md`, `models/kling-3/*-image-to-video.md`
- Gemini: `ai.google.dev/gemini-api/docs/image-generation`, `ai.google.dev/api/interactions-api`
