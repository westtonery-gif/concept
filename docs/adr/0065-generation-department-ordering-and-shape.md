# ADR-0065: The generation department is the next work, and the shape it takes

- **Status:** Accepted
- **Date:** 2026-09-21
- **Deciders:** Lead Architect / Domain Architect
- **Closes:** `CLAUDE.md` queue task 9 (the Stage 13 media-production ordering decision), narrowed to
  the v1 scope this ADR states. Does **not** authorize the rest of ROADMAP Stage 13 (Research /
  Article / Carousel Agents, Analytics Agent) or `CLAUDE.md` task 10
  (`CONTENT_FACTORY_THOUGHTS.md` §16's broader video-vertical-slice vision).

## Context

`CLAUDE.md` task 9 has said since 2026-09-15 that real media production (ROADMAP Stage 13) is
deliberately deferred and needs its own explicit ordering ADR before any code — "asked, not
guessed." ADR-0053 exercised that exact rule for a different candidate (the clipping department)
and, in its own Alternatives considered, explicitly refused to fold that department into Stage 13:
*"Stage 13 is about generating media from nothing, this department reduces existing footage. They
share the adapter shape and nothing else."* The candidate this ADR is about **is** "generating media
from nothing" — it is Stage 13's media-production paragraph itself, not a third thing beside it.

On 2026-09-21 the maintainer asked to start it: AI-generated short videos (the example given — a
timelapse of a bunker's construction) from a reference photo, using **Google Gemini's image
generation** (branded "Nano Banana": Gemini 2.5/3 Flash/Pro Image) and **Higgsfield's video API**
(a unified, asynchronous API over 50+ models including **Kling**, checked 2026-09-21 at
`docs.higgsfield.ai`). The maintainer also named a ChatGPT Store custom GPT, "Restoration
Timelapse," that turns a photo into a generation prompt, and asked whether it could be called from
this pipeline.

Four questions were put to the maintainer and answered the same day, in the shape ADR-0053 used:

1. **It is next work, starting now** — the same explicit go-ahead ADR-0053 §1 recorded for
   clipping.
2. **Source material** enters the same way clipping's does: a **separate Notion database** plus a
   **local folder holding a reference photo**, not the brief board and not a new mechanism.
3. **v1's deliverable is an approved video file, no auto-publish** — PROJECT.md §12's Human
   Approval invariant, and the same scope ADR-0053 §6 drew for clipping.
4. **The prompt-crafting step is out of v1.** A human supplies the generation prompt by hand (using
   the GPT Store tool interactively, or however they choose) as an input field alongside the
   reference photo; an automated prompt-crafting Agent is wanted later, not now.

On the GPT Store question specifically: **a published custom GPT with no declared Action has no
callable API.** OpenAI's platform does not expose one — it is a ChatGPT-surface-only object, not a
limitation of this repo's design. So "plugging it in" is not an available option at all, in v1 or
later. If the prompt-crafting step becomes an Agent, it is built on this repo's own LLM
infrastructure (Claude already takes image input), reusing `client_for_role`, the Prompt store and
metrics capture, not a new OpenAI integration for one narrow step.

## Decision

### 1. The generation department is the next work — as Stage 13's media-production slice, narrowed

We will build it next. It **is** ROADMAP Stage 13's media-production paragraph (image/video
generation), not a fourth department beside content, QA-gated the same way as everything else in
this repo — but narrowed to v1: image + video generation ending at one approved file. It is **not**
authorization for the rest of Stage 13 (new text-content Agents, Analytics Agent) or for
`CLAUDE.md` task 10's larger multi-tenant vision, exactly as ADR-0053 §1 did not reorder task 10
relative to itself. `ROADMAP.md` Stage 13's ordering paragraph is amended in the same change to
record this.

### 2. It is an additive department, not a change to the factory

Every part of it is a new module: new role-named ports in `adapters/`, vendor code in
`infrastructure/`, a new Workflow shape (application code beside `ContentDirector`, mirroring
`ClipProduction` — ADR-0059 §1's reasoning applies unchanged: a candidate that needs its own QA
verdict and its own human gate is Run/Task/Artifact/Evaluation/Human Review exactly as they already
work). `Run`, `Task`, `Output`, `Artifact`, `Evaluation`, `Human Review`, `ContentDirector` and the
existing Adapters are not touched (`PROJECT.md` §4.11). If this department turns out to need a core
change, that is an architecture defect signal and gets its own ADR before any code, as ROADMAP
Stage 13's own DoD already demands.

### 3. Vendors sit behind role-named ports, not vendor-named ones

Image generation and video generation are two capabilities, so two ports (ADR-0023's convention:
named by role — e.g. something like `ImageGenerator` / `VideoGenerator`, not `GeminiClient` /
`HiggsfieldClient`). The core never imports Google's or Higgsfield's SDKs, names them in a contract,
or depends on their call shapes. Exact port names and methods are the follow-up ADR's business, the
same split ADR-0053 §3 made for Vyra.

### 4. Higgsfield's video generation is asynchronous and must run as a queued step

Higgsfield's own documentation describes every generation as asynchronous: submit a request, then
poll or receive a webhook (checked 2026-09-21). This is minutes-long work exactly like clipping's
indexing/transcription, so it runs through the `ProductionService` queued-worker shape (ADR-0049),
never inside an LLM reasoning step's call budget (ADR-0028 §3) — ADR-0053 §4's rule restated for a
second vendor. Whether Gemini's image generation is fast enough to call synchronously, or should go
through the same queue for uniformity, is for the ports ADR to check against the real API, not to
assume here.

### 5. v1 has no prompt-crafting agent — the generation prompt is a human input

Mirroring ADR-0058 §4's precedent (clipping's v1 also shipped without its planner agent, keeping
only the judgement that actually needed a model — QA): the department's v1 pipeline is a
human-supplied prompt and reference photo in, a vendor call, a QA verdict, a human Approve, an
approved file out. No model decides what to generate. This is not a limitation discovered later; it
is the maintainer's explicit choice, so that the vendor-calling skeleton is proven before an agentic
step is added on top of it — the same order clipping's own history took.

