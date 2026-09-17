"""ROADMAP Stage 11 acceptance — n8n triggers the factory over HTTP (S11A).

The real ``ProductionService`` runs on ``127.0.0.1`` over Stage 10's production path
(``STAGE10_ACCEPTANCE.md``): bundled Prompts for Rin, Leo and QA, Rin's Skill and Tool, real
``AnthropicLLMClient``s with the transport below the SDK scripted, the real ``SqliteRunStore`` under
``BriefStatusReporter``, an in-memory board and a desk playing a Google Doc — assembled as
``BriefProduction`` over ``build_run_index``. n8n is not run: each request is rendered from the
committed workflow file, as n8n would send it (``STAGE11_ACCEPTANCE.md``, ADR-0050).
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from anthropic.types import Message

from omemo_content_factory.adapters.brief_board import BriefBoardError, IncomingBrief
from omemo_content_factory.adapters.review_desk import ReviewDecision
from omemo_content_factory.adapters.run_store import RunIndex
from omemo_content_factory.agents import script_writer as leo
from omemo_content_factory.application.brief_production import BriefProduction, run_id_for_brief
from omemo_content_factory.composition import (
    RUN_STORE_PATH_VAR,
    build_production_service,
    build_run_index,
    build_run_store,
    load_prompt_catalogue,
)
from omemo_content_factory.domain.human_review import ReviewStatus
from omemo_content_factory.domain.run import Run, RunStatus
from omemo_content_factory.infrastructure.production_service import ProductionService
from tests.test_stage8_acceptance import (
    BRIEF,
    FLAG,
    INSTRUCTIONS,
    WORKFLOW,
    _producer_turns,
    _script_turn,
    _user_text,
    _verdict,
)
from tests.test_stage9_acceptance import _Board, _Process
from tests.test_stage10_acceptance import APPROVE, _Desk

N8N = Path(__file__).resolve().parent.parent / "n8n"
BRIEF_READY = N8N / "brief-ready.workflow.json"
REVIEW_SWEEP = N8N / "review-sweep.workflow.json"
TOKEN = "s11a-service-token-0123456789abcdef"
PAGE = "2f6c1c4e-8a7d-4b1e-9c3a-5d6e7f8a9b0c"
OTHER_PAGE = "7a8b9c0d-1e2f-4a3b-8c4d-5e6f7a8b9c0d"
Q, RN, WQ, WH, C = (
    RunStatus.QUEUED,
    RunStatus.RUNNING,
    RunStatus.WAITING_QA,
    RunStatus.WAITING_HUMAN,
    RunStatus.COMPLETED,
)


# --- what n8n sends -----------------------------------------------------------------------


class UnknownExpressionError(AssertionError):
    """A workflow body uses an expression this renderer does not evaluate."""


def _render(value: object, item: dict[str, Any]) -> object:
    """Evaluate the expressions the committed workflows use, and nothing else."""
    if not isinstance(value, str) or not value.startswith("="):
        return value
    if value == "={{ $json.id }}":
        return item["id"]
    raise UnknownExpressionError(value)


@dataclass(frozen=True)
class N8nRequest:
    method: str
    path: str
    body: bytes | None
    credential: str


def n8n_request(workflow_path: Path, item: dict[str, Any] | None = None) -> N8nRequest:
    """The request the workflow's HTTP Request node sends for one trigger ``item``."""
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    [node] = [n for n in workflow["nodes"] if n["type"] == "n8n-nodes-base.httpRequest"]
    parameters = node["parameters"]
    body: bytes | None = None
    if parameters.get("sendBody"):
        assert (parameters["contentType"], parameters["specifyBody"]) == ("json", "keypair")
        fields = {
            entry["name"]: _render(entry["value"], item or {})
            for entry in parameters["bodyParameters"]["parameters"]
        }
        body = json.dumps(fields).encode("utf-8")
    path = urllib.parse.urlsplit(parameters["url"]).path
    [(kind, credential)] = node["credentials"].items()
    assert kind == "httpHeaderAuth"
    return N8nRequest(parameters["method"], path, body, credential["name"])


