"""What every speech adapter does with the audio it gets, whoever made it (ADR-0085).

Check that PCM is real sound, write it as a mono 16-bit WAV through a temporary name, read the
duration **back from the file** (ADR-0056 §1), ask the caller for the word times of a file of that
length, and only then move the file into place — so a crash never leaves half a file and never
destroys a previous one.
"""

from __future__ import annotations

import os
import sys
import tempfile
import wave
from array import array
from collections.abc import Callable
from pathlib import Path

from omemo_content_factory.adapters.speech_synthesizer import (
    SpeechSynthesizerError,
    SpokenWord,
    SynthesizedSpeech,
)

__all__ = ["pcm_samples", "publish_wav", "sound_span"]

_SILENCE_FLOOR = 0.02
"""A sample counts as sound from this fraction of the file's peak."""
_MIN_PEAK = 50
"""A file whose loudest sample is quieter than this (of 32767) is silence, not speech."""


def pcm_samples(pcm: bytes, rate: int) -> array[int]:
    """Decode little-endian 16-bit PCM; empty or malformed audio is an error, not an empty file."""
    if isinstance(rate, bool) or not isinstance(rate, int) or rate <= 0:
        raise SpeechSynthesizerError("the speech engine reported no sample rate")
    if not pcm or len(pcm) % 2:
        raise SpeechSynthesizerError("the speech engine returned no usable audio")
    samples: array[int] = array("h")
    samples.frombytes(pcm)
    if sys.byteorder == "big":
        samples.byteswap()
    return samples


def sound_span(samples: array[int]) -> tuple[int, int]:
    """First and last sample that carry speech; silence is an error."""
    peak = max(max(samples), -min(samples))
    if peak < _MIN_PEAK:
        raise SpeechSynthesizerError("the speech engine returned silence")
    floor = max(round(peak * _SILENCE_FLOOR), 1)
    first = next(index for index, value in enumerate(samples) if abs(value) >= floor)
    last = next(index for index in range(len(samples) - 1, -1, -1) if abs(samples[index]) >= floor)
    return first, last


def publish_wav(
    destination: Path,
    pcm: bytes,
    rate: int,
    words_for: Callable[[int], tuple[SpokenWord, ...]],
) -> SynthesizedSpeech:
    """Write ``pcm`` to ``destination`` and return what was written, measured from the file."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp"
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with wave.open(str(temporary), "wb") as target:
            target.setnchannels(1)
            target.setsampwidth(2)
            target.setframerate(rate)
            target.writeframes(pcm)
        duration_ms, measured_rate = _measure(temporary)
        words = words_for(duration_ms)
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return SynthesizedSpeech(
        path=str(destination), duration_ms=duration_ms, sample_rate=measured_rate, words=words
    )


def _measure(path: Path) -> tuple[int, int]:
    try:
        with wave.open(str(path), "rb") as source:
            rate = source.getframerate()
            frames = source.getnframes()
    except (wave.Error, EOFError) as error:
        raise SpeechSynthesizerError("the audio that was written cannot be read back") from error
    duration_ms = round(frames * 1000 / rate)
    if duration_ms <= 0:
        raise SpeechSynthesizerError("the audio that was written is empty")
    return duration_ms, rate
