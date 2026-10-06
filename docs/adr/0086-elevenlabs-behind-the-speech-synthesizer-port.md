# ADR-0086: ElevenLabs behind the `SpeechSynthesizer` port — expressive voices, true word times

- **Status:** Accepted
- **Date:** 2026-10-06
- **Deciders:** Maintainer ("слишком однотонно, всё-таки ElevenLabs", 2026-10-06, after listening to
  the Kokoro dialogue); Lead Architect
- **Builds on:** ADR-0085 (the port, and Kokoro as its first adapter — this is its Deferred item 1)
- **Serves:** `CLAUDE.md` queue task 23 (subtask 23.12)

## Context

ADR-0085 chose a local Kokoro model because it needs no account and cannot be revoked, and said
plainly that its voices are flat. The maintainer listened to a three-voice dialogue and judged it
**too monotone** for comic stories with banter and laughter. Kokoro cannot be made to act; a vendor
whose model reads emotion cues can.

ADR-0085 also recorded the cost of that choice's alternative: **ElevenLabs lists Russia among the
countries it refuses and does not accept Russian cards**, and the maintainer is in Russia. The
maintainer has weighed that and decided to use it. This ADR records the decision and the facts, not
a way round the restriction: the adapter has no proxy, region or identity option, and whether the
account is in good standing is the maintainer's to keep.

## Decision

### 1. `ElevenLabsSpeechSynthesizer` — a second adapter, same port

`infrastructure/elevenlabs_speech_synthesizer.py`, stdlib `urllib` like every other vendor adapter
here (no new dependency; the official SDK would add `httpx` and pydantic for one `POST`).

One `POST /v1/text-to-speech/{voice_id}/with-timestamps?output_format=pcm_24000`, header
`xi-api-key`, body `{text, model_id, voice_settings?}`. `voice` on the request is the vendor's
**voice id** (percent-encoded, so an id can never change the path). The answer's `audio_base64` is
raw 24 kHz 16-bit mono PCM, which is exactly what the shared writer (`speech_wav`) takes.

The contract (path, query values, body keys, `audio_base64`, `alignment` with `characters`,
`character_start_times_seconds`, `character_end_times_seconds`) was read from the vendor's **own
Python SDK on GitHub**, because their documentation site redirects every request from this
machine's region to the country-restriction page. It is confirmed against the real service only by
the opt-in live test (`LIV-04`); until it has run, the adapter is verified against a local server
that plays the shape the SDK describes.

### 2. True word times, with audio tags left out

The alignment is per character, so the adapter groups characters into words and uses the vendor's
**own** times — no estimate (ADR-0085 §3). `eleven_v3` reads **audio tags** in the text
(`[laughs]`, `[whispers]`, `[sighs]`); they are not spoken, so anything inside square brackets gets
no word. Captions must never light up `[laughs]`.

### 3. Configuration, and how the vendor is chosen

`OMEMO_ELEVENLABS_API_KEY` and `OMEMO_ELEVENLABS_MODEL` are required (no default model — selection
lives in config); `OMEMO_ELEVENLABS_STABILITY` (0–1) is optional and sent only when set, so a
character's voice settings are the vendor's unless the maintainer says otherwise. Speed goes into
`voice_settings` only when it is not 1.0.

`build_speech_synthesizer` now **chooses by presence**, the rule ADR-0081 §2 set for video: any
`OMEMO_ELEVENLABS_*` selects ElevenLabs, any `OMEMO_KOKORO_*` selects the local model, **both at
once is an error**, a partly configured vendor fails closed naming its own missing variables rather
than being quietly replaced by the other, and with neither the error names both sets.

### 4. One writer for every speech adapter

What is common — decode and check the PCM, write the WAV through a temporary name, read the duration
back from the file, ask for the word times, rename into place — moved to `infrastructure/
speech_wav.py`. Kokoro now uses it too; its behaviour and tests are unchanged.

## Cost

Third-party figures, not read from the vendor's own pages (the same region problem): about
**$0.10 per 1000 characters** on `eleven_v3` through the API, and a Starter plan around $5–6 a month
with commercial rights (the free plan has none and requires attribution). A story's dialogue is
roughly 1200 characters — **on the order of $0.12–0.15 per video** — against zero for Kokoro.

## Consequences

### Positive

- Voices that can laugh, whisper and sigh on cue, which is the gap the maintainer heard.
- Word times are the vendor's, so highlighting words in captions is exact.
- Kokoro stays: free drafts and a fallback if the account is ever lost, one `.env` edit away.

### Negative / Trade-offs

- **A paid, revocable dependency on a service that refuses the maintainer's country.** If the
  account is closed, paid credit and the voices a channel has become known for go with it; the port
  is the only protection, and it protects the code, not the audience's familiarity with a voice.
- Not yet proven against the real service (§1).
- `eleven_v3` limits a request to a few thousand characters; a line of dialogue is far inside it, a
  paragraph of narration might not be (not enforced here — the vendor's `400` would say so).

## Deferred

1. **The writer of the dialogue must emit audio tags** and pick a voice per character. Today the
   demo script is hand-written; an agent that writes it is the next role.
2. **Voice choice per character** — which voice ids, kept where. A character sheet beside the
   scene is the likely home.
3. **Whether `with-timestamps` accepts `eleven_v3`** — the SDK lists the route for every model, the
   vendor's blog does not say; the live test answers it.
4. **A per-line character budget** and tracking credit used, if the bill ever needs watching.

## References

- ADR-0085 (the port and Kokoro), ADR-0081 §2 (select by presence), ADR-0056 §1 (measure the file)
- ElevenLabs' official Python SDK (`elevenlabs-python`, GitHub): `convert_with_timestamps`,
  `AudioWithTimestampsResponse`, `CharacterAlignmentResponseModel`, `VoiceSettings`, the allowed
  output formats — read 2026-10-06
- ElevenLabs' country-restriction help article, and third-party pricing pages, 2026-10-06
