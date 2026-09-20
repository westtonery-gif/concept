"""Tests for the clipping department's production path (ADR-0059, CLIPPING_SPEC §8).

Maps CLIPPING_ACCEPTANCE.md §7 (CRN). The store is a real ``SqliteRunStore`` over a file, so the
snapshot codec carries every clip too; the boards, source, index, renderer and desk are the
in-memory implementations, which are infrastructure and not test doubles (ADR-0025). Every
restart builds a fresh ``ClipProduction`` over the same file, the way a new process would.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from omemo_content_factory.adapters.episode_board import ClipMode, IncomingEpisode
from omemo_content_factory.adapters.footage_index import IndexedFootage, SceneBreak, SpeechSpan
from omemo_content_factory.adapters.review_desk import ReviewDecision, ReviewDeskError
from omemo_content_factory.adapters.run_store import RunStore
from omemo_content_factory.application.clip_format import ClipFormatLimits
from omemo_content_factory.application.clip_production import (
    CLIP_KIND,
    ClipProduction,
    ClipSettings,
    run_id_for_episode,
)
from omemo_content_factory.application.qa_evaluation import EvaluationResult
from omemo_content_factory.domain.artifact import ArtifactStatus
from omemo_content_factory.domain.evaluation import EvaluationStatus
from omemo_content_factory.domain.human_review import ReviewStatus
from omemo_content_factory.domain.run import Run, RunStatus
from omemo_content_factory.domain.task import TaskStatus
from omemo_content_factory.infrastructure.in_memory_adapters import (
    InMemoryEpisodeBoard,
    InMemoryReviewDesk,
)
from omemo_content_factory.infrastructure.in_memory_clipping import (
    InMemoryClipRenderer,
    InMemoryEpisodeSource,
    InMemoryFootageIndex,
)
from omemo_content_factory.infrastructure.sqlite_run_store import SqliteRunStore

EPISODE = "episode-1"
SOURCE = "s01e01.mp4"
PATH = "/media/s01e01.mp4"
MINUTE = 60_000
SETTINGS = ClipSettings(
    chunk_ms=2 * MINUTE,
    max_ms=2 * MINUTE,
    pause_tolerance_ms=2_000,
    limits=ClipFormatLimits(max_duration_ms=3 * MINUTE, containers=("mp4",)),
)


class _Evaluator:
    """A QA evaluator whose verdict per clip index is scripted."""

    evaluator_ref = "clip_qa_agent@v1"

    def __init__(self, verdicts: dict[int, EvaluationStatus] | None = None) -> None:
        self._verdicts = verdicts or {}
        self.calls: list[str] = []
        self.raises = False

    def evaluate(self, content: str) -> EvaluationResult:
        self.calls.append(content)
        if self.raises:
            raise RuntimeError("QA is down")
        index = len(self.calls)
        verdict = self._verdicts.get(index, EvaluationStatus.PASSED)
        flags = () if verdict is EvaluationStatus.PASSED else ("something is off",)
        return EvaluationResult(verdict=verdict, flags=flags)


class _DyingStore:
    """A ``RunStore`` that stops the process after a given number of saves (the S9A trick)."""

    def __init__(self, inner: RunStore, dies_after: int) -> None:
        self._inner = inner
        self._left = dies_after

    def save(self, run: Run) -> None:
        if self._left <= 0:
            raise _CrashError
        self._left -= 1
        self._inner.save(run)

    def load(self, run_id: str) -> Run | None:
        return self._inner.load(run_id)


class _CrashError(Exception):
    """A process dying mid-step, not a managed failure."""


@dataclass
class _Factory:
    """Everything one episode needs, rebuilt per invocation like a fresh process would."""

    path: Path
    board: InMemoryEpisodeBoard
    source: InMemoryEpisodeSource
    index: InMemoryFootageIndex
    renderer: InMemoryClipRenderer
    desk: InMemoryReviewDesk
    evaluator: _Evaluator

    def production(
        self, *, with_desk: bool = True, dies_after: int | None = None
    ) -> ClipProduction:
        store: RunStore = SqliteRunStore(self.path)
        if dies_after is not None:
            store = _DyingStore(store, dies_after)
        return ClipProduction(
            store,
            self.board,
            self.source,
            self.index,
            self.renderer,
            self.evaluator,
            settings=SETTINGS,
            desk=self.desk if with_desk else None,
        )

    def stored(self) -> Run | None:
        return SqliteRunStore(self.path).load(run_id_for_episode(EPISODE))


@pytest.fixture
def factory(tmp_path: Path) -> Iterator[_Factory]:
    board = InMemoryEpisodeBoard()
    board.put(IncomingEpisode(episode_ref=EPISODE, source_ref=SOURCE, mode=ClipMode.CHUNK))
    source = InMemoryEpisodeSource()
    source.put(SOURCE, PATH)
    index = InMemoryFootageIndex()
    index.put(
        PATH,
        IndexedFootage(
            duration_ms=6 * MINUTE,
            scenes=(SceneBreak(2 * MINUTE), SceneBreak(4 * MINUTE)),
            speech=(SpeechSpan(start_ms=1_000, end_ms=5_000, text="a line"),),
        ),
    )
    yield _Factory(
        path=tmp_path / "runs.sqlite3",
        board=board,
        source=source,
        index=index,
        renderer=InMemoryClipRenderer(),
        desk=InMemoryReviewDesk(),
        evaluator=_Evaluator(),
    )


def _decide_all(factory: _Factory, decision: ReviewStatus, reason: str | None = None) -> None:
    run = factory.stored()
    assert run is not None
    for view in run.human_reviews:
        if view.status is ReviewStatus.PENDING:
            factory.desk.decide(view.review_id, ReviewDecision(decision=decision, reason=reason))


# --- CRN-01 / CRN-02: one Run, a gate per clip ------------------------------------------


def test_crn_01_an_episode_becomes_one_run_with_a_task_and_artifact_per_clip(
    factory: _Factory,
) -> None:
    invocation = factory.production().invoke(EPISODE)
    run = factory.stored()
    assert run is not None
    assert invocation.run_id == run_id_for_episode(EPISODE)
    assert len(run.tasks) == 3 == len(run.artifacts)
    assert all(task.status is TaskStatus.SUCCEEDED for task in run.tasks)
    assert {a.kind for a in run.artifacts} == {CLIP_KIND}
    assert run.status is RunStatus.WAITING_HUMAN
    assert factory.renderer.rendered() == (
        "episode-1-01.mp4",
        "episode-1-02.mp4",
        "episode-1-03.mp4",
    )


def test_crn_02_every_clip_gets_its_own_evaluation_and_its_own_review(
    factory: _Factory,
) -> None:
    factory.production().invoke(EPISODE)
    run = factory.stored()
    assert run is not None
    artifacts = {a.artifact_id for a in run.artifacts}
    assert {e.artifact_ref for e in run.evaluations} == artifacts
    assert {r.artifact_ref for r in run.human_reviews} == artifacts
    assert len(run.human_reviews) == 3, "thirteen clips would mean thirteen gates, not one"


def test_crn_06_publication_addresses_every_clip_not_just_the_last(factory: _Factory) -> None:
    """The defect ADR-0059 §5 found: `pending[-1]` would publish one and lose the rest."""
    invocation = factory.production().invoke(EPISODE)
    assert len(invocation.published) == 3
    assert len(set(invocation.published)) == 3


# --- CRN-03 / CRN-04 / CRN-05: a clip is not the Run's fate -----------------------------


def test_crn_03_a_flagged_clip_blocks_only_itself(factory: _Factory) -> None:
    factory.evaluator = _Evaluator({2: EvaluationStatus.FLAGGED})
    factory.production().invoke(EPISODE)
    _decide_all(factory, ReviewStatus.APPROVED)
    factory.production().invoke(EPISODE)

    run = factory.stored()
    assert run is not None
    approved = [a for a in run.artifacts if a.status is ArtifactStatus.APPROVED]
    assert len(approved) == 2, "the two passed clips ship"
    flagged = [a for a in run.artifacts if a.artifact_id not in {b.artifact_id for b in approved}]
    assert len(flagged) == 1
    assert flagged[0].status is ArtifactStatus.CANDIDATE, "a flagged clip is never approved"
    assert run.status is not RunStatus.FAILED, "one bad clip must not fail the episode"


def test_crn_04_a_rejected_clip_is_simply_not_shipped(factory: _Factory) -> None:
    """No SUPERSEDED chain and no rework: there is no producer whose reasoning could re-run."""
    factory.production().invoke(EPISODE)
    _decide_all(factory, ReviewStatus.REJECTED, reason="bad cut")
    factory.production().invoke(EPISODE)

    run = factory.stored()
    assert run is not None
    assert not [a for a in run.artifacts if a.status is ArtifactStatus.APPROVED]
    assert not [a for a in run.artifacts if a.status is ArtifactStatus.SUPERSEDED]
    assert len(run.tasks) == 3, "no rework Task was appended"
    assert run.status is RunStatus.COMPLETED


def test_crn_05_the_run_completes_once_every_clip_is_decided(factory: _Factory) -> None:
    factory.production().invoke(EPISODE)
    assert factory.stored() is not None
    _decide_all(factory, ReviewStatus.APPROVED)
    invocation = factory.production().invoke(EPISODE)

    run = factory.stored()
    assert run is not None
    assert run.status is RunStatus.COMPLETED
    assert len(invocation.approved) == 3
    assert all(a.status is ArtifactStatus.APPROVED for a in run.artifacts)


def test_crn_05_an_undecided_episode_waits(factory: _Factory) -> None:
    factory.production().invoke(EPISODE)
    factory.production().invoke(EPISODE)
    run = factory.stored()
    assert run is not None
    assert run.status is RunStatus.WAITING_HUMAN


# --- CRN-09: nothing to do ---------------------------------------------------------------


def test_crn_09_an_episode_that_is_not_ready_starts_no_run(factory: _Factory) -> None:
    invocation = factory.production().invoke("episode-unknown")
    assert invocation.run_id is None
    assert SqliteRunStore(factory.path).load(run_id_for_episode("episode-unknown")) is None
    assert factory.index.calls == []


def test_crn_09_a_missing_source_fails_the_run_with_a_stable_reason(factory: _Factory) -> None:
    factory.source = InMemoryEpisodeSource()  # nothing filed
    invocation = factory.production().invoke(EPISODE)
    run = factory.stored()
    assert run is not None
    assert run.status is RunStatus.FAILED
    assert invocation.failure_reason == "EPISODE_SOURCE_MISSING"


def test_crn_09_an_index_outage_fails_the_run_not_the_process(factory: _Factory) -> None:
    factory.index.fail(PATH)
    invocation = factory.production().invoke(EPISODE)
    run = factory.stored()
    assert run is not None
    assert run.status is RunStatus.FAILED
    assert invocation.failure_reason == "FOOTAGE_INDEX_FAILED"


# --- RND-05: a format violation is a failed Task, not a verdict --------------------------


def test_rnd_05_a_clip_out_of_spec_fails_its_task_and_never_reaches_a_human(
    factory: _Factory,
) -> None:
    factory.renderer.duration_override = 10 * MINUTE  # past the 3-minute limit
    invocation = factory.production().invoke(EPISODE)

    run = factory.stored()
    assert run is not None
    assert len(invocation.failed_clips) == 3
    assert all(task.status is TaskStatus.FAILED for task in run.tasks)
    assert run.artifacts == ()
    assert run.evaluations == (), "a render defect is not a content risk"
    assert run.human_reviews == (), "and it is never queued for a person"


def test_crn_03_a_clip_that_cannot_be_rendered_fails_alone(factory: _Factory) -> None:
    factory.renderer.fail("episode-1-02.mp4")
    invocation = factory.production().invoke(EPISODE)
    run = factory.stored()
    assert run is not None
    assert len(invocation.failed_clips) == 1
    assert len(run.artifacts) == 2, "the other two clips were still produced"
    assert run.status is not RunStatus.FAILED


# --- CRN-07 / CRN-10: nothing shared was changed -----------------------------------------


def test_crn_10_the_content_director_is_untouched_by_this_path() -> None:
    import inspect

    from omemo_content_factory.application import content_director

    source = inspect.getsource(content_director)
    assert "clip" not in source.lower(), "the department must not have leaked into the Director"


def test_crn_07_the_single_review_helpers_keep_their_behaviour() -> None:
    """Stage 10-12's acceptance pins these; the department got siblings, not a rewrite."""
    import inspect

    from omemo_content_factory.application import review_publication

    assert "pending[-1]" in inspect.getsource(review_publication.pending_review)


