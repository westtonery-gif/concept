"""Tests for the Notion Adapter, ``NotionReviewDesk`` (ADR-0060).

Maps ADAPTER_ACCEPTANCE.md §14 (NRD). The adapter's real ``urllib`` code talks over a real socket
to a local HTTP server that plays Notion: it answers database queries, creates pages, appends
block children and records every request as it arrived. Nothing is mocked below the adapter and
nothing leaves ``127.0.0.1``.
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

from omemo_content_factory.adapters.review_desk import (
    PostDraft,
    ReviewDesk,
    ReviewDeskError,
    ReviewPackage,
)
from omemo_content_factory.domain.artifact import ArtifactStatus, ArtifactView
from omemo_content_factory.domain.human_review import ReviewStatus
from omemo_content_factory.infrastructure.notion_review_desk import (
    NOTION_API_VERSION,
    POST_DESCRIPTION_PROPERTY_VAR,
    POST_TITLE_PROPERTY_VAR,
    NotionReviewDesk,
    NotionReviewSettings,
    notion_review_settings_from_env,
)

TOKEN = "secret_integration_token_value"
DATABASE = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
RUN = "run-1"
REVIEW = "run-1-review-1"
PAGE_URL = "https://www.notion.so/run-1-review-1-abcdef"

SETTINGS = NotionReviewSettings(
    token=TOKEN,
    database_id=DATABASE,
    title_property="Name",
    review_id_property="Review id",
    decision_property="Решение",
    reason_property="Причина",
    fingerprint_property="Fingerprint",
)

ENV = {
    "OMEMO_REVIEW_NOTION_TOKEN": TOKEN,
    "OMEMO_REVIEW_NOTION_DATABASE_ID": DATABASE,
    "OMEMO_REVIEW_NOTION_TITLE_PROPERTY": "Name",
    "OMEMO_REVIEW_NOTION_REVIEW_ID_PROPERTY": "Review id",
    "OMEMO_REVIEW_NOTION_DECISION_PROPERTY": "Решение",
    "OMEMO_REVIEW_NOTION_REASON_PROPERTY": "Причина",
    "OMEMO_REVIEW_NOTION_FINGERPRINT_PROPERTY": "Fingerprint",
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
    pages: list[dict[str, Any]] = field(default_factory=list)
    requests: list[_Recorded] = field(default_factory=list)
    appended: list[list[dict[str, Any]]] = field(default_factory=list)
    forced: tuple[int, bytes] | None = None
    raw_body: bytes | None = None

    def handle(self, method: str, raw_path: str, body: Any) -> tuple[int, bytes]:
        if self.forced is not None:
            return self.forced
        if self.raw_body is not None:
            return 200, self.raw_body
        segments = urlsplit(raw_path).path.split("/")
        if segments[1:3] == ["v1", "databases"] and segments[4:5] == ["query"]:
            return self._query(body)
        if segments[1:3] == ["v1", "pages"] and len(segments) == 3:
            return self._create(body)
        if segments[1:3] == ["v1", "blocks"] and segments[4:5] == ["children"]:
            self.appended.append(body["children"])
            return _json(200, {"object": "list", "results": []})
        return _json(400, {"object": "error", "code": "invalid_request_url"})

    def _query(self, body: Any) -> tuple[int, bytes]:
        wanted = body["filter"]["rich_text"]["equals"]
        results = [page for page in self.pages if _review_id_of(page) == wanted]
        return _json(200, {"object": "list", "results": results, "has_more": False})

    def _create(self, body: Any) -> tuple[int, bytes]:
        properties = dict(body["properties"])
        properties.setdefault("Решение", {"id": "d", "type": "select", "select": None})
        properties.setdefault("Причина", {"id": "r", "type": "rich_text", "rich_text": []})
        for name in ("Заголовок поста", "Описание поста"):
            properties.setdefault(name, {"id": name, "type": "rich_text", "rich_text": []})
        page = {
            "object": "page",
            "id": "page-1",
            "url": PAGE_URL,
            "archived": False,
            "parent": {"type": "database_id", "database_id": DATABASE},
            "properties": _as_read(properties),
        }
        self.pages.append(page)
        return _json(200, page)


def _as_read(written: dict[str, Any]) -> dict[str, Any]:
    """Turn the write shape Notion accepts into the read shape it returns."""
    out: dict[str, Any] = {}
    for name, value in written.items():
        if "rich_text" in value and isinstance(value["rich_text"], list):
            chunks = [
                {"plain_text": chunk.get("text", {}).get("content", "")}
                for chunk in value["rich_text"]
            ]
            out[name] = {"id": name, "type": "rich_text", "rich_text": chunks}
        elif "title" in value:
            chunks = [
                {"plain_text": chunk.get("text", {}).get("content", "")} for chunk in value["title"]
            ]
            out[name] = {"id": name, "type": "title", "title": chunks}
        else:
            out[name] = value
    return out


def _review_id_of(page: dict[str, Any]) -> str:
    prop = page["properties"].get("Review id", {})
    return "".join(chunk.get("plain_text", "") for chunk in prop.get("rich_text", []))


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
        do_POST = _serve  # noqa: N815
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


def _desk(url: str, settings: NotionReviewSettings = SETTINGS) -> ReviewDesk:
    return NotionReviewDesk(settings, api_url=url, timeout=5.0)


def _package(*, content: str = "The script.", flags: tuple[str, ...] = ()) -> ReviewPackage:
    candidate = ArtifactView(
        artifact_id=f"{RUN}-artifact-1",
        run_id=RUN,
        output_ref=f"{RUN}-output-1",
        kind="script",
        content=content,
        version=1,
        status=ArtifactStatus.CANDIDATE,
    )
    return ReviewPackage(
        run_id=RUN, review_id=REVIEW, candidate=candidate, brief="The brief.", qa_flags=flags
    )


def _decided(fake: _FakeNotion, option: str | None, reason: str = "") -> None:
    page = fake.pages[0]
    page["properties"]["Решение"] = {
        "id": "d",
        "type": "select",
        "select": None if option is None else {"id": "o", "name": option},
    }
    page["properties"]["Причина"] = {
        "id": "r",
        "type": "rich_text",
        "rich_text": [{"plain_text": reason}] if reason else [],
    }


# --- 1. Publication ---------------------------------------------------------------------


def test_nrd_01_publish_creates_one_page_and_returns_its_url(
    notion: tuple[_FakeNotion, str],
) -> None:
    fake, url = notion
    location = _desk(url).publish(_package(flags=("no source",)))
    assert location == PAGE_URL
    assert len(fake.pages) == 1
    created = next(r for r in fake.requests if r.method == "POST" and r.path == "/v1/pages")
    properties = created.body["properties"]
    assert properties["Review id"]["rich_text"][0]["text"]["content"] == REVIEW
    assert properties["Fingerprint"]["rich_text"][0]["text"]["content"]
    assert RUN in properties["Name"]["title"][0]["text"]["content"]
    rendered = json.dumps(created.body["children"], ensure_ascii=False)
    assert "The brief." in rendered
    assert "The script." in rendered
    assert "no source" in rendered


def test_nrd_02_publishing_the_same_review_again_returns_the_same_page(
    notion: tuple[_FakeNotion, str],
) -> None:
    fake, url = notion
    desk = _desk(url)
    first = desk.publish(_package())
    second = desk.publish(_package())
    assert first == second == PAGE_URL
    assert len(fake.pages) == 1
    assert sum(1 for r in fake.requests if r.path == "/v1/pages") == 1


def test_nrd_03_another_package_under_a_published_review_is_refused(
    notion: tuple[_FakeNotion, str],
) -> None:
    _, url = notion
    desk = _desk(url)
    desk.publish(_package())
    with pytest.raises(ReviewDeskError) as caught:
        desk.publish(_package(content="A different script."))
    assert "different package" in str(caught.value)


def test_nrd_04_a_long_candidate_is_chunked_and_extra_blocks_are_appended(
    notion: tuple[_FakeNotion, str],
) -> None:
    fake, url = notion
    lines = "\n".join(f"line {index} " + "x" * 2500 for index in range(60))
    _desk(url).publish(_package(content=lines))
    created = next(r for r in fake.requests if r.path == "/v1/pages")
    assert len(created.body["children"]) == 100
    assert fake.appended, "blocks past the first hundred must be appended"
    every = created.body["children"] + [b for batch in fake.appended for b in batch]
    for block in every:
        for chunk in block[block["type"]]["rich_text"]:
            assert len(chunk["text"]["content"]) <= 2000


def test_nrd_05_an_archived_page_is_not_a_published_review(
    notion: tuple[_FakeNotion, str],
) -> None:
    fake, url = notion
    desk = _desk(url)
    desk.publish(_package())
    fake.pages[0]["archived"] = True
    desk.publish(_package())
    assert len(fake.pages) == 2


# --- 2. Reading the decision ------------------------------------------------------------


def test_nrd_06_an_unset_decision_is_still_pending(notion: tuple[_FakeNotion, str]) -> None:
    fake, url = notion
    desk = _desk(url)
    desk.publish(_package())
    _decided(fake, None)
    assert desk.fetch_decision(REVIEW) is None


@pytest.mark.parametrize(
    ("option", "expected"),
    [
        ("Одобрено", ReviewStatus.APPROVED),
        ("отклонено", ReviewStatus.REJECTED),
        ("  Доработать  ", ReviewStatus.CHANGES_REQUESTED),
        ("approved", ReviewStatus.APPROVED),
        ("changes_requested", ReviewStatus.CHANGES_REQUESTED),
    ],
)
def test_nrd_07_each_option_maps_to_its_review_status(
    notion: tuple[_FakeNotion, str], option: str, expected: ReviewStatus
) -> None:
    fake, url = notion
    desk = _desk(url)
    desk.publish(_package())
    _decided(fake, option)
    decision = desk.fetch_decision(REVIEW)
    assert decision is not None
    assert decision.decision is expected
    assert decision.reason is None


def test_nrd_08_a_reason_is_carried_and_a_blank_one_is_none(
    notion: tuple[_FakeNotion, str],
) -> None:
    fake, url = notion
    desk = _desk(url)
    desk.publish(_package())
    _decided(fake, "Доработать", reason="  Добавь источник.  ")
    decision = desk.fetch_decision(REVIEW)
    assert decision is not None
    assert decision.reason == "Добавь источник."
    _decided(fake, "Доработать", reason="   ")
    again = desk.fetch_decision(REVIEW)
    assert again is not None
    assert again.reason is None


def test_nrd_09_an_option_outside_the_three_is_refused_not_read_as_silence(
    notion: tuple[_FakeNotion, str],
) -> None:
    fake, url = notion
    desk = _desk(url)
    desk.publish(_package())
    _decided(fake, "Может быть")
    with pytest.raises(ReviewDeskError) as caught:
        desk.fetch_decision(REVIEW)
    assert "none of the three" in str(caught.value)


def test_nrd_10_a_review_that_was_never_published_is_refused(
    notion: tuple[_FakeNotion, str],
) -> None:
    _, url = notion
    with pytest.raises(ReviewDeskError) as caught:
        _desk(url).fetch_decision("run-9-review-9")
    assert "never published" in str(caught.value)


def test_nrd_11_two_pages_under_one_review_id_are_refused(
    notion: tuple[_FakeNotion, str],
) -> None:
    fake, url = notion
    desk = _desk(url)
    desk.publish(_package())
    fake.pages.append(json.loads(json.dumps(fake.pages[0])))
    with pytest.raises(ReviewDeskError) as caught:
        desk.fetch_decision(REVIEW)
    assert "more than one page" in str(caught.value)


@pytest.mark.parametrize("review_id", ["", "   "])
def test_nrd_12_a_blank_review_id_is_refused(
    notion: tuple[_FakeNotion, str], review_id: str
) -> None:
    _, url = notion
    with pytest.raises(ReviewDeskError):
        _desk(url).fetch_decision(review_id)


# --- 3. Misconfiguration and transport --------------------------------------------------


def test_nrd_13_a_missing_or_mistyped_property_is_refused(
    notion: tuple[_FakeNotion, str],
) -> None:
    fake, url = notion
    desk = _desk(url)
    desk.publish(_package())
    del fake.pages[0]["properties"]["Решение"]
    with pytest.raises(ReviewDeskError) as missing:
        desk.fetch_decision(REVIEW)
    assert "has no property" in str(missing.value)
    fake.pages[0]["properties"]["Решение"] = {"id": "d", "type": "rich_text", "rich_text": []}
    with pytest.raises(ReviewDeskError) as mistyped:
        desk.fetch_decision(REVIEW)
    assert "must be of type" in str(mistyped.value)


def test_nrd_14_a_refusal_is_reported_without_the_token(
    notion: tuple[_FakeNotion, str],
) -> None:
    fake, url = notion
    fake.forced = _json(401, {"object": "error", "code": "unauthorized"})
    with pytest.raises(ReviewDeskError) as caught:
        _desk(url).publish(_package())
    message = str(caught.value)
    assert "HTTP 401" in message
    assert TOKEN not in message


def test_nrd_15_a_response_that_is_not_json_is_refused(notion: tuple[_FakeNotion, str]) -> None:
    fake, url = notion
    fake.raw_body = b"<html>nope</html>"
    with pytest.raises(ReviewDeskError) as caught:
        _desk(url).publish(_package())
    assert "not JSON" in str(caught.value)


def test_nrd_16_an_unreachable_notion_is_refused() -> None:
    desk = NotionReviewDesk(SETTINGS, api_url="http://127.0.0.1:1", timeout=0.5)
    with pytest.raises(ReviewDeskError) as caught:
        desk.fetch_decision(REVIEW)
    assert "could not be reached" in str(caught.value)


def test_nrd_17_every_request_carries_the_token_and_the_pinned_version(
    notion: tuple[_FakeNotion, str],
) -> None:
    fake, url = notion
    _desk(url).publish(_package())
    assert fake.requests
    for recorded in fake.requests:
        assert recorded.headers["Authorization"] == f"Bearer {TOKEN}"
        assert recorded.headers["Notion-Version"] == NOTION_API_VERSION


# --- 4. Settings ------------------------------------------------------------------------


def test_nrd_18_settings_need_every_variable_and_never_echo_the_token() -> None:
    settings = notion_review_settings_from_env(ENV)
    assert settings.database_id == DATABASE
    assert TOKEN not in repr(settings)
    with pytest.raises(ReviewDeskError) as caught:
        notion_review_settings_from_env({**ENV, "OMEMO_REVIEW_NOTION_DECISION_PROPERTY": "  "})
    message = str(caught.value)
    assert "OMEMO_REVIEW_NOTION_DECISION_PROPERTY" in message
    assert TOKEN not in message


def test_nrd_19_every_missing_variable_is_named() -> None:
    with pytest.raises(ReviewDeskError) as caught:
        notion_review_settings_from_env({})
    message = str(caught.value)
    for name in ENV:
        assert name in message


@pytest.mark.parametrize("field_name", list(ENV))
def test_nrd_20_a_blank_settings_field_is_refused(field_name: str) -> None:
    with pytest.raises(ReviewDeskError):
        notion_review_settings_from_env({**ENV, field_name: ""})


# --- 6. The post text (ADR-0072) --------------------------------------------------------

POST_SETTINGS = NotionReviewSettings(
    token=TOKEN,
    database_id=DATABASE,
    title_property="Name",
    review_id_property="Review id",
    decision_property="Решение",
    reason_property="Причина",
    fingerprint_property="Fingerprint",
    post_title_property="Заголовок поста",
    post_description_property="Описание поста",
)
DRAFT = PostDraft(title="Рик замораживает Фрэнка 🥶", description="Коротко о клипе.\n#рикиморти")


def _with_post() -> ReviewPackage:
    package = _package()
    return ReviewPackage(
        run_id=package.run_id,
        review_id=package.review_id,
        candidate=package.candidate,
        brief=package.brief,
        post=DRAFT,
    )


def _edit(fake: _FakeNotion, name: str, text: str) -> None:
    fake.pages[0]["properties"][name] = {
        "id": name,
        "type": "rich_text",
        "rich_text": [{"plain_text": text}] if text else [],
    }


def test_nrd_21_the_draft_is_written_into_the_post_properties_and_the_page(
    notion: tuple[_FakeNotion, str],
) -> None:
    fake, url = notion
    _desk(url, POST_SETTINGS).publish(_with_post())
    created = next(r for r in fake.requests if r.method == "POST" and r.path == "/v1/pages")
    properties = created.body["properties"]
    assert properties["Заголовок поста"]["rich_text"][0]["text"]["content"] == DRAFT.title
    assert properties["Описание поста"]["rich_text"][0]["text"]["content"] == DRAFT.description
    rendered = json.dumps(created.body["children"], ensure_ascii=False)
    assert "Текст поста" in rendered and DRAFT.title in rendered


def test_nrd_22_the_decision_carries_the_text_as_the_reviewer_left_it(
    notion: tuple[_FakeNotion, str],
) -> None:
    fake, url = notion
    desk = _desk(url, POST_SETTINGS)
    desk.publish(_with_post())
    _edit(fake, "Заголовок поста", "Исправленный заголовок")
    _decided(fake, "Одобрено")
    decision = desk.fetch_decision(REVIEW)
    assert decision is not None
    assert decision.post == PostDraft(title="Исправленный заголовок", description=DRAFT.description)


def test_nrd_22_a_text_that_cannot_be_posted_reads_as_none(
    notion: tuple[_FakeNotion, str],
) -> None:
    """A blank or over-long title is not a postable text; the draft then stands (ADR-0072 §4)."""
    fake, url = notion
    desk = _desk(url, POST_SETTINGS)
    desk.publish(_with_post())
    _decided(fake, "Одобрено")
    _edit(fake, "Заголовок поста", "x" * 101)
    decision = desk.fetch_decision(REVIEW)
    assert decision is not None and decision.post is None
    _edit(fake, "Заголовок поста", "")
    again = desk.fetch_decision(REVIEW)
    assert again is not None and again.post is None


def test_nrd_23_without_the_post_properties_nothing_about_posts_is_read_or_written(
    notion: tuple[_FakeNotion, str],
) -> None:
    fake, url = notion
    desk = _desk(url)
    desk.publish(_with_post())
    created = next(r for r in fake.requests if r.method == "POST" and r.path == "/v1/pages")
    assert "Заголовок поста" not in created.body["properties"]
    _decided(fake, "Одобрено")
    decision = desk.fetch_decision(REVIEW)
    assert decision is not None and decision.post is None


def test_nrd_24_a_package_without_a_post_keeps_its_old_fingerprint(
    notion: tuple[_FakeNotion, str],
) -> None:
    """Pages published before ADR-0072 must still match their package on republish (§3)."""
    fake, url = notion
    _desk(url).publish(_package())
    before = fake.pages[0]["properties"]["Fingerprint"]["rich_text"][0]["plain_text"]
    assert _desk(url, POST_SETTINGS).publish(_package()) == PAGE_URL
    assert fake.pages[0]["properties"]["Fingerprint"]["rich_text"][0]["plain_text"] == before


def test_nrd_25_the_post_properties_are_configured_together() -> None:
    settings = notion_review_settings_from_env(
        {**ENV, POST_TITLE_PROPERTY_VAR: "Заголовок поста", POST_DESCRIPTION_PROPERTY_VAR: "О"}
    )
    assert settings.edits_posts
    assert not notion_review_settings_from_env(ENV).edits_posts
    with pytest.raises(ReviewDeskError, match=POST_DESCRIPTION_PROPERTY_VAR):
        notion_review_settings_from_env({**ENV, POST_TITLE_PROPERTY_VAR: "Заголовок поста"})


def test_nrd_25_a_post_draft_holds_to_youtubes_title_limit() -> None:
    PostDraft(title="x" * 100, description="d")
    with pytest.raises(ValueError, match="100"):
        PostDraft(title="x" * 101, description="d")
    with pytest.raises(ValueError, match="description"):
        PostDraft(title="t", description="  ")
