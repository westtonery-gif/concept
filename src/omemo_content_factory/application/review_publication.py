"""Review publication — a Run waiting for a human is shown on the review desk (ADR-0044).

When the Content Director stops a Run at the Approval Gate (``WAITING_HUMAN`` with a ``PENDING``
Human Review), the reviewer has to see the candidate somewhere. ``publish_pending_review`` builds
the ``ReviewPackage`` from the Run alone — the candidate Artifact, the brief the first step was
given and the candidate's latest QA flags — and hands it to the ``ReviewDesk``.

It reads the Run and never changes it. Publishing is idempotent per ``review_id`` (ADAPTER_SPEC
§6), so an entrypoint calls it after every invocation: a Run that is not waiting for a human
publishes nothing, one already published gets the same location back, and a desk outage
(``ReviewDeskError``) propagates for the entrypoint to report while the stored Run stays as it is.
Nothing here is specific to Google Docs.
"""

from __future__ import annotations

from dataclasses import dataclass

from omemo_content_factory.adapters.review_desk import ReviewDesk, ReviewPackage
from omemo_content_factory.application.qa_evaluation import QA_KIND
from omemo_content_factory.domain.artifact import ArtifactId
from omemo_content_factory.domain.evaluation import EvaluationView
from omemo_content_factory.domain.human_review import HumanReviewView, ReviewId, ReviewStatus
from omemo_content_factory.domain.run import Run, RunStatus


@dataclass(frozen=True, slots=True)
class PublishedReview:
    """Where the desk put a Run's pending review: which review, and the desk's location."""

    review_id: ReviewId
    location: str


def pending_review(run: Run) -> HumanReviewView | None:
    """The review ``run`` waits on: its latest ``PENDING`` Human Review at ``WAITING_HUMAN``.

    ``None`` when the Run is not waiting for a human or every review is decided. Both publication
    (ADR-0044 §3) and reading the decision back (ADR-0045 §2) address this one review.
    """
    if run.status is not RunStatus.WAITING_HUMAN:
        return None
    pending = [view for view in run.human_reviews if view.status is ReviewStatus.PENDING]
    return pending[-1] if pending else None


def latest_qa(run: Run, artifact_id: ArtifactId) -> EvaluationView | None:
    """The latest QA Evaluation of ``artifact_id`` (evaluations are kept in opening order)."""
    evaluations = [
        view
        for view in run.evaluations
        if view.artifact_ref == artifact_id and view.kind == QA_KIND
    ]
    return evaluations[-1] if evaluations else None


def pending_review_package(run: Run) -> ReviewPackage | None:
    """The package a reviewer needs for ``run``'s pending review, or ``None`` if none is pending.

    Only a Run at ``WAITING_HUMAN`` with a ``PENDING`` Human Review has one; the latest pending
    review is taken (ADR-0044 §3). The brief is the first Task's stored input — exactly what the
    Run was produced from; the flags are those of the candidate's latest QA Evaluation, if any.
    """
    review = pending_review(run)
    if review is None or not run.tasks:
        return None
    candidate = run.artifact(review.artifact_ref)
    evaluation = latest_qa(run, candidate.artifact_id)
    return ReviewPackage(
        run_id=run.run_id,
        review_id=review.review_id,
        candidate=candidate,
        brief=run.tasks[0].task_input,
        qa_flags=evaluation.flags if evaluation is not None else (),
    )


def publish_pending_review(run: Run, desk: ReviewDesk) -> PublishedReview | None:
    """Publish ``run``'s pending review on ``desk``; ``None`` when nothing waits for a human.

    A ``ReviewDeskError`` propagates unchanged; the Run is never modified, so calling this again on
    the next invocation is the retry (ADR-0044 §4).
    """
    package = pending_review_package(run)
    if package is None:
        return None
    location = desk.publish(package)
    return PublishedReview(review_id=package.review_id, location=location)
