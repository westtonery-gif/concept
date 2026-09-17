"""Tests for the status and review-link write-back to the brief board (ADR-0041, ADR-0047).

Maps ADAPTER_ACCEPTANCE.md §9 (BSR). ``BriefStatusReporter`` wraps the store handed to a real
``ContentDirector``; the board is ``InMemoryBriefBoard``, or a double around it that refuses a
chosen status (or every review location) and looks at the store at the moment of every report.
Executors and the evaluator are deterministic.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from omemo_content_factory.adapters.brief_board import BriefBoard, BriefBoardError, IncomingBrief
from omemo_content_factory.adapters.run_store import RunStore, RunStoreError
from omemo_content_factory.application.brief_status import (
    BriefStatusReporter,
    FailedLocationReport,
    FailedReport,
)
from omemo_content_factory.application.content_director import ContentDirector
from omemo_content_factory.application.qa_evaluation import EvaluationResult
from omemo_content_factory.application.schema_validation import SchemaBinding
from omemo_content_factory.application.task_execution import ExecutionResult
from omemo_content_factory.domain.evaluation import EvaluationStatus
from omemo_content_factory.domain.human_review import ReviewStatus
from omemo_content_factory.domain.run import Actor, Run, RunSnapshot, RunStatus
from omemo_content_factory.domain.schema import Schema, SchemaStatus, SchemaVersion
from omemo_content_factory.domain.workflow import Workflow, WorkflowStep
from omemo_content_factory.infrastructure.in_memory_adapters import InMemoryBriefBoard
from omemo_content_factory.infrastructure.sqlite_run_store import SqliteRunStore

RUN_ID = "run-bsr-0001"
BRIEF = IncomingBrief(brief_ref="brief-bsr", body="a brief")
WORKFLOW = Workflow.create(
    workflow_id="bsr@v1",
    name="Research -> Write",
    steps=[
        WorkflowStep(step_id="research", task_type="t", agent_ref="researcher@v1", schema_ref="s"),
        WorkflowStep(step_id="write", task_type="t", agent_ref="writer@v1", schema_ref="s"),
    ],
)

Q, RN, WQ, WH, C, F = (
    RunStatus.QUEUED,
    RunStatus.RUNNING,
    RunStatus.WAITING_QA,
    RunStatus.WAITING_HUMAN,
    RunStatus.COMPLETED,
    RunStatus.FAILED,
)
_STRAIGHT = [Q, RN, WQ, WH, C]


# --- Doubles ------------------------------------------------------------------------------


@dataclass
class Executor:
    """Succeeds with a structured Output, or fails every Task when ``fail`` is set."""

    fail: bool = False

    def execute(self, task_input: str) -> ExecutionResult:
        if self.fail:
            return ExecutionResult(succeeded=False, failure_reason="cannot")
        return ExecutionResult(
            succeeded=True, output=f"[gen] {task_input}", schema_ref="s@v1", payload_fields={}
        )


@dataclass
class Evaluator:
    """Answers the verdicts in order, the last one from then on."""

    verdicts: list[EvaluationStatus] = field(default_factory=lambda: [EvaluationStatus.PASSED])
    evaluator_ref: str = "qa@v1"

    def evaluate(self, content: str) -> EvaluationResult:
        verdict = self.verdicts.pop(0) if len(self.verdicts) > 1 else self.verdicts[0]
        flags = () if verdict is EvaluationStatus.PASSED else ("risk",)
        return EvaluationResult(verdict=verdict, flags=flags)


class MemoryStore:
    """A ``RunStore`` keeping the last snapshot per Run."""

    def __init__(self) -> None:
        self.snapshots: dict[str, RunSnapshot] = {}

    def save(self, run: Run, /) -> None:
        self.snapshots[run.run_id] = run.snapshot

    def load(self, run_id: str, /) -> Run | None:
        snapshot = self.snapshots.get(run_id)
        return None if snapshot is None else Run.restore(snapshot)


class FailingStore:
    """A ``RunStore`` whose every save fails."""

    def save(self, run: Run, /) -> None:
        raise RunStoreError("disk full")

    def load(self, run_id: str, /) -> Run | None:
        return None


class WatchingBoard:
    """An ``InMemoryBriefBoard`` refusing chosen statuses and checking the store on each report."""

    def __init__(
        self,
        store: RunStore | None = None,
        refuse: set[RunStatus] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.inner = InMemoryBriefBoard()
        self.inner.put(BRIEF)
        self._store = store
        self.refuse = set() if refuse is None else refuse
        self._error = error
        self.stored_at_report: list[RunStatus | None] = []
        self.refuse_locations = False
        self.location_attempts = 0

    def fetch_brief(self, brief_ref: str, /) -> IncomingBrief | None:
        return self.inner.fetch_brief(brief_ref)

    def report_status(self, brief_ref: str, /, *, run_id: str, status: RunStatus) -> None:
        if self._store is not None:
            stored = self._store.load(run_id)
            self.stored_at_report.append(None if stored is None else stored.status)
        if status in self.refuse:
            raise self._error if self._error is not None else BriefBoardError("board is down")
        self.inner.report_status(brief_ref, run_id=run_id, status=status)

    def report_review_location(self, brief_ref: str, /, *, run_id: str, location: str) -> None:
        self.location_attempts += 1
        if self.refuse_locations:
            raise self._error if self._error is not None else BriefBoardError("board is down")
        self.inner.report_review_location(brief_ref, run_id=run_id, location=location)

    def shown(self) -> list[RunStatus]:
        return [status for _, status in self.inner.reports(BRIEF.brief_ref)]

    def links(self) -> list[str]:
        return [location for _, location in self.inner.review_locations(BRIEF.brief_ref)]


# --- Helpers ------------------------------------------------------------------------------


def _schema() -> Schema:
    schema = Schema.create(
        schema_id="s", version=SchemaVersion(1), description="d", required_fields=[]
    )
    schema.transition(SchemaStatus.ACTIVE)
    return schema


def _director(
    store: RunStore | None, *, qa: Evaluator | None = None, fail: bool = False
) -> ContentDirector:
    schemas = {
        "researcher@v1": SchemaBinding("s@v1", _schema()),
        "writer@v1": SchemaBinding("s@v1", _schema()),
    }
    return ContentDirector(Executor(fail=fail), schemas, qa=qa, store=store)


def _new_run() -> Run:
    return Run.create(
        run_id=RUN_ID, content_brief_ref=BRIEF.brief_ref, workflow_version_ref="bsr@v1"
    )


def _produce(
    store: RunStore, board: BriefBoard, *, qa: Evaluator | None = None, fail: bool = False
) -> tuple[Run, BriefStatusReporter]:
    reporter = BriefStatusReporter(store, board)
    run = _new_run()
    _director(reporter, qa=qa, fail=fail).execute_workflow(run, WORKFLOW, brief=BRIEF.body)
    return run, reporter


# --- BSR-01/02: every committed status, once, after it is stored --------------------------


@pytest.mark.parametrize(
    ("qa", "fail", "expected"),
    [
        (None, False, _STRAIGHT),
        (Evaluator(), False, [Q, RN, WQ, WH]),
        (None, True, [Q, RN, F]),
    ],
    ids=["no-qa", "qa-passed", "step-fails"],
)
def test_bsr_01_every_status_change_is_shown_once_and_the_run_is_unchanged(
    qa: Evaluator | None, fail: bool, expected: list[RunStatus]
) -> None:
    store, plain = MemoryStore(), MemoryStore()
    board = WatchingBoard()

    run, reporter = _produce(store, board, qa=qa, fail=fail)
    reference = _new_run()
    _director(plain, qa=qa, fail=fail).execute_workflow(reference, WORKFLOW, brief=BRIEF.body)

    assert board.shown() == expected
    assert {run_id for run_id, _ in board.inner.reports(BRIEF.brief_ref)} == {RUN_ID}
    assert run.snapshot == reference.snapshot
    assert store.snapshots[RUN_ID] == plain.snapshots[RUN_ID]
    assert reporter.failed_reports == ()


def test_bsr_02_a_status_is_stored_before_it_is_shown() -> None:
    store = MemoryStore()
    board = WatchingBoard(store)

    run, reporter = _produce(store, board, qa=Evaluator())
    run.submit_review(
        run.human_reviews[-1].review_id, ReviewStatus.APPROVED, by=Actor.HUMAN_REVIEWER
    )
    _director(reporter, qa=Evaluator()).resume_workflow(run, WORKFLOW, brief="")

    assert run.status is C
    assert board.stored_at_report == _STRAIGHT


# --- BSR-03: a rework after a restart is shown too ----------------------------------------


def test_bsr_03_a_human_requested_rework_after_a_restart_is_shown(tmp_path: Path) -> None:
    path = tmp_path / "runs.sqlite3"
    board = WatchingBoard()
    run, _ = _produce(
        SqliteRunStore(path),
        board,
        qa=Evaluator([EvaluationStatus.FLAGGED, EvaluationStatus.PASSED]),
    )
    assert board.shown() == [Q, RN, WQ, WH]
    run.submit_review(
        run.human_reviews[-1].review_id,
        ReviewStatus.CHANGES_REQUESTED,
        by=Actor.HUMAN_REVIEWER,
        reason="sharper hook",
    )
    SqliteRunStore(path).save(run)

    restarted = BriefStatusReporter(SqliteRunStore(path), board)
    loaded = restarted.load(RUN_ID)
    assert loaded is not None
    _director(restarted, qa=Evaluator()).resume_workflow(loaded, WORKFLOW, brief="")

    assert loaded.status is WH
    assert loaded.rework_count == 1
    assert board.shown() == [Q, RN, WQ, WH, RN, WQ, WH]


# --- BSR-04/05: a board outage never stops production; a store failure shows nothing ------


def test_bsr_04_a_refused_report_is_logged_kept_and_retried_without_touching_the_run(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store, plain = MemoryStore(), MemoryStore()
    board = WatchingBoard(refuse={RN})

    with caplog.at_level(logging.WARNING, logger="omemo_content_factory.application.brief_status"):
        run, reporter = _produce(store, board, qa=Evaluator())
    reference = _new_run()
    _director(plain, qa=Evaluator()).execute_workflow(reference, WORKFLOW, brief=BRIEF.body)

    assert run.status is WH
    assert run.snapshot == reference.snapshot
    assert store.snapshots[RUN_ID] == plain.snapshots[RUN_ID]
    assert board.shown() == [Q, WQ, WH]
    assert reporter.failed_reports == (FailedReport(RUN_ID, RN, "board is down"),)
    assert [r.levelno for r in caplog.records] == [logging.WARNING]
    assert RUN_ID in caplog.text and "running" in caplog.text


def test_bsr_04_a_status_refused_at_the_end_is_not_retried_by_saves_but_by_sync() -> None:
    board = WatchingBoard(refuse={WH})
    store = MemoryStore()
    run, reporter = _produce(store, board, qa=Evaluator([EvaluationStatus.FLAGGED]))
    assert run.status is WH
    assert board.shown() == [Q, RN, WQ]

    board.refuse.clear()
    reporter.save(run)
    assert board.shown() == [Q, RN, WQ]

    assert reporter.shown_status(RUN_ID) is WQ

    reporter.sync(run)
    assert board.shown() == [Q, RN, WQ, WH]
    assert reporter.shown_status(RUN_ID) is WH
    assert len(reporter.failed_reports) == 1


def test_bsr_05_a_failed_save_raises_and_shows_nothing() -> None:
    board = WatchingBoard()
    reporter = BriefStatusReporter(FailingStore(), board)

    with pytest.raises(RunStoreError):
        _director(reporter).execute_workflow(_new_run(), WORKFLOW, brief=BRIEF.body)

    assert board.shown() == []


# --- BSR-06/07: repeats, sync, other exceptions, load -------------------------------------


def test_bsr_06_an_unchanged_status_is_shown_once_and_a_new_reporter_syncs_it_again() -> None:
    store = MemoryStore()
    board = WatchingBoard()
    run, reporter = _produce(store, board, qa=Evaluator([EvaluationStatus.FLAGGED]))
    assert board.shown() == [Q, RN, WQ, WH]
    reports = len(board.inner.reports(BRIEF.brief_ref))

    reporter.save(run)
    reporter.save(run)
    reporter.sync(run)
    assert len(board.inner.reports(BRIEF.brief_ref)) == reports

    board.inner = InMemoryBriefBoard()  # a fresh page, to count what a new process sends
    board.inner.put(BRIEF)
    restarted = BriefStatusReporter(store, board)
    loaded = restarted.load(RUN_ID)
    assert loaded is not None
    _director(restarted, qa=Evaluator()).resume_workflow(loaded, WORKFLOW, brief="")
    assert board.shown() == []  # nothing changed, nothing saved
    restarted.sync(loaded)
    restarted.sync(loaded)
    assert board.shown() == [WH]


def test_bsr_07_any_other_board_exception_propagates() -> None:
    board = WatchingBoard(refuse={Q}, error=RuntimeError("bug"))
    store = MemoryStore()
    reporter = BriefStatusReporter(store, board)

    with pytest.raises(RuntimeError, match="bug"):
        _director(reporter).execute_workflow(_new_run(), WORKFLOW, brief=BRIEF.body)

    assert store.snapshots[RUN_ID].status is Q  # saved before the report was attempted
    assert reporter.failed_reports == ()


def test_bsr_07_load_is_the_wrapped_store_s() -> None:
    store = MemoryStore()
    reporter = BriefStatusReporter(store, WatchingBoard())
    assert reporter.load(RUN_ID) is None

    run, _ = _produce(store, WatchingBoard())
    loaded = reporter.load(RUN_ID)

    assert loaded is not None
    assert loaded.snapshot == run.snapshot


# --- BSR-08: the reporter is a RunStore ---------------------------------------------------


def test_bsr_08_the_reporter_is_a_run_store() -> None:
    reporter: RunStore = BriefStatusReporter(MemoryStore(), InMemoryBriefBoard())
    assert isinstance(reporter, BriefStatusReporter)


# --- BSR-09: the review link (ADR-0047) ---------------------------------------------------


def test_bsr_09_a_review_location_is_shown_once_per_location_and_nothing_is_saved() -> None:
    store = MemoryStore()
    board = WatchingBoard()
    run, reporter = _produce(store, board, qa=Evaluator())
    saved = store.snapshots[RUN_ID]
    shown = board.shown()

    reporter.show_review_location(run, "doc://1")
    reporter.show_review_location(run, "doc://1")
    reporter.show_review_location(run, "doc://2")

    assert board.links() == ["doc://1", "doc://2"]
    assert board.location_attempts == 2
    assert reporter.shown_review_location(RUN_ID) == "doc://2"
    assert store.snapshots[RUN_ID] is saved
    assert board.shown() == shown
    assert reporter.failed_location_reports == ()


def test_bsr_09_a_refused_location_is_logged_kept_and_tried_again_by_the_next_call(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = MemoryStore()
    board = WatchingBoard()
    run, reporter = _produce(store, board, qa=Evaluator())
    board.refuse_locations = True

    with caplog.at_level(logging.WARNING, logger="omemo_content_factory.application.brief_status"):
        reporter.show_review_location(run, "doc://1")

    assert board.links() == []
    assert reporter.shown_review_location(RUN_ID) is None
    assert reporter.failed_location_reports == (
        FailedLocationReport(RUN_ID, "doc://1", "board is down"),
    )
    assert [r.levelno for r in caplog.records] == [logging.WARNING]
    assert RUN_ID in caplog.text
    assert run.status is WH

    board.refuse_locations = False
    reporter.show_review_location(run, "doc://1")
    assert board.links() == ["doc://1"]
    assert len(reporter.failed_location_reports) == 1


def test_bsr_09_any_other_board_exception_propagates_and_a_new_reporter_shows_it_again() -> None:
    store = MemoryStore()
    board = WatchingBoard(error=RuntimeError("bug"))
    run, reporter = _produce(store, board, qa=Evaluator())
    board.refuse_locations = True

    with pytest.raises(RuntimeError, match="bug"):
        reporter.show_review_location(run, "doc://1")
    assert reporter.failed_location_reports == ()

    board.refuse_locations = False
    reporter.show_review_location(run, "doc://1")
    BriefStatusReporter(store, board).show_review_location(run, "doc://1")
    assert board.location_attempts == 3
    assert board.links() == ["doc://1"]
