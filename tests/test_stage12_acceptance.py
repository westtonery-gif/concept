"""ROADMAP Stage 12 acceptance — the whole loop, read as the MVP's own DoD (S12A).

Stage 11's production path, unchanged (``STAGE11_ACCEPTANCE.md``): the real ``ProductionService`` on
``127.0.0.1`` over ``BriefProduction`` with ``build_run_index``, bundled Prompts for Rin, Leo and
QA, Rin's Skill and Tool, real ``AnthropicLLMClient``s with the transport below the SDK scripted,
the real ``SqliteRunStore`` under ``BriefStatusReporter``, an in-memory board playing Notion and a
desk playing a Google Doc — entered through requests rendered from the committed n8n workflows.

Each test here answers one Definition of Done line of ROADMAP Этап 12 (``PROJECT.md`` §2) on the
assembled system (``STAGE12_ACCEPTANCE.md``, ADR-0051). Milestone M3 itself — one brief through the
four real external services — stays the operator's pilot (ADR-0051 §3).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest
from anthropic.types import Message

from omemo_content_factory.adapters.brief_board import IncomingBrief
from omemo_content_factory.adapters.run_store import RunIndex, RunStoreError
from omemo_content_factory.agents import content_researcher as rin
from omemo_content_factory.agents import qa_agent
from omemo_content_factory.agents import script_writer as leo
from omemo_content_factory.application.content_director import INVALID_OUTPUT_REASON
from omemo_content_factory.application.qa_evaluation import QA_VERDICT_FIELDS
from omemo_content_factory.composition import (
    RUN_STORE_PATH_VAR,
    build_run_index,
    build_run_store,
    load_prompt_catalogue,
)
from omemo_content_factory.domain.artifact import ArtifactCreated, ArtifactStatus
from omemo_content_factory.domain.evaluation import EvaluationCompleted, EvaluationStatus
from omemo_content_factory.domain.human_review import (
    HumanReviewApproved,
    HumanReviewRequested,
    ReviewStatus,
)
from omemo_content_factory.domain.output import OutputStatus
from omemo_content_factory.domain.run import (
    Actor,
    Run,
    RunCompleted,
    RunCreated,
    RunQueued,
    RunStarted,
    RunStatus,
)
from omemo_content_factory.domain.task import TaskStatus
from tests.test_stage8_acceptance import (
    BRIEF,
    FLAG,
    _message,
    _producer_turns,
    _user_text,
    _verdict,
)
from tests.test_stage9_acceptance import _CrashError
from tests.test_stage10_acceptance import APPROVE, _Desk
from tests.test_stage11_acceptance import (
    OTHER_PAGE,
    PAGE,
    Factory,
    _FailingBoard,
    _stored,
)

Q, RN, WQ, WH, C, F = (
    RunStatus.QUEUED,
    RunStatus.RUNNING,
    RunStatus.WAITING_QA,
    RunStatus.WAITING_HUMAN,
    RunStatus.COMPLETED,
    RunStatus.FAILED,
)
World = tuple[dict[str, str], _FailingBoard, _Desk]


# --- the factory behind the trigger --------------------------------------------------------


class _BrokenIndex:
    """The Run index, which can be taken down: the sweep then cannot list what waits."""

    def __init__(self, index: RunIndex) -> None:
        self._index = index
        self.down = False

    def run_ids(self, /, *, status: RunStatus) -> tuple[str, ...]:
        if self.down:
            raise RunStoreError("the run index is unreadable")
        return self._index.run_ids(status=status)


@pytest.fixture
def world(tmp_path: Path) -> Iterator[World]:
    environ = {RUN_STORE_PATH_VAR: str(tmp_path / "stage12" / "runs.sqlite3")}
    board = _FailingBoard()
    board.put(IncomingBrief(PAGE, BRIEF))
    yield environ, board, _Desk()


@pytest.fixture
def factories() -> Iterator[list[Factory]]:
    started: list[Factory] = []
    yield started
    for factory in started:
        factory.stop()


def _start(
    world: World,
    factories: list[Factory],
    producer: list[Message] | None = None,
    qa: list[Message] | None = None,
    *,
    dies_after_saves: int | None = None,
    index: RunIndex | None = None,
) -> Factory:
    """One running service over the shared SQLite file, board and desk."""
    environ, board, desk = world
    factory = Factory(
        environ,
        board,
        desk,
        producer or [],
        qa or [],
        dies_after_saves=dies_after_saves,
        index=index,
    )
    factories.append(factory)
    return factory


def _produced(
    world: World,
    factories: list[Factory],
    verdict: str = "passed",
    *,
    index: RunIndex | None = None,
) -> Factory:
    """A ready page is updated in Notion and produced to the human gate."""
    flags = [] if verdict == "passed" else [FLAG]
    factory = _start(world, factories, _producer_turns(), [_verdict(verdict, flags)], index=index)
    assert factory.page_updated() == (202, {"brief_ref": PAGE, "queued": True})
    return factory


def _run(environ: dict[str, str]) -> Run:
    run = _stored(environ)
    assert run is not None
    return run


def _invalid_producer_turns() -> list[Message]:
    """Rin asks for the date, then answers without the required ``angle``."""
    date_turn, *_ = _producer_turns()
    return [
        date_turn,
        _message(
            "emit_fields",
            {"audience": "начинающие бегуны"},
            model="claude-rin-actual",
            tokens=(1400, 90),
        ),
    ]


def _emitted_fields(call: dict[str, object]) -> tuple[str, ...]:
    """The field names the provider was told to answer with, for this call's role."""
    tools = cast(list[dict[str, Any]], call["tools"])
    [emit] = [tool for tool in tools if tool["name"] == "emit_fields"]
    return tuple(cast(dict[str, object], emit["input_schema"]["properties"]))


