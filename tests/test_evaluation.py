"""Behavioural tests for the Evaluation (QA) entity and the fail-closed QA gate (ADR-0018).

Everything is driven **only through the Run aggregate root** (``open_evaluation`` /
``record_evaluation`` / ``transition_artifact`` and the read-only views). The central invariant —
no Artifact is approved (hence published) without a passing latest QA verdict, even after a human
``Approve`` (DOMAIN_MODEL.md §6, PROJECT.md §10) — is exercised end to end. Scenario ids refer to
``EVALUATION_ACCEPTANCE.md``. No mocks, sleep or randomness.
"""

from __future__ import annotations

import pytest

from omemo_content_factory.domain.artifact import (
    ArtifactNotApprovedError,
    ArtifactQaNotPassedError,
    ArtifactStatus,
)
from omemo_content_factory.domain.evaluation import (
    Evaluation,
    EvaluationCompleted,
    EvaluationStatus,
    ImmutableEvaluationAttributeError,
    InvalidEvaluationTransitionError,
)
from omemo_content_factory.domain.human_review import ReviewStatus
from omemo_content_factory.domain.run import (
    Actor,
    InvalidTransitionError,
    Run,
    UnauthorizedActorError,
)
from omemo_content_factory.domain.task import TaskStatus

CD = Actor.CONTENT_DIRECTOR
REVIEWER = Actor.HUMAN_REVIEWER
PASSED = EvaluationStatus.PASSED
RISK_VERDICTS = [EvaluationStatus.FLAGGED, EvaluationStatus.FAILED]


def make_run() -> Run:
    """A fresh Run in CREATED."""
    return Run.create(
        run_id="run-qa-0001", content_brief_ref="brief-0001", workflow_version_ref="workflow@v1"
    )


def candidate_artifact(run: Run) -> str:
    """Produce a Task -> Output -> Artifact on ``run``, move it to CANDIDATE, return its id."""
    task_id = run.open_task(
        workflow_step_ref="step-1", agent_ref="writer@v1", task_input="brief", by=CD
    )
    run.transition_task(task_id, TaskStatus.RUNNING, by=CD)
    run.transition_task(task_id, TaskStatus.SUCCEEDED, by=CD)
    output_id = run.record_output(task_id, payload="content", schema_ref="s@v1", by=CD)
    artifact_id = run.create_artifact(output_id, kind="script", by=CD)
    run.transition_artifact(artifact_id, ArtifactStatus.CANDIDATE, by=CD)
    return artifact_id


def evaluate(
    run: Run, artifact_id: str, verdict: EvaluationStatus, flags: tuple[str, ...] = ()
) -> str:
    """Open an evaluation of ``artifact_id`` and record ``verdict``; return the evaluation id."""
    evaluation_id = run.open_evaluation(artifact_id, kind="qa", by=CD)
    run.record_evaluation(evaluation_id, verdict, by=CD, flags=flags)
    return evaluation_id


def approve_by_human(run: Run, artifact_id: str) -> None:
    """Open a Human Review on ``artifact_id`` and record the human's Approve."""
    review_id = run.open_human_review(artifact_id, by=CD)
    run.submit_review(review_id, ReviewStatus.APPROVED, by=REVIEWER)


# --- Happy path (EHP) --------------------------------------------------------------------


def test_ehp_01_open_evaluation_creates_a_pending_evaluation_without_an_event() -> None:
    run = make_run()
    artifact_id = candidate_artifact(run)
    events_before = len(run.events)
    evaluation_id = run.open_evaluation(artifact_id, kind="qa", by=CD)
    view = run.evaluation(evaluation_id)
    assert view.evaluation_id == evaluation_id
    assert view.run_id == run.run_id
    assert view.artifact_ref == artifact_id
    assert view.kind == "qa"
    assert view.status is EvaluationStatus.PENDING
    assert view.flags == ()
    assert len(run.events) == events_before
    assert run.evaluations == (view,)


