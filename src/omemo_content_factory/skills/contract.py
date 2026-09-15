"""The executable Skill contract — one typed, deterministic operation (ADR-0021 §3).

``Skill[InT, OutT]`` is a structural ``Protocol``: anything exposing a ``descriptor`` and an
``apply(input) -> output`` method is a Skill, with no base class to inherit (no premature shared
base, CLAUDE.md conventions). The contract is deliberately tiny:

- ``apply`` is **pure**: same input → equal output; no clock, randomness, I/O, model call or state
  carried between calls. The input is an immutable dataclass validated at construction (a
  violation raises ``InvalidSkillInputError``), the output an immutable dataclass.
- ``apply`` receives no ``Run``, ``Task``, agent or caller identity — a Skill cannot know who
  called it and cannot steer the pipeline (`PROJECT.md` §14).
"""

from __future__ import annotations

from typing import Protocol, TypeVar

from omemo_content_factory.domain.skill import SkillDescriptor

InT = TypeVar("InT", contravariant=True)
OutT = TypeVar("OutT", covariant=True)


class Skill(Protocol[InT, OutT]):
    """A reusable one-task operation: typed input in, typed output out (ADR-0021 §3)."""

    @property
    def descriptor(self) -> SkillDescriptor:
        """The Skill's catalogue entry (identity, version, purpose)."""
        ...

    def apply(self, skill_input: InT, /) -> OutT:
        """Perform the one task deterministically; never mutates the input."""
        ...