def notion_item(page_id: str) -> dict[str, Any]:
    """One item of n8n's Notion Trigger with *Simplify* on (``simplifyObjects``)."""
    return {
        "id": page_id,
        "name": "Кроссовки",
        "url": f"https://www.notion.so/{page_id.replace('-', '')}",
        "property_stage": "Ready for production",
    }


# --- the factory behind the service -------------------------------------------------------


class _FailingBoard(_Board):
    """The board, whose brief reads can be made to fail."""

    reads_down = False
    writes = 0

    def fetch_brief(self, brief_ref: str, /) -> IncomingBrief | None:
        if self.reads_down:
            raise BriefBoardError("board unreachable")
        return super().fetch_brief(brief_ref)

    def report_status(self, brief_ref: str, /, *, run_id: str, status: RunStatus) -> None:
        self.writes += 1
        super().report_status(brief_ref, run_id=run_id, status=status)

    def report_review_location(self, brief_ref: str, /, *, run_id: str, location: str) -> None:
        self.writes += 1
        super().report_review_location(brief_ref, run_id=run_id, location=location)


class Factory:
    """One running ``factory_service.py``: scripted models, the real service over HTTP."""

    def __init__(
        self,
        environ: dict[str, str],
        board: _FailingBoard,
        desk: _Desk,
        producer: list[Message],
        qa: list[Message],
        *,
        dies_after_saves: int | None = None,
        index: RunIndex | None = None,
    ) -> None:
        self.environ = environ
        self.board = board
        self.desk = desk
        self.process = _Process(environ, board, producer, qa, dies_after_saves=dies_after_saves)
        production = BriefProduction(
            self.process.director,
            self.process.store,
            board,
            WORKFLOW,
            desk=desk,
            index=index if index is not None else build_run_index(environ),
        )
        self.service: ProductionService = build_production_service(
            {**environ, "OMEMO_SERVICE_TOKEN": TOKEN, "OMEMO_SERVICE_PORT": "0"}, production
        )
        self.service.start()

    def send(self, request: N8nRequest, token: str = TOKEN) -> tuple[int, dict[str, Any]]:
        """Send as n8n does (its credential holds ``Bearer <token>``); let the worker finish."""
        assert request.credential == "Concept factory service"
        host, port = self.service.address
        http_request = urllib.request.Request(
            f"http://{host}:{port}{request.path}",
            data=request.body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method=request.method,
        )
        try:
            with urllib.request.urlopen(http_request, timeout=5) as response:
                status, payload = response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            with error:
                status, payload = error.code, json.loads(error.read())
        assert self.service.wait_idle(10)
        return status, payload

    def page_updated(self, page_id: str = PAGE) -> tuple[int, dict[str, Any]]:
        return self.send(n8n_request(BRIEF_READY, notion_item(page_id)))

    def sweep(self) -> tuple[int, dict[str, Any]]:
        return self.send(n8n_request(REVIEW_SWEEP))

    def silent(self) -> bool:
        return self.process.silent()

    def stop(self) -> None:
        self.service.stop()


@pytest.fixture
def world(tmp_path: Path) -> Iterator[tuple[dict[str, str], _FailingBoard, _Desk]]:
    environ = {RUN_STORE_PATH_VAR: str(tmp_path / "stage11" / "runs.sqlite3")}
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
    world: tuple[dict[str, str], _FailingBoard, _Desk],
    factories: list[Factory],
    producer: list[Message] | None = None,
    qa: list[Message] | None = None,
) -> Factory:
    environ, board, desk = world
    factory = Factory(environ, board, desk, producer or [], qa or [])
    factories.append(factory)
    return factory


