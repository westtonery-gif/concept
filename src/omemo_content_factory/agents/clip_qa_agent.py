"""``clip_qa_agent@v1`` — the clipping department's QA role (ADR-0056, CLIPPING_SPEC §8).

The same shape as ``qa_agent``: an :class:`Agent` descriptor plus the **same** ``qa-verdict@v1``
Schema, whose ``required_fields`` are the ADR-0034 verdict fields. The versioned Prompt text lives
in the external package store (ADR-0030) and is materialized by the Composition Root, never here.

Two things are deliberate. The Schema object is **reused**, not recreated: one verdict contract,
one source, and a second vocabulary would mean a second decoder to keep honest. And the role judges
**meaning only** — duration, container and resolution are computed by ``check_clip_format``
(ADR-0056 §1), because a probabilistic answer to an exact question is not a gate and a render
defect is not something a human should adjudicate.

Granted no Skills and no Tools, and it answers with a verdict rather than an Output: it sits behind
the ``ArtifactEvaluator`` port, never as a Workflow step.
"""

from __future__ import annotations

from omemo_content_factory.agents.qa_agent import QA_VERDICT_SCHEMA, SCHEMA_REF
from omemo_content_factory.domain.agent import Agent
from omemo_content_factory.domain.schema import Schema

__all__ = ["AGENTS", "AGENT_REF", "CLIP_QA_AGENT", "PROMPT_REF", "SCHEMAS", "SCHEMA_REF"]

AGENT_REF = "clip_qa_agent@v1"
"""The ``agent_ref`` identifying this role."""

PROMPT_REF = "clip-qa-agent"
"""The logical Prompt id this Agent binds to (``Agent.prompt_ref -> Prompt.prompt_id``)."""


CLIP_QA_AGENT = Agent(
    agent_id=AGENT_REF,
    name="Clip QA Agent",
    prompt_ref=PROMPT_REF,
    description="Checks one clip cut from an episode for meaning preserved under cutting; "
    "returns a verdict.",
)


AGENTS: tuple[Agent, ...] = (CLIP_QA_AGENT,)
SCHEMAS: dict[str, Schema] = {SCHEMA_REF: QA_VERDICT_SCHEMA}
