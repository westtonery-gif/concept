"""Behavioural tests for Artifact versioning — the rework path (ADR-0019).

Everything is driven **only through the Run aggregate root** (``create_artifact_version`` /
``transition_artifact`` and the read-only views). The central invariant — a fixed Artifact version
is immutable, an edit is a **new version** that supersedes its predecessor and starts its own
review/QA lifecycle (DOMAIN_MODEL.md §2.12, §6) — is exercised end to end, including the rework
loop that ADR-0018 deferred: a risk verdict on v1 -> a passing verdict on v2 -> approval.
No mocks, sleep or randomness.
"""

from __future__ import annotations

import pytest

from omemo_content_factory.domain.artifact import (
    ArtifactCreated,
    ArtifactNotApprovedError,
    ArtifactQaNotPassedError,
    ArtifactStatus,
    ArtifactSupersessionError,
    DuplicateArtifactError,
    InvalidArtifactTransitionError,
)
from omemo_content_factory.domain.errors import DomainError
from omemo_content_factory.domain.evaluation import EvaluationStatus
from omemo_content_factory.domain.human_review import ReviewStatus
from omemo_content_factory.domain.run import Actor, Run, UnauthorizedActorError
from omemo_content_factory.domain.task import TaskStatus

CD = Actor.CONTENT_DIRECTOR
REVIEWER = Actor.HUMAN_REVIEWER
WORKING_STATES = [ArtifactStatus.DRAFT, ArtifactStatus.CANDIDATE, ArtifactStatus.APPROVED]
TERMINAL_STATES = [ArtifactStatus.REJECTED, ArtifactStatus.PUBLISHED, ArtifactStatus.SUPERSEDED]


def make_run() -> Run:
    """A fresh Run in CREATED."""
    return Run.create(
        run_id="run-ver-0001", content_brief_ref="brief-0001", workflow_version_ref="workflow@v1"
    )


def output_on_run(run: Run, payload: str, step: str = "step-1") -> str:
    """Drive a Task to SUCCEEDED on ``run``, record its Output, and return the output id."""
    task_id = run.open_task(
        workflow_step_ref=step, agent_ref="writer@v1", task_input="brief", by=CD
    )
    run.transition_task(task_id, TaskStatus.RUNNING, by=CD)
    run.transition_task(task_id, TaskStatus.SUCCEEDED, by=CD)
    return run.record_output(task_id, payload=payload, schema_ref="s@v1", by=CD)


def approve_by_human(run: Run, artifact_id: str) -> None:
    """Open a Human Review on ``artifact_id`` and record the human's Approve (ADR-0007)."""
    review_id = run.open_human_review(artifact_id, by=CD)
    run.submit_review(review_id, ReviewStatus.APPROVED, by=REVIEWER)


def evaluate(run: Run, artifact_id: str, verdict: EvaluationStatus) -> None:
    """Open a QA Evaluation of ``artifact_id`` and record ``verdict`` (ADR-0018)."""
    evaluation_id = run.open_evaluation(artifact_id, kind="qa", by=CD)
    run.record_evaluation(evaluation_id, verdict, by=CD)


def artifact_in(run: Run, state: ArtifactStatus) -> str:
    """Create a first-version Artifact on ``run`` and drive it to ``state``; return its id."""
    artifact_id = run.create_artifact(output_on_run(run, "v1 content"), kind="script", by=CD)
    if state is ArtifactStatus.DRAFT:
        return artifact_id
    run.transition_artifact(artifact_id, ArtifactStatus.CANDIDATE, by=CD)
    if state is ArtifactStatus.CANDIDATE:
        return artifact_id
    if state is ArtifactStatus.REJECTED:
        run.transition_artifact(artifact_id, ArtifactStatus.REJECTED, by=CD)
        return artifact_id
    approve_by_human(run, artifact_id)
    evaluate(run, artifact_id, EvaluationStatus.PASSED)
    run.transition_artifact(artifact_id, ArtifactStatus.APPROVED, by=CD)
    if state is ArtifactStatus.APPROVED:
        return artifact_id
    if state is ArtifactStatus.PUBLISHED:
        run.transition_artifact(artifact_id, ArtifactStatus.PUBLISHED, by=CD)
        return artifact_id
    # SUPERSEDED is reachable only by creating the next version; the id asked for is the old one.
    run.create_artifact_version(artifact_id, output_on_run(run, "v2 content", step="step-2"), by=CD)
    return artifact_id


