"""``content_researcher@v1`` (Rin) — the content-research role definition.

The concrete, static assets of one production role, owned by the factory (migrated from Main Core
by Pattern Application over Slice A / ADR-0016 — same shape as ``script_writer``): the
:class:`Agent` descriptor, its versioned :class:`Prompt` (System + User template + ``schema_ref``;
PROJECT.md §15, ADR-0011) and the ``content-research`` :class:`Schema` its Output conforms to
(ADR-0008). They are **passive data** in existing domain types — no new entity, no execution
logic — assembled into an executor by the Composition Root, never here.

Scope note: the Schema is **intentionally minimal** — only what the ownership migration needs, not
the final content-research document model. It fixes no future research structure; expanding it is a
separate, later concern outside this Pattern-Application slice.

The module exposes the three catalogues keyed as the Composition Root expects
(``agent.prompt_ref -> Prompt``, ``prompt.schema_ref -> Schema``) so a caller wires Rin without
restating any ref string, exactly like ``script_writer``.
"""

from __future__ import annotations

from omemo_content_factory.agents.skill_invocations import NormalizeTerminologyInvocation
from omemo_content_factory.domain.agent import Agent
from omemo_content_factory.domain.prompt import Prompt, PromptId, PromptVersion
from omemo_content_factory.domain.schema import Schema, SchemaStatus, SchemaVersion
from omemo_content_factory.skills.normalize_terminology import TermMapping

# --- reference strings (opaque handles used across the Composition Root) ------------------

AGENT_REF = "content_researcher@v1"
"""The ``agent_ref`` selecting this role's executor (unchanged from Main Core)."""

PROMPT_REF: PromptId = "content-researcher"
"""The logical Prompt id this Agent binds to (``Agent.prompt_ref -> Prompt.prompt_id``)."""

SCHEMA_REF = "content-research-report"
"""The opaque ``schema_ref`` of the produced Output — kept stable from Main Core across the
migration (same handle Main Core already used), so no downstream ref-consumer resynchronises."""


# --- Schema: the output contract (audience / angle), ACTIVE ------------------------------
# Intentionally minimal (migration scope only) — NOT the final research-document model.

CONTENT_RESEARCH_SCHEMA = Schema.create(
    schema_id="content-research-report",
    version=SchemaVersion(1),
    description="Minimal content-research contract: target audience and content angle.",
    required_fields=("audience", "angle"),
)
CONTENT_RESEARCH_SCHEMA.transition(SchemaStatus.ACTIVE)


# --- Prompt: role behaviour (System + User template) -------------------------------------

CONTENT_RESEARCHER_PROMPT = Prompt(
    prompt_id=PROMPT_REF,
    version=PromptVersion(1),
    system=(
        "Ты Content Researcher видео-фабрики OMEMO. По присланному брифу кратко определи "
        "целевую аудиторию (audience) и контентный угол подачи (angle). Минимально, по делу, "
        "без воды."
    ),
    user_template="Бриф и контекст:\n{input}\n\nОпредели: audience, angle.",
    schema_ref=SCHEMA_REF,
)


# --- Agent: the role descriptor (agent_ref -> prompt_ref) --------------------------------

CONTENT_RESEARCHER_SKILL_INVOCATIONS = (
    NormalizeTerminologyInvocation(glossary=(TermMapping(variant="омемо", canonical="OMEMO"),)),
)
"""Ordered, caller-configured Skill invocations applied before Rin's model call (ADR-0027)."""

CONTENT_RESEARCHER_AGENT = Agent(
    agent_id=AGENT_REF,
    name="Rin — Content Researcher",
    prompt_ref=PROMPT_REF,
    description="Turns a brief into minimal content research (target audience, content angle).",
    skill_refs=tuple(invocation.skill_ref for invocation in CONTENT_RESEARCHER_SKILL_INVOCATIONS),
)


# --- catalogues keyed for the Composition Root -------------------------------------------

AGENTS: tuple[Agent, ...] = (CONTENT_RESEARCHER_AGENT,)
PROMPTS: dict[PromptId, Prompt] = {CONTENT_RESEARCHER_PROMPT.prompt_id: CONTENT_RESEARCHER_PROMPT}
SCHEMAS: dict[str, Schema] = {SCHEMA_REF: CONTENT_RESEARCH_SCHEMA}
SKILL_INVOCATIONS = {AGENT_REF: CONTENT_RESEARCHER_SKILL_INVOCATIONS}
"""Static ``agent_ref -> invocations`` data consumed only by the Composition Root."""