def _stored(environ: dict[str, str], page: str = PAGE) -> Run | None:
    return build_run_store(environ).load(run_id_for_brief(page))


def _produced(
    world: tuple[dict[str, str], _FailingBoard, _Desk],
    factories: list[Factory],
    verdict: str = "passed",
) -> Factory:
    flags = [] if verdict == "passed" else [FLAG]
    factory = _start(world, factories, _producer_turns(), [_verdict(verdict, flags)])
    assert factory.page_updated() == (202, {"brief_ref": PAGE, "queued": True})
    return factory


# --- S11A -----------------------------------------------------------------------------


def test_s11a_01_a_ready_page_updated_in_notion_starts_a_run_through_n8n(
    world: tuple[dict[str, str], _FailingBoard, _Desk], factories: list[Factory]
) -> None:
    environ, board, desk = world
    factory = _produced(world, factories)

    run = _stored(environ)
    assert run is not None
    assert (run.run_id, run.content_brief_ref, run.status) == (run_id_for_brief(PAGE), PAGE, WH)
    assert run.tasks[0].task_input == BRIEF
    assert BRIEF in _user_text(factory.process.producer.calls[0])
    assert board.shown(PAGE) == [Q, RN, WQ, WH]
    [review] = run.human_reviews
    location = f"memory://reviews/{review.review_id}"
    assert desk.published(review.review_id) is not None
    assert board.review_locations(PAGE) == ((run.run_id, location),)


def test_s11a_02_an_unready_or_unknown_page_starts_nothing(
    world: tuple[dict[str, str], _FailingBoard, _Desk], factories: list[Factory]
) -> None:
    environ, board, _ = world
    board.put(IncomingBrief(PAGE, BRIEF), ready=False)
    factory = _start(world, factories)

    assert factory.page_updated(PAGE)[0] == 202
    assert factory.page_updated(OTHER_PAGE)[0] == 202

    assert _stored(environ) is None and _stored(environ, OTHER_PAGE) is None
    assert factory.silent()
    assert board.writes == 0


def test_s11a_03_n8n_polling_again_after_the_cores_own_writes_settles(
    world: tuple[dict[str, str], _FailingBoard, _Desk], factories: list[Factory]
) -> None:
    environ, board, _ = world
    factory = _produced(world, factories)
    snapshot = _stored(environ).snapshot  # type: ignore[union-attr]
    writes, calls = board.writes, len(factory.process.producer.calls)

    for _ in range(3):
        assert factory.page_updated()[0] == 202

    assert (board.writes, len(factory.process.producer.calls)) == (writes, calls)
    assert len(factory.process.qa.calls) == 1
    assert _stored(environ).snapshot == snapshot  # type: ignore[union-attr]


def test_s11a_04_an_approval_on_the_desk_is_picked_up_by_the_scheduled_sweep(
    world: tuple[dict[str, str], _FailingBoard, _Desk], factories: list[Factory]
) -> None:
    environ, board, desk = world
    factory = _produced(world, factories)
    [review] = _stored(environ).human_reviews  # type: ignore[union-attr]
    desk.type(review.review_id, APPROVE)
    calls = len(factory.process.producer.calls), len(factory.process.qa.calls)

    assert factory.sweep() == (202, {"waiting": [PAGE], "queued": [PAGE]})

    run = _stored(environ)
    assert run is not None and run.status is C
    assert (len(factory.process.producer.calls), len(factory.process.qa.calls)) == calls
    assert board.shown(PAGE)[-1] is C
    assert factory.sweep() == (202, {"waiting": [], "queued": []})


