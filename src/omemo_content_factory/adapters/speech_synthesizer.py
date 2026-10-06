"""Turning one line of text into a spoken audio file (ADR-0085, GENERATION_SPEC §9).

The engine lives in ``infrastructure/`` and never reaches this contract — the port is named by its
role, like every other outside system here (ADR-0023). Text and a destination in, a path and **the
adapter's own measurements** out (ADR-0056 §1): whatever lays the line into a video reads numbers
taken from the written file, not the caller's expectations.

The port speaks **one utterance**. A dialogue is several requests; joining them, the gaps between
them and mixing under music belong to the assembly step. The call is synchronous and repeatable —
repeating it overwrites the same destination and corrupts nothing — and it runs in a deterministic
Workflow step, never inside a model's Tool budget (ADR-0054 §2).

``words`` carries each spoken word with its start and end in milliseconds, because captions that
highlight the word being said need them. How exact those times are is the adapter's to state: an
engine that reports none may estimate them (ADR-0085 §3), and what is promised here is only that
they are ordered, do not overlap and lie inside the file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


def _blank(value: object) -> bool:
    return not isinstance(value, str) or not value.strip()


@dataclass(frozen=True, slots=True)
class SpeechRequest:
    """One line to speak: what, in which voice, written where.

    ``voice`` is an opaque string — a vendor's voice id or a local voice name; what it means is
    its adapter's business.
    """

    text: str
    voice: str
    destination: str
    speed: float = 1.0

    def __post_init__(self) -> None:
        for name in ("text", "voice", "destination"):
            if _blank(getattr(self, name)):
                raise ValueError(f"a speech request needs a non-blank {name}")
        speed = self.speed
        if isinstance(speed, bool) or not isinstance(speed, int | float) or not speed > 0:
            raise ValueError("a speech request needs a positive speed")


@dataclass(frozen=True, slots=True)
class SpokenWord:
    """One word as written in the text (punctuation attached), with when it is said."""

    text: str
    start_ms: int
    end_ms: int

    def __post_init__(self) -> None:
        if _blank(self.text):
            raise ValueError("a spoken word needs non-blank text")
        for name in ("start_ms", "end_ms"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"a spoken word needs a whole {name}")
        if self.start_ms < 0 or self.end_ms <= self.start_ms:
            raise ValueError("a spoken word needs 0 <= start_ms < end_ms")


@dataclass(frozen=True, slots=True)
class SynthesizedSpeech:
    """The file that was written, measured, with the words in it."""

    path: str
    duration_ms: int
    sample_rate: int
    words: tuple[SpokenWord, ...]

    def __post_init__(self) -> None:
        if _blank(self.path):
            raise ValueError("synthesized speech needs a non-blank path")
        for name in ("duration_ms", "sample_rate"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"synthesized speech needs a positive {name}")
        if not self.words:
            raise ValueError("synthesized speech needs at least one word")
        previous_end = 0
        for word in self.words:
            if word.start_ms < previous_end:
                raise ValueError("spoken words must be in order and must not overlap")
            previous_end = word.end_ms
        if previous_end > self.duration_ms:
            raise ValueError("spoken words must lie inside the file")


class SpeechSynthesizerError(Exception):
    """The line could not be spoken (technical failure, not a ``DomainError``)."""


class SpeechSynthesizer(Protocol):
    """Turns one line of text into one audio file."""

    def synthesize(self, request: SpeechRequest, /) -> SynthesizedSpeech:
        """Write the audio to ``request.destination`` and report what was written."""
        ...
