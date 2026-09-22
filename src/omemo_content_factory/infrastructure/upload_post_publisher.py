"""A ``ClipPublisher`` over upload-post's HTTP API (ADR-0073 §2).

One API posts to YouTube, TikTok and Instagram; upload-post owns the platform apps and their
audits. Reached through stdlib ``urllib`` like every vendor here, with the multipart body built by
hand — no new dependency for one endpoint.

``submit`` sends ``async_upload=true`` with **our** ``request_id`` and the same value as the
``Idempotency-Key`` header, so a repeated submission returns the job that exists instead of
posting again. ``status`` asks for that id and maps the vendor's words onto ``PublishState``.
"""

from __future__ import annotations

import json
import secrets
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
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

__all__ = [
    "API_KEY_VAR",
    "DEFAULT_API_URL",
    "PLATFORMS_VAR",
    "PROFILE_VAR",
    "YOUTUBE_PRIVACY_VAR",
    "UploadPostPublisher",
    "UploadPostSettings",
    "upload_post_settings_from_env",
]

DEFAULT_API_URL = "https://api.upload-post.com"

API_KEY_VAR = "OMEMO_UPLOAD_POST_API_KEY"
PROFILE_VAR = "OMEMO_UPLOAD_POST_PROFILE"
PLATFORMS_VAR = "OMEMO_UPLOAD_POST_PLATFORMS"
YOUTUBE_PRIVACY_VAR = "OMEMO_UPLOAD_POST_YOUTUBE_PRIVACY"

_PRIVACY = ("public", "unlisted", "private")
_CAPTION_IN_TITLE = ("tiktok", "instagram")
"""Platforms whose caption is the title field: they get the title and the description together."""

_PENDING = {"pending", "queued", "processing", "in_progress"}


@dataclass(frozen=True, slots=True)
class UploadPostSettings:
    """The account, the profile posted as, where to post, and how YouTube shows it."""

    api_key: str = field(repr=False)
    profile: str
    platforms: tuple[str, ...]
    youtube_privacy: str

    def __post_init__(self) -> None:
        if not self.api_key.strip() or not self.profile.strip():
            raise ValueError("upload-post settings need an API key and a profile")
        if not self.platforms or not all(name.strip() for name in self.platforms):
            raise ValueError("upload-post settings need at least one platform")
        if self.youtube_privacy not in _PRIVACY:
            raise ValueError(f"YouTube privacy must be one of {', '.join(_PRIVACY)}")


def upload_post_settings_from_env(environ: Mapping[str, str]) -> UploadPostSettings:
    """Read the configuration; anything missing fails closed, named, without its value."""
    names = (API_KEY_VAR, PROFILE_VAR, YOUTUBE_PRIVACY_VAR)
    values = {name: environ.get(name, "").strip() for name in names}
    missing = [name for name in names if not values[name]]
    if missing:
        raise ClipPublisherError("the clip publisher is not configured; set " + ", ".join(missing))
    privacy = values[YOUTUBE_PRIVACY_VAR].casefold()
    if privacy not in _PRIVACY:
        raise ClipPublisherError(f"{YOUTUBE_PRIVACY_VAR} must be one of {', '.join(_PRIVACY)}")
    raw_platforms = environ.get(PLATFORMS_VAR, "").strip() or "youtube"
    platforms = tuple(name.strip().casefold() for name in raw_platforms.split(",") if name.strip())
    if not platforms:
        raise ClipPublisherError(f"{PLATFORMS_VAR} names no platform")
    return UploadPostSettings(
        api_key=values[API_KEY_VAR],
        profile=values[PROFILE_VAR],
        platforms=platforms,
        youtube_privacy=privacy,
    )


