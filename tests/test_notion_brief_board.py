"""Tests for the Notion Adapter, ``NotionBriefBoard`` (ADR-0040).

Maps ADAPTER_ACCEPTANCE.md §8 (NBB). The adapter's real ``urllib`` code talks over a real socket to
a local HTTP server that plays Notion: it serves pages and block listings, applies ``PATCH``es to
page properties, and records every request as it arrived. Nothing is mocked below the adapter and
nothing leaves ``127.0.0.1``.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from omemo_content_factory.adapters.brief_board import BriefBoard, BriefBoardError, IncomingBrief
from omemo_content_factory.domain.run import RunStatus
from omemo_content_factory.infrastructure.notion_brief_board import (
    NOTION_API_VERSION,
    NotionBoardSettings,
    NotionBriefBoard,
    notion_settings_from_env,
)

TOKEN = "secret_integration_token_value"
DATABASE = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
PAGE = "11111111-2222-3333-4444-555555555555"

SETTINGS = NotionBoardSettings(
    token=TOKEN,
    database_id=DATABASE,
    ready_property="Stage",
    ready_value="Ready for production",
    run_status_property="Run status",
    run_id_property="Run id",
    review_link_property="Review",
)

ENV = {
    "OMEMO_NOTION_TOKEN": TOKEN,
    "OMEMO_NOTION_DATABASE_ID": DATABASE,
    "OMEMO_NOTION_READY_PROPERTY": "Stage",
    "OMEMO_NOTION_READY_VALUE": "Ready for production",
    "OMEMO_NOTION_RUN_STATUS_PROPERTY": "Run status",
    "OMEMO_NOTION_RUN_ID_PROPERTY": "Run id",
    "OMEMO_NOTION_REVIEW_LINK_PROPERTY": "Review",
}


# --- A local server playing Notion ------------------------------------------------------


@dataclass
class _Recorded:
    method: str
    path: str
    headers: dict[str, str]
    body: Any


@dataclass
class _FakeNotion:
    pages: dict[str, dict[str, Any]] = field(default_factory=dict)
    blocks: dict[str, list[list[dict[str, Any]]]] = field(default_factory=dict)
    requests: list[_Recorded] = field(default_factory=list)
    forced: tuple[int, bytes] | None = None
    fail_patch: int | None = None
    delay: float = 0.0

    def handle(self, method: str, raw_path: str, body: Any) -> tuple[int, bytes]:
        if self.delay:
            time.sleep(self.delay)
        if self.forced is not None:
            return self.forced
        parts = urlsplit(raw_path)
        segments = parts.path.split("/")
        if segments[1:3] == ["v1", "pages"] and len(segments) == 4:
            return self._page(method, segments[3], body)
        if segments[1:3] == ["v1", "blocks"] and len(segments) == 5 and segments[4] == "children":
            cursor = parse_qs(parts.query).get("start_cursor", ["0"])[0]
            return self._listing(segments[3], int(cursor))
        return _json(400, {"object": "error", "code": "invalid_request_url"})

    def _page(self, method: str, page_id: str, body: Any) -> tuple[int, bytes]:
        page = self.pages.get(page_id)
        if page is None:
            return _json(404, {"object": "error", "code": "object_not_found"})
        if method == "PATCH":
            if self.fail_patch is not None:
                return _json(self.fail_patch, {"object": "error", "code": "conflict_error"})
            for name, value in body["properties"].items():
                page["properties"][name].update(value)
        return _json(200, page)

    def _listing(self, page_id: str, index: int) -> tuple[int, bytes]:
        chunks = self.blocks.get(page_id)
        if chunks is None:
            return _json(404, {"object": "error", "code": "object_not_found"})
        has_more = index + 1 < len(chunks)
        return _json(
            200,
            {
                "object": "list",
                "results": chunks[index],
                "has_more": has_more,
                "next_cursor": str(index + 1) if has_more else None,
            },
        )


def _json(status: int, payload: object) -> tuple[int, bytes]:
    return status, json.dumps(payload).encode("utf-8")


@pytest.fixture
def notion() -> Iterator[tuple[_FakeNotion, str]]:
    fake = _FakeNotion()

    class Handler(BaseHTTPRequestHandler):
        def _serve(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            body = json.loads(raw) if raw else None
            fake.requests.append(_Recorded(self.command, self.path, dict(self.headers), body))
            status, payload = fake.handle(self.command, self.path, body)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = _serve  # noqa: N815
        do_PATCH = _serve  # noqa: N815

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    try:
        yield fake, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _page(
    *,
    stage: str | None = "Ready for production",
    stage_type: str = "status",
    database: str = DATABASE,
    archived: bool = False,
    in_trash: bool = False,
) -> dict[str, Any]:
    option = None if stage is None else {"id": "opt", "name": stage}
    return {
        "object": "page",
        "id": PAGE,
        "archived": archived,
        "in_trash": in_trash,
        "parent": {"type": "database_id", "database_id": database},
        "properties": {
            "Stage": {"id": "a", "type": stage_type, stage_type: option},
            "Run status": {"id": "b", "type": "rich_text", "rich_text": []},
            "Run id": {"id": "c", "type": "rich_text", "rich_text": []},
            "Review": {"id": "d", "type": "url", "url": None},
        },
    }


def _text_block(kind: str, *pieces: str) -> dict[str, Any]:
    chunks = [{"type": "text", "plain_text": piece} for piece in pieces]
    return {"object": "block", "type": kind, kind: {"rich_text": chunks}}


def _image_block() -> dict[str, Any]:
    return {"object": "block", "type": "image", "image": {"type": "external"}}


def _file(fake: _FakeNotion, page: dict[str, Any], *chunks: list[dict[str, Any]]) -> None:
    fake.pages[PAGE] = page
    fake.blocks[PAGE] = list(chunks) or [[_text_block("paragraph", "Brief text")]]


def _board(url: str, **kwargs: Any) -> NotionBriefBoard:
    return NotionBriefBoard(SETTINGS, api_url=url, **kwargs)


def _text(prop: dict[str, Any]) -> str:
    return "".join(chunk["text"]["content"] for chunk in prop["rich_text"])


# --- NBB-01 ------------------------------------------------------------------------------


def test_nbb_01_the_adapter_is_the_contract_and_sends_the_pinned_headers(
    notion: tuple[_FakeNotion, str],
) -> None:
    fake, url = notion
    _file(fake, _page())
    board: BriefBoard = _board(url)

    board.fetch_brief(PAGE)
    board.report_status(PAGE, run_id="run-1", status=RunStatus.QUEUED)

    assert [r.method for r in fake.requests] == ["GET", "GET", "GET", "PATCH"]
    for recorded in fake.requests:
        assert recorded.headers["Authorization"] == f"Bearer {TOKEN}"
        assert recorded.headers["Notion-Version"] == NOTION_API_VERSION == "2022-06-28"
    assert fake.requests[-1].headers["Content-Type"] == "application/json"


# --- NBB-02 ------------------------------------------------------------------------------


def test_nbb_02_a_ready_page_is_a_brief_of_its_text_blocks_across_every_listing_page(
    notion: tuple[_FakeNotion, str],
) -> None:
    fake, url = notion
    _file(
        fake,
        _page(),
        [_text_block("heading_2", "Тема"), _image_block()],
        [_text_block("paragraph", "Аудитория: ", "владельцы кофеен"), _text_block("to_do", "CTA")],
    )

    brief = _board(url).fetch_brief(PAGE)

    assert brief == IncomingBrief(brief_ref=PAGE, body="Тема\nАудитория: владельцы кофеен\nCTA")
    listings = [r.path for r in fake.requests if "/children" in r.path]
    assert len(listings) == 2
    assert "start_cursor=1" in listings[1]


def test_nbb_02_a_select_readiness_property_works_like_a_status(
    notion: tuple[_FakeNotion, str],
) -> None:
    fake, url = notion
    _file(fake, _page(stage_type="select"))

    assert _board(url).fetch_brief(PAGE) == IncomingBrief(brief_ref=PAGE, body="Brief text")


# --- NBB-03 ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "page",
    [
        _page(archived=True),
        _page(in_trash=True),
        _page(database="99999999-9999-9999-9999-999999999999"),
        _page(stage="Draft"),
        _page(stage=None),
    ],
    ids=["archived", "in-trash", "other-database", "not-ready", "no-option"],
)
def test_nbb_03_a_page_that_is_not_a_ready_brief_on_this_board_is_none(
    notion: tuple[_FakeNotion, str], page: dict[str, Any]
) -> None:
    fake, url = notion
    _file(fake, page)

    assert _board(url).fetch_brief(PAGE) is None


def test_nbb_03_an_unknown_page_is_none(notion: tuple[_FakeNotion, str]) -> None:
    _, url = notion

    assert _board(url).fetch_brief(PAGE) is None


def test_nbb_03_a_blank_ref_is_none_without_a_request(notion: tuple[_FakeNotion, str]) -> None:
    fake, url = notion

    assert _board(url).fetch_brief("  ") is None
    assert fake.requests == []


def test_nbb_03_a_ready_page_without_text_is_none(notion: tuple[_FakeNotion, str]) -> None:
    fake, url = notion
    _file(fake, _page(), [_image_block(), _text_block("paragraph", "   ")])

    assert _board(url).fetch_brief(PAGE) is None


def test_nbb_03_the_same_database_spelled_differently_is_this_board(
    notion: tuple[_FakeNotion, str],
) -> None:
    fake, url = notion
    _file(fake, _page(database=DATABASE.replace("-", "").upper()))

    assert _board(url).fetch_brief(PAGE) is not None


# --- NBB-04 ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "page",
    [
        {**_page(), "properties": {}},
        _page(stage_type="checkbox"),
    ],
    ids=["missing-property", "wrong-type"],
)
def test_nbb_04_a_misconfigured_readiness_property_is_an_error_not_none(
    notion: tuple[_FakeNotion, str], page: dict[str, Any]
) -> None:
    fake, url = notion
    _file(fake, page)

    with pytest.raises(BriefBoardError, match="Stage"):
        _board(url).fetch_brief(PAGE)


@pytest.mark.parametrize("status", [400, 401, 429, 500])
def test_nbb_04_any_other_refusal_is_an_error(notion: tuple[_FakeNotion, str], status: int) -> None:
    fake, url = notion
    fake.forced = _json(status, {"object": "error"})

    with pytest.raises(BriefBoardError, match=f"HTTP {status}"):
        _board(url).fetch_brief(PAGE)


@pytest.mark.parametrize(
    "payload", [b"<html>not json</html>", b"[1, 2]"], ids=["not-json", "not-object"]
)
def test_nbb_04_a_response_that_is_not_a_json_object_is_an_error(
    notion: tuple[_FakeNotion, str], payload: bytes
) -> None:
    fake, url = notion
    fake.forced = (200, payload)

    with pytest.raises(BriefBoardError, match="JSON"):
        _board(url).fetch_brief(PAGE)


def test_nbb_04_a_block_listing_of_the_wrong_shape_is_an_error(
    notion: tuple[_FakeNotion, str],
) -> None:
    fake, url = notion
    _file(fake, _page())
    board = _board(url)
    fake.blocks[PAGE] = [{"not": "a list"}]  # type: ignore[list-item]

    with pytest.raises(BriefBoardError, match="block listing"):
        board.fetch_brief(PAGE)


def test_nbb_04_an_unreachable_server_is_an_error() -> None:
    with pytest.raises(BriefBoardError, match="could not be reached"):
        _board("http://127.0.0.1:9").fetch_brief(PAGE)


def test_nbb_04_a_timeout_is_an_error(notion: tuple[_FakeNotion, str]) -> None:
    fake, url = notion
    _file(fake, _page())
    fake.delay = 0.5

    with pytest.raises(BriefBoardError, match="could not be reached"):
        _board(url, timeout=0.1).fetch_brief(PAGE)


# --- NBB-05 ------------------------------------------------------------------------------


def test_nbb_05_a_status_is_written_into_the_two_text_properties_and_a_repeat_is_harmless(
    notion: tuple[_FakeNotion, str],
) -> None:
    fake, url = notion
    _file(fake, _page())
    board = _board(url)

    board.report_status(PAGE, run_id="run-7", status=RunStatus.WAITING_HUMAN)
    after_first = json.dumps(fake.pages[PAGE], sort_keys=True)
    board.report_status(PAGE, run_id="run-7", status=RunStatus.WAITING_HUMAN)

    properties = fake.pages[PAGE]["properties"]
    assert _text(properties["Run status"]) == "waiting_human"
    assert _text(properties["Run id"]) == "run-7"
    assert json.dumps(fake.pages[PAGE], sort_keys=True) == after_first
    patch = fake.requests[-1]
    assert patch.method == "PATCH"
    assert set(patch.body["properties"]) == {"Run status", "Run id"}


def test_nbb_05_a_later_status_replaces_the_earlier_one(notion: tuple[_FakeNotion, str]) -> None:
    fake, url = notion
    _file(fake, _page())
    board = _board(url)

    board.report_status(PAGE, run_id="run-7", status=RunStatus.RUNNING)
    board.report_status(PAGE, run_id="run-7", status=RunStatus.COMPLETED)

    assert _text(fake.pages[PAGE]["properties"]["Run status"]) == "completed"


# --- NBB-06 ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "page",
    [
        None,
        _page(archived=True),
        _page(in_trash=True),
        _page(database="99999999-9999-9999-9999-999999999999"),
    ],
    ids=["unknown", "archived", "in-trash", "other-database"],
)
def test_nbb_06_a_status_for_a_page_not_on_the_board_is_refused_before_any_write(
    notion: tuple[_FakeNotion, str], page: dict[str, Any] | None
) -> None:
    fake, url = notion
    if page is not None:
        _file(fake, page)

    with pytest.raises(BriefBoardError, match="not on the board"):
        _board(url).report_status(PAGE, run_id="run-1", status=RunStatus.QUEUED)
    assert "PATCH" not in [r.method for r in fake.requests]


def test_nbb_06_a_blank_ref_is_refused_without_a_request(notion: tuple[_FakeNotion, str]) -> None:
    fake, url = notion

    with pytest.raises(BriefBoardError):
        _board(url).report_status("", run_id="run-1", status=RunStatus.QUEUED)
    assert fake.requests == []


@pytest.mark.parametrize("name", ["Run status", "Run id"])
@pytest.mark.parametrize("broken", ["missing", "wrong-type"])
def test_nbb_06_a_misconfigured_status_property_is_refused_before_any_write(
    notion: tuple[_FakeNotion, str], name: str, broken: str
) -> None:
    fake, url = notion
    page = _page()
    if broken == "missing":
        del page["properties"][name]
    else:
        page["properties"][name] = {"id": "x", "type": "number", "number": None}
    _file(fake, page)

    with pytest.raises(BriefBoardError, match=name):
        _board(url).report_status(PAGE, run_id="run-1", status=RunStatus.QUEUED)
    assert "PATCH" not in [r.method for r in fake.requests]


def test_nbb_06_a_failed_write_is_an_error(notion: tuple[_FakeNotion, str]) -> None:
    fake, url = notion
    _file(fake, _page())
    fake.fail_patch = 409

    with pytest.raises(BriefBoardError, match="HTTP 409"):
        _board(url).report_status(PAGE, run_id="run-1", status=RunStatus.QUEUED)


# --- NBB-07 ------------------------------------------------------------------------------


@pytest.mark.parametrize("ref", ["../../v1/users", "a/b", "x?filter=1", "id#frag", ".."])
def test_nbb_07_a_ref_stays_one_path_segment(notion: tuple[_FakeNotion, str], ref: str) -> None:
    fake, url = notion

    assert _board(url).fetch_brief(ref) is None

    (recorded,) = fake.requests
    path = urlsplit(recorded.path).path
    assert path.startswith("/v1/pages/")
    assert path.count("/") == 3
    assert "." not in path.removeprefix("/v1/pages/")
    assert urlsplit(recorded.path).query == ""


# --- NBB-08 ------------------------------------------------------------------------------


def test_nbb_08_settings_come_from_the_environment() -> None:
    assert notion_settings_from_env(ENV) == SETTINGS


def test_nbb_08_every_missing_or_blank_variable_is_named_without_values() -> None:
    environ = {**ENV, "OMEMO_NOTION_DATABASE_ID": "  "}
    del environ["OMEMO_NOTION_READY_VALUE"]

    with pytest.raises(BriefBoardError) as refused:
        notion_settings_from_env(environ)

    message = str(refused.value)
    assert "OMEMO_NOTION_DATABASE_ID" in message
    assert "OMEMO_NOTION_READY_VALUE" in message
    assert "OMEMO_NOTION_TOKEN" not in message
    assert TOKEN not in message


def test_nbb_08_the_token_never_shows_in_repr_or_errors(notion: tuple[_FakeNotion, str]) -> None:
    fake, url = notion
    fake.forced = _json(401, {"object": "error", "message": "bad token"})

    assert TOKEN not in repr(SETTINGS)
    with pytest.raises(BriefBoardError) as refused:
        _board(url).fetch_brief(PAGE)
    assert TOKEN not in str(refused.value)


def test_nbb_08_blank_settings_are_refused() -> None:
    with pytest.raises(ValueError, match="token"):
        NotionBoardSettings(
            token=" ",
            database_id=DATABASE,
            ready_property="Stage",
            ready_value="Ready",
            run_status_property="Run status",
            run_id_property="Run id",
            review_link_property="Review",
        )


@pytest.mark.parametrize("variable", ["OMEMO_NOTION_REVIEW_LINK_PROPERTY"])
def test_nbb_08_the_review_link_property_is_required_too(variable: str) -> None:
    environ = {**ENV}
    del environ[variable]

    with pytest.raises(BriefBoardError, match=variable):
        notion_settings_from_env(environ)


# --- NBB-09/10: the review link (ADR-0047) ------------------------------------------------

DOC = "https://docs.google.com/document/d/doc-1/edit"


def test_nbb_09_a_review_location_is_written_into_the_url_property_and_a_repeat_is_harmless(
    notion: tuple[_FakeNotion, str],
) -> None:
    fake, url = notion
    _file(fake, _page())
    board: BriefBoard = _board(url)

    board.report_review_location(PAGE, run_id="run-7", location=DOC)
    after_first = json.dumps(fake.pages[PAGE], sort_keys=True)
    board.report_review_location(PAGE, run_id="run-7", location=DOC)

    assert fake.pages[PAGE]["properties"]["Review"]["url"] == DOC
    assert json.dumps(fake.pages[PAGE], sort_keys=True) == after_first
    patch = fake.requests[-1]
    assert (patch.method, patch.body) == ("PATCH", {"properties": {"Review": {"url": DOC}}})
    assert patch.headers["Authorization"] == f"Bearer {TOKEN}"
    assert _text(fake.pages[PAGE]["properties"]["Run status"]) == ""


def test_nbb_09_a_later_location_replaces_the_earlier_one(notion: tuple[_FakeNotion, str]) -> None:
    fake, url = notion
    _file(fake, _page())
    board = _board(url)

    board.report_review_location(PAGE, run_id="run-7", location=DOC)
    board.report_review_location(PAGE, run_id="run-7", location=DOC.replace("doc-1", "doc-2"))

    assert fake.pages[PAGE]["properties"]["Review"]["url"].endswith("/doc-2/edit")


@pytest.mark.parametrize(
    "page",
    [
        None,
        _page(archived=True),
        _page(in_trash=True),
        _page(database="99999999-9999-9999-9999-999999999999"),
    ],
    ids=["unknown", "archived", "in-trash", "other-database"],
)
def test_nbb_10_a_location_for_a_page_not_on_the_board_is_refused_before_any_write(
    notion: tuple[_FakeNotion, str], page: dict[str, Any] | None
) -> None:
    fake, url = notion
    if page is not None:
        _file(fake, page)

    with pytest.raises(BriefBoardError, match="not on the board"):
        _board(url).report_review_location(PAGE, run_id="run-1", location=DOC)
    assert "PATCH" not in [r.method for r in fake.requests]


@pytest.mark.parametrize(("ref", "location"), [("", DOC), (PAGE, " ")], ids=["ref", "location"])
def test_nbb_10_a_blank_ref_or_location_is_refused_without_a_request(
    notion: tuple[_FakeNotion, str], ref: str, location: str
) -> None:
    fake, url = notion
    _file(fake, _page())

    with pytest.raises(BriefBoardError, match="blank"):
        _board(url).report_review_location(ref, run_id="run-1", location=location)
    assert fake.requests == []


@pytest.mark.parametrize("broken", ["missing", "rich-text"])
def test_nbb_10_a_misconfigured_link_property_is_refused_before_any_write(
    notion: tuple[_FakeNotion, str], broken: str
) -> None:
    fake, url = notion
    page = _page()
    if broken == "missing":
        del page["properties"]["Review"]
    else:
        page["properties"]["Review"] = {"id": "d", "type": "rich_text", "rich_text": []}
    _file(fake, page)

    with pytest.raises(BriefBoardError, match="Review"):
        _board(url).report_review_location(PAGE, run_id="run-1", location=DOC)
    assert "PATCH" not in [r.method for r in fake.requests]


def test_nbb_10_a_failed_link_write_is_an_error(notion: tuple[_FakeNotion, str]) -> None:
    fake, url = notion
    _file(fake, _page())
    fake.fail_patch = 502

    with pytest.raises(BriefBoardError, match="HTTP 502"):
        _board(url).report_review_location(PAGE, run_id="run-1", location=DOC)