def _prompt_ref(prompt_id: str) -> str:
    prompt = load_prompt_catalogue()[prompt_id]
    return f"{prompt.prompt_id}@v{prompt.version.value}"


def _approve(world: World, factory: Factory) -> Run:
    """The reviewer approves the pending review on the desk; the scheduled sweep picks it up."""
    environ, _, desk = world
    [review] = _run(environ).human_reviews
    desk.type(review.review_id, APPROVE)
    assert factory.sweep()[0] == 202
    return _run(environ)


# --- S12A: DoD 1 — one brief reaches an artifact a human approved --------------------------


def test_s12a_01_one_brief_goes_from_the_trigger_to_an_artifact_a_human_approved(
    world: World, factories: list[Factory]
) -> None:
    environ, board, desk = world
    factory = _produced(world, factories)

    waiting = _run(environ)
    candidate = waiting.artifacts[-1]
    assert (waiting.status, candidate.status) == (WH, ArtifactStatus.CANDIDATE)
    [review] = waiting.human_reviews
    assert desk.published(review.review_id) is not None
    assert board.review_locations(PAGE) == (
        (waiting.run_id, f"memory://reviews/{review.review_id}"),
    )

    done = _approve(world, factory)

    assert done.status is C
    assert done.artifact(candidate.artifact_id).status is ArtifactStatus.APPROVED
    decided = done.human_review(review.review_id)
    assert (decided.status, decided.decided_by) == (
        ReviewStatus.APPROVED,
        Actor.HUMAN_REVIEWER.value,
    )
    assert board.shown(PAGE) == [Q, RN, WQ, WH, C]


# --- S12A: DoD 2 — any run is reproducible from its stored input and trace -----------------