# --- Creating the next version -----------------------------------------------------------


def test_new_version_supersedes_its_predecessor_and_increments_the_version() -> None:
    """A new version replaces the previous one, which becomes SUPERSEDED (DOMAIN_MODEL.md §2.12)."""
    run = make_run()
    v1 = artifact_in(run, ArtifactStatus.CANDIDATE)
    output_v2 = output_on_run(run, "reworked content", step="step-2")

    v2 = run.create_artifact_version(v1, output_v2, by=CD)

    assert v2 != v1
    previous, current = run.artifact(v1), run.artifact(v2)
    assert previous.status is ArtifactStatus.SUPERSEDED
    assert previous.version == 1
    assert current.status is ArtifactStatus.DRAFT
    assert current.version == 2
    assert current.supersedes_ref == v1
    assert current.output_ref == output_v2
    assert current.content == "reworked content"
    assert current.run_id == run.run_id


def test_a_first_version_supersedes_nothing() -> None:
    """An Artifact created from an Output is version 1 with no predecessor (ADR-0019 §2)."""
    run = make_run()
    assert run.artifact(artifact_in(run, ArtifactStatus.DRAFT)).supersedes_ref is None


def test_a_new_version_inherits_the_kind_unless_one_is_given() -> None:
    """``kind`` defaults to the predecessor's and can be overridden for the rework (ADR-0019 §4)."""
    run = make_run()
    v1 = artifact_in(run, ArtifactStatus.CANDIDATE)
    v2 = run.create_artifact_version(v1, output_on_run(run, "v2", step="step-2"), by=CD)
    assert run.artifact(v2).kind == run.artifact(v1).kind == "script"

    v3 = run.create_artifact_version(
        v2, output_on_run(run, "v3", step="step-3"), by=CD, kind="edited"
    )
    assert run.artifact(v3).kind == "edited"


def test_versions_form_a_linear_chain() -> None:
    """Each version points at the one it replaced; only the last one is live (ADR-0019 §3)."""
    run = make_run()
    v1 = artifact_in(run, ArtifactStatus.CANDIDATE)
    v2 = run.create_artifact_version(v1, output_on_run(run, "v2", step="step-2"), by=CD)
    v3 = run.create_artifact_version(v2, output_on_run(run, "v3", step="step-3"), by=CD)

    chain = [run.artifact(a) for a in (v1, v2, v3)]
    assert [view.version for view in chain] == [1, 2, 3]
    assert [view.supersedes_ref for view in chain] == [None, v1, v2]
    assert [view.status for view in chain] == [
        ArtifactStatus.SUPERSEDED,
        ArtifactStatus.SUPERSEDED,
        ArtifactStatus.DRAFT,
    ]


def test_a_superseded_version_cannot_be_superseded_again() -> None:
    """A predecessor is terminal once replaced, so the chain cannot fork (ADR-0019 §3)."""
    run = make_run()
    v1 = artifact_in(run, ArtifactStatus.CANDIDATE)
    run.create_artifact_version(v1, output_on_run(run, "v2", step="step-2"), by=CD)

    with pytest.raises(InvalidArtifactTransitionError):
        run.create_artifact_version(v1, output_on_run(run, "fork", step="step-3"), by=CD)
    assert len(run.artifacts) == 2


