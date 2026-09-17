"""ROADMAP Stage 10 acceptance — a candidate goes to the review desk, a decision comes back (S10A).

Stage 9's production path (``STAGE9_ACCEPTANCE.md``, whose process is reused): the bundled Prompt
store for Rin, Leo and QA, Rin's Skill and Tool, real ``AnthropicLLMClient``s with the network below
the SDK scripted, the real ``SqliteRunStore`` under ``BriefStatusReporter`` over an in-memory board.
The review desk is an ``InMemoryReviewDesk`` playing a Google Doc. Every invocation is a new process
doing what ``demo_notion.py`` does: ``take_review_decision`` → ``produce_brief`` →
``publish_pending_review`` (``STAGE10_ACCEPTANCE.md``, ADR-0046).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from anthropic.types import Message

from omemo_content_factory.adapters.review_desk import (
    ReviewDecision,
    ReviewDeskError,
    ReviewPackage,
)
from omemo_content_factory.agents import script_writer as leo
from omemo_content_factory.application.content_director import REWORK_LIMIT_REASON
from omemo_content_factory.application.review_decision import (
    FetchedDecision,
    take_review_decision,
)
from omemo_content_factory.application.review_publication import (
    PublishedReview,
    publish_pending_review,
)
from omemo_content_factory.composition import (
    RUN_STORE_PATH_VAR,
    build_run_store,
    load_prompt_catalogue,
)
from omemo_content_factory.domain.artifact import ArtifactStatus
from omemo_content_factory.domain.human_review import ReviewId, ReviewStatus
from omemo_content_factory.domain.run import Actor, Run, RunStatus
from omemo_content_factory.infrastructure.in_memory_adapters import InMemoryReviewDesk
from tests.test_stage8_acceptance import (
    BRIEF,
    FLAG,
    INSTRUCTIONS,
    _producer_turns,
    _script_turn,
    _verdict,
)
from tests.test_stage9_acceptance import BRIEF_REF, RUN_ID, _Board, _filed_board, _Process

Q, RN, WQ, WH, C, F = (
    RunStatus.QUEUED,
    RunStatus.RUNNING,
    RunStatus.WAITING_QA,
    RunStatus.WAITING_HUMAN,
    RunStatus.COMPLETED,
    RunStatus.FAILED,
)
APPROVE = ReviewDecision(ReviewStatus.APPROVED)


# --- the desk, and one invocation of the entrypoint ----------------------------------------


class _Desk(InMemoryReviewDesk):
    """The in-memory desk as a Google Doc: the decision line can be retyped; the desk can go down.

    ``InMemoryReviewDesk.decide`` keeps the first decision for good (ADR-0025 §3), but a reviewer
    edits a Doc, and ADR-0045 §3 relies on that; ``type`` overwrites the decision line.
    """

    def __init__(self) -> None:
        super().__init__()
        self.down = False
        self.fetches = 0
        self._lines: dict[ReviewId, ReviewDecision | None] = {}

    def publish(self, package: ReviewPackage, /) -> str:
        if self.down:
            raise ReviewDeskError("desk is down")
        return super().publish(package)

    def fetch_decision(self, review_id: ReviewId, /) -> ReviewDecision | None:
        self.fetches += 1
        if self.down:
            raise ReviewDeskError("desk is down")
        if self.published(review_id) is None:
            raise ReviewDeskError(f"review {review_id} was never published")
        return self._lines.get(review_id)

    def type(self, review_id: ReviewId, decision: ReviewDecision | None) -> None:
        """The reviewer types (or clears) the decision in the published Doc."""
        assert self.published(review_id) is not None
        self._lines[review_id] = decision


@dataclass(frozen=True)
class _Invocation:
    run: Run
    decision: FetchedDecision | None
    published: PublishedReview | None


def _invoke(
    environ: dict[str, str],
    board: _Board,
    desk: _Desk,
    producer: list[Message] | None = None,
    qa: list[Message] | None = None,
) -> tuple[_Process, _Invocation]:
    """One run of the entrypoint: read the decision, produce the brief, publish what is pending."""
    process = _Process(environ, board, producer or [], qa or [])
    decision = take_review_decision(process.store, desk, RUN_ID)
    run = process.produced()
    return process, _Invocation(run, decision, publish_pending_review(run, desk))


def _environ(tmp_path: Path) -> dict[str, str]:
    return {RUN_STORE_PATH_VAR: str(tmp_path / "stage10" / "runs.sqlite3")}


def _stored(environ: dict[str, str]) -> Run:
    run = build_run_store(environ).load(RUN_ID)
    assert run is not None
    return run


def _first(
    environ: dict[str, str], board: _Board, desk: _Desk, verdict: str = "passed"
) -> _Invocation:
    flags = [] if verdict == "passed" else [FLAG]
    return _invoke(environ, board, desk, _producer_turns(), [_verdict(verdict, flags)])[1]


def _review_of(invocation: _Invocation) -> ReviewId:
    assert invocation.published is not None
    return invocation.published.review_id


# --- S10A -----------------------------------------------------------------------------


@pytest.mark.parametrize(("verdict", "flags"), [("passed", ()), ("flagged", (FLAG,))])
def test_s10a_01_the_candidate_is_published_with_its_brief_flags_and_version(
    tmp_path: Path, verdict: str, flags: tuple[str, ...]
) -> None:
    environ = _environ(tmp_path)
    desk = _Desk()
    first = _first(environ, _filed_board(), desk, verdict)

    run = first.run
    assert run.status is WH
    [review] = run.human_reviews
    assert first.published == PublishedReview(
        review.review_id, f"memory://reviews/{review.review_id}"
    )
    candidate = run.artifacts[-1]
    assert (candidate.version, candidate.supersedes_ref, candidate.status) == (
        1,
        None,
        ArtifactStatus.CANDIDATE,
    )
    assert desk.published(review.review_id) == ReviewPackage(
        run_id=RUN_ID, review_id=review.review_id, candidate=candidate, brief=BRIEF, qa_flags=flags
    )
    assert first.decision is None
    assert _stored(environ).snapshot == run.snapshot


def test_s10a_02_an_undecided_review_keeps_the_run_waiting_and_calls_no_model(
    tmp_path: Path,
) -> None:
    environ = _environ(tmp_path)
    board, desk = _filed_board(), _Desk()
    first = _first(environ, board, desk)

    process, again = _invoke(environ, board, desk)

    assert process.silent()
    assert again.decision is None
    assert again.published == first.published
    assert again.run.snapshot == first.run.snapshot
    assert board.shown() == [Q, RN, WQ, WH]


def test_s10a_03_an_approval_on_the_desk_completes_the_run(tmp_path: Path) -> None:
    environ = _environ(tmp_path)
    board, desk = _filed_board(), _Desk()
    review_id = _review_of(_first(environ, board, desk))
    desk.type(review_id, APPROVE)

    process, done = _invoke(environ, board, desk)

    assert process.silent()
    assert done.decision == FetchedDecision(review_id, ReviewStatus.APPROVED, None, True)
    review = done.run.human_review(review_id)
    assert (review.status, review.decided_by) == (ReviewStatus.APPROVED, Actor.HUMAN_REVIEWER.value)
    assert done.run.artifacts[-1].status is ArtifactStatus.APPROVED
    assert done.run.status is C
    assert done.published is None
    assert board.shown() == [Q, RN, WQ, WH, C]
    assert _stored(environ).snapshot == done.run.snapshot


@pytest.mark.parametrize("decision", [ReviewStatus.CHANGES_REQUESTED, ReviewStatus.REJECTED])
def test_s10a_04_changes_or_a_rejection_on_the_desk_rework_and_publish_the_next_version(
    tmp_path: Path, decision: ReviewStatus
) -> None:
    environ = _environ(tmp_path)
    board, desk = _filed_board(), _Desk()
    first = _first(environ, board, desk, "flagged")
    v1_review = _review_of(first)
    desk.type(v1_review, ReviewDecision(decision, INSTRUCTIONS))

    process, reworked = _invoke(
        environ,
        board,
        desk,
        [_script_turn("Сцена 1. Подбирайте по стопе.")],
        [_verdict("passed", [])],
    )

    assert len(process.producer.calls) == 1 and len(process.qa.calls) == 1
    assert process.producer.calls[0]["system"] == load_prompt_catalogue()[leo.PROMPT_REF].system
    rework_input = json.loads(reworked.run.tasks[-1].task_input)
    assert (rework_input["human_decision"], rework_input["human_instructions"]) == (
        decision.value,
        INSTRUCTIONS,
    )
    run = reworked.run
    assert (run.human_review(v1_review).status, run.human_review(v1_review).reason) == (
        decision,
        INSTRUCTIONS,
    )
    v1, v2 = run.artifacts[-2], run.artifacts[-1]
    assert (v1.status, v2.version, v2.supersedes_ref) == (
        ArtifactStatus.SUPERSEDED,
        2,
        v1.artifact_id,
    )
    v2_review = _review_of(reworked)
    assert v2_review != v1_review
    assert desk.published(v2_review) == ReviewPackage(
        run_id=RUN_ID, review_id=v2_review, candidate=v2, brief=BRIEF, qa_flags=()
    )
    assert run.status is WH
    assert board.shown() == [Q, RN, WQ, WH, RN, WQ, WH]

    desk.type(v2_review, APPROVE)
    later, done = _invoke(environ, board, desk)

    assert later.silent()
    assert (done.run.status, done.run.artifacts[-1].status) == (C, ArtifactStatus.APPROVED)
    assert board.shown()[-1] is C


def test_s10a_05_an_approval_qa_did_not_pass_is_not_recorded_until_the_reviewer_retypes(
    tmp_path: Path,
) -> None:
    environ = _environ(tmp_path)
    board, desk = _filed_board(), _Desk()
    review_id = _review_of(_first(environ, board, desk, "flagged"))
    desk.type(review_id, APPROVE)
    before = _stored(environ).snapshot

    process, refused = _invoke(environ, board, desk)

    assert process.silent()
    assert refused.decision == FetchedDecision(review_id, ReviewStatus.APPROVED, None, False)
    assert refused.run.snapshot == before
    assert refused.run.human_review(review_id).status is ReviewStatus.PENDING
    assert refused.published is not None and refused.published.review_id == review_id

    desk.type(review_id, ReviewDecision(ReviewStatus.CHANGES_REQUESTED, INSTRUCTIONS))
    changed, reworked = _invoke(
        environ, board, desk, [_script_turn("Сцена 1. Без цифр.")], [_verdict("passed", [])]
    )

    assert len(changed.producer.calls) == 1
    assert reworked.run.human_review(review_id).status is ReviewStatus.CHANGES_REQUESTED
    assert (reworked.run.artifacts[-1].version, reworked.run.status) == (2, WH)


def test_s10a_06_a_publication_refused_or_lost_is_published_by_the_next_invocation(
    tmp_path: Path,
) -> None:
    environ = _environ(tmp_path)
    board, desk = _filed_board(), _Desk()
    process = _Process(environ, board, _producer_turns(), [_verdict("passed", [])])
    run = process.produced()
    desk.down = True
    with pytest.raises(ReviewDeskError, match="desk is down"):
        publish_pending_review(run, desk)

    [review] = run.human_reviews
    assert desk.published(review.review_id) is None
    assert _stored(environ).snapshot == run.snapshot

    desk.down = False
    later = _Process(environ, board, [], [])
    assert take_review_decision(later.store, desk, RUN_ID) is None
    assert later.silent()
    assert desk.published(review.review_id) is not None

    desk.type(review.review_id, APPROVE)
    _, done = _invoke(environ, board, desk)
    assert done.run.status is C


def test_s10a_07_a_desk_outage_while_reading_changes_nothing_and_the_next_invocation_applies(
    tmp_path: Path,
) -> None:
    environ = _environ(tmp_path)
    board, desk = _filed_board(), _Desk()
    review_id = _review_of(_first(environ, board, desk))
    desk.type(review_id, APPROVE)
    before = _stored(environ).snapshot
    desk.down = True

    process = _Process(environ, board, [], [])
    with pytest.raises(ReviewDeskError, match="desk is down"):
        take_review_decision(process.store, desk, RUN_ID)

    assert _stored(environ).snapshot == before
    assert process.silent()
    assert board.shown() == [Q, RN, WQ, WH]

    desk.down = False
    _, done = _invoke(environ, board, desk)
    assert done.decision is not None and done.decision.applied
    assert done.run.status is C


def test_s10a_08_a_crash_after_the_decision_is_saved_completes_without_asking_the_desk_again(
    tmp_path: Path,
) -> None:
    environ = _environ(tmp_path)
    board, desk = _filed_board(), _Desk()
    review_id = _review_of(_first(environ, board, desk))
    desk.type(review_id, APPROVE)
    dying = _Process(environ, board, [], [])
    fetched = take_review_decision(dying.store, desk, RUN_ID)
    assert fetched is not None and fetched.applied
    saved = _stored(environ)
    assert (saved.status, saved.human_review(review_id).status) == (WH, ReviewStatus.APPROVED)
    fetches = desk.fetches

    process, done = _invoke(environ, board, desk)

    assert process.silent()
    assert done.decision is None
    assert desk.fetches == fetches
    assert done.run.status is C
    assert board.shown() == [Q, RN, WQ, WH, C]


def test_s10a_09_rejections_past_the_rework_bound_fail_the_run_and_publish_nothing(
    tmp_path: Path,
) -> None:
    environ = _environ(tmp_path)
    board, desk = _filed_board(), _Desk()
    invocation = _first(environ, board, desk, "flagged")
    bound = invocation.run.snapshot.rework_policy.max_rework_iterations

    for iteration in range(bound):
        desk.type(_review_of(invocation), ReviewDecision(ReviewStatus.REJECTED, INSTRUCTIONS))
        _, invocation = _invoke(
            environ,
            board,
            desk,
            [_script_turn(f"Сцена 1. Вариант {iteration + 2}.")],
            [_verdict("flagged", [FLAG])],
        )
        assert (invocation.run.status, invocation.run.artifacts[-1].version) == (WH, iteration + 2)

    desk.type(_review_of(invocation), ReviewDecision(ReviewStatus.REJECTED, INSTRUCTIONS))
    process, failed = _invoke(environ, board, desk)

    assert process.silent()
    assert (failed.run.status, failed.run.failure_reason) == (F, REWORK_LIMIT_REASON)
    assert failed.published is None
    assert board.reports(BRIEF_REF)[-1] == (RUN_ID, F)
