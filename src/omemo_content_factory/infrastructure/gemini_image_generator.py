"""The ``ImageGenerator`` on Google Gemini's image models — "Nano Banana" (ADR-0066 §2, ADR-0067).

One synchronous ``POST /v1beta/interactions`` per frame, through stdlib ``urllib`` (ADR-0067 §1):
the reference photo goes inline as base64 beside the prompt, the answer carries the generated image
inline, and nothing is stored at Google (``store: false``) — the photo is the operator's material.

The output keeps the photo's aspect ratio: Gemini takes a ratio from a closed list, so the nearest
listed one is asked for, and an answer whose own measured ratio is off it is a failure, not a frame
to animate (ADR-0067 §3). The model, the size and the key are configuration with no defaults
(ADR-0040); no Gemini shape leaves this module.
"""

from __future__ import annotations

import base64
import binascii
import json
import math
import os
import tempfile
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from omemo_content_factory.adapters.image_generator import (
    GeneratedImage,
    ImageGenerationRequest,
    ImageGeneratorError,
)
from omemo_content_factory.infrastructure.media_measure import (
    MediaMeasureError,
    measure_image,
)

__all__ = [
    "ASPECT_RATIOS",
    "DEFAULT_API_URL",
    "IMAGE_SIZES",
    "GeminiImageGenerator",
    "GeminiImageSettings",
    "gemini_image_settings_from_env",
    "nearest_aspect_ratio",
]

DEFAULT_API_URL = "https://generativelanguage.googleapis.com"
_ROUTE = "/v1beta/interactions"

API_KEY_VAR = "OMEMO_GEMINI_API_KEY"
MODEL_VAR = "OMEMO_GEMINI_IMAGE_MODEL"
SIZE_VAR = "OMEMO_GEMINI_IMAGE_SIZE"

IMAGE_SIZES = ("512", "1K", "2K", "4K")
"""Gemini's ``image_size`` values, checked 2026-09-21; the ``K`` must be upper case."""

ASPECT_RATIOS: Mapping[str, float] = {
    ratio: int(ratio.split(":")[0]) / int(ratio.split(":")[1])
    for ratio in ("1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9")
}
"""The listed ratios a photo can be matched to (the extreme 1:8/8:1/1:4/4:1 are left out)."""

RATIO_TOLERANCE = 0.03
"""How far the answer's measured ratio may sit from the one asked for (rounding to pixels)."""

MAX_REFERENCE_BYTES = 15 * 1024 * 1024
"""Inline payloads are bounded; a larger photo needs the Files API, which v1 does not use."""


