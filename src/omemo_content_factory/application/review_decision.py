"""Review decision — the human's decision on the review desk is read into the Run (ADR-0045).

A Run waiting at the Approval Gate has one pending Human Review, published on the ``ReviewDesk``
(ADR-0044). ``apply_review_decision`` asks the desk for the reviewer's decision on exactly that
review and records it through ``Run.submit_review`` as the Human Reviewer; the Content Director then
routes it on ``resume`` — ``APPROVED`` completes a QA-passed candidate, ``CHANGES_REQUESTED`` and
``REJECTED`` rework it.

An ``APPROVED`` decision on a candidate whose latest QA is not ``PASSED`` is **not** recorded: the
fail-closed gate would refuse it, and a recorded approval would leave the Run with no pending review
for the reviewer to change their mind on (ADR-0045 §3). The review stays ``PENDING``.

The Run is changed in memory only; the caller saves it and resumes. A ``ReviewDeskError`` propagates
with the Run untouched. ``take_review_decision`` is that caller's first half for a stored Run —
publish, apply, save (ADR-0046). Nothing here is specific to Google Docs.
"""

from __future__ import annotations

from dataclasses import dataclass

from omemo_content_factory.adapters.review_desk import PostDraft, ReviewDesk
from omemo_content_factory.adapters.run_store import RunStore
from omemo_content_factory.application.review_publication import (
    latest_qa,
    pending_review,
    publish_pending_review,
)
from omemo_content_factory.domain.evaluation import EvaluationStatus
from omemo_content_factory.domain.human_review import ReviewId, ReviewStatus
from omemo_content_factory.domain.run import Actor, Run


@dataclass(frozen=True, slots=True)
class FetchedDecision:
    """A decision the desk returned for a Run's pending review, and whether it was recorded.

    ``applied`` is ``False`` only for an approval the QA gate would refuse (ADR-0045 §3).
    """

    review_id: ReviewId
    decision: ReviewStatus
    reason: str | None
    applied: bool
    post: PostDraft | None = None
    """The post text the reviewer left, when the desk reads one (ADR-0072 §3)."""


def apply_review_decision(run: Run, desk: ReviewDesk) -> FetchedDecision | None:
    """Record the desk's decision on ``run``'s pending review; ``None`` while there is none.

    No pending review → ``None`` without calling the desk. An undecided review → ``None`` and the
    Run is unchanged. A ``ReviewDeskError`` propagates unchanged (ADR-0045 §2).
    """
    review = pending_review(run)
    if review is None:
        return None
    fetched = desk.fetch_decision(review.review_id)
    if fetched is None:
        return None
    if fetched.decision is ReviewStatus.APPROVED:
        evaluation = latest_qa(run, review.artifact_ref)
        if evaluation is None or evaluation.status is not EvaluationStatus.PASSED:
            return FetchedDecision(review.review_id, fetched.decision, fetched.reason, False)
    run.submit_review(
        review.review_id, fetched.decision, by=Actor.HUMAN_REVIEWER, reason=fetched.reason
    )
    return FetchedDecision(review.review_id, fetched.decision, fetched.reason, True)


def take_review_decision(store: RunStore, desk: ReviewDesk, run_id: str) -> FetchedDecision | None:
    """Read the desk's decision into the stored Run ``run_id`` and save it (ADR-0046 §1).

    No stored Run → ``None`` without calling the desk. Otherwise the pending review is published
    first — idempotent, and it makes a review a crash left unpublished readable (ADR-0045 §4) — then
    ``apply_review_decision``; the Run is saved only when the decision was recorded. The caller
    resumes the Run afterwards. A ``ReviewDeskError`` propagates and nothing is saved.
    """
    run = store.load(run_id)
    if run is None:
        return None
    publish_pending_review(run, desk)
    fetched = apply_review_decision(run, desk)
    if fetched is not None and fetched.applied:
        store.save(run)
    return fetched
