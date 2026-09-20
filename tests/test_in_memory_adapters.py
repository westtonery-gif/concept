"""Tests for the in-memory board, review desk and analytics sink (ADR-0025).

Maps ADAPTER_ACCEPTANCE.md §6 (STB). Each stub is checked against its contract (ADAPTER_SPEC §5–§7)
and against the stricter rules ADR-0025 §3 fixes where the contract is silent. The Runs are driven
through the public Run API only, the way the Content Director drives them. No mocks.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path
from typing import Any

import pytest

import omemo_content_factory.infrastructure.in_memory_adapters as stubs_module
from omemo_content_factory.adapters.analytics_sink import AnalyticsSink, AnalyticsSinkError
from omemo_content_factory.adapters.brief_board import BriefBoard, BriefBoardError, IncomingBrief
from omemo_content_factory.adapters.episode_board import (
    ClipMode,
    EpisodeBoard,
    EpisodeBoardError,
    IncomingEpisode,
)
from omemo_content_factory.adapters.review_desk import (
    ReviewDecision,
    ReviewDesk,
    ReviewDeskError,
    ReviewPackage,
)
from omemo_content_factory.domain.artifact import ArtifactStatus
from omemo_content_factory.domain.evaluation import EvaluationStatus
from omemo_content_factory.domain.human_review import ReviewStatus
from omemo_content_factory.domain.run import Run, RunStatus
from omemo_content_factory.infrastructure.in_memory_adapters import (
    InMemoryAnalyticsSink,
    InMemoryBriefBoard,
    InMemoryEpisodeBoard,
    InMemoryReviewDesk,
)
from tests.restorable_runs import CD, REVIEWER, drive, evaluate, record_call, succeeded_task

BRIEF = IncomingBrief(brief_ref="brief-7", body="Тема: сон и восстановление.")
APPROVE = ReviewDecision(decision=ReviewStatus.APPROVED)


def _run_at_review(brief: IncomingBrief = BRIEF) -> tuple[Run, str, str]:
    """A Run from ``brief`` in ``WAITING_HUMAN``: a passed candidate, a pending review, one call.

    Returns the Run, the candidate's id and the review's id.
    """
    run = Run.create(run_id="r-1", content_brief_ref=brief.brief_ref, workflow_version_ref="wf@v1")
    drive(run, RunStatus.QUEUED, RunStatus.RUNNING)
    task_id, output_id = succeeded_task(run, step="script", payload="Сценарий.")
    record_call(run, task_id)
    candidate = run.create_artifact(output_id, kind="script", by=CD)
    run.transition_artifact(candidate, ArtifactStatus.CANDIDATE, by=CD)
    drive(run, RunStatus.WAITING_QA)
    evaluate(run, candidate, EvaluationStatus.PASSED, flags=("проверить дозировку",))
    drive(run, RunStatus.WAITING_HUMAN)
    return run, candidate, run.open_human_review(candidate, by=CD)


def _package(run: Run, candidate: str, review_id: str, **overrides: Any) -> ReviewPackage:
    fields: dict[str, Any] = {
        "run_id": run.run_id,
        "review_id": review_id,
        "candidate": run.artifact(candidate),
        "brief": BRIEF.body,
        "qa_flags": run.evaluations[-1].flags,
    }
    fields.update(overrides)
    return ReviewPackage(**fields)


# --- STB-01: conformance and purity -------------------------------------------------------


_BOARD: BriefBoard = InMemoryBriefBoard()
_DESK: ReviewDesk = InMemoryReviewDesk()
_SINK: AnalyticsSink = InMemoryAnalyticsSink()


def test_stb_01_each_stub_is_its_contract() -> None:
    """mypy --strict holds each assignment above to its Protocol; here they are the stubs."""
    for stub in (_BOARD, _DESK, _SINK):
        assert type(stub).__module__ == stubs_module.__name__


def test_stb_01_stubs_reach_nothing_outside_the_process() -> None:
    tree = ast.parse(Path(stubs_module.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    project = "omemo_content_factory."
    outside = {
        m
        for m in imported
        if m not in {"__future__", "collections.abc", "dataclasses", "typing"}
        and not m.startswith((f"{project}domain", f"{project}adapters"))
    }
    assert outside == set()


# --- STB-02 / STB-03: the board -----------------------------------------------------------


def test_stb_02_a_ready_brief_is_handed_over_as_filed() -> None:
    board = InMemoryBriefBoard()
    board.put(BRIEF)
    assert board.fetch_brief("brief-7") == BRIEF


def test_stb_02_unknown_and_not_ready_briefs_give_none_until_filed_ready() -> None:
    board = InMemoryBriefBoard()
    board.put(BRIEF, ready=False)
    assert board.fetch_brief("brief-7") is None
    assert board.fetch_brief("brief-unknown") is None

    edited = IncomingBrief(brief_ref="brief-7", body="Тема: сон, версия 2.")
    board.put(edited)
    assert board.fetch_brief("brief-7") == edited

    board.put(edited, ready=False)
    assert board.fetch_brief("brief-7") is None


def test_stb_03_statuses_are_shown_in_order_and_a_repeat_is_harmless() -> None:
    board = InMemoryBriefBoard()
    board.put(BRIEF)
    for status in (RunStatus.CREATED, RunStatus.RUNNING, RunStatus.RUNNING, RunStatus.COMPLETED):
        board.report_status("brief-7", run_id="r-1", status=status)
    board.report_status("brief-7", run_id="r-1", status=RunStatus.COMPLETED)

    assert board.reports("brief-7") == (
        ("r-1", RunStatus.CREATED),
        ("r-1", RunStatus.RUNNING),
        ("r-1", RunStatus.COMPLETED),
    )
    assert board.reports("brief-unknown") == ()


def test_stb_03_a_status_on_an_unknown_brief_is_refused_and_not_recorded() -> None:
    board = InMemoryBriefBoard()
    with pytest.raises(BriefBoardError):
        board.report_status("brief-7", run_id="r-1", status=RunStatus.CREATED)
    assert board.reports("brief-7") == ()


def test_stb_08_review_locations_are_shown_in_order_and_a_repeat_is_harmless() -> None:
    board = InMemoryBriefBoard()
    board.put(BRIEF)
    for location in ("doc://1", "doc://1", "doc://2"):
        board.report_review_location("brief-7", run_id="r-1", location=location)

    assert board.review_locations("brief-7") == (("r-1", "doc://1"), ("r-1", "doc://2"))
    assert board.reports("brief-7") == ()
    assert board.review_locations("brief-unknown") == ()


@pytest.mark.parametrize(("ref", "location"), [("brief-unknown", "doc://1"), ("brief-7", " ")])
def test_stb_08_a_location_on_an_unknown_brief_or_a_blank_one_is_refused(
    ref: str, location: str
) -> None:
    board = InMemoryBriefBoard()
    board.put(BRIEF)
    with pytest.raises(BriefBoardError):
        board.report_review_location(ref, run_id="r-1", location=location)
    assert board.review_locations(ref) == ()


# --- STB-04 / STB-05: the desk ------------------------------------------------------------


def test_stb_04_republishing_the_same_package_returns_the_same_place() -> None:
    run, candidate, review_id = _run_at_review()
    desk = InMemoryReviewDesk()
    package = _package(run, candidate, review_id)

    location = desk.publish(package)
    assert location.strip()
    assert desk.publish(_package(run, candidate, review_id)) == location
    assert desk.published(review_id) == package
    assert desk.published("r-1-review-99") is None


def test_stb_04_another_package_under_a_published_review_is_refused() -> None:
    run, candidate, review_id = _run_at_review()
    desk = InMemoryReviewDesk()
    first = _package(run, candidate, review_id)
    desk.publish(first)

    with pytest.raises(ReviewDeskError):
        desk.publish(_package(run, candidate, review_id, qa_flags=()))
    assert desk.published(review_id) == first


def test_stb_05_a_decision_is_none_while_pending_then_read_back() -> None:
    run, candidate, review_id = _run_at_review()
    desk = InMemoryReviewDesk()
    desk.publish(_package(run, candidate, review_id))

    assert desk.fetch_decision(review_id) is None
    desk.decide(review_id, APPROVE)
    assert desk.fetch_decision(review_id) == APPROVE


def test_stb_05_an_unpublished_review_can_be_neither_read_nor_decided() -> None:
    desk = InMemoryReviewDesk()
    with pytest.raises(ReviewDeskError):
        desk.fetch_decision("r-1-review-1")
    with pytest.raises(ReviewDeskError):
        desk.decide("r-1-review-1", APPROVE)


def test_stb_05_the_first_decision_is_final() -> None:
    run, candidate, review_id = _run_at_review()
    desk = InMemoryReviewDesk()
    desk.publish(_package(run, candidate, review_id))
    desk.decide(review_id, APPROVE)

    desk.decide(review_id, ReviewDecision(decision=ReviewStatus.APPROVED))
    with pytest.raises(ReviewDeskError):
        desk.decide(review_id, ReviewDecision(ReviewStatus.REJECTED, reason="Не по теме."))
    assert desk.fetch_decision(review_id) == APPROVE


# --- STB-06: the sink ---------------------------------------------------------------------


def test_stb_06_records_are_delivered_once_each_in_order() -> None:
    run, *_ = _run_at_review()
    task_id = run.tasks[0].task_id
    record_call(run, task_id, cost="0.0200")
    first, second = run.analytics_records
    sink = InMemoryAnalyticsSink()

    sink.export([first])
    sink.export(run.analytics_records)
    sink.export(run.analytics_records)
    sink.export([])

    assert sink.records == (first, second)


def test_stb_06_a_different_record_under_a_delivered_id_refuses_the_whole_batch() -> None:
    run, *_ = _run_at_review()
    (delivered,) = run.analytics_records
    sink = InMemoryAnalyticsSink()
    sink.export([delivered])

    other_run, *_ = _run_at_review()
    task_id = other_run.tasks[0].task_id
    fresh = other_run.analytics_record(record_call(other_run, task_id))
    forged = dataclasses.replace(delivered, model="model-b")

    with pytest.raises(AnalyticsSinkError):
        sink.export([fresh, forged])
    with pytest.raises(AnalyticsSinkError):
        InMemoryAnalyticsSink().export([delivered, forged])
    assert sink.records == (delivered,)


# --- STB-07: the stubs around the unchanged Run API ---------------------------------------


def test_stb_07_a_brief_goes_to_an_approved_artifact_through_the_stubs() -> None:
    board, desk, sink = InMemoryBriefBoard(), InMemoryReviewDesk(), InMemoryAnalyticsSink()
    board.put(BRIEF)
    brief = board.fetch_brief(BRIEF.brief_ref)
    assert brief is not None

    run, candidate, review_id = _run_at_review(brief)
    board.report_status(brief.brief_ref, run_id=run.run_id, status=run.status)
    desk.publish(_package(run, candidate, review_id))
    assert desk.fetch_decision(review_id) is None

    desk.decide(review_id, APPROVE)
    decision = desk.fetch_decision(review_id)
    assert decision is not None
    run.submit_review(review_id, decision.decision, by=REVIEWER, reason=decision.reason)
    run.transition_artifact(candidate, ArtifactStatus.APPROVED, by=CD)
    drive(run, RunStatus.COMPLETED)
    board.report_status(brief.brief_ref, run_id=run.run_id, status=run.status)
    sink.export(run.analytics_records)
    sink.export(run.analytics_records)

    assert run.artifact(candidate).status is ArtifactStatus.APPROVED
    assert board.reports(brief.brief_ref) == (
        ("r-1", RunStatus.WAITING_HUMAN),
        ("r-1", RunStatus.COMPLETED),
    )
    assert sink.records == tuple(run.analytics_records)


# --- STE: the episode board stub (CLIPPING_ACCEPTANCE §2) ---------------------------------


def _episode(ref: str = "episode-1", mode: ClipMode = ClipMode.SCENE) -> IncomingEpisode:
    return IncomingEpisode(episode_ref=ref, source_ref=f"{ref}.mp4", mode=mode)


def test_ste_01_a_filed_episode_is_handed_over() -> None:
    board = InMemoryEpisodeBoard()
    episode = _episode()
    board.put(episode)
    assert board.fetch_episode("episode-1") == episode


def test_ste_01_the_stub_satisfies_the_port_and_keeps_its_control_side_outside_it() -> None:
    board: EpisodeBoard = InMemoryEpisodeBoard()  # mypy --strict rejects a drifted shape
    assert board.fetch_episode("nothing") is None
    assert not hasattr(EpisodeBoard, "put")
    assert not hasattr(EpisodeBoard, "reports")


def test_ste_02_an_unknown_or_unready_episode_is_none() -> None:
    board = InMemoryEpisodeBoard()
    assert board.fetch_episode("episode-1") is None
    board.put(_episode(), ready=False)
    assert board.fetch_episode("episode-1") is None, "on the board is not the same as ready"
    board.put(_episode())
    assert board.fetch_episode("episode-1") is not None


def test_ste_03_a_status_on_an_unknown_episode_fails_loudly_and_records_nothing() -> None:
    board = InMemoryEpisodeBoard()
    with pytest.raises(EpisodeBoardError) as caught:
        board.report_status("episode-1", run_id="run-1", status=RunStatus.QUEUED)
    assert "episode-1" in str(caught.value)
    assert board.reports("episode-1") == ()


def test_ste_04_repeating_the_last_status_changes_nothing_and_history_is_readable() -> None:
    board = InMemoryEpisodeBoard()
    board.put(_episode())
    board.report_status("episode-1", run_id="run-1", status=RunStatus.QUEUED)
    board.report_status("episode-1", run_id="run-1", status=RunStatus.QUEUED)
    board.report_status("episode-1", run_id="run-1", status=RunStatus.RUNNING)
    assert board.reports("episode-1") == (
        ("run-1", RunStatus.QUEUED),
        ("run-1", RunStatus.RUNNING),
    )


def test_ste_04_refiling_an_episode_replaces_what_the_reference_held() -> None:
    board = InMemoryEpisodeBoard()
    board.put(_episode(mode=ClipMode.SCENE))
    board.put(_episode(mode=ClipMode.CHUNK))
    episode = board.fetch_episode("episode-1")
    assert episode is not None
    assert episode.mode is ClipMode.CHUNK
