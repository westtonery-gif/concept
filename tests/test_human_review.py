"""Behavioural tests for Human Review and the Artifact publication path (ADR-0007).

Everything is driven **only through the Run aggregate root** (``open_human_review`` /
``submit_review`` / ``transition_artifact`` and the read-only views). The central invariant —
no Artifact reaches ``PUBLISHED`` without an approving Human Review (DOMAIN_MODEL.md §6,
PROJECT.md §12) — is exercised end to end. No mocks, sleep or randomness.
"""

from __future__ import annotations

import pytest

from omemo_content_factory.domain.artifact import ArtifactNotApprovedError, ArtifactStatus
from omemo_content_factory.domain.evaluation import EvaluationStatus
from omemo_content_factory.domain.human_review import (
    HumanReviewApproved,
    HumanReviewRejected,
    HumanReviewRequested,
    InvalidReviewTransitionError,
    ReviewStatus,
)
from omemo_content_factory.domain.run import (
    Actor,
    InvalidTransitionError,
    Run,
    UnauthorizedActorError,
)
from omemo_content_factory.domain.task import TaskStatus

CD = Actor.CONTENT_DIRECTOR
REVIEWER = Actor.HUMAN_REVIEWER


def make_run() -> Run:
    """A fresh Run in CREATED."""
    return Run.create(
        run_id="run-hr-0001", content_brief_ref="brief-0001", workflow_version_ref="workflow@v1"
    )


def draft_artifact(run: Run) -> str:
    """Produce a Task -> Output -> Artifact (DRAFT) on ``run`` and return the artifact id."""
    task_id = run.open_task(
        workflow_step_ref="step-1", agent_ref="writer@v1", task_input="brief", by=CD
    )
    run.transition_task(task_id, TaskStatus.RUNNING, by=CD)
    run.transition_task(task_id, TaskStatus.SUCCEEDED, by=CD)
    output_id = run.record_output(task_id, payload="content", schema_ref="s@v1", by=CD)
    return run.create_artifact(output_id, kind="draft", by=CD)


def candidate_artifact(run: Run) -> str:
    """Produce an Artifact and move it to CANDIDATE; return its id."""
    artifact_id = draft_artifact(run)
    run.transition_artifact(artifact_id, ArtifactStatus.CANDIDATE, by=CD)
    return artifact_id


# --- Opening a review --------------------------------------------------------------------


def test_open_human_review_creates_a_pending_review_on_the_candidate() -> None:
    """Opening a review yields a PENDING review targeting the candidate, with an event."""
    run = make_run()
    artifact_id = candidate_artifact(run)
    review_id = run.open_human_review(artifact_id, by=CD)
    review = run.human_review(review_id)
    assert review.review_id == review_id
    assert review.run_id == run.run_id
    assert review.artifact_ref == artifact_id
    assert review.status is ReviewStatus.PENDING
    assert review.decided_by is None
    last = run.events[-1]
    assert isinstance(last, HumanReviewRequested)
    assert last.artifact_ref == artifact_id


def test_only_content_director_can_open_a_review() -> None:
    """Opening the Approval Gate is the Content Director's job (ADR-0007 §2, §5)."""
    run = make_run()
    artifact_id = candidate_artifact(run)
    with pytest.raises(UnauthorizedActorError):
        run.open_human_review(artifact_id, by=Actor.AGENT)


def test_a_review_needs_a_candidate_artifact() -> None:
    """A review cannot be opened on a non-CANDIDATE (here DRAFT) Artifact (ADR-0007 §5)."""
    run = make_run()
    artifact_id = draft_artifact(run)  # still DRAFT
    with pytest.raises(InvalidTransitionError):
        run.open_human_review(artifact_id, by=CD)


# --- Deciding a review -------------------------------------------------------------------


