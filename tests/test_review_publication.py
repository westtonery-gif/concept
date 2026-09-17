"""Tests for the Approval Gate and the publication of a pending review (ADR-0044).

Maps EVALUATION_ACCEPTANCE.md §4.5 (APG) and ADAPTER_ACCEPTANCE.md §11 (RPB). A real
``ContentDirector`` commits to a recording store; the desk is ``InMemoryReviewDesk`` or a double
around it that refuses on demand. Executors and the evaluator are deterministic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from omemo_content_factory.adapters.review_desk import (
    ReviewDecision,
    ReviewDesk,
    ReviewDeskError,
    ReviewPackage,
)
from omemo_content_factory.application.content_director import ContentDirector, TaskRequest
from omemo_content_factory.application.qa_evaluation import EvaluationResult
from omemo_content_factory.application.review_publication import (
    PublishedReview,
    pending_review_package,
    publish_pending_review,
)
from omemo_content_factory.application.schema_validation import SchemaBinding
from omemo_content_factory.application.task_execution import ExecutionResult
from omemo_content_factory.composition import build_review_desk
from omemo_content_factory.domain.artifact import ArtifactStatus
from omemo_content_factory.domain.evaluation import EvaluationStatus
from omemo_content_factory.domain.human_review import ReviewId, ReviewStatus
from omemo_content_factory.domain.run import Actor, Run, RunSnapshot, RunStatus
from omemo_content_factory.domain.schema import Schema, SchemaStatus, SchemaVersion
from omemo_content_factory.infrastructure.google_docs_review_desk import GoogleDocsReviewDesk
from omemo_content_factory.infrastructure.in_memory_adapters import InMemoryReviewDesk

RUN_ID = "run-rpb-0001"
BRIEF = "a brief about running shoes"
REQUESTS = [
    TaskRequest("research", "researcher@v1", BRIEF, artifact_kind="research"),
    TaskRequest("write", "writer@v1", "", artifact_kind="script"),
]
PASSED, FLAGGED, FAILED = (
    EvaluationStatus.PASSED,
    EvaluationStatus.FLAGGED,
    EvaluationStatus.FAILED,
)
_REVIEWER = Actor.HUMAN_REVIEWER


# --- Doubles ------------------------------------------------------------------------------


@dataclass
class Executor:
    """Succeeds with a structured Output and counts its calls."""

    calls: int = 0

    def execute(self, task_input: str) -> ExecutionResult:
        self.calls += 1
        return ExecutionResult(
            succeeded=True, output=f"[gen] {task_input}", schema_ref="s@v1", payload_fields={}
        )


@dataclass
class Evaluator:
    """Answers the verdicts in order, the last one from then on; a risk carries one flag."""

    verdicts: list[EvaluationStatus] = field(default_factory=lambda: [PASSED])
    evaluator_ref: str = "qa@v1"
    calls: int = 0

    def evaluate(self, content: str) -> EvaluationResult:
        self.calls += 1
        verdict = self.verdicts.pop(0) if len(self.verdicts) > 1 else self.verdicts[0]
        flags = () if verdict is PASSED else (f"risk in {content}",)
        return EvaluationResult(verdict=verdict, flags=flags)


class RecordingStore:
    """A ``RunStore`` keeping every saved snapshot in order."""

    def __init__(self) -> None:
        self.snapshots: list[RunSnapshot] = []

    def save(self, run: Run, /) -> None:
        self.snapshots.append(run.snapshot)

    def load(self, run_id: str, /) -> Run | None:
        return Run.restore(self.snapshots[-1]) if self.snapshots else None


class FlakyDesk:
    """An ``InMemoryReviewDesk`` that refuses to publish while ``down``."""

    def __init__(self) -> None:
        self.inner = InMemoryReviewDesk()
        self.down = True
        self.attempts = 0

    def publish(self, package: ReviewPackage, /) -> str:
        self.attempts += 1
        if self.down:
            raise ReviewDeskError("desk is down")
        return self.inner.publish(package)

    def fetch_decision(self, review_id: ReviewId, /) -> ReviewDecision | None:
        return self.inner.fetch_decision(review_id)


# --- Helpers ------------------------------------------------------------------------------


def _schema() -> Schema:
    schema = Schema.create(
        schema_id="s", version=SchemaVersion(1), description="d", required_fields=[]
    )
    schema.transition(SchemaStatus.ACTIVE)
    return schema


def _director(
    qa: Evaluator | None, store: RecordingStore, executor: Executor | None = None
) -> ContentDirector:
    schemas = {
        "researcher@v1": SchemaBinding("s@v1", _schema()),
        "writer@v1": SchemaBinding("s@v1", _schema()),
    }
    return ContentDirector(executor or Executor(), schemas, qa=qa, store=store)


def _produced(qa: Evaluator | None, store: RecordingStore | None = None) -> Run:
    run = Run.create(run_id=RUN_ID, content_brief_ref="brief-rpb", workflow_version_ref="rpb@v1")
    _director(qa, store or RecordingStore()).execute(run, REQUESTS)
    return run


def _decide(run: Run, decision: ReviewStatus, reason: str | None = None) -> None:
    pending = [view for view in run.human_reviews if view.status is ReviewStatus.PENDING]
    run.submit_review(pending[-1].review_id, decision, by=_REVIEWER, reason=reason)


# --- APG: the Approval Gate ---------------------------------------------------------------


def test_apg_01_a_passed_candidate_stops_at_the_gate_with_its_review_in_one_commit() -> None:
    store = RecordingStore()
    run = _produced(Evaluator(), store)

    assert run.status is RunStatus.WAITING_HUMAN
    script = run.artifacts[-1]
    [review] = run.human_reviews
    assert (review.artifact_ref, review.status) == (script.artifact_id, ReviewStatus.PENDING)
    assert script.status is ArtifactStatus.CANDIDATE
    waiting = [s for s in store.snapshots if s.status is RunStatus.WAITING_HUMAN]
    assert len(waiting) == 1 and len(waiting[0].human_reviews) == 1
    assert store.snapshots[-1] == run.snapshot


def test_apg_02_an_approve_on_a_passed_candidate_approves_it_and_completes_in_one_commit() -> None:
    store = RecordingStore()
    run = _produced(Evaluator(), store)
    _decide(run, ReviewStatus.APPROVED)
    saves = len(store.snapshots)
    executor, qa = Executor(), Evaluator()

    _director(qa, store, executor).resume(run, REQUESTS)

    assert run.status is RunStatus.COMPLETED
    assert run.artifacts[-1].status is ArtifactStatus.APPROVED
    assert len(store.snapshots) == saves + 1
    assert store.snapshots[-1] == run.snapshot
    assert (executor.calls, qa.calls) == (0, 0)


@pytest.mark.parametrize(
    ("verdicts", "decision"),
    [
        ([PASSED], None),
        ([PASSED], ReviewStatus.REJECTED),
        ([FLAGGED], ReviewStatus.APPROVED),
        ([FAILED], ReviewStatus.APPROVED),
        ([FLAGGED], ReviewStatus.REJECTED),
    ],
    ids=["pending", "rejected", "approved-flagged", "approved-failed", "rejected-flagged"],
)
def test_apg_03_any_other_state_of_the_gate_is_left_alone(
    verdicts: list[EvaluationStatus], decision: ReviewStatus | None
) -> None:
    run = _produced(Evaluator(verdicts))
    if decision is not None:
        _decide(run, decision, reason="no" if decision is ReviewStatus.REJECTED else None)
    before = run.snapshot
    store, executor, qa = RecordingStore(), Executor(), Evaluator()

    _director(qa, store, executor).resume(run, REQUESTS)

    assert run.snapshot == before
    assert run.status is RunStatus.WAITING_HUMAN
    assert (store.snapshots, executor.calls, qa.calls) == ([], 0, 0)


def test_apg_04_without_qa_the_legacy_route_still_completes_without_a_review() -> None:
    run = _produced(None)

    assert run.status is RunStatus.COMPLETED
    assert run.human_reviews == ()


def test_apg_05_a_reworked_version_that_passes_waits_for_approval_then_completes() -> None:
    store = RecordingStore()
    run = _produced(Evaluator([FLAGGED, PASSED]), store)
    _decide(run, ReviewStatus.CHANGES_REQUESTED, reason="sharper hook")
    _director(Evaluator(), store).resume(run, REQUESTS)

    v1, v2 = run.artifacts[-2:]
    assert (v2.version, v2.status) == (2, ArtifactStatus.CANDIDATE)
    assert run.snapshot.status is RunStatus.WAITING_HUMAN
    assert run.human_reviews[-1].artifact_ref == v2.artifact_id

    _decide(run, ReviewStatus.APPROVED)
    _director(Evaluator(), store).resume(run, REQUESTS)

    assert run.status is RunStatus.COMPLETED
    assert run.artifact(v2.artifact_id).status is ArtifactStatus.APPROVED
    assert run.artifact(v1.artifact_id).status is ArtifactStatus.SUPERSEDED


# --- RPB: publishing the pending review ---------------------------------------------------


@pytest.mark.parametrize(("verdict", "flagged"), [(PASSED, False), (FLAGGED, True), (FAILED, True)])
def test_rpb_01_the_pending_review_is_published_with_its_candidate_brief_and_flags(
    verdict: EvaluationStatus, flagged: bool
) -> None:
    run = _produced(Evaluator([verdict]))
    desk = InMemoryReviewDesk()
    before = run.snapshot

    published = publish_pending_review(run, desk)

    [review] = run.human_reviews
    script = run.artifacts[-1]
    assert published == PublishedReview(review.review_id, f"memory://reviews/{review.review_id}")
    package = desk.published(review.review_id)
    assert package == ReviewPackage(
        run_id=RUN_ID,
        review_id=review.review_id,
        candidate=script,
        brief=BRIEF,
        qa_flags=run.evaluations[-1].flags,
    )
    assert bool(package.qa_flags) is flagged
    assert run.snapshot == before


@pytest.mark.parametrize("state", ["completed", "decided", "waiting-qa", "running", "created"])
def test_rpb_02_a_run_not_waiting_on_a_pending_review_publishes_nothing(state: str) -> None:
    if state == "completed":
        run = _produced(None)
    elif state == "decided":
        run = _produced(Evaluator([FLAGGED]))
        _decide(run, ReviewStatus.CHANGES_REQUESTED, reason="more")
    else:
        run = Run.create(run_id=RUN_ID, content_brief_ref="b", workflow_version_ref="rpb@v1")
        if state != "created":
            run.transition(RunStatus.QUEUED, by=Actor.CONTENT_DIRECTOR)
            run.transition(RunStatus.RUNNING, by=Actor.CONTENT_DIRECTOR)
        if state == "waiting-qa":
            run.transition(RunStatus.WAITING_QA, by=Actor.CONTENT_DIRECTOR)
    desk = FlakyDesk()

    assert pending_review_package(run) is None
    assert publish_pending_review(run, desk) is None
    assert desk.attempts == 0


def test_rpb_03_publishing_again_or_after_a_restart_returns_the_same_place() -> None:
    store = RecordingStore()
    run = _produced(Evaluator(), store)
    desk = InMemoryReviewDesk()

    first = publish_pending_review(run, desk)
    restored = store.load(RUN_ID)
    assert restored is not None
    again = publish_pending_review(restored, desk)

    assert first is not None and first == again


def test_rpb_04_a_new_version_is_a_new_review_with_its_own_flags() -> None:
    store = RecordingStore()
    run = _produced(Evaluator([FLAGGED, PASSED]), store)
    desk = InMemoryReviewDesk()
    first = publish_pending_review(run, desk)
    assert first is not None
    _decide(run, ReviewStatus.CHANGES_REQUESTED, reason="sharper hook")
    assert publish_pending_review(run, desk) is None

    _director(Evaluator(), store).resume(run, REQUESTS)
    second = publish_pending_review(run, desk)

    assert second is not None and second.review_id != first.review_id
    v1_package, v2_package = desk.published(first.review_id), desk.published(second.review_id)
    assert v1_package is not None and v2_package is not None
    assert v1_package.qa_flags != () and v2_package.qa_flags == ()
    assert (v2_package.candidate.version, v2_package.candidate.supersedes_ref) == (
        2,
        v1_package.candidate.artifact_id,
    )
    assert v2_package.brief == BRIEF
    assert json.loads(run.tasks[-1].task_input)["human_instructions"] == "sharper hook"


def test_rpb_05_a_desk_outage_propagates_changes_nothing_and_the_next_call_publishes() -> None:
    run = _produced(Evaluator())
    desk = FlakyDesk()
    before = run.snapshot

    with pytest.raises(ReviewDeskError, match="desk is down"):
        publish_pending_review(run, desk)
    assert run.snapshot == before

    desk.down = False
    published = publish_pending_review(run, desk)
    assert published is not None
    assert desk.inner.published(published.review_id) is not None


def test_rpb_06_the_publisher_takes_any_review_desk() -> None:
    desk: ReviewDesk = FlakyDesk()
    assert publish_pending_review(_produced(None), desk) is None


def test_rpb_07_build_review_desk_fails_closed_or_builds_the_google_desk(tmp_path: Path) -> None:
    with pytest.raises(ReviewDeskError) as caught:
        build_review_desk({})
    assert "OMEMO_GOOGLE_SERVICE_ACCOUNT_FILE" in str(caught.value)
    assert "OMEMO_GOOGLE_REVIEW_FOLDER_ID" in str(caught.value)

    pem = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode("ascii")
    )
    key = tmp_path / "key.json"
    key.write_text(
        json.dumps(
            {
                "type": "service_account",
                "client_email": "factory@example.iam.gserviceaccount.com",
                "private_key": pem,
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        ),
        encoding="utf-8",
    )
    desk = build_review_desk(
        {
            "OMEMO_GOOGLE_SERVICE_ACCOUNT_FILE": str(key),
            "OMEMO_GOOGLE_REVIEW_FOLDER_ID": "folder-1",
        }
    )
    assert isinstance(desk, GoogleDocsReviewDesk)
