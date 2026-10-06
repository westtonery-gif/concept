# ADR-0085: A `SpeechSynthesizer` port, and a local Kokoro adapter behind it

- **Status:** Accepted
- **Date:** 2026-10-06
- **Deciders:** Maintainer ("надо достроить фабрику, а именно голос", 2026-10-06; listened to the
  Kokoro test and answered "годится"); Lead Architect
- **Serves:** `CLAUDE.md` queue task 23 (new subtask 23.12)
- **Touches:** nothing existing — the port and the adapter are new modules (`PROJECT.md` §4 п.11)

## Context

The story-video format the maintainer wants to make (reference videos analysed 2026-10-06) is
**dialogue**: several characters speaking in different voices, with burnt-in captions that
highlight the word being said. The generation department has no voice at all. Seedance 1.0 pro
produces no audio (measured, ADR-0084 §Deferred), and nothing in `src/` turns text into speech.

Three ways to get a voice were looked at.

- **ElevenLabs** — the best quality and the most voices. It officially refuses access from Russia
  (its own help centre lists it) and does not accept Russian cards; the maintainer is in Russia, and
  a service the factory depends on for every video is exactly where the Google block (ADR-0084)
  would repeat. Its free plan also carries **no commercial rights**; the cheapest paid one (Starter,
  ~$6/month) does.
- **BytePlus speech** — advertised with 200+ voices on the vendor the department already uses. Its
  price, English voices and API were **not found** in a first search, so it is unknown rather than
  rejected.
- **Kokoro-82M, locally** — Apache-2.0 weights, runs on the maintainer's Mac on the CPU, no
  account, no region, no per-character price. Measured on the maintainer's machine on 2026-10-06:
  31.5 seconds of three-voice dialogue rendered in 21.7 seconds, and **the maintainer listened to it
  and judged it good enough** for the format. It has 28 English voices (American and British) and no
  Russian ones.

## Decision

### 1. A role-named port, `SpeechSynthesizer`

`adapters/speech_synthesizer.py`, in the shape of the other generation ports (ADR-0066): paths and
plain values in, a path and **the adapter's own measurements** out.

