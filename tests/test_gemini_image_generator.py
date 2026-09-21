"""Tests for ``GeminiImageGenerator`` (GENERATION_ACCEPTANCE §4, GIG; ADR-0066 §2, ADR-0067).

The adapter's real ``urllib`` code talks over a real socket to a local server that plays Gemini's
``/v1beta/interactions``: it records each request as it arrived and answers with a scripted
interaction whose image is a real PNG. Nothing is mocked below the adapter and nothing leaves
``127.0.0.1``. The live call against Google is ``test_live_generation.py``, opt-in.
"""

from __future__ import annotations

import base64
import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from omemo_content_factory.adapters.image_generator import (
    ImageGenerationRequest,
    ImageGenerator,
    ImageGeneratorError,
)
from omemo_content_factory.infrastructure.gemini_image_generator import (
    GeminiImageGenerator,
    GeminiImageSettings,
    gemini_image_settings_from_env,
    nearest_aspect_ratio,
)
from tests.media_fixtures import jpeg, png

KEY = "gemini-secret-key-value"
SETTINGS = GeminiImageSettings(api_key=KEY, model="gemini-3.1-flash-image", image_size="2K")


def _completed(image: bytes, *, text: str = "Here is the finished bunker.") -> dict[str, Any]:
    return {
        "id": "v1_abc",
        "object": "interaction",
        "status": "completed",
        "steps": [
            {"type": "thought", "signature": "sig"},
            {
                "type": "model_output",
                "content": [
                    {"type": "text", "text": text},
                    {
                        "type": "image",
                        "mime_type": "image/png",
                        "data": base64.b64encode(image).decode("ascii"),
                    },
                ],
            },
        ],
    }


@dataclass
class FakeGemini:
    answer: tuple[int, Any] = (200, _completed(png(9, 16)))
    requests: list[tuple[str, dict[str, str], Any]] = field(default_factory=list)


@pytest.fixture
def gemini() -> Iterator[tuple[FakeGemini, str]]:
    fake = FakeGemini()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length))
            fake.requests.append((self.path, {k.lower(): v for k, v in self.headers.items()}, body))
            code, payload = fake.answer
            raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    try:
        yield fake, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _photo(tmp_path: Path, data: bytes = b"") -> str:
    path = tmp_path / "bunker.jpg"
    path.write_bytes(data or jpeg(1080, 1920))
    return str(path)


def _request(tmp_path: Path, **overrides: str) -> ImageGenerationRequest:
    values = {
        "reference": _photo(tmp_path),
        "prompt": "The same bunker, finished and camouflaged",
        "destination": str(tmp_path / "out" / "frame.png"),
    }
    values.update(overrides)
    return ImageGenerationRequest(**values)


def test_gig_01_a_frame_is_generated_written_and_measured(
    gemini: tuple[FakeGemini, str], tmp_path: Path
) -> None:
    fake, url = gemini
    generator: ImageGenerator = GeminiImageGenerator(SETTINGS, api_url=url)
    request = _request(tmp_path)

    image = generator.generate(request)

    assert (image.path, image.width, image.height, image.media_type) == (
        request.destination,
        9,
        16,
        "image/png",
    )
    assert Path(request.destination).read_bytes() == png(9, 16)
    [(path, headers, body)] = fake.requests
    assert path == "/v1beta/interactions"
    assert headers["x-goog-api-key"] == KEY
    assert body["model"] == "gemini-3.1-flash-image"
    assert body["store"] is False
    assert body["response_format"] == {"type": "image", "aspect_ratio": "9:16", "image_size": "2K"}
    text, photo = body["input"]
    assert text == {"type": "text", "text": request.prompt}
    assert photo["type"] == "image" and photo["mime_type"] == "image/jpeg"
    assert base64.b64decode(photo["data"]) == jpeg(1080, 1920)


@pytest.mark.parametrize(
    ("size", "ratio"),
    [
        ((1080, 1920), "9:16"),
        ((1920, 1080), "16:9"),
        ((1000, 1000), "1:1"),
        ((3024, 4032), "3:4"),
        ((4000, 3000), "4:3"),
        ((2560, 1080), "21:9"),
    ],
)
def test_gig_02_the_photo_is_matched_to_the_nearest_listed_ratio(
    size: tuple[int, int], ratio: str
) -> None:
    assert nearest_aspect_ratio(*size) == ratio


