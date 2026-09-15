"""``check_required_elements@v1`` — report which mandatory elements a text contains (ADR-0021 §4).

Each required element (e.g. a health-domain disclaimer, a call to action) is satisfied when **any**
of its phrases occurs in the text — whole-word, case-insensitive, whitespace-tolerant. The Skill
only **reports** present/missing elements; deciding what a missing element means (block, escalate,
rework) belongs to QA and the Content Director, never to the Skill (`PROJECT.md` §14). An empty
requirement set is refused, so "nothing checked" can never read as "complete".
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
from omemo_content_factory.skills._phrase import phrase_pattern

DESCRIPTOR = SkillDescriptor(
    skill_id="check_required_elements",
    version=SkillVersion(1),
    name="Required elements check",
    purpose="Report which required elements (any of their phrases) a text contains or misses.",
)


@dataclass(frozen=True, slots=True)
class RequiredElement:
    """One mandatory element, satisfied by any of its ``phrases``."""

    element_id: str
    phrases: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CheckRequiredElementsInput:
    """The text and a non-empty set of uniquely-identified elements, each with non-blank phrases."""

    text: str
    elements: tuple[RequiredElement, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise InvalidSkillInputError("text must be a str")
        if not self.elements:
            raise InvalidSkillInputError("at least one required element must be given")
        seen: set[str] = set()
        for element in self.elements:
            if not element.element_id.strip():
                raise InvalidSkillInputError("element_id must be non-blank")
            if element.element_id in seen:
                raise InvalidSkillInputError(f"duplicate element_id '{element.element_id}'")
            seen.add(element.element_id)
            if not element.phrases or any(not phrase.strip() for phrase in element.phrases):
                raise InvalidSkillInputError(
                    f"element '{element.element_id}' needs one or more non-blank phrases"
                )


@dataclass(frozen=True, slots=True)
class RequiredElementsReport:
    """Element ids found / not found, each in input order; ``complete`` iff nothing is missing."""

    present: tuple[str, ...]
    missing: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return not self.missing


class CheckRequiredElements:
    """Stateless presence check.

    A ``Skill[CheckRequiredElementsInput, RequiredElementsReport]``.
    """

    __slots__ = ()
    descriptor: ClassVar[SkillDescriptor] = DESCRIPTOR

    def apply(self, skill_input: CheckRequiredElementsInput, /) -> RequiredElementsReport:
        present: list[str] = []
        missing: list[str] = []
        for element in skill_input.elements:
            pattern = "|".join(phrase_pattern(phrase) for phrase in element.phrases)
            found = re.search(pattern, skill_input.text, re.IGNORECASE) is not None
            (present if found else missing).append(element.element_id)
        return RequiredElementsReport(present=tuple(present), missing=tuple(missing))
