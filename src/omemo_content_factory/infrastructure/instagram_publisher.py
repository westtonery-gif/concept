"""Direct Instagram Reels through the Graph API with Facebook Login (ADR-0099).

A reel goes up in four requests: a resumable container, the file's bytes to
``rupload.facebook.com``, a wait for the container to be ``FINISHED``, and ``media_publish``.
Only the last one is visible to anyone, so everything before it may simply start again; the
receipt records ``publishing`` before that call, and a job left there without a media id is
uncertain and is never repeated by code.
"""

from __future__ import annotations

import json
import time
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from omemo_content_factory.adapters.clip_publisher import (
    ClipPublisherError,
    PlatformResult,
    PublishRequest,
    PublishState,
    PublishStatus,
)
from omemo_content_factory.infrastructure._social import (
    caption,
    read_receipt,
    read_token,
    receipt_path,
    request_json,
    reserve_receipt,
    write_receipt,
)

__all__ = [
    "DEFAULT_GRAPH_URL",
    "DEFAULT_GRAPH_VERSION",
    "DEFAULT_UPLOAD_URL",
    "InstagramPublisher",
    "InstagramSettings",
    "instagram_settings_from_env",
]

DEFAULT_GRAPH_URL = "https://graph.facebook.com"
DEFAULT_UPLOAD_URL = "https://rupload.facebook.com"
DEFAULT_GRAPH_VERSION = "v25.0"
MAX_BYTES = 300 * 1024 * 1024
"""Instagram's documented limit for a reel."""
MAX_CAPTION = 2200

_RESTARTABLE = {"reserved", "created"}
"""Phases with nothing public yet: a new container is simply made (the old one expires)."""


@dataclass(frozen=True)
class InstagramSettings:
    token_path: Path
    user_id: str
    handle: str
    receipts: Path
    graph_version: str = DEFAULT_GRAPH_VERSION
    graph_url: str = DEFAULT_GRAPH_URL
    upload_url: str = DEFAULT_UPLOAD_URL
    share_to_feed: bool = True
    access_token: str | None = field(default=None, repr=False)
    """Only for tests and embedding; otherwise the token is read from ``token_path``."""

    def __post_init__(self) -> None:
        if not self.user_id.strip():
            raise ClipPublisherError("Задайте OMEMO_INSTAGRAM_USER_ID через configure_instagram.py")


def instagram_settings_from_env(environ: Mapping[str, str]) -> InstagramSettings:
    return InstagramSettings(
        token_path=Path(environ.get("OMEMO_INSTAGRAM_TOKEN_PATH", ".omemo/instagram-token.json")),
        user_id=environ.get("OMEMO_INSTAGRAM_USER_ID", ""),
        handle=environ.get("OMEMO_INSTAGRAM_HANDLE", ""),
        receipts=Path(environ.get("OMEMO_INSTAGRAM_RECEIPTS", ".omemo/instagram-uploads")),
        graph_version=environ.get("OMEMO_INSTAGRAM_GRAPH_VERSION", "").strip()
        or DEFAULT_GRAPH_VERSION,
    )