def test_ehp_02_recording_passed_emits_evaluation_completed() -> None:
    run = make_run()
    artifact_id = candidate_artifact(run)
    evaluation_id = evaluate(run, artifact_id, PASSED)
    assert run.evaluation(evaluation_id).status is PASSED
    last = run.events[-1]
    assert isinstance(last, EvaluationCompleted)
    assert last.evaluation_id == evaluation_id
    assert last.artifact_ref == artifact_id
    assert last.verdict is PASSED
    assert last.flags == ()


@pytest.mark.parametrize("verdict", RISK_VERDICTS)
def test_ehp_03_risk_verdict_records_its_flags(verdict: EvaluationStatus) -> None:
    run = make_run()
    flags = ("unsupported health claim", "missing source")
    evaluation_id = evaluate(run, candidate_artifact(run), verdict, flags)
    view = run.evaluation(evaluation_id)
    assert view.status is verdict
    assert view.flags == flags
    last = run.events[-1]
    assert isinstance(last, EvaluationCompleted)
    assert last.verdict is verdict
    assert last.flags == flags


# --- Violations (EFL) --------------------------------------------------------------------


@pytest.mark.parametrize("actor", [Actor.AGENT, Actor.HUMAN_REVIEWER])
def test_efl_01_only_content_director_opens_an_evaluation(actor: Actor) -> None:
    run = make_run()
    artifact_id = candidate_artifact(run)
    with pytest.raises(UnauthorizedActorError):
        run.open_evaluation(artifact_id, kind="qa", by=actor)
    assert run.evaluations == ()


@pytest.mark.parametrize("actor", [Actor.AGENT, Actor.HUMAN_REVIEWER])
def test_efl_02_only_content_director_records_a_verdict(actor: Actor) -> None:
    run = make_run()
    evaluation_id = run.open_evaluation(candidate_artifact(run), kind="qa", by=CD)
    with pytest.raises(UnauthorizedActorError):
        run.record_evaluation(evaluation_id, PASSED, by=actor)
    assert run.evaluation(evaluation_id).status is EvaluationStatus.PENDING


def test_efl_03_an_evaluation_needs_a_candidate_artifact() -> None:
    run = make_run()
    task_id = run.open_task(workflow_step_ref="s", agent_ref="a@v1", task_input="i", by=CD)
    run.transition_task(task_id, TaskStatus.RUNNING, by=CD)
    run.transition_task(task_id, TaskStatus.SUCCEEDED, by=CD)
    output_id = run.record_output(task_id, payload="p", schema_ref="s@v1", by=CD)
    draft_id = run.create_artifact(output_id, kind="script", by=CD)  # still DRAFT
    with pytest.raises(InvalidTransitionError):
        run.open_evaluation(draft_id, kind="qa", by=CD)


def test_efl_04_a_verdict_is_given_only_once() -> None:
    run = make_run()
    evaluation_id = evaluate(run, candidate_artifact(run), EvaluationStatus.FLAGGED, ("risk",))
    with pytest.raises(InvalidEvaluationTransitionError):
        run.record_evaluation(evaluation_id, PASSED, by=CD)
    view = run.evaluation(evaluation_id)
    assert view.status is EvaluationStatus.FLAGGED
    assert view.flags == ("risk",)


def test_efl_05_pending_is_not_a_verdict() -> None:
    run = make_run()
    evaluation_id = run.open_evaluation(candidate_artifact(run), kind="qa", by=CD)
    with pytest.raises(InvalidEvaluationTransitionError):
        run.record_evaluation(evaluation_id, EvaluationStatus.PENDING, by=CD)


@pytest.mark.parametrize("name", ["_evaluation_id", "run_id", "_artifact_ref", "kind"])
def test_efl_06_identity_subject_and_kind_are_immutable(name: str) -> None:
    evaluation = Evaluation(evaluation_id="e-1", run_id="r-1", artifact_ref="a-1", kind="qa")
    with pytest.raises(ImmutableEvaluationAttributeError):
        setattr(evaluation, name, "changed")


# --- The fail-closed gate (EGT) ----------------------------------------------------------


