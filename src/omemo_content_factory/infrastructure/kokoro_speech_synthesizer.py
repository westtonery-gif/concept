"""A real ``SpeechSynthesizer`` over Kokoro-82M, run locally (ADR-0085).

Kokoro is an Apache-2.0 text-to-speech model that runs on the operator's own machine: no account,
no region, no per-character price. Everything that is **ours** lives here — the voice grammar, the
file, its measurements and the word times — and only "text to PCM" is delegated to a small
``SpeechEngine`` seam, so the logic is tested without a model and the core, CI and a machine that
never speaks need neither ``kokoro-onnx`` nor ``numpy``: the default engine imports them on first
use (the ``tts`` extra).

The measurements are read back from the file that was written, never echoed from the engine
(ADR-0056 §1). The word times are an **estimate** (ADR-0085 §3): the model reports none, so the
span where sound actually is gets shared among the words by length. Ordered, non-overlapping and
inside the file are guaranteed; the exact millisecond is not.
"""

from __future__ import annotations

import importlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from omemo_content_factory.adapters.speech_synthesizer import (
    SpeechRequest,
    SpeechSynthesizerError,
    SpokenWord,
    SynthesizedSpeech,
)
from omemo_content_factory.infrastructure.speech_wav import pcm_samples, publish_wav, sound_span

__all__ = [
    "MODEL_VAR",
    "VOICES_VAR",
    "KokoroSettings",
    "KokoroSpeechSynthesizer",
    "SpeechEngine",
    "VoicePart",
    "estimate_word_times",
    "kokoro_settings_from_env",
    "parse_voice",
]

MODEL_VAR = "OMEMO_KOKORO_MODEL"
VOICES_VAR = "OMEMO_KOKORO_VOICES"

_LANGUAGES = {"af": "en-us", "am": "en-us", "bf": "en-gb", "bm": "en-gb"}
"""Voice prefix → language. Only English has been listened to (ADR-0085 §2); any other prefix is a
refusal rather than a silently wrong accent."""

_PAD_MS = 20
"""Sound has soft edges: the speech span is widened by this much on each side."""

_SHORT_PAUSE = frozenset(",;:")
_LONG_PAUSE = frozenset(".!?…")
_SHORT_PAUSE_WEIGHT = 2
_LONG_PAUSE_WEIGHT = 4
"""A pause after a comma is worth two letters of speaking time; after a full stop, four."""

_ALNUM = re.compile(r"[^\W_]", re.UNICODE)
_AUDIO_TAG = re.compile(r"\[[^\[\]]{1,30}\]")


@dataclass(frozen=True, slots=True)
class VoicePart:
    """One voice of a blend, with its share (the shares of a blend sum to 1)."""

    name: str
    weight: float


class SpeechEngine(Protocol):
    """Turns text into mono 16-bit PCM. The one thing delegated to the model."""

    def render(self, text: str, voice: tuple[VoicePart, ...], speed: float, /) -> tuple[bytes, int]:
        """Return little-endian 16-bit mono PCM and its sample rate."""
        ...


@dataclass(frozen=True, slots=True)
class KokoroSettings:
    """Where the model and its voices are."""

    model: Path
    voices: Path


def kokoro_settings_from_env(environ: Mapping[str, str]) -> KokoroSettings:
    """Read the two required paths; missing variables and missing files are named."""
    missing = [name for name in (MODEL_VAR, VOICES_VAR) if not environ.get(name, "").strip()]
    if missing:
        raise SpeechSynthesizerError(
            "speech is not configured; set " + " and ".join(missing) + " (see ADR-0085 §4)"
        )
    model = Path(environ[MODEL_VAR].strip())
    voices = Path(environ[VOICES_VAR].strip())
    for variable, path in ((MODEL_VAR, model), (VOICES_VAR, voices)):
        if not path.is_file():
            raise SpeechSynthesizerError(f"{variable} points at a file that is missing: {path}")
    return KokoroSettings(model=model, voices=voices)


def parse_voice(spec: str) -> tuple[VoicePart, ...]:
    """Read ``name`` or ``name:weight+name:weight`` into a normalised blend.

    Raises ``SpeechSynthesizerError`` for an empty name, a repeated name, a weight that is not a
    positive number, or a voice whose language has not been listened to (ADR-0085 §2).
    """
    parts: list[tuple[str, float]] = []
    for chunk in spec.split("+"):
        name, separator, raw_weight = chunk.strip().partition(":")
        name = name.strip()
        if not name:
            raise SpeechSynthesizerError(f"a voice needs a name, got {spec!r}")
        weight = 1.0
        if separator:
            try:
                weight = float(raw_weight)
            except ValueError:
                raise SpeechSynthesizerError(
                    f"the weight of voice {name!r} must be a number"
                ) from None
        if not weight > 0 or weight == float("inf"):
            raise SpeechSynthesizerError(f"the weight of voice {name!r} must be positive")
        if name[:2] not in _LANGUAGES:
            raise SpeechSynthesizerError(
                f"voice {name!r} is not an English voice; v1 speaks only af_/am_/bf_/bm_ voices"
            )
        parts.append((name, weight))
    names = [name for name, _ in parts]
    if len(set(names)) != len(names):
        raise SpeechSynthesizerError(f"a voice is named twice in {spec!r}")
    total = sum(weight for _, weight in parts)
    return tuple(VoicePart(name, weight / total) for name, weight in parts)