@pytest.mark.parametrize("state", WORKING_STATES)
def test_any_working_version_can_be_superseded(state: ArtifactStatus) -> None:
    """DRAFT, CANDIDATE and APPROVED are all supersedable ("любой рабочий → Superseded")."""
    run = make_run()
    v1 = artifact_in(run, state)
    run.create_artifact_version(v1, output_on_run(run, "v2", step="step-9"), by=CD)
    assert run.artifact(v1).status is ArtifactStatus.SUPERSEDED


@pytest.mark.parametrize("state", TERMINAL_STATES)
def test_a_terminal_version_cannot_be_superseded(state: ArtifactStatus) -> None:
    """PUBLISHED / REJECTED / SUPERSEDED are terminal; no new version replaces them (§2.12)."""
    run = make_run()
    artifact_id = artifact_in(run, state)
    artifacts_before = len(run.artifacts)
    output_id = output_on_run(run, "rework", step="step-9")

    with pytest.raises(InvalidArtifactTransitionError):
        run.create_artifact_version(artifact_id, output_id, by=CD)
    assert run.artifact(artifact_id).status is state
    assert len(run.artifacts) == artifacts_before


def test_new_version_emits_artifact_created_carrying_the_supersession() -> None:
    """The Run log records the new version and what it replaced (ADR-0019 §5)."""
    run = make_run()
    v1 = artifact_in(run, ArtifactStatus.CANDIDATE)
    output_v2 = output_on_run(run, "v2", step="step-2")

    v2 = run.create_artifact_version(v1, output_v2, by=CD)

    last = run.events[-1]
    assert isinstance(last, ArtifactCreated)
    assert last.run_id == run.run_id
    assert last.artifact_id == v2
    assert last.output_ref == output_v2
    assert last.kind == "script"
    assert last.version == 2
    assert last.supersedes_ref == v1


def test_first_version_event_is_unchanged() -> None:
    """``ArtifactCreated`` for a first version keeps its defaults (additive event, ADR-0019 §5)."""
    run = make_run()
    artifact_id = artifact_in(run, ArtifactStatus.DRAFT)
    created = [event for event in run.events if isinstance(event, ArtifactCreated)]
    assert created == [
        ArtifactCreated(
            run_id=run.run_id,
            artifact_id=artifact_id,
            output_ref=run.artifact(artifact_id).output_ref,
            kind="script",
        )
    ]


# --- Guards ------------------------------------------------------------------------------


def test_superseded_is_not_reachable_through_transition_artifact() -> None:
    """A bare status change to SUPERSEDED is refused — there would be no successor (§4)."""
    run = make_run()
    artifact_id = artifact_in(run, ArtifactStatus.CANDIDATE)

    with pytest.raises(ArtifactSupersessionError):
        run.transition_artifact(artifact_id, ArtifactStatus.SUPERSEDED, by=CD)
    assert run.artifact(artifact_id).status is ArtifactStatus.CANDIDATE


def test_artifact_supersession_error_is_a_domain_error() -> None:
    """The new error is rooted at the shared DomainError (ADR-0017)."""
    assert issubclass(ArtifactSupersessionError, DomainError)


def test_only_the_content_director_can_create_a_new_version() -> None:
    """Versioning is a Content Director operation; nothing changes on refusal (ADR-0019 §4)."""
    run = make_run()
    v1 = artifact_in(run, ArtifactStatus.CANDIDATE)
    output_id = output_on_run(run, "v2", step="step-2")

    for actor in (Actor.AGENT, Actor.HUMAN_REVIEWER):
        with pytest.raises(UnauthorizedActorError):
            run.create_artifact_version(v1, output_id, by=actor)
    assert run.artifact(v1).status is ArtifactStatus.CANDIDATE
    assert len(run.artifacts) == 1


def test_a_new_version_needs_a_known_predecessor() -> None:
    """An unknown predecessor id is rejected as for any other owned child (ADR-0006 §5)."""
    run = make_run()
    with pytest.raises(KeyError):
        run.create_artifact_version("no-such-artifact", output_on_run(run, "v2"), by=CD)


