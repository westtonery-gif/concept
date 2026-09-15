"""``script_writer@v1`` (Leo) — the video script-writer role definition.

The concrete, static assets of one production role, owned by the factory (migrated from Main Core
per ADR-0001 Slice A): the :class:`Agent` descriptor and the ``script-draft`` :class:`Schema` its
Output conforms to (ADR-0008). The versioned Prompt text lives in the external package store and is
materialized by the Composition Root (ADR-0030), never by this role module.

The module exposes the Agent/Schema catalogues keyed as the Composition Root expects.
``script-draft@v1`` is the same opaque ``schema_ref`` the Output already carried, kept stable
across the migration.
"""

from __future__ import annotations

from omemo_content_factory.domain.agent import Agent
from omemo_content_factory.domain.schema import Schema, SchemaStatus, SchemaVersion

# --- reference strings (opaque handles used across the Composition Root) ------------------

AGENT_REF = "script_writer@v1"
"""The ``agent_ref`` selecting this role's executor (unchanged from Main Core)."""

PROMPT_REF = "script-writer"
"""The logical Prompt id this Agent binds to (``Agent.prompt_ref -> Prompt.prompt_id``)."""

SCHEMA_REF = "script-draft@v1"
"""The opaque ``schema_ref`` of the produced Output (stable across the migration)."""


# --- Schema: the output contract (title / hook / script), ACTIVE -------------------------

SCRIPT_DRAFT_SCHEMA = Schema.create(
    schema_id="script-draft",
    version=SchemaVersion(1),
    description="Video script draft contract: catchy title, first-seconds hook, scene script.",
    required_fields=("title", "hook", "script"),
)
SCRIPT_DRAFT_SCHEMA.transition(SchemaStatus.ACTIVE)


# --- Agent: the role descriptor (agent_ref -> prompt_ref) --------------------------------

SCRIPT_WRITER_AGENT = Agent(
    agent_id=AGENT_REF,
    name="Leo — Script Writer",
    prompt_ref=PROMPT_REF,
    description="Turns research/brief into a short vertical video script (title, hook, script).",
)


# --- catalogues keyed for the Composition Root -------------------------------------------

AGENTS: tuple[Agent, ...] = (SCRIPT_WRITER_AGENT,)
SCHEMAS: dict[str, Schema] = {SCHEMA_REF: SCRIPT_DRAFT_SCHEMA}
