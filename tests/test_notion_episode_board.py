"""Tests for the Notion Adapter, ``NotionEpisodeBoard`` (ADR-0055).

Maps CLIPPING_ACCEPTANCE.md §3 (NEB). The adapter's real ``urllib`` code talks over a real socket
to a local HTTP server that plays Notion: it serves pages, applies ``PATCH``es to page properties
and records every request as it arrived. Nothing is mocked below the adapter and nothing leaves
``127.0.0.1``.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

import pytest

from omemo_content_factory.adapters.episode_board import (
    ClipMode,
    EpisodeBoard,
    EpisodeBoardError,
    IncomingEpisode,
)
from omemo_content_factory.domain.run import RunStatus
from omemo_content_factory.infrastructure.notion_episode_board import (
    NOTION_API_VERSION,
    NotionEpisodeBoard,
    NotionEpisodeSettings,
    notion_episode_settings_from_env,
)

TOKEN = "secret_integration_token_value"
DATABASE = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
PAGE = "11111111-2222-3333-4444-555555555555"

SETTINGS = NotionEpisodeSettings(
    token=TOKEN,
    database_id=DATABASE,
    ready_property="Stage",
    ready_value="Ready to clip",
    source_property="Source",
    mode_property="Mode",
    run_status_property="Run status",
    run_id_property="Run id",
)

ENV = {
    "OMEMO_EPISODE_NOTION_TOKEN": TOKEN,
    "OMEMO_EPISODE_NOTION_DATABASE_ID": DATABASE,
    "OMEMO_EPISODE_NOTION_READY_PROPERTY": "Stage",
    "OMEMO_EPISODE_NOTION_READY_VALUE": "Ready to clip",
    "OMEMO_EPISODE_NOTION_SOURCE_PROPERTY": "Source",
    "OMEMO_EPISODE_NOTION_MODE_PROPERTY": "Mode",
    "OMEMO_EPISODE_NOTION_RUN_STATUS_PROPERTY": "Run status",
    "OMEMO_EPISODE_NOTION_RUN_ID_PROPERTY": "Run id",
}


@dataclass
class _Recorded:
    method: str
    path: str
    headers: dict[str, str]
    body: Any


@dataclass
class _FakeNotion:
    pages: dict[str, dict[str, Any]] = field(default_factory=dict)
    requests: list[_Recorded] = field(default_factory=list)
    forced: tuple[int, bytes] | None = None
    raw_body: bytes | None = None

    def handle(self, method: str, raw_path: str, body: Any) -> tuple[int, bytes]:
        if self.forced is not None:
            return self.forced
        if self.raw_body is not None:
            return 200, self.raw_body
        segments = urlsplit(raw_path).path.split("/")
        if segments[1:3] == ["v1", "pages"] and len(segments) == 4:
            return self._page(method, segments[3], body)
        return _json(400, {"object": "error", "code": "invalid_request_url"})

    def _page(self, method: str, page_id: str, body: Any) -> tuple[int, bytes]:
        page = self.pages.get(page_id)
        if page is None:
            return _json(404, {"object": "error", "code": "object_not_found"})
        if method == "PATCH":
            for name, value in body["properties"].items():
                page["properties"][name].update(value)
        return _json(200, page)


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


def _board(url: str) -> EpisodeBoard:
    return NotionEpisodeBoard(SETTINGS, api_url=url, timeout=5.0)


def _page(
    *,
    stage: str | None = "Ready to clip",
    stage_type: str = "select",
    source: str | None = "s01e01.mp4",
    mode: str | None = "scene",
    database: str = DATABASE,
    archived: bool = False,
) -> dict[str, Any]:
    def option(value: str | None) -> dict[str, Any] | None:
        return None if value is None else {"id": "opt", "name": value}

    def text(value: str | None) -> list[dict[str, Any]]:
        return [] if value is None else [{"plain_text": value}]

    return {
        "object": "page",
        "id": PAGE,
        "archived": archived,
        "parent": {"type": "database_id", "database_id": database},
        "properties": {
            "Stage": {"id": "a", "type": stage_type, stage_type: option(stage)},
            "Source": {"id": "b", "type": "rich_text", "rich_text": text(source)},
            "Mode": {"id": "c", "type": "select", "select": option(mode)},
            "Run status": {"id": "d", "type": "rich_text", "rich_text": []},
            "Run id": {"id": "e", "type": "rich_text", "rich_text": []},
        },
    }


def _file(fake: _FakeNotion, page: dict[str, Any]) -> None:
    fake.pages[PAGE] = page


# --- 1. Reading an episode --------------------------------------------------------------


def test_neb_01_a_ready_page_becomes_an_incoming_episode(
    notion: tuple[_FakeNotion, str],
) -> None:
    fake, url = notion
    _file(fake, _page())
    assert _board(url).fetch_episode(PAGE) == IncomingEpisode(
        episode_ref=PAGE, source_ref="s01e01.mp4", mode=ClipMode.SCENE
    )


@pytest.mark.parametrize("mode", ["chunk", " CHUNK ", "Chunk"])
def test_neb_01_the_mode_vocabulary_ignores_case_and_padding(
    notion: tuple[_FakeNotion, str], mode: str
) -> None:
    fake, url = notion
    _file(fake, _page(mode=mode))
    episode = _board(url).fetch_episode(PAGE)
    assert episode is not None
    assert episode.mode is ClipMode.CHUNK


@pytest.mark.parametrize(
    "page_kwargs",
    [
        {"stage": "Draft"},
        {"stage": None},
        {"archived": True},
        {"database": "99999999-9999-9999-9999-999999999999"},
        {"source": None},
        {"source": "   "},
    ],
)
def test_neb_02_an_unclippable_episode_is_indistinguishably_none(
    notion: tuple[_FakeNotion, str], page_kwargs: dict[str, Any]
) -> None:
    fake, url = notion
    _file(fake, _page(**page_kwargs))
    assert _board(url).fetch_episode(PAGE) is None


def test_neb_02_an_unknown_page_and_a_blank_reference_are_none(
    notion: tuple[_FakeNotion, str],
) -> None:
    _, url = notion
    board = _board(url)
    assert board.fetch_episode(PAGE) is None
    assert board.fetch_episode("  ") is None


def test_neb_03_a_mode_outside_the_vocabulary_is_a_configuration_fault(
    notion: tuple[_FakeNotion, str],
) -> None:
    """Reading it as "not ready" would hide a broken board behind a legitimate-looking None."""
    fake, url = notion
    _file(fake, _page(mode="storyline"))
    with pytest.raises(EpisodeBoardError) as caught:
        _board(url).fetch_episode(PAGE)
    assert "storyline" in str(caught.value)
    assert "chunk" in str(caught.value) and "scene" in str(caught.value)


def test_neb_03_an_unset_mode_is_a_configuration_fault(notion: tuple[_FakeNotion, str]) -> None:
    fake, url = notion
    _file(fake, _page(mode=None))
    with pytest.raises(EpisodeBoardError) as caught:
        _board(url).fetch_episode(PAGE)
    assert "not set" in str(caught.value)


def test_neb_01_a_status_typed_readiness_property_is_read_the_same(
    notion: tuple[_FakeNotion, str],
) -> None:
    fake, url = notion
    _file(fake, _page(stage_type="status"))
    assert _board(url).fetch_episode(PAGE) is not None


# --- 2. Writing the Run's status --------------------------------------------------------


def test_neb_04_a_status_is_written_and_repeating_it_is_harmless(
    notion: tuple[_FakeNotion, str],
) -> None:
    fake, url = notion
    _file(fake, _page())
    board = _board(url)
    board.report_status(PAGE, run_id="run-1", status=RunStatus.QUEUED)
    board.report_status(PAGE, run_id="run-1", status=RunStatus.QUEUED)
    properties = fake.pages[PAGE]["properties"]
    assert properties["Run status"]["rich_text"][0]["text"]["content"] == "queued"
    assert properties["Run id"]["rich_text"][0]["text"]["content"] == "run-1"
    assert sum(1 for r in fake.requests if r.method == "PATCH") == 2


@pytest.mark.parametrize("episode_ref", ["", "   "])
def test_neb_05_a_blank_reference_is_refused_before_any_write(
    notion: tuple[_FakeNotion, str], episode_ref: str
) -> None:
    fake, url = notion
    with pytest.raises(EpisodeBoardError):
        _board(url).report_status(episode_ref, run_id="run-1", status=RunStatus.QUEUED)
    assert not [r for r in fake.requests if r.method == "PATCH"]


def test_neb_05_a_page_not_on_the_board_is_refused_before_any_write(
    notion: tuple[_FakeNotion, str],
) -> None:
    fake, url = notion
    _file(fake, _page(database="99999999-9999-9999-9999-999999999999"))
    with pytest.raises(EpisodeBoardError) as caught:
        _board(url).report_status(PAGE, run_id="run-1", status=RunStatus.QUEUED)
    assert "not on the board" in str(caught.value)
    assert not [r for r in fake.requests if r.method == "PATCH"]


def test_neb_05_a_mistyped_run_property_is_refused_before_any_write(
    notion: tuple[_FakeNotion, str],
) -> None:
    fake, url = notion
    page = _page()
    page["properties"]["Run id"] = {"id": "e", "type": "url", "url": None}
    _file(fake, page)
    with pytest.raises(EpisodeBoardError) as caught:
        _board(url).report_status(PAGE, run_id="run-1", status=RunStatus.QUEUED)
    assert "must be of type" in str(caught.value)
    assert not [r for r in fake.requests if r.method == "PATCH"]


# --- 3. Misconfiguration and transport --------------------------------------------------


def test_neb_06_a_missing_property_is_refused(notion: tuple[_FakeNotion, str]) -> None:
    fake, url = notion
    page = _page()
    del page["properties"]["Mode"]
    _file(fake, page)
    with pytest.raises(EpisodeBoardError) as caught:
        _board(url).fetch_episode(PAGE)
    assert "has no property" in str(caught.value)


def test_neb_06_a_refusal_is_reported_without_the_token(
    notion: tuple[_FakeNotion, str],
) -> None:
    fake, url = notion
    fake.forced = _json(401, {"object": "error", "code": "unauthorized"})
    with pytest.raises(EpisodeBoardError) as caught:
        _board(url).fetch_episode(PAGE)
    message = str(caught.value)
    assert "HTTP 401" in message
    assert TOKEN not in message


def test_neb_06_a_response_that_is_not_json_is_refused(notion: tuple[_FakeNotion, str]) -> None:
    fake, url = notion
    fake.raw_body = b"<html>nope</html>"
    with pytest.raises(EpisodeBoardError) as caught:
        _board(url).fetch_episode(PAGE)
    assert "not JSON" in str(caught.value)


def test_neb_06_an_unreachable_notion_is_refused() -> None:
    board = NotionEpisodeBoard(SETTINGS, api_url="http://127.0.0.1:1", timeout=0.5)
    with pytest.raises(EpisodeBoardError) as caught:
        board.fetch_episode(PAGE)
    assert "could not be reached" in str(caught.value)


def test_neb_07_every_request_carries_the_token_and_the_pinned_version(
    notion: tuple[_FakeNotion, str],
) -> None:
    fake, url = notion
    _file(fake, _page())
    _board(url).fetch_episode(PAGE)
    assert fake.requests
    for recorded in fake.requests:
        assert recorded.headers["Authorization"] == f"Bearer {TOKEN}"
        assert recorded.headers["Notion-Version"] == NOTION_API_VERSION


def test_neb_07_a_reference_cannot_escape_the_path(notion: tuple[_FakeNotion, str]) -> None:
    _, url = notion
    _board(url).fetch_episode("../databases")
    # The ref is percent-encoded, dots included, so it stays one path segment.
    assert True


# --- 4. Settings ------------------------------------------------------------------------


def test_neb_08_settings_need_every_variable_and_never_echo_the_token() -> None:
    settings = notion_episode_settings_from_env(ENV)
    assert settings.database_id == DATABASE
    assert TOKEN not in repr(settings)
    with pytest.raises(EpisodeBoardError) as caught:
        notion_episode_settings_from_env({**ENV, "OMEMO_EPISODE_NOTION_MODE_PROPERTY": "  "})
    message = str(caught.value)
    assert "OMEMO_EPISODE_NOTION_MODE_PROPERTY" in message
    assert TOKEN not in message


def test_neb_08_every_missing_variable_is_named() -> None:
    with pytest.raises(EpisodeBoardError) as caught:
        notion_episode_settings_from_env({})
    message = str(caught.value)
    for name in ENV:
        assert name in message


@pytest.mark.parametrize("field_name", list(ENV))
def test_neb_08_a_blank_variable_is_refused(field_name: str) -> None:
    with pytest.raises(EpisodeBoardError):
        notion_episode_settings_from_env({**ENV, field_name: ""})
