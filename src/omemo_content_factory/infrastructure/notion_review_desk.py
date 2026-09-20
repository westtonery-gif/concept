"""The Notion Adapter — a real ``ReviewDesk`` over the Notion REST API (ADR-0060).

A review is a page of its own database: the package is written into the page's blocks, the
reviewer answers in **typed properties** — a ``select`` for the decision and a ``rich_text`` for
the reason — and the page's ``url`` is the location the port returns. The API is reached through
``urllib`` with the version pinned; no Notion shape leaves this module (ADAPTER_SPEC §3).

Unlike ``GoogleDocsReviewDesk`` (ADR-0043) there is no marker line to parse: a Doc has no
properties, a Notion page does, so the decision is a dropdown the reviewer cannot mistype. The only
ambiguity left is "unset", which is ``None`` — still pending. An option outside the three is a
misconfigured database and is refused, never read as silence.

This is the **second** Notion implementation. Its request helper, auth header and property reader
are duplicated from ``notion_brief_board`` on purpose: the rule of three (``CLAUDE.md``
Conventions) authorises duplication at two, and ADR-0061 records that the shared client is
extracted only once three implementations exist in ``src/``.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from omemo_content_factory.adapters.review_desk import (
    ReviewDecision,
    ReviewDeskError,
    ReviewPackage,
)
from omemo_content_factory.domain.human_review import ReviewId, ReviewStatus

__all__ = [
    "DEFAULT_API_URL",
    "NOTION_API_VERSION",
    "NotionReviewDesk",
    "NotionReviewSettings",
    "notion_review_settings_from_env",
]

NOTION_API_VERSION = "2022-06-28"
DEFAULT_API_URL = "https://api.notion.com"

TOKEN_VAR = "OMEMO_REVIEW_NOTION_TOKEN"
DATABASE_ID_VAR = "OMEMO_REVIEW_NOTION_DATABASE_ID"
TITLE_PROPERTY_VAR = "OMEMO_REVIEW_NOTION_TITLE_PROPERTY"
REVIEW_ID_PROPERTY_VAR = "OMEMO_REVIEW_NOTION_REVIEW_ID_PROPERTY"
DECISION_PROPERTY_VAR = "OMEMO_REVIEW_NOTION_DECISION_PROPERTY"
REASON_PROPERTY_VAR = "OMEMO_REVIEW_NOTION_REASON_PROPERTY"
FINGERPRINT_PROPERTY_VAR = "OMEMO_REVIEW_NOTION_FINGERPRINT_PROPERTY"

_DECISION_TYPES = ("select", "status")
"""The Notion API cannot create ``status`` properties, only ``select`` — both are read the same."""

DECISIONS: Mapping[str, ReviewStatus] = {
    "одобрено": ReviewStatus.APPROVED,
    "отклонено": ReviewStatus.REJECTED,
    "доработать": ReviewStatus.CHANGES_REQUESTED,
    ReviewStatus.APPROVED.value: ReviewStatus.APPROVED,
    ReviewStatus.REJECTED.value: ReviewStatus.REJECTED,
    ReviewStatus.CHANGES_REQUESTED.value: ReviewStatus.CHANGES_REQUESTED,
}
"""The three options, plus the ``ReviewStatus`` spellings — the same tolerance ADR-0043 §2 keeps."""

_MAX_TEXT = 2000
"""Notion refuses a rich_text chunk longer than this."""

_MAX_CHILDREN = 100
"""Notion refuses more children than this in one request."""


@dataclass(frozen=True, slots=True)
class NotionReviewSettings:
    """Which database holds the reviews and which of its properties the adapter reads and writes."""

    token: str = field(repr=False)
    database_id: str
    title_property: str
    review_id_property: str
    decision_property: str
    reason_property: str
    fingerprint_property: str

    def __post_init__(self) -> None:
        for name in (
            "token",
            "database_id",
            "title_property",
            "review_id_property",
            "decision_property",
            "reason_property",
            "fingerprint_property",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Notion review settings need a non-blank {name}")


def notion_review_settings_from_env(environ: Mapping[str, str]) -> NotionReviewSettings:
    """Read the seven required variables; a missing or blank one fails closed, named, no values."""
    names = (
        TOKEN_VAR,
        DATABASE_ID_VAR,
        TITLE_PROPERTY_VAR,
        REVIEW_ID_PROPERTY_VAR,
        DECISION_PROPERTY_VAR,
        REASON_PROPERTY_VAR,
        FINGERPRINT_PROPERTY_VAR,
    )
    values = {name: environ.get(name, "").strip() for name in names}
    missing = [name for name in names if not values[name]]
    if missing:
        raise ReviewDeskError("the Notion review desk is not configured; set " + ", ".join(missing))
    return NotionReviewSettings(
        token=values[TOKEN_VAR],
        database_id=values[DATABASE_ID_VAR],
        title_property=values[TITLE_PROPERTY_VAR],
        review_id_property=values[REVIEW_ID_PROPERTY_VAR],
        decision_property=values[DECISION_PROPERTY_VAR],
        reason_property=values[REASON_PROPERTY_VAR],
        fingerprint_property=values[FINGERPRINT_PROPERTY_VAR],
    )


class NotionReviewDesk:
    """A ``ReviewDesk`` whose reviews are pages of one Notion database (ADAPTER_SPEC §6)."""

    def __init__(
        self,
        settings: NotionReviewSettings,
        *,
        api_url: str = DEFAULT_API_URL,
        timeout: float = 10.0,
    ) -> None:
        self._settings = settings
        self._api_url = api_url.rstrip("/")
        self._timeout = timeout

    def publish(self, package: ReviewPackage, /) -> str:
        """Put the package in front of the reviewer; return the page's URL.

        Publishing the same ``review_id`` again returns the same page instead of a second copy
        (ADR-0023 §7), which is also how a failed publication is retried (ADR-0044 §4). A different
        package under a published ``review_id`` is refused: the reviewer would be deciding on
        something other than what was published.
        """
        fingerprint = _fingerprint(package)
        page = self._page_for(package.review_id)
        if page is not None:
            if self._text_property(page, self._settings.fingerprint_property) != fingerprint:
                raise ReviewDeskError(
                    f"review {package.review_id!r} was published with a different package"
                )
            return self._location(page)
        return self._location(self._create(package, fingerprint))

    def fetch_decision(self, review_id: ReviewId, /) -> ReviewDecision | None:
        """The reviewer's decision, or ``None`` while the decision property is unset.

        A ``review_id`` that was never published raises ``ReviewDeskError``, as the port requires.
        """
        page = self._page_for(review_id)
        if page is None:
            raise ReviewDeskError(f"review {review_id!r} was never published")
        prop = self._property(page, self._settings.decision_property, _DECISION_TYPES)
        option = prop.get(prop["type"])
        if option is None:
            return None
        if not isinstance(option, dict):
            raise ReviewDeskError("Notion returned a decision option of an unexpected shape")
        name = option.get("name")
        if not isinstance(name, str):
            raise ReviewDeskError("Notion returned a decision option without a name")
        decision = DECISIONS.get(name.strip().casefold())
        if decision is None:
            raise ReviewDeskError(
                f"the decision property holds {name!r}, which is none of the three options"
            )
        reason = self._text_property(page, self._settings.reason_property)
        return ReviewDecision(decision=decision, reason=reason or None)

    # --- reading ------------------------------------------------------------------------

    def _page_for(self, review_id: str) -> dict[str, Any] | None:
        if not isinstance(review_id, str) or not review_id.strip():
            raise ReviewDeskError("a review needs a non-blank review id")
        body = {
            "filter": {
                "property": self._settings.review_id_property,
                "rich_text": {"equals": review_id},
            },
            "page_size": 2,
        }
        path = f"/v1/databases/{_quoted(self._settings.database_id)}/query"
        listing = self._request("POST", path, body=body)
        results = listing.get("results") if listing is not None else None
        if listing is None or not isinstance(results, list):
            raise ReviewDeskError("Notion returned a review listing of an unexpected shape")
        pages = [
            page
            for page in results
            if isinstance(page, dict)
            and page.get("archived") is not True
            and page.get("in_trash") is not True
        ]
        if len(pages) > 1:
            raise ReviewDeskError(f"review {review_id!r} has more than one page")
        return pages[0] if pages else None

    def _location(self, page: dict[str, Any]) -> str:
        url = page.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ReviewDeskError("Notion returned a review page without a url")
        return url

    def _text_property(self, page: dict[str, Any], name: str) -> str:
        prop = self._property(page, name, ("rich_text",))
        chunks = prop.get("rich_text")
        if not isinstance(chunks, list):
            raise ReviewDeskError(f"the Notion property {name!r} has an unexpected shape")
        return "".join(_plain_text(chunk) for chunk in chunks).strip()

    def _property(self, page: dict[str, Any], name: str, types: tuple[str, ...]) -> dict[str, Any]:
        properties = page.get("properties")
        prop = properties.get(name) if isinstance(properties, dict) else None
        if not isinstance(prop, dict):
            raise ReviewDeskError(f"the Notion review database has no property {name!r}")
        if prop.get("type") not in types:
            raise ReviewDeskError(
                f"the Notion property {name!r} must be of type {' or '.join(types)}"
            )
        return prop

    # --- writing ------------------------------------------------------------------------

    def _create(self, package: ReviewPackage, fingerprint: str) -> dict[str, Any]:
        settings = self._settings
        blocks = _blocks(package)
        body = {
            "parent": {"database_id": settings.database_id},
            "properties": {
                settings.title_property: _title(_page_title(package)),
                settings.review_id_property: _rich_text(package.review_id),
                settings.fingerprint_property: _rich_text(fingerprint),
            },
            "children": blocks[:_MAX_CHILDREN],
        }
        page = self._request("POST", "/v1/pages", body=body)
        if page is None:
            raise ReviewDeskError("Notion returned no page for a created review")
        page_id = page.get("id")
        if not isinstance(page_id, str) or not page_id:
            raise ReviewDeskError("Notion returned a created review without an id")
        for start in range(_MAX_CHILDREN, len(blocks), _MAX_CHILDREN):
            self._request(
                "PATCH",
                f"/v1/blocks/{_quoted(page_id)}/children",
                body={"children": blocks[start : start + _MAX_CHILDREN]},
            )
        return page

    # --- transport ----------------------------------------------------------------------

    def _request(
        self, method: str, path: str, *, body: dict[str, Any] | None = None
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
            raise ReviewDeskError(
                f"Notion refused {method} {path.split('?')[0]}: HTTP {error.code}"
            ) from error
        except (urllib.error.URLError, OSError) as error:
            raise ReviewDeskError(f"Notion could not be reached: {error}") from error
        return _json_object(raw)


# --- rendering --------------------------------------------------------------------------


def _page_title(package: ReviewPackage) -> str:
    candidate = package.candidate
    return f"{package.run_id} · {candidate.kind} v{candidate.version}"


def _blocks(package: ReviewPackage) -> list[dict[str, Any]]:
    candidate = package.candidate
    blocks: list[dict[str, Any]] = [
        _paragraph(
            "Выберите решение в свойстве страницы и, если нужно, заполните причину. "
            "Пока решение не выставлено, ревью считается незавершённым."
        ),
        _heading("Что ревьюится"),
        _paragraph(
            f"Run: {package.run_id} · Артефакт: {candidate.artifact_id} "
            f"({candidate.kind}, версия {candidate.version})"
        ),
        _heading("Исходный запрос"),
    ]
    blocks.extend(_paragraphs(package.brief))
    blocks.append(_heading("Замечания QA"))
    if package.qa_flags:
        blocks.extend(_bullet(flag) for flag in package.qa_flags)
    else:
        blocks.append(_paragraph("Нет."))
    blocks.append(_heading("Материал"))
    blocks.extend(_paragraphs(candidate.content))
    return blocks


def _paragraphs(text: str) -> list[dict[str, Any]]:
    lines = [line for line in text.splitlines() if line.strip()] or [""]
    return [_paragraph(chunk) for line in lines for chunk in _chunks(line)]


def _chunks(text: str) -> list[str]:
    if len(text) <= _MAX_TEXT:
        return [text]
    return [text[start : start + _MAX_TEXT] for start in range(0, len(text), _MAX_TEXT)]


def _paragraph(content: str) -> dict[str, Any]:
    return {"object": "block", "type": "paragraph", "paragraph": _text_payload(content)}


def _heading(content: str) -> dict[str, Any]:
    return {"object": "block", "type": "heading_2", "heading_2": _text_payload(content)}


def _bullet(content: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": _text_payload(content[:_MAX_TEXT]),
    }


def _text_payload(content: str) -> dict[str, Any]:
    return {"rich_text": [{"type": "text", "text": {"content": content}}]}


def _title(content: str) -> dict[str, Any]:
    return {"title": [{"type": "text", "text": {"content": content[:_MAX_TEXT]}}]}


def _rich_text(content: str) -> dict[str, Any]:
    return {"rich_text": [{"type": "text", "text": {"content": content[:_MAX_TEXT]}}]}


def _fingerprint(package: ReviewPackage) -> str:
    """A stable digest of everything a reviewer is shown (ADR-0043 §3's check, a property here)."""
    candidate = package.candidate
    canonical = json.dumps(
        {
            "run_id": package.run_id,
            "review_id": package.review_id,
            "artifact_id": candidate.artifact_id,
            "kind": candidate.kind,
            "version": candidate.version,
            "content": candidate.content,
            "brief": package.brief,
            "qa_flags": list(package.qa_flags),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _plain_text(chunk: object) -> str:
    text = chunk.get("plain_text") if isinstance(chunk, dict) else None
    return text if isinstance(text, str) else ""


def _json_object(raw: bytes) -> dict[str, Any]:
    try:
        decoded = json.loads(raw)
    except ValueError as error:
        raise ReviewDeskError("Notion returned a response that is not JSON") from error
    if not isinstance(decoded, dict):
        raise ReviewDeskError("Notion returned a response that is not a JSON object")
    return decoded


def _quoted(value: str) -> str:
    # Dots are encoded too, so a ref of ".." cannot become a dot segment of the path.
    return urllib.parse.quote(value, safe="").replace(".", "%2E")