# --- CRN-08: a crash at every commit point -----------------------------------------------


# A full first invocation of this episode commits fifteen times, so there are fourteen points at
# which the process can die *mid-invocation*. Every one of them is exercised.
@pytest.mark.parametrize("dies_after", list(range(1, 15)))
def test_crn_08_a_crash_after_any_commit_resumes_without_duplicating_work(
    factory: _Factory, dies_after: int
) -> None:
    with pytest.raises(_CrashError):
        factory.production(dies_after=dies_after).invoke(EPISODE)

    renders_before = len(factory.renderer.requests)
    qa_before = len(factory.evaluator.calls)

    factory.production().invoke(EPISODE)
    _decide_all(factory, ReviewStatus.APPROVED)
    factory.production().invoke(EPISODE)

    run = factory.stored()
    assert run is not None
    assert run.status is RunStatus.COMPLETED
    assert len(run.artifacts) == 3
    assert all(a.status is ArtifactStatus.APPROVED for a in run.artifacts)
    assert len(run.evaluations) == 3, "no evaluation was recorded twice"
    assert len(factory.evaluator.calls) >= qa_before
    assert len(factory.renderer.requests) >= renders_before


# --- the desk is optional and its outages never change the Run ---------------------------


def test_crn_06_without_a_desk_the_reviews_stay_in_the_run_store(factory: _Factory) -> None:
    invocation = factory.production(with_desk=False).invoke(EPISODE)
    run = factory.stored()
    assert run is not None
    assert invocation.published == ()
    assert len(run.human_reviews) == 3
    assert all(view.status is ReviewStatus.PENDING for view in run.human_reviews)


def test_crn_06_a_desk_that_cannot_publish_never_changes_the_run(factory: _Factory) -> None:
    class _Broken(InMemoryReviewDesk):
        def publish(self, package, /):  # type: ignore[no-untyped-def]
            raise ReviewDeskError("the desk is down")

    factory.desk = _Broken()
    invocation = factory.production().invoke(EPISODE)
    run = factory.stored()
    assert run is not None
    assert invocation.publish_error is not None
    assert len(run.human_reviews) == 3, "the reviews were opened and saved regardless"
    assert run.status is RunStatus.WAITING_HUMAN


def test_crn_02_a_qa_outage_parks_the_run_and_the_next_invocation_asks_again(
    factory: _Factory,
) -> None:
    factory.evaluator.raises = True
    invocation = factory.production().invoke(EPISODE)
    run = factory.stored()
    assert run is not None
    assert invocation.qa_error is not None
    assert run.status is RunStatus.WAITING_QA, "fail closed: no verdict was guessed"

    factory.evaluator.raises = False
    factory.production().invoke(EPISODE)
    run = factory.stored()
    assert run is not None
    assert len(run.evaluations) == 3
    assert run.status is RunStatus.WAITING_HUMAN
