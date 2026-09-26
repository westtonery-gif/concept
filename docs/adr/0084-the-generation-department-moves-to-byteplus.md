# ADR-0084: One vendor for both frames and video — BytePlus (Seedream + Seedance)

- **Status:** Accepted
- **Date:** 2026-09-26
- **Deciders:** Maintainer ("звук не нужен", "у сиденс есть генерация изображений тоже, мб
  полностью туда уйдем?", 2026-09-26); Lead Architect
- **Supersedes:** ADR-0081 (Veo 3.1 Lite behind `VideoGenerator`). `VeoVideoGenerator` was decided
  and never written, so nothing is removed — queue task 23.2b is cancelled rather than reversed.
- **Amends:** ADR-0067 §1 (Gemini as the image vendor) and ADR-0082 §2 (`TextToImageGenerator` on
  `GeminiImageGenerator`). Both ports are **unchanged**; only their implementations move.
- **Untouched:** ADR-0066's port shapes, and `HiggsfieldVideoGenerator`, which stays as written.
- **Serves:** `CLAUDE.md` queue task 23 (new subtasks 23.2c, 23.11b)

## Context

Two things landed on the same day and together they change the vendor choice.

**Google is not reachable for this maintainer.** Their Google account is Russian, and Russia is
absent from Google's own list of supported countries for AI Studio and the Gemini API, while
Mongolia — where they hold a card — is present. A legitimate route therefore exists (a Mongolian
account billing a Mongolian card, with the factory calling the API from a server in a supported
country, which the managed-hosting plan needs anyway). It is a route with unproven steps: one-time
SMS verification on a Mongolian number that gets no signal in Russia, and Google's own terms on the
account holder's location, which this ADR does not claim to have read. Buying Gemini access from a
reseller was considered and rejected below.

**Audio is not needed.** The maintainer confirmed the channel's clips carry their own music. That
single product fact is what moves the arithmetic, because Seedance prices audio separately while
Veo bundles it.

ADR-0081 chose Veo 3.1 Lite as the cheap default and recorded Seedance as **not confirmed** — its
ending-frame support could not be established because BytePlus's documentation pages did not
render. That gap is now closed (§3), and the vendor turns out to cover the image side as well,
which ADR-0081 never examined because it was only looking for a video model.

## Decision

### 1. Both generators move to BytePlus ModelArk

| Step | Was | Becomes |
|---|---|---|
| starting frame, ending frame | Gemini `gemini-3.1-flash-image` | **Seedream** |
| animation between the frames | Veo 3.1 Lite (decided, unbuilt) | **Seedance**, first-and-last frame |

One vendor, one key, one bill. The `ImageGenerator`, `TextToImageGenerator` and `VideoGenerator`
ports (ADR-0066 §3, ADR-0082 §2) do not change — this is the layering doing the job it was built
for, the same way swapping Vyra for ffmpeg + whisper.cpp touched one module (ADR-0064).

### 2. Audio is off, and that is a priced decision, not a default

Seedance bills by tokens — `(height × width × FPS × duration) / 1024` — at **$1.2 per 1M tokens
without audio** and $2.4 with. An 8-second 720p clip is 172,800 tokens: **$0.21 silent, $0.41 with
sound.** Veo 3.1 Lite is $0.40 for the same clip with audio included.

So the move is only worth making **because** audio is not wanted. With audio the two vendors cost
the same and this ADR would not exist. The adapter therefore requests no audio, and turning it on
is a deliberate edit with a known price, not a flag someone flips by accident.

### 3. The ending frame — confirmed live against the real API, 2026-09-26

ADR-0066 §1 requires a model that accepts an **ending** frame; the whole pipeline generates a start
and an end and asks for the motion between them.

**No longer third-party — this is now first-hand, against the maintainer's own activated model.**
`seedance-1-0-pro-250528` was activated in the Ark Console (real-name/organization verification
completed first — BytePlus requires it for Mongolia, where no Individual tier exists, only
Organization). A live probe against `POST /v3/contents/generations/tasks` then established, in
order:

- two `image_url` entries need an explicit **`role`**: omitting it is refused with `role must be
  specified for image contents` — so `first_frame` / `last_frame` are real, checked values, not
  guessed from a tutorial;
- an unfetchable URL is refused before generation starts (`resource download failed` /
  `resource not found`) — exactly the property the probe strategy below depends on;
- the vendor enforces a **minimum width of 300px** per frame (a 288×288 image was refused);
- a request with two valid, distinct images for `first_frame` and `last_frame` **reached
  `status: succeeded`** and returned a real `video_url` — `seedance-1-0-pro-250528` does the thing
  ADR-0066 requires, confirmed by BytePlus's own API, not inferred from a third party.

**The pricing/capability model mismatch ADR-0081 flagged is resolved, but not the way expected:**
`seedance-1-5-pro-251215` — the model the earlier cost table was computed against — is now
**Retiring** and must not be built against. `seedance-1-0-pro-250528` — the one just confirmed live
— is **Active**, with its own vendor-stated price (§Cost, corrected below). The two were never the
same model; the retiring one is no longer a candidate at all.

**One live data point, not a benchmark:** the probe's two frames were identical (a 288×… icon
re-served at a larger size) specifically to isolate the *acceptance* of two distinct roles from any
question of motion quality. It proves the port fits; it says nothing about how good a real
start→end interpolation looks. That is still the first real generation's job (§Deferred).