class UploadPostPublisher:
    """Posts clips through upload-post and reads back how the posting went."""

    def __init__(
        self,
        settings: UploadPostSettings,
        *,
        api_url: str = DEFAULT_API_URL,
        timeout: float = 300.0,
    ) -> None:
        self._settings = settings
        self._api_url = api_url.rstrip("/")
        self._timeout = timeout

    @property
    def platforms(self) -> tuple[str, ...]:
        """Where this publisher posts — the platforms a request names."""
        return self._settings.platforms

    def submit(self, request: PublishRequest, /) -> None:
        """Upload the file as an async job under our ``request_id``; idempotent per that id."""
        video = Path(request.video_path)
        if not video.is_file():
            raise ClipPublisherError(f"the clip file is missing: {request.video_path}")
        post = request.post
        fields: list[tuple[str, str]] = [
            ("user", self._settings.profile),
            *(("platform[]", name) for name in request.platforms),
            ("title", post.title),
            ("description", post.description),
            ("youtube_description", post.description),
            ("privacyStatus", self._settings.youtube_privacy),
            ("selfDeclaredMadeForKids", "false"),
            ("async_upload", "true"),
            ("request_id", request.request_id),
        ]
        caption = f"{post.title}\n\n{post.description}"
        fields.extend(
            (f"{name}_title", caption) for name in request.platforms if name in _CAPTION_IN_TITLE
        )
        body, content_type = _multipart(fields, video)
        answer = self._request(
            "POST",
            "/api/upload",
            body=body,
            headers={"Content-Type": content_type, "Idempotency-Key": request.request_id},
        )
        if answer.get("success") is not True:
            raise ClipPublisherError(f"upload-post refused the upload: {_message(answer)}")

    def status(self, request_id: str, /) -> PublishStatus:
        """The job's state; ``NOT_FOUND`` when upload-post has never heard of ``request_id``."""
        if not request_id.strip():
            raise ClipPublisherError("a status needs a non-blank request id")
        query = urllib.parse.urlencode({"request_id": request_id})
        answer = self._request("GET", f"/api/uploadposts/status?{query}", not_found_is_answer=True)
        if answer.get("_not_found") is True:
            return PublishStatus(state=PublishState.NOT_FOUND)
        raw = answer.get("status")
        if not isinstance(raw, str):
            raise ClipPublisherError("upload-post returned a status without a state")
        word = raw.strip().casefold()
        if word == "not_found":
            return PublishStatus(state=PublishState.NOT_FOUND)
        if word in _PENDING:
            state = PublishState.PENDING
        elif word == "completed":
            state = PublishState.COMPLETED
        elif word == "failed":
            state = PublishState.FAILED
        else:
            raise ClipPublisherError(f"upload-post returned an unknown state {raw!r}")
        return PublishStatus(state=state, results=_results(answer.get("results")))

    # --- transport ----------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        not_found_is_answer: bool = False,
    ) -> dict[str, Any]:
        request = urllib.request.Request(
            self._api_url + path,
            data=body,
            method=method,
            headers={
                "Authorization": f"Apikey {self._settings.api_key}",
                "Accept": "application/json",
                **(headers or {}),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            detail = error.read()
            error.close()
            if not_found_is_answer and error.code == 404:
                return {"_not_found": True}
            raise ClipPublisherError(
                f"upload-post refused {method} {path.split('?')[0]}: HTTP {error.code} "
                f"{_message(_json_or_empty(detail))}"
            ) from error
        except (urllib.error.URLError, OSError) as error:
            raise ClipPublisherError(f"upload-post could not be reached: {error}") from error
        answer = _json_or_empty(raw)
        if not answer:
            raise ClipPublisherError("upload-post returned something that is not a JSON object")
        return answer


def _multipart(fields: list[tuple[str, str]], video: Path) -> tuple[bytes, str]:
    """A ``multipart/form-data`` body: the text fields, then the video file."""
    boundary = f"----omemo{secrets.token_hex(16)}"
    parts: list[bytes] = []
    for name, value in fields:
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
            + value.encode("utf-8")
            + b"\r\n"
        )
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="video"; '
        f'filename="{video.name}"\r\nContent-Type: video/mp4\r\n\r\n'.encode()
        + video.read_bytes()
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _results(raw: object) -> tuple[PlatformResult, ...]:
    """Per-platform results, from a list or a ``{platform: result}`` mapping."""
    items: list[tuple[str, object]] = []
    if isinstance(raw, list):
        items = [
            (str(entry.get("platform", "")), entry) for entry in raw if isinstance(entry, dict)
        ]
    elif isinstance(raw, dict):
        items = list(raw.items())
    results: list[PlatformResult] = []
    for platform, entry in items:
        if not platform or not isinstance(entry, dict):
            continue
        url = entry.get("url") or entry.get("post_url")
        message = entry.get("message") or entry.get("error")
        results.append(
            PlatformResult(
                platform=platform,
                success=entry.get("success") is True,
                url=url if isinstance(url, str) and url.strip() else None,
                message=message if isinstance(message, str) and message.strip() else None,
            )
        )
    return tuple(results)


def _json_or_empty(raw: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _message(answer: Mapping[str, Any]) -> str:
    for key in ("message", "error", "detail"):
        value = answer.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:300]
    return "no message"