@dataclass(frozen=True, slots=True)
class GeminiImageSettings:
    """The key, the model and the output size — all required, none defaulted (ADR-0040)."""

    api_key: str = field(repr=False)
    model: str
    image_size: str

    def __post_init__(self) -> None:
        for name in ("api_key", "model", "image_size"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Gemini image settings need a non-blank {name}")
        if self.image_size not in IMAGE_SIZES:
            raise ValueError(f"Gemini image size must be one of {', '.join(IMAGE_SIZES)}")


def gemini_image_settings_from_env(environ: Mapping[str, str]) -> GeminiImageSettings:
    """Read the three required variables; a missing one fails closed, named, never echoed."""
    names = (API_KEY_VAR, MODEL_VAR, SIZE_VAR)
    values = {name: environ.get(name, "").strip() for name in names}
    missing = [name for name in names if not values[name]]
    if missing:
        raise ImageGeneratorError(
            "Gemini image generation is not configured; set " + ", ".join(missing)
        )
    if values[SIZE_VAR] not in IMAGE_SIZES:
        raise ImageGeneratorError(f"{SIZE_VAR} must be one of {', '.join(IMAGE_SIZES)}")
    return GeminiImageSettings(
        api_key=values[API_KEY_VAR], model=values[MODEL_VAR], image_size=values[SIZE_VAR]
    )


def nearest_aspect_ratio(width: int, height: int) -> str:
    """The listed ratio closest to ``width:height``, compared on a log scale (so 2:1 ≈ 1:2)."""
    target = math.log(width / height)
    return min(ASPECT_RATIOS, key=lambda name: abs(math.log(ASPECT_RATIOS[name]) - target))


class GeminiImageGenerator:
    """An ``ImageGenerator`` that asks a Gemini image model for the frame."""

    def __init__(
        self,
        settings: GeminiImageSettings,
        *,
        api_url: str = DEFAULT_API_URL,
        timeout: float = 180.0,
    ) -> None:
        self._settings = settings
        self._api_url = api_url.rstrip("/")
        self._timeout = timeout

    def generate(self, request: ImageGenerationRequest, /) -> GeneratedImage:
        photo = _read_reference(request.reference)
        try:
            measured = measure_image(photo)
        except MediaMeasureError as exc:
            raise ImageGeneratorError(f"the reference photo cannot be read: {exc}") from exc
        ratio = nearest_aspect_ratio(measured.width, measured.height)

        answer = self._post(
            {
                "model": self._settings.model,
                "input": [
                    {"type": "text", "text": request.prompt},
                    {
                        "type": "image",
                        "mime_type": measured.media_type,
                        "data": base64.b64encode(photo).decode("ascii"),
                    },
                ],
                "response_format": {
                    "type": "image",
                    "aspect_ratio": ratio,
                    "image_size": self._settings.image_size,
                },
                "store": False,
            }
        )
        image = _image_from(answer)
        try:
            produced = measure_image(image)
        except MediaMeasureError as exc:
            raise ImageGeneratorError(f"the generated image cannot be read: {exc}") from exc
        wanted = ASPECT_RATIOS[ratio]
        got = produced.width / produced.height
        if abs(got - wanted) / wanted > RATIO_TOLERANCE:
            raise ImageGeneratorError(
                f"the generated image is {produced.width}x{produced.height}, "
                f"not the {ratio} that was asked for"
            )
        _write_atomically(Path(request.destination), image)
        return GeneratedImage(
            path=request.destination,
            width=produced.width,
            height=produced.height,
            media_type=produced.media_type,
        )

    def _post(self, body: Mapping[str, Any]) -> Mapping[str, Any]:
        http_request = urllib.request.Request(
            self._api_url + _ROUTE,
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={
                "x-goog-api-key": self._settings.api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(http_request, timeout=self._timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise ImageGeneratorError(
                f"Gemini refused the request: HTTP {exc.code}{_google_detail(exc)}"
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ImageGeneratorError(f"Gemini could not be reached: {_reason(exc)}") from None
        try:
            answer = json.loads(raw)
        except ValueError:
            raise ImageGeneratorError("Gemini answered with something that is not JSON") from None
        if not isinstance(answer, dict):
            raise ImageGeneratorError("Gemini answered with an unexpected shape")
        return answer


def _read_reference(path: str) -> bytes:
    file = Path(path)
    try:
        size = file.stat().st_size
    except OSError:
        raise ImageGeneratorError("the reference photo does not exist or cannot be read") from None
    if size > MAX_REFERENCE_BYTES:
        raise ImageGeneratorError(
            f"the reference photo is larger than {MAX_REFERENCE_BYTES // (1024 * 1024)} MB"
        )
    try:
        return file.read_bytes()
    except OSError:
        raise ImageGeneratorError("the reference photo cannot be read") from None


def _image_from(answer: Mapping[str, Any]) -> bytes:
    """The generated image's bytes; a finished answer that holds none is a failure."""
    status = answer.get("status")
    if status != "completed":
        raise ImageGeneratorError(f"Gemini did not complete the image (status {status!r})")
    texts: list[str] = []
    steps = answer.get("steps")
    for step in steps if isinstance(steps, list) else []:
        if not isinstance(step, dict) or step.get("type") != "model_output":
            continue
        content = step.get("content")
        for item in content if isinstance(content, list) else []:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "image" and isinstance(item.get("data"), str):
                try:
                    return base64.b64decode(item["data"], validate=True)
                except (binascii.Error, ValueError):
                    raise ImageGeneratorError("Gemini's image is not valid base64") from None
            if item.get("type") == "text" and isinstance(item.get("text"), str):
                texts.append(item["text"])
    said = " ".join(texts).strip()
    raise ImageGeneratorError(
        "Gemini returned no image" + (f"; it said: {said[:300]}" if said else "")
    )


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
        raise ImageGeneratorError(f"the image could not be written: {exc.strerror}") from None


def _google_detail(error: urllib.error.HTTPError) -> str:
    try:
        body = json.loads(error.read())
        message = body["error"]["message"]
    except (ValueError, KeyError, TypeError, OSError):
        return ""
    return f" ({str(message)[:300]})" if message else ""


def _reason(exc: BaseException) -> str:
    reason = getattr(exc, "reason", None)
    return str(reason if reason is not None else exc.__class__.__name__)