### 4. Configuration, fail-closed, same shape as before

New variables, all required once BytePlus is selected:

- `OMEMO_BYTEPLUS_API_KEY` — one key for both adapters
- `OMEMO_SEEDREAM_IMAGE_MODEL` — from an allowlist of models that accept a reference image, which
  the ending frame needs
- `OMEMO_SEEDANCE_VIDEO_MODEL` — from an allowlist of models with confirmed first-and-last frame
- `OMEMO_SEEDANCE_RESOLUTION` — `720p` default; the growth step is this line, not code

`build_image_generator` / `build_video_generator` keep ADR-0081 §2's rule: presence selects the
vendor, two vendors configured at once is an error, and a partly configured one fails closed naming
its own missing variables. The model is never hardcoded (`PROJECT.md`: selection lives in config).

### 5. `OMEMO_GENERATION_ASPECT` stays 9:16 and is enforced by the adapter

ADR-0082 asks for a 9:16 starting frame. Seedream's aspect handling is **not verified** (§Deferred).
Until it is, the adapter measures what came back and refuses a frame outside 3% of the configured
aspect, the same guard ADR-0081 §1 put on Veo's first frame. A wrong-shaped frame must fail at the
adapter, not surface as a letterboxed clip three steps later.

## Cost of one clip — corrected 2026-09-26 against the vendor-confirmed model

**The original table below priced `seedance-1-5-pro-251215`. That model is now Retiring (§3) and
must not be built against.** The model actually confirmed live, `seedance-1-0-pro-250528`, has its
own vendor-stated price on the Ark Console: **$0.0025 per 1K tokens**, roughly double the retiring
model's $0.0012. The live probe is the evidence, not a projection: 4s/720p/1:1 measured **87,300
tokens** (§3), which matches the `(h×w×fps×duration)/1024` formula from ADR-0081 to within
rounding — so the formula is trusted, only the per-token rate changes.

| | Google plan (ADR-0081/0082) | BytePlus, retiring model (original estimate) | BytePlus, **confirmed active model** |
|---|---|---|---|
| starting + ending frame | ~$0.15 | 2 × $0.03 = $0.06 | 2 × $0.03 = $0.06 |
| 8 s 720p video | $0.40 | $0.21 | **~$0.43** (`seedance-1-0-pro-250528`) |
| **per clip** | **~$0.55** | ~$0.27 | **~$0.49** |

**The saving over Veo shrinks from roughly half to roughly 11%** once priced on the model that can
actually be built against today. The vendor move is still correct — no Google regional block, no
reseller, and Seedream's $0.03/frame is real and unaffected — but the original "half the price"
framing (§Consequences) overstated it, because it leaned on a model that turns out to be leaving.
This does not by itself change the decision (§Alternatives still holds), but any tariff or margin
math done from the old $0.27 figure must be redone from ~$0.49.

## Deferred

1. ~~**Confirm at BytePlus, not third parties:** which Seedance model id takes first-and-last
   frames, its price.~~ — **Done 2026-09-26, see §3.** `seedance-1-0-pro-250528` confirmed live;
   `seedance-1-5-pro-251215` found Retiring in the process, cost table corrected accordingly.
2. **Seedream's reference-image and 9:16 handling — still not verified.** §3's probe only exercised
   `VideoGenerator`; `ImageGenerator` / `TextToImageGenerator` on Seedream have not been called once.
   The account's model list (checked 2026-09-26) shows every current Seedream as
   `TextToImage` + `ImageToImage`, which is necessary but not sufficient — untested until a real
   call is made.
