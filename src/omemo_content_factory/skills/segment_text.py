"""``segment_text@v1`` — split a long text into bounded, meaningful blocks (ADR-0021 §4).

Paragraphs (separated by a blank line) are never merged; inside a paragraph whole sentences are
packed greedily into segments of at most ``max_chars``. A sentence longer than the limit is packed
word by word, and a single word longer than the limit is cut into ``max_chars`` pieces — so the
limit always holds. Whitespace is normalised to single spaces; every non-whitespace character is
kept, in order.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import ClassVar

from omemo_content_factory.domain.skill import (
    InvalidSkillInputError,
    SkillDescriptor,
    SkillVersion,
)

DESCRIPTOR = SkillDescriptor(
    skill_id="segment_text",
    version=SkillVersion(1),
    name="Text segmentation",
    purpose="Split a text into paragraph-respecting segments of at most max_chars characters.",
)

_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


@dataclass(frozen=True, slots=True)
class SegmentTextInput:
    """The text to segment and the per-segment character limit (``max_chars >= 1``)."""

    text: str
    max_chars: int

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise InvalidSkillInputError("text must be a str")
        if isinstance(self.max_chars, bool) or not isinstance(self.max_chars, int):
            raise InvalidSkillInputError("max_chars must be an int")
        if self.max_chars < 1:
            raise InvalidSkillInputError(f"max_chars must be >= 1, got {self.max_chars}")


@dataclass(frozen=True, slots=True)
class SegmentTextOutput:
    """Non-empty, whitespace-trimmed segments in text order (empty for a blank text)."""

    segments: tuple[str, ...]


class SegmentText:
    """Stateless text segmentation — a ``Skill[SegmentTextInput, SegmentTextOutput]``."""

    __slots__ = ()
    descriptor: ClassVar[SkillDescriptor] = DESCRIPTOR

    def apply(self, skill_input: SegmentTextInput, /) -> SegmentTextOutput:
        limit = skill_input.max_chars
        segments: list[str] = []
        for paragraph in _PARAGRAPH_BREAK.split(skill_input.text):
            sentences = (" ".join(s.split()) for s in _SENTENCE_END.split(paragraph))
            segments.extend(_pack((s for s in sentences if s), limit))
        return SegmentTextOutput(segments=tuple(segments))


def _pack(units: Iterable[str], limit: int) -> list[str]:
    """Greedily join ``units`` with single spaces into chunks of at most ``limit`` characters."""
    packed: list[str] = []
    current = ""
    for unit in units:
        if len(unit) > limit:
            if current:
                packed.append(current)
                current = ""
            packed.extend(_pack(_pieces(unit, limit), limit))
            continue
        candidate = f"{current} {unit}" if current else unit
        if len(candidate) <= limit:
            current = candidate
        else:
            packed.append(current)
            current = unit
    if current:
        packed.append(current)
    return packed


def _pieces(unit: str, limit: int) -> list[str]:
    """Break an over-long unit into its words, or a single word into ``limit``-sized cuts."""
    words = unit.split(" ")
    if len(words) > 1:
        return words
    return [unit[i : i + limit] for i in range(0, len(unit), limit)]
