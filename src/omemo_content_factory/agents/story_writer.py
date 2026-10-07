"""``story_writer@v1`` — writes a voiced story script from an idea (ADR-0087).

A producer on the Rin/Leo template: an :class:`Agent` descriptor and the ``story-script@v1``
:class:`Schema` its Output conforms to. The versioned Prompt text lives in the bundled store and is
materialized by the Composition Root (ADR-0030), never here. No Skills and no Tools.

Its answer is flat strings (ADR-0014); the two list-like fields use a one-item-per-line grammar that
:func:`omemo_content_factory.application.story_script.decode_story_script` alone judges.
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
    "STORY_SCRIPT_SCHEMA",
    "STORY_WRITER_AGENT",
]

AGENT_REF = "story_writer@v1"
"""The ``agent_ref`` selecting this role's executor."""

PROMPT_REF = "story-writer"
"""The logical Prompt id this Agent binds to (``Agent.prompt_ref -> Prompt.prompt_id``)."""

SCHEMA_REF = "story-script@v1"
"""The opaque ``schema_ref`` of the produced Output."""

STORY_SCRIPT_SCHEMA = Schema.create(
    schema_id="story-script",
    version=SchemaVersion(1),
    description="A voiced story: title, premise, characters, line-by-line dialogue, next part.",
    required_fields=("title", "premise", "characters", "dialogue", "next_part"),
)
STORY_SCRIPT_SCHEMA.transition(SchemaStatus.ACTIVE)

STORY_WRITER_AGENT = Agent(
    agent_id=AGENT_REF,
    name="Story Writer",
    prompt_ref=PROMPT_REF,
    description="Writes a short voiced story script — characters and dialogue — from an idea.",
)

AGENTS: tuple[Agent, ...] = (STORY_WRITER_AGENT,)
SCHEMAS: dict[str, Schema] = {SCHEMA_REF: STORY_SCRIPT_SCHEMA}
