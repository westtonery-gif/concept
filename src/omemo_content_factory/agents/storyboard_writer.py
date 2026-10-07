"""``storyboard_writer@v1`` — turns a story script into shots with picture prompts (ADR-0089).

A producer on the Rin/Leo template: an :class:`Agent` descriptor and the ``storyboard@v1``
:class:`Schema` its Output conforms to. The versioned Prompt text lives in the bundled store and is
materialized by the Composition Root (ADR-0030), never here. No Skills and no Tools.

Its answer is flat strings (ADR-0014); ``shots`` uses a one-shot-per-line grammar that
:func:`omemo_content_factory.application.storyboard.decode_storyboard` alone judges.
"""

from __future__ import annotations

from omemo_content_factory.domain.agent import Agent
from omemo_content_factory.domain.schema import Schema, SchemaStatus, SchemaVersion

__all__ = [
    "AGENTS",
    "AGENT_REF",
    "PROMPT_REF",
    "SCHEMAS",
    "SCHEMA_REF",
    "STORYBOARD_SCHEMA",
    "STORYBOARD_WRITER_AGENT",
]

AGENT_REF = "storyboard_writer@v1"
"""The ``agent_ref`` selecting this role's executor."""

PROMPT_REF = "storyboard-writer"
"""The logical Prompt id this Agent binds to (``Agent.prompt_ref -> Prompt.prompt_id``)."""

SCHEMA_REF = "storyboard@v1"
"""The opaque ``schema_ref`` of the produced Output."""

STORYBOARD_SCHEMA = Schema.create(
    schema_id="storyboard",
    version=SchemaVersion(1),
    description="A story's shots: the shared world and one line per shot with picture and motion.",
    required_fields=("world", "shots"),
)
STORYBOARD_SCHEMA.transition(SchemaStatus.ACTIVE)

STORYBOARD_WRITER_AGENT = Agent(
    agent_id=AGENT_REF,
    name="Storyboard Writer",
    prompt_ref=PROMPT_REF,
    description="Cuts a voiced story script into pictures: shots, framing, action and motion.",
)

AGENTS: tuple[Agent, ...] = (STORYBOARD_WRITER_AGENT,)
SCHEMAS: dict[str, Schema] = {SCHEMA_REF: STORYBOARD_SCHEMA}