def test_a_new_version_needs_an_existing_unused_output() -> None:
    """Output -> Artifact stays 1:1, and a failed call supersedes nothing (ADR-0019 §4)."""
    run = make_run()
    v1 = artifact_in(run, ArtifactStatus.CANDIDATE)

    with pytest.raises(KeyError):
        run.create_artifact_version(v1, "no-such-output", by=CD)
    assert run.artifact(v1).status is ArtifactStatus.CANDIDATE

    with pytest.raises(DuplicateArtifactError):
        run.create_artifact_version(v1, run.artifact(v1).output_ref, by=CD)
    assert run.artifact(v1).status is ArtifactStatus.CANDIDATE
    assert len(run.artifacts) == 1


# --- A new version starts a fresh review / QA lifecycle -----------------------------------


def test_a_new_version_does_not_inherit_the_predecessors_approval() -> None:
    """The human's Approve applies to the version he saw, not to its successor (ADR-0019 §6)."""
    run = make_run()
    v1 = artifact_in(run, ArtifactStatus.CANDIDATE)
    approve_by_human(run, v1)
    evaluate(run, v1, EvaluationStatus.PASSED)

    v2 = run.create_artifact_version(v1, output_on_run(run, "v2", step="step-2"), by=CD)
    run.transition_artifact(v2, ArtifactStatus.CANDIDATE, by=CD)

    with pytest.raises(ArtifactNotApprovedError):
        run.transition_artifact(v2, ArtifactStatus.APPROVED, by=CD)
    assert run.artifact(v2).status is ArtifactStatus.CANDIDATE


def test_a_new_version_does_not_inherit_the_predecessors_qa_verdict() -> None:
    """A reworked version needs its own passing QA verdict — fail closed (ADR-0018 §5)."""
    run = make_run()
    v1 = artifact_in(run, ArtifactStatus.CANDIDATE)
    evaluate(run, v1, EvaluationStatus.PASSED)

    v2 = run.create_artifact_version(v1, output_on_run(run, "v2", step="step-2"), by=CD)
    run.transition_artifact(v2, ArtifactStatus.CANDIDATE, by=CD)
    approve_by_human(run, v2)

    with pytest.raises(ArtifactQaNotPassedError):
        run.transition_artifact(v2, ArtifactStatus.APPROVED, by=CD)
    assert run.artifact(v2).status is ArtifactStatus.CANDIDATE


@pytest.mark.parametrize("risk", [EvaluationStatus.FLAGGED, EvaluationStatus.FAILED])
def test_rework_loop_risk_verdict_then_a_passing_new_version(risk: EvaluationStatus) -> None:
    """The loop ADR-0018 deferred: risk on v1 -> rework -> v2 passes QA and is approved."""
    run = make_run()
    v1 = artifact_in(run, ArtifactStatus.CANDIDATE)
    evaluate(run, v1, risk)
    approve_by_human(run, v1)
    # Fail closed: v1 is not approvable, whatever the human says.
    with pytest.raises(ArtifactQaNotPassedError):
        run.transition_artifact(v1, ArtifactStatus.APPROVED, by=CD)

    v2 = run.create_artifact_version(v1, output_on_run(run, "fixed content", step="step-2"), by=CD)
    run.transition_artifact(v2, ArtifactStatus.CANDIDATE, by=CD)
    evaluate(run, v2, EvaluationStatus.PASSED)
    approve_by_human(run, v2)
    run.transition_artifact(v2, ArtifactStatus.APPROVED, by=CD)
    run.transition_artifact(v2, ArtifactStatus.PUBLISHED, by=CD)

    assert run.artifact(v1).status is ArtifactStatus.SUPERSEDED
    assert run.artifact(v2).status is ArtifactStatus.PUBLISHED
    assert run.artifact(v2).version == 2
