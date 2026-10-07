"""The ``ShotAnimator`` on BytePlus Seedance, through stdlib ``urllib`` (ADR-0084, ADR-0090).

``submit`` makes **one** ``POST /api/v3/contents/generations/tasks`` — the picture inline as a
base64 data URI with the role ``first_frame`` (so nothing is hosted), the prompt and the clip's
settings as the flags Seedance reads at the end of the text — and returns the task id. It is never
retried: a network failure there may have been an accepted job, and a second ``POST`` is a second
paid clip. ``collect`` makes one status ``GET`` and, on success, downloads the video, measures it
and writes it atomically; network failures there are asked again.

Audio is not requested: Seedance 1.0 pro makes none (measured), and the story's voice is a separate
track. The result's size and length are read back from the MP4 itself.
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from omemo_content_factory.adapters.shot_animator import (
    AnimationRequest,
    GeneratedVideo,
    VideoGeneratorError,
    VideoJob,
    VideoJobResult,
    VideoJobState,
)
from omemo_content_factory.infrastructure.media_measure import (
    MediaMeasureError,
    measure_image,
    measure_video,
)

__all__ = [
    "API_KEY_VAR",
    "DEFAULT_API_URL",
    "MODEL_VAR",
    "RESOLUTION_VAR",
    "SeedanceSettings",
    "SeedanceShotAnimator",
    "seedance_settings_from_env",
]

API_KEY_VAR = "OMEMO_BYTEPLUS_API_KEY"
MODEL_VAR = "OMEMO_SEEDANCE_VIDEO_MODEL"
RESOLUTION_VAR = "OMEMO_SEEDANCE_RESOLUTION"

DEFAULT_API_URL = "https://ark.ap-southeast.bytepluses.com"
_ROUTE = "/api/v3/contents/generations/tasks"
_USER_AGENT = "concept-content-factory/1.0 (+api-client)"
_RESOLUTIONS = ("480p", "720p", "1080p")
_MIN_SECONDS, _MAX_SECONDS = 2, 12
_MAX_FRAME_BYTES = 10 * 1024 * 1024
_RUNNING = frozenset({"queued", "running"})
_FAILED = frozenset({"failed", "cancelled", "expired"})


@dataclass(frozen=True, slots=True)
class SeedanceSettings:
    """Key, model and resolution — all required, none defaulted (ADR-0040)."""

    api_key: str = field(repr=False)
    model: str
    resolution: str

    def __post_init__(self) -> None:
        for name in ("api_key", "model", "resolution"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Seedance settings need a non-blank {name}")
        if not self.model.startswith("seedance-"):
            raise ValueError("the Seedance model id must start with 'seedance-'")
        if self.resolution not in _RESOLUTIONS:
            raise ValueError(f"Seedance resolution must be one of {', '.join(_RESOLUTIONS)}")


def seedance_settings_from_env(environ: Mapping[str, str]) -> SeedanceSettings:
    """Read the three required variables; a missing one fails closed, named, never echoed."""
    names = (API_KEY_VAR, MODEL_VAR, RESOLUTION_VAR)
    values = {name: environ.get(name, "").strip() for name in names}
    missing = [name for name in names if not values[name]]
    if missing:
        raise VideoGeneratorError("Seedance is not configured; set " + ", ".join(missing))
    try:
        return SeedanceSettings(values[API_KEY_VAR], values[MODEL_VAR], values[RESOLUTION_VAR])
    except ValueError as error:
        raise VideoGeneratorError(str(error)) from None


class SeedanceShotAnimator:
    """A ``ShotAnimator`` that asks Seedance to animate a picture."""

    def __init__(
        self,
        settings: SeedanceSettings,
        *,
        api_url: str = DEFAULT_API_URL,
        timeout: float = 120.0,
        retries: int = 2,
        retry_delay: float = 2.0,
    ) -> None:
        self._settings = settings
        self._api_url = api_url.rstrip("/")
        self._timeout = timeout
        self._retries = retries
        self._retry_delay = retry_delay

    def submit(self, request: AnimationRequest, /) -> VideoJob:
        """Start the job. One request, never asked again."""
        if not _MIN_SECONDS <= request.duration_s <= _MAX_SECONDS:
            raise VideoGeneratorError(
                f"Seedance clips last {_MIN_SECONDS}–{_MAX_SECONDS} s, not {request.duration_s} s"
            )
        flags = (
            f"--resolution {self._settings.resolution} --duration {request.duration_s} "
            f"--ratio {request.ratio} --watermark false --camerafixed false"
        )
        body = {
            "model": self._settings.model,
            "content": [
                {"type": "text", "text": f"{request.prompt.strip()} {flags}"},
                {
                    "type": "image_url",
                    "image_url": {"url": _data_uri(request.frame)},
                    "role": "first_frame",
                },
            ],
        }
        http_request = urllib.request.Request(
            self._api_url + _ROUTE,
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers=self._headers(),
        )
        try:
            with urllib.request.urlopen(http_request, timeout=self._timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise VideoGeneratorError(
                f"Seedance refused the job: HTTP {exc.code}{_detail(exc)}"
            ) from None
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as exc:
            raise VideoGeneratorError(
                f"Seedance could not be reached while submitting ({_reason(exc)}); the job may "
                "or may not have been accepted and is NOT submitted again"
            ) from None
        job_id = _object(raw, "Seedance").get("id")
        if not isinstance(job_id, str) or not job_id.strip():
            raise VideoGeneratorError("Seedance accepted the job but returned no id")
        return VideoJob(job_id)

    def collect(self, job: VideoJob, destination: str, /) -> VideoJobResult:
        """Ask once; on success download, measure and write the file."""
        url = f"{self._api_url}{_ROUTE}/{urllib.parse.quote(job.job_id, safe='')}"
        status = _object(
            self._send(urllib.request.Request(url, headers=self._headers()), "Seedance"), "Seedance"
        )
        state = status.get("status")
        if state in _RUNNING:
            return VideoJobResult(VideoJobState.PENDING)
        if state in _FAILED:
            return VideoJobResult(_failure(status))
        if state != "succeeded":
            raise VideoGeneratorError(f"Seedance reported an unknown status {state!r}")
        content = status.get("content")
        video_url = content.get("video_url") if isinstance(content, dict) else None
        if not isinstance(video_url, str) or not video_url.startswith(("http://", "https://")):
            raise VideoGeneratorError("Seedance finished the job but returned no video")
        data = self._send(
            urllib.request.Request(video_url, headers={"User-Agent": _USER_AGENT}), "the video"
        )
        try:
            measured = measure_video(data)
        except MediaMeasureError as exc:
            raise VideoGeneratorError(f"Seedance's answer is not a video: {exc}") from exc
        _write_atomically(Path(destination), data)
        return VideoJobResult(
            VideoJobState.COMPLETED,
            GeneratedVideo(
                path=destination,
                duration_ms=measured.duration_ms,
                width=measured.width,
                height=measured.height,
                container=measured.container,
            ),
        )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._settings.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": _USER_AGENT,
        }

    def _send(self, http_request: urllib.request.Request, what: str) -> bytes:
        """A read that is safe to repeat: asked again on a network drop, never on a refusal."""
        last: BaseException | None = None
        for attempt in range(self._retries + 1):
            try:
                with urllib.request.urlopen(http_request, timeout=self._timeout) as response:
                    return bytes(response.read())
            except urllib.error.HTTPError as exc:
                raise VideoGeneratorError(
                    f"{what} refused the request: HTTP {exc.code}{_detail(exc)}"
                ) from None
            except (
                urllib.error.URLError,
                http.client.HTTPException,
                TimeoutError,
                OSError,
            ) as exc:
                last = exc
                if attempt < self._retries:
                    time.sleep(self._retry_delay)
        raise VideoGeneratorError(f"{what} could not be reached: {_reason(last)}")


def _failure(status: Mapping[str, Any]) -> VideoJobState:
    error = status.get("error")
    code = str(error.get("code", "")) if isinstance(error, dict) else ""
    return VideoJobState.REJECTED if "sensitive" in code.lower() else VideoJobState.FAILED


def _data_uri(path: str) -> str:
    file = Path(path)
    try:
        if file.stat().st_size > _MAX_FRAME_BYTES:
            raise VideoGeneratorError(f"the frame {file.name} is larger than 10 MB")
        data = file.read_bytes()
    except OSError:
        raise VideoGeneratorError(f"the frame {file.name} cannot be read") from None
    try:
        media_type = measure_image(data).media_type
    except MediaMeasureError as exc:
        raise VideoGeneratorError(f"the frame {file.name} is not an image: {exc}") from exc
    return f"data:{media_type};base64,{base64.b64encode(data).decode('ascii')}"


def _object(raw: bytes, who: str) -> Mapping[str, Any]:
    try:
        answer = json.loads(raw)
    except ValueError:
        raise VideoGeneratorError(f"{who} answered with something that is not JSON") from None
    if not isinstance(answer, dict):
        raise VideoGeneratorError(f"{who} answered with an unexpected shape")
    return answer


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


def _detail(error: urllib.error.HTTPError) -> str:
    try:
        message = json.loads(error.read())["error"]["message"]
    except (ValueError, KeyError, TypeError, OSError):
        return ""
    return f" ({str(message)[:300]})" if message else ""


def _reason(exc: BaseException | None) -> str:
    reason = getattr(exc, "reason", None)
    if reason is not None:
        return str(reason)
    return exc.__class__.__name__ if exc else "unknown"
