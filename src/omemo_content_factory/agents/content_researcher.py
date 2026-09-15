"""``content_researcher@v1`` (Rin) — the content-research role definition.

The concrete, static assets of one production role, owned by the factory (migrated from Main Core
by Pattern Application over Slice A / ADR-0016 — same shape as ``script_writer``): the
:class:`Agent` descriptor and the ``content-research`` :class:`Schema` its Output conforms to
(ADR-0008). The versioned Prompt text lives in the external package store and is materialized by
the Composition Root (ADR-0030), never by this role module.

Scope note: the Schema is **intentionally minimal** — only what the ownership migration needs, not
the final content-research document model. It fixes no future research structure; expanding it is a
separate, later concern outside this Pattern-Application slice.

The module exposes the Agent/Schema catalogues keyed as the Composition Root expects. Rin is
granted ``current_date@v1`` so
the model can resolve relative dates during reasoning; the Composition Root supplies its clock
(``ADR-0028``).
"""

from __future__ import annotations

from omemo_content_factory.agents.skill_invocations import NormalizeTerminologyInvocation
from omemo_content_factory.domain.agent import Agent
from omemo_content_factory.domain.schema import Schema, SchemaStatus, SchemaVersion
from omemo_content_factory.skills.normalize_terminology import TermMapping
from omemo_content_factory.tools.current_date import DESCRIPTOR as CURRENT_DATE_DESCRIPTOR

# --- reference strings (opaque handles used across the Composition Root) ------------------

AGENT_REF = "content_researcher@v1"
"""The ``agent_ref`` selecting this role's executor (unchanged from Main Core)."""

PROMPT_REF = "content-researcher"
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
    tool_refs=(CURRENT_DATE_DESCRIPTOR.ref,),
)


# --- catalogues keyed for the Composition Root -------------------------------------------

AGENTS: tuple[Agent, ...] = (CONTENT_RESEARCHER_AGENT,)
SCHEMAS: dict[str, Schema] = {SCHEMA_REF: CONTENT_RESEARCH_SCHEMA}
SKILL_INVOCATIONS = {AGENT_REF: CONTENT_RESEARCHER_SKILL_INVOCATIONS}
"""Static ``agent_ref -> invocations`` data consumed only by the Composition Root."""
