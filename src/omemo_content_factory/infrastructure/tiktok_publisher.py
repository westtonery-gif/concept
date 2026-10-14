"""Direct TikTok through the Content Posting API (ADR-0099).

Two routes, chosen by configuration: ``inbox`` puts the clip into the creator's TikTok inbox as a
draft (scope ``video.upload``), ``direct`` posts it (scope ``video.publish``; private until the
app passes TikTok's audit). Both upload the local file in sequential chunks to the URL an init
call returns.

TikTok takes no idempotency key, so the receipt is reserved before init and ``publish_id`` is
written right after it. A receipt without ``publish_id`` is uncertain and blocks a repeat.
A draft is not a post: ``SEND_TO_USER_INBOX`` is reported as ``PENDING`` until the creator
publishes it in the app.
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
    "DEFAULT_API_URL",
    "MODES",
    "PRIVACY_LEVELS",
    "TikTokPublisher",
    "TikTokSettings",
    "chunk_plan",
    "tiktok_settings_from_env",
]

DEFAULT_API_URL = "https://open.tiktokapis.com"
MODES = ("inbox", "direct")
PRIVACY_LEVELS = ("SELF_ONLY", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "PUBLIC_TO_EVERYONE")
MAX_TITLE = 2200

_MIN_CHUNK = 5 * 1024 * 1024
_MAX_SINGLE = 64 * 1024 * 1024
_CHUNK = 32 * 1024 * 1024
_REFRESH_MARGIN = 300.0
_PENDING = {"PROCESSING_UPLOAD", "PROCESSING_DOWNLOAD"}


def chunk_plan(size: int) -> tuple[int, int]:
    """``(chunk_size, total_chunk_count)`` by TikTok's rules: whole up to 64 MB, else 32 MB pieces
    with the remainder folded into the last one (count rounded down)."""
    if size <= 0:
        raise ClipPublisherError("Файл клипа пуст")
    if size <= _MAX_SINGLE:
        return size, 1
    return _CHUNK, size // _CHUNK


@dataclass(frozen=True)
class TikTokSettings:
    client_key: str
    client_secret: str = field(repr=False)
    token_path: Path
    open_id: str
    handle: str
    mode: str
    privacy: str
    receipts: Path
    api_url: str = DEFAULT_API_URL
    is_aigc: bool = False

    def __post_init__(self) -> None:
        if not self.client_key.strip() or not self.client_secret.strip():
            raise ClipPublisherError(
                "Задайте OMEMO_TIKTOK_CLIENT_KEY и OMEMO_TIKTOK_CLIENT_SECRET (configure_tiktok.py)"
            )
        if not self.open_id.strip():
            raise ClipPublisherError("TikTok ещё не подключён: запустите configure_tiktok.py")
        if self.mode not in MODES:
            raise ClipPublisherError("OMEMO_TIKTOK_MODE: inbox или direct")
        if self.privacy not in PRIVACY_LEVELS:
            raise ClipPublisherError("OMEMO_TIKTOK_PRIVACY: " + ", ".join(PRIVACY_LEVELS))


def tiktok_settings_from_env(environ: Mapping[str, str]) -> TikTokSettings:
    return TikTokSettings(
        client_key=environ.get("OMEMO_TIKTOK_CLIENT_KEY", ""),
        client_secret=environ.get("OMEMO_TIKTOK_CLIENT_SECRET", ""),
        token_path=Path(environ.get("OMEMO_TIKTOK_TOKEN_PATH", ".omemo/tiktok-token.json")),
        open_id=environ.get("OMEMO_TIKTOK_OPEN_ID", ""),
        handle=environ.get("OMEMO_TIKTOK_HANDLE", "").lstrip("@"),
        mode=environ.get("OMEMO_TIKTOK_MODE", "inbox").strip().casefold() or "inbox",
        privacy=environ.get("OMEMO_TIKTOK_PRIVACY", "SELF_ONLY").strip().upper() or "SELF_ONLY",
        receipts=Path(environ.get("OMEMO_TIKTOK_RECEIPTS", ".omemo/tiktok-uploads")),
    )


class TikTokPublisher:
    def __init__(self, settings: TikTokSettings, *, now: Callable[[], float] = time.time) -> None:
        self.settings = settings
        self._now = now

    # --- port ---------------------------------------------------------------------------

    def status(self, request_id: str, /) -> PublishStatus:
        receipt = read_receipt(self._receipt(request_id))
        if receipt is None:
            return PublishStatus(PublishState.NOT_FOUND)
        self._check_account(receipt)
        publish_id = receipt.get("publish_id")
        if not isinstance(publish_id, str) or not publish_id:
            raise ClipPublisherError(
                "Исход отправки в TikTok неизвестен (нет publish_id). Проверьте TikTok; "
                "автоматическая повторная отправка заблокирована, квитанцию не удаляйте"
            )
        data = self._api("/v2/post/publish/status/fetch/", {"publish_id": publish_id})
        state = data.get("status")
        if state in _PENDING:
            return PublishStatus(
                PublishState.PENDING,
                (PlatformResult("tiktok", False, None, "TikTok обрабатывает загрузку"),),
            )
        if state == "SEND_TO_USER_INBOX":
            return PublishStatus(
                PublishState.PENDING,
                (
                    PlatformResult(
                        "tiktok",
                        False,
                        None,
                        "Черновик во «Входящих» TikTok: опубликуйте его в приложении",
                    ),
                ),
            )
        if state == "PUBLISH_COMPLETE":
            ids = data.get("publicaly_available_post_id") or []
            url = (
                f"https://www.tiktok.com/@{self.settings.handle}/video/{ids[0]}"
                if ids and self.settings.handle
                else None
            )
            return PublishStatus(
                PublishState.COMPLETED,
                (
                    PlatformResult(
                        "tiktok",
                        True,
                        url,
                        None if url else "Опубликовано (приватно или ещё на модерации)",
                    ),
                ),
            )
        if state == "FAILED":
            return PublishStatus(
                PublishState.FAILED,
                (PlatformResult("tiktok", False, None, str(data.get("fail_reason") or "FAILED")),),
            )
        raise ClipPublisherError(f"TikTok вернул неизвестный статус {state!r}")

    def submit(self, request: PublishRequest, /) -> None:
        if request.platforms != ("tiktok",):
            raise ClipPublisherError("Прямой TikTok publisher поддерживает только tiktok")
        path = self._receipt(request.request_id)
        if read_receipt(path) is not None:
            self.status(request.request_id)  # known job: nothing to send; uncertain → raises
            return
        video = Path(request.video_path)
        if not video.is_file() or not video.stat().st_size:
            raise ClipPublisherError("Файл клипа отсутствует или пуст")
        size = video.stat().st_size
        chunk, count = chunk_plan(size)
        text = caption(request)
        source = {
            "source": "FILE_UPLOAD",
            "video_size": size,
            "chunk_size": chunk,
            "total_chunk_count": count,
        }
        if self.settings.mode == "direct":
            if len(text) > MAX_TITLE:
                raise ClipPublisherError("Подпись TikTok длиннее 2200 символов")
            info = self._api("/v2/post/publish/creator_info/query/", {})
            options = info.get("privacy_level_options") or []
            if self.settings.privacy not in options:
                raise ClipPublisherError(
                    f"TikTok не разрешает видимость {self.settings.privacy} для этого аккаунта; "
                    f"доступно: {', '.join(map(str, options)) or 'ничего'}"
                )
            route = "/v2/post/publish/video/init/"
            body: dict[str, Any] = {
                "post_info": {
                    "title": text,
                    "privacy_level": self.settings.privacy,
                    "disable_duet": False,
                    "disable_comment": False,
                    "disable_stitch": False,
                    "brand_content_toggle": False,
                    "brand_organic_toggle": False,
                    "is_aigc": self.settings.is_aigc,
                },
                "source_info": source,
            }
        else:
            route = "/v2/post/publish/inbox/video/init/"
            body = {"source_info": source}
        receipt: dict[str, Any] = {
            "request_id": request.request_id,
            "open_id": self.settings.open_id,
            "mode": self.settings.mode,
            "caption": text,
        }
        reserve_receipt(path, receipt)  # on disk before init: TikTok has no idempotency key
        data = self._api(route, body)
        publish_id, upload_url = data.get("publish_id"), data.get("upload_url")
        if not isinstance(publish_id, str) or not isinstance(upload_url, str):
            raise ClipPublisherError("TikTok не вернул publish_id и адрес загрузки")
        receipt = {**receipt, "publish_id": publish_id}
        write_receipt(path, receipt)
        with video.open("rb") as stream:
            for index in range(count):
                first = index * chunk
                last = size - 1 if index == count - 1 else first + chunk - 1
                piece = stream.read(last - first + 1)
                request_json(
                    "PUT",
                    upload_url,
                    vendor="TikTok",
                    headers={
                        "Content-Type": "video/mp4",
                        "Content-Range": f"bytes {first}-{last}/{size}",
                    },
                    body=piece,
                    timeout=900.0,
                )
        write_receipt(path, {**receipt, "uploaded": True})

    # --- plumbing -----------------------------------------------------------------------

    def _receipt(self, request_id: str) -> Path:
        return receipt_path(self.settings.receipts, "tiktok", request_id)

    def _check_account(self, receipt: Mapping[str, Any]) -> None:
        if receipt.get("open_id") != self.settings.open_id:
            raise ClipPublisherError(
                "Квитанция относится к другому аккаунту TikTok; повтор запрещён"
            )

    def _api(self, route: str, body: Mapping[str, Any]) -> dict[str, Any]:
        answer = request_json(
            "POST",
            self.settings.api_url.rstrip("/") + route,
            vendor="TikTok",
            headers={
                "Authorization": f"Bearer {self._access_token()}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            body=json.dumps(body, ensure_ascii=False).encode(),
        )
        error = answer.get("error")
        code = error.get("code") if isinstance(error, dict) else None
        if code not in (None, "ok"):
            message = error.get("message", "") if isinstance(error, dict) else ""
            raise ClipPublisherError(f"TikTok отказал: {code} {message}".strip())
        data = answer.get("data")
        if not isinstance(data, dict):
            raise ClipPublisherError("TikTok ответил без данных")
        return data

    def _access_token(self) -> str:
        """The stored token, refreshed when it has under five minutes left."""
        token = read_token(self.settings.token_path, "TikTok")
        if token.get("open_id") != self.settings.open_id:
            raise ClipPublisherError(
                "Токен TikTok относится к другому аккаунту; повторите configure_tiktok.py"
            )
        if float(token.get("expires_at", 0)) - _REFRESH_MARGIN > self._now():
            return str(token["access_token"])
        refreshed = refresh_token(
            self.settings.api_url,
            self.settings.client_key,
            self.settings.client_secret,
            str(token.get("refresh_token", "")),
            now=self._now(),
        )
        refreshed["open_id"] = refreshed["open_id"] or self.settings.open_id
        write_receipt(self.settings.token_path, refreshed)
        return str(refreshed["access_token"])


def token_request(api_url: str, form: Mapping[str, str], *, now: float) -> dict[str, Any]:
    """``POST /v2/oauth/token/`` → a token record with absolute expiry times."""
    answer = request_json(
        "POST",
        api_url.rstrip("/") + "/v2/oauth/token/",
        vendor="TikTok",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        body=urllib.parse.urlencode(dict(form)).encode(),
    )
    if not isinstance(answer.get("access_token"), str) or not isinstance(
        answer.get("refresh_token"), str
    ):
        raise ClipPublisherError(
            "TikTok не выдал токен"
            + (f": {answer['error_description']}" if answer.get("error_description") else "")
        )
    return {
        "access_token": answer["access_token"],
        "refresh_token": answer["refresh_token"],
        "open_id": answer.get("open_id", ""),
        "scope": answer.get("scope", ""),
        "expires_at": now + float(answer.get("expires_in", 0)),
        "refresh_expires_at": now + float(answer.get("refresh_expires_in", 0)),
    }


def refresh_token(
    api_url: str, client_key: str, client_secret: str, refresh: str, *, now: float
) -> dict[str, Any]:
    if not refresh:
        raise ClipPublisherError("Нет refresh token TikTok; повторите configure_tiktok.py")
    try:
        return token_request(
            api_url,
            {
                "client_key": client_key,
                "client_secret": client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh,
            },
            now=now,
        )
    except ClipPublisherError as error:
        raise ClipPublisherError(
            f"Не удалось обновить токен TikTok ({error}); повторите configure_tiktok.py"
        ) from None
