"""``qa_agent@v1`` — the QA (quality and compliance) role definition (ADR-0035; v2 Prompt criteria
per CLAUDE.md queue task 12).

The static assets of the Stage 8 QA role, in the same shape as ``script_writer``: the
:class:`Agent` descriptor and the ``qa-verdict`` :class:`Schema` whose ``required_fields`` are the
ADR-0034 verdict fields, taken from :data:`QA_VERDICT_FIELDS` so the contract has one source. The
versioned Prompt text lives in the external package store and is materialized by the Composition
Root (ADR-0030), never by this role module.

The role answers with a verdict, not an Output: it is meant to sit behind the ``ArtifactEvaluator``
port (ADR-0018 §6), never as a Workflow step. It is granted no Skills and no Tools (ADR-0035 §4).
"""

from __future__ import annotations

from omemo_content_factory.application.qa_evaluation import QA_VERDICT_FIELDS
from omemo_content_factory.domain.agent import Agent
from omemo_content_factory.domain.schema import Schema, SchemaStatus, SchemaVersion

# --- reference strings (opaque handles used across the Composition Root) ------------------

AGENT_REF = "qa_agent@v1"
"""The ``agent_ref`` identifying this role."""

PROMPT_REF = "qa-agent"
"""The logical Prompt id this Agent binds to (``Agent.prompt_ref -> Prompt.prompt_id``)."""

SCHEMA_REF = "qa-verdict@v1"
"""The opaque ``schema_ref`` of the role's structured answer."""


# --- Schema: the verdict contract (verdict / flags), ACTIVE -------------------------------

QA_VERDICT_SCHEMA = Schema.create(
    schema_id="qa-verdict",
    version=SchemaVersion(1),
    description="QA verdict contract: passed/flagged/failed and a JSON array of flags (ADR-0034).",
    required_fields=QA_VERDICT_FIELDS,
)
QA_VERDICT_SCHEMA.transition(SchemaStatus.ACTIVE)


# --- Agent: the role descriptor (agent_ref -> prompt_ref) --------------------------------

QA_AGENT = Agent(
    agent_id=AGENT_REF,
    name="QA Agent",
    prompt_ref=PROMPT_REF,
    description="Checks a candidate content artifact for uniqueness, quality and compliance; "
    "returns a verdict.",
)


# --- catalogues keyed for the Composition Root -------------------------------------------

AGENTS: tuple[Agent, ...] = (QA_AGENT,)
SCHEMAS: dict[str, Schema] = {SCHEMA_REF: QA_VERDICT_SCHEMA}
