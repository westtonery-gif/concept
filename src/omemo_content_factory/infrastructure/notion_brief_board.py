"""The Notion Adapter — a real ``BriefBoard`` over the Notion REST API (ADR-0040).

A brief is a page of one configured database: it is ready when a ``status``/``select`` property
holds the configured option, its body is the text of its top-level blocks, a Run's status is
written back into two ``rich_text`` properties and the Run's review link into a ``url`` property
(ADR-0047). The API is reached through ``urllib`` with the version pinned; no Notion shape leaves
this module (ADAPTER_SPEC §3).

Where the contract is silent (ADR-0040 §3): a brief that simply is not producible is ``None``, while
a technical or configuration fault is ``BriefBoardError`` — never a guess.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from omemo_content_factory.adapters.brief_board import BriefBoardError, IncomingBrief
from omemo_content_factory.domain.run import RunStatus

__all__ = [
    "DEFAULT_API_URL",
    "NOTION_API_VERSION",
    "NotionBoardSettings",
    "NotionBriefBoard",
    "notion_settings_from_env",
]

NOTION_API_VERSION = "2022-06-28"
DEFAULT_API_URL = "https://api.notion.com"

TOKEN_VAR = "OMEMO_NOTION_TOKEN"
DATABASE_ID_VAR = "OMEMO_NOTION_DATABASE_ID"
READY_PROPERTY_VAR = "OMEMO_NOTION_READY_PROPERTY"
READY_VALUE_VAR = "OMEMO_NOTION_READY_VALUE"
RUN_STATUS_PROPERTY_VAR = "OMEMO_NOTION_RUN_STATUS_PROPERTY"
RUN_ID_PROPERTY_VAR = "OMEMO_NOTION_RUN_ID_PROPERTY"
REVIEW_LINK_PROPERTY_VAR = "OMEMO_NOTION_REVIEW_LINK_PROPERTY"

_READY_TYPES = ("status", "select")
_PAGE_SIZE = 100


@dataclass(frozen=True, slots=True)
class NotionBoardSettings:
    """Which database is the board and which of its properties the adapter reads and writes."""

    token: str = field(repr=False)
    database_id: str
    ready_property: str
    ready_value: str
    run_status_property: str
    run_id_property: str
    review_link_property: str

    def __post_init__(self) -> None:
        for name in (
            "token",
            "database_id",
            "ready_property",
            "ready_value",
            "run_status_property",
            "run_id_property",
            "review_link_property",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Notion board settings need a non-blank {name}")


def notion_settings_from_env(environ: Mapping[str, str]) -> NotionBoardSettings:
    """Read the seven required variables; a missing or blank one fails closed, named, no values."""
    names = (
        TOKEN_VAR,
        DATABASE_ID_VAR,
        READY_PROPERTY_VAR,
        READY_VALUE_VAR,
        RUN_STATUS_PROPERTY_VAR,
        RUN_ID_PROPERTY_VAR,
        REVIEW_LINK_PROPERTY_VAR,
    )
    values = {name: environ.get(name, "").strip() for name in names}
    missing = [name for name in names if not values[name]]
    if missing:
        raise BriefBoardError("the Notion board is not configured; set " + ", ".join(missing))
    return NotionBoardSettings(
        token=values[TOKEN_VAR],
        database_id=values[DATABASE_ID_VAR],
        ready_property=values[READY_PROPERTY_VAR],
        ready_value=values[READY_VALUE_VAR],
        run_status_property=values[RUN_STATUS_PROPERTY_VAR],
        run_id_property=values[RUN_ID_PROPERTY_VAR],
        review_link_property=values[REVIEW_LINK_PROPERTY_VAR],
    )


class NotionBriefBoard:
    """A ``BriefBoard`` whose briefs are pages of one Notion database (ADAPTER_SPEC §5)."""

    def __init__(
        self,
        settings: NotionBoardSettings,
        *,
        api_url: str = DEFAULT_API_URL,
        timeout: float = 10.0,
    ) -> None:
        self._settings = settings
        self._api_url = api_url.rstrip("/")
        self._timeout = timeout

    def fetch_brief(self, brief_ref: str, /) -> IncomingBrief | None:
        """The page as a brief if it is on the board, ready and has text; otherwise ``None``."""
        if not brief_ref.strip():
            return None
        page = self._board_page(brief_ref)
        if page is None or not self._is_ready(page):
            return None
        body = "\n".join(self._text_lines(brief_ref)).strip()
        if not body:
            return None
        return IncomingBrief(brief_ref=brief_ref, body=body)

    def report_status(self, brief_ref: str, /, *, run_id: str, status: RunStatus) -> None:
        """Write the Run's status and id onto its page; a page not on the board is refused."""
        page = self._writable_page(brief_ref, "a status")
        settings = self._settings
        for name in (settings.run_status_property, settings.run_id_property):
            self._property(page, name, ("rich_text",))
        properties = {
            settings.run_status_property: _rich_text(status.value),
            settings.run_id_property: _rich_text(run_id),
        }
        self._request("PATCH", _page_path(brief_ref), body={"properties": properties})

    def report_review_location(self, brief_ref: str, /, *, run_id: str, location: str) -> None:
        """Write the review's location into its ``url`` property (ADR-0047 §2).

        A blank location, a page not on the board and a missing or non-``url`` property are refused
        before any write.
        """
        if not location.strip():
            raise BriefBoardError(f"a review location for run {run_id} must not be blank")
        page = self._writable_page(brief_ref, "a review location")
        name = self._settings.review_link_property
        self._property(page, name, ("url",))
        properties = {name: {"url": location}}
        self._request("PATCH", _page_path(brief_ref), body={"properties": properties})

    def _writable_page(self, brief_ref: str, what: str) -> dict[str, Any]:
        if not brief_ref.strip():
            raise BriefBoardError(f"{what} needs a non-blank brief reference")
        page = self._board_page(brief_ref)
        if page is None:
            raise BriefBoardError(f"brief {brief_ref!r} is not on the board")
        return page

    # --- reading ------------------------------------------------------------------------

    def _board_page(self, brief_ref: str) -> dict[str, Any] | None:
        page = self._request("GET", _page_path(brief_ref), missing_ok=True)
        if page is None or page.get("archived") is True or page.get("in_trash") is True:
            return None
        parent = page.get("parent")
        database_id = parent.get("database_id") if isinstance(parent, dict) else None
        if not isinstance(database_id, str):
            return None
        if _normalized_id(database_id) != _normalized_id(self._settings.database_id):
            return None
        return page

    def _is_ready(self, page: dict[str, Any]) -> bool:
        prop = self._property(page, self._settings.ready_property, _READY_TYPES)
        option = prop.get(prop["type"])
        if option is None:
            return False
        if not isinstance(option, dict):
            raise BriefBoardError("Notion returned a readiness option of an unexpected shape")
        return option.get("name") == self._settings.ready_value

    def _property(self, page: dict[str, Any], name: str, types: tuple[str, ...]) -> dict[str, Any]:
        properties = page.get("properties")
        prop = properties.get(name) if isinstance(properties, dict) else None
        if not isinstance(prop, dict):
            raise BriefBoardError(f"the Notion board has no property {name!r}")
        if prop.get("type") not in types:
            raise BriefBoardError(
                f"the Notion property {name!r} must be of type {' or '.join(types)}"
            )
        return prop

    def _text_lines(self, brief_ref: str) -> Iterator[str]:
        for block in self._blocks(brief_ref):
            block_type = block.get("type")
            payload = block.get(block_type) if isinstance(block_type, str) else None
            rich_text = payload.get("rich_text") if isinstance(payload, dict) else None
            if isinstance(rich_text, list):
                yield "".join(_plain_text(chunk) for chunk in rich_text)

    def _blocks(self, brief_ref: str) -> Iterator[dict[str, Any]]:
        cursor: str | None = None
        while True:
            query = {"page_size": str(_PAGE_SIZE)}
            if cursor is not None:
                query["start_cursor"] = cursor
            path = f"/v1/blocks/{_quoted(brief_ref)}/children?{urllib.parse.urlencode(query)}"
            listing = self._request("GET", path)
            results = listing.get("results") if listing is not None else None
            if listing is None or not isinstance(results, list):
                raise BriefBoardError("Notion returned a block listing of an unexpected shape")
            yield from (block for block in results if isinstance(block, dict))
            cursor = _next_cursor(listing)
            if cursor is None:
                return

    # --- transport ----------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        missing_ok: bool = False,
    ) -> dict[str, Any] | None:
        headers = {
            "Authorization": f"Bearer {self._settings.token}",
            "Notion-Version": NOTION_API_VERSION,
            "Accept": "application/json",
        }
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self._api_url + path, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            error.close()
            if error.code == 404 and missing_ok:
                return None
            raise BriefBoardError(
                f"Notion refused {method} {path.split('?')[0]}: HTTP {error.code}"
            ) from error
        except (urllib.error.URLError, OSError) as error:
            raise BriefBoardError(f"Notion could not be reached: {error}") from error
        return _json_object(raw)