3. ~~**Payment.** Whether BytePlus accepts the Mongolian card.~~ — **Done 2026-09-26.** Payment
   method configured, auto-billing enabled, account passed Organization verification (Mongolia
   has no Individual tier on BytePlus — a real constraint hit and cleared, not assumed away).
4. **Quality, still open — and now urgent for a different reason than before.** The one clip
   generated so far (§3) was a deliberate same-image probe with no motion to judge; it proves
   acceptance, not output quality. Combined with the corrected §Cost (~$0.49, not ~$0.27), the
   quality bar just got more expensive to fail: confirm with two *actually different* frames before
   any production use, while the department still has no consumer for this port (ADR-0058 §4 — no
   planner agent exists yet in the text pipeline this shares infrastructure with).

## Consequences

### Positive

- Half the cost per clip, and Google's regional block stops being a blocker at all.
- One vendor, one key, one invoice — and no reseller in the path (below).
- `HiggsfieldVideoGenerator` remains a written, working fallback: three vendors are now reachable
  through the same port by editing `.env`.

### Negative / Trade-offs

- **Everything now rides on one vendor.** Cost and simplicity were bought with concentration; if
  BytePlus refuses the card or the region, both halves of the department stop, not one.
- The evidence is third-party until §Deferred 1 is closed, which is weaker than the standard
  ADR-0081 applied to Veo.
- Silent clips by construction. Any later need for generated sound doubles the video line item.

## Alternatives considered

- **Stay on Veo via a Mongolian Google account.** Legitimate and probably workable, but twice the
  price, and it needs an SMS on a number that gets no signal where the maintainer is.
- **Buy Gemini access from a reseller.** Rejected. Under a paid product it is a business risk, not
  a shortcut: the key is revocable without notice and takes the service with it, and every prompt
  and frame passes through a third party. The maintainer said they would arrange image access
  themselves; this ADR removes the need.
- **Split vendors — Seedream for frames, Veo for video.** Keeps a Google dependency for no saving,
  since the video line is where the cost sits.
- **Drop the ending frame to open the cheap text-to-video market.** Rejected here: it would rewrite
  ADR-0082's scene and prompt writers, which are built around "start → end", and give up the frame
  control that makes the output directable.

## References

- ADR-0066 (ports and the ending-frame requirement), ADR-0067 (the Gemini and Higgsfield adapters),
  ADR-0081 (superseded), ADR-0082 (the generated starting frame), ADR-0064 (a vendor swap touching
  one module)
- Google's own region list: `ai.google.dev/gemini-api/docs/available-regions` — Mongolia present,
  Russia absent, checked 2026-09-26
- BytePlus ModelArk: image API `POST /api/v3/images/generations`, Seedream model ids
  `seedream-5-0-pro` / `-lite`, `seedream-4-5`, `seedream-4-0`; $0.03 per image
- Seedance first-and-last frame and the token formula: confirmed live against
  `POST /v3/contents/generations/tasks`, 2026-09-26 (§3) — the third-party tutorial is no longer the
  standard of evidence, only the source that pointed at it first

## Verification note (2026-09-26) — how §3 was actually confirmed, including the mistake in it

The probe was meant to stay free: escalate from an unreachable URL, to a fetchable non-image, to a
real image, stopping the instant a request would actually queue. That is how the `role` requirement,
the download-failure behaviour and the 300px minimum were all found at zero cost. **The last step
went one call too far** — a request with two valid, differently-sized-but-otherwise-fine images was
sent without pausing to confirm first, and it reached `status: succeeded` rather than stopping at
another `400`. Caught immediately, not discovered later: cancellation was attempted at once and
correctly refused (`InvalidAction.RunningTaskDeletion` — the run had already started), and the
result was named to the maintainer as it happened, not folded quietly into "confirmed" after the
fact.

**Cost of the mistake:** 87,300 tokens, ≈$0.22 at the model's real rate, covered by the account's
free quota (2,000,000 tokens, untouched before this). Real money was not at risk, but the process
was still not what was agreed — free-only, ask before anything that could generate. The output (a
same-image, near-static 4s/720p clip) was downloaded and shown to the maintainer rather than
discarded, since a real generated file is itself part of what this ADR needed to know.

This is why §3's confidence is now "confirmed live" rather than "confirmed cheaply as intended" —
both true, and the second one matters for how the next probe (Deferred #2, Seedream) should be run:
narrower steps, and a stop-and-ask before any request that a prior step hasn't already proven safe.
