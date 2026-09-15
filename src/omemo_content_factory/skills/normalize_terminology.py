"""``normalize_terminology@v1`` — rewrite term variants to their canonical form (ADR-0021 §4).

The caller supplies the glossary (variant → canonical); the Skill hardcodes no vocabulary, so a
medical or brand glossary lives in the caller's configuration. Matching is whole-word,
case-insensitive and whitespace-tolerant; all variants are replaced in **one pass** (longest variant
first), so a canonical form is never itself re-replaced. The report lists what actually changed, for
audit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar

from omemo_content_factory.domain.skill import (
    InvalidSkillInputError,
    SkillDescriptor,
    SkillVersion,
)
from omemo_content_factory.skills._phrase import phrase_key, phrase_pattern

DESCRIPTOR = SkillDescriptor(
    skill_id="normalize_terminology",
    version=SkillVersion(1),
    name="Terminology normalization",
    purpose="Replace glossary term variants in a text with their canonical terms.",
)


@dataclass(frozen=True, slots=True)
class TermMapping:
    """One glossary entry: a ``variant`` spelling and the ``canonical`` term it becomes."""

    variant: str
    canonical: str


@dataclass(frozen=True, slots=True)
class NormalizeTerminologyInput:
    """The text and its glossary; variants must be non-blank and unique (case/space-insensitive)."""

    text: str
    glossary: tuple[TermMapping, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise InvalidSkillInputError("text must be a str")
        seen: set[str] = set()
        for entry in self.glossary:
            if not entry.variant.strip() or not entry.canonical.strip():
                raise InvalidSkillInputError(f"blank glossary entry {entry!r}")
            key = phrase_key(entry.variant)
            if key in seen:
                raise InvalidSkillInputError(f"duplicate glossary variant '{entry.variant}'")
            seen.add(key)


@dataclass(frozen=True, slots=True)
class TermReplacement:
    """How many times one glossary ``variant`` was rewritten to ``canonical``."""

    variant: str
    canonical: str
    occurrences: int


@dataclass(frozen=True, slots=True)
class NormalizedText:
    """The normalised text and the non-zero replacements, in glossary order."""

    text: str
    replacements: tuple[TermReplacement, ...]


class NormalizeTerminology:
    """Stateless glossary normalisation — a ``Skill[NormalizeTerminologyInput, NormalizedText]``."""

    __slots__ = ()
    descriptor: ClassVar[SkillDescriptor] = DESCRIPTOR

    def apply(self, skill_input: NormalizeTerminologyInput, /) -> NormalizedText:
        glossary = skill_input.glossary
        if not glossary:
            return NormalizedText(text=skill_input.text, replacements=())
        # One named group per entry, longest variant first so "витамин D3" wins over "витамин D".
        order = sorted(range(len(glossary)), key=lambda i: -len(phrase_key(glossary[i].variant)))
        pattern = re.compile(
            "|".join(f"(?P<t{i}>{phrase_pattern(glossary[i].variant)})" for i in order),
            re.IGNORECASE,
        )
        counts = [0] * len(glossary)

        def _replace(match: re.Match[str]) -> str:
            index = int((match.lastgroup or "t0")[1:])
            canonical = glossary[index].canonical
            if match.group() != canonical:
                counts[index] += 1
            return canonical

        text = pattern.sub(_replace, skill_input.text)
        replacements = tuple(
            TermReplacement(variant=entry.variant, canonical=entry.canonical, occurrences=count)
            for entry, count in zip(glossary, counts, strict=True)
            if count
        )
        return NormalizedText(text=text, replacements=replacements)