def test_approve_records_the_author_and_emits_event() -> None:
    """An Approve decision is recorded with its author and emits HumanReviewApproved."""
    run = make_run()
    review_id = run.open_human_review(candidate_artifact(run), by=CD)
    run.submit_review(review_id, ReviewStatus.APPROVED, by=REVIEWER)
    review = run.human_review(review_id)
    assert review.status is ReviewStatus.APPROVED
    assert review.decided_by == Actor.HUMAN_REVIEWER.value
    last = run.events[-1]
    assert isinstance(last, HumanReviewApproved)
    assert last.decided_by == Actor.HUMAN_REVIEWER.value


def test_reject_records_the_reason_and_emits_event() -> None:
    """A Reject decision records its reason and emits HumanReviewRejected."""
    run = make_run()
    review_id = run.open_human_review(candidate_artifact(run), by=CD)
    run.submit_review(review_id, ReviewStatus.REJECTED, by=REVIEWER, reason="off-topic")
    review = run.human_review(review_id)
    assert review.status is ReviewStatus.REJECTED
    assert review.reason == "off-topic"
    last = run.events[-1]
    assert isinstance(last, HumanReviewRejected)
    assert last.reason == "off-topic"


def test_only_the_human_reviewer_can_decide() -> None:
    """The decision is the human's; the Content Director cannot submit it (ADR-0007 §2)."""
    run = make_run()
    review_id = run.open_human_review(candidate_artifact(run), by=CD)
    with pytest.raises(UnauthorizedActorError):
        run.submit_review(review_id, ReviewStatus.APPROVED, by=CD)
    assert run.human_review(review_id).status is ReviewStatus.PENDING


def test_a_review_is_decided_only_once() -> None:
    """A decided review cannot be re-decided (ADR-0007 §3)."""
    run = make_run()
    review_id = run.open_human_review(candidate_artifact(run), by=CD)
    run.submit_review(review_id, ReviewStatus.APPROVED, by=REVIEWER)
    with pytest.raises(InvalidReviewTransitionError):
        run.submit_review(review_id, ReviewStatus.REJECTED, by=REVIEWER)


# --- The publication invariant -----------------------------------------------------------


def test_artifact_cannot_be_approved_without_an_approving_review() -> None:
    """CANDIDATE -> APPROVED is refused without an approving Human Review (ADR-0007 §6)."""
    run = make_run()
    artifact_id = candidate_artifact(run)
    with pytest.raises(ArtifactNotApprovedError):
        run.transition_artifact(artifact_id, ArtifactStatus.APPROVED, by=CD)
    assert run.artifact(artifact_id).status is ArtifactStatus.CANDIDATE


def test_approved_review_lets_the_artifact_be_approved_then_published() -> None:
    """With an Approve (and a passed QA, ADR-0018 §5), CANDIDATE -> APPROVED -> PUBLISHED."""
    run = make_run()
    artifact_id = candidate_artifact(run)
    evaluation_id = run.open_evaluation(artifact_id, kind="qa", by=CD)
    run.record_evaluation(evaluation_id, EvaluationStatus.PASSED, by=CD)
    review_id = run.open_human_review(artifact_id, by=CD)
    run.submit_review(review_id, ReviewStatus.APPROVED, by=REVIEWER)
    run.transition_artifact(artifact_id, ArtifactStatus.APPROVED, by=CD)
    assert run.artifact(artifact_id).status is ArtifactStatus.APPROVED
    run.transition_artifact(artifact_id, ArtifactStatus.PUBLISHED, by=CD)
    assert run.artifact(artifact_id).status is ArtifactStatus.PUBLISHED


def test_a_candidate_artifact_can_be_rejected() -> None:
    """A candidate may be rejected (no approval needed to reject; fail-closed is safe)."""
    run = make_run()
    artifact_id = candidate_artifact(run)
    run.transition_artifact(artifact_id, ArtifactStatus.REJECTED, by=CD)
    assert run.artifact(artifact_id).status is ArtifactStatus.REJECTED