def test_s11a_05_changes_requested_on_the_desk_are_reworked_by_the_sweep(
    world: tuple[dict[str, str], _FailingBoard, _Desk], factories: list[Factory]
) -> None:
    environ, board, desk = world
    factory = _produced(world, factories, "flagged")
    [review] = _stored(environ).human_reviews  # type: ignore[union-attr]
    desk.type(review.review_id, ReviewDecision(ReviewStatus.CHANGES_REQUESTED, INSTRUCTIONS))
    factory.process.producer.responses.append(_script_turn("Сцена 1. Подбирайте по стопе."))
    factory.process.qa.responses.append(_verdict("passed", []))
    producer_calls = len(factory.process.producer.calls)

    assert factory.sweep()[0] == 202

    [rework_call] = factory.process.producer.calls[producer_calls:]
    assert rework_call["system"] == load_prompt_catalogue()[leo.PROMPT_REF].system
    assert len(factory.process.qa.calls) == 2
    run = _stored(environ)
    assert run is not None and run.status is WH
    assert run.artifacts[-1].version == 2
    v2_review = run.human_reviews[-1]
    assert [location for _, location in board.review_locations(PAGE)] == [
        f"memory://reviews/{review.review_id}",
        f"memory://reviews/{v2_review.review_id}",
    ]


def test_s11a_06_a_misconfigured_n8n_credential_is_refused(
    world: tuple[dict[str, str], _FailingBoard, _Desk], factories: list[Factory]
) -> None:
    environ, board, _ = world
    factory = _start(world, factories)
    wrong = "not-the-service-token-0123456789abcdef"

    brief_status, _ = factory.send(n8n_request(BRIEF_READY, notion_item(PAGE)), token=wrong)
    sweep_status, _ = factory.send(n8n_request(REVIEW_SWEEP), token=wrong)

    assert (brief_status, sweep_status) == (401, 401)
    assert _stored(environ) is None
    assert factory.silent() and board.writes == 0


def test_s11a_07_a_failing_job_does_not_stop_the_factory(
    world: tuple[dict[str, str], _FailingBoard, _Desk],
    factories: list[Factory],
    caplog: pytest.LogCaptureFixture,
) -> None:
    environ, board, _ = world
    board.put(IncomingBrief(OTHER_PAGE, BRIEF))
    factory = _start(world, factories, _producer_turns(), [_verdict("passed", [])])
    board.reads_down = True

    with caplog.at_level(logging.ERROR):
        assert factory.page_updated(PAGE)[0] == 202
    [failure] = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert failure.exc_info is not None
    assert str(failure.exc_info[1]) == "board unreachable"
    assert _stored(environ) is None

    board.reads_down = False
    assert factory.page_updated(OTHER_PAGE)[0] == 202
    run = _stored(environ, OTHER_PAGE)
    assert run is not None and run.status is WH


def test_s11a_08_a_restarted_service_sweeps_a_run_the_previous_one_left_waiting(
    world: tuple[dict[str, str], _FailingBoard, _Desk], factories: list[Factory]
) -> None:
    environ, board, desk = world
    first = _produced(world, factories)
    first.stop()
    [review] = _stored(environ).human_reviews  # type: ignore[union-attr]
    desk.type(review.review_id, APPROVE)

    restarted = _start(world, factories)
    assert restarted.sweep() == (202, {"waiting": [PAGE], "queued": [PAGE]})

    run = _stored(environ)
    assert run is not None and run.status is C
    assert restarted.silent()
    assert board.shown(PAGE)[-2:] == [WH, C]


def test_s11a_09_an_expression_the_renderer_does_not_know_fails_instead_of_guessing(
    tmp_path: Path,
) -> None:
    workflow = json.loads(BRIEF_READY.read_text(encoding="utf-8"))
    [node] = [n for n in workflow["nodes"] if n["type"] == "n8n-nodes-base.httpRequest"]
    node["parameters"]["bodyParameters"]["parameters"][0]["value"] = "={{ $json.property_stage }}"
    edited = tmp_path / "edited.workflow.json"
    edited.write_text(json.dumps(workflow), encoding="utf-8")

    with pytest.raises(UnknownExpressionError):
        n8n_request(edited, notion_item(PAGE))
