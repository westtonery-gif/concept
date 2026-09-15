"""Skill domain model — the immutable catalogue descriptor of a reusable one-task module.

A Skill is a standalone root of the definitions catalogue (`DOMAIN_MODEL.md` §2.6, §9.5; ADR-0021):
a reusable, deterministic operation that agents compose. This module holds only its **passive
descriptor** — identity, version, name and purpose — exactly as ``domain/agent.py`` holds the Agent
descriptor while the role assets live in ``agents/``. The executable contract (``Skill``) and the
concrete Skills live in the ``skills`` library layer, never here.

A Skill references no agent and touches no ``Run`` (`DOMAIN_MODEL.md` §6), so nothing here knows
Run, Task, Agent or Workflow. The descriptor is validated at construction and immutable: a revised
Skill is a new version, never a mutation. Only stdlib and ``domain.errors`` are used.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from omemo_content_factory.domain.errors import DomainError

SkillId: TypeAlias = str
"""Opaque, stable identifier of the logical Skill (shared across its versions; ADR-0021 §2)."""

SkillRef: TypeAlias = str
"""Opaque handle of one Skill version, ``<skill_id>@v<n>`` — what a consumer references."""


# --- Domain errors -----------------------------------------------------------------------
# Skill contract violations (ADR-0021 §5). Rooted at the shared ``DomainError`` (ADR-0017).


class SkillDomainError(DomainError):
    """Base class for all Skill contract violations."""


class InvalidSkillDescriptorError(SkillDomainError):
    """A Skill descriptor was built with a blank/ill-formed id, name, purpose or version."""


class InvalidSkillInputError(SkillDomainError):
    """A Skill's typed input violated that Skill's declared input contract (ADR-0021 §3)."""


# --- Value Object + descriptor -----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SkillVersion:
    """A version of a Skill — a Value Object compared by value (`DOMAIN_MODEL.md` §11)."""

    value: int

    def __post_init__(self) -> None:
        if isinstance(self.value, bool) or not isinstance(self.value, int) or self.value < 1:
            raise InvalidSkillDescriptorError(
                f"a Skill version must be an int >= 1, got {self.value!r}"
            )


@dataclass(frozen=True, slots=True)
class SkillDescriptor:
    """The immutable catalogue entry of one Skill version (ADR-0021 §2; SKILL_SPEC §2).

    ``purpose`` states the **one** task the Skill performs (`PROJECT.md` §14 "Skill = одна
    задача"). The input/output contract is the Skill's typed ``apply`` signature, not a field here.
    """

    skill_id: SkillId
    version: SkillVersion
    name: str
    purpose: str

    def __post_init__(self) -> None:
        if not self.skill_id.strip() or any(c.isspace() or c == "@" for c in self.skill_id):
            raise InvalidSkillDescriptorError(
                f"skill_id must be non-blank, without whitespace or '@', got {self.skill_id!r}"
            )
        if not self.name.strip():
            raise InvalidSkillDescriptorError(f"Skill '{self.skill_id}' needs a non-blank name")
        if not self.purpose.strip():
            raise InvalidSkillDescriptorError(f"Skill '{self.skill_id}' needs a non-blank purpose")

    @property
    def ref(self) -> SkillRef:
        """The version handle ``<skill_id>@v<n>`` (same shape as an ``agent_ref``)."""
        return f"{self.skill_id}@v{self.version.value}"