def estimate_word_times(
    text: str, start_ms: int, end_ms: int, *, limit_ms: int
) -> tuple[SpokenWord, ...]:
    """Share ``[start_ms, end_ms]`` among the words of ``text`` in proportion to their length.

    A token with no letter or digit (a lone dash) is not a word and gets no time. Each word's cost
    is its letters plus one; a comma or a full stop after it adds a pause. The result is ordered,
    non-overlapping and never past ``limit_ms`` — an **estimate** (ADR-0085 §3).
    """
    tokens = [token for token in text.split() if _ALNUM.search(token)]
    if not tokens:
        raise SpeechSynthesizerError("the text has no words to speak")
    span = end_ms - start_ms
    if span < 2 * len(tokens):
        raise SpeechSynthesizerError("the audio is too short for the words it should hold")
    costs = [len(_ALNUM.findall(token)) + 1 for token in tokens]
    pauses = [_pause_after(token) for token in tokens]
    pauses[-1] = 0
    total = sum(costs) + sum(pauses)
    words: list[SpokenWord] = []
    cursor = 0.0
    previous_end = 0
    for token, cost, pause in zip(tokens, costs, pauses, strict=True):
        first = max(start_ms + round(span * cursor / total), previous_end)
        last = max(start_ms + round(span * (cursor + cost) / total), first + 1)
        words.append(SpokenWord(token, first, last))
        previous_end = last
        cursor += cost + pause
    if previous_end > limit_ms:
        raise SpeechSynthesizerError("the audio is too short for the words it should hold")
    return tuple(words)


def _pause_after(token: str) -> int:
    stripped = token.rstrip("\"')]}»”’")
    if stripped and stripped[-1] in _LONG_PAUSE:
        return _LONG_PAUSE_WEIGHT
    if stripped and stripped[-1] in _SHORT_PAUSE:
        return _SHORT_PAUSE_WEIGHT
    return 0


class KokoroSpeechSynthesizer:
    """Speaks one line with Kokoro-82M on this machine, or with whatever engine is injected."""

    def __init__(
        self, settings: KokoroSettings | None = None, *, engine: SpeechEngine | None = None
    ) -> None:
        if engine is None:
            if settings is None:
                raise SpeechSynthesizerError("a Kokoro synthesizer needs settings or an engine")
            engine = _KokoroOnnxEngine(settings)
        self._engine = engine

    def synthesize(self, request: SpeechRequest, /) -> SynthesizedSpeech:
        """Write the line to ``request.destination`` and report what was written."""
        voice = parse_voice(request.voice)
        # Kokoro cannot act on ElevenLabs-style audio tags and would read "[laughs]" aloud.
        text = " ".join(_AUDIO_TAG.sub(" ", request.text).split())
        if not text:
            raise SpeechSynthesizerError("the line has no spoken words once audio tags are removed")
        try:
            pcm, rate = self._engine.render(text, voice, request.speed)
        except SpeechSynthesizerError:
            raise
        except Exception as error:  # the engine is outside our code; its failures are one error
            raise SpeechSynthesizerError(f"the speech engine failed: {error}") from error
        samples = pcm_samples(pcm, rate)
        first, last = sound_span(samples)

        def words_for(duration_ms: int) -> tuple[SpokenWord, ...]:
            return estimate_word_times(
                text,
                max(0, round(first * 1000 / rate) - _PAD_MS),
                min(duration_ms, round((last + 1) * 1000 / rate) + _PAD_MS),
                limit_ms=duration_ms,
            )

        return publish_wav(Path(request.destination), pcm, rate, words_for)


class _KokoroOnnxEngine:
    """The default engine: ``kokoro-onnx`` on the CPU, imported only when first used."""

    def __init__(self, settings: KokoroSettings) -> None:
        self._settings = settings
        self._model: Any = None

    def render(self, text: str, voice: tuple[VoicePart, ...], speed: float, /) -> tuple[bytes, int]:
        try:
            numpy = importlib.import_module("numpy")
            kokoro_onnx = importlib.import_module("kokoro_onnx")
        except ImportError as error:
            raise SpeechSynthesizerError(
                "speech needs the 'tts' extra: pip install -e '.[tts]'"
            ) from error
        if self._model is None:
            self._model = kokoro_onnx.Kokoro(str(self._settings.model), str(self._settings.voices))
        known = set(self._model.get_voices())
        for part in voice:
            if part.name not in known:
                raise SpeechSynthesizerError(f"the model has no voice named {part.name!r}")
        style = sum(
            (self._model.get_voice_style(part.name) * part.weight for part in voice[1:]),
            self._model.get_voice_style(voice[0].name) * voice[0].weight,
        )
        audio, rate = self._model.create(
            text, voice=style, speed=speed, lang=_LANGUAGES[voice[0].name[:2]]
        )
        pcm = (numpy.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()
        return bytes(pcm), int(rate)
