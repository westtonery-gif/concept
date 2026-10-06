"""A real ``SpeechSynthesizer`` over ElevenLabs, through stdlib ``urllib`` (ADR-0086).

One ``POST /v1/text-to-speech/{voice_id}/with-timestamps?output_format=pcm_24000`` per line. The
answer carries the audio (raw 24 kHz 16-bit PCM, base64) and the **character-level alignment** of
the text, which is what gives this adapter true word times — unlike Kokoro's estimate (ADR-0085
§3). Nothing from the vendor reaches the core: ``voice`` on the request is the vendor's voice id,
and the file is written and measured the same way for every speech adapter (``speech_wav``).

``eleven_v3`` reads **audio tags** inside the text — ``[laughs]``, ``[whispers]``, ``[sighs]`` —
which is where the expressiveness the Kokoro voices lack comes from. A tag is not spoken, so it is
left out of the word times: captions must never light up ``[laughs]``.

The contract was read from the vendor's official Python SDK on GitHub (their docs site does not
answer from every region), and is confirmed against the real service only by the opt-in live test.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from omemo_content_factory.adapters.speech_synthesizer import (
    SpeechRequest,
    SpeechSynthesizerError,
    SpokenWord,
    SynthesizedSpeech,
)
from omemo_content_factory.infrastructure.speech_wav import pcm_samples, publish_wav, sound_span

__all__ = [
    "API_KEY_VAR",
    "MODEL_VAR",
    "STABILITY_VAR",
    "ElevenLabsSettings",
    "ElevenLabsSpeechSynthesizer",
    "elevenlabs_settings_from_env",
    "words_from_alignment",
]

API_KEY_VAR = "OMEMO_ELEVENLABS_API_KEY"
MODEL_VAR = "OMEMO_ELEVENLABS_MODEL"
STABILITY_VAR = "OMEMO_ELEVENLABS_STABILITY"

DEFAULT_API_URL = "https://api.elevenlabs.io"
_OUTPUT_FORMAT = "pcm_24000"
_SAMPLE_RATE = 24_000
_ALNUM = re.compile(r"[^\W_]", re.UNICODE)


@dataclass(frozen=True, slots=True)
class ElevenLabsSettings:
    """The key and the model are required; ``stability`` is sent only when it is set."""

    api_key: str = field(repr=False)
    model: str
    stability: float | None = None

    def __post_init__(self) -> None:
        for name in ("api_key", "model"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"ElevenLabs settings need a non-blank {name}")
        if self.stability is not None and not 0.0 <= self.stability <= 1.0:
            raise ValueError("ElevenLabs stability must be between 0 and 1")


def elevenlabs_settings_from_env(environ: Mapping[str, str]) -> ElevenLabsSettings:
    """Read the two required variables and the optional stability; a missing one is named."""
    values = {name: environ.get(name, "").strip() for name in (API_KEY_VAR, MODEL_VAR)}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise SpeechSynthesizerError("ElevenLabs is not configured; set " + ", ".join(missing))
    raw = environ.get(STABILITY_VAR, "").strip()
    try:
        stability = float(raw) if raw else None
        return ElevenLabsSettings(values[API_KEY_VAR], values[MODEL_VAR], stability)
    except ValueError:
        raise SpeechSynthesizerError(f"{STABILITY_VAR} must be a number from 0 to 1") from None


class ElevenLabsSpeechSynthesizer:
    """A ``SpeechSynthesizer`` that asks ElevenLabs to speak the line."""

    def __init__(
        self,
        settings: ElevenLabsSettings,
        *,
        api_url: str = DEFAULT_API_URL,
        timeout: float = 120.0,
    ) -> None:
        self._settings = settings
        self._api_url = api_url.rstrip("/")
        self._timeout = timeout

    def synthesize(self, request: SpeechRequest, /) -> SynthesizedSpeech:
        """Write the line to ``request.destination`` and report what was written."""
        answer = self._post(request)
        pcm = _audio(answer)
        rate = _SAMPLE_RATE
        sound_span(pcm_samples(pcm, rate))  # silence is an error, as for every adapter
        alignment = _alignment(answer)

        def words_for(duration_ms: int) -> tuple[SpokenWord, ...]:
            return words_from_alignment(
                alignment["characters"],
                alignment["character_start_times_seconds"],
                alignment["character_end_times_seconds"],
                limit_ms=duration_ms,
            )

        return publish_wav(Path(request.destination), pcm, rate, words_for)

    def _post(self, request: SpeechRequest) -> Mapping[str, Any]:
        body: dict[str, Any] = {"text": request.text, "model_id": self._settings.model}
        voice_settings: dict[str, float] = {}
        if self._settings.stability is not None:
            voice_settings["stability"] = self._settings.stability
        if request.speed != 1.0:
            voice_settings["speed"] = float(request.speed)
        if voice_settings:
            body["voice_settings"] = voice_settings
        voice = urllib.parse.quote(request.voice.strip(), safe="")
        url = f"{self._api_url}/v1/text-to-speech/{voice}/with-timestamps"
        http_request = urllib.request.Request(
            f"{url}?output_format={_OUTPUT_FORMAT}",
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={
                "xi-api-key": self._settings.api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(http_request, timeout=self._timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise SpeechSynthesizerError(
                f"ElevenLabs refused the request: HTTP {exc.code}{_detail(exc)}"
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise SpeechSynthesizerError(
                f"ElevenLabs could not be reached: {_reason(exc)}"
            ) from None
        try:
            answer = json.loads(raw)
        except ValueError:
            raise SpeechSynthesizerError(
                "ElevenLabs answered with something that is not JSON"
            ) from None
        if not isinstance(answer, dict):
            raise SpeechSynthesizerError("ElevenLabs answered with an unexpected shape")
        return answer


def words_from_alignment(
    characters: Sequence[str],
    starts: Sequence[float],
    ends: Sequence[float],
    *,
    limit_ms: int,
) -> tuple[SpokenWord, ...]:
    """Group the vendor's per-character times into the words of the text.

    Whitespace splits words, and anything inside square brackets (an audio tag such as
    ``[laughs]``) is not spoken, so it gets no word. A token with no letter or digit is not a word.
    The times are the vendor's own, only rounded to milliseconds, ordered and clamped into the file.
    """
    if not len(characters) == len(starts) == len(ends):
        raise SpeechSynthesizerError("ElevenLabs sent an alignment whose lists differ in length")
    words: list[SpokenWord] = []
    previous_end = 0
    for text, begin, finish in _tokens(characters, starts, ends):
        start = max(round(begin * 1000), previous_end)
        end = max(min(round(finish * 1000), limit_ms), start + 1)
        if end > limit_ms:
            continue
        words.append(SpokenWord(text, start, end))
        previous_end = end
    if not words:
        raise SpeechSynthesizerError("ElevenLabs sent no spoken words for the line")
    return tuple(words)


def _tokens(
    characters: Sequence[str], starts: Sequence[float], ends: Sequence[float]
) -> Iterator[tuple[str, float, float]]:
    """Spoken tokens as (text, first start, last end) in seconds; tags and dashes are dropped."""
    letters: list[str] = []
    begin = finish = 0.0
    depth = 0
    for character, start, end in zip(characters, starts, ends, strict=True):
        in_tag = depth > 0
        if character == "[":
            depth += 1
        elif character == "]" and in_tag:
            depth -= 1
        elif not in_tag and character and not character.isspace():
            if not letters:
                begin = float(start)
            finish = float(end)
            letters.append(character)
            continue
        # a space, or either edge of a tag, ends the word being built
        text = "".join(letters)
        letters.clear()
        if text and _ALNUM.search(text):
            yield text, begin, finish
    text = "".join(letters)
    if text and _ALNUM.search(text):
        yield text, begin, finish


def _audio(answer: Mapping[str, Any]) -> bytes:
    encoded = answer.get("audio_base64")
    if not isinstance(encoded, str) or not encoded:
        raise SpeechSynthesizerError("ElevenLabs answered without audio")
    try:
        return base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise SpeechSynthesizerError("ElevenLabs sent audio that is not valid base64") from None


def _alignment(answer: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("alignment", "normalized_alignment"):
        candidate = answer.get(key)
        if isinstance(candidate, dict) and all(
            isinstance(candidate.get(name), list)
            for name in (
                "characters",
                "character_start_times_seconds",
                "character_end_times_seconds",
            )
        ):
            return candidate
    raise SpeechSynthesizerError("ElevenLabs answered without a character alignment")


def _detail(error: urllib.error.HTTPError) -> str:
    try:
        detail = json.loads(error.read())["detail"]
    except (ValueError, KeyError, TypeError, OSError):
        return ""
    message = detail.get("message") if isinstance(detail, dict) else detail
    return f" ({str(message)[:300]})" if message else ""


def _reason(exc: BaseException) -> str:
    reason = getattr(exc, "reason", None)
    return str(reason if reason is not None else exc.__class__.__name__)
