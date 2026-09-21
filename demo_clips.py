"""Produce one episode's clips from the Notion episode board (queue task 21.6).

    python demo_clips.py <notion page id of the episode>

One invocation of the clipping department, the same code a service would run (ADR-0059): the board
hands over an episode that is ready to clip, the file is located under ``OMEMO_EPISODE_ROOT``,
ffmpeg and whisper.cpp index it, the plan is cut and rendered, QA judges every clip and — when a
review desk is configured — each clip's review is published and its decision read back.

An episode that is unknown, not ready or has no source is the same clean exit: ``fetch_episode``
answers ``None`` for all of them on purpose (ADR-0055 §1).

Nothing here decides a review. A clip is approved only by a human, through the desk or by hand in
Notion; a session must not act as the reviewer.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping, Sequence

from demo import safe_print
from demo_factory import _print_binding_instructions

from omemo_content_factory.adapters.episode_board import EpisodeBoardError
from omemo_content_factory.adapters.episode_source import EpisodeSourceError
from omemo_content_factory.adapters.footage_index import FootageIndexError
from omemo_content_factory.adapters.review_desk import ReviewDesk, ReviewDeskError
from omemo_content_factory.agents import clip_qa_agent
from omemo_content_factory.application.clip_production import ClipInvocation, ClipProduction
from omemo_content_factory.application.qa_evaluation import ArtifactEvaluator
from omemo_content_factory.composition import (
    GOOGLE_REVIEW_DESK_VARS,
    NOTION_REVIEW_DESK_VARS,
    build_clip_production,
    build_qa_evaluator,
    build_review_desk,
)
from omemo_content_factory.domain.run import RunStatus
from omemo_content_factory.infrastructure.provider_model import (
    ProviderModelSelectionError,
    client_for_role,
)

_DESK_VARS = NOTION_REVIEW_DESK_VARS + GOOGLE_REVIEW_DESK_VARS


def _build_desk(environ: Mapping[str, str]) -> ReviewDesk | None:
    """The configured desk, or ``None`` when none is configured at all (ADR-0044 §5)."""
    if not any(environ.get(name, "").strip() for name in _DESK_VARS):
        return None
    return build_review_desk(environ)


def _build_evaluator() -> ArtifactEvaluator:
    """Compile the clip QA role on its own provider/model binding (ADR-0038 §2)."""
    return build_qa_evaluator(
        clip_qa_agent.CLIP_QA_AGENT,
        None,
        client_for_role(clip_qa_agent.AGENT_REF, os.environ),
        clip_qa_agent.SCHEMAS,
    )


def _build(environ: Mapping[str, str]) -> ClipProduction:
    return build_clip_production(environ, evaluator=_build_evaluator(), desk=_build_desk(environ))


def _report(invocation: ClipInvocation) -> None:
    if invocation.run_id is None:
        safe_print(f"Episode {invocation.episode_ref} is not ready to clip; nothing was started.")
        return
    safe_print(f"Run {invocation.run_id} — {invocation.status.value if invocation.status else '?'}")
    safe_print(f"  clips: {invocation.clips}")
    tally = invocation.tally
    judged = (
        f"  QA: {tally.passed} passed, {tally.flagged} flagged, {tally.failed} failed"
        + (f", {tally.unjudged} not judged yet" if tally.unjudged else "")
        + (f"; {tally.render_failed} clip(s) could not be rendered" if tally.render_failed else "")
    )
    safe_print(judged)
    safe_print(f"  approved: {tally.approved} of {invocation.clips}")
    if invocation.status is RunStatus.COMPLETED and not tally.approved:
        # `completed` means every clip is settled, not that any is ready (ADR-0059 §3).
        safe_print("  nothing is ready to publish: no clip passed QA and was approved")
    if invocation.failure_reason:
        safe_print(f"  the episode could not be produced: {invocation.failure_reason}")
    for task_id in invocation.failed_clips:
        safe_print(f"  clip failed: {task_id}")
    for location in invocation.published:
        safe_print(f"  review published: {location}")
    for decision in invocation.decisions:
        applied = "recorded" if decision.applied else "not recorded (QA has not passed)"
        safe_print(f"  decision {decision.decision.value} on {decision.review_id} — {applied}")
    for artifact_id in invocation.approved:
        safe_print(f"  approved: {artifact_id}")
    for label, error in (
        ("QA", invocation.qa_error),
        ("publication", invocation.publish_error),
        ("decision", invocation.decision_error),
    ):
        if error:
            safe_print(f"  {label} error (retried next invocation): {error}")


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1 or not arguments[0].strip():
        safe_print("usage: python demo_clips.py <notion page id of the episode>")
        return 2
    try:
        production = _build(os.environ)
    except (EpisodeBoardError, EpisodeSourceError, FootageIndexError, ReviewDeskError) as exc:
        safe_print(f"The clipping department is not configured: {exc}")
        return 1
    except ProviderModelSelectionError as exc:
        safe_print(f"Provider/model selection failed: {exc}")
        safe_print("The clip QA role needs its own binding and pricing (ADR-0016/0029). Set:")
        _print_binding_instructions()
        return 1

    if not production.has_desk:
        safe_print("No review desk is configured; the reviews stay in the Run store.")
    _report(production.invoke(arguments[0].strip()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
