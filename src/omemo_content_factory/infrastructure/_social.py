"""Shared plumbing of the direct Instagram and TikTok publishers (ADR-0099).

Local receipts — the idempotency key neither vendor offers — and one JSON-over-``urllib`` call.
Functions only: each publisher keeps its own settings, flow and error wording.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from omemo_content_factory.adapters.clip_publisher import ClipPublisherError, PublishRequest


def caption(request: PublishRequest) -> str:
    """The post text both platforms take as one field: title, blank line, description."""
    return f"{request.post.title}\n\n{request.post.description}".strip()


def receipt_path(root: Path, platform: str, request_id: str) -> Path:
    key = hashlib.sha256(f"{platform}:{request_id}".encode()).hexdigest()
    return root / f"{key}.json"


def read_receipt(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ClipPublisherError(
            f"Не удалось прочитать квитанцию {path.name}; повтор запрещён"
        ) from None
    if not isinstance(value, dict):
        raise ClipPublisherError(f"Квитанция {path.name} повреждена; повтор запрещён")
    return value


def reserve_receipt(path: Path, value: Mapping[str, Any]) -> None:
    """Create the receipt exclusively and flush it before any irreversible request."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise ClipPublisherError(
            "Эта отправка уже начиналась; проверьте платформу и квитанцию"
        ) from None
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(dict(value), stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())


def write_receipt(path: Path, value: Mapping[str, Any]) -> None:
    """Replace the receipt atomically with mode 0600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(dict(value), stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def request_json(
    method: str,
    url: str,
    *,
    vendor: str,
    headers: Mapping[str, str] | None = None,
    body: bytes | None = None,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """One HTTP call answered with a JSON object; any failure is a ``ClipPublisherError``.

    The vendor's error text is kept (it explains a refusal) but the URL and headers never enter a
    message: both can carry a token.
    """
    request = urllib.request.Request(url, data=body, method=method, headers=dict(headers or {}))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        detail = error.read()
        error.close()
        raise ClipPublisherError(f"{vendor} ответил HTTP {error.code}: {_detail(detail)}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise ClipPublisherError(f"{vendor} недоступен: {type(error).__name__}") from None
    try:
        answer = json.loads(raw) if raw else {}
    except ValueError:
        raise ClipPublisherError(f"{vendor} ответил не JSON") from None
    if not isinstance(answer, dict):
        raise ClipPublisherError(f"{vendor} ответил не объектом JSON")
    return answer


def _detail(raw: bytes) -> str:
    try:
        answer = json.loads(raw)
    except ValueError:
        return raw.decode("utf-8", "replace")[:300]
    if isinstance(answer, dict):
        error = answer.get("error")
        if isinstance(error, dict):
            words = [
                str(error[key])
                for key in ("code", "message", "error_user_msg")
                if error.get(key) not in (None, "")
            ]
            if words:
                return " — ".join(words)[:300]
        for key in ("error_description", "message"):
            if isinstance(answer.get(key), str):
                return str(answer[key])[:300]
    return json.dumps(answer, ensure_ascii=False)[:300]


def profile_path(root: Path, platform: str, profile: str) -> Path:
    """A separate environment file per account; never accepts a filesystem path."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", profile):
        raise ValueError("Имя профиля: 1–64 символа a-z, 0-9, дефис или подчёркивание")
    return root / f".env.{platform}-{profile}"


def read_token(path: Path, vendor: str) -> dict[str, Any]:
    """A stored OAuth token file; missing or damaged means the account is not connected."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ClipPublisherError(
            f"{vendor} ещё не подключён или токен повреждён: "
            f"запустите configure_{vendor.lower()}.py"
        ) from None
    if not isinstance(value, dict) or not isinstance(value.get("access_token"), str):
        raise ClipPublisherError(f"Токен {vendor} повреждён: повторите подключение")
    return value