def test_egt_01_human_approve_without_any_evaluation_cannot_approve() -> None:
    run = make_run()
    artifact_id = candidate_artifact(run)
    approve_by_human(run, artifact_id)
    with pytest.raises(ArtifactQaNotPassedError):
        run.transition_artifact(artifact_id, ArtifactStatus.APPROVED, by=CD)
    assert run.artifact(artifact_id).status is ArtifactStatus.CANDIDATE


def test_egt_02_a_pending_evaluation_keeps_the_gate_shut() -> None:
    run = make_run()
    artifact_id = candidate_artifact(run)
    run.open_evaluation(artifact_id, kind="qa", by=CD)
    approve_by_human(run, artifact_id)
    with pytest.raises(ArtifactQaNotPassedError):
        run.transition_artifact(artifact_id, ArtifactStatus.APPROVED, by=CD)


@pytest.mark.parametrize("verdict", RISK_VERDICTS)
def test_egt_03_a_risk_verdict_cannot_be_overridden_by_a_human_approve(
    verdict: EvaluationStatus,
) -> None:
    run = make_run()
    artifact_id = candidate_artifact(run)
    evaluate(run, artifact_id, verdict, ("risk",))
    approve_by_human(run, artifact_id)
    with pytest.raises(ArtifactQaNotPassedError):
        run.transition_artifact(artifact_id, ArtifactStatus.APPROVED, by=CD)
    assert run.artifact(artifact_id).status is ArtifactStatus.CANDIDATE


def test_egt_04_passed_qa_and_human_approve_allow_approval_and_publication() -> None:
    run = make_run()
    artifact_id = candidate_artifact(run)
    evaluate(run, artifact_id, PASSED)
    approve_by_human(run, artifact_id)
    run.transition_artifact(artifact_id, ArtifactStatus.APPROVED, by=CD)
    run.transition_artifact(artifact_id, ArtifactStatus.PUBLISHED, by=CD)
    assert run.artifact(artifact_id).status is ArtifactStatus.PUBLISHED


@pytest.mark.parametrize(
    ("first", "second", "approvable"),
    [
        (PASSED, EvaluationStatus.FLAGGED, False),
        (EvaluationStatus.FLAGGED, PASSED, True),
    ],
)
def test_egt_05_the_latest_evaluation_decides(
    first: EvaluationStatus, second: EvaluationStatus, approvable: bool
) -> None:
    run = make_run()
    artifact_id = candidate_artifact(run)
    evaluate(run, artifact_id, first, ("r",))
    evaluate(run, artifact_id, second, ("r",))
    approve_by_human(run, artifact_id)
    if approvable:
        run.transition_artifact(artifact_id, ArtifactStatus.APPROVED, by=CD)
        assert run.artifact(artifact_id).status is ArtifactStatus.APPROVED
    else:
        with pytest.raises(ArtifactQaNotPassedError):
            run.transition_artifact(artifact_id, ArtifactStatus.APPROVED, by=CD)


def test_egt_06_a_passed_evaluation_of_another_artifact_does_not_count() -> None:
    run = make_run()
    target = candidate_artifact(run)
    other = candidate_artifact(run)
    evaluate(run, other, PASSED)
    approve_by_human(run, target)
    with pytest.raises(ArtifactQaNotPassedError):
        run.transition_artifact(target, ArtifactStatus.APPROVED, by=CD)


def test_egt_07_passed_qa_does_not_replace_the_human_approve() -> None:
    run = make_run()
    artifact_id = candidate_artifact(run)
    evaluate(run, artifact_id, PASSED)
    with pytest.raises(ArtifactNotApprovedError):
        run.transition_artifact(artifact_id, ArtifactStatus.APPROVED, by=CD)


def test_egt_08_rejecting_a_candidate_needs_neither_review_nor_evaluation() -> None:
    run = make_run()
    artifact_id = candidate_artifact(run)
    run.transition_artifact(artifact_id, ArtifactStatus.REJECTED, by=CD)
    assert run.artifact(artifact_id).status is ArtifactStatus.REJECTED
