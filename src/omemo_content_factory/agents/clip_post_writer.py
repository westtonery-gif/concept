"""``clip_post_writer@v1`` — drafts the text a clip is posted with (ADR-0072).

A producer on the Rin/Leo template: an :class:`Agent` descriptor and the ``clip-post@v1``
:class:`Schema` its Output conforms to. The versioned Prompt text lives in the bundled store and is
materialized by the Composition Root (ADR-0030), never here. No Skills and no Tools.

The draft is shown on the clip's review page, where the reviewer may correct it before deciding;
whichever text stands at the decision is the one posted (ADR-0072 §4).
"""

from __future__ import annotations

from omemo_content_factory.domain.agent import Agent
from omemo_content_factory.domain.schema import Schema, SchemaStatus, SchemaVersion

__all__ = [
    "AGENTS",
    "AGENT_REF",
    "CLIP_POST_SCHEMA",
    "CLIP_POST_WRITER_AGENT",
    "PROMPT_REF",
    "SCHEMAS",
    "SCHEMA_REF",
]

AGENT_REF = "clip_post_writer@v1"
"""The ``agent_ref`` selecting this role's executor."""

PROMPT_REF = "clip-post-writer"
"""The logical Prompt id this Agent binds to (``Agent.prompt_ref -> Prompt.prompt_id``)."""

SCHEMA_REF = "clip-post@v1"
"""The opaque ``schema_ref`` of the produced Output — also the shape of a reviewer's edit."""

CLIP_POST_SCHEMA = Schema.create(
    schema_id="clip-post",
    version=SchemaVersion(1),
    description="Post text for a short clip: a title and a description with hashtags.",
    required_fields=("title", "description"),
)
CLIP_POST_SCHEMA.transition(SchemaStatus.ACTIVE)

CLIP_POST_WRITER_AGENT = Agent(
    agent_id=AGENT_REF,
    name="Clip Post Writer",
    prompt_ref=PROMPT_REF,
    description="Drafts the title and description a clip is posted with; a human edits it.",
)

AGENTS: tuple[Agent, ...] = (CLIP_POST_WRITER_AGENT,)
SCHEMAS: dict[str, Schema] = {SCHEMA_REF: CLIP_POST_SCHEMA}