`synthesize(SpeechRequest(text, voice, destination, speed)) -> SynthesizedSpeech(path, duration_ms,
sample_rate, words)`. `voice` is an **opaque non-blank string** — what it means is the adapter's
business, so a vendor's voice id and a local voice name fit the same field. `words` are the spoken
words with start and end times in milliseconds (`SpokenWord`): captions that highlight the word
being said (ADR-0071's burn-in, extended) need them, and a vendor such as ElevenLabs returns them,
so the port owes them whoever implements it.

The call is **synchronous and repeatable**: repeating it overwrites the same destination and
corrupts nothing (a paid vendor would spend again, as `ImageGenerator` does, ADR-0026 §4). It runs
in a deterministic Workflow step, never inside a model's Tool budget (ADR-0054 §2).

A dialogue is **several requests**, one per line, and joining them (gaps, order, mixing under the
music) belongs to the assembly step with ffmpeg, not to this port. The port speaks one utterance.

### 2. `KokoroSpeechSynthesizer` — in-process, with the engine injectable

`infrastructure/kokoro_speech_synthesizer.py`. The adapter holds everything that is **ours**
— the voice grammar, the file, the measurements, the word times — and delegates only "text → PCM"
to a small `SpeechEngine` seam. The default engine imports `kokoro_onnx` and `numpy` **lazily**, on
first use, so the core, the test suite and CI need neither package. This is the `ffmpeg` /
`whisper-cli` pattern (ADR-0064) with one difference: a Python package is called in-process instead
of a binary through `subprocess`, because there is no Kokoro binary and a subprocess around a
script would only add a second place for the settings to live.

- **Voice grammar.** `name` for one voice, or `name:weight+name:weight` for a **blend** (voice
  style vectors averaged by weight; weights must be positive and are normalised). Blending was
  verified on 2026-10-06 and is how 28 stock voices become many characters.
- **Language.** Taken from the first voice's prefix: `af_`/`am_` → American English, `bf_`/`bm_` →
  British English. Any other prefix is `SpeechSynthesizerError` in v1 — the model has other
  languages, but nothing here has been listened to, and a silent wrong accent is worse than a
  refusal.
- **Output.** A mono 16-bit PCM WAV, written to a temporary file next to the destination and
  renamed into place, so a crash never leaves half a file and never destroys a previous one.
- **Measurements are read back from the written file** (`wave`), never echoed from the engine —
  the rule `RenderedClip` established (ADR-0056 §1).

### 3. Word times are an estimate, and are named as one

Kokoro through `kokoro-onnx` reports **no timings** (the PyTorch package does, at the price of
`torch`). So the adapter derives them: find where sound actually starts and ends in the file
(leading and trailing silence are not speech), then share that span among the words in proportion
to their length, with a longer share of silence after a comma or a full stop. The result is
**deterministic, ordered, inside the file, and drift of a few hundred milliseconds at worst** —
good enough to move a highlight from word to word, not good enough to cut on. Anything that needs
true alignment must say so and get it from a vendor that returns it or from whisper.cpp
(Deferred).

This is a deliberate shortcut and the ADR records it as one, because the field name `words` on the
port could be read as measured. Callers highlight a word; they do not cut a clip on it.

### 4. Configuration

Two required variables, no defaults (selection lives in config, `PROJECT.md`):

- `OMEMO_KOKORO_MODEL` — path to the `.onnx` file;
- `OMEMO_KOKORO_VOICES` — path to the `voices-*.bin` file.

`composition.build_speech_synthesizer(environ)` builds it. A missing variable or a missing file
stops the build, **naming** what is missing, so nothing half-configured reaches a Run. There is no
vendor choice to make yet, so there is no presence-based selection; the second implementation
brings it, exactly as ADR-0081 §2 did for video.

The packages are an **optional extra**, `pip install -e ".[tts]"` (`kokoro-onnx`). They are not in
`dependencies`: a machine that never speaks does not install `onnxruntime`.

## Consequences

### Positive

- The department can finally have a voice, at no per-character price and with no account that can
  be revoked — the property the maintainer's situation made decisive.
- A paid vendor later is one new module behind an unchanged port, which is the layering earning its
  keep for the fourth time (ADR-0064, ADR-0084).
- The suite stays hermetic: the engine is a seam, so the logic is tested without a model.

### Negative / Trade-offs

- **The voices are flat.** Kokoro reads; it does not act, and laughter such as the reference videos'
  "ха-ха" will not come out of it. Judged acceptable by the maintainer's listen; a vendor adapter is
  the answer if it stops being.
- English only in v1. The reference videos are Russian; the accounts are American, so English is the
  language that is needed (the maintainer's account arrangement, 2026-10-06).
- Word times are estimates (§3).
- `espeak-ng`, the phonemiser `kokoro-onnx` loads, is GPL-licensed. The weights are Apache-2.0 and
  the audio it produces is not a derivative of the phonemiser, but this ADR does not claim to have
  settled that for a commercial channel; it is flagged for the maintainer, not resolved.

## Deferred

1. **A paid vendor adapter** (ElevenLabs, or BytePlus speech once its price, English voices and API
   are read from the vendor). Whether the maintainer may legitimately use ElevenLabs is their call.
2. **True word alignment** — whisper.cpp over the synthesised file, if the estimate drifts visibly
   in a real render.
3. **Pitch shifting** of a voice (a small character, a heavy boss). It is an ffmpeg step on the
   file, and the maintainer heard it work; it belongs to assembly, not to the port.
4. **Other languages**, once someone has listened to them.
5. **The dialogue assembly step** and the captions that highlight a word — the next two pieces of
   the department, each with its own spec.

## References

- ADR-0066 (port shapes: paths in, measurements out), ADR-0064 (a local tool behind a port),
  ADR-0056 §1 (measure the file, do not echo the request), ADR-0071 (burnt captions),
  ADR-0084 (the vendor concentration risk that decided this)
- Kokoro-82M, Apache-2.0; `kokoro-onnx` 0.4.x with `kokoro-v1.0.int8.onnx` + `voices-v1.0.bin`,
  tried on the maintainer's machine 2026-10-06 (`generation-tests/tts/`)
- ElevenLabs' own list of restricted countries (help centre) and plan terms, read 2026-10-06

## Verification note (2026-10-06)

The adapter was run on the real model once (`int8` weights, a 50/50 blend of `af_heart` and
`af_nova`, a ten-word line) and its word times were compared with whisper.cpp's word-level
timestamps on the same file. Every word's start was within **about 250 ms** of whisper's, most
within 100; the largest gap was the pause after "sir.", which whisper counts into the word and the
estimate counts as silence. That supports §3's "a few hundred milliseconds at worst" for moving a
highlight; it is **one line**, not a benchmark, and the first real render is the next test.
