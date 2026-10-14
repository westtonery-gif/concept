"""Shared Notion HTTP and payload helpers for the three adapters (ADR-0098)."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

NOTION_API_VERSION = "2022-06-28"
DEFAULT_API_URL = "https://api.notion.com"


def request_json(
    method: str,
    path: str,
    *,
    api_url: str,
    token: str,
    timeout: float,
    error_type: type[Exception],
    body: dict[str, Any] | None = None,
    missing_ok: bool = False,
) -> dict[str, Any] | None:
    """Send a request, preserving the calling adapter's errors and missing-page policy."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_API_VERSION,
        "Accept": "application/json",
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(api_url + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        error.close()
        if error.code == 404 and missing_ok:
            return None
        raise error_type(
            f"Notion refused {method} {path.split('?')[0]}: HTTP {error.code}"
        ) from error
    except (urllib.error.URLError, OSError) as error:
        raise error_type(f"Notion could not be reached: {error}") from error
    try:
        decoded = json.loads(raw)
    except ValueError as error:
        raise error_type("Notion returned a response that is not JSON") from error
    if not isinstance(decoded, dict):
        raise error_type("Notion returned a response that is not a JSON object")
    return decoded


def plain_text(chunk: object) -> str:
    text = chunk.get("plain_text") if isinstance(chunk, dict) else None
    return text if isinstance(text, str) else ""


def quoted(value: str) -> str:
    # Dots are encoded too, so a ref of ".." cannot become a dot segment of the path.
    return urllib.parse.quote(value, safe="").replace(".", "%2E")
