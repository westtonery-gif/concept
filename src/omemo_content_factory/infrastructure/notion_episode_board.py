"""The Notion Adapter — a real ``EpisodeBoard`` over the Notion REST API (ADR-0055).

An episode is a page of its own database: it is ready to clip when a ``status``/``select`` property
holds the configured option, its source handle and cutting mode come from two more properties, and
a Run's status is written back into two ``rich_text`` properties. The API is reached through
``urllib`` with the version pinned; no Notion shape leaves this module (ADAPTER_SPEC §3).

Where the contract is silent the rule is ADR-0040's: an episode that simply is not clippable is
``None`` — unknown, archived, not ready and sourceless alike — while a technical or
**configuration** fault is ``EpisodeBoardError``. A mode property holding something that is not
a ``ClipMode`` is the second kind: a misconfigured board, never silently "not ready".

This is the **third** Notion implementation. Its request helper, auth header and property reader are
still duplicated from ``notion_brief_board`` — ADR-0061 holds the extraction until three exist in
``src/``, and this is the third, so the shared client is now due as its own behaviour-neutral
refactor (ADR-0055 §3), never mixed into this module's own commit.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from omemo_content_factory.adapters.episode_board import (
    ClipMode,
    EpisodeBoardError,
    IncomingEpisode,
)
from omemo_content_factory.domain.run import RunStatus

__all__ = [
    "DEFAULT_API_URL",
    "NOTION_API_VERSION",
    "NotionEpisodeBoard",
    "NotionEpisodeSettings",
    "notion_episode_settings_from_env",
]

NOTION_API_VERSION = "2022-06-28"
DEFAULT_API_URL = "https://api.notion.com"

TOKEN_VAR = "OMEMO_EPISODE_NOTION_TOKEN"
DATABASE_ID_VAR = "OMEMO_EPISODE_NOTION_DATABASE_ID"
READY_PROPERTY_VAR = "OMEMO_EPISODE_NOTION_READY_PROPERTY"
READY_VALUE_VAR = "OMEMO_EPISODE_NOTION_READY_VALUE"
SOURCE_PROPERTY_VAR = "OMEMO_EPISODE_NOTION_SOURCE_PROPERTY"
MODE_PROPERTY_VAR = "OMEMO_EPISODE_NOTION_MODE_PROPERTY"
RUN_STATUS_PROPERTY_VAR = "OMEMO_EPISODE_NOTION_RUN_STATUS_PROPERTY"
RUN_ID_PROPERTY_VAR = "OMEMO_EPISODE_NOTION_RUN_ID_PROPERTY"

_OPTION_TYPES = ("status", "select")
"""The Notion API cannot create ``status`` properties, only ``select`` — both are read the same."""

MODES: Mapping[str, ClipMode] = {mode.value: mode for mode in ClipMode}
"""The board speaks exactly ``ClipMode``'s own vocabulary: ``chunk`` and ``scene``."""


