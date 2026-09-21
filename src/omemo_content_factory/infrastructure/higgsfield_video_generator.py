"""The ``VideoGenerator`` on Higgsfield's API, Kling 3.0 image-to-video (ADR-0066 §3, ADR-0067).

Through stdlib ``urllib`` (ADR-0067 §1). ``submit`` uploads both local frames through Higgsfield's
presigned upload (``POST /files/generate-upload-url`` then a ``PUT`` that carries **no** Higgsfield
credentials), and posts one generation with the photo as ``image_url`` and the generated frame as
``last_image_url``. It never retries: a submission has no idempotency key, so a repeat could pay for
a second video (ADR-0066 §3). ``collect`` is one ``GET /requests/{id}/status``; on ``completed`` it
downloads the video from the vendor's short-lived URL into the caller's destination and measures
the file it wrote. The URL is never returned, logged or put into a message.

Only a model known to take an ending frame is accepted (ADR-0066 §4) — checked 2026-09-21, that is
Kling 3.0 Standard, Pro and 4K; Turbo is not, since it takes no ``last_image_url``.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from omemo_content_factory.adapters.video_generator import (
    GeneratedVideo,
    VideoGenerationRequest,
    VideoGeneratorError,
    VideoJob,
    VideoJobResult,
    VideoJobState,
)
from omemo_content_factory.infrastructure.media_measure import (
    MediaMeasureError,
    measure_video,
    sniff_image_type,
)

__all__ = [
    "DEFAULT_API_URL",
    "END_FRAME_MODELS",
    "HiggsfieldVideoGenerator",
    "HiggsfieldVideoSettings",
    "higgsfield_video_settings_from_env",
]

DEFAULT_API_URL = "https://api.higgsfield.ai"
_UPLOAD_ROUTE = "/files/generate-upload-url"

KEY_ID_VAR = "OMEMO_HIGGSFIELD_API_KEY_ID"
KEY_SECRET_VAR = "OMEMO_HIGGSFIELD_API_KEY_SECRET"
MODEL_VAR = "OMEMO_HIGGSFIELD_VIDEO_MODEL"
SOUND_VAR = "OMEMO_HIGGSFIELD_SOUND"

END_FRAME_MODELS = frozenset(
    {
        "kling-video/v3.0/std/image-to-video",
        "kling-video/v3.0/pro/image-to-video",
        "kling-video/v3.0/4k/image-to-video",
    }
)
"""Endpoints whose documented schema takes ``last_image_url`` (checked 2026-09-21)."""

SOUND_VALUES = ("on", "off")
MIN_DURATION_S = 3
MAX_DURATION_S = 15
MAX_FRAME_BYTES = 15 * 1024 * 1024
MAX_VIDEO_BYTES = 300 * 1024 * 1024

_REQUEST_ID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_PENDING = {"queued", "in_progress"}
_FAILED = {"failed", "canceled"}


@dataclass(frozen=True, slots=True)
class HiggsfieldVideoSettings:
    """The key pair, the model endpoint and whether it makes sound — required, no defaults."""

    key_id: str = field(repr=False)
    key_secret: str = field(repr=False)
    model: str
    sound: str

    def __post_init__(self) -> None:
        for name in ("key_id", "key_secret", "model", "sound"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Higgsfield video settings need a non-blank {name}")
        if self.model not in END_FRAME_MODELS:
            raise ValueError("the Higgsfield model must be one that takes an ending frame")
        if self.sound not in SOUND_VALUES:
            raise ValueError("Higgsfield sound must be 'on' or 'off'")


def higgsfield_video_settings_from_env(environ: Mapping[str, str]) -> HiggsfieldVideoSettings:
    """Read the four required variables; a missing or wrong one fails closed, named."""
    names = (KEY_ID_VAR, KEY_SECRET_VAR, MODEL_VAR, SOUND_VAR)
    values = {name: environ.get(name, "").strip() for name in names}
    missing = [name for name in names if not values[name]]
    if missing:
        raise VideoGeneratorError(
            "Higgsfield video generation is not configured; set " + ", ".join(missing)
        )
    if values[MODEL_VAR] not in END_FRAME_MODELS:
        raise VideoGeneratorError(
            f"{MODEL_VAR} must be a model that takes an ending frame: "
            + ", ".join(sorted(END_FRAME_MODELS))
        )
    if values[SOUND_VAR] not in SOUND_VALUES:
        raise VideoGeneratorError(f"{SOUND_VAR} must be 'on' or 'off'")
    return HiggsfieldVideoSettings(
        key_id=values[KEY_ID_VAR],
        key_secret=values[KEY_SECRET_VAR],
        model=values[MODEL_VAR],
        sound=values[SOUND_VAR],
    )


class HiggsfieldVideoGenerator:
    """A ``VideoGenerator`` that asks Higgsfield to animate first frame → last frame."""

    def __init__(
        self,
        settings: HiggsfieldVideoSettings,
        *,
        api_url: str = DEFAULT_API_URL,
        timeout: float = 60.0,
        download_timeout: float = 300.0,
    ) -> None:
        self._settings = settings
        self._api_url = api_url.rstrip("/")
        self._timeout = timeout
        self._download_timeout = download_timeout

    # --- submit -------------------------------------------------------------------------

    def submit(self, request: VideoGenerationRequest, /) -> VideoJob:
        if not MIN_DURATION_S <= request.duration_s <= MAX_DURATION_S:
            raise VideoGeneratorError(
                f"the model makes {MIN_DURATION_S}-{MAX_DURATION_S} second videos, "
                f"not {request.duration_s}"
            )
        first = self._upload(request.first_frame, "first frame")
        last = self._upload(request.last_frame, "last frame")
        answer = self._call(
            "POST",
            "/" + self._settings.model,
            {
                "prompt": request.prompt,
                "image_url": first,
                "last_image_url": last,
                "duration": request.duration_s,
                "sound": self._settings.sound,
            },
            what="the generation",
        )
        request_id = answer.get("request_id")
        if not isinstance(request_id, str) or not _REQUEST_ID.match(request_id):
            raise VideoGeneratorError("Higgsfield accepted the generation without a request id")
        return VideoJob(job_id=request_id)

    def _upload(self, path: str, what: str) -> str:
        data = _read_frame(path, what)
        content_type = sniff_image_type(data)
        if content_type is None:
            raise VideoGeneratorError(f"the {what} is not a PNG, JPEG or WebP image")
        ticket = self._call(
            "POST", _UPLOAD_ROUTE, {"content_type": content_type}, what=f"the {what} upload"
        )
        upload_url = ticket.get("upload_url")
        public_url = ticket.get("public_url")
        headers = ticket.get("upload_headers", {})
        if (
            not isinstance(upload_url, str)
            or not isinstance(public_url, str)
            or not isinstance(headers, dict)
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items())
        ):
            raise VideoGeneratorError(f"Higgsfield's upload ticket for the {what} is malformed")
        _require_http(upload_url, f"the {what} upload")
        put = urllib.request.Request(
            upload_url, data=data, method="PUT", headers={"Content-Type": content_type, **headers}
        )
        try:
            with urllib.request.urlopen(put, timeout=self._timeout) as response:
                response.read()
        except urllib.error.HTTPError as exc:
            raise VideoGeneratorError(f"the {what} upload was refused: HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise VideoGeneratorError(f"the {what} could not be uploaded: {_reason(exc)}") from None
        return public_url

    # --- collect ------------------------------------------------------------------------

    def collect(self, job: VideoJob, destination: str, /) -> VideoJobResult:
        if not _REQUEST_ID.match(job.job_id):
            raise VideoGeneratorError("the job id is not a Higgsfield request id")
        answer = self._call(
            "GET",
            f"/requests/{urllib.parse.quote(job.job_id, safe='')}/status",
            None,
            what="the job status",
        )
        status = answer.get("status")
        if status in _PENDING:
            return VideoJobResult(state=VideoJobState.PENDING)
        if status == "nsfw":
            return VideoJobResult(state=VideoJobState.REJECTED)
        if status in _FAILED:
            return VideoJobResult(state=VideoJobState.FAILED)
        if status != "completed":
            raise VideoGeneratorError(f"Higgsfield reported an unknown status {status!r}")
        video = answer.get("video")
        url = video.get("url") if isinstance(video, dict) else None
        if not isinstance(url, str):
            raise VideoGeneratorError("Higgsfield completed the job without a video")
        data = self._download(url)
        try:
            measured = measure_video(data)
        except MediaMeasureError as exc:
            raise VideoGeneratorError(f"the downloaded video cannot be read: {exc}") from exc
        _write_atomically(Path(destination), data)
        return VideoJobResult(
            state=VideoJobState.COMPLETED,
            video=GeneratedVideo(
                path=destination,
                duration_ms=measured.duration_ms,
                width=measured.width,
                height=measured.height,
                container=measured.container,
            ),
        )

    def _download(self, url: str) -> bytes:
        _require_http(url, "the video download")
        try:
            with urllib.request.urlopen(url, timeout=self._download_timeout) as response:
                data: bytes = response.read(MAX_VIDEO_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise VideoGeneratorError(f"the video download was refused: HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise VideoGeneratorError(
                f"the video could not be downloaded: {_reason(exc)}"
            ) from None
        if len(data) > MAX_VIDEO_BYTES:
            raise VideoGeneratorError("the video is larger than this adapter accepts")
        return data

    # --- transport ----------------------------------------------------------------------

    def _call(
        self, method: str, route: str, body: Mapping[str, Any] | None, *, what: str
    ) -> Mapping[str, Any]:
        headers = {
            "Authorization": f"Key {self._settings.key_id}:{self._settings.key_secret}",
            "Accept": "application/json",
        }
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        http_request = urllib.request.Request(
            self._api_url + route, data=data, method=method, headers=headers
        )
        try:
            with urllib.request.urlopen(http_request, timeout=self._timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise VideoGeneratorError(
                f"Higgsfield refused {what}: HTTP {exc.code}{_status_meaning(exc.code)}"
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise VideoGeneratorError(
                f"Higgsfield could not be reached for {what}: {_reason(exc)}"
            ) from None
        try:
            answer = json.loads(raw)
        except ValueError:
            raise VideoGeneratorError(f"Higgsfield answered {what} with non-JSON") from None
        if not isinstance(answer, dict):
            raise VideoGeneratorError(f"Higgsfield answered {what} with an unexpected shape")
        return answer


_MEANINGS = {
    400: "invalid parameters, rejected input or concurrency reached",
    401: "missing or invalid credentials",
    403: "insufficient credits",
    404: "not found for this account",
    422: "request body failed validation",
    423: "the model is temporarily blocked",
    503: "the model is disabled or not ready",
}


def _status_meaning(code: int) -> str:
    meaning = _MEANINGS.get(code)
    return f" ({meaning})" if meaning else ""


def _read_frame(path: str, what: str) -> bytes:
    file = Path(path)
    try:
        if file.stat().st_size > MAX_FRAME_BYTES:
            raise VideoGeneratorError(
                f"the {what} is larger than {MAX_FRAME_BYTES // (1024 * 1024)} MB"
            )
        return file.read_bytes()
    except OSError:
        raise VideoGeneratorError(f"the {what} does not exist or cannot be read") from None


def _require_http(url: str, what: str) -> None:
    if urllib.parse.urlsplit(url).scheme not in ("https", "http"):
        raise VideoGeneratorError(f"Higgsfield gave {what} a URL that is not HTTP(S)")


def _write_atomically(destination: Path, data: bytes) -> None:
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=destination.parent, prefix=".partial-")
        try:
            with os.fdopen(handle, "wb") as out:
                out.write(data)
            os.replace(temporary, destination)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
    except OSError as exc:
        raise VideoGeneratorError(f"the video could not be written: {exc.strerror}") from None


def _reason(exc: BaseException) -> str:
    reason = getattr(exc, "reason", None)
    return str(reason if reason is not None else exc.__class__.__name__)
