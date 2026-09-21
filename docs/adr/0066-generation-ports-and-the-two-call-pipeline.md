# ADR-0066: The generation ports — `ImageGenerator` and `VideoGenerator`, and a two-call pipeline

- **Status:** Accepted
- **Date:** 2026-09-21
- **Deciders:** Lead Architect / Domain Architect (the pipeline's shape chosen by the maintainer)
- **Resolves:** ADR-0065 "Deferred" — the two ports' exact shape, and whether Nano Banana produces
  an intermediate asset Higgsfield then animates
- **Serves:** `CLAUDE.md` queue task 23.1

## Context

ADR-0065 left one question it refused to guess: does Higgsfield/Kling need a Nano Banana image as
input, or can it go from a photo and a text prompt straight to video? It also left open whether
Gemini's image call is fast enough to be made synchronously. Both were checked against the vendors'
own documentation on 2026-09-21, the way ADR-0064 checked ffmpeg's filter list before deciding.

**Higgsfield** (`docs.higgsfield.ai`, shared pages *Requests and lifecycle*, *Polling*, *Webhooks*,
*File uploads*, *Errors and retries*, *Billing and retention*, and the Kling 3.0 model pages):

| Fact | Consequence here |
|---|---|
| Every generation is asynchronous: `POST` to a model endpoint returns `queued` with a `request_id`, `status_url`, `cancel_url` | A submit and a later collect, not one blocking call |
| Statuses: `queued`, `in_progress` (non-terminal); `completed`, `failed`, `nsfw`, `canceled` (terminal). `failed`/`nsfw` are not charged | Four outcomes the port must be able to say, and a content rejection is distinct from a failure |
| **Submissions accept no idempotency key**; the docs say not to repeat a generation `POST` after an ambiguous timeout | A crash between submit and commit cannot be recovered by resubmitting — §3 |
| Status polling: start at 2 s, back off to 10 s with jitter; `GET` is safe to retry | Collecting is repeatable; submitting is not |
| Webhooks need a publicly reachable HTTPS endpoint that answers within 10 s | Not v1 — the production service binds `127.0.0.1` (ADR-0049) |
| Input media is a **public URL** (`image_url`); local files go through `POST /files/generate-upload-url` and a presigned `PUT` | Uploading is a vendor detail; the port takes local files |
| Output URLs are retained **at least seven days** | The file is downloaded into our own destination on collect |
| Kling 3.0 **image-to-video** takes `image_url` + `prompt` (3–15 s, all tiers) — no Nano Banana image is *needed* | The one-call pipeline is possible |
| Kling 3.0 **Standard / Pro / 4K** image-to-video also take an optional **`last_image_url`** (an ending frame); **Turbo does not**. Their `sound` parameter defaults to `on` | The two-call pipeline is possible, and restricts which tier can serve it |
| Auth: `Authorization: Key <HF_API_KEY_ID>:<HF_API_KEY_SECRET>`; official Python SDK `higgsfield-client` exists | Subtask 23.2's business |

**Gemini** (`ai.google.dev/gemini-api/docs/image-generation`, last updated 2026-09-17): image
generation and editing (`gemini-3.1-flash-image` "Nano Banana 2", `gemini-3-pro-image` "Nano Banana
Pro", and the Lite and legacy 2.5 variants) is a **synchronous** request whose response carries the
image bytes (base64) inline. An input photo is passed inline as base64 with its MIME type (the Files
API is the route for larger payloads). The request can fix the output's aspect ratio (a closed list
including `9:16`, `16:9`, `1:1`) and size (`0.5K`–`4K`). Every generated image carries a SynthID
watermark. The docs give no latency figure; they present the Flash models as the low-latency ones
and let thinking be set to `minimal`. A Batch API exists and is not what a one-image-per-request
department needs.

So the answer to ADR-0065's question is **both are possible**, and which one v1 builds is a
product choice, not a vendor fact. The two options were put to the maintainer on 2026-09-21:

- **one call** — the reference photo and a prompt straight into Kling image-to-video; Gemini is not
  used in v1 at all;
- **two calls** — Nano Banana turns the reference photo into the *ending* frame (the finished
  bunker, the restored building), and Kling animates from the photo to that frame through
  `last_image_url`. This is the mechanism a construction or restoration timelapse actually needs:
  the model is told where the video must end instead of guessing it.

**The maintainer chose two calls.**

## Decision

### 1. v1's pipeline: photo → ending frame (Gemini) → video between them (Higgsfield/Kling)

One generation request runs, in order:

1. **Ending frame** — `ImageGenerator.generate`: the reference photo and an image prompt in, one
   image file out, with the photo's aspect ratio.
2. **Video** — `VideoGenerator.submit` then `VideoGenerator.collect`: the reference photo as the
   first frame, the generated image as the last frame, and a video prompt; one video file out.
3. QA, Human Approval, one approved video file (ADR-0065 §5; criteria are subtask 23.3).

Both prompts are human input in v1 (ADR-0065 §5). Whether the board carries them as two fields or
one field the department reuses for both steps is the board ADR's business (task 23.4); the ports
take one prompt each, because the two steps are asked different things (*what the end looks like*
vs. *how the change unfolds*).

The ending frame is an intermediate, not a deliverable: it is recorded in the Run (a succeeded
Task's Output) so a resume reuses it instead of paying for it twice, but it is not an Artifact a
human approves on its own. Whether a human should see it before the video is paid for is a v2
question (Deferred).

### 2. `ImageGenerator` is synchronous; the call happens in a Workflow step inside the queued worker

Gemini answers in one HTTP round trip, so the port is one blocking method, shaped like
`ClipRenderer` (ADR-0056 §1: the adapter wrote the file and reports what it wrote):

```python
@dataclass(frozen=True, slots=True)
class ImageGenerationRequest:
    reference: str        # local path of the reference photo
    prompt: str           # non-blank
    destination: str      # local path to write the image to

@dataclass(frozen=True, slots=True)
class GeneratedImage:
    path: str
    width: int
    height: int
    media_type: str       # e.g. "image/png"

class ImageGeneratorError(Exception): ...

class ImageGenerator(Protocol):
    def generate(self, request: ImageGenerationRequest, /) -> GeneratedImage: ...
```

"Synchronous" means it needs no job id, not that it is quick enough for a reasoning step: it is
still an outside, paid call, so it runs in a deterministic Workflow step of the department's
application module, inside the ADR-0049 worker, never inside a model's Tool budget (ADR-0054 §2
already forbids a Tool that writes a file). The adapter keeps the photo's aspect ratio by asking
Gemini for it; how it maps an arbitrary photo onto Gemini's closed list is an implementation detail
of 23.2, but a result whose aspect differs from the photo's is the adapter's failure, not a verdict.

A crash after Gemini answered but before the step committed repeats the call on resume. That is
ADR-0026 §4's at-least-once, the same exposure `ClipRenderer` has: it spends a few cents again and
overwrites the same destination, and it corrupts nothing.

### 3. `VideoGenerator` is two methods, and the job id is committed between them

Because a Higgsfield submission cannot be made idempotent, **the port does not hide polling behind
one blocking call.** A blocking `generate` would leave nothing in the Run between "paid for" and
"downloaded", and a crash there would make resume submit — and pay for — a second video.

```python
@dataclass(frozen=True, slots=True)
class VideoGenerationRequest:
    first_frame: str      # local path — the reference photo
    last_frame: str       # local path — the generated ending frame
    prompt: str           # non-blank
    duration_s: int       # within the configured model's range

@dataclass(frozen=True, slots=True)
class VideoJob:
    job_id: str           # opaque to the core; Higgsfield's request_id today

class VideoJobState(Enum):
    PENDING = "pending"       # queued or in progress — ask again later
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"     # the vendor's content moderation said no ("nsfw")

@dataclass(frozen=True, slots=True)
class VideoJobResult:
    state: VideoJobState
    video: GeneratedVideo | None = None   # set exactly when COMPLETED

@dataclass(frozen=True, slots=True)
class GeneratedVideo:
    path: str
    duration_ms: int
    width: int
    height: int
    container: str

class VideoGeneratorError(Exception): ...

class VideoGenerator(Protocol):
    def submit(self, request: VideoGenerationRequest, /) -> VideoJob: ...
    def collect(self, job: VideoJob, destination: str, /) -> VideoJobResult: ...
```

- **`submit`** uploads the two frames and starts the job. It is the department's one non-repeatable
  call. The Workflow commits the Task that will make it **before** calling (ADR-0026 §2, as
  `ClipProduction` does), and commits the returned `job_id` **immediately after**, as that Task's
  succeeded Output. A submit Task found still `RUNNING` on resume means the outcome is unknown: it
  is **failed with a stable reason and never resubmitted** — the vendor's own instruction, and the
  only rule that cannot pay twice. A human re-triggers if they want another attempt.
- **`collect`** is one status check. It is repeatable (a `GET` and a download into the same
  destination), so a crash during it simply collects again. `PENDING` is not an error. On
  `COMPLETED` the adapter downloads the file into `destination` and reports its own measurements,
  so a format check on the result is arithmetic, as for clips (ADR-0056 §1). `CANCELED` never
  reaches the core: v1 never cancels, so the adapter treats it as `FAILED`.
- **`REJECTED` is not `FAILED`.** A content-moderation refusal is a fact a human should read, not a
  technical fault to retry; how the department surfaces it is the spec's business (23.6), but the
  port keeps the distinction so it is not lost at the boundary.
- **Polling cadence is the Workflow's, not the port's.** v1 calls `collect` inside the same
  invocation with the vendor's recommended backoff (2 s rising to 10 s, with jitter) up to a
  configured deadline. At the deadline the invocation ends with the job still `PENDING` and the Run
  still running; the next invocation — an n8n trigger or sweep, as for every other department —
  collects by the committed `job_id`. Nothing is resubmitted. The single ADR-0049 worker is blocked
  while it polls; clipping's indexing already accepted the same cost.

### 4. The vendors stay behind the ports

- **Local paths in, local paths out.** Higgsfield's public-URL requirement, its upload endpoint,
  Gemini's base64 encoding, both vendors' model ids and parameter names, and every output URL
  (which is short-lived and, while it lives, a bearer of the content) stay in `infrastructure/`. No
  URL is recorded in a Run, logged, or put in an error message.
- **`duration_s` is the only generation parameter the core names.** Model id, resolution, `sound`,
  `cfg_scale` and similar are configuration read by the adapter (ADR-0040's fail-closed,
  no-default rule; the variable names are 23.2's). The configured video model **must accept an
  ending frame** — on 2026-09-21 that is Kling 3.0 Standard, Pro or 4K image-to-video, not Turbo —
  and the adapter refuses at construction a configuration it knows cannot.
- **Errors.** Each port raises its own technical `<Port>Error` (not a `DomainError`), whose message
  says what went wrong without a key, token or URL — the discipline `notion_settings_from_env`
  keeps. Rejected credentials, insufficient credits and a blocked model are `VideoGeneratorError`
  from `submit`; the Task fails with a stable reason and nothing was charged.
- **No Tool is built over either port in v1.** There is no agent (ADR-0065 §5), so ADR-0054's
  allowlist is not widened. Should the later prompt-crafting Agent want to *look* at a generated
  frame, that is a read and would go through ADR-0054 §3; generating never becomes a Tool
  (ADR-0054 §2).

### 5. What is not decided here

Libraries (official SDKs vs. stdlib `urllib`) and env var names are subtask 23.2's own evaluation,
the way ADR-0040 and ADR-0064 made theirs. The Run's Task/Output layout — step refs, payload shapes,
which Tasks a resume reuses — is `GENERATION_SPEC.md`'s (23.6), after the Run-granularity decision
(23.5). This ADR fixes what the ports promise and the one rule a resume must never break.

## Deferred

- **Showing the ending frame to a human before the video is paid for** — a second gate would cut
  wasted video spend on a bad frame, at the cost of a second round trip through the review desk.
  Not v1; revisit after the first real generations show how often the frame is the problem.
- **Webhooks** instead of polling — needs a publicly reachable HTTPS endpoint the service does not
  have today. Polling stays the recovery path even if webhooks are added (the vendor's own advice).
- **Cancellation** — `cancel_url` exists and only works while `queued`; v1 never cancels.
- **The one-call pipeline** as a per-request option (no ending frame) — the port's `last_frame` is
  required in v1; making it optional is additive if it is ever wanted.
- **Cost estimation before submit** — Higgsfield has an estimate endpoint; a budget check would use
  it. Not v1.
- **Audio** — the Kling tiers that take an ending frame produce sound by default. Whether v1 keeps,
  strips or forbids it is for the QA criteria (23.3) and the adapter's configuration (23.2).

## Consequences

### Positive

- ADR-0065's open question is answered from the vendors' pages, not assumed, and the answer changed
  the design: "Higgsfield is async" alone would have suggested one blocking call behind the queue;
  "and a submit cannot be repeated" is what forces the job id into the Run.
- The ports carry nothing either vendor would recognise. Replacing Gemini with another image model,
  or Higgsfield with a direct Kling or another video API, touches one module in `infrastructure/`,
  as ADR-0064 showed for `FootageIndex`.
- Collect-only resumption means an invocation that times out while the vendor is still working costs
  nothing to retry.

### Negative / Trade-offs

- Two vendors, two paid calls and two prompts per video, where one call would have worked. Chosen
  for control over the ending, knowingly.
- The Turbo tier cannot be used, since it takes no ending frame — likely the cheapest and fastest
  Kling option on Higgsfield is off the table for v1.
- A crash in the narrow window between Higgsfield accepting a submission and the Run recording it
  leaves a paid-for video the department will not collect; a human must re-trigger. Accepted: the
  alternative is a design that can pay twice without anyone noticing.
- One worker blocked for minutes while polling delays every other queued job, as clipping's indexing
  already does.

## Alternatives considered

- **One call: the photo and a prompt straight into Kling image-to-video.** Simpler and cheaper, and
  the vendor supports it. Not chosen by the maintainer: the video's ending would be the model's guess,
  which is exactly what a timelapse to a known end state cannot afford. Kept reachable (Deferred).
- **A single blocking `VideoGenerator.generate` that submits and polls internally.** The easiest
  port to write and to fake. Rejected: with no idempotency key, a crash while polling would make
  resume submit again and pay twice, and nothing in the Run would show it.
- **Webhooks.** The vendor's recommendation for production. Rejected for v1 only: no public HTTPS
  endpoint exists, and adding one is a deployment decision, not a port decision.
- **Route Gemini through a submit/collect shape too, for uniformity.** Rejected: a synchronous API
  given an asynchronous port would need a fake job id and a second call that only returns a stored
  result — machinery with nothing to do.
- **Pass URLs through the ports** (the photo already uploaded, the result as a vendor URL).
  Rejected: it leaks a vendor's hosting model into the core, and a recorded output URL would expire
  in days while the Run lives on.

## References

- `ARCHITECTURE.md` §8, §15 (a vendor reaches the core only through its port and layer)
- ADR-0023 (ports named by role), ADR-0026 §2/§4 (commit before the outside call; at-least-once),
  ADR-0040 (fail-closed env configuration), ADR-0049 (the queued worker), ADR-0054 §2 (Tools observe,
  they do not change), ADR-0056 §1 (the adapter reports its own measurements), ADR-0064 (checking the
  real tool before deciding), ADR-0065 (the department's shape and this ADR's open questions)
- Higgsfield: `docs.higgsfield.ai/docs/llms.txt` and the pages named in Context; Kling 3.0
  `models/kling-3/{turbo,standard,pro,4k}-image-to-video`
- Gemini: `ai.google.dev/gemini-api/docs/image-generation`
- `CLAUDE.md` queue task 23
