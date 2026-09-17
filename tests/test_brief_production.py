"""Tests for one brief invocation and the waiting-brief sweep list (ADR-0048).

Maps ADAPTER_ACCEPTANCE.md §13 (BPR). A real ``ContentDirector`` with deterministic executors and
evaluator commits through ``BriefStatusReporter`` over a real ``SqliteRunStore``; the board is
``InMemoryBriefBoard``, the desk ``InMemoryReviewDesk`` wrapped so that it counts calls and can
refuse reads or publications. "A new process" is a new ``BriefProduction`` over the same file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from omemo_content_factory.adapters.brief_board import IncomingBrief
from omemo_content_factory.adapters.review_desk import (
    ReviewDecision,
    ReviewDeskError,
    ReviewPackage,
)
from omemo_content_factory.adapters.run_store import RunIndex
from omemo_content_factory.application.brief_intake import BriefIntakeError
from omemo_content_factory.application.brief_production import (
    BriefInvocation,
    BriefProduction,
    run_id_for_brief,
)
from omemo_content_factory.application.brief_status import BriefStatusReporter
from omemo_content_factory.application.content_director import ContentDirector
from omemo_content_factory.application.qa_evaluation import EvaluationResult, QaCallError
from omemo_content_factory.application.review_decision import FetchedDecision
from omemo_content_factory.application.schema_validation import SchemaBinding
from omemo_content_factory.application.task_execution import ExecutionResult
from omemo_content_factory.composition import RUN_STORE_PATH_VAR, build_run_index
from omemo_content_factory.domain.evaluation import EvaluationStatus
from omemo_content_factory.domain.human_review import ReviewId, ReviewStatus
from omemo_content_factory.domain.run import Actor, Run, RunStatus
from omemo_content_factory.domain.schema import Schema, SchemaStatus, SchemaVersion
from omemo_content_factory.domain.workflow import Workflow, WorkflowStep
from omemo_content_factory.infrastructure.in_memory_adapters import (
    InMemoryBriefBoard,
    InMemoryReviewDesk,
)
from omemo_content_factory.infrastructure.sqlite_run_store import SqliteRunStore

BRIEF = IncomingBrief(brief_ref="brief-bpr", body="a brief")
RUN_ID = run_id_for_brief(BRIEF.brief_ref)
WORKFLOW = Workflow.create(
    workflow_id="bpr@v1",
    name="Research -> Write",
    steps=[
        WorkflowStep(step_id="research", task_type="t", agent_ref="researcher@v1", schema_ref="s"),
        WorkflowStep(step_id="write", task_type="t", agent_ref="writer@v1", schema_ref="s"),
    ],
)
PASSED, FLAGGED = EvaluationStatus.PASSED, EvaluationStatus.FLAGGED
Q, RN, WQ, WH, C = (
    RunStatus.QUEUED,
    RunStatus.RUNNING,
    RunStatus.WAITING_QA,
    RunStatus.WAITING_HUMAN,
    RunStatus.COMPLETED,
)


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
    verdicts: list[EvaluationStatus | None] = field(default_factory=lambda: [PASSED])
    evaluator_ref: str = "qa@v1"
    calls: int = 0

    def evaluate(self, content: str) -> EvaluationResult:
        self.calls += 1
        verdict = self.verdicts.pop(0) if len(self.verdicts) > 1 else self.verdicts[0]
        if verdict is None:
            raise QaCallError("model unavailable")
        return EvaluationResult(verdict, () if verdict is PASSED else ("risk",))


class Desk:
    """``InMemoryReviewDesk`` that counts calls and refuses reads / publications on demand."""

    def __init__(self) -> None:
        self.inner = InMemoryReviewDesk()
        self.reads_down = False
        self.publishing_down = False
        self.calls = 0

    def publish(self, package: ReviewPackage, /) -> str:
        self.calls += 1
        if self.publishing_down:
            raise ReviewDeskError("cannot publish")
        return self.inner.publish(package)

    def fetch_decision(self, review_id: ReviewId, /) -> ReviewDecision | None:
        self.calls += 1
        if self.reads_down:
            raise ReviewDeskError("cannot read")
        return self.inner.fetch_decision(review_id)


class Board(InMemoryBriefBoard):
    """The in-memory board, counting every write to a brief."""

    writes = 0

    def report_status(self, brief_ref: str, /, *, run_id: str, status: RunStatus) -> None:
        self.writes += 1
        super().report_status(brief_ref, run_id=run_id, status=status)

    def report_review_location(self, brief_ref: str, /, *, run_id: str, location: str) -> None:
        self.writes += 1
        super().report_review_location(brief_ref, run_id=run_id, location=location)

    def shown(self) -> list[RunStatus]:
        return [status for _, status in self.reports(BRIEF.brief_ref)]

    def links(self) -> list[str]:
        return [location for _, location in self.review_locations(BRIEF.brief_ref)]


@dataclass
class World:
    """What outlives a process: the database file, the board and the desk."""

    path: Path
    board: Board = field(default_factory=Board)
    desk: Desk | None = field(default_factory=Desk)
    executor: Executor = field(default_factory=Executor)
    evaluator: Evaluator = field(default_factory=Evaluator)

    def production(self) -> BriefProduction:
        """A new process over the same world."""
        sqlite = SqliteRunStore(self.path)
        store = BriefStatusReporter(sqlite, self.board)
        binding = SchemaBinding("s@v1", _schema())
        director = ContentDirector(
            self.executor,
            {"researcher@v1": binding, "writer@v1": binding},
            qa=self.evaluator,
            store=store,
        )
        return BriefProduction(director, store, self.board, WORKFLOW, desk=self.desk, index=sqlite)

    def stored(self, run_id: str = RUN_ID) -> Run | None:
        return SqliteRunStore(self.path).load(run_id)


def _schema() -> Schema:
    schema = Schema.create(
        schema_id="s", version=SchemaVersion(1), description="d", required_fields=[]
    )
    schema.transition(SchemaStatus.ACTIVE)
    return schema


def _world(tmp_path: Path, *verdicts: EvaluationStatus | None, desk: bool = True) -> World:
    world = World(tmp_path / "runs.sqlite3", desk=Desk() if desk else None)
    world.evaluator = Evaluator(list(verdicts) or [PASSED])
    world.board.put(BRIEF)
    return world


def _decide(world: World, invocation: BriefInvocation, decision: ReviewDecision) -> ReviewId:
    assert world.desk is not None and invocation.published is not None
    world.desk.inner.decide(invocation.published.review_id, decision)
    return invocation.published.review_id


# --- BPR-01/02: a ready brief is produced, published and linked; an unready one does nothing --


def test_bpr_01_a_ready_brief_is_produced_published_and_linked(tmp_path: Path) -> None:
    world = _world(tmp_path)

    invocation = world.production().invoke(BRIEF.brief_ref)

    run = invocation.run
    assert run is not None and run.status is WH
    assert (invocation.brief_ref, invocation.run_id) == (BRIEF.brief_ref, RUN_ID)
    assert RUN_ID == "run-notion-brief-bpr"
    [review] = run.human_reviews
    assert invocation.published is not None
    assert invocation.published.review_id == review.review_id
    assert invocation.published.location == f"memory://reviews/{review.review_id}"
    assert (invocation.decision, invocation.decision_error) == (None, None)
    assert (invocation.qa_error, invocation.publish_error) == (None, None)
    assert world.board.shown() == [Q, RN, WQ, WH]
    assert world.board.links() == [invocation.published.location]
    stored = world.stored()
    assert stored is not None and stored.snapshot == run.snapshot


@pytest.mark.parametrize("filed", ["unknown", "not-ready"])
def test_bpr_02_a_brief_that_is_not_producible_starts_nothing(tmp_path: Path, filed: str) -> None:
    world = _world(tmp_path)
    ref = "brief-unknown" if filed == "unknown" else BRIEF.brief_ref
    if filed == "not-ready":
        world.board.put(BRIEF, ready=False)

    invocation = world.production().invoke(ref)

    assert invocation.run is None
    assert invocation.published is None and invocation.decision is None
    assert world.stored(run_id_for_brief(ref)) is None
    assert world.executor.calls == 0
    assert world.desk is not None and world.desk.calls == 0
    assert world.board.writes == 0


# --- BPR-03/04: a decision on the desk is taken; nothing new writes nothing ----------------


def test_bpr_03_an_approval_on_the_desk_completes_the_run_in_the_next_process(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    first = world.production().invoke(BRIEF.brief_ref)
    review_id = _decide(world, first, ReviewDecision(ReviewStatus.APPROVED))

    done = world.production().invoke(BRIEF.brief_ref)

    assert done.decision == FetchedDecision(review_id, ReviewStatus.APPROVED, None, True)
    assert done.run is not None and done.run.status is C
    assert done.published is None
    assert world.board.shown() == [Q, RN, WQ, WH, C]
    assert world.board.links() == [f"memory://reviews/{review_id}"]
    assert world.executor.calls == 2


def test_bpr_03_changes_requested_rework_publishes_and_links_the_new_version(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path, FLAGGED, PASSED)
    first = world.production().invoke(BRIEF.brief_ref)
    _decide(world, first, ReviewDecision(ReviewStatus.CHANGES_REQUESTED, "sharper"))

    again = world.production().invoke(BRIEF.brief_ref)

    assert again.decision is not None and again.decision.applied
    assert again.run is not None and again.run.status is WH
    assert again.published is not None and again.published != first.published
    assert again.run.artifact(again.run.human_reviews[-1].artifact_ref).version == 2
    assert first.published is not None
    assert world.board.links() == [first.published.location, again.published.location]


def test_bpr_04_an_invocation_with_nothing_new_calls_no_executor_and_writes_nothing(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    first = world.production()
    first.invoke(BRIEF.brief_ref)
    writes, calls = world.board.writes, world.executor.calls

    for _ in range(3):
        first.invoke(BRIEF.brief_ref)

    assert (world.board.writes, world.executor.calls) == (writes, calls)
    assert world.evaluator.calls == 1

    restarted = world.production()
    restarted.invoke(BRIEF.brief_ref)
    restarted.invoke(BRIEF.brief_ref)
    assert world.board.writes == writes + 2  # a new process shows status and link once again
    assert world.executor.calls == calls


# --- BPR-05/06/07: refusals are reported; QA without a verdict; no desk ---------------------


def test_bpr_05_desk_refusals_are_reported_and_the_run_keeps_waiting(tmp_path: Path) -> None:
    world = _world(tmp_path)
    world.production().invoke(BRIEF.brief_ref)
    before = world.stored()
    assert world.desk is not None and before is not None
    world.desk.reads_down = True
    world.desk.publishing_down = True

    invocation = world.production().invoke(BRIEF.brief_ref)

    assert invocation.decision_error == "cannot publish"  # take_review_decision publishes first
    assert invocation.publish_error == "cannot publish"
    assert invocation.decision is None and invocation.published is None
    assert invocation.run is not None and invocation.run.snapshot == before.snapshot

    world.desk.publishing_down = False
    reading = world.production().invoke(BRIEF.brief_ref)
    assert reading.decision_error == "cannot read"
    assert reading.publish_error is None and reading.published is not None


def test_bpr_06_qa_without_a_verdict_is_reported_and_the_run_waits_at_waiting_qa(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path, None, PASSED)

    invocation = world.production().invoke(BRIEF.brief_ref)

    assert invocation.qa_error == "QaCallError: model unavailable"
    assert invocation.run is not None and invocation.run.status is WQ
    assert invocation.published is None
    assert world.board.shown() == [Q, RN, WQ]

    again = world.production().invoke(BRIEF.brief_ref)
    assert again.qa_error is None
    assert again.run is not None and again.run.status is WH
    assert world.executor.calls == 2


def test_bpr_07_without_a_desk_nothing_is_read_or_published(tmp_path: Path) -> None:
    world = _world(tmp_path, desk=False)
    production = world.production()

    invocation = production.invoke(BRIEF.brief_ref)

    assert not production.has_desk
    assert invocation.run is not None and invocation.run.status is WH
    assert (invocation.decision, invocation.published) == (None, None)
    assert world.board.links() == []


def test_bpr_07_an_intake_error_propagates(tmp_path: Path) -> None:
    world = _world(tmp_path)
    queued = Run.create(
        run_id=RUN_ID, content_brief_ref=BRIEF.brief_ref, workflow_version_ref=WORKFLOW.workflow_id
    )
    queued.transition(RunStatus.QUEUED, by=Actor.CONTENT_DIRECTOR)
    SqliteRunStore(world.path).save(queued)
    world.board.put(BRIEF, ready=False)

    with pytest.raises(BriefIntakeError):
        world.production().invoke(BRIEF.brief_ref)


# --- BPR-08: the briefs a sweep invokes ------------------------------------------------------


def test_bpr_08_waiting_briefs_are_the_intake_runs_waiting_for_a_human(tmp_path: Path) -> None:
    world = _world(tmp_path)
    other = IncomingBrief(brief_ref="brief-other", body="another brief")
    done = IncomingBrief(brief_ref="brief-done", body="a finished brief")
    for brief in (other, done):
        world.board.put(brief)
    production = world.production()
    assert production.waiting_briefs() == ()

    production.invoke(BRIEF.brief_ref)
    production.invoke(other.brief_ref)
    finished = production.invoke(done.brief_ref)
    _decide(world, finished, ReviewDecision(ReviewStatus.APPROVED))
    production.invoke(done.brief_ref)
    _store_foreign_waiting_run(world, run_id="run-demo-factory")

    assert world.production().waiting_briefs() == (BRIEF.brief_ref, other.brief_ref)


def test_bpr_08_without_an_index_there_is_no_sweep(tmp_path: Path) -> None:
    world = _world(tmp_path)
    store = BriefStatusReporter(SqliteRunStore(world.path), world.board)
    binding = SchemaBinding("s@v1", _schema())
    director = ContentDirector(
        Executor(), {"researcher@v1": binding, "writer@v1": binding}, store=store
    )

    with pytest.raises(ValueError, match="index"):
        BriefProduction(director, store, world.board, WORKFLOW).waiting_briefs()


def test_bpr_08_the_root_builds_the_index_over_the_run_store_file(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "runs.sqlite3"
    index: RunIndex = build_run_index({RUN_STORE_PATH_VAR: str(path)})

    assert isinstance(index, SqliteRunStore)
    assert path.parent.is_dir()
    assert index.run_ids(status=WH) == ()


def _store_foreign_waiting_run(world: World, *, run_id: str) -> None:
    """A Run waiting for a human that the brief intake did not create (another entrypoint's)."""
    binding = SchemaBinding("s@v1", _schema())
    director = ContentDirector(
        Executor(),
        {"researcher@v1": binding, "writer@v1": binding},
        qa=Evaluator(),
        store=SqliteRunStore(world.path),
    )
    run = Run.create(
        run_id=run_id, content_brief_ref=BRIEF.brief_ref, workflow_version_ref=WORKFLOW.workflow_id
    )
    director.execute_workflow(run, WORKFLOW, brief="a hardcoded brief")
    assert SqliteRunStore(world.path).run_ids(status=WH) == ("run-demo-factory", *_intake_ids())


def _intake_ids() -> tuple[str, ...]:
    return tuple(run_id_for_brief(ref) for ref in ("brief-bpr", "brief-other"))
