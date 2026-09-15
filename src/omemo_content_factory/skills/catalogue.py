"""The Skills catalogue — descriptors of every Skill in the library (ADR-0021 §6).

Passive data only: which Skill versions exist, keyed by their ``ref``. It resolves nothing and
selects nothing at runtime — an agent uses a Skill by importing it (Skill ≠ Tool, `PROJECT.md` §18).
A new Skill is added here as one more entry; refs must stay unique
(``tests/test_skill_contract.py``).
"""

from __future__ import annotations

from omemo_content_factory.domain.skill import SkillDescriptor, SkillRef
from omemo_content_factory.skills import normalize_terminology, required_elements, segment_text

SKILL_DESCRIPTORS: tuple[SkillDescriptor, ...] = (
    segment_text.DESCRIPTOR,
    normalize_terminology.DESCRIPTOR,
    required_elements.DESCRIPTOR,
)

SKILLS_BY_REF: dict[SkillRef, SkillDescriptor] = {d.ref: d for d in SKILL_DESCRIPTORS}
