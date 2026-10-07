"""The ``FrameGenerator`` on BytePlus Seedream, through stdlib ``urllib`` (ADR-0084, ADR-0088).

One synchronous ``POST /api/v3/images/generations`` per picture. The prompt, the size as an
explicit ``WIDTHxHEIGHT`` (checked live: the answer is exactly that size, no crop or padding) and
any reference pictures — inline as base64 data URIs, so no file is ever hosted — go in; the answer
names a short-lived URL that is downloaded at once and never stored or returned (``watermark`` is
off, which is what removes the "AI generated" mark, checked live too).

The measurements are read back from the bytes that came back, and an answer whose ratio is off the
ratio asked for is a failure, not a frame to animate (ADR-0067 §3's rule). A network failure is
asked again, twice; a refusal from the vendor is final.
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from omemo_content_factory.adapters.frame_generator import FrameRequest
from omemo_content_factory.adapters.image_generator import GeneratedImage, ImageGeneratorError
from omemo_content_factory.infrastructure.media_measure import MediaMeasureError, measure_image

__all__ = [
    "API_KEY_VAR",
    "DEFAULT_API_URL",
    "MODEL_VAR",
    "SeedreamFrameGenerator",
    "SeedreamSettings",
    "seedream_settings_from_env",
]

API_KEY_VAR = "OMEMO_BYTEPLUS_API_KEY"
MODEL_VAR = "OMEMO_SEEDREAM_IMAGE_MODEL"

DEFAULT_API_URL = "https://ark.ap-southeast.bytepluses.com"
_ROUTE = "/api/v3/images/generations"
_USER_AGENT = "concept-content-factory/1.0 (+api-client)"
_RATIO_TOLERANCE = 0.03
_MAX_REFERENCE_BYTES = 10 * 1024 * 1024
_MAX_REFERENCES = 10
_MODEL_PREFIX = "seedream-"


@dataclass(frozen=True, slots=True)
class SeedreamSettings:
    """The key and the model — both required, neither defaulted (ADR-0040)."""

    api_key: str = field(repr=False)
    model: str

    def __post_init__(self) -> None:
        for name in ("api_key", "model"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Seedream settings need a non-blank {name}")
        if not self.model.startswith(_MODEL_PREFIX):
            raise ValueError(f"the Seedream model id must start with {_MODEL_PREFIX!r}")


def seedream_settings_from_env(environ: Mapping[str, str]) -> SeedreamSettings:
    """Read the two required variables; a missing one fails closed, named, never echoed."""
    names = (API_KEY_VAR, MODEL_VAR)
    values = {name: environ.get(name, "").strip() for name in names}
    missing = [name for name in names if not values[name]]
    if missing:
        raise ImageGeneratorError("Seedream is not configured; set " + ", ".join(missing))
    try:
        return SeedreamSettings(api_key=values[API_KEY_VAR], model=values[MODEL_VAR])
    except ValueError as error:
        raise ImageGeneratorError(str(error)) from None


class SeedreamFrameGenerator:
    """A ``FrameGenerator`` that asks a Seedream model for the picture."""

    def __init__(
        self,
        settings: SeedreamSettings,
        *,
        api_url: str = DEFAULT_API_URL,
        timeout: float = 240.0,
        retries: int = 2,
        retry_delay: float = 2.0,
    ) -> None:
        self._settings = settings
        self._api_url = api_url.rstrip("/")
        self._timeout = timeout
        self._retries = retries
        self._retry_delay = retry_delay

    def generate(self, request: FrameRequest, /) -> GeneratedImage:
        """Write the picture to ``request.destination`` and report what was written."""
        if len(request.references) > _MAX_REFERENCES:
            raise ImageGeneratorError(f"at most {_MAX_REFERENCES} reference pictures are allowed")
        body: dict[str, Any] = {
            "model": self._settings.model,
            "prompt": request.prompt,
            "size": f"{request.width}x{request.height}",
            "response_format": "url",
            "watermark": False,
        }
        if request.references:
            body["image"] = [_data_uri(path) for path in request.references]
        answer = self._json(
            urllib.request.Request(
                self._api_url + _ROUTE,
                data=json.dumps(body).encode("utf-8"),
                method="POST",
                headers={
                    "Authorization": f"Bearer {self._settings.api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": _USER_AGENT,
                },
            )
        )
        url = _image_url(answer)
        data = self._send(urllib.request.Request(url, headers={"User-Agent": _USER_AGENT}), "image")
        try:
            measured = measure_image(data)
        except MediaMeasureError as exc:
            raise ImageGeneratorError(f"Seedream's answer is not an image: {exc}") from exc
        wanted = request.width / request.height
        if abs(measured.width / measured.height - wanted) > wanted * _RATIO_TOLERANCE:
            raise ImageGeneratorError(
                f"Seedream answered {measured.width}x{measured.height}, "
                f"not the {request.width}x{request.height} asked for"
            )
        _write_atomically(Path(request.destination), data)
        return GeneratedImage(
            path=request.destination,
            width=measured.width,
            height=measured.height,
            media_type=measured.media_type,
        )

    def _json(self, http_request: urllib.request.Request) -> Mapping[str, Any]:
        raw = self._send(http_request, "Seedream")
        try:
            answer = json.loads(raw)
        except ValueError:
            raise ImageGeneratorError("Seedream answered with something that is not JSON") from None
        if not isinstance(answer, dict):
            raise ImageGeneratorError("Seedream answered with an unexpected shape")
        return answer

    def _send(self, http_request: urllib.request.Request, what: str) -> bytes:
        """One request, asked again only when the network failed — never when the vendor refused."""
        last: BaseException | None = None
        for attempt in range(self._retries + 1):
            try:
                with urllib.request.urlopen(http_request, timeout=self._timeout) as response:
                    return bytes(response.read())
            except urllib.error.HTTPError as exc:
                raise ImageGeneratorError(
                    f"{what} refused the request: HTTP {exc.code}{_detail(exc)}"
                ) from None
            except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as exc:
                last = exc
                if attempt < self._retries:
                    time.sleep(self._retry_delay)
        raise ImageGeneratorError(f"{what} could not be reached: {_reason(last)}")


def _data_uri(path: str) -> str:
    file = Path(path)
    try:
        if file.stat().st_size > _MAX_REFERENCE_BYTES:
            raise ImageGeneratorError(f"the reference {file.name} is larger than 10 MB")
        data = file.read_bytes()
    except OSError:
        raise ImageGeneratorError(f"the reference {file.name} cannot be read") from None
    try:
        media_type = measure_image(data).media_type
    except MediaMeasureError as exc:
        raise ImageGeneratorError(f"the reference {file.name} is not an image: {exc}") from exc
    return f"data:{media_type};base64,{base64.b64encode(data).decode('ascii')}"


def _image_url(answer: Mapping[str, Any]) -> str:
    items = answer.get("data")
    first = items[0] if isinstance(items, list) and items else None
    url = first.get("url") if isinstance(first, dict) else None
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        error = answer.get("error")
        message = error.get("message") if isinstance(error, dict) else None
        raise ImageGeneratorError(
            "Seedream returned no image" + (f"; it said: {str(message)[:300]}" if message else "")
        )
    return url


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


def _detail(error: urllib.error.HTTPError) -> str:
    try:
        message = json.loads(error.read())["error"]["message"]
    except (ValueError, KeyError, TypeError, OSError):
        return ""
    return f" ({str(message)[:300]})" if message else ""


def _reason(exc: BaseException | None) -> str:
    reason = getattr(exc, "reason", None)
    return str(reason if reason is not None else exc.__class__.__name__ if exc else "unknown")