@dataclass(frozen=True, slots=True)
class NotionEpisodeSettings:
    """Which database is the episode board and which of its properties are read and written."""

    token: str = field(repr=False)
    database_id: str
    ready_property: str
    ready_value: str
    source_property: str
    mode_property: str
    run_status_property: str
    run_id_property: str

    def __post_init__(self) -> None:
        for name in (
            "token",
            "database_id",
            "ready_property",
            "ready_value",
            "source_property",
            "mode_property",
            "run_status_property",
            "run_id_property",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Notion episode board settings need a non-blank {name}")


def notion_episode_settings_from_env(environ: Mapping[str, str]) -> NotionEpisodeSettings:
    """Read the eight required variables; a missing or blank one fails closed, named, no values."""
    names = (
        TOKEN_VAR,
        DATABASE_ID_VAR,
        READY_PROPERTY_VAR,
        READY_VALUE_VAR,
        SOURCE_PROPERTY_VAR,
        MODE_PROPERTY_VAR,
        RUN_STATUS_PROPERTY_VAR,
        RUN_ID_PROPERTY_VAR,
    )
    values = {name: environ.get(name, "").strip() for name in names}
    missing = [name for name in names if not values[name]]
    if missing:
        raise EpisodeBoardError(
            "the Notion episode board is not configured; set " + ", ".join(missing)
        )
    return NotionEpisodeSettings(
        token=values[TOKEN_VAR],
        database_id=values[DATABASE_ID_VAR],
        ready_property=values[READY_PROPERTY_VAR],
        ready_value=values[READY_VALUE_VAR],
        source_property=values[SOURCE_PROPERTY_VAR],
        mode_property=values[MODE_PROPERTY_VAR],
        run_status_property=values[RUN_STATUS_PROPERTY_VAR],
        run_id_property=values[RUN_ID_PROPERTY_VAR],
    )


class NotionEpisodeBoard:
    """An ``EpisodeBoard`` whose episodes are pages of one Notion database."""

    def __init__(
        self,
        settings: NotionEpisodeSettings,
        *,
        api_url: str = DEFAULT_API_URL,
        timeout: float = 10.0,
    ) -> None:
        self._settings = settings
        self._api_url = api_url.rstrip("/")
        self._timeout = timeout

    def fetch_episode(self, episode_ref: str, /) -> IncomingEpisode | None:
        """The page as an episode if it is on the board, ready and has a source; else ``None``."""
        if not episode_ref.strip():
            return None
        page = self._board_page(episode_ref)
        if page is None or not self._is_ready(page):
            return None
        source_ref = self._text(page, self._settings.source_property)
        if not source_ref:
            return None
        return IncomingEpisode(
            episode_ref=episode_ref, source_ref=source_ref, mode=self._mode(page)
        )

    def report_status(self, episode_ref: str, /, *, run_id: str, status: RunStatus) -> None:
        """Write the Run's status and id onto its page; a page not on the board is refused."""
        if not episode_ref.strip():
            raise EpisodeBoardError("a status needs a non-blank episode reference")
        page = self._board_page(episode_ref)
        if page is None:
            raise EpisodeBoardError(f"episode {episode_ref!r} is not on the board")
        settings = self._settings
        for name in (settings.run_status_property, settings.run_id_property):
            self._property(page, name, ("rich_text",))
        properties = {
            settings.run_status_property: _rich_text(status.value),
            settings.run_id_property: _rich_text(run_id),
        }
        self._request("PATCH", _page_path(episode_ref), body={"properties": properties})

    # --- reading ------------------------------------------------------------------------

    def _board_page(self, episode_ref: str) -> dict[str, Any] | None:
        page = self._request("GET", _page_path(episode_ref), missing_ok=True)
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
        option = self._option(page, self._settings.ready_property)
        return option == self._settings.ready_value

    def _mode(self, page: dict[str, Any]) -> ClipMode:
        """The cutting mode, or ``EpisodeBoardError`` — a wrong option is misconfiguration.

        Deliberately **not** ``None``: a board whose mode column holds something that is not a
        ``ClipMode`` is broken, and reading it as "not ready" would hide that silently.
        """
        option = self._option(page, self._settings.mode_property)
        if option is None:
            raise EpisodeBoardError("the episode's cutting mode is not set")
        mode = MODES.get(option.strip().casefold())
        if mode is None:
            raise EpisodeBoardError(
                f"the cutting mode holds {option!r}; it must be one of " + ", ".join(sorted(MODES))
            )
        return mode

    def _option(self, page: dict[str, Any], name: str) -> str | None:
        prop = self._property(page, name, _OPTION_TYPES)
        option = prop.get(prop["type"])
        if option is None:
            return None
        if not isinstance(option, dict):
            raise EpisodeBoardError(f"Notion returned {name!r} with an unexpected shape")
        value = option.get("name")
        if not isinstance(value, str):
            raise EpisodeBoardError(f"Notion returned {name!r} without an option name")
        return value

    def _text(self, page: dict[str, Any], name: str) -> str:
        prop = self._property(page, name, ("rich_text",))
        chunks = prop.get("rich_text")
        if not isinstance(chunks, list):
            raise EpisodeBoardError(f"the Notion property {name!r} has an unexpected shape")
        return "".join(_plain_text(chunk) for chunk in chunks).strip()

    def _property(self, page: dict[str, Any], name: str, types: tuple[str, ...]) -> dict[str, Any]:
        properties = page.get("properties")
        prop = properties.get(name) if isinstance(properties, dict) else None
        if not isinstance(prop, dict):
            raise EpisodeBoardError(f"the Notion episode board has no property {name!r}")
        if prop.get("type") not in types:
            raise EpisodeBoardError(
                f"the Notion property {name!r} must be of type {' or '.join(types)}"
            )
        return prop

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
            raise EpisodeBoardError(
                f"Notion refused {method} {path.split('?')[0]}: HTTP {error.code}"
            ) from error
        except (urllib.error.URLError, OSError) as error:
            raise EpisodeBoardError(f"Notion could not be reached: {error}") from error
        return _json_object(raw)


def _json_object(raw: bytes) -> dict[str, Any]:
    try:
        decoded = json.loads(raw)
    except ValueError as error:
        raise EpisodeBoardError("Notion returned a response that is not JSON") from error
    if not isinstance(decoded, dict):
        raise EpisodeBoardError("Notion returned a response that is not a JSON object")
    return decoded


def _plain_text(chunk: object) -> str:
    text = chunk.get("plain_text") if isinstance(chunk, dict) else None
    return text if isinstance(text, str) else ""


def _rich_text(content: str) -> dict[str, Any]:
    return {"rich_text": [{"type": "text", "text": {"content": content}}]}


def _page_path(episode_ref: str) -> str:
    return f"/v1/pages/{_quoted(episode_ref)}"


def _quoted(value: str) -> str:
    # Dots are encoded too, so a ref of ".." cannot become a dot segment of the path.
    return urllib.parse.quote(value, safe="").replace(".", "%2E")


def _normalized_id(notion_id: str) -> str:
    return notion_id.replace("-", "").lower()
