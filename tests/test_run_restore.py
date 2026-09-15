"""Behavioural tests for Run restoration: ``Run.snapshot`` / ``Run.restore`` (ADR-0015, ADR-0024).

Maps RUN_RESTORE_ACCEPTANCE.md (RST). Every snapshot is taken from a real Run driven through the
public API, the way the Storage Adapter takes it. Each reject case then breaks exactly one invariant
with ``dataclasses.replace`` and checks that the matching rule refuses it. No mocks, sleep or
randomness.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from omemo_content_factory.domain.artifact import (
    ArtifactNotApprovedError,
    ArtifactQaNotPassedError,
    ArtifactStatus,
)
from omemo_content_factory.domain.errors import DomainError
from omemo_content_factory.domain.evaluation import EvaluationStatus
from omemo_content_factory.domain.human_review import ReviewStatus
from omemo_content_factory.domain.run import (
    InvalidTransitionError,
    ReworkLimitExceededError,
    ReworkPolicy,
    Run,
    RunCompleted,
    RunCreated,
    RunDomainError,
    RunRestorationError,
    RunSnapshot,
    RunStatus,
)
from omemo_content_factory.domain.task import (
    TaskRetryLimitExceededError,
    TaskRetryPolicy,
    TaskStatus,
)
from tests.restorable_runs import (
    CD,
    REVIEWER,
    RUN_ID,
    V2,
    V2_REVIEW,
    busy_run,
    drive,
    evaluate,
    new_run,
    record_call,
    succeeded_task,
)

_CHILDREN = ("tasks", "artifacts", "human_reviews", "evaluations", "analytics_records")
_COUNTERS = ("task_seq", "artifact_seq", "review_seq", "evaluation_seq", "analytics_seq")


def restored(run: Run) -> Run:
    return Run.restore(run.snapshot)


def tamper(snapshot: RunSnapshot, **changes: Any) -> RunSnapshot:
    """``snapshot`` with ``changes`` applied, bypassing no check of ``Run.restore``."""
    return dataclasses.replace(snapshot, **changes)


def with_first(items: tuple[Any, ...], **changes: Any) -> tuple[Any, ...]:
    """``items`` with ``changes`` applied to its first element."""
    return (dataclasses.replace(items[0], **changes), *items[1:])


def pending_task_run() -> Run:
    """A Run in ``WAITING_HUMAN`` that still owns a ``PENDING`` Task."""
    run = new_run()
    drive(run, RunStatus.QUEUED, RunStatus.RUNNING)
    run.open_task("step", "script_writer@v1", "бриф", by=CD)
    drive(run, RunStatus.WAITING_QA, RunStatus.WAITING_HUMAN)
    return run


# --- 1. Round trip ------------------------------------------------------------------------


def test_rst_01_round_trip_preserves_the_whole_observable_truth() -> None:
    run = busy_run()
    run.submit_review(V2_REVIEW, ReviewStatus.APPROVED, by=REVIEWER)

    back = restored(run)

    assert (back.run_id, back.content_brief_ref, back.workflow_version_ref) == (
        run.run_id,
        run.content_brief_ref,
        run.workflow_version_ref,
    )
    assert (back.status, back.rework_count, back.failure_reason) == (
        RunStatus.WAITING_HUMAN,
        1,
        None,
    )
    assert back.tasks == run.tasks
    assert back.artifacts == run.artifacts
    assert back.human_reviews == run.human_reviews
    assert back.evaluations == run.evaluations
    assert back.analytics_records == run.analytics_records
    assert back.events == run.events
    assert back.snapshot == run.snapshot


def test_rst_10_policies_survive_the_round_trip() -> None:
    run = new_run(ReworkPolicy(max_rework_iterations=1))
    drive(run, RunStatus.QUEUED, RunStatus.RUNNING)
    task_id = run.open_task(
        "step", "script_writer@v1", "бриф", by=CD, retry_policy=TaskRetryPolicy(1)
    )
    run.transition_task(task_id, TaskStatus.RUNNING, by=CD)
    drive(run, RunStatus.WAITING_QA, RunStatus.RUNNING, RunStatus.WAITING_QA)

    back = restored(run)

    with pytest.raises(ReworkLimitExceededError):
        back.transition(RunStatus.RUNNING, by=CD)
    with pytest.raises(TaskRetryLimitExceededError):
        back.transition_task(task_id, TaskStatus.RUNNING, by=CD)


def test_rst_11_restore_emits_no_event() -> None:
    snapshot = busy_run().snapshot

    back = Run.restore(snapshot)

    assert back.events == snapshot.events
    assert sum(isinstance(event, RunCreated) for event in back.events) == 1


def test_rst_15_qa_verdicts_and_analytics_survive_the_round_trip() -> None:
    run = new_run()
    _, output_id = succeeded_task(run, step="script", payload="Сценарий.")
    artifact_id = run.create_artifact(output_id, kind="script", by=CD)
    run.transition_artifact(artifact_id, ArtifactStatus.CANDIDATE, by=CD)
    evaluate(run, artifact_id, EvaluationStatus.PASSED)
    evaluate(run, artifact_id, EvaluationStatus.FLAGGED, flags=("риск",))
    review_id = run.open_human_review(artifact_id, by=CD)
    run.submit_review(review_id, ReviewStatus.APPROVED, by=REVIEWER)

    back = restored(run)

    # The latest verdict is still the FLAGGED one: the fail-closed gate stays shut (ADR-0018).
    with pytest.raises(ArtifactQaNotPassedError):
        back.transition_artifact(artifact_id, ArtifactStatus.APPROVED, by=CD)
    assert restored(busy_run()).analytics_records == busy_run().analytics_records


def test_rst_16_snapshot_is_a_frozen_point_in_time_observation() -> None:
    run = busy_run()
    snapshot = run.snapshot
    assert run.snapshot == snapshot
    assert run.events == snapshot.events

    run.open_task("edit", "editor@v1", "бриф", by=CD)

    assert (len(snapshot.tasks), snapshot.task_seq) == (5, 5)
    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.status = RunStatus.FAILED  # type: ignore[misc]


# --- 2. Continuation ----------------------------------------------------------------------


def test_rst_02_continuation_hands_out_the_next_ids_and_overwrites_nothing() -> None:
    back = restored(busy_run())
    before = back.snapshot

    task_id, output_id = succeeded_task(back, step="edit", payload="Правка.")
    ids = (
        task_id,
        back.create_artifact(output_id, kind="script", by=CD),
        back.open_human_review(V2, by=CD),
        back.open_evaluation(V2, kind="qa", by=CD),
        record_call(back, task_id),
    )

    assert ids == tuple(
        f"{RUN_ID}-{kind}-{n}"
        for kind, n in (
            ("task", 6),
            ("artifact", 3),
            ("review", 3),
            ("evaluation", 3),
            ("analytics", 3),
        )
    )
    after = back.snapshot
    for field in _CHILDREN:
        old, new = getattr(before, field), getattr(after, field)
        assert new[: len(old)] == old
        assert len(new) == len(old) + 1


def test_rst_03_restored_run_advances_under_the_unchanged_contract() -> None:
    back = restored(busy_run())

    with pytest.raises(ArtifactNotApprovedError):
        back.transition_artifact(V2, ArtifactStatus.APPROVED, by=CD)
    with pytest.raises(InvalidTransitionError):
        back.transition(RunStatus.QUEUED, by=CD)

    back.submit_review(V2_REVIEW, ReviewStatus.APPROVED, by=REVIEWER)
    back.transition_artifact(V2, ArtifactStatus.APPROVED, by=CD)
    back.transition(RunStatus.COMPLETED, by=CD)

    assert back.status is RunStatus.COMPLETED
    assert back.events[-1] == RunCompleted(run_id=RUN_ID)


def test_rst_12_completion_guard_holds_after_restore() -> None:
    back = restored(pending_task_run())

    with pytest.raises(InvalidTransitionError):
        back.transition(RunStatus.COMPLETED, by=CD)


# --- 3. Terminality -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        (
            RunStatus.QUEUED,
            RunStatus.RUNNING,
            RunStatus.WAITING_QA,
            RunStatus.WAITING_HUMAN,
            RunStatus.COMPLETED,
        ),
        (RunStatus.FAILED,),
    ],
)
def test_rst_04_terminal_stays_terminal(path: tuple[RunStatus, ...]) -> None:
    run = new_run()
    drive(run, *path)

    back = restored(run)

    assert (back.status, back.failure_reason) == (run.status, run.failure_reason)
    for target in RunStatus:
        with pytest.raises(InvalidTransitionError):
            back.transition(target, by=CD)


# --- 4. verify/reject ---------------------------------------------------------------------


def test_restoration_error_is_a_run_domain_error() -> None:
    assert issubclass(RunRestorationError, RunDomainError)
    assert issubclass(RunRestorationError, DomainError)


@pytest.mark.parametrize("field", [*_CHILDREN, "events"])
def test_rst_05_a_child_or_journal_entry_of_another_run_is_refused(field: str) -> None:
    snapshot = busy_run().snapshot

    with pytest.raises(RunRestorationError, match="another Run"):
        Run.restore(tamper(snapshot, **{field: with_first(getattr(snapshot, field), run_id="r-9")}))


@pytest.mark.parametrize(
    ("field", "changes", "message"),
    [
        ("artifacts", {"output_ref": "nowhere"}, "Output this Run does not own"),  # RST-06
        ("human_reviews", {"artifact_ref": "nowhere"}, "Artifact this Run does not own"),  # RST-07
        ("evaluations", {"artifact_ref": "nowhere"}, "Artifact this Run does not own"),
        ("artifacts", {"supersedes_ref": "nowhere"}, "Artifact this Run does not own"),
        ("analytics_records", {"task_id": "nowhere"}, "owned Task and its role"),
        ("analytics_records", {"agent_ref": "someone@v1"}, "owned Task and its role"),
    ],
)
def test_rst_06_07_17_a_reference_to_a_child_this_run_does_not_own_is_refused(
    field: str, changes: dict[str, str], message: str
) -> None:
    snapshot = busy_run().snapshot

    with pytest.raises(RunRestorationError, match=message):
        Run.restore(tamper(snapshot, **{field: with_first(getattr(snapshot, field), **changes)}))


@pytest.mark.parametrize("counter", _COUNTERS)
def test_rst_08_a_counter_below_the_number_of_children_is_refused(counter: str) -> None:
    snapshot = busy_run().snapshot

    with pytest.raises(RunRestorationError, match="counter"):
        Run.restore(tamper(snapshot, **{counter: getattr(snapshot, counter) - 1}))


def test_rst_19_a_counter_below_an_id_already_used_is_refused() -> None:
    run = new_run()
    for _ in range(3):
        run.open_task("step", "script_writer@v1", "бриф", by=CD)
    snapshot = run.snapshot
    only_the_third = snapshot.tasks[2:]

    with pytest.raises(RunRestorationError, match="counter"):
        Run.restore(tamper(snapshot, tasks=only_the_third, task_seq=2))

    back = Run.restore(tamper(snapshot, tasks=only_the_third, task_seq=3))
    assert back.open_task("step", "script_writer@v1", "бриф", by=CD) == f"{RUN_ID}-task-4"


@pytest.mark.parametrize("field", ["run_id", "content_brief_ref", "workflow_version_ref"])
@pytest.mark.parametrize("blank", ["", "  "])
def test_rst_09_a_missing_fixed_input_is_refused(field: str, blank: str) -> None:
    with pytest.raises(RunRestorationError, match="non-blank"):
        Run.restore(tamper(busy_run().snapshot, **{field: blank}))


def test_rst_13_an_output_held_by_another_task_is_refused() -> None:
    snapshot = busy_run().snapshot
    research = snapshot.tasks[0]
    assert research.output is not None
    foreign = dataclasses.replace(research.output, task_id=f"{RUN_ID}-task-9")

    with pytest.raises(RunRestorationError, match="another Task's Output"):
        Run.restore(tamper(snapshot, tasks=with_first(snapshot.tasks, output=foreign)))


def test_rst_18_ids_are_unique_and_output_to_artifact_stays_one_to_one() -> None:
    snapshot = busy_run().snapshot
    v1, v2 = snapshot.artifacts

    with pytest.raises(RunRestorationError, match="twice"):
        Run.restore(tamper(snapshot, tasks=(*snapshot.tasks, snapshot.tasks[0])))
    with pytest.raises(RunRestorationError, match="1:1"):
        Run.restore(
            tamper(snapshot, artifacts=(v1, dataclasses.replace(v2, output_ref=v1.output_ref)))
        )
    with pytest.raises(RunRestorationError, match="did not succeed"):
        Run.restore(tamper(snapshot, tasks=with_first(snapshot.tasks, status=TaskStatus.RUNNING)))


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"rework_count": 3}, "rework_count"),
        ({"rework_count": -1}, "rework_count"),
        ({"status": "waiting_human"}, "RunStatus"),
    ],
)
def test_rst_20_run_state_outside_its_bounds_is_refused(
    changes: dict[str, Any], message: str
) -> None:
    with pytest.raises(RunRestorationError, match=message):
        Run.restore(tamper(busy_run().snapshot, **changes))


def test_rst_20_attempts_outside_the_retry_policy_are_refused() -> None:
    snapshot = busy_run().snapshot

    with pytest.raises(RunRestorationError, match="attempts"):
        Run.restore(tamper(snapshot, tasks=with_first(snapshot.tasks, attempt_count=3)))


def test_rst_21_a_completed_run_with_an_unfinished_task_is_refused() -> None:
    snapshot = tamper(pending_task_run().snapshot, status=RunStatus.COMPLETED)

    with pytest.raises(RunRestorationError, match="COMPLETED"):
        Run.restore(snapshot)


# --- 5. Boundaries ------------------------------------------------------------------------


def test_rst_14_restore_neither_replays_nor_transitions() -> None:
    run = new_run()
    drive(run, RunStatus.QUEUED, RunStatus.RUNNING)
    snapshot = run.snapshot

    back = Run.restore(snapshot)

    assert back.status is RunStatus.RUNNING
    assert len(back.events) == len(snapshot.events) == 3