def test_gig_03_an_answer_off_the_asked_ratio_is_a_failure_and_writes_nothing(
    gemini: tuple[FakeGemini, str], tmp_path: Path
) -> None:
    fake, url = gemini
    fake.answer = (200, _completed(png(16, 9)))
    request = _request(tmp_path)
    with pytest.raises(ImageGeneratorError, match="not the 9:16"):
        GeminiImageGenerator(SETTINGS, api_url=url).generate(request)
    assert not Path(request.destination).exists()


def test_gig_04_an_answer_without_an_image_says_what_the_model_said(
    gemini: tuple[FakeGemini, str], tmp_path: Path
) -> None:
    fake, url = gemini
    answer = _completed(png(9, 16), text="I can't make that image.")
    answer["steps"][1]["content"] = answer["steps"][1]["content"][:1]
    fake.answer = (200, answer)
    with pytest.raises(ImageGeneratorError, match="no image; it said: I can't make that image"):
        GeminiImageGenerator(SETTINGS, api_url=url).generate(_request(tmp_path))


@pytest.mark.parametrize(
    "answer",
    [
        (200, {"status": "failed", "steps": []}),
        (200, b"<html>not json</html>"),
        (200, [1, 2]),
        (
            200,
            {
                "status": "completed",
                "steps": [
                    {
                        "type": "model_output",
                        "content": [{"type": "image", "data": "@@not base64@@"}],
                    }
                ],
            },
        ),
        (200, _completed(b"not an image")),
    ],
)
def test_gig_05_a_malformed_or_unfinished_answer_is_an_error(
    gemini: tuple[FakeGemini, str], tmp_path: Path, answer: tuple[int, Any]
) -> None:
    fake, url = gemini
    fake.answer = answer
    with pytest.raises(ImageGeneratorError):
        GeminiImageGenerator(SETTINGS, api_url=url).generate(_request(tmp_path))


def test_gig_06_a_refusal_names_the_status_and_googles_message_but_never_the_key(
    gemini: tuple[FakeGemini, str], tmp_path: Path
) -> None:
    fake, url = gemini
    fake.answer = (400, {"error": {"code": 400, "message": "API key not valid."}})
    with pytest.raises(ImageGeneratorError) as caught:
        GeminiImageGenerator(SETTINGS, api_url=url).generate(_request(tmp_path))
    assert "HTTP 400" in str(caught.value) and "API key not valid" in str(caught.value)
    assert KEY not in str(caught.value)


def test_gig_07_an_unreachable_service_is_an_error(tmp_path: Path) -> None:
    generator = GeminiImageGenerator(SETTINGS, api_url="http://127.0.0.1:9", timeout=2)
    with pytest.raises(ImageGeneratorError, match="could not be reached"):
        generator.generate(_request(tmp_path))


def test_gig_08_a_missing_or_unreadable_photo_fails_before_any_call(
    gemini: tuple[FakeGemini, str], tmp_path: Path
) -> None:
    fake, url = gemini
    generator = GeminiImageGenerator(SETTINGS, api_url=url)
    with pytest.raises(ImageGeneratorError, match="does not exist"):
        generator.generate(_request(tmp_path, reference=str(tmp_path / "missing.jpg")))
    not_a_photo = tmp_path / "notes.txt"
    not_a_photo.write_text("hello")
    with pytest.raises(ImageGeneratorError, match="cannot be read"):
        generator.generate(_request(tmp_path, reference=str(not_a_photo)))
    assert fake.requests == []


ENV = {
    "OMEMO_GEMINI_API_KEY": KEY,
    "OMEMO_GEMINI_IMAGE_MODEL": "gemini-3.1-flash-image",
    "OMEMO_GEMINI_IMAGE_SIZE": "2K",
}


def test_gig_09_settings_come_from_three_required_variables() -> None:
    settings = gemini_image_settings_from_env(ENV)
    assert (settings.model, settings.image_size) == ("gemini-3.1-flash-image", "2K")
    assert KEY not in repr(settings)
    with pytest.raises(ImageGeneratorError) as caught:
        gemini_image_settings_from_env({"OMEMO_GEMINI_API_KEY": KEY})
    assert "OMEMO_GEMINI_IMAGE_MODEL" in str(caught.value)
    assert "OMEMO_GEMINI_IMAGE_SIZE" in str(caught.value)
    assert KEY not in str(caught.value)
    with pytest.raises(ImageGeneratorError, match="OMEMO_GEMINI_IMAGE_SIZE must be"):
        gemini_image_settings_from_env({**ENV, "OMEMO_GEMINI_IMAGE_SIZE": "2k"})
