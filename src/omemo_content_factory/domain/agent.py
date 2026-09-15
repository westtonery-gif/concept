"""Agent domain model — an immutable role descriptor (a passive catalogue entry).

An Agent links an `agent_ref` (its ``agent_id``) to a `prompt_ref` (a Prompt's id) and to passive
Skill/Tool references (`DOMAIN_MODEL.md` §2.5, §9.3; `ADR-0010`, `ADR-0011`, `ADR-0022`,
`ADR-0027`). It is **pure, immutable data**: it does not execute, orchestrate, decide, or influence
Workflow ordering, and it holds no executable semantics. Resolution of `agent_ref` and its runtime
collaborators happens only in the composition root (`ADR-0012`, `ADR-0013`), never inside the Agent.
Only stdlib is used.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from omemo_content_factory.domain.prompt import PromptId
from omemo_content_factory.domain.skill import SkillRef
from omemo_content_factory.domain.tool import ToolRef

AgentId: TypeAlias = str
"""Opaque, stable identifier of the role; the target of an `agent_ref` (`ADR-0011`)."""


@dataclass(frozen=True, slots=True)
class Agent:
    """The immutable Agent descriptor (`ADR-0010`, `ADR-0011`; AGENT_SPEC §1, §2).

    ``prompt_ref`` references the role's active Prompt (active binding 1:1). ``description`` is
    optional, non-execution metadata only. ``skill_refs`` is the ordered list of deterministic
    Skills composed around this role's executor (`ADR-0027`); ``tool_refs`` is the role's Tool
    grant — the only Tools its model may call (`ADR-0022`). Both are empty by default. Like
    ``prompt_ref`` they are plain data, read only where their runtime collaborators are wired.
    """

    agent_id: AgentId
    name: str
    prompt_ref: PromptId
    description: str = ""
    tool_refs: tuple[ToolRef, ...] = ()
    skill_refs: tuple[SkillRef, ...] = ()
