"""Typed adapters through which agent/system code invokes deterministic Skills (ADR-0027).

These adapters supply caller-owned configuration and bridge the string Task-input boundary to a
concrete Skill's typed contract. They do not know Task, Run, orchestration or model execution.
"""

from __future__ import annotations

from dataclasses import dataclass

from omemo_content_factory.domain.skill import SkillRef
from omemo_content_factory.skills.normalize_terminology import (
    DESCRIPTOR as NORMALIZE_TERMINOLOGY_DESCRIPTOR,
)
from omemo_content_factory.skills.normalize_terminology import (
    NormalizeTerminology,
    NormalizeTerminologyInput,
    TermMapping,
)


@dataclass(frozen=True, slots=True)
class NormalizeTerminologyInvocation:
    """Apply ``normalize_terminology@v1`` to a Task input with a caller-owned glossary."""

    glossary: tuple[TermMapping, ...]

    def __post_init__(self) -> None:
        # Validate the static glossary when the role configuration is built, before any Run starts.
        NormalizeTerminologyInput(text="", glossary=self.glossary)

    @property
    def skill_ref(self) -> SkillRef:
        """The concrete Skill version invoked by this adapter."""
        return NORMALIZE_TERMINOLOGY_DESCRIPTOR.ref

    def apply_to(self, task_input: str, /) -> str:
        """Return the Skill's canonicalised text; its replacement audit is not persisted yet."""
        result = NormalizeTerminology().apply(
            NormalizeTerminologyInput(text=task_input, glossary=self.glossary)
        )
        return result.text
