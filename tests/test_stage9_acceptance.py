"""ROADMAP Stage 9 acceptance — a brief on the board becomes a produced Run, shown back (S9A).

The brief is filed on an ``InMemoryBriefBoard`` and produced only through
``application.brief_intake.produce_brief``. The rest is Stage 8's production path
(``STAGE8_ACCEPTANCE.md``, whose scripted SDK transport is reused): the bundled Prompt store for
Rin, Leo and QA, Rin's Skill and Tool, a real ``AnthropicLLMClient`` for the producers and another
for QA, and the real ``SqliteRunStore`` — wrapped in ``BriefStatusReporter`` over the same board.
Every restart compiles a new Root and a new reporter over the same SQLite file and the same board
(``STAGE9_ACCEPTANCE.md``, ADR-0042).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import anthropic
import pytest
from anthropic.types import Message

from omemo_content_factory.adapters.brief_board import BriefBoardError, IncomingBrief
from omemo_content_factory.adapters.run_store import RunStore
from omemo_content_factory.agents import qa_agent
from omemo_content_factory.agents import script_writer as leo
from omemo_content_factory.application.brief_intake import BriefIntakeError, produce_brief
from omemo_content_factory.application.brief_status import BriefStatusReporter
from omemo_content_factory.application.qa_evaluation import QaVerdictError
from omemo_content_factory.composition import (
    RUN_STORE_PATH_VAR,
    build_available_tools,
    build_qa_evaluator,
    build_run_store,
    compile_runtime,
    load_prompt_catalogue,
)
from omemo_content_factory.domain.evaluation import EvaluationStatus
from omemo_content_factory.domain.human_review import ReviewStatus
from omemo_content_factory.domain.run import Actor, Run, RunStatus
from omemo_content_factory.infrastructure.in_memory_adapters import InMemoryBriefBoard
from omemo_content_factory.infrastructure.llm import AnthropicLLMClient
from tests.test_stage8_acceptance import (
    _PRODUCER_PRICING,
    _QA_PRICING,
    _TODAY,
    AGENTS,
    BRIEF,
    FLAG,
    INSTRUCTIONS,
    SCHEMAS,
    SKILL_INVOCATIONS,
    WORKFLOW,
    _producer_turns,
    _script_turn,
    _ScriptedAnthropic,
    _ticking_clock,
    _user_text,
    _verdict,
)

BRIEF_REF = "brief-running-shoes"
RUN_ID = "run-stage9-acceptance"
Q, RN, WQ, WH, C = (
    RunStatus.QUEUED,
    RunStatus.RUNNING,
    RunStatus.WAITING_QA,
    RunStatus.WAITING_HUMAN,
    RunStatus.COMPLETED,
)


# --- the board, and a process that can die ------------------------------------------------


class _Board(InMemoryBriefBoard):
    """The in-memory board, which can be taken down: every report is then refused."""

    down = False

    def report_status(self, brief_ref: str, /, *, run_id: str, status: RunStatus) -> None:
        if self.down:
            raise BriefBoardError("board is down")
        super().report_status(brief_ref, run_id=run_id, status=status)

    def shown(self, brief_ref: str = BRIEF_REF) -> list[RunStatus]:
        return [status for _, status in self.reports(brief_ref)]


class _CrashError(Exception):
    """The process dies."""


class _DyingStore:
    """A store whose process dies at the save after ``saves`` successful ones."""

    def __init__(self, store: RunStore, saves: int) -> None:
        self._store = store
        self._left = saves

    def save(self, run: Run, /) -> None:
        if self._left == 0:
            raise _CrashError
        self._left -= 1
        self._store.save(run)

    def load(self, run_id: str, /) -> Run | None:
        return self._store.load(run_id)


class _Process:
    """One process: a Root compiled over the SQLite file, committing through a board reporter."""

    def __init__(
        self,
        environ: dict[str, str],
        board: _Board,
        producer: list[Message],
        qa: list[Message],
        *,
        dies_after_saves: int | None = None,
    ) -> None:
        producer_sdk = _ScriptedAnthropic(producer)
        qa_sdk = _ScriptedAnthropic(qa)
        self.producer = producer_sdk.messages
        self.qa = qa_sdk.messages
        self.board = board
        sqlite = build_run_store(environ)
        inner = sqlite if dies_after_saves is None else _DyingStore(sqlite, dies_after_saves)
        self.store = BriefStatusReporter(inner, board)
        producer_client = AnthropicLLMClient(
            model="producer-alias",
            pricing=_PRODUCER_PRICING,
            client=cast(anthropic.Anthropic, producer_sdk),
            clock=_ticking_clock(),
        )
        qa_client = AnthropicLLMClient(
            model="qa-alias",
            pricing=_QA_PRICING,
            client=cast(anthropic.Anthropic, qa_sdk),
            clock=_ticking_clock(),
        )
        self.director = compile_runtime(
            AGENTS,
            None,
            producer_client,
            WORKFLOW,
            SCHEMAS,
            skill_invocations=SKILL_INVOCATIONS,
            available_tools=build_available_tools(clock=lambda: _TODAY),
            store=self.store,
            qa=build_qa_evaluator(qa_agent.QA_AGENT, None, qa_client, qa_agent.SCHEMAS),
        )

    def produce(self, brief_ref: str = BRIEF_REF, run_id: str = RUN_ID) -> Run | None:
        return produce_brief(
            self.director, self.store, self.board, WORKFLOW, brief_ref=brief_ref, run_id=run_id
        )

    def produced(self) -> Run:
        run = self.produce()
        assert run is not None
        return run

    def silent(self) -> bool:
        return self.producer.calls == [] and self.qa.calls == []


def _environ(tmp_path: Path) -> dict[str, str]:
    return {RUN_STORE_PATH_VAR: str(tmp_path / "stage9" / "runs.sqlite3")}


def _filed_board() -> _Board:
    board = _Board()
    board.put(IncomingBrief(BRIEF_REF, BRIEF))
    return board


def _stored(environ: dict[str, str]) -> Run | None:
    return build_run_store(environ).load(RUN_ID)


# --- S9A ------------------------------------------------------------------------------


def test_s9a_01_a_filed_brief_produces_a_valid_run(tmp_path: Path) -> None:
    environ = _environ(tmp_path)
    process = _Process(environ, _filed_board(), _producer_turns(), [_verdict("passed", [])])
    run = process.produced()

    assert (run.run_id, run.content_brief_ref, run.workflow_version_ref) == (
        RUN_ID,
        BRIEF_REF,
        WORKFLOW.workflow_id,
    )
    assert run.tasks[0].task_input == BRIEF
    assert BRIEF in _user_text(process.producer.calls[0])
    assert run.status is WH
    stored = _stored(environ)
    assert stored is not None and stored.snapshot == run.snapshot


def test_s9a_02_every_status_lands_back_on_the_brief(tmp_path: Path) -> None:
    environ = _environ(tmp_path)
    board = _filed_board()
    process = _Process(environ, board, _producer_turns(), [_verdict("passed", [])])
    run = process.produced()

    assert board.reports(BRIEF_REF) == tuple((RUN_ID, s) for s in (Q, RN, WQ, WH))
    assert process.store.shown_status(RUN_ID) is run.status
    assert process.store.failed_reports == ()

    decided = _stored(environ)
    assert decided is not None
    [pending] = decided.human_reviews
    decided.submit_review(pending.review_id, ReviewStatus.APPROVED, by=Actor.HUMAN_REVIEWER)
    build_run_store(environ).save(decided)
    later = _Process(environ, board, [], [])
    done = later.produced()

    assert later.silent()
    assert done.status is C
    assert board.reports(BRIEF_REF) == tuple((RUN_ID, s) for s in (Q, RN, WQ, WH, C))


@pytest.mark.parametrize("ready", [None, False])
def test_s9a_03_an_unproducible_brief_starts_nothing(tmp_path: Path, ready: bool | None) -> None:
    environ = _environ(tmp_path)
    board = _Board()
    if ready is not None:
        board.put(IncomingBrief(BRIEF_REF, BRIEF), ready=ready)
    process = _Process(environ, board, [], [])

    assert process.produce() is None
    assert _stored(environ) is None
    assert process.silent()
    assert board.shown() == []


def test_s9a_04_a_human_requested_rework_after_a_restart_is_shown(tmp_path: Path) -> None:
    environ = _environ(tmp_path)
    board = _filed_board()
    first = _Process(environ, board, _producer_turns(), [_verdict("flagged", [FLAG])]).produced()
    assert first.status is WH
    assert board.shown() == [Q, RN, WQ, WH]

    decided = _stored(environ)
    assert decided is not None
    [pending] = decided.human_reviews
    decided.submit_review(
        pending.review_id,
        ReviewStatus.CHANGES_REQUESTED,
        by=Actor.HUMAN_REVIEWER,
        reason=INSTRUCTIONS,
    )
    build_run_store(environ).save(decided)

    restart = _Process(
        environ, board, [_script_turn("Сцена 1. Подбирайте по стопе.")], [_verdict("passed", [])]
    )
    run = restart.produced()

    [leo_call] = restart.producer.calls
    assert leo_call["system"] == load_prompt_catalogue()[leo.PROMPT_REF].system
    assert json.loads(run.tasks[-1].task_input)["human_instructions"] == INSTRUCTIONS
    v2 = run.artifacts[-1]
    assert v2.version == 2
    assert run.evaluations[-1].artifact_ref == v2.artifact_id
    assert run.status is WH
    assert board.shown() == [Q, RN, WQ, WH, RN, WQ, WH]


def test_s9a_05_a_crash_before_the_first_task_resumes_on_the_brief_from_the_board(
    tmp_path: Path,
) -> None:
    environ = _environ(tmp_path)
    board = _filed_board()
    dying = _Process(environ, board, [], [], dies_after_saves=1)
    with pytest.raises(_CrashError):
        dying.produce()
    crashed = _stored(environ)
    assert crashed is not None and (crashed.status, crashed.tasks) == (Q, ())
    assert dying.silent()

    restart = _Process(environ, board, _producer_turns(), [_verdict("passed", [])])
    run = restart.produced()

    assert run.tasks[0].task_input == BRIEF
    assert BRIEF in _user_text(restart.producer.calls[0])
    assert run.status is WH
    assert board.shown()[-1] is WH


def test_s9a_05_a_crash_before_the_first_task_with_the_brief_withdrawn_is_refused(
    tmp_path: Path,
) -> None:
    environ = _environ(tmp_path)
    board = _filed_board()
    with pytest.raises(_CrashError):
        _Process(environ, board, [], [], dies_after_saves=1).produce()
    crashed = _stored(environ)
    assert crashed is not None
    board.put(IncomingBrief(BRIEF_REF, BRIEF), ready=False)

    restart = _Process(environ, board, [], [])
    with pytest.raises(BriefIntakeError):
        restart.produce()

    after = _stored(environ)
    assert after is not None and after.snapshot == crashed.snapshot
    assert restart.silent()
    assert board.shown() == [Q]


def test_s9a_06_a_qa_error_is_shown_parked_and_a_restart_asks_again(tmp_path: Path) -> None:
    environ = _environ(tmp_path)
    board = _filed_board()
    failing = _Process(environ, board, _producer_turns(), [_verdict("ok", [])])
    with pytest.raises(QaVerdictError):
        failing.produce()

    parked = _stored(environ)
    assert parked is not None and parked.status is WQ
    assert board.shown()[-1] is WQ
    assert failing.store.shown_status(RUN_ID) is WQ

    restart = _Process(environ, board, [], [_verdict("passed", [])])
    run = restart.produced()

    assert restart.producer.calls == []
    assert len(restart.qa.calls) == 1
    [evaluation] = run.evaluations
    assert (evaluation.evaluation_id, evaluation.status) == (
        parked.evaluations[0].evaluation_id,
        EvaluationStatus.PASSED,
    )
    assert run.status is WH
    assert board.shown()[-1] is WH


def test_s9a_07_a_board_outage_never_stops_the_run_and_the_next_invocation_catches_up(
    tmp_path: Path,
) -> None:
    reference = _Process(
        _environ(tmp_path / "reference"),
        _filed_board(),
        _producer_turns(),
        [_verdict("passed", [])],
    ).produced()

    environ = _environ(tmp_path)
    board = _filed_board()
    board.down = True
    outage = _Process(environ, board, _producer_turns(), [_verdict("passed", [])])
    run = outage.produced()

    assert run.status is WH
    assert run.snapshot == reference.snapshot
    assert [f.status for f in outage.store.failed_reports] == [Q, RN, WQ, WH, WH]
    assert board.shown() == []

    board.down = False
    later = _Process(environ, board, [], [])
    again = later.produced()

    assert later.silent()
    assert again.snapshot == run.snapshot
    assert board.shown() == [WH]


def test_s9a_08_a_stored_run_of_another_brief_is_refused_untouched(tmp_path: Path) -> None:
    environ = _environ(tmp_path)
    board = _filed_board()
    run = _Process(environ, board, _producer_turns(), [_verdict("passed", [])]).produced()
    board.put(IncomingBrief("brief-other", "Тема: другое."))

    other = _Process(environ, board, [], [])
    with pytest.raises(BriefIntakeError):
        other.produce(brief_ref="brief-other")

    after = _stored(environ)
    assert after is not None and after.snapshot == run.snapshot
    assert other.silent()
    assert board.shown("brief-other") == []