def test_s12a_02_the_completed_run_is_reproducible_from_one_stored_row(
    world: World, factories: list[Factory]
) -> None:
    environ, _, _ = world
    factory = _produced(world, factories)
    run = _approve(world, factory)

    assert Path(environ[RUN_STORE_PATH_VAR]).is_file()
    research, script = run.tasks
    assert [task.status for task in (research, script)] == [
        TaskStatus.SUCCEEDED,
        TaskStatus.SUCCEEDED,
    ]
    assert research.task_input == BRIEF
    assert research.output is not None and script.output is not None
    assert [(o.status, o.schema_ref) for o in (research.output, script.output)] == [
        (OutputStatus.VALID, rin.SCHEMA_REF),
        (OutputStatus.VALID, leo.SCHEMA_REF),
    ]
    assert [artifact.output_ref for artifact in run.artifacts] == [
        research.output.output_id,
        script.output.output_id,
    ]
    [evaluation] = run.evaluations
    assert (evaluation.status, evaluation.evaluator_ref, evaluation.artifact_ref) == (
        EvaluationStatus.PASSED,
        qa_agent.AGENT_REF,
        run.artifacts[-1].artifact_id,
    )

    records = run.analytics_records
    assert [(r.agent_ref, r.prompt_ref) for r in records] == [
        (rin.AGENT_REF, _prompt_ref(rin.PROMPT_REF)),
        (rin.AGENT_REF, _prompt_ref(rin.PROMPT_REF)),
        (leo.AGENT_REF, _prompt_ref(leo.PROMPT_REF)),
        (qa_agent.AGENT_REF, _prompt_ref(qa_agent.PROMPT_REF)),
    ]
    assert [(r.provider, r.model) for r in records] == [
        ("anthropic", "claude-rin-actual"),
        ("anthropic", "claude-rin-actual"),
        ("anthropic", "claude-leo-actual"),
        ("anthropic", "claude-qa-actual"),
    ]
    assert [(r.token_usage.input_tokens, r.token_usage.output_tokens) for r in records] == [
        (1200, 40),
        (1400, 90),
        (900, 300),
        (2000, 60),
    ]
    assert all(r.cost.currency == "USD" and r.cost.amount > Decimal(0) for r in records)

    journal = {type(event) for event in run.events}
    assert {
        RunCreated,
        RunQueued,
        RunStarted,
        ArtifactCreated,
        EvaluationCompleted,
        HumanReviewRequested,
        HumanReviewApproved,
        RunCompleted,
    } <= journal


def test_s12a_03_replaying_the_triggers_over_a_finished_run_produces_nothing_again(
    world: World, factories: list[Factory]
) -> None:
    environ, board, _ = world
    factory = _produced(world, factories)
    snapshot = _approve(world, factory).snapshot
    writes = board.writes
    calls = len(factory.process.producer.calls), len(factory.process.qa.calls)

    assert factory.page_updated()[0] == 202
    for _ in range(2):
        assert factory.sweep() == (202, {"waiting": [], "queued": []})

    assert (len(factory.process.producer.calls), len(factory.process.qa.calls)) == calls
    assert board.writes == writes
    assert _run(environ).snapshot == snapshot


# --- S12A: DoD 3 — nothing leaves without an explicit Approve ------------------------------


def test_s12a_04_nothing_is_approved_until_a_human_explicitly_approves_a_passed_candidate(
    world: World, factories: list[Factory]
) -> None:
    environ, _, desk = world
    factory = _produced(world, factories, "flagged")
    [review] = _run(environ).human_reviews

    assert factory.sweep() == (202, {"waiting": [PAGE], "queued": [PAGE]})

    undecided = _run(environ)
    assert (undecided.status, undecided.artifacts[-1].status) == (WH, ArtifactStatus.CANDIDATE)
    assert undecided.human_review(review.review_id).status is ReviewStatus.PENDING

    desk.type(review.review_id, APPROVE)
    assert factory.sweep()[0] == 202

    refused = _run(environ)
    assert refused.human_review(review.review_id).status is ReviewStatus.PENDING
    assert refused.status is WH
    assert all(a.status is not ArtifactStatus.APPROVED for a in refused.artifacts)