def _json_object(raw: bytes) -> dict[str, Any]:
    try:
        decoded = json.loads(raw)
    except ValueError as error:
        raise BriefBoardError("Notion returned a response that is not JSON") from error
    if not isinstance(decoded, dict):
        raise BriefBoardError("Notion returned a response that is not a JSON object")
    return decoded


def _next_cursor(listing: dict[str, Any]) -> str | None:
    if listing.get("has_more") is not True:
        return None
    cursor = listing.get("next_cursor")
    if not isinstance(cursor, str) or not cursor:
        raise BriefBoardError("Notion reported more blocks without a cursor")
    return cursor


def _plain_text(chunk: object) -> str:
    text = chunk.get("plain_text") if isinstance(chunk, dict) else None
    return text if isinstance(text, str) else ""


def _rich_text(content: str) -> dict[str, Any]:
    return {"rich_text": [{"type": "text", "text": {"content": content}}]}


def _page_path(brief_ref: str) -> str:
    return f"/v1/pages/{_quoted(brief_ref)}"


def _quoted(brief_ref: str) -> str:
    # Dots are encoded too, so a ref of ".." cannot become a dot segment of the path.
    return urllib.parse.quote(brief_ref, safe="").replace(".", "%2E")


def _normalized_id(notion_id: str) -> str:
    return notion_id.replace("-", "").lower()
