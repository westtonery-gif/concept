"""Publishing and reading back **every** clip's review (ADR-0059 §5, CLIPPING_SPEC §8).

The content factory's review path addresses one review: ``pending_review`` returns ``pending[-1]``
and publication and decision-reading both inherit that. With thirteen clips waiting on one Run it
would serve the last one and **silently leave twelve unpublished** — no error, no flag, just clips
that never reach a human.

So these are per-Artifact siblings, not a change to those functions: Stage 10–12's acceptance pins
their behaviour, and "fixing" them in place would alter a path the factory depends on. They reuse
``latest_qa``, which was already per-Artifact.

Nothing here mutates the store. A caller publishes, applies and then saves, exactly as
``review_publication`` and ``review_decision`` expect (ADR-0044 §4, ADR-0045 §2).
"""

from __future__ import annotations

import json

from omemo_content_factory.adapters.review_desk import PostDraft, ReviewDesk, ReviewPackage
from omemo_content_factory.application.review_decision import FetchedDecision
from omemo_content_factory.application.review_publication import PublishedReview, latest_qa
from omemo_content_factory.domain.artifact import ArtifactStatus
from omemo_content_factory.domain.evaluation import EvaluationStatus
from omemo_content_factory.domain.human_review import HumanReviewView, ReviewStatus
from omemo_content_factory.domain.output import OutputStatus
from omemo_content_factory.domain.run import Actor, Run
from omemo_content_factory.domain.task import TaskStatus

__all__ = [
    "POST_SCHEMA_REF",
    "POST_STEP_REF",
    "clip_review_package",
    "latest_post",
    "open_clip_reviews",
    "pending_clip_reviews",
    "post_task_input",
    "publish_clip_reviews",
    "take_clip_decisions",
]


POST_STEP_REF = "post-text"
"""The step of every post text a clip has, the model's draft or the reviewer's edit (ADR-0072)."""

POST_SCHEMA_REF = "clip-post@v1"


def post_task_input(artifact_id: str, /, **extra: str) -> str:
    """The canonical input of a ``post-text`` Task: which clip it is for, plus anything else."""
    return json.dumps({"artifact_ref": artifact_id, **extra}, ensure_ascii=False, sort_keys=True)


def latest_post(run: Run, artifact_id: str, /) -> PostDraft | None:
    """The text a clip would be posted with: its latest valid ``post-text`` Output (§4).

    The reviewer's edit when there is one, the model's draft otherwise, ``None`` when neither
    exists or the latest cannot be posted.
    """
    latest: PostDraft | None = None
    for task in run.tasks:
        if task.workflow_step_ref != POST_STEP_REF or task.status is not TaskStatus.SUCCEEDED:
            continue
        output = task.output
        if output is None or output.status is not OutputStatus.VALID:
            continue
        try:
            if json.loads(task.task_input).get("artifact_ref") != artifact_id:
                continue
            fields = json.loads(output.payload)
            latest = PostDraft(title=fields["title"], description=fields["description"])
        except (ValueError, KeyError, TypeError, AttributeError):
            latest = None
    return latest


def pending_clip_reviews(run: Run) -> tuple[HumanReviewView, ...]:
    """**Every** ``PENDING`` Human Review of ``run``, in opening order.

    The deliberate difference from ``review_publication.pending_review``, which takes the latest
    one only.
    """
    return tuple(view for view in run.human_reviews if view.status is ReviewStatus.PENDING)


def open_clip_reviews(run: Run) -> tuple[str, ...]:
    """Open a review for every QA-passed candidate that has none yet; return their artifact ids.

    A candidate whose latest verdict is not ``PASSED`` gets no review: it cannot be approved
    anyway (ADR-0018), and queueing it would ask a human to decide something the gate has already
    settled. The Run is changed in memory only — the caller saves.
    """
    reviewed = {view.artifact_ref for view in run.human_reviews}
    opened: list[str] = []
    for artifact in run.artifacts:
        if artifact.status is not ArtifactStatus.CANDIDATE or artifact.artifact_id in reviewed:
            continue
        evaluation = latest_qa(run, artifact.artifact_id)
        if evaluation is None or evaluation.status is not EvaluationStatus.PASSED:
            continue
        run.open_human_review(artifact.artifact_id, by=Actor.CONTENT_DIRECTOR)
        opened.append(artifact.artifact_id)
    return tuple(opened)


def clip_review_package(run: Run, review: HumanReviewView) -> ReviewPackage:
    """What the reviewer is shown for one clip: the candidate, its episode and its QA flags."""
    candidate = run.artifact(review.artifact_ref)
    evaluation = latest_qa(run, candidate.artifact_id)
    return ReviewPackage(
        run_id=run.run_id,
        review_id=review.review_id,
        candidate=candidate,
        brief=run.content_brief_ref,
        qa_flags=evaluation.flags if evaluation is not None else (),
        post=latest_post(run, candidate.artifact_id),
    )


def publish_clip_reviews(run: Run, desk: ReviewDesk) -> tuple[PublishedReview, ...]:
    """Publish every pending review of ``run``; a ``ReviewDeskError`` propagates unchanged.

    Publishing is idempotent per ``review_id`` (ADR-0023 §7), so calling this again is the retry
    and already-published clips cost one lookup each. The Run is never modified.
    """
    return tuple(
        PublishedReview(
            review_id=review.review_id, location=desk.publish(clip_review_package(run, review))
        )
        for review in pending_clip_reviews(run)
    )


def take_clip_decisions(run: Run, desk: ReviewDesk) -> tuple[FetchedDecision, ...]:
    """Read back every pending review's decision and record the ones the gate admits.

    An undecided review is skipped. An approval of a candidate whose latest QA verdict is not
    ``PASSED`` is **not recorded** (``applied=False``, ADR-0045 §3): recording it would strand that
    clip with no pending review while it still cannot be approved. The Run is changed in memory
    only — the caller saves.
    """
    taken: list[FetchedDecision] = []
    for review in pending_clip_reviews(run):
        decision = desk.fetch_decision(review.review_id)
        if decision is None:
            continue
        evaluation = latest_qa(run, review.artifact_ref)
        passed = evaluation is not None and evaluation.status is EvaluationStatus.PASSED
        applied = decision.decision is not ReviewStatus.APPROVED or passed
        if applied:
            run.submit_review(
                review.review_id,
                decision.decision,
                by=Actor.HUMAN_REVIEWER,
                reason=decision.reason,
            )
        taken.append(
            FetchedDecision(
                review_id=review.review_id,
                decision=decision.decision,
                reason=decision.reason,
                applied=applied,
                post=decision.post,
            )
        )
    return tuple(taken)
