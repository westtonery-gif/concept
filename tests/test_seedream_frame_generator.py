"""Tests for ``SeedreamFrameGenerator`` and the frame port (GENERATION_ACCEPTANCE §10, SDR).

ADR-0088.

The adapter's real ``urllib`` code talks over a real socket to a local server that plays Ark's
``/api/v3/images/generations`` and the CDN the answer's URL points at. Nothing is mocked below the
adapter and nothing leaves ``127.0.0.1``.
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

from omemo_content_factory.adapters.frame_generator import FrameGenerator, FrameRequest
from omemo_content_factory.adapters.image_generator import ImageGeneratorError
from omemo_content_factory.composition import CompositionError, build_frame_generator
from omemo_content_factory.infrastructure.seedream_frame_generator import (
    API_KEY_VAR,
    MODEL_VAR,
    SeedreamFrameGenerator,
    SeedreamSettings,
    seedream_settings_from_env,
)
from tests.media_fixtures import jpeg, png

KEY = "ark-secret-key-value"
SETTINGS = SeedreamSettings(api_key=KEY, model="seedream-4-0-250828")


@dataclass
class FakeArk:
    image: bytes = field(default_factory=lambda: jpeg(1080, 1920))
    refusal: tuple[int, Any] | None = None
    no_image: bool = False
    cut_cdn: int = 0  # how many CDN downloads to cut short before serving properly
    requests: list[tuple[str, dict[str, str], Any]] = field(default_factory=list)
    base: str = ""


@pytest.fixture
def ark() -> Iterator[tuple[FakeArk, str]]:
    fake = FakeArk()

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, code: int, payload: Any, ctype: str = "application/json") -> None:
            raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            if fake.cut_cdn > 0 and self.path.startswith("/cdn/"):
                fake.cut_cdn -= 1
                self.wfile.write(raw[: len(raw) // 2])
                self.wfile.flush()
                self.close_connection = True
                return
            self.wfile.write(raw)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length))
            fake.requests.append((self.path, {k.lower(): v for k, v in self.headers.items()}, body))
            if fake.refusal:
                self._reply(*fake.refusal)
            elif fake.no_image:
                self._reply(200, {"data": [], "error": {"message": "blocked by moderation"}})
            else:
                self._reply(200, {"data": [{"url": f"{fake.base}/cdn/img.jpeg"}]})

        def do_GET(self) -> None:
            self._reply(200, fake.image, "image/jpeg")

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    fake.base = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    try:
        yield fake, fake.base
    finally:
        server.shutdown()
        server.server_close()


def _gen(url: str) -> SeedreamFrameGenerator:
    return SeedreamFrameGenerator(SETTINGS, api_url=url, timeout=5.0, retry_delay=0.0)


def _request(tmp_path: Path, **overrides: Any) -> FrameRequest:
    values: dict[str, Any] = {
        "prompt": "A round peach girl with a blue bow, 3D animated film style",
        "destination": str(tmp_path / "out" / "frame.jpeg"),
        "width": 1080,
        "height": 1920,
    }
    values.update(overrides)
    return FrameRequest(**values)


def test_sdr_01_a_picture_from_words_alone(tmp_path: Path, ark: tuple[FakeArk, str]) -> None:
    fake, url = ark
    request = _request(tmp_path)
    image = _gen(url).generate(request)
    ((path, headers, body),) = fake.requests
    assert path == "/api/v3/images/generations"
    assert headers["authorization"] == f"Bearer {KEY}"
    assert body == {
        "model": "seedream-4-0-250828",
        "prompt": request.prompt,
        "size": "1080x1920",
        "response_format": "url",
        "watermark": False,
    }
    assert (image.width, image.height, image.media_type) == (1080, 1920, "image/jpeg")
    assert Path(request.destination).read_bytes() == fake.image
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["frame.jpeg"]


def test_sdr_02_references_go_inline_with_their_own_type(
    tmp_path: Path, ark: tuple[FakeArk, str]
) -> None:
    fake, url = ark
    first, second = tmp_path / "a.jpg", tmp_path / "b.png"
    first.write_bytes(jpeg(64, 64))
    second.write_bytes(png(64, 64))
    _gen(url).generate(_request(tmp_path, references=(str(first), str(second))))
    images = fake.requests[0][2]["image"]
    assert images[0] == "data:image/jpeg;base64," + base64.b64encode(first.read_bytes()).decode()
    assert images[1].startswith("data:image/png;base64,")


def test_sdr_03_an_answer_of_the_wrong_shape_is_a_failure_and_nothing_is_written(
    tmp_path: Path, ark: tuple[FakeArk, str]
) -> None:
    fake, url = ark
    fake.image = jpeg(1920, 1080)  # landscape for a portrait request
    with pytest.raises(ImageGeneratorError, match="1920x1080"):
        _gen(url).generate(_request(tmp_path))
    fake.image = b"not an image"
    with pytest.raises(ImageGeneratorError, match="not an image"):
        _gen(url).generate(_request(tmp_path))
    assert not (tmp_path / "out").exists()


def test_sdr_04_no_image_quotes_what_the_vendor_said(
    tmp_path: Path, ark: tuple[FakeArk, str]
) -> None:
    fake, url = ark
    fake.no_image = True
    with pytest.raises(ImageGeneratorError, match="blocked by moderation"):
        _gen(url).generate(_request(tmp_path))


def test_sdr_05_a_refusal_is_final_carries_the_vendors_words_and_never_the_key(
    tmp_path: Path, ark: tuple[FakeArk, str]
) -> None:
    fake, url = ark
    fake.refusal = (400, {"error": {"message": "image size is out of range"}})
    with pytest.raises(ImageGeneratorError) as raised:
        _gen(url).generate(_request(tmp_path))
    assert "HTTP 400" in str(raised.value) and "out of range" in str(raised.value)
    assert KEY not in str(raised.value)
    assert len(fake.requests) == 1


def test_sdr_06_a_download_cut_short_is_asked_for_again(
    tmp_path: Path, ark: tuple[FakeArk, str]
) -> None:
    fake, url = ark
    fake.cut_cdn = 2
    image = _gen(url).generate(_request(tmp_path))
    assert (image.width, image.height) == (1080, 1920)
    fake.cut_cdn = 3
    with pytest.raises(ImageGeneratorError, match="could not be reached"):
        _gen(url).generate(_request(tmp_path))


def test_sdr_07_a_bad_reference_is_refused_before_any_request(
    tmp_path: Path, ark: tuple[FakeArk, str]
) -> None:
    fake, url = ark
    note = tmp_path / "note.txt"
    note.write_text("not a picture")
    with pytest.raises(ImageGeneratorError, match="not an image"):
        _gen(url).generate(_request(tmp_path, references=(str(note),)))
    with pytest.raises(ImageGeneratorError, match="cannot be read"):
        _gen(url).generate(_request(tmp_path, references=(str(tmp_path / "nope.jpg"),)))
    many = []
    for index in range(11):
        path = tmp_path / f"r{index}.jpg"
        path.write_bytes(jpeg(8, 8))
        many.append(str(path))
    with pytest.raises(ImageGeneratorError, match="at most"):
        _gen(url).generate(_request(tmp_path, references=tuple(many)))
    assert fake.requests == []


def test_sdr_08_settings_from_the_environment(tmp_path: Path) -> None:
    with pytest.raises(ImageGeneratorError) as missing:
        seedream_settings_from_env({})
    assert API_KEY_VAR in str(missing.value) and MODEL_VAR in str(missing.value)
    with pytest.raises(ImageGeneratorError, match="seedream-"):
        seedream_settings_from_env({API_KEY_VAR: KEY, MODEL_VAR: "gemini-3"})
    settings = seedream_settings_from_env({API_KEY_VAR: KEY, MODEL_VAR: "seedream-4-0-250828"})
    assert KEY not in repr(settings)
    built = build_frame_generator({API_KEY_VAR: KEY, MODEL_VAR: "seedream-4-0-250828"})
    assert isinstance(built, SeedreamFrameGenerator)
    with pytest.raises(CompositionError, match=MODEL_VAR):
        build_frame_generator({API_KEY_VAR: KEY})


def test_sdr_09_the_request_refuses_what_cannot_be_asked() -> None:
    for overrides in (
        {"prompt": " "},
        {"destination": ""},
        {"width": 0},
        {"height": True},
        {"references": ("",)},
        {"references": ("/a.jpg", "/a.jpg")},
    ):
        values: dict[str, Any] = {
            "prompt": "p",
            "destination": "/d.jpeg",
            "width": 100,
            "height": 100,
        }
        values.update(overrides)
        with pytest.raises(ValueError):
            FrameRequest(**values)


def test_sdr_09_the_generator_satisfies_the_port() -> None:
    generator: FrameGenerator = SeedreamFrameGenerator(SETTINGS)
    assert generator is not None