# --- S12A: DoD 4 — every inter-agent message is schema-validated ---------------------------


def test_s12a_05_every_message_between_the_agents_passes_its_schema(
    world: World, factories: list[Factory]
) -> None:
    environ, _, _ = world
    factory = _produced(world, factories)
    run = _run(environ)

    research, script = run.tasks
    assert research.output is not None
    assert script.task_input == research.output.payload

    rin_call, _, leo_call = factory.process.producer.calls
    [qa_call] = factory.process.qa.calls
    assert _emitted_fields(rin_call) == rin.SCHEMAS[rin.SCHEMA_REF].view.required_fields
    assert _emitted_fields(leo_call) == leo.SCHEMAS[leo.SCHEMA_REF].view.required_fields
    assert _emitted_fields(qa_call) == QA_VERDICT_FIELDS
    assert run.artifacts[-1].content in _user_text(qa_call)


def test_s12a_06_a_message_that_fails_its_schema_stops_the_pipeline(
    world: World, factories: list[Factory]
) -> None:
    environ, board, _ = world
    factory = _start(world, factories, _invalid_producer_turns())

    assert factory.page_updated() == (202, {"brief_ref": PAGE, "queued": True})

    run = _run(environ)
    assert (run.status, run.failure_reason) == (F, INVALID_OUTPUT_REASON)
    (research,) = run.tasks
    assert research.output is not None and research.output.status is OutputStatus.INVALID
    assert run.artifacts == ()
    assert len(factory.process.producer.calls) == 2
    assert factory.process.qa.calls == []
    assert len(run.analytics_records) == 2
    assert board.shown(PAGE)[-1] is F


# --- S12A: DoD 5 — a failing step leaves the Run in a managed state ------------------------


def test_s12a_07_a_crash_during_brief_intake_leaves_a_managed_run_the_next_trigger_produces(
    world: World, factories: list[Factory], caplog: pytest.LogCaptureFixture
) -> None:
    environ, board, _ = world
    dying = _start(world, factories, dies_after_saves=1)

    with caplog.at_level(logging.ERROR):
        assert dying.page_updated()[0] == 202

    [failure] = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert failure.exc_info is not None and isinstance(failure.exc_info[1], _CrashError)
    crashed = _run(environ)
    assert (crashed.status, crashed.tasks) == (Q, ())
    assert dying.silent()
    assert dying.page_updated(OTHER_PAGE)[0] == 202

    restarted = _start(world, factories, _producer_turns(), [_verdict("passed", [])])
    assert restarted.page_updated()[0] == 202

    run = _run(environ)
    assert run.status is WH
    assert run.tasks[0].task_input == BRIEF
    assert BRIEF in _user_text(restarted.process.producer.calls[0])
    assert board.shown(PAGE)[-1] is WH


def test_s12a_08_a_trigger_the_service_cannot_dispatch_changes_nothing_and_the_next_one_does(
    world: World, factories: list[Factory]
) -> None:
    environ, _, desk = world
    index = _BrokenIndex(build_run_index(environ))
    factory = _produced(world, factories, index=index)
    [review] = _run(environ).human_reviews
    desk.type(review.review_id, APPROVE)
    before = _run(environ).snapshot
    calls = len(factory.process.producer.calls), len(factory.process.qa.calls)
    index.down = True

    assert factory.sweep() == (503, {"error": "the waiting briefs could not be listed"})

    assert _run(environ).snapshot == before
    assert (len(factory.process.producer.calls), len(factory.process.qa.calls)) == calls

    index.down = False
    assert factory.sweep() == (202, {"waiting": [PAGE], "queued": [PAGE]})

    done = _run(environ)
    assert done.status is C
    assert done.artifacts[-1].status is ArtifactStatus.APPROVED
    assert build_run_store(environ).load(done.run_id) is not None
