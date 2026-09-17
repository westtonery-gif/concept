"""Tests for reading the reviewer's decision from the review desk into the Run (ADR-0045).

Maps ADAPTER_ACCEPTANCE.md §12 (RDF). A real ``ContentDirector`` commits to a recording store; the
desk is ``InMemoryReviewDesk`` (its control side ``decide`` plays the reviewer) or a double around
it that refuses on demand. Executors and the evaluator are deterministic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from omemo_content_factory.adapters.review_desk import (
    ReviewDecision,
    ReviewDeskError,
    ReviewPackage,
)
from omemo_content_factory.application.content_director import ContentDirector, TaskRequest
from omemo_content_factory.application.qa_evaluation import EvaluationResult
from omemo_content_factory.application.review_decision import (
    FetchedDecision,
    apply_review_decision,
    take_review_decision,
)
from omemo_content_factory.application.review_publication import publish_pending_review
from omemo_content_factory.application.schema_validation import SchemaBinding
from omemo_content_factory.application.task_execution import ExecutionResult
from omemo_content_factory.domain.artifact import ArtifactStatus
from omemo_content_factory.domain.evaluation import EvaluationStatus
from omemo_content_factory.domain.human_review import ReviewId, ReviewStatus
from omemo_content_factory.domain.run import Actor, Run, RunSnapshot, RunStatus
from omemo_content_factory.domain.schema import Schema, SchemaStatus, SchemaVersion
from omemo_content_factory.infrastructure.in_memory_adapters import InMemoryReviewDesk

RUN_ID = "run-rdf-0001"
REQUESTS = [
    TaskRequest("research", "researcher@v1", "a brief", artifact_kind="research"),
    TaskRequest("write", "writer@v1", "", artifact_kind="script"),
]
PASSED, FLAGGED = EvaluationStatus.PASSED, EvaluationStatus.FLAGGED


@dataclass
class Executor:
    calls: int = 0

    def execute(self, task_input: str) -> ExecutionResult:
        self.calls += 1
        return ExecutionResult(
            succeeded=True, output=f"[gen] {task_input}", schema_ref="s@v1", payload_fields={}
        )


@dataclass
class Evaluator:
    verdicts: list[EvaluationStatus] = field(default_factory=lambda: [PASSED])
    evaluator_ref: str = "qa@v1"

    def evaluate(self, content: str) -> EvaluationResult:
        verdict = self.verdicts.pop(0) if len(self.verdicts) > 1 else self.verdicts[0]
        return EvaluationResult(verdict, () if verdict is PASSED else ("risk",))


class RecordingStore:
    def __init__(self) -> None:
        self.snapshots: list[RunSnapshot] = []

    def save(self, run: Run, /) -> None:
        self.snapshots.append(run.snapshot)

    def load(self, run_id: str, /) -> Run | None:
        return Run.restore(self.snapshots[-1]) if self.snapshots else None


class CountingDesk:
    """An ``InMemoryReviewDesk`` that counts reads and refuses them while ``down``."""

    def __init__(self) -> None:
        self.inner = InMemoryReviewDesk()
        self.down = False
        self.fetches = 0

    def publish(self, package: ReviewPackage, /) -> str:
        return self.inner.publish(package)

    def fetch_decision(self, review_id: ReviewId, /) -> ReviewDecision | None:
        self.fetches += 1
        if self.down:
            raise ReviewDeskError("desk is down")
        return self.inner.fetch_decision(review_id)


def _schema() -> Schema:
    schema = Schema.create(
        schema_id="s", version=SchemaVersion(1), description="d", required_fields=[]
    )
    schema.transition(SchemaStatus.ACTIVE)
    return schema


def _director(store: RecordingStore, executor: Executor | None = None) -> ContentDirector:
    binding = SchemaBinding("s@v1", _schema())
    return ContentDirector(
        executor or Executor(),
        {"researcher@v1": binding, "writer@v1": binding},
        qa=Evaluator(),
        store=store,
    )


def _published(
    verdict: EvaluationStatus = PASSED,
) -> tuple[Run, CountingDesk, RecordingStore, ReviewId]:
    """A Run waiting at the Approval Gate with its review published on a fresh desk."""
    store = RecordingStore()
    run = Run.create(run_id=RUN_ID, content_brief_ref="brief-rdf", workflow_version_ref="rdf@v1")
    binding = SchemaBinding("s@v1", _schema())
    ContentDirector(
        Executor(),
        {"researcher@v1": binding, "writer@v1": binding},
        qa=Evaluator([verdict]),
        store=store,
    ).execute(run, REQUESTS)
    desk = CountingDesk()
    published = publish_pending_review(run, desk)
    assert published is not None
    return run, desk, store, published.review_id


def test_rdf_01_an_undecided_review_leaves_the_run_unchanged() -> None:
    run, desk, _, _ = _published()
    before = run.snapshot

    assert apply_review_decision(run, desk) is None
    assert run.snapshot == before
    assert desk.fetches == 1


def test_rdf_02_an_approval_of_a_passed_candidate_is_recorded_and_completes_on_resume() -> None:
    run, desk, store, review_id = _published()
    desk.inner.decide(review_id, ReviewDecision(ReviewStatus.APPROVED))

    fetched = apply_review_decision(run, desk)

    assert fetched == FetchedDecision(review_id, ReviewStatus.APPROVED, None, True)
    review = run.human_review(review_id)
    assert (review.status, review.decided_by) == (
        ReviewStatus.APPROVED,
        Actor.HUMAN_REVIEWER.value,
    )
    store.save(run)
    _director(store).resume(run, REQUESTS)
    assert run.status is RunStatus.COMPLETED
    assert run.artifacts[-1].status is ArtifactStatus.APPROVED


@pytest.mark.parametrize("decision", [ReviewStatus.CHANGES_REQUESTED, ReviewStatus.REJECTED])
def test_rdf_03_changes_or_a_rejection_are_recorded_with_the_reason_and_reworked(
    decision: ReviewStatus,
) -> None:
    run, desk, store, review_id = _published(FLAGGED)
    desk.inner.decide(review_id, ReviewDecision(decision, reason="shorter, please"))

    fetched = apply_review_decision(run, desk)

    assert fetched == FetchedDecision(review_id, decision, "shorter, please", True)
    assert run.human_review(review_id).reason == "shorter, please"
    executor = Executor()
    _director(store, executor).resume(run, REQUESTS)
    assert executor.calls == 1
    rework_input = json.loads(run.tasks[-1].task_input)
    assert rework_input["human_decision"] == decision.value
    assert rework_input["human_instructions"] == "shorter, please"
    assert run.status is RunStatus.WAITING_HUMAN
    assert run.artifacts[-1].version == 2
    assert run.human_reviews[-1].status is ReviewStatus.PENDING
    assert run.human_reviews[-1].review_id != review_id


def test_rdf_04_an_approval_the_qa_gate_would_refuse_is_not_recorded() -> None:
    run, desk, _, review_id = _published(FLAGGED)
    desk.inner.decide(review_id, ReviewDecision(ReviewStatus.APPROVED))
    before = run.snapshot

    fetched = apply_review_decision(run, desk)

    assert fetched == FetchedDecision(review_id, ReviewStatus.APPROVED, None, False)
    assert run.snapshot == before
    assert run.human_review(review_id).status is ReviewStatus.PENDING


@pytest.mark.parametrize("state", ["completed", "waiting-qa", "created", "decided"])
def test_rdf_05_a_run_without_a_pending_review_never_calls_the_desk(state: str) -> None:
    desk = CountingDesk()
    if state == "decided":
        run, desk, _, review_id = _published(FLAGGED)
        run.submit_review(review_id, ReviewStatus.CHANGES_REQUESTED, by=Actor.HUMAN_REVIEWER)
    else:
        run = Run.create(run_id=RUN_ID, content_brief_ref="b", workflow_version_ref="rdf@v1")
        if state == "created":
            pass
        else:
            run.transition(RunStatus.QUEUED, by=Actor.CONTENT_DIRECTOR)
            run.transition(RunStatus.RUNNING, by=Actor.CONTENT_DIRECTOR)
            run.transition(RunStatus.WAITING_QA, by=Actor.CONTENT_DIRECTOR)
            if state == "completed":
                run.transition(RunStatus.WAITING_HUMAN, by=Actor.CONTENT_DIRECTOR)
                run.transition(RunStatus.COMPLETED, by=Actor.CONTENT_DIRECTOR)

    assert apply_review_decision(run, desk) is None
    assert desk.fetches == 0


def test_rdf_06_a_desk_outage_propagates_and_changes_nothing() -> None:
    run, desk, _, review_id = _published()
    desk.inner.decide(review_id, ReviewDecision(ReviewStatus.APPROVED))
    desk.down = True
    before = run.snapshot

    with pytest.raises(ReviewDeskError, match="desk is down"):
        apply_review_decision(run, desk)
    assert run.snapshot == before


def test_rdf_07_a_decision_is_read_after_a_restart_and_only_once() -> None:
    _, desk, store, review_id = _published()
    desk.inner.decide(review_id, ReviewDecision(ReviewStatus.APPROVED))
    restored = store.load(RUN_ID)
    assert restored is not None

    fetched = apply_review_decision(restored, desk)
    assert fetched is not None and fetched.applied
    fetches = desk.fetches

    assert apply_review_decision(restored, desk) is None
    assert desk.fetches == fetches


def _stored_waiting(verdict: EvaluationStatus = PASSED) -> tuple[RecordingStore, ReviewId]:
    """A stored Run waiting at the Approval Gate whose review was never published (a crash)."""
    store = RecordingStore()
    run = Run.create(run_id=RUN_ID, content_brief_ref="brief-rdf", workflow_version_ref="rdf@v1")
    binding = SchemaBinding("s@v1", _schema())
    ContentDirector(
        Executor(),
        {"researcher@v1": binding, "writer@v1": binding},
        qa=Evaluator([verdict]),
        store=store,
    ).execute(run, REQUESTS)
    return store, run.human_reviews[-1].review_id


def test_rdf_08_taking_a_decision_without_a_stored_run_never_calls_the_desk() -> None:
    store, desk = RecordingStore(), CountingDesk()

    assert take_review_decision(store, desk, RUN_ID) is None
    assert (desk.fetches, store.snapshots) == (0, [])


def test_rdf_08_taking_a_decision_publishes_first_and_saves_nothing_while_undecided() -> None:
    store, review_id = _stored_waiting()
    desk = CountingDesk()
    saves = len(store.snapshots)

    assert take_review_decision(store, desk, RUN_ID) is None
    assert desk.inner.published(review_id) is not None
    assert desk.fetches == 1
    assert len(store.snapshots) == saves


def test_rdf_08_a_recorded_decision_is_saved_once_and_a_refused_one_is_not() -> None:
    store, review_id = _stored_waiting()
    desk = CountingDesk()
    take_review_decision(store, desk, RUN_ID)
    desk.inner.decide(review_id, ReviewDecision(ReviewStatus.APPROVED))
    saves = len(store.snapshots)

    fetched = take_review_decision(store, desk, RUN_ID)

    assert fetched == FetchedDecision(review_id, ReviewStatus.APPROVED, None, True)
    assert len(store.snapshots) == saves + 1
    saved = store.load(RUN_ID)
    assert saved is not None and saved.human_review(review_id).status is ReviewStatus.APPROVED

    flagged_store, flagged_review = _stored_waiting(FLAGGED)
    flagged_desk = CountingDesk()
    take_review_decision(flagged_store, flagged_desk, RUN_ID)
    flagged_desk.inner.decide(flagged_review, ReviewDecision(ReviewStatus.APPROVED))
    before = list(flagged_store.snapshots)

    refused = take_review_decision(flagged_store, flagged_desk, RUN_ID)

    assert refused is not None and not refused.applied
    assert flagged_store.snapshots == before


def test_rdf_08_a_desk_outage_while_taking_a_decision_saves_nothing() -> None:
    store, review_id = _stored_waiting()
    desk = CountingDesk()
    take_review_decision(store, desk, RUN_ID)
    desk.inner.decide(review_id, ReviewDecision(ReviewStatus.APPROVED))
    desk.down = True
    before = list(store.snapshots)

    with pytest.raises(ReviewDeskError, match="desk is down"):
        take_review_decision(store, desk, RUN_ID)
    assert store.snapshots == before