class InstagramPublisher:
    def __init__(
        self,
        settings: InstagramSettings,
        *,
        sleep: Callable[[float], None] = time.sleep,
        wait_seconds: float = 600.0,
        poll_seconds: float = 10.0,
    ) -> None:
        self.settings = settings
        self._sleep = sleep
        self._wait = wait_seconds
        self._poll = poll_seconds

    # --- port ---------------------------------------------------------------------------

    def status(self, request_id: str, /) -> PublishStatus:
        """What the receipt proves; an uploaded job is moved on (published once ``FINISHED``)."""
        path = self._receipt(request_id)
        receipt = read_receipt(path)
        if receipt is None:
            return PublishStatus(PublishState.NOT_FOUND)
        self._check_account(receipt)
        phase = receipt.get("phase")
        if phase in _RESTARTABLE:
            return PublishStatus(PublishState.NOT_FOUND)
        if phase == "uploaded":
            receipt = self._advance(path, receipt, wait=False)
            phase = receipt.get("phase")
        if phase == "uploaded":
            return PublishStatus(
                PublishState.PENDING,
                (PlatformResult("instagram", False, None, "Instagram обрабатывает видео"),),
            )
        if phase == "published":
            return PublishStatus(
                PublishState.COMPLETED,
                (PlatformResult("instagram", True, self._permalink(path, receipt)),),
            )
        if phase == "failed":
            return PublishStatus(
                PublishState.FAILED,
                (PlatformResult("instagram", False, None, str(receipt.get("message", ""))),),
            )
        raise ClipPublisherError(
            "Исход публикации в Instagram неизвестен. Проверьте профиль в Instagram; "
            "автоматическая повторная отправка заблокирована, квитанцию не удаляйте"
        )

    def submit(self, request: PublishRequest, /) -> None:
        if request.platforms != ("instagram",):
            raise ClipPublisherError("Прямой Instagram publisher поддерживает только instagram")
        path = self._receipt(request.request_id)
        existing = read_receipt(path)
        if existing is not None and self._continued(request.request_id, path, existing):
            return
        video = Path(request.video_path)
        if not video.is_file() or not video.stat().st_size:
            raise ClipPublisherError("Файл клипа отсутствует или пуст")
        size = video.stat().st_size
        if size > MAX_BYTES:
            raise ClipPublisherError("Instagram принимает Reels до 300 МБ; уменьшите файл")
        text = caption(request)
        if len(text) > MAX_CAPTION:
            raise ClipPublisherError("Подпись Instagram длиннее 2200 символов")
        receipt: dict[str, Any] = {
            "request_id": request.request_id,
            "user_id": self.settings.user_id,
            "phase": "reserved",
        }
        if existing is None:
            reserve_receipt(path, receipt)
        else:
            write_receipt(path, receipt)
        created = self._graph(
            "POST",
            f"{self.settings.user_id}/media",
            body={
                "media_type": "REELS",
                "upload_type": "resumable",
                "caption": text,
                "share_to_feed": self.settings.share_to_feed,
            },
        )
        container = created.get("id")
        if not isinstance(container, str) or not container:
            raise ClipPublisherError("Instagram не вернул ID контейнера")
        receipt = {**receipt, "phase": "created", "container_id": container}
        write_receipt(path, receipt)
        uploaded = request_json(
            "POST",
            f"{self.settings.upload_url.rstrip('/')}/ig-api-upload/"
            f"{self.settings.graph_version}/{urllib.parse.quote(container, safe='')}",
            vendor="Instagram",
            headers={
                "Authorization": f"OAuth {self._token()}",
                "offset": "0",
                "file_size": str(size),
            },
            body=video.read_bytes(),
            timeout=900.0,
        )
        if uploaded.get("success") is not True:
            raise ClipPublisherError("Instagram не подтвердил загрузку файла")
        receipt = {**receipt, "phase": "uploaded"}
        write_receipt(path, receipt)
        self._advance(path, receipt, wait=True)

    # --- flow ---------------------------------------------------------------------------

    def _continued(self, request_id: str, path: Path, receipt: dict[str, Any]) -> bool:
        """Finish a job past the restartable phases; ``False`` means start it again."""
        self._check_account(receipt)
        if receipt.get("phase") == "uploaded":
            self._advance(path, receipt, wait=True)
            return True
        if receipt.get("phase") in _RESTARTABLE:
            return False
        self.status(request_id)  # published / failed: nothing to do; uncertain: raises
        return True

    def _advance(self, path: Path, receipt: dict[str, Any], *, wait: bool) -> dict[str, Any]:
        """Publish an uploaded container once Instagram has processed it."""
        container = str(receipt["container_id"])
        deadline = time.monotonic() + (self._wait if wait else 0.0)
        while True:
            answer = self._graph("GET", container, query={"fields": "status_code,status"})
            code = answer.get("status_code")
            if code == "FINISHED":
                break
            if code in ("ERROR", "EXPIRED"):
                failed = {
                    **receipt,
                    "phase": "failed",
                    "message": f"{code}: {answer.get('status') or 'Instagram отклонил видео'}",
                }
                write_receipt(path, failed)
                return failed
            if code == "PUBLISHED":
                # Published outside this receipt's knowledge: never publish it again.
                publishing = {**receipt, "phase": "publishing"}
                write_receipt(path, publishing)
                return publishing
            if code != "IN_PROGRESS":
                raise ClipPublisherError(f"Instagram вернул неизвестный статус {code!r}")
            if time.monotonic() >= deadline:
                return receipt
            self._sleep(self._poll)
        publishing = {**receipt, "phase": "publishing"}
        write_receipt(path, publishing)  # on disk before the one irreversible request
        published = self._graph(
            "POST", f"{self.settings.user_id}/media_publish", body={"creation_id": container}
        )
        media = published.get("id")
        if not isinstance(media, str) or not media:
            raise ClipPublisherError("Instagram не вернул ID публикации; проверьте профиль")
        done = {**publishing, "phase": "published", "media_id": media}
        write_receipt(path, done)
        return done

    def _permalink(self, path: Path, receipt: dict[str, Any]) -> str | None:
        if isinstance(receipt.get("permalink"), str):
            return str(receipt["permalink"])
        try:
            answer = self._graph("GET", str(receipt["media_id"]), query={"fields": "permalink"})
        except ClipPublisherError:
            return None  # the post exists; a link is a convenience
        link = answer.get("permalink")
        if not isinstance(link, str) or not link:
            return None
        write_receipt(path, {**receipt, "permalink": link})
        return link

    # --- plumbing -----------------------------------------------------------------------

    def _receipt(self, request_id: str) -> Path:
        return receipt_path(self.settings.receipts, "instagram", request_id)

    def _check_account(self, receipt: Mapping[str, Any]) -> None:
        if receipt.get("user_id") != self.settings.user_id:
            raise ClipPublisherError(
                "Квитанция относится к другому аккаунту Instagram; повтор запрещён"
            )

    def _token(self) -> str:
        if self.settings.access_token is not None:
            return self.settings.access_token
        return str(read_token(self.settings.token_path, "Instagram")["access_token"])

    def _graph(
        self,
        method: str,
        path: str,
        *,
        body: Mapping[str, Any] | None = None,
        query: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.settings.graph_url.rstrip('/')}/{self.settings.graph_version}/{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        headers = {"Authorization": f"Bearer {self._token()}"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=False).encode()
        return request_json(method, url, vendor="Instagram", headers=headers, body=data)