## Deferred — each needs its own ADR before any code

- **The two ports' exact shape.** What `ImageGenerator`/`VideoGenerator` (working names) return,
  how a request names its reference photo and prompt, and how the async Higgsfield result is
  represented (a job id the queue polls, per ADR-0049's shape).
- **Whether Nano Banana produces an intermediate asset Higgsfield then animates, or whether
  Higgsfield/Kling can go from a photo + text prompt straight to video.** A real vendor-capability
  question, not to be guessed — the first thing the ports ADR must check against both APIs directly
  (`docs.higgsfield.ai`, the Gemini image-generation docs), the same way ADR-0064 checked ffmpeg's
  actual filter list before deciding anything.
- **The board's properties** — mirroring `EPISODE_BOARD` (ADR-0055): a title, a readiness property,
  the reference-photo location, the manual generation prompt, `Run status` / `Run id`, and whether
  the existing Notion integration needs sharing onto a new database the way clipping's did
  (ADR-0055 §4 found this was never a real blocker once checked).
- **QA criteria for generated video** — a different judgement than clip QA (ADR-0056): coherence
  with the prompt, visual/temporal artifacts, nothing the platform would reject — asked of the
  maintainer, not invented, the same way ADR-0056 §2 and ADR-0053's clip criteria were.
- **Auth and env vars** — `OMEMO_GEMINI_*` / `OMEMO_HIGGSFIELD_*` (naming TBD), following the
  `client_for_role` / `OMEMO_NOTION_*` convention of explicit, fail-closed, env-sourced
  configuration, never hardcoded (ADR-0040's rule).
- **One Run per generation, or a batch** — mirroring the clipping department's own settled question
  (ADR-0059), but this department's unit of work (one requested video) does not obviously fan out
  the way one episode fans out into ~13 clips, so this may simply be one Run per request.
  Confirm, don't assume, in the Run-granularity ADR.
- **Any post-processing of the vendor's output** — captions, trimming, format checks analogous to
  `check_clip_format` (ADR-0056 §1) — deferred to whichever ADR designs the render/assembly step.
- **The prompt-crafting Agent** — wanted later, explicitly out of v1 (Decision §5). When it is
  built, it is this repo's own Agent on Claude, not an OpenAI integration (Context, above).
- **Automatic publishing** — out of scope until asked for, additive per-platform Adapters later,
  the same deferral ADR-0053 §6 made for clipping.

## Consequences

### Positive

- The queue's own rule is honoured: this department starts from an explicit ordering record naming
  real vendor facts, not an assumption.
- The GPT Store question is answered definitively (a platform limitation, not a design choice),
  closing off a dead-end investigation before any code depended on it.
- The department reuses infrastructure two vendors already justified building: `ProductionService`
  (ADR-0049) for async work, the Notion board + local-file pattern (ADR-0053/0055), and the
  additive-Adapter discipline (ADR-0023/0037) that has now held for four outside systems in a row
  (Notion, Google Docs, Vyra's replacement, and now two more).

### Negative / Trade-offs

- A third department run in parallel with an unfinished Milestone M3 pilot and a clipping department
  that has not yet produced a first real clip. The quality-gate surface keeps growing faster than
  any one department reaches production proof — accepted deliberately, as ADR-0053 §"Negative"
  already accepted once.
- Committing to Gemini and Higgsfield before either is called for real draws the ports' shape around
  two vendors' current APIs. The role-named-port discipline reduces this cost; it does not remove
  it, exactly as ADR-0053 said of Vyra.
- Deferring the prompt-crafting Agent means v1's throughput is bounded by how fast a human can write
  generation prompts by hand — an accepted, named cost, not a hidden one.

## Alternatives considered

- **Build the prompt-crafting Agent first.** Rejected by the maintainer, mirroring ADR-0058 §4's own
  precedent: prove the vendor-calling skeleton before adding a model-decision step on top of it.
- **Call the "Restoration Timelapse" GPT Store tool programmatically.** Not available — no declared
  Action means no callable API; this is a platform fact, not a choice between alternatives.
- **Treat this as an extension of the clipping department.** Rejected on the same grounds ADR-0053's
  own Alternatives section already gave: they share the adapter shape and nothing else, and folding
  them together would blur two independently-scoped v1s.

## References

- `PROJECT.md` §4 п.11 (extend by adding, never by modifying the core), §12 (nothing ships without
  Approve)
- `ARCHITECTURE.md` §8 (a Tool/Adapter reaches the world only through its own layer), §15 (layering)
- `ROADMAP.md` Stage 13 (media production's architectural shape)
- ADR-0023 (Adapter contracts named by role), ADR-0037 (business-agnostic core), ADR-0040
  (fail-closed, no-default env config), ADR-0049 (the queued production service), ADR-0053 (the
  clipping department's ordering ADR — the template this one follows), ADR-0055 (the episode board
  shape), ADR-0058 §4 (an agentless v1, the precedent for deferring the prompt-crafting Agent)
- `CLAUDE.md` queue tasks 9, 10
