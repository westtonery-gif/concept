"""Direct YouTube upload. Local receipts prevent duplicate sends after an uncertain outcome."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from omemo_content_factory.adapters.clip_publisher import (
    ClipPublisherError,
    PlatformResult,
    PublishRequest,
    PublishState,
    PublishStatus,
)

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


def youtube_profile_path(root: Path, profile: str) -> Path:
    """A separate environment file per channel; never accepts a filesystem path."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", profile):
        raise ValueError("Имя профиля: 1–64 символа a-z, 0-9, дефис или подчёркивание")
    return root / f".env.youtube-{profile}"


def write_private_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


@dataclass(frozen=True)
class YouTubeSettings:
    token_path: Path
    channel_id: str
    privacy: str
    receipts: Path
    made_for_kids: bool = False

    def __post_init__(self) -> None:
        if not self.channel_id.strip():
            raise ClipPublisherError("Задайте OMEMO_YOUTUBE_CHANNEL_ID через configure_youtube.py")
        if self.privacy not in {"private", "unlisted", "public"}:
            raise ClipPublisherError("OMEMO_YOUTUBE_PRIVACY: private, unlisted или public")


def youtube_settings_from_env(environ: Mapping[str, str]) -> YouTubeSettings:
    return YouTubeSettings(
        token_path=Path(environ.get("OMEMO_YOUTUBE_TOKEN_PATH", ".omemo/youtube-token.json")),
        channel_id=environ.get("OMEMO_YOUTUBE_CHANNEL_ID", ""),
        privacy=environ.get("OMEMO_YOUTUBE_PRIVACY", "private"),
        receipts=Path(environ.get("OMEMO_YOUTUBE_RECEIPTS", ".omemo/youtube-uploads")),
    )


def youtube_service(token_path: Path) -> Any:
    # Optional dependency: other publishers do not need the Google SDK.
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build  # type: ignore[import-untyped]

    if not token_path.is_file():
        raise ClipPublisherError("YouTube ещё не подключён: запустите configure_youtube.py")
    try:
        credentials = Credentials.from_authorized_user_file(  # type: ignore[no-untyped-call]
            str(token_path), SCOPES
        )
        if not credentials.valid:
            credentials.refresh(Request())
            write_private_json(token_path, json.loads(credentials.to_json()))
        return build("youtube", "v3", credentials=credentials, cache_discovery=False)
    except Exception:
        raise ClipPublisherError(
            "Не удалось подключиться к YouTube; проверьте OAuth и сеть"
        ) from None


def verify_channel(service: Any, channel_id: str) -> None:
    channels = service.channels().list(part="id", mine=True).execute()
    if [item["id"] for item in channels.get("items", [])] != [channel_id]:
        raise ClipPublisherError(
            "Подключён другой канал YouTube. Повторите OAuth для нужного канала"
        )


class YouTubePublisher:
    def __init__(self, settings: YouTubeSettings, *, service: Any = None) -> None:
        self.settings = settings
        self._service = service

    def _client(self) -> Any:
        if self._service is None:
            self._service = youtube_service(self.settings.token_path)
        return self._service

    def _receipt(self, request_id: str) -> Path:
        key = hashlib.sha256(request_id.encode()).hexdigest()
        return self.settings.receipts / f"{key}.json"

    def status(self, request_id: str, /) -> PublishStatus:
        path = self._receipt(request_id)
        if not path.exists():
            return PublishStatus(PublishState.NOT_FOUND)
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ClipPublisherError(
                "Не удалось прочитать квитанцию YouTube; повтор запрещён"
            ) from None
        if receipt.get("channel_id") != self.settings.channel_id:
            raise ClipPublisherError("Квитанция относится к другому каналу; повтор запрещён")
        if video_id := receipt.get("video_id"):
            return PublishStatus(
                PublishState.COMPLETED,
                (PlatformResult("youtube", True, f"https://www.youtube.com/watch?v={video_id}"),),
            )
        raise ClipPublisherError(
            "Исход предыдущей загрузки неизвестен. Проверьте YouTube Studio; "
            "автоматическая повторная отправка заблокирована"
        )

    def submit(self, request: PublishRequest, /) -> None:
        if request.platforms != ("youtube",):
            raise ClipPublisherError("Прямой YouTube publisher поддерживает только youtube")
        if self.status(request.request_id).state is PublishState.COMPLETED:
            return
        video = Path(request.video_path)
        if not video.is_file() or not video.stat().st_size:
            raise ClipPublisherError("Файл клипа отсутствует или пуст")
        from googleapiclient.http import MediaFileUpload  # type: ignore[import-untyped]

        try:
            service = self._client()
            verify_channel(service, self.settings.channel_id)
            upload = service.videos().insert(
                part="snippet,status",
                body={
                    "snippet": {
                        "title": request.post.title,
                        "description": request.post.description,
                    },
                    "status": {
                        "privacyStatus": self.settings.privacy,
                        "selfDeclaredMadeForKids": self.settings.made_for_kids,
                    },
                },
                media_body=MediaFileUpload(str(video), chunksize=8 * 1024 * 1024, resumable=True),
            )
            path = self._receipt(request.request_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            receipt = {"request_id": request.request_id, "channel_id": self.settings.channel_id}
            # Exclusive reservation must reach disk before the first upload request.
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(receipt, stream)
                stream.flush()
                os.fsync(stream.fileno())
            response = None
            while response is None:
                _, response = upload.next_chunk(num_retries=3)
            if not response.get("id"):
                raise ClipPublisherError("YouTube не вернул ID видео; проверьте Studio")
            write_private_json(path, {**receipt, "video_id": response["id"]})
        except ClipPublisherError:
            raise
        except Exception:
            # Do not expose SDK exceptions: they can contain OAuth data or upload session URLs.
            raise ClipPublisherError(
                "Загрузка YouTube не подтверждена. Проверьте подключение и YouTube Studio; "
                "сохранённую квитанцию не удаляйте"
            ) from None
