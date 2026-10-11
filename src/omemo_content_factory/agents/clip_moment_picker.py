"""``clip_moment_picker@v1`` — picks an episode's strongest moments from its transcript (ADR-0097).

A producer on the Rin/Leo template: an :class:`Agent` descriptor and the ``clip-moments@v1``
:class:`Schema` its Output conforms to. The versioned Prompt text lives in the bundled store and is
materialized by the Composition Root (ADR-0030), never here. No Skills and no Tools — everything
it needs is in its input. The pure decoder in ``application/clip_moments.py`` alone judges its
answer.
"""

from __future__ import annotations

from omemo_content_factory.domain.agent import Agent
from omemo_content_factory.domain.schema import Schema, SchemaStatus, SchemaVersion

__all__ = [
    "AGENTS",
    "AGENT_REF",
    "CLIP_MOMENTS_SCHEMA",
    "CLIP_MOMENT_PICKER_AGENT",
    "PROMPT_REF",
    "SCHEMAS",
    "SCHEMA_REF",
]

AGENT_REF = "clip_moment_picker@v1"
"""The ``agent_ref`` selecting this role's executor."""

PROMPT_REF = "clip-moment-picker"
"""The logical Prompt id this Agent binds to (``Agent.prompt_ref -> Prompt.prompt_id``)."""

SCHEMA_REF = "clip-moments@v1"
"""The opaque ``schema_ref`` of the produced Output."""

CLIP_MOMENTS_SCHEMA = Schema.create(
    schema_id="clip-moments",
    version=SchemaVersion(1),
    description="The strongest moments of an episode, one 'first-last | kind | why' per line.",
    required_fields=("moments",),
)
CLIP_MOMENTS_SCHEMA.transition(SchemaStatus.ACTIVE)

CLIP_MOMENT_PICKER_AGENT = Agent(
    agent_id=AGENT_REF,
    name="Clip Moment Picker",
    prompt_ref=PROMPT_REF,
    description="Chooses the moments of an episode most worth cutting, naming them by line.",
)

AGENTS: tuple[Agent, ...] = (CLIP_MOMENT_PICKER_AGENT,)
SCHEMAS: dict[str, Schema] = {SCHEMA_REF: CLIP_MOMENTS_SCHEMA}
