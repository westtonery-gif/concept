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

### 3. The ending frame — the requirement that decides everything

ADR-0066 §1 requires a model that accepts an **ending** frame; the whole pipeline generates a start
and an end and asks for the motion between them. Seedance takes them as two sequential
`image_url` entries in the request's `content` array — start first, end second.

**This is confirmed from a third-party tutorial, not from BytePlus.** Their own docs still do not
render for us, exactly as in ADR-0081. Two gaps are recorded rather than papered over:

- the capability is quoted for **`seedance-1-0-pro-250528`**, while the pricing above is quoted for
  **`seedance-1-5-pro-251215`** — different models, and which one does both is not established;
- the vendor has not confirmed either fact directly.

Both must be checked against BytePlus before any money is spent (§Deferred). ADR-0081 refused
Seedance for exactly this standard of evidence, and lowering the bar now would be dishonest.

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

## Cost of one clip, for scale

| | Google plan (ADR-0081/0082) | BytePlus (this ADR) |
|---|---|---|
| starting + ending frame | ~$0.15 | 2 × $0.03 = **$0.06** |
| 8 s 720p video | $0.40 | **$0.21** |
| **per clip** | **~$0.55** | **~$0.27** |

At 100 clips a month that is ~$27 against ~$55 — which is the difference between a tariff that
carries its own model cost comfortably and one that does not (`concept-platform/DECISIONS.md`:
tariffs are priced in videos per month because the maintainer pays for the models).

## Deferred — must be closed before spending

1. **Confirm at BytePlus, not third parties:** which Seedance model id takes first-and-last frames,
   its price, and that Seedream accepts a reference image and a 9:16 ratio.
2. **Payment.** Whether BytePlus accepts the Mongolian card, and from which regions the API serves.
   The same wall as everywhere else in this project (`DECISIONS.md`: buyer in RF → Stripe out).
3. **Quality.** Nobody here has seen a Seedance clip or a Seedream frame. The first real generation
   is the check; if the output is poor, the ports make going back a one-module change.

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
- Seedance first-and-last frame and the token formula: third-party tutorial, to be confirmed (§3)
